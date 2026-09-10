"""Standalone route tests: fake aircraft, clocks and frames; network forbidden."""
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "trials"))
import standalone_tag_shuttle as shuttle
from drone_nav.config import load_config
from drone_nav.localization import TagDetection
from drone_nav.transforms import identity
from drone_nav.tool_control.live import DeadlineTransport


def profile(**changes):
    return {"schema_version": 1, "wall_ids_left_to_right": [1, 2, 3, 6],
            "floor_tag_id": 0, "home_tag_id": 6, "route_ids": [6, 3, 2, 1, 2, 3, 6],
            "target_height_m": 1.4, "tag_size_m": .15, "max_tilt_deg": 1.5,
            "visit_pause_s": 3., "leg_timeout_s": 45., "total_timeout_s": 240.,
            "layout_confirmed": True, "tag_size_confirmed": True, **changes}


def config():
    base = load_config(ROOT / "pc" / "config.sample.json")
    return replace(base, camera=replace(base.camera, calibrated=True),
                   network=replace(base.network, host="127.0.0.1", confirmation_token="offline-fixture-token"))


def tag(tag_id, x=640., y=360.):
    return TagDetection(tag_id, identity(), .01, (x, y))


class FakeClient:
    def __init__(self):
        self.raw = {"is_flying": False, "are_motors_on": False,
                    "is_flying_age_ms": 10., "are_motors_on_age_ms": 10.,
                    "armed": False, "vs_enabled": False, "vs_advanced_enabled": False,
                    "vs_authority": "RC", "bridge_build_id": shuttle.BUILD_ID,
                    "process_start_id": "offline", "telemetry_generation": 1}
        self.last_telemetry = SimpleNamespace(battery_percent=80., rc_override_age_s=None,
            height_m=1.4, height_age_s=.01, velocity_age_s=.01,
            velocity_north_mps=0., velocity_east_mps=0., velocity_down_mps=0.)
        self.received = time.perf_counter()
        self._socket, self.attempted_action = None, False
        self.session_id, self.log_path = "offline", None
        self.calls, self.events = [], []
        self.cleaning, self.fail_verification = False, False

    def connect(self):
        self.calls.append("connect")
        self._socket = object()

    def log_event(self, event, data):
        self.events.append((event, data))

    def status(self, state):
        self.calls.append(("status", state))
        if state == "standalone_release_verification" and self.fail_verification:
            raise ConnectionError("verification unavailable")
        self.received = time.perf_counter()

    def gimbal_down(self):
        self.attempted_action = True
        self.calls.append("gimbal_down")

    def gimbal(self, value):
        self.calls.append(("gimbal", value))

    def stick_mode(self, mode):
        self.calls.append(("stick_mode", mode))

    def takeoff(self, token):
        self.attempted_action = True
        self.calls.append("takeoff")
        self.raw.update(is_flying=True, are_motors_on=True)

    def arm(self, token):
        self.calls.append("arm")
        self.raw.update(armed=True, vs_enabled=True, vs_advanced_enabled=True, vs_authority="MSDK")

    def zero(self):
        self.calls.append("zero")
        self.received = time.perf_counter()

    def attitude(self, *axes):
        self.calls.append(("attitude", axes))
        self.received = time.perf_counter()

    def disarm(self):
        self.calls.append("disarm")
        self.raw.update(armed=False, vs_enabled=False, vs_advanced_enabled=False, vs_authority="RC")

    def close(self):
        self.calls.append("close")


class StandaloneTestCase(unittest.TestCase):
    def setUp(self):
        guard = patch("socket.create_connection", side_effect=AssertionError("Real network forbidden"))
        guard.start()
        self.addCleanup(guard.stop)


class PlanAndConfigurationTests(StandaloneTestCase):
    def test_exact_user_route_has_three_left_then_three_right_legs(self):
        p = shuttle.plan(profile())
        self.assertEqual(p["profile"]["route_ids"], [6, 3, 2, 1, 2, 3, 6])
        self.assertEqual([leg["direction"] for leg in p["legs"]], ["left"] * 3 + ["right"] * 3)
        self.assertEqual(p["finish"], "hover_release_to_RC_manual_landing")
        with self.assertRaises(ValueError):
            shuttle.planned_direction(6, 1)

    def test_default_cli_only_prints_plan_even_with_unconfirmed_size(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps(profile(tag_size_confirmed=False)), encoding="utf-8")
            with patch.object(shuttle, "MissionClient", side_effect=AssertionError("No control client")), \
                    patch.object(shuttle, "FreshVideoStream", side_effect=AssertionError("No video")), \
                    patch.object(shuttle, "load_config", side_effect=AssertionError("No private config read")), \
                    redirect_stdout(io.StringIO()) as output:
                result = shuttle.main(["--profile", str(path)])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())["network_connections_opened"], 0)

    def test_execute_requires_explicit_private_config_before_io(self):
        with patch.object(shuttle, "load_profile", return_value=profile()), \
                patch.object(shuttle, "MissionClient", side_effect=AssertionError("No control client")), \
                patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                shuttle.main(["--execute"])
            self.assertEqual(error.exception.code, 2)

    def test_route_home_size_and_limits_are_strict(self):
        for changes in ({"home_tag_id": 2}, {"route_ids": [2, 1, 3, 1, 2]},
                        {"floor_tag_id": False}, {"tag_size_m": float("nan")},
                        {"max_tilt_deg": 3}, {"target_height_m": 1.8},
                        {"total_timeout_s": 1000}, {"unknown": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                shuttle.validate_profile(profile(**changes))

    def test_prepare_preserves_calibration_truth_and_adds_unsurveyed_home6(self):
        original = config()
        self.assertFalse(original.body_camera.calibrated)
        with patch.object(shuttle, "load_config", return_value=original):
            prepared = shuttle.prepare_config("unused", profile(), "127.0.0.2")
        self.assertEqual(prepared.network.host, "127.0.0.2")
        self.assertEqual(prepared.patrol.route_ids, (6, 3, 2, 1))
        self.assertIsNone(prepared.tag_map[6].world_pose)
        self.assertEqual(prepared.tag_map[6].size_m, .15)
        self.assertEqual(prepared.body_camera, original.body_camera)
        self.assertEqual(prepared.actual_measurements_confirmed, original.actual_measurements_confirmed)
        self.assertEqual(prepared.patrol.obstacle_stop_m, 0.)
        self.assertFalse(prepared.patrol.align_cruise_yaw)
        self.assertNotIn(6, original.tag_map)

    def test_missing_size_or_camera_calibration_blocks_execution_before_socket(self):
        with patch.object(shuttle, "load_config", return_value=config()), self.assertRaises(ValueError):
            shuttle.prepare_config("unused", profile(tag_size_confirmed=False))
        uncalibrated = replace(config(), camera=replace(config().camera, calibrated=False))
        with patch.object(shuttle, "load_config", return_value=uncalibrated), self.assertRaises(ValueError):
            shuttle.prepare_config("unused", profile())


class HorizontalGateTests(StandaloneTestCase):
    def gate(self):
        return shuttle.HorizontalGate(3, "left", 640., 1.5)

    def test_unexpected_tags_do_not_confirm_or_reverse_expected_leg(self):
        gate = self.gate()
        for index, now in enumerate((0., .4, .8), 1):
            right, confirmed = gate.update([tag(2), tag(1), tag(6)], now, .01, (1, index))
            self.assertEqual(right, -1.5)
            self.assertIsNone(confirmed)

    def test_confirm_requires_centered_distinct_fresh_frames(self):
        gate = self.gate()
        self.assertIsNone(gate.update([tag(3)], 0., .01, (1, 1))[1])
        self.assertIsNone(gate.update([tag(3)], .4, .01, (1, 1))[1])
        self.assertEqual(gate.update([tag(3)], .41, .01, (1, 2))[1].tag_id, 3)

    def test_stale_missing_negative_and_nan_frame_age_are_rejected(self):
        for age, key in ((.501, (1, 2)), (-.01, (1, 2)), (float("nan"), (1, 2)), (.01, None)):
            with self.subTest(age=age, key=key), self.assertRaises(RuntimeError):
                self.gate().update([tag(3)], 1., age, key)

    def test_old_frame_or_video_reconnect_cannot_complete_hold(self):
        gate = self.gate()
        gate.update([tag(3)], 0., .01, (1, 10))
        self.assertEqual(gate.update([tag(3)], 1., .01, (1, 9)), (0., None))
        with self.assertRaises(InterruptedError):
            gate.update([tag(3)], 1., .01, (2, 11))

    def test_target_loss_holds_and_resets_confirmation(self):
        gate = self.gate()
        gate.update([tag(3)], 0., .01, (1, 1))
        self.assertEqual(gate.update([tag(2)], .2, .01, (1, 2)), (0., None))
        self.assertIsNone(gate.update([tag(3)], .5, .01, (1, 3))[1])
        self.assertEqual(gate.update([tag(3)], .81, .01, (1, 4))[1].tag_id, 3)

    def test_centering_uses_horizontal_error_only_and_obeys_tilt_limit(self):
        gate = self.gate()
        right, found = gate.update([tag(3, 100., -1000.)], 0., .01, (1, 1))
        self.assertEqual(right, -1.5)
        self.assertIsNone(found)
        right, found = gate.update([tag(3, 1100., 2000.)], .1, .01, (1, 2))
        self.assertEqual(right, 1.5)
        self.assertIsNone(found)

    def test_real_traversal_sends_only_right_tilt_then_zero(self):
        clock = [100.]
        client, logger = FakeClient(), MagicMock()
        client.arm("offline")
        client.raw["is_flying"] = True
        frames = iter(([tag(6)], [tag(3, 300.)], [tag(3)], [tag(3)], [tag(3)], [tag(3)]))
        stream = SimpleNamespace(last_detection_snapshot=None)
        def detect(_detector, _age):
            frame_id = 1 if stream.last_detection_snapshot is None else stream.last_detection_snapshot.key[1] + 1
            stream.last_detection_snapshot = SimpleNamespace(key=(1, frame_id))
            return next(frames), .01
        stream.detect_latest = detect
        limiter = SimpleNamespace(wait=lambda: clock.__setitem__(0, clock[0] + .11))
        cfg = replace(config(), camera=replace(config().camera, cx=640.))
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]), \
                redirect_stdout(io.StringIO()):
            result = shuttle.traverse_horizontal(client, limiter, stream, None, logger, cfg, profile(),
                                                 6, 3, shuttle.PatrolPhase.OUTBOUND)
        self.assertEqual(result.tag_id, 3)
        commands = [item[1] for item in client.calls if isinstance(item, tuple) and item[0] == "attitude"]
        self.assertGreater(len(commands), 0)
        for forward, right, up, yaw in commands:
            self.assertEqual((forward, up, yaw), (0., 0., 0.))
            self.assertLessEqual(abs(right), 1.5)
        self.assertIn("zero", client.calls)


class MissionLifecycleTests(StandaloneTestCase):
    def run_fake(self, client, leg_error=None):
        acquisitions = []
        legs = []
        def acquire(_client, _limiter, _stream, _detector, _logger, gate, _patrol, **kwargs):
            acquisitions.append(gate.expected_id)
            gate.confirm(gate.expected_id)
        def traverse(_client, _limiter, _stream, _detector, _logger, _config, _profile, departure, expected, phase):
            legs.append((departure, expected))
            if leg_error:
                raise leg_error
        with ExitStack() as stack:
            for name, replacement in (
                ("ShuttleClient", lambda *args: client), ("AprilTagDetector", MagicMock()),
                ("FreshVideoStream", MagicMock()), ("_DetectionLogger", MagicMock()),
                ("_wait_ground_video", MagicMock()), ("_climb", MagicMock()),
                ("_pause", MagicMock()), ("_acquire_tag", acquire),
                ("traverse_horizontal", traverse), ("RateLimiter", MagicMock()),
            ):
                stack.enter_context(patch.object(shuttle, name, replacement))
            stack.enter_context(redirect_stdout(io.StringIO()))
            result = shuttle.run(config(), profile())
        return result, acquisitions, legs

    def test_success_anchors_home6_and_returns_without_automatic_landing(self):
        client = FakeClient()
        result, acquisitions, legs = self.run_fake(client)
        self.assertEqual(acquisitions, [0, 6])
        self.assertEqual(legs, list(zip(shuttle.ROUTE_IDS, shuttle.ROUTE_IDS[1:])))
        self.assertEqual(result["visited_ids"], shuttle.ROUTE_IDS)
        self.assertEqual(result["state"], "awaiting_rc_landing")
        self.assertTrue(result["control_released_to_rc"])
        self.assertFalse(result["ground_verified"])
        self.assertTrue(result["manual_landing_required"])
        self.assertEqual(client.calls.count("takeoff"), 1)
        self.assertEqual(client.calls.count("arm"), 1)
        self.assertEqual(client.calls[-4:], ["zero", "disarm", ("status", "standalone_release_verification"), "close"])
        self.assertNotIn("land", client.calls)

    def test_failed_leg_runs_cleanup_no_rearm_and_redacts_token(self):
        client = FakeClient()
        result, _, legs = self.run_fake(client, RuntimeError("offline-fixture-token failed"))
        self.assertFalse(result["route_completed"])
        self.assertEqual(result["visited_ids"], [6])
        self.assertEqual(legs, [(6, 3)])
        self.assertEqual(client.calls.count("connect"), 1)
        self.assertEqual(client.calls.count("takeoff"), 1)
        self.assertEqual(client.calls.count("arm"), 1)
        self.assertTrue(result["control_released_to_rc"])
        self.assertNotIn("offline-fixture-token", result["error"])
        self.assertIn("<redacted>", result["error"])

    def test_missing_release_ack_never_claims_control_or_stop(self):
        client = FakeClient()
        client.fail_verification = True
        result, _, _ = self.run_fake(client, RuntimeError("failed"))
        self.assertEqual(result["state"], "outcome_unknown")
        self.assertFalse(result["control_released_to_rc"])
        self.assertFalse(result["physical_stop_confirmed"])
        self.assertFalse(result["ground_verified"])

    def test_old_app_stale_ground_or_motor_on_never_take_off(self):
        for changes in ({"bridge_build_id": "old"}, {"is_flying_age_ms": 2000}, {"are_motors_on": True}):
            with self.subTest(changes=changes):
                client = FakeClient()
                client.raw.update(changes)
                result, _, legs = self.run_fake(client)
                self.assertFalse(result["route_completed"])
                self.assertEqual(legs, [])
                self.assertNotIn("takeoff", client.calls)
                self.assertNotIn("arm", client.calls)

    def test_rc_label_without_fresh_flight_state_is_not_release_proof(self):
        client = FakeClient()
        client.raw["is_flying_age_ms"] = 5000.
        result = shuttle.release_to_rc(client)
        self.assertFalse(result["control_released_to_rc"])
        self.assertFalse(result["physical_stop_confirmed"])

    def test_elapsed_telemetry_cannot_admit_lateral_motion(self):
        client = FakeClient()
        client.raw.update(is_flying=True, armed=True, vs_enabled=True, vs_advanced_enabled=True, vs_authority="MSDK")
        client.raw["is_flying_age_ms"] = 0.
        client.received = time.perf_counter() - 1.
        with self.assertRaises(InterruptedError):
            shuttle._require_flight(client)

    def test_public_run_rejects_uncalibrated_config_before_opening_socket(self):
        cfg = config()
        cfg = replace(cfg, camera=replace(cfg.camera, calibrated=False))
        with patch.object(shuttle, "ShuttleClient", side_effect=AssertionError("No client")), \
                self.assertRaises(ValueError):
            shuttle.run(cfg, profile())

    def test_result_log_failure_still_closes_all_resources(self):
        client = FakeClient()
        original_log = client.log_event
        def log(event, data):
            if event == "standalone_result":
                raise OSError("disk full")
            original_log(event, data)
        client.log_event = log
        result, _, _ = self.run_fake(client)
        self.assertEqual(client.calls[-1], "close")
        self.assertIn("result_log:OSError", result["cleanup_errors"])

    def test_ground_video_can_be_admitted_without_floor_tag_before_takeoff(self):
        client, logger, clock = FakeClient(), MagicMock(), [100.]
        stream = SimpleNamespace(last_detection_snapshot=None)
        def detect(_detector, _age):
            frame_id = 1 if stream.last_detection_snapshot is None else stream.last_detection_snapshot.key[1] + 1
            stream.last_detection_snapshot = SimpleNamespace(key=(1, frame_id))
            return [], .01
        stream.detect_latest = detect
        limiter = SimpleNamespace(wait=lambda: clock.__setitem__(0, clock[0] + .11))
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]):
            shuttle._wait_ground_video(client, limiter, stream, None, logger)
        self.assertNotIn("takeoff", client.calls)
        self.assertTrue(all(isinstance(call, tuple) and call[0] == "status" for call in client.calls))
        self.assertGreaterEqual(logger.observations.call_count, 4)

    def test_release_ages_include_time_since_ack_not_only_callback_age(self):
        client = FakeClient()
        client.received = time.perf_counter() - .3
        client.raw.update(is_flying_age_ms=300., are_motors_on_age_ms=300.)
        client.last_telemetry.velocity_age_s = .3
        client.status = lambda state: None
        client.zero = lambda: None
        result = shuttle.release_to_rc(client)
        self.assertFalse(result["control_released_to_rc"])
        self.assertFalse(result["ground_verified"])
        self.assertFalse(result["physical_stop_confirmed"])


class DispatchBoundaryTests(StandaloneTestCase):
    def client(self, clock):
        cfg = shuttle.configure_execution(config(), profile())
        client = shuttle.ShuttleClient(cfg, threading.Event(), lambda snapshot: None)
        client.phase = "lateral"
        client.stream = SimpleNamespace(
            last_detection_snapshot=SimpleNamespace(key=(1, 1), received_s=clock[0] - .4),
            read=lambda: (True, None, .01))
        return client

    def test_post_status_rtt_can_expire_detection_and_prevent_motion_write(self):
        clock = [100.]
        raw_state = {**FakeClient().raw, "is_flying": True, "are_motors_on": True,
                     "armed": True, "vs_enabled": True, "vs_advanced_enabled": True,
                     "vs_authority": "MSDK", "height_m": 1.4, "height_age_ms": 10.,
                     "velocity_age_ms": 10., "attitude_age_ms": 10., "yaw_deg": 0.,
                     "velocity_north_mps": 0., "velocity_east_mps": 0.,
                     "velocity_down_mps": 0., "battery_percent": 80.}
        class AckSocket:
            def __init__(self):
                self.writes, self.pending = [], b""
            def settimeout(self, value):
                pass
            def sendall(self, data):
                request = json.loads(data)
                self.writes.append(request["type"])
                self.pending = (json.dumps({"version": 1, "sequence": request["sequence"],
                    "timestamp_ns": request["sequence"] + 1, "type": "ack",
                    "payload": {"ok": True, "detail": "fixture", "telemetry": raw_state}}) + "\n").encode()
            def recv(self, size):
                clock[0] += .2
                data, self.pending = self.pending[:size], self.pending[size:]
                return data
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]):
            client = self.client(clock)
            raw = AckSocket()
            client.owner, client._armed = threading.get_ident(), True
            client._socket = client._file = DeadlineTransport(raw)
            with self.assertRaises(InterruptedError):
                client.attitude(0., -1.5, 0., 0.)
        self.assertEqual(raw.writes, ["status"])

    def test_lateral_and_climb_axis_restrictions_apply_to_direct_send(self):
        clock = [100.]
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]):
            client = self.client(clock)
            for phase, axes in (("lateral", (1., 0., 0., 0.)), ("lateral", (0., 0., .1, 0.)),
                                ("lateral", (0., 0., 0., .1)), ("climb", (0., 1., .1, 0.)),
                                ("climb", (0., 0., -.1, 0.)), ("hover", (0., 1., 0., 0.))):
                client.phase = phase
                payload = dict(zip(("forward_tilt_deg", "right_tilt_deg", "up_mps", "yaw_rate_rps"), axes))
                with self.subTest(phase=phase, axes=axes), self.assertRaises(PermissionError):
                    client.send("attitude", payload)

    def test_video_generation_stays_pinned_across_phases(self):
        clock = [100.]
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]):
            client = self.client(clock)
            client.observe_frame(client.stream.last_detection_snapshot)
            client.phase = "hover"
            with self.assertRaises(InterruptedError):
                client.observe_frame(SimpleNamespace(key=(2, 2), received_s=100.))
            client.phase = "climb"
            client.stream.last_detection_snapshot = SimpleNamespace(key=(2, 2), received_s=100.)
            with self.assertRaises(InterruptedError):
                client.send("attitude", {"forward_tilt_deg": 0., "right_tilt_deg": 0.,
                                         "up_mps": .1, "yaw_rate_rps": 0.})


if __name__ == "__main__":
    unittest.main()
