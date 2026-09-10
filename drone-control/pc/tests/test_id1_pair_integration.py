"""Offline ID1 mission integration; every real socket connection is forbidden."""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import unittest

from test_standalone_tag_shuttle import (
    FakeClient, StandaloneTestCase, config, profile, shuttle,
)
from drone_nav.tool_control.live import DeadlineTransport


REFERENCE = {"roi_tag_bounds": {"x_min": -5.090921, "x_max": 1.406036,
                              "y_min": -1.011650, "y_max": 2.623666},
             "margin_fraction": .03}


def fitted_tag(tag_id=1):
    return shuttle.PixelTag(tag_id, (1240., 390.),
        ((1330., 300.), (1150., 300.), (1150., 480.), (1330., 480.)), 80., 0)


def fake_stream(clock):
    stream = SimpleNamespace(last_detection_snapshot=None, close=MagicMock())
    def detect(_detector, _age):
        previous = stream.last_detection_snapshot
        sequence = 1 if previous is None else previous.key[1] + 1
        stream.last_detection_snapshot = SimpleNamespace(
            key=(1, sequence), received_s=clock[0],
            frame=SimpleNamespace(shape=(1080, 1920, 3)))
        # Every outbound wall tag is served fitted, so each pair gate finds its own id.
        return [fitted_tag(tag_id) for tag_id in (1, 2, 3)], 0.
    stream.detect_latest = detect
    return stream


class PairMissionTests(StandaloneTestCase):
    def run_pair(self, photo):
        client, clock, logger = FakeClient(), [100.], MagicMock()
        logger.save_confirmation_photo.return_value = photo
        stream = fake_stream(clock)
        limiter = SimpleNamespace(wait=lambda: clock.__setitem__(0, clock[0] + .1))
        forbidden_traverse = MagicMock(side_effect=AssertionError("ID1 mode cannot use full-route traversal"))
        pause = MagicMock()
        with ExitStack() as stack:
            for name, replacement in (
                ("ShuttleClient", lambda *args: client), ("MixedDetector", MagicMock()),
                ("FreshVideoStream", lambda *args, **kwargs: stream),
                ("ShuttleDetectionLogger", lambda *args, **kwargs: logger),
                ("_wait_ground_video", MagicMock()), ("_wait_takeoff_settled", MagicMock()),
                ("_acquire_tag", MagicMock()), ("_climb", MagicMock()),
                ("acquire_wall_home", MagicMock()), ("_pause", pause),
                ("RateLimiter", lambda *args: limiter), ("traverse_horizontal", forbidden_traverse),
            ):
                stack.enter_context(patch.object(shuttle, name, replacement))
            stack.enter_context(patch.object(shuttle.time, "monotonic", lambda: clock[0]))
            stack.enter_context(patch.object(shuttle.time, "perf_counter", lambda: clock[0]))
            stack.enter_context(redirect_stdout(io.StringIO()))
            result = shuttle.run(config(), profile(), pair_reference=REFERENCE)
        return result, client, logger, stream, forbidden_traverse, pause

    def test_pair_mission_visits_only_home6_and_id1_then_hover_rc(self):
        result, client, logger, stream, traverse, pause = self.run_pair(Path("offline-ID1.jpg"))
        self.assertTrue(result["route_completed"], result)
        self.assertEqual(result["visited_ids"], [6, 1])
        self.assertEqual(result["active_route_ids"], [6, 1])
        self.assertFalse(result["full_route_completed"])
        self.assertEqual(result["state"], "awaiting_rc_landing")
        self.assertTrue(result["manual_landing_required"])
        self.assertTrue(result["control_released_to_rc"])
        self.assertEqual(result["pair_capture"]["tag_id"], 1)
        self.assertFalse(result["pair_capture"]["tv_visibility_verified"])
        self.assertEqual(client.calls.count("takeoff"), 1)
        self.assertEqual(client.calls.count("arm"), 1)
        self.assertEqual(client.calls.count("disarm"), 1)
        self.assertNotIn("land", client.calls)
        self.assertNotIn(2, [call.args[-1] for call in pause.call_args_list])
        self.assertNotIn(3, [call.args[-1] for call in pause.call_args_list])
        traverse.assert_not_called()
        logger.save_confirmation_photo.assert_called_once()
        stream.close.assert_called_once()
        self.assertEqual(client.calls[-1], "close")

    def test_required_photo_failure_is_failure_and_runs_rc_cleanup(self):
        result, client, logger, stream, traverse, _ = self.run_pair(None)
        self.assertFalse(result["route_completed"])
        self.assertEqual(result["visited_ids"], [6])
        self.assertIn("required photo", result["error"])
        self.assertIsNone(result["pair_capture"])
        self.assertTrue(result["control_released_to_rc"])
        self.assertTrue(result["manual_landing_required"])
        self.assertEqual(client.calls.count("arm"), 1)
        self.assertEqual(client.calls.count("disarm"), 1)
        self.assertEqual(client.calls[-1], "close")
        traverse.assert_not_called()
        logger.close.assert_called_once()
        stream.close.assert_called_once()

    def test_null_pair_reference_cannot_fall_back_to_full_route(self):
        with tempfile.TemporaryDirectory() as directory:
            reference_path = Path(directory) / "null-reference.json"
            reference_path.write_text("null", encoding="utf-8")
            for mode in ([], ["--check"], ["--execute"]):
                with self.subTest(mode=mode), \
                        patch.object(shuttle, "load_profile", return_value=profile()), \
                        patch.object(shuttle, "prepare_config") as prepare, \
                        patch.object(shuttle, "run") as run, \
                        patch.object(shuttle, "ShuttleClient", side_effect=AssertionError("No client")), \
                        patch.object(shuttle, "MixedDetector", side_effect=AssertionError("No detector")), \
                        patch("sys.stderr", io.StringIO()) as error, redirect_stdout(io.StringIO()):
                    code = shuttle.main(["--id1-pair", "--framing-reference", str(reference_path),
                                         "--config", "unused.json", *mode])
                self.assertEqual(code, 2)
                self.assertIn("Setup error", error.getvalue())
                prepare.assert_not_called()
                run.assert_not_called()


def airborne_raw(**changes):
    return {**FakeClient().raw, "is_flying": True, "are_motors_on": True,
            "armed": True, "vs_enabled": True, "vs_advanced_enabled": True,
            "vs_authority": "MSDK", "height_m": 1.4, "height_age_ms": 10.,
            "velocity_age_ms": 10., "attitude_age_ms": 10., "yaw_deg": 0.,
            "velocity_north_mps": 0., "velocity_east_mps": 0.,
            "velocity_down_mps": 0., "battery_percent": 80., **changes}


class AckSocket:
    """In-memory wire exercises actual protocol/ACK and dispatch-log ordering."""
    def __init__(self, clock, raw, rtt=.05):
        self.clock, self.raw, self.rtt = clock, raw, rtt
        self.writes, self.pending = [], b""

    def settimeout(self, value):
        pass

    def sendall(self, data):
        request = json.loads(data)
        self.writes.append(request)
        self.pending = (json.dumps({"version": 1, "sequence": request["sequence"],
            "timestamp_ns": request["sequence"] + 1, "type": "ack",
            "payload": {"ok": True, "detail": "offline fixture", "telemetry": self.raw}}) + "\n").encode()

    def recv(self, size):
        self.clock[0] += self.rtt
        data, self.pending = self.pending[:size], self.pending[size:]
        return data


class PairDispatchTests(StandaloneTestCase):
    def client(self, clock, raw=None):
        client = shuttle.ShuttleClient(shuttle.configure_execution(config(), profile()),
                                       threading.Event(), lambda snapshot: None)
        client.phase, client.pair_mode = "lateral", True
        client.lateral_bounds = (-.6, .6)
        client.stream = SimpleNamespace(
            last_detection_snapshot=SimpleNamespace(key=(1, 1), received_s=clock[0]),
            read=lambda: (True, None, .01))
        client.last_telemetry = FakeClient().last_telemetry
        client.raw, client.received = airborne_raw(), clock[0]
        wire = AckSocket(clock, airborne_raw() if raw is None else raw)
        client.owner, client._armed = threading.get_ident(), True
        client._socket = client._file = DeadlineTransport(wire)
        return client, wire

    def test_lateral_bounds_are_enforced_at_actual_send(self):
        for right, allowed in ((-.6, True), (.6, True), (-.60001, False), (.60001, False)):
            clock = [100.]
            with self.subTest(right=right), \
                    patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                    patch.object(shuttle.time, "perf_counter", lambda: clock[0]):
                client, wire = self.client(clock)
                if allowed:
                    client.attitude(0., right, 0., 0.)
                else:
                    with self.assertRaises(PermissionError):
                        client.attitude(0., right, 0., 0.)
                self.assertEqual([item["type"] for item in wire.writes],
                                 ["status", "attitude"] if allowed else ["status"])

    def test_expired_pulse_is_refused_after_status_round_trip(self):
        clock = [100.]
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]):
            client, wire = self.client(clock)
            client.motion_valid_until_s = 100.04
            with self.assertRaises(shuttle.FramingCorrectionDeferred):
                client.attitude(0., .2, 0., 0.)
        self.assertEqual([item["type"] for item in wire.writes], ["status"])

    def test_pulse_rechecks_velocity_changed_by_status_ack(self):
        clock = [100.]
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]):
            client, wire = self.client(clock, airborne_raw(velocity_north_mps=.2))
            client.motion_valid_until_s = 100.25
            self.assertEqual(client.last_telemetry.velocity_north_mps, 0.)
            with self.assertRaises((InterruptedError, PermissionError)):
                client.attitude(0., .2, 0., 0.)
        self.assertEqual([item["type"] for item in wire.writes], ["status"])

    def test_pulse_velocity_proof_ages_through_request_log_before_wire_write(self):
        clock = [100.]
        original_log = shuttle.MissionClient._log_event
        def slow_log(client, event, data):
            original_log(client, event, data)
            if event == "pc_request" and data.get("type") == "attitude":
                clock[0] += .11
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]), \
                patch.object(shuttle.MissionClient, "_log_event", slow_log):
            client, wire = self.client(clock, airborne_raw(velocity_age_ms=400.))
            client.motion_valid_until_s = 100.25
            with self.assertRaises((InterruptedError, PermissionError)):
                client.attitude(0., .2, 0., 0.)
        # The frame and pulse deadline remain valid; only velocity age expires.
        self.assertLess(clock[0], 100.25)
        self.assertEqual([item["type"] for item in wire.writes], ["status"])

    def test_fresh_settled_pulse_survives_internal_telemetry_clear_and_is_sent(self):
        clock = [100.]
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]):
            client, wire = self.client(clock)
            client.motion_valid_until_s = 100.25
            client.attitude(0., .2, 0., 0.)
        self.assertEqual([item["type"] for item in wire.writes], ["status", "attitude"])

    def test_expiry_during_log_preserves_unsent_retry_type_and_transport(self):
        clock = [100.]
        original_log = shuttle.MissionClient._log_event
        def slow_log(client, event, data):
            original_log(client, event, data)
            if event == "pc_request" and data.get("type") == "attitude":
                clock[0] += .21
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]), \
                patch.object(shuttle.MissionClient, "_log_event", slow_log):
            client, wire = self.client(clock)
            client.motion_valid_until_s = 100.25
            with self.assertRaises(shuttle.FramingCorrectionDeferred):
                client.attitude(0., .2, 0., 0.)
            self.assertFalse(client.failed)
            self.assertFalse(client.write_started)
            client.motion_valid_until_s = None
            client.zero()
        self.assertEqual([item["type"] for item in wire.writes], ["status", "zero"])


class CorrectionRetryTests(StandaloneTestCase):
    def exercise(self, rejection):
        clock, client, logger = [100.], FakeClient(), MagicMock()
        client.raw.update(airborne_raw())
        logger.save_confirmation_photo.return_value = Path("offline-corrected-ID1.jpg")
        stream = fake_stream(clock)
        limiter = SimpleNamespace(wait=lambda: clock.__setitem__(0, clock[0] + .1))
        gate = SimpleNamespace(diagnostic={})
        observations, attempts = [], []
        def update(tags, now, age, key, shape, speed, velocity_age):
            observations.append(key)
            if len(observations) <= 2:
                gate.diagnostic = {"state": "REACQUIRE_EDGE_PULSE",
                                   "motion_valid_until_s": now + .25}
                return .25, None
            gate.diagnostic = {"state": "CAPTURE_READY", "arrival_policy": "tag_right_edge_band",
                               "arrival_ready": True, "capture_ready": True,
                               "footprint": {"fits": False}}
            return 0., tags[0]
        gate.update = update
        original_attitude = client.attitude
        def attitude(*axes):
            attempts.append(axes)
            if len(attempts) == 1:
                raise rejection("offline unsent correction")
            original_attitude(*axes)
        client.attitude = attitude
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]), \
                patch.object(shuttle.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0]+seconds)), \
                redirect_stdout(io.StringIO()):
            if rejection is shuttle.FramingCorrectionDeferred:
                shuttle.capture_id1_pair(client, limiter, stream, None, logger, config(), profile(), gate)
            else:
                with self.assertRaises(rejection):
                    shuttle.capture_id1_pair(client, limiter, stream, None, logger, config(), profile(), gate)
        return client, logger, observations, attempts

    def test_unsent_pulse_is_zeroed_then_reobserved_corrected_and_captured(self):
        client, logger, observations, attempts = self.exercise(shuttle.FramingCorrectionDeferred)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(observations), 3)
        self.assertEqual(len(set(observations)), 3)
        self.assertEqual(client.calls.count("zero"), 3)
        self.assertIsNone(client.motion_valid_until_s)
        self.assertEqual(len([x for x in client.events if x[0] == "id1_pair_correction_deferred"]), 1)
        logger.save_confirmation_photo.assert_called_once()
        self.assertFalse(client.pair_capture["tv_visibility_verified"])

    def test_sdk_rejection_or_uncertain_transport_is_not_retried(self):
        for rejection in (PermissionError, ConnectionError):
            with self.subTest(rejection=rejection):
                client, logger, observations, attempts = self.exercise(rejection)
                self.assertEqual(len(attempts), 1)
                self.assertEqual(client.calls.count("zero"), 1)
                logger.save_confirmation_photo.assert_not_called()


class CapturePostAckTests(StandaloneTestCase):
    def capture_with_zero_change(self, change, *, expect_abort=False):
        clock, client, logger = [100.], FakeClient(), MagicMock()
        client.raw.update(airborne_raw())
        client.pair_mode = True
        stream = fake_stream(clock)
        limiter = SimpleNamespace(wait=lambda: clock.__setitem__(0, clock[0] + .1))
        gate = SimpleNamespace(update=lambda *args: (0., args[0][0]),
                               diagnostic={"capture_ready": True, "footprint": {"fits": True}})
        zero_count = [0]
        original_zero, original_status = client.zero, client.status
        def zero():
            zero_count[0] += 1
            if zero_count[0] == 1:
                change(clock, client, stream)
            original_zero()
        def status(state):
            original_status(state)
            client.last_telemetry.velocity_north_mps = 0.
        client.zero, client.status = zero, status
        def save(*args, **kwargs):
            self.assertGreaterEqual(zero_count[0], 2, "The pre-zero framing decision was invalidated")
            return Path("offline-ID1.jpg")
        logger.save_confirmation_photo.side_effect = save
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]), \
                redirect_stdout(io.StringIO()):
            if expect_abort:
                with self.assertRaisesRegex(InterruptedError, "frame expired or changed"):
                    shuttle.capture_id1_pair(client, limiter, stream, None, logger, config(), profile(), gate)
            else:
                shuttle.capture_id1_pair(client, limiter, stream, None, logger, config(), profile(), gate)
        if expect_abort:
            logger.save_confirmation_photo.assert_not_called()
        else:
            logger.save_confirmation_photo.assert_called_once()

    def test_photo_waits_when_zero_ack_reports_horizontal_motion(self):
        self.capture_with_zero_change(lambda clock, client, stream:
            setattr(client.last_telemetry, "velocity_north_mps", .2))

    def test_photo_is_refused_when_exact_decision_frame_expires_during_zero_ack(self):
        self.capture_with_zero_change(
            lambda clock, client, stream: clock.__setitem__(0, clock[0] + .501), expect_abort=True)

    def test_photo_is_refused_when_exact_decision_frame_is_replaced_during_zero_ack(self):
        def replace_snapshot(clock, client, stream):
            original = stream.last_detection_snapshot
            stream.last_detection_snapshot = SimpleNamespace(
                key=(original.key[0], original.key[1]+1), received_s=clock[0], frame=original.frame)
        self.capture_with_zero_change(replace_snapshot, expect_abort=True)


if __name__ == "__main__":
    unittest.main()
