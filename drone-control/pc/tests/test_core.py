from __future__ import annotations

import json
import math
from pathlib import Path
import unittest

from drone_nav.config import load_config
from drone_nav.controller import GoalVisualAligner, LineFollower, Velocity
from drone_nav.estimator import PoseEstimator
from drone_nav.localization import TagDetection, TagLocalizer
from drone_nav.protocol import (
    ControlResult,
    NDJSONClient,
    Protocol,
    Telemetry,
    assess_motion,
)
from drone_nav.sdk_api import SdkApiClient, parse_sdk_response
from drone_nav.runtime import (
    _diagnostic_leg_done,
    _diagnostic_velocity,
    _directional_speed,
    _nearest_obstacle_m,
    _opposite_direction,
    _shutdown,
)
from drone_nav.safety import SafetyFSM, State
from drone_nav.simulator import run_simulation
from drone_nav.transforms import (
    from_xyz_rpy,
    identity,
    inverse_rigid,
    matmul,
    translation,
    yaw,
)

CONFIG = Path(__file__).parents[1] / "config.sample.json"


class TransformTests(unittest.TestCase):
    def test_rigid_inverse(self) -> None:
        pose = from_xyz_rpy(1.2, -0.3, 0.8, 0.2, -0.1, 0.7)
        product = matmul(pose, inverse_rigid(pose))
        for actual, expected in zip(sum(product, ()), sum(identity(), ())):
            self.assertAlmostEqual(actual, expected, places=10)

    def test_required_localization_equation(self) -> None:
        config = load_config(CONFIG)
        actual = from_xyz_rpy(1.1, 0.7, 1.0, 0.0, 0.0, 0.2)
        tag = config.start_tag.world_pose.matrix()
        camera_tag = matmul(
            matmul(inverse_rigid(config.body_camera.matrix()), inverse_rigid(actual)),
            tag,
        )
        recovered = TagLocalizer(config).candidate_pose(
            TagDetection(0, camera_tag, 0.01)
        )
        for got, wanted in zip(sum(recovered, ()), sum(actual, ())):
            self.assertAlmostEqual(got, wanted, places=9)


class LocalizationTests(unittest.TestCase):
    def test_fusion_rejects_outlier_and_ema(self) -> None:
        config = load_config(CONFIG)
        localizer = TagLocalizer(config)

        def detection(tag_id: int, body_x: float, error: float) -> TagDetection:
            body = from_xyz_rpy(body_x, 0.7, 1.0, 0.0, 0.0, 0.0)
            camera_tag = matmul(
                matmul(
                    inverse_rigid(config.body_camera.matrix()),
                    inverse_rigid(body),
                ),
                config.tag_map[tag_id].world_pose.matrix(),
            )
            return TagDetection(tag_id, camera_tag, error)

        fused = localizer.update(
            [detection(0, 1.0, 0.02), detection(2, 1.02, 0.01), detection(0, 3.0, 0.02)]
        )
        self.assertIsNotNone(fused)
        self.assertAlmostEqual(translation(fused)[0], 1.0133, places=2)
        moved = localizer.update([detection(0, 1.2, 0.01)])
        self.assertGreater(translation(moved)[0], translation(fused)[0])
        self.assertLess(translation(moved)[0], 1.2)

    def test_consensus_beats_single_high_confidence_outlier(self) -> None:
        config = load_config(CONFIG)
        localizer = TagLocalizer(config)

        def detection(tag_id: int, body_x: float, error: float) -> TagDetection:
            body = from_xyz_rpy(body_x, 0.7, 1.0, 0.0, 0.0, 0.0)
            camera_tag = matmul(
                matmul(inverse_rigid(config.body_camera.matrix()), inverse_rigid(body)),
                config.tag_map[tag_id].world_pose.matrix(),
            )
            return TagDetection(tag_id, camera_tag, error)

        fused = localizer.update(
            [
                detection(0, 1.0, 0.02),
                detection(2, 1.02, 0.02),
                detection(0, 3.0, 0.0001),
            ]
        )
        self.assertLess(translation(fused)[0], 1.1)


class ControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG).controller

    def test_speed_clamp_slowdown_and_world_to_body(self) -> None:
        limit = self.config.max_speed_mps
        follower = LineFollower((0.0, 0.0), (4.0, 0.0), self.config)
        command = follower.command((0.0, 0.0), math.pi / 2)
        self.assertLessEqual(command.speed, limit)
        self.assertAlmostEqual(command.forward, 0.0, places=7)
        self.assertAlmostEqual(command.right, -limit, places=7)
        near = follower.command((3.9, 0.0), 0.0)
        self.assertLess(near.speed, command.speed)
        self.assertTrue(follower.command((3.95, 0.0), 0.0).is_zero)

    def test_cross_track_correction_and_visual_alignment(self) -> None:
        follower = LineFollower((0.0, 0.0), (4.0, 0.0), self.config)
        self.assertLess(follower.command((1.0, 0.2), 0.0).right, 0.0)
        aligner = GoalVisualAligner(self.config, (640.0, 360.0))
        self.assertTrue(aligner.command((644.0, 363.0)).is_zero)
        self.assertLessEqual(aligner.command((900.0, 600.0)).speed, 0.05)


class SafetyTests(unittest.TestCase):
    def test_timeout_recovery_and_emergency_latch(self) -> None:
        fsm = SafetyFSM(load_config(CONFIG).safety)
        fsm.transition(State.FOLLOW_LINE, 0.0)
        for observe in (
            fsm.observe_tag,
            fsm.observe_network,
        ):
            observe(0.0)
        self.assertEqual(fsm.check(0.1), State.FOLLOW_LINE)
        self.assertEqual(fsm.check(0.6), State.NETWORK_LOST)
        fsm.emergency_stop(1.0)
        fsm.transition(State.FOLLOW_LINE, 1.1)
        self.assertEqual(fsm.state, State.EMERGENCY_STOP)
        self.assertTrue(fsm.must_hover)

    def test_exact_states_and_tag_network_thresholds(self) -> None:
        self.assertEqual(
            {state.value for state in State},
            {
                "IDLE", "WAIT_FOR_TAKEOFF", "INITIALIZE", "GIMBAL_DOWN",
                "LOCALIZE_START", "FOLLOW_LINE", "GOAL_APPROACH",
                "VISUAL_ALIGN", "HOVER", "TAG_LOST", "NETWORK_LOST",
                "EMERGENCY_STOP",
            },
        )
        fsm = SafetyFSM(load_config(CONFIG).safety)
        fsm.transition(State.FOLLOW_LINE, 0.0)
        fsm.observe_tag(0.0)
        fsm.observe_network(0.0)
        self.assertEqual(fsm.tag_scale(0.29), 1.0)
        self.assertAlmostEqual(fsm.tag_scale(0.65), 0.5)
        self.assertEqual(fsm.tag_scale(1.01), 0.0)
        self.assertEqual(fsm.state, State.TAG_LOST)
        fsm = SafetyFSM(load_config(CONFIG).safety)
        fsm.observe_network(0.0)
        self.assertEqual(fsm.network_action(0.5), "continue")
        self.assertEqual(fsm.network_action(0.51), "zero")
        self.assertEqual(fsm.network_action(2.01), "disable")


class ProtocolTests(unittest.TestCase):
    def test_round_trip_and_strict_sequence(self) -> None:
        sender, receiver = Protocol(), Protocol()
        first = sender.encode("heartbeat", {})
        message = receiver.decode(first)
        self.assertEqual(message.sequence, 0)
        with self.assertRaises(ValueError):
            receiver.decode(first)
        second = json.loads(sender.encode("status", {"state": "HOVER"}))
        second["sequence"] = 9
        with self.assertRaises(ValueError):
            receiver.decode((json.dumps(second) + "\n").encode())

    def test_velocity_value_object(self) -> None:
        self.assertTrue(Velocity(0.0, 0.0).is_zero)
        self.assertAlmostEqual(Velocity(0.3, 0.4).speed, 0.5)

    def test_nonzero_velocity_requires_arm(self) -> None:
        client = NDJSONClient("127.0.0.1", 1)
        sent: list[tuple[str, dict[str, object]]] = []
        client.send = (  # type: ignore[method-assign]
            lambda kind, payload, timeout_s=None: sent.append((kind, payload))
        )
        with self.assertRaises(PermissionError):
            client.velocity(Velocity(0.1, 0.0))
        client.arm("operator-confirmed")
        client.velocity(Velocity(0.1, 0.0))
        self.assertEqual([item[0] for item in sent], ["arm", "velocity"])

    def test_basic_and_advanced_stick_modes_are_selectable(self) -> None:
        client = NDJSONClient("127.0.0.1", 1)
        sent: list[tuple[str, dict[str, object]]] = []
        client.send = (  # type: ignore[method-assign]
            lambda kind, payload, timeout_s=None: sent.append((kind, payload))
        )
        client.stick_mode("basic")
        client.stick_mode("advanced")
        client.stick_mode("advanced_angle")
        client.stick_mode("advanced_direct")
        self.assertEqual(
            sent,
            [
                ("stick_mode", {"mode": "basic"}),
                ("stick_mode", {"mode": "advanced"}),
                ("stick_mode", {"mode": "advanced_angle"}),
                ("stick_mode", {"mode": "advanced_direct"}),
            ],
        )
        with self.assertRaises(ValueError):
            client.stick_mode("invalid")

    def test_takeoff_and_land_are_token_gated(self) -> None:
        client = NDJSONClient("127.0.0.1", 1)
        sent: list[tuple[str, dict[str, object]]] = []
        client.send = (  # type: ignore[method-assign]
            lambda kind, payload, timeout_s=None: sent.append((kind, payload))
        )
        with self.assertRaises(PermissionError):
            client.takeoff("")
        with self.assertRaises(PermissionError):
            client.land("")
        client.takeoff("operator-confirmed")
        client.land("operator-confirmed")
        self.assertEqual([item[0] for item in sent], ["takeoff", "land"])
        for _, payload in sent:
            self.assertEqual(payload, {"confirmation_token": "operator-confirmed"})

    def test_advanced_angle_attitude_is_explicit_and_token_gated(self) -> None:
        client = NDJSONClient("127.0.0.1", 1)
        sent: list[tuple[str, dict[str, object]]] = []
        client.send = (  # type: ignore[method-assign]
            lambda kind, payload, timeout_s=None: sent.append((kind, payload))
        )
        with self.assertRaises(PermissionError):
            client.attitude(0.0, -2.0)
        client.arm("operator-confirmed")
        client.attitude(0.0, -2.0, 0.1, 0.0)
        self.assertEqual(sent[-1][0], "attitude")
        self.assertEqual(sent[-1][1]["right_tilt_deg"], -2.0)
        client.attitude(0.0, -3.5)
        with self.assertRaises(ValueError):
            client.attitude(0.0, -5.1)

    def test_new_message_types_encode(self) -> None:
        protocol = Protocol()
        for kind, payload in (
            ("takeoff", {"confirmation_token": "token"}),
            ("land", {"confirmation_token": "token"}),
            ("emergency_stop", {}),
        ):
            document = json.loads(protocol.encode(kind, payload))
            self.assertEqual(document["type"], kind)

    def test_ack_telemetry_is_optional_and_parsed(self) -> None:
        sender, receiver = Protocol(), Protocol()
        receiver.decode(sender.encode("ack", {"ok": True, "detail": "plain"}))
        with_telemetry = sender.encode(
            "ack",
            {
                "ok": True,
                "detail": "with-telemetry",
                "telemetry": {
                    "velocity_north_mps": 0.1,
                    "velocity_east_mps": -0.2,
                    "velocity_age_ms": 40,
                    "yaw_deg": 12.5,
                    "pitch_deg": -2.0,
                    "roll_deg": 3.0,
                    "attitude_age_ms": 40,
                    "height_m": 0.8,
                    "height_age_ms": 50,
                    "is_flying": True,
                    "armed": True,
                    "vs_advanced_enabled": False,
                    "flight_mode": "VIRTUAL_STICK",
                    "flight_mode_age_ms": 20,
                    "oa_type": "CLOSE",
                    "oa_sensors_working": "left,fwd",
                    "oa_horizontal_switch_support": "SUPPORTED",
                    "oa_horizontal_enabled": False,
                    "oa_upward_enabled": False,
                    "oa_downward_enabled": False,
                    "vision_positioning_enabled": True,
                    "oa_horizontal_angle_interval_deg": 90,
                    "oa_horizontal_distances_mm": [1200, 800, 1500, 900],
                    "oa_horizontal_sample_count": 4,
                    "oa_upward_distance_mm": 700,
                    "oa_downward_distance_mm": 1150,
                    "oa_obstacle_data_age_ms": 25,
                    "time_watchdog_enabled": False,
                    "disconnect_release_enabled": True,
                    "stick_mode": "ADVANCED_DIRECT",
                    "direct_frames_sent": 22,
                    "direct_frames_succeeded": 21,
                    "direct_frames_failed": 1,
                    "direct_consecutive_failures": 0,
                    "direct_callback_age_ms": 15,
                    "direct_last_error": "sample error",
                    "sdk_submit_result": "SUBMITTED_DIRECT_WITH_CALLBACK",
                    "sdk_submit_sequence": 7,
                    "sdk_result": "SUCCEEDED",
                    "sdk_result_sequence": 7,
                    "setpoint_forward_tilt_deg": 0.0,
                    "setpoint_right_tilt_deg": -2.0,
                    "sdk_roll": -2.0,
                    "sdk_pitch": 0.0,
                    "sdk_roll_pitch_units": "degrees",
                    "sdk_roll_pitch_mode": "ANGLE",
                },
            },
        )
        message = receiver.decode(with_telemetry)
        telemetry = Telemetry.from_ack_payload(message.payload)
        self.assertIsNotNone(telemetry)
        self.assertAlmostEqual(telemetry.velocity_north_mps, 0.1)
        self.assertAlmostEqual(telemetry.velocity_age_s, 0.04)
        self.assertAlmostEqual(telemetry.pitch_deg, -2.0)
        self.assertAlmostEqual(telemetry.roll_deg, 3.0)
        self.assertAlmostEqual(telemetry.height_m, 0.8)
        self.assertTrue(telemetry.is_flying)
        self.assertFalse(telemetry.vs_advanced_enabled)
        self.assertEqual(telemetry.flight_mode, "VIRTUAL_STICK")
        self.assertEqual(telemetry.oa_type, "CLOSE")
        self.assertEqual(telemetry.oa_sensors_working, "left,fwd")
        self.assertEqual(telemetry.oa_horizontal_switch_support, "SUPPORTED")
        self.assertFalse(telemetry.oa_horizontal_enabled)
        self.assertFalse(telemetry.oa_upward_enabled)
        self.assertFalse(telemetry.oa_downward_enabled)
        self.assertTrue(telemetry.vision_positioning_enabled)
        self.assertEqual(telemetry.oa_horizontal_angle_interval_deg, 90)
        self.assertEqual(
            telemetry.oa_horizontal_distances_mm, (1200, 800, 1500, 900)
        )
        self.assertEqual(telemetry.oa_horizontal_sample_count, 4)
        self.assertEqual(telemetry.oa_upward_distance_mm, 700)
        self.assertEqual(telemetry.oa_downward_distance_mm, 1150)
        self.assertAlmostEqual(telemetry.oa_obstacle_data_age_s, 0.025)
        self.assertFalse(telemetry.time_watchdog_enabled)
        self.assertTrue(telemetry.disconnect_release_enabled)
        self.assertEqual(telemetry.direct_frames_sent, 22)
        self.assertEqual(telemetry.direct_frames_succeeded, 21)
        self.assertEqual(telemetry.direct_frames_failed, 1)
        self.assertAlmostEqual(telemetry.direct_callback_age_s, 0.015)
        self.assertEqual(telemetry.direct_last_error, "sample error")
        self.assertEqual(telemetry.sdk_submit_result, "SUBMITTED_DIRECT_WITH_CALLBACK")
        self.assertEqual(telemetry.sdk_result, "SUCCEEDED")
        self.assertEqual(telemetry.setpoint_right_tilt_deg, -2.0)
        self.assertEqual(telemetry.sdk_roll, -2.0)
        self.assertEqual(telemetry.sdk_roll_pitch_mode, "ANGLE")
        with self.assertRaises(ValueError):
            Protocol().encode("ack", {"ok": True, "detail": "x", "telemetry": []})

    def test_semantic_ack_is_parsed_for_realtime(self) -> None:
        result = ControlResult.from_ack_payload(
            {
                "ok": True,
                "detail": "velocity_accepted",
                "result_schema_version": 1,
                "status": "PHONE_ACCEPTED_WAITING_FOR_MOTION",
                "message_ko": "실제 기체 이동은 아직 확인되지 않았습니다.",
            }
        )
        self.assertEqual(result.schema_version, 1)
        self.assertEqual(result.status, "PHONE_ACCEPTED_WAITING_FOR_MOTION")
        self.assertIn("아직", result.message_ko)

    def test_motion_assessment_confirms_left_body_velocity(self) -> None:
        # At yaw 90 degrees, body-left is NED north.  A right setpoint of
        # -0.3 m/s therefore projects positively onto north velocity.
        result = assess_motion(
            Telemetry(
                velocity_north_mps=0.25,
                velocity_east_mps=0.0,
                velocity_down_mps=0.0,
                velocity_age_s=0.05,
                yaw_deg=90.0,
                attitude_age_s=0.05,
                active_command_sequence=42,
                active_command_age_s=1.0,
                setpoint_forward_mps=0.0,
                setpoint_right_mps=-0.3,
                setpoint_up_mps=0.0,
                sdk_submit_result="SUBMITTED_OFFICIAL_NO_CALLBACK",
            )
        )
        self.assertEqual(result.status, "MOTION_CONFIRMED")
        self.assertEqual(result.command_sequence, 42)
        self.assertGreater(result.evidence["projected_speed_mps"], 0.2)

    def test_motion_assessment_does_not_claim_fc_acceptance_without_motion(self) -> None:
        result = assess_motion(
            Telemetry(
                velocity_north_mps=0.0,
                velocity_east_mps=0.0,
                velocity_down_mps=0.0,
                velocity_age_s=0.05,
                yaw_deg=0.0,
                attitude_age_s=0.05,
                active_command_sequence=7,
                active_command_age_s=1.0,
                setpoint_forward_mps=0.0,
                setpoint_right_mps=-0.3,
                setpoint_up_mps=0.0,
                sdk_submit_result="SUBMITTED_OFFICIAL_NO_CALLBACK",
            )
        )
        self.assertEqual(result.status, "SDK_SUBMITTED_NO_MOTION")
        self.assertNotIn("FC", result.status)

    def test_payload_schema_and_timestamp_are_strict(self) -> None:
        protocol = Protocol()
        with self.assertRaises(ValueError):
            protocol.encode("heartbeat", {"extra": True})
        with self.assertRaises(ValueError):
            protocol.encode(
                "velocity",
                {
                    "forward_mps": math.nan,
                    "right_mps": 0.0,
                    "up_mps": 0.0,
                    "yaw_rate_rps": 0.0,
                },
            )
        sender, receiver = Protocol(), Protocol()
        first = sender.encode("heartbeat", {})
        receiver.decode(first)
        document = json.loads(sender.encode("heartbeat", {}))
        document["timestamp_ns"] = 0
        with self.assertRaises(ValueError):
            receiver.decode((json.dumps(document) + "\n").encode())


class FlightDiagnosticTests(unittest.TestCase):
    def test_all_four_body_directions_map_without_axis_swaps(self) -> None:
        self.assertEqual(_diagnostic_velocity("forward", 0.3, 0.1), Velocity(0.3, 0.0, 0.1))
        self.assertEqual(_diagnostic_velocity("back", 0.3, 0.1), Velocity(-0.3, 0.0, 0.1))
        self.assertEqual(_diagnostic_velocity("right", 0.3, 0.1), Velocity(0.0, 0.3, 0.1))
        self.assertEqual(_diagnostic_velocity("left", 0.3, 0.1), Velocity(0.0, -0.3, 0.1))

    def test_directional_speed_uses_body_frame_and_sign(self) -> None:
        telemetry = Telemetry(
            velocity_north_mps=0.0,
            velocity_east_mps=0.25,
            velocity_age_s=0.05,
            yaw_deg=0.0,
            attitude_age_s=0.05,
        )
        self.assertAlmostEqual(_directional_speed(telemetry, "right"), 0.25)
        self.assertAlmostEqual(_directional_speed(telemetry, "left"), -0.25)
        self.assertEqual(_opposite_direction("right"), "left")
        self.assertEqual(_opposite_direction("left"), "right")

    def test_nearest_obstacle_uses_only_fresh_positive_ranges(self) -> None:
        telemetry = Telemetry(
            oa_horizontal_distances_mm=(-1, 0, 1250, 800),
            oa_obstacle_data_age_s=0.05,
        )
        self.assertAlmostEqual(_nearest_obstacle_m(telemetry), 0.8)
        self.assertIsNone(
            _nearest_obstacle_m(
                Telemetry(
                    oa_horizontal_distances_mm=(800,),
                    oa_obstacle_data_age_s=1.1,
                )
            )
        )

    def test_duration_is_the_only_stop_rule_when_requested(self) -> None:
        values = dict(
            until_stopped=False,
            until_rc=False,
            goal_seen=False,
            duration_s=3.0,
            travelled_m=99.0,
            distance_m=0.1,
            timeout_s=1.0,
        )
        self.assertFalse(_diagnostic_leg_done(elapsed_s=2.99, **values))
        self.assertTrue(_diagnostic_leg_done(elapsed_s=3.0, **values))

    def test_until_rc_never_auto_completes_a_leg(self) -> None:
        self.assertFalse(
            _diagnostic_leg_done(
                until_stopped=False,
                until_rc=True,
                goal_seen=False,
                duration_s=None,
                elapsed_s=999.0,
                travelled_m=999.0,
                distance_m=0.1,
                timeout_s=1.0,
            )
        )


class DirectSdkApiTests(unittest.TestCase):
    def test_get_response_is_semantic_json_ready(self) -> None:
        result = parse_sdk_response(
            "GET",
            "FlightController",
            "FCFlightMode",
            "FlightController FCFlightMode VIRTUAL_STICK",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "SDK_GET_SUCCEEDED")
        self.assertEqual(result.value, "VIRTUAL_STICK")
        self.assertIn("VIRTUAL_STICK", result.message_ko)

    def test_dji_error_fields_are_preserved(self) -> None:
        result = parse_sdk_response(
            "ACTION",
            "FlightController",
            "StartTakeoff",
            "FlightController StartTakeoff ErrorImp{errorType='CORE', "
            "errorCode='SYSTEM_ERROR', innerCode='FLIGHTCONTROLLER.StartTakeoff:-7'}",
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "SDK_ACTION_FAILED")
        self.assertEqual(result.dji_error["errorCode"], "SYSTEM_ERROR")
        self.assertEqual(
            result.dji_error["innerCode"], "FLIGHTCONTROLLER.StartTakeoff:-7"
        )

    def test_mutating_sdk_calls_require_operator_token(self) -> None:
        with self.assertRaises(PermissionError):
            SdkApiClient._command(
                "ACTION", "FlightController", "StartTakeoff", None, None
            )
        command = SdkApiClient._command(
            "ACTION", "FlightController", "StartTakeoff", None, "secret"
        )
        self.assertEqual(
            command,
            "ACTION FlightController StartTakeoff TOKEN=secret",
        )


class EstimatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG)

    def _telemetry(self, north: float, east: float, yaw_deg: float) -> Telemetry:
        return Telemetry(
            velocity_north_mps=north,
            velocity_east_mps=east,
            velocity_age_s=0.05,
            yaw_deg=yaw_deg,
            attitude_age_s=0.05,
        )

    def test_ned_velocity_maps_into_world(self) -> None:
        # World and NED yaw share handedness (both clockwise from above in
        # the z-down tag world): offset = yaw_ned - yaw_world = 0.7 rad.
        estimator = PoseEstimator(self.config.dead_reckoning)
        yaw_deg = math.degrees(0.7)
        estimator.update_fix(1.0, 0.7, 0.0, self._telemetry(0.0, 0.0, yaw_deg))
        # NED velocity along the aircraft nose must integrate as world +x.
        north = 0.5 * math.cos(0.7)
        east = 0.5 * math.sin(0.7)
        ok = estimator.update_blind(
            1.0, self._telemetry(north, east, yaw_deg), None
        )
        self.assertTrue(ok)
        state = estimator.state
        self.assertAlmostEqual(state.x_m, 1.5, places=6)
        self.assertAlmostEqual(state.y_m, 0.7, places=6)
        self.assertAlmostEqual(state.yaw_rad, 0.0, places=6)

    def test_lateral_ned_velocity_is_not_mirrored(self) -> None:
        # A starboard velocity (theta_ned = offset + pi/2) must integrate as
        # world +y; the old opposite-handed formula mirrored this to -y.
        estimator = PoseEstimator(self.config.dead_reckoning)
        yaw_deg = math.degrees(0.7)
        estimator.update_fix(1.0, 0.7, 0.0, self._telemetry(0.0, 0.0, yaw_deg))
        theta_ned = 0.7 + math.pi / 2
        north = 0.5 * math.cos(theta_ned)
        east = 0.5 * math.sin(theta_ned)
        ok = estimator.update_blind(
            1.0, self._telemetry(north, east, yaw_deg), None
        )
        self.assertTrue(ok)
        state = estimator.state
        self.assertAlmostEqual(state.x_m, 1.0, places=6)
        self.assertAlmostEqual(state.y_m, 1.2, places=6)

    def test_commanded_velocity_fallback_without_telemetry(self) -> None:
        estimator = PoseEstimator(self.config.dead_reckoning)
        estimator.update_fix(1.0, 0.7, math.pi / 2, None)
        ok = estimator.update_blind(2.0, None, Velocity(0.25, 0.0))
        self.assertTrue(ok)
        state = estimator.state
        self.assertAlmostEqual(state.x_m, 1.0, places=6)
        self.assertAlmostEqual(state.y_m, 1.2, places=6)

    def test_blind_budget_exhaustion_stops_the_estimate(self) -> None:
        estimator = PoseEstimator(self.config.dead_reckoning)
        estimator.update_fix(1.0, 0.7, 0.0, None)
        budget = self.config.dead_reckoning.max_blind_s
        step = 0.1
        ok = True
        elapsed = 0.0
        while elapsed < budget + 1.0:
            ok = estimator.update_blind(step, None, Velocity(0.0, 0.0))
            elapsed += step
            if not ok:
                break
        self.assertFalse(ok)
        self.assertGreater(estimator.blind_time_s, budget)

    def test_no_fix_means_no_dead_reckoning(self) -> None:
        estimator = PoseEstimator(self.config.dead_reckoning)
        self.assertFalse(estimator.update_blind(0.1, None, Velocity(0.1, 0.0)))


class ShutdownTests(unittest.TestCase):
    def test_disarm_and_close_run_even_when_zero_fails(self) -> None:
        calls: list[str] = []

        class FailingClient:
            def zero(self) -> None:
                calls.append("zero")
                raise ConnectionError("boom")

            def disarm(self) -> None:
                calls.append("disarm")
                raise ValueError("stale ack")

            def close(self) -> None:
                calls.append("close")

        class Capture:
            def close(self) -> None:
                calls.append("capture")

        _shutdown(FailingClient(), Capture())
        self.assertEqual(calls, ["zero", "disarm", "close", "capture"])


class EndToEndTests(unittest.TestCase):
    def test_deterministic_goal_through_blind_stretch(self) -> None:
        config = load_config(CONFIG)
        one = run_simulation(config)
        two = run_simulation(config)
        self.assertEqual(one, two)
        self.assertTrue(one.reached_goal)
        self.assertEqual(one.final_state, State.HOVER)
        goal = config.goal_tag.world_pose
        self.assertIsNotNone(goal)
        assert goal is not None
        self.assertLessEqual(math.dist(one.final_xy, (goal.x_m, goal.y_m)), 0.081)
        self.assertLessEqual(
            one.maximum_command_mps, config.controller.max_speed_mps
        )
        self.assertIn(State.VISUAL_ALIGN, one.states_visited)
        # The honest camera footprint forces a real blind stretch that must
        # be crossed by dead reckoning, not by tag sightings.
        self.assertGreater(one.max_blind_time_s, 1.0)


if __name__ == "__main__":
    unittest.main()
