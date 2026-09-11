"""Counted 2->3->1->3->2 trial with operator-requested OA observation only.

Explicit --execute is required. Production config and prior working tree are
not rewritten. Ultrasonic 1.40m ascent uses the bounded test's controller;
route sequencing/detection/return reuse drone_nav.patrol without monkeypatching.
Physical leftward wall-tag order remains 2, 1, 3; each leg selects its direction.
Non-target ID1 is passed without a visit on both 2-to-3 and final 3-to-2 legs.
PC obstacle-distance stops are excluded; raw OA telemetry remains recorded.
Aircraft avoidance settings, height/authority/video/RC/battery bounds remain.
"""
import argparse
from dataclasses import replace
import json
import math
from pathlib import Path
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "pc"))
from drone_nav.config import load_config
from drone_nav.protocol import NDJSONClient, RateLimiter
from drone_nav.vision import TcpVideoStream, AprilTagDetector
from drone_nav.patrol import (
    PatrolRouteTracker, PatrolPhase, _DetectionLogger, _acquire_tag,
    _traverse_to_expected, _align_floor_id0, _align_cruise_yaw,
    _confirm_advanced_authority, _pause_zero, _wait_for_landing,
)
from drone_nav.runtime import _wait_for_takeoff, _arm_when_ready
from bounded_sonar_climb import CLIMB_TIMEOUT_S, climb_to_sonar_target

SUPPORTED_TAKEOFF_BATTERY = (15, 20)
DEFAULT_TAKEOFF_BATTERY = 15
RUNTIME_MIN_BATTERY = 15
TARGET_HEIGHT_M = 1.4
REQUIRED_BRIDGE_BUILD_ID = "5.18-telemetry-age.20260906.4"
REQUESTED_ROUTE = (2, 3, 1, 3, 2)
PHYSICAL_LEFTWARD_IDS = (2, 1, 3)
PHYSICAL_LAYOUT_CONFIRMATION = "operator_confirmed_unchanged_wall_3_1_2_floor0_under2_20260907"


def planned_direction(departure, expected):
    """Directions in the already tested aircraft frame, not observer left/right."""
    legs = {(None,2): "left", (2,3): "left", (3,1): "right",
            (1,3): "left", (3,2): "right"}
    try:
        return legs[(departure,expected)]
    except KeyError as error:
        raise ValueError(f"unauthorized route leg: {departure}->{expected}") from error


def motion_gate(t, frame_age, forward, right, up, yaw):
    if t is None or t.is_flying is not True:
        raise RuntimeError("airborne state not confirmed")
    if (t.armed is not True or t.vs_enabled is not True
            or t.vs_advanced_enabled is not True or t.vs_authority != "MSDK"):
        raise InterruptedError("Advanced authority lost; no rearm")
    if t.rc_override_age_s is not None and t.rc_override_age_s < 5:
        raise InterruptedError("physical RC override; no rearm")
    if not math.isfinite(frame_age) or not 0 <= frame_age <= .5:
        raise RuntimeError("camera unavailable for movement")
    if t.height_m is None or t.height_age_s is None or not math.isfinite(t.height_m) or not 0 <= t.height_age_s <= .5:
        raise RuntimeError("height unavailable/stale")
    if not .5 <= t.height_m <= 1.8:
        raise RuntimeError(f"height outside test envelope: {t.height_m}")
    if up > 0 and t.height_m >= 1.6:
        raise RuntimeError("wall centering requests climb above 1.6m display; stop for review")
    if up < 0 and t.height_m <= 1.0:
        raise RuntimeError("wall centering requests descent below 1.0m display")
    if t.battery_percent is None or not math.isfinite(t.battery_percent) or t.battery_percent < RUNTIME_MIN_BATTERY:
        raise RuntimeError(f"battery below{RUNTIME_MIN_BATTERY}% or unknown")
    if max(abs(forward), abs(right)) > 1.50001 or abs(up) > .18001 or abs(yaw) > math.radians(15):
        raise RuntimeError("command exceeds bounded patrol limits")
    # OA arrays, enabled flags and callback ages are observations, not motion
    # admission criteria in this operator-requested trial policy. The protocol
    # still records their raw values on every ACK, including unknown values.


class CheckedClient(NDJSONClient):
    def __init__(self, host):
        super().__init__(host, 9998, timeout_s=.8)
        self.capture = None
        self.deadline = None
        self.last_print = 0
        self.phase = "preflight"

    def attitude(self, forward_tilt_deg, right_tilt_deg, up_mps=0.0, yaw_rate_rps=0.0):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise TimeoutError("single patrol time budget expired; no repeat")
        self.status("full_patrol_before_motion")
        age = self.capture.read()[2] if self.capture is not None else float("inf")
        t = self.last_telemetry
        motion_gate(t, age, forward_tilt_deg, right_tilt_deg, up_mps, yaw_rate_rps)
        super().attitude(forward_tilt_deg, right_tilt_deg, up_mps, yaw_rate_rps)
        now = time.monotonic()
        if now-self.last_print >= 1:
            self.last_print = now
            motion = self.last_motion_assessment
            print(json.dumps({"phase":self.phase, "height_m":self.last_telemetry.height_m,
                "tilt_right":right_tilt_deg,"up_mps":up_mps,
                "motion":None if motion is None else motion.status}))

    def obstacle_avoidance_off(self):
        raise RuntimeError("sensor setting changes are excluded from this test")


def run(host, *, min_takeoff_battery=DEFAULT_TAKEOFF_BATTERY, trial_label=None, trial_number=1):
    if isinstance(trial_number, bool) or not isinstance(trial_number, int) or trial_number < 1:
        raise ValueError("trial_number must be a positive integer")
    if min_takeoff_battery not in SUPPORTED_TAKEOFF_BATTERY:
        raise ValueError(f"supported departure battery thresholds:{SUPPORTED_TAKEOFF_BATTERY}; runtime minimum={RUNTIME_MIN_BATTERY}")
    original = load_config(PROJECT_ROOT / "pc" / "config.local.json")
    # No hidden step-up above the 1.5deg already used in today's short test.
    config = replace(original, network=replace(original.network, host=host, rate_hz=10),
        patrol=replace(original.patrol, route_ids=(2,3,1), obstacle_stop_m=0.0,
            cruise_altitude_m=TARGET_HEIGHT_M, angle_deg=1.5,
            recovery_max_angle_deg=1.5, leg_timeout_s=45, align_cruise_yaw=False))
    # The shared traverse helper yields only strictly positive distances or
    # None. Its <= obstacle_stop_m check is inactive at 0.0. This is a local
    # trial override; the stored configuration and shared helper are unchanged.
    p = config.patrol
    if p.route_ids != (2,3,1) or p.outbound_direction != "left":
        raise RuntimeError("configured visits differ from authorized 2->3->1->3->2")
    token = config.network.confirmation_token
    route = PatrolRouteTracker(p.route_ids)
    client = CheckedClient(host)
    stream = logger = None
    try:
        client.connect()
        client.log_event("trial_metadata", {"trial_label": trial_label,
            "counted": True, "trial_number": trial_number,
            "series_id": "20260907_front100_rear90_three_23132_skip1_oa_observe",
            "requested_route": list(REQUESTED_ROUTE),
            "physical_leftward_tag_order": list(PHYSICAL_LEFTWARD_IDS),
            "non_target_tag_policy": "record_observations_without_visit_or_direction_change",
            "pass_through_legs": [{"from":2,"to":3,"ignored_id":1},{"from":3,"to":2,"ignored_id":1}],
            "environment": {"front_clearance_cm": 100, "rear_objects": "three_operator_reported", "rear_object_count": 3, "rear_clearance_cm": 90},
            "pc_obstacle_distance_policy": "observe_only",
            "pc_obstacle_stop_m": p.obstacle_stop_m,
            "aircraft_oa_settings_changed": False,
            "physical_layout_confirmation_source": PHYSICAL_LAYOUT_CONFIRMATION,
            "height_target_m": TARGET_HEIGHT_M,
            "climb_timeout_s": CLIMB_TIMEOUT_S,
            "min_takeoff_battery_percent": min_takeoff_battery,
            "runtime_min_battery_percent": RUNTIME_MIN_BATTERY})
        client.status("full_patrol_ground_check")
        t = client.last_telemetry
        if t is None or t.is_flying is not False or t.armed is not False:
            raise RuntimeError("disarmed ground state not confirmed; no automatic resume")
        if t.bridge_build_id != REQUIRED_BRIDGE_BUILD_ID or t.battery_percent is None or not math.isfinite(t.battery_percent) or t.battery_percent < min_takeoff_battery:
            raise RuntimeError(f"build mismatch or battery<{min_takeoff_battery}/unknown before takeoff")
        print(f"GROUND confirmed battery={t.battery_percent}% log={client.log_path}")
        detector = AprilTagDetector(config)
        stream = TcpVideoStream(host,9999,config.network.video_codec)
        client.capture = stream
        logger = _DetectionLogger(client, photo_root=(PROJECT_ROOT / "pc" / "captures")/client.session_id)
        logger.attach_capture(stream)
        client.gimbal_down()
        deadline = time.monotonic()+12
        while time.monotonic() < deadline:
            ok, _, age = stream.read()
            if ok and age <= .5:
                break
            time.sleep(.05)
        else:
            client.log_event("preflight_video_failed", stream.diagnostics())
            raise RuntimeError("no live video before takeoff")
        client.log_event("full_patrol_plan", {"route":[0,*p.route_ids,*reversed(p.route_ids[:-1]),0],
            "visit_pause_s":p.visit_pause_s,
            "height_source":"ultrasonic_display","height_target_m":TARGET_HEIGHT_M,
            "climb_timeout_s":CLIMB_TIMEOUT_S,
            "leg_directions":[{"from":a,"to":b,"direction":planned_direction(a,b)}
                for a,b in zip((None,*REQUESTED_ROUTE[:-1]),REQUESTED_ROUTE)],
            "max_tilt_deg":1.5,
            "heading_policy":"preserve_departure_no_rotation",
            "oa_changes":False,"auto_land_only_at_id0":True,
            "pc_obstacle_distance_policy":"observe_only"})
        client.stick_mode("advanced_angle")
        limiter = RateLimiter(10)
        print(f"TAKEOFF NOW: counted trial {trial_number}; ID0 -> sonar{TARGET_HEIGHT_M:.2f} -> ID2 -> ID3(pass ID1) -> ID1 -> ID3 -> ID2(pass ID1) -> floorID0 landing; pause3s; PC OA distances observation only")
        client.takeoff(token)
        _wait_for_takeoff(client,limiter,12)
        _arm_when_ready(client,token,limiter,timeout_s=8)
        _confirm_advanced_authority(client,limiter)
        client.deadline = time.monotonic()+180
        client.phase = "floor_home"
        floor = _acquire_tag(client,limiter,stream,detector,logger,route,p)
        floor_center = floor.center_px
        client.phase = f"climb_{TARGET_HEIGHT_M:.2f}"
        climb_to_sonar_target(client,limiter,stream,detector,logger.recorder,config,TARGET_HEIGHT_M)
        client.gimbal(0)
        _pause_zero(client,limiter,1)
        client.log_event("cruise_yaw_preserved", {
            "actual_yaw_deg":client.last_telemetry.yaw_deg,
            "historical_target_ignored_deg":p.cruise_yaw_deg})
        print("Departure heading preserved: no automatic -95deg rotation")
        previous = None
        while route.phase in (PatrolPhase.WALL_HOME, PatrolPhase.OUTBOUND):
            expected = route.expected_id
            client.phase = f"outbound_ID{expected}"
            client.log_event("requested_route_leg", {"from":previous,"to":expected,
                "direction":planned_direction(previous,expected),
                "ignored_tag_ids":[1] if (previous,expected) in ((2,3),(3,2)) else []})
            _traverse_to_expected(client,limiter,stream,detector,logger,route,p,
                direction=planned_direction(previous,expected),target_x=config.camera.cx,target_y=p.wall_target_y_px,
                departure_tag_id=previous)
            previous = expected
        client.zero()
        print(f"ID{p.route_ids[-1]} CONFIRMED: next leg {planned_direction(p.route_ids[-1],p.route_ids[-2])} to ID{p.route_ids[-2]}")
        client.log_event("patrol_turnaround", {"at_tag_id":p.route_ids[-1]})
        _pause_zero(client,limiter,p.turnaround_pause_s)
        route.begin_return()
        while route.phase is PatrolPhase.RETURN:
            expected = route.expected_id
            client.phase = f"return_ID{expected}"
            client.log_event("requested_route_leg", {"from":previous,"to":expected,
                "direction":planned_direction(previous,expected),
                "ignored_tag_ids":[1] if (previous,expected) in ((2,3),(3,2)) else []})
            _traverse_to_expected(client,limiter,stream,detector,logger,route,p,
                direction=planned_direction(previous,expected),target_x=config.camera.cx,target_y=p.wall_target_y_px,
                departure_tag_id=previous)
            previous = expected
        client.phase = "landing_align_ID0"
        client.gimbal_down()
        _pause_zero(client,limiter,1)
        _align_floor_id0(config,client,limiter,stream,detector,logger,route,floor_center)
        client.zero()
        print("ID0 ALIGNED: commanding DJI landing")
        client.log_event("landing_commanded", {"tag_id":0})
        client.land(token)
        _wait_for_landing(client,limiter)
        client.log_event("full_patrol_complete", {"landed":True})
        print("FULL PATROL COMPLETE: is_flying=false")
    except BaseException as error:
        client.log_event("full_patrol_aborted", {"phase":client.phase,"reason":repr(error)})
        print(f"PATROL STOPPED phase={client.phase}: {type(error).__name__}: {error}")
        raise
    finally:
        for action in ("zero","disarm","close"):
            try:
                getattr(client,action)()
            except Exception as error:
                print(f"cleanup {action}: {type(error).__name__}: {error}")
        if stream is not None:
            stream.close()
        if logger is not None:
            logger.close()
        print("No automatic repeat/rearm. RC pilot: land if the test stopped airborne.")


def positive_trial_number(value):
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("trial number must be a positive integer") from error
    if number < 1:
        raise argparse.ArgumentTypeError("trial number must be a positive integer")
    return number


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host",required=True)
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--min-takeoff-battery", type=int,
                        choices=SUPPORTED_TAKEOFF_BATTERY, default=DEFAULT_TAKEOFF_BATTERY)
    parser.add_argument("--trial-label")
    parser.add_argument("--trial-number", type=positive_trial_number, default=1)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    if args.execute:
        run(args.host, min_takeoff_battery=args.min_takeoff_battery,
            trial_label=args.trial_label, trial_number=args.trial_number)
    else:
        print(f"PLAN ONLY: counted trial {args.trial_number}; ID0 -> sonar{TARGET_HEIGHT_M:.2f} -> ID2 -> left ID3(pass ID1) -> right ID1 -> left ID3 -> right ID2(pass ID1) -> ID0 landing; pause3s; Advanced ANGLE<=1.5deg; no aircraft sensor changes; PC OA distances observation only")
