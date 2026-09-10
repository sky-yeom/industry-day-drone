"""Standalone route tests: fake aircraft, clocks and frames; network forbidden."""
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
from functools import partial
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
    return {"schema_version": 2, "wall_ids_left_to_right": [3, 2, 1, 6],
            "floor_tag_id": 0, "home_tag_id": 6, "route_ids": [6, 1, 2, 3, 2, 1, 6],
            "target_height_m": 1.4, "max_tilt_deg": 1.5,
            "visit_pause_s": 3., "leg_timeout_s": 45., "total_timeout_s": 240.,
            "layout_confirmed": True, "wall_measurement": "image_only",
            "floor_size_source": "private_config", **changes}


def config():
    base = load_config(ROOT / "pc" / "config.sample.json")
    return replace(base, camera=replace(base.camera, calibrated=True),
                   network=replace(base.network, host="127.0.0.1", confirmation_token="offline-fixture-token"))


def tag(tag_id, x=640., y=360.):
    return shuttle.PixelTag(tag_id, (x, y), ((x-30, y-30), (x+30, y-30),
                            (x+30, y+30), (x-30, y+30)), 75., 0)


class FakeClient:
    def __init__(self):
        self.raw = {"is_flying": False, "are_motors_on": False,
                    "is_flying_age_ms": 10., "are_motors_on_age_ms": 10.,
                    "armed": False, "vs_enabled": False, "vs_advanced_enabled": False,
                    "vs_authority": "RC", "bridge_build_id": shuttle.BUILD_ID,
                    "process_start_id": "offline", "telemetry_generation": 1}
        self.last_telemetry = SimpleNamespace(battery_percent=80., rc_override_age_s=None,
            height_m=1.4, height_age_s=.01, velocity_age_s=.01,
            flight_mode="GPS_NORMAL", flight_mode_age_s=.01,
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
    def test_exact_corrected_route_directions_follow_confirmed_wall_positions(self):
        p = shuttle.plan(profile())
        self.assertEqual(p["profile"]["route_ids"], [6, 1, 2, 3, 2, 1, 6])
        self.assertEqual([leg["direction"] for leg in p["legs"]], ["left"] * 3 + ["right"] * 3)
        self.assertEqual(p["finish"], "hover_release_to_RC_manual_landing")
        with self.assertRaises(ValueError):
            shuttle.planned_direction(6, 3)
        with self.assertRaises(ValueError):
            shuttle.validate_profile(profile(route_ids=[6, 3, 2, 1, 2, 3, 6]))

    def test_default_cli_only_prints_plan_even_with_unconfirmed_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps(profile(layout_confirmed=False)), encoding="utf-8")
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

    def test_route_home_measurement_policy_and_limits_are_strict(self):
        for changes in ({"home_tag_id": 2}, {"route_ids": [2, 1, 3, 1, 2]},
                        {"floor_tag_id": False}, {"tag_size_m": float("nan")},
                        {"wall_measurement": "metric"}, {"floor_size_source": "guess"},
                        {"max_tilt_deg": 3}, {"target_height_m": 1.8},
                        {"total_timeout_s": 1000}, {"unknown": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                shuttle.validate_profile(profile(**changes))

    def test_prepare_preserves_existing_sizes_poses_and_does_not_inject_home6(self):
        original = config()
        original = replace(original, tags=tuple(replace(item, size_m=.19 if item.id == 0 else .31)
                                                for item in original.tags))
        self.assertFalse(original.body_camera.calibrated)
        with patch.object(shuttle, "load_config", return_value=original):
            prepared = shuttle.prepare_config("unused", profile(), "127.0.0.2")
        self.assertEqual(prepared.network.host, "127.0.0.2")
        self.assertEqual(prepared.patrol.route_ids, (6, 1, 2, 3))
        self.assertNotIn(6, prepared.tag_map)
        self.assertEqual(prepared.tags, original.tags)
        self.assertEqual(prepared.tag_map[0].size_m, .19)
        self.assertEqual(prepared.tag_map[2].size_m, .31)
        self.assertEqual(prepared.body_camera, original.body_camera)
        self.assertEqual(prepared.actual_measurements_confirmed, original.actual_measurements_confirmed)
        self.assertEqual(prepared.patrol.obstacle_stop_m, 0.)
        self.assertFalse(prepared.patrol.align_cruise_yaw)
        self.assertNotIn(6, original.tag_map)

    def test_missing_layout_floor_or_camera_calibration_blocks_before_socket(self):
        with patch.object(shuttle, "load_config", return_value=config()), self.assertRaises(ValueError):
            shuttle.prepare_config("unused", profile(layout_confirmed=False))
        missing_floor = replace(config(), tags=tuple(item for item in config().tags if item.id != 0))
        with self.assertRaises(ValueError):
            shuttle.configure_execution(missing_floor, profile())
        uncalibrated = replace(config(), camera=replace(config().camera, calibrated=False))
        with patch.object(shuttle, "load_config", return_value=uncalibrated), self.assertRaises(ValueError):
            shuttle.prepare_config("unused", profile())

    def test_offline_check_requires_no_control_or_video_socket_and_does_not_print_token(self):
        with patch.object(shuttle, "load_profile", return_value=profile()), \
                patch.object(shuttle, "load_config", return_value=config()), \
                patch.object(shuttle, "MixedDetector") as detector, \
                patch.object(shuttle, "ShuttleClient", side_effect=AssertionError("No control")), \
                patch.object(shuttle, "FreshVideoStream", side_effect=AssertionError("No video")), \
                patch.dict(sys.modules, {"av": SimpleNamespace()}), redirect_stdout(io.StringIO()) as output:
            result = shuttle.main(["--check", "--config", "offline.json"])
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["setup_ready"])
        self.assertEqual(payload["floor_tag_size_m"], config().tag_map[0].size_m)
        self.assertEqual(payload["network_connections_opened"], 0)
        self.assertFalse(payload["aircraft_connection_verified"])
        self.assertNotIn(config().network.confirmation_token, output.getvalue())
        detector.assert_called_once()


class HorizontalGateTests(StandaloneTestCase):
    def gate(self):
        gate = shuttle.HorizontalGate(3, "left", 640., 1.5, config().patrol)
        gate.update = partial(gate.update, frame_shape=(720, 1280, 3))
        return gate

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

    def test_off_center_expected_tag_is_accepted_without_point_chasing(self):
        gate = self.gate()
        right, found = gate.update([tag(3, 1000., 600.)], 0., .01, (1, 1))
        self.assertEqual(right, 0.)
        self.assertIsNone(found)
        right, found = gate.update([tag(3, 1050., 600.)], .31, .01, (1, 2))
        self.assertEqual(right, 0.)
        self.assertEqual(found.tag_id, 3)

    def test_near_edge_approach_preserves_direction_and_passed_edge_never_reverses(self):
        gate = self.gate()
        right, found = gate.update([tag(3, 100., 360.)], 0., .01, (1, 1))
        self.assertLess(right, 0.)
        self.assertLessEqual(abs(right), 1.5)
        self.assertIsNone(found)
        with self.assertRaisesRegex(RuntimeError, "passed_view_no_reverse"):
            gate.update([tag(3, 1150., 360.)], .1, .01, (1, 2))

    def test_outside_vertical_window_cannot_trigger_height_or_reverse_chase(self):
        with self.assertRaisesRegex(RuntimeError, "vertical_outside"):
            self.gate().update([tag(3, 640., 20.)], 0., .01, (1, 1))

    def test_return_leg_uses_only_right_or_zero(self):
        gate = shuttle.HorizontalGate(2, "right", 640., 1.5, config().patrol)
        commands = []
        for index, x in enumerate((1200., 1000., 750., 500., 300.)):
            right, found = gate.update([tag(2, x, 360.)], index * .11, .01, (1, index + 1), (720, 1280, 3))
            commands.append(right)
        self.assertTrue(all(right >= 0. for right in commands))
        self.assertEqual(found.tag_id, 2)

    def test_real_traversal_sends_only_right_tilt_then_zero(self):
        clock = [100.]
        client, logger = FakeClient(), MagicMock()
        client.arm("offline")
        client.raw["is_flying"] = True
        frames = iter(([tag(6)], [tag(1, 300.)], [tag(1)], [tag(1)], [tag(1)], [tag(1)]))
        stream = SimpleNamespace(last_detection_snapshot=None)
        def detect(_detector, _age):
            frame_id = 1 if stream.last_detection_snapshot is None else stream.last_detection_snapshot.key[1] + 1
            stream.last_detection_snapshot = SimpleNamespace(key=(1, frame_id), frame=SimpleNamespace(shape=(720, 1280, 3)))
            return next(frames), .01
        stream.detect_latest = detect
        limiter = SimpleNamespace(wait=lambda: clock.__setitem__(0, clock[0] + .11))
        cfg = replace(config(), camera=replace(config().camera, cx=640.))
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]), \
                redirect_stdout(io.StringIO()):
            result = shuttle.traverse_horizontal(client, limiter, stream, None, logger, cfg, profile(),
                                                 6, 1, shuttle.PatrolPhase.OUTBOUND)
        self.assertEqual(result.tag_id, 1)
        commands = [item[1] for item in client.calls if isinstance(item, tuple) and item[0] == "attitude"]
        self.assertGreater(len(commands), 0)
        for forward, right, up, yaw in commands:
            self.assertEqual((forward, up, yaw), (0., 0., 0.))
            self.assertLessEqual(abs(right), 1.5)
        self.assertIn("zero", client.calls)

    def test_recorded_id1_overshoot_positions_complete_without_right_correction(self):
        # Actual logged positions from 20260910T135321. This is an offline
        # controller regression using those positions, not a new flight result.
        positions = ((307.3896242772593, 303.03661637591654),
                     (1085.8591883913136, 273.68444006476386),
                     (1611.3319607989451, 252.9723836416164),
                     (1501.813638100111, 218.34580139257628))
        clock, client, logger = [100.], FakeClient(), MagicMock()
        client.arm("offline")
        client.raw["is_flying"] = True
        sequence = iter(positions)
        stream = SimpleNamespace(last_detection_snapshot=None)
        def detect(_detector, _age):
            frame_id = 1 if stream.last_detection_snapshot is None else stream.last_detection_snapshot.key[1] + 1
            stream.last_detection_snapshot = SimpleNamespace(key=(1, frame_id), frame=SimpleNamespace(shape=(1080, 1920, 3)))
            return [tag(1, *next(sequence))], .01
        stream.detect_latest = detect
        limiter = SimpleNamespace(wait=lambda: clock.__setitem__(0, clock[0] + .11))
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]), \
                redirect_stdout(io.StringIO()):
            found = shuttle.traverse_horizontal(client, limiter, stream, None, logger,
                                                config(), profile(), 6, 1, shuttle.PatrolPhase.OUTBOUND)
        self.assertEqual(found.tag_id, 1)
        motion = [call[1] for call in client.calls if isinstance(call, tuple) and call[0] == "attitude"]
        self.assertTrue(all(axes[1] <= 0. and axes[0] == axes[2] == axes[3] == 0. for axes in motion))
        self.assertEqual(motion, [])
        self.assertEqual(client.calls.count("zero"), 4)
        logger.save_confirmation_photo.assert_called_once()

    def test_invalid_decoding_quality_cannot_confirm_a_wall(self):
        for changes in ({"hamming": None}, {"hamming": 3}, {"decision_margin": None},
                        {"decision_margin": float("nan")}):
            gate = self.gate()
            observed = replace(tag(3), **changes)
            with self.subTest(changes=changes):
                self.assertIsNone(gate.update([observed], 0., .01, (1, 1))[1])
                self.assertIsNone(gate.update([observed], .4, .01, (1, 2))[1])


class PixelAndFloorSeparationTests(StandaloneTestCase):
    def detector(self, raw_ids, floor_outputs=()):
        raw = [SimpleNamespace(tag_id=tag_id, center=(640., 360.),
            corners=((600., 320.), (680., 320.), (680., 400.), (600., 400.)),
            hamming=0, decision_margin=90.) for tag_id in raw_ids]
        backend = MagicMock()
        backend.detect.return_value = raw
        base = SimpleNamespace(_detector=backend, _matrix=[], _distortion=[],
            _cv2=SimpleNamespace(undistort=lambda frame, matrix, distortion: frame),
            detect=MagicMock(return_value=list(floor_outputs)))
        factory = MagicMock(return_value=base)
        cfg = config()
        cfg = replace(cfg, tags=tuple(replace(item, size_m=.19 if item.id == 0 else .31)
                                       for item in cfg.tags))
        detector = shuttle.MixedDetector(cfg, detector_factory=factory)
        return detector, base, factory

    def test_unconfigured_home6_is_detected_without_any_metric_wall_pass(self):
        detector, base, factory = self.detector([1, 2, 3, 6])
        detections = detector.detect(SimpleNamespace(ndim=2))
        self.assertEqual([d.tag_id for d in detections], [1, 2, 3, 6])
        self.assertTrue(all(isinstance(d, shuttle.PixelTag) for d in detections))
        self.assertTrue(all(d.T_C_T is None and d.pose_error is None for d in detections))
        self.assertEqual([t.id for t in factory.call_args.args[0].tags], [0])
        self.assertEqual(factory.call_args.args[0].tag_map[0].size_m, .19)
        base._detector.detect.assert_called_once_with(unittest.mock.ANY, estimate_tag_pose=False)
        base.detect.assert_not_called()

    def test_floor_keeps_real_pose_while_wall_pose_is_explicitly_unknown(self):
        floor = TagDetection(0, identity(), .002, (100., 100.))
        fake_wall_pose = TagDetection(6, identity(), .001, (640., 360.))
        detector, base, _ = self.detector([0, 6], [floor, fake_wall_pose])
        detections = detector.detect(SimpleNamespace(ndim=2))
        self.assertEqual(len(detections), 2)
        self.assertIs(detections[0], floor)
        self.assertIsInstance(detections[1], shuttle.PixelTag)
        self.assertIsNone(detections[1].T_C_T)
        wall_log = shuttle.ShuttleDetectionLogger.payload(detections[1],
            phase=shuttle.PatrolPhase.WALL_HOME, expected_id=6, frame_age_s=.01,
            telemetry=None, direction=None)
        for name in ("range_m", "camera_translation_m", "camera_to_tag_transform", "pose_error"):
            self.assertIsNone(wall_log[name])
        self.assertFalse(wall_log["metric_pose_available"])
        self.assertEqual(wall_log["measurement"], "image_only")
        floor_log = shuttle.ShuttleDetectionLogger.payload(floor,
            phase=shuttle.PatrolPhase.FLOOR_HOME, expected_id=0, frame_age_s=.01,
            telemetry=None, direction=None)
        self.assertTrue(floor_log["metric_pose_available"])
        self.assertEqual(floor_log["camera_to_tag_transform"], [list(row) for row in floor.T_C_T])
        base.detect.assert_called_once()

    def test_full_pixel_route_including_home6_needs_no_home_size_configuration(self):
        cfg = config()
        self.assertNotIn(6, cfg.tag_map)
        cfg = shuttle.configure_execution(cfg, profile())
        cfg = replace(cfg, camera=replace(cfg.camera, cx=640.))
        self.assertNotIn(6, cfg.tag_map)
        client, logger, clock = FakeClient(), MagicMock(), [100.]
        client.arm("offline")
        client.raw["is_flying"] = True
        pending = []
        stream = SimpleNamespace(last_detection_snapshot=None)
        def detect(_detector, _age):
            frame_id = 1 if stream.last_detection_snapshot is None else stream.last_detection_snapshot.key[1] + 1
            stream.last_detection_snapshot = SimpleNamespace(key=(1, frame_id), frame=SimpleNamespace(shape=(720, 1280, 3)))
            return pending.pop(0), .01
        stream.detect_latest = detect
        limiter = SimpleNamespace(wait=lambda: clock.__setitem__(0, clock[0] + .11))
        visits = []
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]), \
                redirect_stdout(io.StringIO()):
            pending.extend([[tag(3)], [tag(6, 300.)], *[[tag(6)]] * 4])
            home = shuttle.acquire_wall_home(client, limiter, stream, None, logger, cfg)
            visits.append(home.tag_id)
            self.assertFalse(any(isinstance(call, tuple) and call[0] == "attitude" for call in client.calls))
            for departure, expected in zip(shuttle.ROUTE_IDS, shuttle.ROUTE_IDS[1:]):
                pending.extend([[tag(departure)], *[[tag(expected)]] * 4])
                result = shuttle.traverse_horizontal(client, limiter, stream, None, logger, cfg, profile(),
                                                     departure, expected, shuttle.PatrolPhase.OUTBOUND)
                visits.append(result.tag_id)
        self.assertEqual(visits, [6, 1, 2, 3, 2, 1, 6])
        self.assertEqual(pending, [])
        for call in client.calls:
            if isinstance(call, tuple) and call[0] == "attitude":
                forward, right, up, yaw = call[1]
                self.assertEqual((forward, up, yaw), (0., 0., 0.))


class MissionLifecycleTests(StandaloneTestCase):
    def run_fake(self, client, leg_error=None, real_settle=False, limiter=None):
        acquisitions = []
        legs = []
        def acquire(_client, _limiter, _stream, _detector, _logger, gate, _patrol, **kwargs):
            acquisitions.append(gate.expected_id)
            gate.confirm(gate.expected_id)
        def acquire_home(*args):
            acquisitions.append(6)
            return tag(6)
        def traverse(_client, _limiter, _stream, _detector, _logger, _config, _profile, departure, expected, phase):
            legs.append((departure, expected))
            if leg_error:
                raise leg_error
        with ExitStack() as stack:
            for name, replacement in (
                ("ShuttleClient", lambda *args: client), ("MixedDetector", MagicMock()),
                ("FreshVideoStream", MagicMock()), ("ShuttleDetectionLogger", MagicMock()),
                ("_wait_ground_video", MagicMock()), ("_climb", MagicMock()),
                ("_pause", MagicMock()), ("_acquire_tag", acquire),
                ("acquire_wall_home", acquire_home),
                ("traverse_horizontal", traverse),
                ("_wait_takeoff_settled", shuttle._wait_takeoff_settled if real_settle else MagicMock()),
                ("RateLimiter", MagicMock(return_value=limiter) if limiter is not None else MagicMock()),
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
        self.assertEqual(legs, [(6, 1)])
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

    def takeoff_script(self, samples):
        client, clock, history, arm_times = FakeClient(), [100.], [], []
        original_status, original_arm = client.status, client.arm
        index = [0]
        def status(state):
            original_status(state)
            if state != "standalone_takeoff_settle":
                return
            values = samples[min(index[0], len(samples) - 1)]
            index[0] += 1
            for name, value in values.items():
                if name == "received_age_s":
                    client.received -= value
                elif name == "is_flying":
                    client.raw["is_flying"] = value
                else:
                    setattr(client.last_telemetry, name, value)
            history.append((clock[0], client.last_telemetry.flight_mode,
                            client.last_telemetry.velocity_down_mps))
        def arm(token):
            arm_times.append(clock[0])
            original_arm(token)
        client.status, client.arm = status, arm
        limiter = SimpleNamespace(wait=lambda: clock.__setitem__(0, clock[0] + .1))
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]):
            result, acquisitions, legs = self.run_fake(client, real_settle=True, limiter=limiter)
        return result, client, history, arm_times

    def test_automatic_takeoff_then_unstable_idle_waits_before_one_arm(self):
        samples = ([{"flight_mode": "AUTO_TAKE_OFF", "height_m": .5,
                     "velocity_down_mps": -.4}] * 10
                   + [{"flight_mode": "GPS_NORMAL", "height_m": 1.2,
                       "velocity_down_mps": -.2}] * 10
                   + [{"flight_mode": "GPS_NORMAL", "height_m": 1.2,
                       "velocity_down_mps": 0.}] * 21)
        result, client, history, arm_times = self.takeoff_script(samples)
        self.assertTrue(result["route_completed"])
        self.assertEqual(client.calls.count("takeoff"), 1)
        self.assertEqual(client.calls.count("arm"), 1)
        self.assertEqual(len(arm_times), 1)
        self.assertGreaterEqual(arm_times[0] - history[20][0], 2. - 1e-8)
        self.assertEqual(history[-1][1:], ("GPS_NORMAL", 0.))

    def test_takeoff_mode_timeout_never_arms_even_if_height_and_speed_look_stable(self):
        result, client, history, arm_times = self.takeoff_script([
            {"flight_mode": "AUTO_TAKE_OFF", "height_m": 1.4, "velocity_down_mps": 0.}])
        self.assertFalse(result["route_completed"])
        self.assertIn("did not settle within 20s", result["error"])
        self.assertEqual(client.calls.count("takeoff"), 1)
        self.assertNotIn("arm", client.calls)
        self.assertEqual(arm_times, [])
        self.assertGreaterEqual(history[-1][0], 120. - 1e-8)

    def test_takeoff_stability_hold_restarts_after_automatic_mode_returns(self):
        samples = ([{"flight_mode": "GPS_NORMAL", "velocity_down_mps": 0.}] * 10
                   + [{"flight_mode": "AUTO_TAKE_OFF", "velocity_down_mps": 0.}]
                   + [{"flight_mode": "GPS_NORMAL", "velocity_down_mps": 0.}] * 21)
        result, client, history, arm_times = self.takeoff_script(samples)
        self.assertTrue(result["route_completed"])
        self.assertEqual(client.calls.count("arm"), 1)
        self.assertGreaterEqual(arm_times[0] - history[11][0], 2. - 1e-8)

    def test_stale_takeoff_evidence_rc_override_or_high_altitude_never_arms(self):
        for sample in ({"height_age_s": .3, "received_age_s": .3},
                       {"flight_mode_age_s": .6}, {"velocity_age_s": .6},
                       {"rc_override_age_s": .1}, {"height_m": 1.81}):
            with self.subTest(sample=sample):
                result, client, _, arm_times = self.takeoff_script([sample])
                self.assertFalse(result["route_completed"])
                self.assertNotIn("arm", client.calls)
                self.assertEqual(arm_times, [])


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
