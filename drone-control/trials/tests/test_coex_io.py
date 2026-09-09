"""Offline transport tests. Every socket is fake; no device or network is used."""

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import tempfile
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pc"))
from coex_io import ActionOutcomeUnknown, ControlIO, MissingTelemetryError, QueryLatchedError


def ack(sequence=0, telemetry=None, *, ok=True, detail="ok", include_telemetry=True):
    payload = {"ok": ok, "detail": detail}
    if include_telemetry:
        payload["telemetry"] = telemetry or {"height_m": 1.8, "height_age_ms": 12,
                                             "telemetry_generation": 7, "is_flying_age_ms": 18}
    return (json.dumps({"version": 1, "sequence": sequence, "timestamp_ns": sequence + 1,
                        "type": "ack", "payload": payload}) + "\n").encode()


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeSocket:
    def __init__(self, chunks=(), *, clock=None, connect_delay=0, send_delay=0, recv_delay=0):
        self.chunks = list(chunks)
        self.clock = clock
        self.connect_delay = connect_delay
        self.send_delay = send_delay
        self.recv_delay = recv_delay
        self.sent = []
        self.timeouts = []
        self.closed = False
        self.shutdown_called = False

    def _advance(self, delta):
        if self.clock:
            self.clock.now += delta

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def connect(self, address):
        self.address = address
        self._advance(self.connect_delay)

    def sendall(self, data):
        self.sent.append(data)
        self._advance(self.send_delay)

    def recv(self, size):
        self._advance(self.recv_delay)
        if not self.chunks:
            raise TimeoutError("fake deadline")
        value = self.chunks.pop(0)
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            value = value()
        return value

    def shutdown(self, how):
        self.shutdown_called = True

    def close(self):
        self.closed = True


class ControlIOTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def io(self, sockets):
        factory = self.enterContext(patch("coex_io.socket.socket", side_effect=sockets))
        obj = ControlIO("127.0.0.1", 9998, log_dir=self.directory.name)
        self.addCleanup(obj.close)
        obj.connect()
        return obj, factory

    def test_construction_does_not_connect_or_create_logs(self):
        with patch("coex_io.socket.socket") as factory:
            ControlIO("127.0.0.1", 9998, log_dir=self.directory.name)
        factory.assert_not_called()
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_non_numeric_host_rejected_before_any_network(self):
        with patch("coex_io.socket.socket") as factory, self.assertRaises(ValueError):
            ControlIO("phone.example", 9998)
        factory.assert_not_called()

    def test_ground_queries_precede_control_connect_and_share_its_log_session(self):
        flying = FakeSocket([b"FlightController IsFlying false\n"])
        motors = FakeSocket([b"FlightController AreMotorsOn false\n"])
        control = FakeSocket([ack()])
        factory = self.enterContext(patch("coex_io.socket.socket", side_effect=[flying, motors, control]))
        obj = ControlIO("127.0.0.1", 9998, log_dir=self.directory.name)
        self.addCleanup(obj.close)
        self.assertFalse(obj.query_bool("IsFlying"))
        self.assertFalse(obj.query_bool("AreMotorsOn"))
        self.assertIsNone(obj._socket)
        session_id, path = obj.session_id, obj.log_path
        self.assertEqual(factory.call_count, 2)
        self.assertIn("coex_query_response", path.read_text(encoding="utf-8"))
        obj.connect()
        obj.command("status", {"state": "PREFLIGHT"})
        self.assertEqual(obj.session_id, session_id)
        self.assertEqual(obj.log_path, path)
        self.assertIsNone(obj.motors_snapshot().value)
        obj.close()
        self.assertEqual(obj.log_path, path)
        self.assertIn("network_connected", path.read_text(encoding="utf-8"))

    def test_initial_ground_query_failure_is_logged_and_prevents_control_connect(self):
        factory = self.enterContext(patch("coex_io.socket.socket", side_effect=[FakeSocket([TimeoutError()])]))
        obj = ControlIO("127.0.0.1", 9998, log_dir=self.directory.name)
        self.addCleanup(obj.close)
        with self.assertRaises(QueryLatchedError):
            obj.query_bool("IsFlying")
        with self.assertRaises(QueryLatchedError):
            obj.connect()
        self.assertEqual(factory.call_count, 1)
        self.assertIn("coex_query_failed", obj.log_path.read_text(encoding="utf-8"))

    def test_raw_ack_fields_and_receipt_metadata_are_preserved_and_copied(self):
        sock = FakeSocket([ack(), ack(1)])
        obj, _ = self.io([sock])
        result = obj.command("status", {"state": "PREFLIGHT"})
        self.assertEqual(result["telemetry_generation"], 7)
        self.assertEqual(result["is_flying_age_ms"], 18)
        self.assertEqual(result["_connection_epoch"], 1)
        self.assertIsInstance(result["_received_monotonic_s"], float)
        result["height_m"] = -999
        self.assertEqual(obj._raw_snapshot["height_m"], 1.8)
        self.assertEqual(obj.command("status", {"state": "PREFLIGHT"})["_request_sequence"], 1)

    def test_distinct_acks_in_one_coarse_windows_tick_have_distinct_receipt_times(self):
        # Windows Python 3.12 monotonic() may share one 15.625 ms tick across
        # distinct ACKs. QPC must preserve their actual order for odometry.
        clock = Clock()
        self.enterContext(patch("coex_io.time.monotonic", return_value=4096.0))
        self.enterContext(patch("coex_io.time.perf_counter", clock))
        sock = FakeSocket([ack(), ack(1)], clock=clock, recv_delay=.001)
        obj, _ = self.io([sock])
        first = obj.command("status", {"state": "CLIMB"})
        second = obj.command("status", {"state": "CLIMB"})
        self.assertNotEqual(first["_request_sequence"], second["_request_sequence"])
        delta = second["_received_monotonic_s"] - first["_received_monotonic_s"]
        self.assertAlmostEqual(delta, .001)
        self.assertGreater(delta, 0)
        self.assertLess(delta, .015625)

    def test_missing_current_telemetry_cannot_reuse_a_previous_snapshot(self):
        sock = FakeSocket([ack(), ack(1, include_telemetry=False)])
        obj, factory = self.io([sock])
        obj.command("status", {"state": "PREFLIGHT"})
        with self.assertRaises(MissingTelemetryError):
            obj.command("status", {"state": "PREFLIGHT"})
        self.assertIsNone(obj.last_telemetry)
        self.assertIsNone(obj._raw_snapshot)
        self.assertTrue(sock.closed)
        with self.assertRaises(ConnectionError):
            obj.command("status", {"state": "PREFLIGHT"})
        self.assertEqual(factory.call_count, 1)

    def test_wrong_sequence_after_a_write_is_unknown_and_never_replayed(self):
        sock = FakeSocket([ack(9)])
        obj, _ = self.io([sock])
        with self.assertRaises(ActionOutcomeUnknown):
            obj.command("arm", {"confirmation_token": "test-token"})
        with self.assertRaises(ConnectionError):
            obj.command("arm", {"confirmation_token": "test-token"})
        self.assertEqual(len(sock.sent), 1)
        self.assertTrue(sock.shutdown_called)

    def test_known_negative_ack_is_not_reported_as_unknown_execution(self):
        sock = FakeSocket([ack(ok=False, detail="denied")])
        obj, _ = self.io([sock])
        with self.assertRaises(PermissionError):
            obj.command("arm", {"confirmation_token": "test-token"})
        self.assertFalse(obj._armed)
        self.assertIsNone(obj._raw_snapshot)

    def test_control_trickle_has_one_total_deadline_not_a_timeout_per_chunk(self):
        clock = Clock()
        self.enterContext(patch("coex_io.time.perf_counter", clock))
        sock = FakeSocket([b'{"version":', b'1}'], clock=clock, recv_delay=.25)
        obj, _ = self.io([sock])
        with self.assertRaises(ConnectionError):
            obj.command("status", {"state": "PREFLIGHT"})
        self.assertIsNone(obj._raw_snapshot)
        self.assertTrue(sock.closed)
        self.assertTrue(any(0 < timeout <= .151 for timeout in sock.timeouts))

    def test_inherited_slow_timeout_is_capped_but_arm_can_take_longer_than_point_four(self):
        clock = Clock()
        self.enterContext(patch("coex_io.time.perf_counter", clock))
        sock = FakeSocket([ack()], clock=clock, send_delay=2.5)
        obj, _ = self.io([sock])
        obj.arm("test-token")  # NDJSONClient.arm requests 20 s; adapter caps at 3 s.
        self.assertTrue(obj._armed)
        self.assertLessEqual(max(sock.timeouts), 3.0)
        self.assertTrue(any(.49 < timeout <= .5 for timeout in sock.timeouts))

    def test_takeoff_timeout_is_unknown_and_smaller_caller_deadline_is_respected(self):
        clock = Clock()
        self.enterContext(patch("coex_io.time.perf_counter", clock))
        sock = FakeSocket([ack()], clock=clock, send_delay=.3)
        obj, _ = self.io([sock])
        with self.assertRaises(ActionOutcomeUnknown):
            obj.command("takeoff", {"confirmation_token": "test-token"}, timeout_s=.2)
        self.assertTrue(sock.closed)
        self.assertEqual(len(sock.sent), 1)

    def test_control_socket_cannot_be_used_by_another_thread(self):
        sock = FakeSocket([ack()])
        obj, _ = self.io([sock])
        errors = []
        def attempt():
            try:
                obj.command("zero", {})
            except RuntimeError as error:
                errors.append(error)
        thread = threading.Thread(target=attempt)
        thread.start()
        thread.join(1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(sock.sent, [])

    def test_prebuffered_ack_cannot_acknowledge_a_request_that_has_not_been_sent(self):
        sock = FakeSocket([ack() + ack(1)])
        obj, _ = self.io([sock])
        obj.command("status", {"state": "PREFLIGHT"})
        with self.assertRaises(ConnectionError):
            obj.command("status", {"state": "PREFLIGHT"})
        self.assertEqual(len(sock.sent), 1)

    def test_out_of_scope_commands_and_mode_changes_send_nothing(self):
        sock = FakeSocket()
        obj, _ = self.io([sock])
        for kind, payload in (("obstacle_avoidance", {}), ("land", {"confirmation_token": "test-token"}),
                              ("stick_mode", {"mode": "advanced_angle"})):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                obj.command(kind, payload)
        self.assertEqual(sock.sent, [])

    def test_logs_keep_correlated_raw_evidence_but_redact_tokens_even_in_ack_text(self):
        token = "offline-test-private-value"
        sock = FakeSocket([ack(detail=f"echo {token}")])
        obj, _ = self.io([sock])
        obj.command("arm", {"confirmation_token": token})
        obj.log_event("fixture", {"nested": {"confirmation_token": token}, "detail": token})
        text = obj.log_path.read_text(encoding="utf-8")
        self.assertNotIn(token, text)
        events = [json.loads(line) for line in text.splitlines()]
        self.assertTrue({"pc_request", "android_ack", "motion_assessment"}.issubset({x["event"] for x in events}))
        self.assertIn("<redacted>", text)

    def test_query_false_is_valid_and_motor_snapshot_is_immutable(self):
        query = FakeSocket([b"FlightController AreMotorsOn false\n"])
        obj, _ = self.io([FakeSocket(), query])
        self.assertIs(obj.query_bool("AreMotorsOn"), False)
        snapshot = obj.motors_snapshot()
        self.assertIs(snapshot.value, False)
        self.assertIsNone(snapshot.error)
        self.assertEqual(snapshot.connection_epoch, 1)
        with self.assertRaises(FrozenInstanceError):
            snapshot.value = True
        self.assertEqual(query.sent, [b"GET FlightController AreMotorsOn\n"])
        self.assertTrue(query.closed)

    def test_query_rejects_mismatched_prefix_sdk_error_and_non_boolean_success(self):
        for raw in (b"FlightController IsFlying true\n", b"FlightController AreMotorsOn success\n",
                    b"FlightController AreMotorsOn ErrorImp{errorCode='NO_HANDLER'}\n",
                    b"QUERY_LOCAL_ERROR busy\n"):
            with self.subTest(raw=raw):
                obj, factory = self.io([FakeSocket(), FakeSocket([raw])])
                with self.assertRaises(QueryLatchedError):
                    obj.query_bool("AreMotorsOn")
                with self.assertRaises(QueryLatchedError):
                    obj.query_bool("AreMotorsOn")
                self.assertEqual(factory.call_count, 2)
                self.assertIsNone(obj.motors_snapshot().value)
                obj.close()

    def test_query_deadline_includes_connect_send_and_all_response_chunks(self):
        clock = Clock()
        self.enterContext(patch("coex_io.time.perf_counter", clock))
        query = FakeSocket([b"FlightController AreMotorsOn true\n"], clock=clock,
                           connect_delay=.15, send_delay=.1, recv_delay=.2)
        obj, factory = self.io([FakeSocket(clock=clock), query])
        with self.assertRaises(QueryLatchedError):
            obj.query_bool("AreMotorsOn")
        self.assertIsNone(obj.motors_snapshot().value)
        self.assertTrue(query.closed)
        self.assertEqual(factory.call_count, 2)
        self.assertTrue(any(0 < timeout <= .151 for timeout in query.timeouts))

    def test_generation_change_invalidates_motor_evidence_and_latches_sampler(self):
        control = FakeSocket([ack(), ack(1, {"telemetry_generation": 8, "height_m": 1.8})])
        obj, _ = self.io([control, FakeSocket([b"FlightController AreMotorsOn true\n"])])
        obj.command("status", {"state": "PREFLIGHT"})
        obj.query_bool("AreMotorsOn")
        self.assertTrue(obj.motors_snapshot().value)
        obj.command("status", {"state": "PREFLIGHT"})
        self.assertIsNone(obj.motors_snapshot().value)
        with self.assertRaises(QueryLatchedError):
            obj.start_motors()

    def test_initial_motor_query_must_be_rebound_after_first_control_generation(self):
        obj, _ = self.io([FakeSocket([ack()]),
                          FakeSocket([b"FlightController AreMotorsOn false\n"]),
                          FakeSocket([b"FlightController IsFlying false\n"]),
                          FakeSocket([b"FlightController AreMotorsOn false\n"])])
        self.assertFalse(obj.query_bool("AreMotorsOn"))
        obj.command("status", {"state": "PREFLIGHT"})
        self.assertIsNone(obj.motors_snapshot().value)
        self.assertFalse(obj.query_bool("IsFlying"))
        self.assertFalse(obj.query_bool("AreMotorsOn"))
        self.assertIsNone(obj.motors_snapshot().error)

    def test_motor_sampler_accepts_false_then_latches_first_error_without_retry(self):
        control = FakeSocket()
        obj, factory = self.io([control, FakeSocket([b"FlightController AreMotorsOn false\n"]),
                               FakeSocket([TimeoutError("fake failure")])])
        obj.QUERY_PERIOD_S = .01
        obj.start_motors()
        obj._motors_thread.join(1)
        self.assertFalse(obj._motors_thread.is_alive())
        self.assertEqual(factory.call_count, 3)
        self.assertEqual(control.sent, [])
        self.assertIsNone(obj.motors_snapshot().value)
        with self.assertRaises(QueryLatchedError):
            obj.start_motors()

    def test_only_one_readonly_query_and_stop_waits_for_pending_completion(self):
        entered = threading.Event()
        release = threading.Event()
        def reply():
            entered.set()
            if not release.wait(.2):
                raise TimeoutError("fake wait exceeded")
            return b"FlightController AreMotorsOn true\n"
        obj, factory = self.io([FakeSocket(), FakeSocket([reply])])
        obj.start_motors()
        self.assertTrue(entered.wait(.2))
        with self.assertRaises(RuntimeError):
            obj.query_bool("IsFlying")
        self.assertEqual(factory.call_count, 2)
        release.set()
        obj.stop_motors()
        self.assertIsNone(obj._motors_thread)
        self.assertTrue(obj.motors_snapshot().value)


if __name__ == "__main__":
    unittest.main()
