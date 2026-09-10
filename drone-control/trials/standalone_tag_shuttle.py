"""Standalone camera-relative ID6 -> 3 -> 2 -> 1 -> 2 -> 3 -> 6 shuttle.

No Speech, dashboard, HTTP tool service, GPS route, or surveyed wall poses are
used. The default CLI is an offline plan: --execute and a private --config are
both required to open a connection. Successful return releases control to the
RC pilot for manual landing; it never sends a landing command.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import ipaddress
import json
import math
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from drone_nav.config import TagConfig, load_config
from drone_nav.patrol import (
    ExpectedTagTracker, PatrolPhase, _DetectionLogger, _acquire_tag,
    _visual_floor_height_m,
)
from drone_nav.protocol import RateLimiter
from drone_nav.tool_control.live import (
    BUILD_ID, FreshVideoStream, MissionClient, VisitGate,
    fresh, ground_verified, process_identity,
)
from drone_nav.vision import AprilTagDetector
from bounded_sonar_climb import CLIMB_TIMEOUT_S, climb_command

DEFAULT_PROFILE = Path(__file__).with_name("profiles") / "standalone_tag_6321236.json"
WALL_IDS = [1, 2, 3, 6]
ROUTE_IDS = [6, 3, 2, 1, 2, 3, 6]
CONFIRM_S = .3
CENTER_TOLERANCE_PX = 80.
FRESH_S = .5
PROFILE_FIELDS = {
    "schema_version", "wall_ids_left_to_right", "floor_tag_id", "home_tag_id",
    "route_ids", "target_height_m", "tag_size_m", "max_tilt_deg", "visit_pause_s",
    "leg_timeout_s", "total_timeout_s", "layout_confirmed", "tag_size_confirmed",
}


def _number(value, low, high):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and low <= value <= high)


def validate_profile(profile):
    if not isinstance(profile, dict) or set(profile) != PROFILE_FIELDS:
        raise ValueError("Standalone shuttle profile fields do not match schema 1")
    for key, expected in (("schema_version", 1), ("floor_tag_id", 0), ("home_tag_id", 6)):
        if type(profile[key]) is not int or profile[key] != expected:
            raise ValueError(f"{key} must be {expected}")
    for key, expected in (("wall_ids_left_to_right", WALL_IDS), ("route_ids", ROUTE_IDS)):
        if (profile[key] != expected or not isinstance(profile[key], list)
                or any(type(item) is not int for item in profile[key])):
            raise ValueError(f"{key} must be {expected}")
    for key in ("layout_confirmed", "tag_size_confirmed"):
        if type(profile[key]) is not bool:
            raise ValueError(f"{key} must be a boolean")
    for key, low, high in (
        ("target_height_m", 1.4, 1.4), ("tag_size_m", .02, 1.),
        ("max_tilt_deg", .1, 1.5), ("visit_pause_s", 0., 10.),
        ("leg_timeout_s", 1., 45.), ("total_timeout_s", 10., 240.),
    ):
        if not _number(profile[key], low, high):
            raise ValueError(f"{key} must be finite and within [{low}, {high}]")
    return profile


def load_profile(path):
    return validate_profile(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def planned_direction(departure, expected):
    if (departure, expected) not in tuple(zip(ROUTE_IDS, ROUTE_IDS[1:])):
        raise ValueError(f"Unapproved shuttle leg: {departure} -> {expected}")
    return "left" if WALL_IDS.index(expected) < WALL_IDS.index(departure) else "right"


def plan(profile):
    validate_profile(profile)
    return {
        "mode": "offline_plan", "profile": profile,
        "legs": [{"from": a, "to": b, "direction": planned_direction(a, b)}
                 for a, b in zip(ROUTE_IDS, ROUTE_IDS[1:])],
        "required_bridge_build_id": BUILD_ID,
        "height_source": "downward_ultrasonic_display_with_floor_ID0_visual_crosscheck",
        "climb_timeout_s": CLIMB_TIMEOUT_S,
        "wall_center_tolerance_px": CENTER_TOLERANCE_PX,
        "unique_frame_hold_s": CONFIRM_S,
        "lateral_phase_axes": ["right_tilt_deg"],
        "finish": "hover_release_to_RC_manual_landing",
        "pc_obstacle_distance_policy": "observe_only",
        "aircraft_avoidance_changes": False,
        "navigation": "camera_relative_no_surveyed_wall_pose",
        "frame_age_basis": "PC_decode_time_not_aircraft_exposure",
        "network_connections_opened": 0,
    }


def prepare_config(path, profile, host=None):
    return configure_execution(load_config(path), profile, host)


def configure_execution(config, profile, host=None):
    """Validate both CLI and direct Python entry points without changing files."""
    validate_profile(profile)
    if not profile["layout_confirmed"] or not profile["tag_size_confirmed"]:
        raise ValueError("Confirmed layout and measured printed tag size are required for execution")
    if config.patrol is None or not config.camera.calibrated:
        raise ValueError("Existing patrol settings and calibrated camera intrinsics are required")
    address = host or config.network.host
    ipaddress.ip_address(address)
    if (not config.network.confirmation_token
            or config.network.confirmation_token.startswith("REPLACE_")):
        raise ValueError("Private phone confirmation token is required")
    tags = {tag.id: tag for tag in config.tags}
    for tag_id in (0, 1, 2, 3, 6):
        # ID6 is a new visual landmark, not a claimed surveyed coordinate.
        prior = tags.get(tag_id)
        tags[tag_id] = TagConfig(tag_id, profile["tag_size_m"],
                                None if tag_id == 6 or prior is None else prior.world_pose)
    return replace(config, tags=tuple(tags.values()),
        network=replace(config.network, host=address, rate_hz=10.),
        patrol=replace(config.patrol, route_ids=(6, 3, 2, 1), outbound_direction="left", obstacle_stop_m=0.,
            cruise_altitude_m=profile["target_height_m"],
            angle_deg=profile["max_tilt_deg"], recovery_max_angle_deg=profile["max_tilt_deg"],
            leg_timeout_s=profile["leg_timeout_s"], acquire_timeout_s=12.,
            tag_confirm_s=CONFIRM_S, wall_center_tolerance_px=CENTER_TOLERANCE_PX,
            visit_pause_s=profile["visit_pause_s"], align_cruise_yaw=False))


class ShuttleClient(MissionClient):
    """Keep the validated transport and enforce this trial's axes at dispatch."""
    def __init__(self, config, cancel, on_snapshot):
        super().__init__(config, cancel, on_snapshot)
        self.phase = "preflight"
        self.video_generation = None
        self.max_tilt = config.patrol.angle_deg

    def observe_frame(self, snapshot):
        if snapshot is None or not _number(time.monotonic() - snapshot.received_s, 0., FRESH_S):
            raise InterruptedError("Detected camera frame expired before command dispatch")
        if self.video_generation is None:
            self.video_generation = snapshot.key[0]
        elif snapshot.key[0] != self.video_generation:
            raise InterruptedError("Video generation changed during shuttle; no automatic resume")

    def _guard_dispatch(self, kind, payload):
        if kind != "attitude":
            return
        self.observe_frame(None if self.stream is None else self.stream.last_detection_snapshot)
        forward, right, up, yaw = (payload.get(key) for key in
            ("forward_tilt_deg", "right_tilt_deg", "up_mps", "yaw_rate_rps"))
        if self.phase == "climb":
            permitted = forward == right == yaw == 0. and _number(up, 0., .18)
        elif self.phase == "lateral":
            permitted = forward == up == yaw == 0. and _number(right, -self.max_tilt, self.max_tilt)
        else:
            permitted = False
        if not permitted:
            raise PermissionError("Motion axes are not permitted in the current shuttle phase")

    def send(self, kind, payload, timeout_s=None):
        # MissionClient.attitude() polls status before reaching this method:
        # admission here therefore rechecks the exact detection after that RTT.
        self._guard_dispatch(kind, payload)
        return super().send(kind, payload, timeout_s)

    def _log_event(self, event, data):
        super()._log_event(event, data)
        if event == "pc_request":
            # NDJSONClient logs just before sendall. Recheck after a slow log
            # write as well; an unsent refusal preserves the cleanup sequence.
            self._guard_dispatch(data.get("type"), data.get("payload", {}))


class HorizontalGate:
    """Only the current expected tag can end a horizontal leg.

    Duplicate frames never produce movement or confirmation. Once the target
    has been seen, losing it requests zero until another fresh observation.
    """
    def __init__(self, expected, direction, target_x, max_tilt):
        if direction not in ("left", "right"):
            raise ValueError("A lateral direction is required")
        self.expected, self.direction = expected, direction
        self.target_x, self.max_tilt = target_x, max_tilt
        self.tracker = ExpectedTagTracker(expected, CONFIRM_S, target_x, CENTER_TOLERANCE_PX)
        self.last_key = None
        self.target_seen = False

    def update(self, detections, now_s, frame_age, frame_key):
        if not _number(frame_age, 0., FRESH_S) or frame_key is None:
            raise RuntimeError("Fresh identified camera frame required; lateral motion cancelled")
        if self.last_key is not None:
            if frame_key[0] != self.last_key[0]:
                raise InterruptedError("Video stream generation changed; no automatic flight resume")
            if frame_key[1] <= self.last_key[1]:
                return 0., None
        self.last_key = frame_key
        choices = [d for d in detections if d.tag_id == self.expected
                   and d.center_px is not None and len(d.center_px) == 2
                   and all(_number(v, -1e6, 1e6) for v in d.center_px)
                   and _number(d.pose_error, -1e12, 1e12)]
        observed = min(choices, key=lambda d: abs(d.pose_error)) if choices else None
        confirmed = self.tracker.update(choices, now_s, frame_key=frame_key)
        if confirmed is not None:
            return 0., confirmed
        if observed is None:
            return (0. if self.target_seen else (-self.max_tilt if self.direction == "left" else self.max_tilt)), None
        self.target_seen = True
        error = observed.center_px[0] - self.target_x
        if abs(error) <= CENTER_TOLERANCE_PX:
            return 0., None
        tilt = min(self.max_tilt, max(.2, abs(error) * .005))
        return math.copysign(tilt, error), None


def _snapshot_key(stream):
    snapshot = stream.last_detection_snapshot
    return None if snapshot is None else snapshot.key


def _observe(client, stream, detector, logger, phase, expected, direction=None):
    tags, age = stream.detect_latest(detector, FRESH_S)
    if _number(age, 0., FRESH_S) and stream.last_detection_snapshot is not None:
        observer = getattr(client, "observe_frame", None)
        if observer is not None:
            observer(stream.last_detection_snapshot)
    logger.observations(tags, time.monotonic(), phase=phase, expected_id=expected,
                        frame_age_s=age, telemetry=client.last_telemetry, direction=direction)
    return tags, age


def _require_flight(client):
    raw = client.raw
    elapsed = time.perf_counter() - client.received
    if (raw.get("is_flying") is not True or not _number(elapsed, 0., FRESH_S)
            or not fresh(raw, "is_flying", max(0., (FRESH_S - elapsed) * 1000))):
        raise InterruptedError("Fresh airborne state lost; no resume")
    if (raw.get("armed") is not True or raw.get("vs_enabled") is not True
            or raw.get("vs_advanced_enabled") is not True or raw.get("vs_authority") != "MSDK"):
        raise InterruptedError("RC/Virtual Stick authority changed; no re-arm")


def _wait_ground_video(client, limiter, stream, detector, logger):
    # At ground height the downward camera can crop the printed floor tag.
    # The tested takeoff sequence requires fresh video here, then ID0 after
    # takeoff before ascent/lateral motion. All visible IDs are still logged.
    deadline, previous_key, held = time.monotonic() + 12., None, None
    while time.monotonic() < deadline:
        limiter.wait()
        client.status("standalone_ground_video")
        if not ground_verified(client.raw):
            raise InterruptedError("Ground/motor/RC proof changed before takeoff")
        _, age = _observe(client, stream, detector, logger, PatrolPhase.FLOOR_HOME, 0)
        key = _snapshot_key(stream)
        if not _number(age, 0., FRESH_S) or key is None:
            held, previous_key = None, None
            continue
        if (previous_key is not None and key[0] == previous_key[0]
                and key[1] <= previous_key[1]):
            if key[1] < previous_key[1]:
                held = None
            continue
        if previous_key is not None and key[0] != previous_key[0]:
            held = None
        previous_key = key
        now = time.monotonic()
        held = now if held is None else held
        if now - held >= CONFIRM_S:
            return
    raise RuntimeError("Fresh video not confirmed before takeoff")


def _climb(client, limiter, stream, detector, logger, config, target):
    client.phase = "climb"
    deadline, held, previous_key = time.monotonic() + CLIMB_TIMEOUT_S, None, None
    while time.monotonic() < deadline:
        limiter.wait()
        client.status("standalone_bounded_sonar_climb")
        _require_flight(client)
        tags, age = _observe(client, stream, detector, logger, PatrolPhase.FLOOR_HOME, 0)
        floor = next((tag for tag in tags if tag.tag_id == 0), None)
        if not _number(age, 0., FRESH_S) or floor is None:
            raise RuntimeError("Fresh floor ID0 unavailable during climb")
        visual_height = _visual_floor_height_m(config, floor)
        if not _number(visual_height, 0., min(2.1, config.room.height_m - .3)):
            raise RuntimeError("Floor ID0 visual height cross-check exceeds configured ceiling")
        t = client.last_telemetry
        elapsed = time.perf_counter() - client.received
        up = climb_command(t.height_m, None if t.height_age_s is None else t.height_age_s + elapsed, target)
        now = time.monotonic()
        if now >= deadline:
            break
        row = {"target_height_m": target, "height_m": t.height_m,
               "floor_visual_height_m": visual_height, "up_mps": up,
               "source": "downward_ultrasonic_display"}
        client.log_event("standalone_climb_sample", row)
        if up == 0.:
            client.zero()
            stable = (_number(t.velocity_down_mps, -.05, .05)
                      and t.velocity_age_s is not None
                      and _number(t.velocity_age_s + elapsed, 0., FRESH_S))
            key = _snapshot_key(stream)
            if not stable or key is None:
                held, previous_key = None, None
                continue
            if previous_key is not None and key[0] != previous_key[0]:
                raise InterruptedError("Video generation changed during climb")
            if previous_key is None or key[1] > previous_key[1]:
                held = now if held is None else held
                if now - held >= .5:
                    client.log_event("standalone_target_height_confirmed", row)
                    return
                previous_key = key
        else:
            held, previous_key = None, None
            client.attitude(0., 0., up, 0.)
    raise RuntimeError("1.4m downward height target not confirmed within 8s; no lateral command")


def _pause(client, limiter, stream, detector, logger, seconds, phase, expected):
    client.phase = "hover"
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        limiter.wait()
        client.zero()
        _require_flight(client)
        _, age = _observe(client, stream, detector, logger, phase, expected)
        if not _number(age, 0., FRESH_S):
            raise RuntimeError("Camera became stale while hovering")


def traverse_horizontal(client, limiter, stream, detector, logger, config, profile,
                        departure, expected, phase):
    client.phase = "lateral"
    direction = planned_direction(departure, expected)
    gate = HorizontalGate(expected, direction, config.camera.cx, profile["max_tilt_deg"])
    deadline = time.monotonic() + profile["leg_timeout_s"]
    client.log_event("standalone_leg", {"from": departure, "to": expected, "direction": direction})
    print(f"ID{departure} -> ID{expected}: {direction}", flush=True)
    while time.monotonic() < deadline:
        limiter.wait()
        client.status("standalone_horizontal_leg")
        _require_flight(client)
        tags, age = _observe(client, stream, detector, logger, phase, expected, direction)
        now = time.monotonic()
        if now >= deadline:
            break
        right, confirmed = gate.update(tags, now, age, _snapshot_key(stream))
        client.log_event("standalone_horizontal_sample", {
            "expected_id": expected, "visible_ids": [tag.tag_id for tag in tags],
            "frame_age_s": age, "frame_key": _snapshot_key(stream),
            "target_seen": gate.target_seen, "right_tilt_deg": right,
            "forward_tilt_deg": 0., "up_mps": 0., "yaw_rate_rps": 0.,
        })
        # The sole motion-producing call in the wall traversal has one axis.
        if right:
            client.attitude(0., right, 0., 0.)
        else:
            client.zero()
        if confirmed is not None:
            _require_flight(client)
            client.log_event("tag_visit_confirmed", logger.payload(confirmed,
                phase=phase, expected_id=expected, frame_age_s=age,
                telemetry=client.last_telemetry, direction=direction))
            logger.save_confirmation_photo(stream, confirmed, phase=phase)
            return confirmed
    raise TimeoutError(f"ID{expected} not confirmed within {profile['leg_timeout_s']:g}s")


def release_to_rc(client):
    """Cleanup reports observed RC/ground state, never assumes a landing."""
    client.cleaning = True
    errors = []
    for action in ("zero", "disarm"):
        try:
            getattr(client, action)()
        except Exception as exc:
            errors.append(f"{action}:{type(exc).__name__}")
    try:
        client.status("standalone_release_verification")
        raw, t = client.raw, client.last_telemetry
        elapsed = time.perf_counter() - client.received
        recent = _number(elapsed, 0., FRESH_S)
        aged = dict(raw)
        for key in ("is_flying_age_ms", "are_motors_on_age_ms"):
            if _number(aged.get(key), 0., 1e12):
                aged[key] += elapsed * 1000
        rc = (recent and raw.get("vs_authority") == "RC" and raw.get("armed") is False
              and raw.get("vs_enabled") is False and fresh(aged, "is_flying"))
        speeds = (t.velocity_north_mps, t.velocity_east_mps, t.velocity_down_mps) if t else ()
        stopped = (bool(speeds) and t.velocity_age_s is not None
                   and _number(t.velocity_age_s + elapsed, 0., FRESH_S)
                   and all(_number(v, -.08, .08) for v in speeds))
        ground = recent and ground_verified(aged)
        return {"control_released_to_rc": rc, "ground_verified": ground,
                "physical_stop_confirmed": rc and (ground or stopped), "cleanup_errors": errors}
    except Exception as exc:
        return {"control_released_to_rc": False, "ground_verified": False,
                "physical_stop_confirmed": False, "cleanup_errors": errors + [type(exc).__name__]}


def run(config, profile, cancel=None):
    config = configure_execution(config, profile)
    cancel = threading.Event() if cancel is None else cancel
    client = ShuttleClient(config, cancel, lambda snapshot: None)
    stream = logger = None
    visited, completed, error, diagnostic_errors = [], False, None, []
    release = {"control_released_to_rc": False, "ground_verified": False,
               "physical_stop_confirmed": False, "cleanup_errors": []}
    try:
        client.connect()
        client.deadline = time.perf_counter() + profile["total_timeout_s"]
        client.status("standalone_preflight")
        if not ground_verified(client.raw) or client.raw.get("bridge_build_id") != BUILD_ID:
            raise RuntimeError("Fresh motors-off grounded RC state and bridge .5 required")
        if not process_identity(client.raw) or client.raw.get("telemetry_generation") is None:
            raise RuntimeError("App process/generation identity required")
        if not _number(client.last_telemetry.battery_percent, 30., 100.):
            raise RuntimeError("At least 30% battery required")
        execution_plan = plan(profile)
        execution_plan.pop("network_connections_opened")
        client.log_event("standalone_plan", {**execution_plan, "mode": "execute",
            "camera_calibrated": config.camera.calibrated,
            "body_camera_calibrated": config.body_camera.calibrated,
            "actual_measurements_confirmed": config.actual_measurements_confirmed,
            "world_pose_used": False})
        print(f"GROUND confirmed; log={client.log_path}", flush=True)
        detector = AprilTagDetector(config)
        stream = FreshVideoStream(config.network.host, config.network.video_port, config.network.video_codec)
        client.stream = stream
        logger = _DetectionLogger(client, photo_root=ROOT / "pc" / "captures" / client.session_id)
        logger.attach_capture(stream)
        limiter = RateLimiter(10.)
        client.gimbal_down()
        _wait_ground_video(client, limiter, stream, detector, logger)
        client.stick_mode("advanced_angle")
        client.status("standalone_immediate_takeoff_ground_proof")
        if not ground_verified(client.raw):
            raise RuntimeError("Ground/motor state changed before takeoff")
        rc_age = client.last_telemetry.rc_override_age_s
        if rc_age is not None and 0 <= rc_age < 5:
            raise InterruptedError("Recent RC stick input; no takeoff")
        print("TAKEOFF once; floor0 -> height1.4m -> wall6", flush=True)
        client.takeoff(config.network.confirmation_token)
        deadline = time.monotonic() + 12.
        while time.monotonic() < deadline:
            limiter.wait()
            client.status("standalone_takeoff_confirmation")
            if (client.raw.get("is_flying") is True and fresh(client.raw, "is_flying")
                    and _number(client.last_telemetry.height_m, .5, 1.8)):
                break
        else:
            raise RuntimeError("Takeoff unconfirmed; never retried")
        client.arm(config.network.confirmation_token)
        floor = VisitGate(0, PatrolPhase.FLOOR_HOME)
        _acquire_tag(client, limiter, stream, detector, logger, floor, config.patrol)
        _climb(client, limiter, stream, detector, logger, config, profile["target_height_m"])
        client.gimbal(0.)
        _pause(client, limiter, stream, detector, logger, 1., PatrolPhase.WALL_HOME, 6)
        home = VisitGate(6, PatrolPhase.WALL_HOME)
        _acquire_tag(client, limiter, stream, detector, logger, home, config.patrol,
                     target_x=config.camera.cx, tolerance_px=CENTER_TOLERANCE_PX)
        _require_flight(client)
        visited.append(6)
        _pause(client, limiter, stream, detector, logger, profile["visit_pause_s"], PatrolPhase.WALL_HOME, 6)
        for index, (departure, expected) in enumerate(zip(ROUTE_IDS, ROUTE_IDS[1:])):
            phase = PatrolPhase.OUTBOUND if index < 3 else PatrolPhase.RETURN
            traverse_horizontal(client, limiter, stream, detector, logger, config, profile,
                                departure, expected, phase)
            visited.append(expected)
            _pause(client, limiter, stream, detector, logger, profile["visit_pause_s"], phase, expected)
        client.zero()
        completed = True
    except (Exception, KeyboardInterrupt) as exc:
        message = str(exc)
        if config.network.confirmation_token:
            message = message.replace(config.network.confirmation_token, "<redacted>")
        error = f"{type(exc).__name__}: {message}"
        try:
            client.log_event("standalone_interrupted", {"error": error, "visited_ids": visited})
        except Exception as log_error:
            diagnostic_errors.append(f"interruption_log:{type(log_error).__name__}")
    finally:
        if client._socket is not None and client.attempted_action:
            release = release_to_rc(client)
        elif client.raw and _number(time.perf_counter() - client.received, 0., .1):
            ground = ground_verified(client.raw)
            release.update(ground_verified=ground, physical_stop_confirmed=ground,
                           control_released_to_rc=ground)
        result = {**release, "route_completed": completed, "visited_ids": visited,
                  "error": error, "log_path": str(client.log_path) if client.log_path else None,
                  "no_flight_action_dispatched": not client.attempted_action,
                  "manual_landing_required": client.attempted_action and not release["ground_verified"]}
        result["cleanup_errors"].extend(diagnostic_errors)
        if completed and release["control_released_to_rc"]:
            result["state"] = "completed" if release["ground_verified"] else "awaiting_rc_landing"
        elif release["physical_stop_confirmed"]:
            result["state"] = "stopped"
        else:
            result["state"] = "outcome_unknown"
        try:
            client.log_event("standalone_result", result)
        except Exception as log_error:
            result["cleanup_errors"].append(f"result_log:{type(log_error).__name__}")
        finally:
            for resource in (logger, stream, client):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception as exc:
                        result["cleanup_errors"].append(f"close:{type(exc).__name__}")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--config", type=Path, help="Private navigation JSON; required with --execute")
    parser.add_argument("--host", help="Current phone IPv4/IPv6 address override")
    parser.add_argument("--execute", action="store_true", help="Connect and execute one supervised shuttle")
    args = parser.parse_args(argv)
    try:
        profile = load_profile(args.profile)
        if not args.execute:
            print(json.dumps(plan(profile), ensure_ascii=False, indent=2))
            return 0
        if args.config is None:
            parser.error("--config is required with --execute")
        config = prepare_config(args.config, profile, args.host)
        result = run(config, profile)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["manual_landing_required"]:
            print("RC pilot: land manually. No automatic repeat, reconnect, re-arm, or landing.", flush=True)
        return 0 if result["route_completed"] and result["control_released_to_rc"] else 1
    except (ValueError, OSError) as exc:
        print(f"Setup error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    for output in (sys.stdout, sys.stderr):
        if hasattr(output, "reconfigure"):
            output.reconfigure(encoding="utf-8", line_buffering=True)
    raise SystemExit(main())
