"""Live adapter regression tests using fake sockets, frames, clocks and configuration only."""
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from drone_nav.tool_control.live import BUILD_ID, DeadlineTransport, FreshVideoStream, LiveAdapter, MissionClient, process_identity
from drone_nav.vision import VideoSnapshot


GROUND = {"is_flying": False, "are_motors_on": False, "is_flying_age_ms": 10,
          "are_motors_on_age_ms": 10, "armed": False, "vs_enabled": False,
          "vs_authority": "RC", "telemetry_generation": 1,
          "bridge_health": {"process_start_id": "offline-process"}}
AIRBORNE = {**GROUND, "is_flying": True, "are_motors_on": True, "armed": True,
            "vs_enabled": True, "vs_advanced_enabled": True, "vs_authority": "MSDK",
            "velocity_north_mps": 0., "velocity_east_mps": 0., "velocity_down_mps": 0.,
            "velocity_age_ms": 10, "yaw_deg": 0., "attitude_age_ms": 10,
            "height_m": 1.4, "height_age_ms": 10, "battery_percent": 80}


class FakeSocket:
    def __init__(self, telemetry=None, acknowledge=True):
        self.telemetry = copy.deepcopy(telemetry or GROUND)
        self.acknowledge = acknowledge
        self.writes = []
        self.pending = b""
        self.closed = False
        self.ok = True
        self.detail = "fake"

    def settimeout(self, value):
        pass

    def sendall(self, data):
        request = json.loads(data)
        self.writes.append(request)
        if self.acknowledge:
            self.pending = (json.dumps({"version": 1, "sequence": request["sequence"],
                "timestamp_ns": request["sequence"] + 1, "type": "ack",
                "payload": {"ok": self.ok, "detail": self.detail, "telemetry": self.telemetry}}) + "\n").encode()

    def recv(self, size):
        result, self.pending = self.pending[:size], self.pending[size:]
        return result

    def close(self):
        self.closed = True


class LiveTransportBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.network_guard = patch("socket.create_connection", side_effect=AssertionError("Real network is forbidden in this test"))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)

    def client(self, raw=None):
        raw = raw or FakeSocket()
        client = MissionClient(SimpleNamespace(network=SimpleNamespace(host="127.0.0.1", port=1, confirmation_token="offline-fixture")),
                               threading.Event(), lambda snapshot: None)
        client.owner = threading.get_ident()
        client._socket = client._file = DeadlineTransport(raw)
        return client, raw

    def ground_proof(self, client):
        client.raw = copy.deepcopy(GROUND)
        client.received = time.perf_counter()

    def test_cancel_during_request_logging_blocks_takeoff_at_actual_write(self):
        client, raw = self.client()
        self.ground_proof(client)
        client._log_event = lambda event, data: client.cancel.set() if event == "pc_request" else None
        with self.assertRaises(InterruptedError):
            client.takeoff("offline-fixture")
        self.assertEqual(raw.writes, [])
        self.assertFalse(client.failed)
        self.assertFalse(client.attempted_action)

    def test_unsent_cancel_does_not_consume_cleanup_ack_sequence(self):
        client, raw = self.client()
        self.ground_proof(client)
        client._log_event = lambda event, data: client.cancel.set() if event == "pc_request" else None
        with self.assertRaises(InterruptedError):
            client.takeoff("offline-fixture")
        client.cleaning = True
        client.zero()
        client.disarm()
        client.status("offline_cleanup_verification")
        self.assertEqual([r["type"] for r in raw.writes], ["zero", "disarm", "status"])
        self.assertEqual([r["sequence"] for r in raw.writes], [0, 1, 2])
        self.assertFalse(client.failed)

    def test_mission_deadline_rechecked_after_logging_before_write(self):
        clock = [10.]
        with patch("drone_nav.tool_control.live.time.perf_counter", lambda: clock[0]):
            client, raw = self.client()
            self.ground_proof(client)
            client.deadline = 11.
            client._log_event = lambda event, data: clock.__setitem__(0, 11.) if event == "pc_request" else None
            with self.assertRaises(InterruptedError):
                client.takeoff("offline-fixture")
            self.assertEqual(raw.writes, [])

    def test_cleanup_bypass_only_allows_zero_disarm_status(self):
        commands = [("takeoff", {"confirmation_token": "offline-fixture"}),
                    ("arm", {"confirmation_token": "offline-fixture"}),
                    ("gimbal", {"pitch_deg": 0}),
                    ("attitude", {"forward_tilt_deg": 0, "right_tilt_deg": 1,
                                  "up_mps": 0, "yaw_rate_rps": 0})]
        for command, payload in commands:
            with self.subTest(command=command):
                client, raw = self.client()
                client.cleaning = True
                client.cancel.set()
                with self.assertRaises(PermissionError):
                    client.send(command, payload)
                self.assertEqual(raw.writes, [])

    def test_unknown_write_outcome_cannot_be_replayed_or_followed_by_another_command(self):
        client, raw = self.client(FakeSocket(acknowledge=False))
        self.ground_proof(client)
        with self.assertRaises(ConnectionError):
            client.takeoff("offline-fixture")
        self.assertTrue(client.failed)
        for command, payload in [("takeoff", {"confirmation_token": "offline-fixture"}), ("zero", {})]:
            with self.assertRaises(ConnectionError):
                client.send(command, payload)
        self.assertEqual(len(raw.writes), 1)

    def test_takeoff_refuses_missing_changed_or_stale_ground_proof(self):
        for proof in ({}, {**GROUND, "is_flying": True}, {**GROUND, "are_motors_on": True},
                      {**GROUND, "are_motors_on_age_ms": 501}, {**GROUND, "vs_authority": "MSDK"}):
            with self.subTest(proof=proof):
                client, raw = self.client()
                client.raw = copy.deepcopy(proof)
                client.received = time.perf_counter()
                with self.assertRaises(PermissionError):
                    client.takeoff("offline-fixture")
                self.assertEqual(raw.writes, [])
                self.assertFalse(client.attempted_action)
                self.assertFalse(client.failed)

    def test_ground_proof_age_is_rechecked_after_request_logging(self):
        clock = [10.]
        with patch("drone_nav.tool_control.live.time.perf_counter", lambda: clock[0]):
            client, raw = self.client()
            self.ground_proof(client)
            client.raw["are_motors_on_age_ms"] = 490
            client._log_event = lambda event, data: clock.__setitem__(0, 10.02) if event == "pc_request" else None
            with self.assertRaises(PermissionError):
                client.takeoff("offline-fixture")
            self.assertEqual(raw.writes, [])
            self.assertEqual(client.protocol._out_sequence, 0)

    def test_takeoff_with_current_explicit_ground_proof_writes_once(self):
        client, raw = self.client()
        self.ground_proof(client)
        client.takeoff("offline-fixture")
        self.assertEqual([r["type"] for r in raw.writes], ["takeoff"])
        self.assertTrue(client.attempted_action)

    def test_missing_or_nonfinite_actual_velocity_or_yaw_prevents_attitude_write(self):
        for field in ("velocity_north_mps", "velocity_east_mps", "velocity_down_mps", "yaw_deg"):
            for invalid in (None, float("nan"), float("inf"), True):
                with self.subTest(field=field, invalid=invalid):
                    telemetry = {**AIRBORNE, field: invalid}
                    client, raw = self.client(FakeSocket(telemetry))
                    client._armed = True
                    client.stream = SimpleNamespace(read=lambda: (True, object(), .01))
                    with self.assertRaises(InterruptedError):
                        client.attitude(0., 1.)
                    self.assertEqual([r["type"] for r in raw.writes], ["status"])

    def test_finite_fresh_motion_evidence_allows_the_bounded_attitude(self):
        client, raw = self.client(FakeSocket(AIRBORNE))
        client._armed = True
        client.stream = SimpleNamespace(read=lambda: (True, object(), .01))
        client.attitude(0., 1.)
        self.assertEqual([r["type"] for r in raw.writes], ["status", "attitude"])

    def test_bridge_health_process_change_latches_transport_before_next_action(self):
        client, raw = self.client()
        client.status("offline_initial")
        raw.telemetry["bridge_health"]["process_start_id"] = "replacement-process"
        with self.assertRaises(ConnectionError):
            client.status("offline_replacement")
        with self.assertRaises(ConnectionError):
            client.takeoff("offline-fixture")
        self.assertEqual([r["type"] for r in raw.writes], ["status", "status"])

    def test_rc_authority_after_arm_cannot_dispatch_attitude(self):
        client, raw = self.client()
        client._armed = True
        with self.assertRaises(InterruptedError):
            client.attitude(0., 1.)
        self.assertEqual([r["type"] for r in raw.writes], ["status"])

    def test_connect_cannot_reopen_after_close(self):
        client, _ = self.client()
        client._socket = client._file = None
        client._open_session_log = lambda: None
        raw = FakeSocket()
        with patch("drone_nav.tool_control.live.socket.create_connection", return_value=raw) as connect:
            client.connect()
            client.close()
            with self.assertRaises(RuntimeError):
                client.connect()
            self.assertEqual(connect.call_count, 1)

    def test_echoed_ack_errors_and_nested_secrets_are_absent_from_logs_and_status_snapshot(self):
        client, raw = self.client()
        logs, snapshots = io.StringIO(), []
        client._log_file, client.on_snapshot = logs, snapshots.append
        raw.telemetry["extra_diagnostics"] = {"sdk_error": "echoed offline-fixture",
            "nested": [{"password": "other-private-fixture"}]}
        client.status("offline_snapshot")
        raw.ok, raw.detail = False, "SDK rejected offline-fixture"
        with self.assertRaises(PermissionError) as result:
            client.status("offline_error")
        client.log_event("mission_error", {"reason": "transport echoed offline-fixture",
                                           "details": [{"token": "second-private-fixture"}]})
        emitted = logs.getvalue() + json.dumps(snapshots) + str(result.exception)
        for secret in ("offline-fixture", "other-private-fixture", "second-private-fixture"):
            self.assertNotIn(secret, emitted)
        self.assertIn("<redacted>", emitted)
        self.assertEqual(len(snapshots), 1)


class LiveConfigurationBoundaryTest(unittest.TestCase):
    def test_uncalibrated_camera_or_extrinsics_cannot_become_live_ready(self):
        root = Path(__file__).resolve().parents[2]
        sample = json.loads((root / "pc/config.sample.json").read_text())
        sample["actual_measurements_confirmed"] = True
        sample["network"].update(host="127.0.0.1", confirmation_token="offline-fixture")
        site = json.loads((root / "integration/site.example.json").read_text())
        site.update(layout_confirmed=True, site_revision="offline-review")
        with tempfile.TemporaryDirectory() as tmp, patch("socket.create_connection", side_effect=AssertionError("No network")):
            site_path, config_path = Path(tmp) / "site.json", Path(tmp) / "config.json"
            site_path.write_text(json.dumps(site))
            for field in ("camera", "body_camera"):
                with self.subTest(field=field):
                    config = copy.deepcopy(sample)
                    config[field]["calibrated"] = False
                    config_path.write_text(json.dumps(config))
                    with self.assertRaises((RuntimeError, ValueError)):
                        LiveAdapter(site_path, config_path)

    def test_process_identity_accepts_actual_nested_bridge_contract(self):
        self.assertEqual(process_identity(GROUND), "offline-process")
        self.assertIsNone(process_identity({"bridge_health": None}))


class LivePreflightBoundaryTest(unittest.TestCase):
    def test_ground_change_during_fourteen_second_camera_preparation_prevents_takeoff(self):
        clock, statuses, takeoffs = [0.], [], []
        class Client:
            def __init__(self, *args):
                self._socket = None
                self.attempted_action = False
                self.raw = {}
                self.received = 0.
                self.last_telemetry = SimpleNamespace(battery_percent=80, rc_override_age_s=None)
            def connect(self):
                self._socket = object()
            def status(self, reason):
                statuses.append(reason)
                self.raw = {**GROUND, "bridge_build_id": BUILD_ID}
                if reason == "tool_immediate_takeoff_ground_proof":
                    self.raw.update(is_flying=True, are_motors_on=True)
                self.received = clock[0]
            def log_event(self, *args):
                pass
            def gimbal_down(self):
                self.attempted_action = True
                clock[0] += 1.9
            def stick_mode(self, mode):
                pass
            def takeoff(self, token):
                takeoffs.append(True)
                raise AssertionError("Takeoff must not be dispatched after ground state changes")
            def close(self):
                pass
        class Stream:
            def __init__(self, *args):
                pass
            def detect_latest(self, *args):
                clock[0] += 11.9
                return [SimpleNamespace(tag_id=0)], .01
            def close(self):
                pass
        def wait(seconds):
            clock[0] += seconds
            return False
        adapter = LiveAdapter.__new__(LiveAdapter)
        adapter.lock, adapter.busy, adapter.site = threading.Lock(), False, {}
        adapter.config = SimpleNamespace(network=SimpleNamespace(confirmation_token="offline-fixture",
            host="127.0.0.1", video_port=1, video_codec="h264"))
        adapter._release = lambda client: {"physical_stop_confirmed": False, "ground_verified": False}
        logger = SimpleNamespace(attach_capture=lambda stream: None, close=lambda: None)
        with patch("socket.create_connection", side_effect=AssertionError("No network")), \
             patch("drone_nav.tool_control.live.time.perf_counter", lambda: clock[0]), \
             patch("drone_nav.tool_control.live.MissionClient", Client), \
             patch("drone_nav.tool_control.live.FreshVideoStream", Stream), \
             patch("drone_nav.vision.AprilTagDetector", lambda config: object()), \
             patch("drone_nav.patrol._DetectionLogger", lambda client: logger):
            result = adapter.run({"mission_id": "offline", "destination_ids": [], "visits": []},
                                 SimpleNamespace(wait=wait, is_set=lambda: False), lambda **event: None)
        self.assertGreaterEqual(clock[0], 13.8)
        self.assertIn("tool_immediate_takeoff_ground_proof", statuses)
        self.assertEqual(takeoffs, [])
        self.assertFalse(result["route_completed"])
        self.assertIn("Ground/motor state changed", result["error"])


class LiveCaptureBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.clock = 0.
        self.addCleanup(patch.stopall)
        patch("socket.create_connection", side_effect=AssertionError("No network")).start()
        patch("drone_nav.tool_control.live.time.perf_counter", lambda: self.clock).start()
        patch("drone_nav.tool_control.live.time.monotonic", lambda: self.clock).start()

    def test_detection_latency_is_included_in_frame_age(self):
        stream = FreshVideoStream.__new__(FreshVideoStream)
        snapshot = VideoSnapshot(1, 1, self.clock, object())
        stream.snapshot = lambda: snapshot
        stream._detection_key, stream._detections = None, []
        def detect(frame):
            self.clock += .7
            return [SimpleNamespace(tag_id=3)]
        tags, age = stream.detect_latest(SimpleNamespace(detect=detect), .5)
        self.assertEqual(tags, [])
        self.assertAlmostEqual(age, .7)

    def capture(self, detection_s=0., encoding_s=0., velocity_age=.01, visible_tag=3):
        test = self
        captures = []
        class Stream:
            last_detection_snapshot = None
            count = 0
            def detect_latest(self, detector, max_age):
                self.count += 1
                received = test.clock
                test.clock += detection_s
                self.last_detection_snapshot = VideoSnapshot(1, self.count, received, object())
                # A conservative caller must also account for elapsed telemetry age.
                return [SimpleNamespace(tag_id=visible_tag)], detection_s
        class Client:
            def zero(self):
                self.received = test.clock
                self.last_telemetry = SimpleNamespace(velocity_age_s=velocity_age,
                    velocity_north_mps=0., velocity_east_mps=0., velocity_down_mps=0.)
        class Limiter:
            def wait(self):
                test.clock += .1
        class Encoded:
            size = 16
            def tobytes(self):
                return b"\x89PNG\r\n\x1a\nFAKE"
        def encode(kind, frame):
            test.clock += encoding_s
            return True, Encoded()
        with patch.dict(sys.modules, {"cv2": SimpleNamespace(imencode=encode)}):
            try:
                LiveAdapter.__new__(LiveAdapter)._capture(Client(), Limiter(), Stream(), None, 3, 0,
                    lambda **event: captures.append(event["capture"]))
            except RuntimeError as error:
                return captures, str(error)
        return captures, None

    def test_detection_elapsed_time_cannot_hide_stale_velocity(self):
        captures, error = self.capture(detection_s=.2, velocity_age=.35)
        self.assertEqual(captures, [])
        self.assertIn("not confirmed", error)

    def test_png_encoding_latency_cannot_publish_stale_frame_or_velocity(self):
        captures, error = self.capture(encoding_s=.6)
        self.assertEqual(captures, [])
        self.assertIn("not confirmed", error)

    def test_stationary_fresh_expected_tag_produces_two_distinct_frame_captures(self):
        captures, error = self.capture()
        self.assertIsNone(error)
        self.assertEqual(len(captures), 2)
        self.assertNotEqual(captures[0]["frame_id"], captures[1]["frame_id"])
        self.assertTrue(all(c["arrival_confirmed"] for c in captures))
        self.assertTrue(all(c["capture_source"] == "pc_decoded_camera_frame" for c in captures))

    def test_other_tag_cannot_supply_requested_capture(self):
        captures, error = self.capture(visible_tag=1)
        self.assertEqual(captures, [])
        self.assertIn("not confirmed", error)

    def climb(self, down=0., velocity_age=.01, distinct=True):
        test = self
        effects = []
        class Client:
            def status(self, reason):
                self.received = test.clock
                self.last_telemetry = SimpleNamespace(height_m=1.4, height_age_s=.01,
                    velocity_down_mps=down, velocity_age_s=velocity_age)
            def zero(self):
                effects.append("zero")
            def attitude(self, *args):
                raise AssertionError("At target height no ascent command is authorized")
        class Stream:
            count = 0
            last_detection_snapshot = None
            def detect_latest(self, detector, max_age):
                self.count += 1
                self.last_detection_snapshot = VideoSnapshot(1, self.count if distinct else 1, test.clock, object())
                return [SimpleNamespace(tag_id=0)], .01
        class Limiter:
            def wait(self):
                test.clock += .1
        adapter = LiveAdapter.__new__(LiveAdapter)
        adapter.config = SimpleNamespace(room=SimpleNamespace(height_m=2.5))
        try:
            adapter._climb(Client(), Limiter(), Stream(), None, lambda config, tag: 1.4)
        except RuntimeError as error:
            return effects, str(error)
        return effects, None

    def test_target_height_without_finite_near_zero_vertical_velocity_does_not_complete_climb(self):
        for down in (None, float("nan"), float("inf"), .051, -.051):
            with self.subTest(down=down):
                self.clock = 0.
                effects, error = self.climb(down=down)
                self.assertTrue(effects)
                self.assertIn("ascent target unconfirmed", error)

    def test_target_height_with_missing_or_stale_velocity_age_does_not_complete_climb(self):
        for age in (None, .501):
            with self.subTest(age=age):
                self.clock = 0.
                _, error = self.climb(velocity_age=age)
                self.assertIn("ascent target unconfirmed", error)

    def test_target_hold_requires_distinct_camera_frames(self):
        _, error = self.climb(distinct=False)
        self.assertIn("ascent target unconfirmed", error)

    def test_fresh_target_height_and_vertical_velocity_boundary_confirm_climb(self):
        for down in (.05, -.05, 0.):
            with self.subTest(down=down):
                self.clock = 0.
                effects, error = self.climb(down=down)
                self.assertIsNone(error)
                self.assertGreaterEqual(self.clock, .6)
                self.assertEqual(set(effects), {"zero"})


if __name__ == "__main__":
    unittest.main()
