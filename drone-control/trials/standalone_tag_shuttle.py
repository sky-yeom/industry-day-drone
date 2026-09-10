"""Standalone camera-relative ID6 -> 1 -> 2 -> 3 -> 2 -> 1 -> 6 shuttle.

No Speech, dashboard, HTTP tool service, GPS route, or surveyed wall poses are
used. The default CLI is an offline plan: --execute and a private --config are
both required to open a connection. Successful return releases control to the
RC pilot for manual landing; it never sends a landing command.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
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

from drone_nav.config import load_config
from drone_nav.patrol import (
    PatrolPhase, _DetectionLogger, _acquire_tag,
    _visual_floor_height_m,
)
from drone_nav.protocol import RateLimiter
from drone_nav.observation import write_image
from drone_nav.tool_control.live import (
    BUILD_ID, FreshVideoStream, MissionClient, VisitGate,
    fresh, ground_verified, process_identity,
)
from drone_nav.vision import AprilTagDetector, VisionDependencyError
from drone_nav.wall_framing import WallViewAction, wall_view_action, wall_view_bounds
from bounded_sonar_climb import CLIMB_TIMEOUT_S, climb_command

DEFAULT_PROFILE = Path(__file__).with_name("profiles") / "standalone_tag_6321236.json"
DEFAULT_PAIR_REFERENCE = Path(__file__).with_name("profiles") / "id1_tv_pair_reference.json"
WALL_IDS = [3, 2, 1, 6]
ROUTE_IDS = [6, 1, 2, 3, 2, 1, 6]
CONFIRM_S = .3
CENTER_TOLERANCE_PX = 80.
FRESH_S = .5
# The phone polls IsFlying about every 0.54 s, so a 0.5 s freshness gate on it
# trips at random (15:23 flight: age 517 ms while armed/MSDK/height were all
# fine). Three missed polls is the stale threshold for the flight flag only.
FLIGHT_STATE_FRESH_S = 1.5
TAKEOFF_SETTLE_TIMEOUT_S = 20.
TAKEOFF_STABLE_HOLD_S = 2.
PROFILE_FIELDS = {
    "schema_version", "wall_ids_left_to_right", "floor_tag_id", "home_tag_id",
    "route_ids", "target_height_m", "max_tilt_deg", "visit_pause_s",
    "leg_timeout_s", "total_timeout_s", "layout_confirmed", "wall_measurement",
    "floor_size_source",
}


def _number(value, low, high):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and low <= value <= high)


def validate_profile(profile):
    if not isinstance(profile, dict) or set(profile) != PROFILE_FIELDS:
        raise ValueError("Standalone shuttle profile fields do not match schema 2")
    for key, expected in (("schema_version", 2), ("floor_tag_id", 0), ("home_tag_id", 6)):
        if type(profile[key]) is not int or profile[key] != expected:
            raise ValueError(f"{key} must be {expected}")
    for key, expected in (("wall_ids_left_to_right", WALL_IDS), ("route_ids", ROUTE_IDS)):
        if (profile[key] != expected or not isinstance(profile[key], list)
                or any(type(item) is not int for item in profile[key])):
            raise ValueError(f"{key} must be {expected}")
    if type(profile["layout_confirmed"]) is not bool:
        raise ValueError("layout_confirmed must be a boolean")
    for key, expected in (("wall_measurement", "image_only"), ("floor_size_source", "private_config")):
        if profile[key] != expected:
            raise ValueError(f"{key} must be {expected}")
    for key, low, high in (
        ("target_height_m", 1.4, 1.6),
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


def validate_external_route(route):
    """Separate HTTP extension; the standalone profile/CLI route stays fixed."""
    if (not isinstance(route, (list, tuple)) or len(route) != 5
            or any(type(tag) is not int for tag in route)
            or route[0] != 6 or route[-1] != 6 or set(route[1:-1]) != {1, 2, 3}):
        raise ValueError("External route must be Home6, each of ID1/2/3 once, Home6")
    return tuple(route)


def external_direction(route, departure, expected):
    route = validate_external_route(route)
    if (departure, expected) not in tuple(zip(route, route[1:])):
        raise ValueError("Leg is not in the explicitly selected external route")
    return "left" if WALL_IDS.index(expected) < WALL_IDS.index(departure) else "right"


def plan(profile, pair_reference=None, continue_patrol=False):
    validate_profile(profile)
    if continue_patrol and pair_reference is None:
        raise ValueError("continue_patrol requires ID1 pair framing")
    result = {
        "mode": "offline_plan", "profile": profile,
        "legs": [{"from": a, "to": b, "direction": planned_direction(a, b)}
                 for a, b in zip(ROUTE_IDS, ROUTE_IDS[1:])],
        "required_bridge_build_id": BUILD_ID,
        "height_source": "downward_ultrasonic_display_with_floor_ID0_visual_crosscheck",
        "climb_timeout_s": CLIMB_TIMEOUT_S,
        "takeoff_settle_timeout_s": TAKEOFF_SETTLE_TIMEOUT_S,
        "takeoff_stable_hold_s": TAKEOFF_STABLE_HOLD_S,
        "wall_home_visibility_margin_fraction": .03,
        "wall_capture_bounds_fraction": {"x_min": .15, "x_max": .85, "y_min": .15, "y_max": .85},
        "unique_frame_hold_s": CONFIRM_S,
        "lateral_phase_axes": ["right_tilt_deg"],
        "finish": "hover_release_to_RC_manual_landing",
        "pc_obstacle_distance_policy": "observe_only",
        "aircraft_avoidance_changes": False,
        "navigation": "wall_image_centers_no_wall_size_or_metric_pose",
        "frame_age_basis": "PC_decode_time_not_aircraft_exposure",
        "network_connections_opened": 0,
    }
    if pair_reference is not None:
        result.pop("wall_capture_bounds_fraction", None)
        result.update(mission_scope="ID1_and_TV_pair_capture_only", active_route_ids=[6, 1],
            legs=[{"from": 6, "to": 1, "direction": "left"}],
            finish="hover_at_ID1_release_to_RC_manual_landing",
            framing_reference=pair_reference, approach_tilt_limit_deg=.6,
            right_correction_limit_deg=.6, tv_visibility_verified=False,
            navigation="ID1_reference_plane_pair_framing",
            unique_frame_hold_s=.5, zero_hold_s=1.,
            correction_mode="continuous_proportional", max_reverse_pulses=3)
        if continue_patrol:
            result.update(mission_scope="full_patrol_with_first_ID1_pair_capture",
                photo_quality_policy="require_full_ID1_and_mock_framing_before_continuing",
                active_route_ids=list(ROUTE_IDS),
                legs=[{"from": a, "to": b, "direction": planned_direction(a, b)}
                      for a, b in zip(ROUTE_IDS, ROUTE_IDS[1:])],
                finish="hover_at_ID6_release_to_RC_manual_landing",
                pair_capture_visit_index=1, pair_capture_ids=[1, 2, 3], patrol_tilt_limit_deg=.6,
                profile={**profile, "max_tilt_deg": .6})
        if pair_reference.get("arrival_center_x_fraction") is not None:
            result.update(navigation="ID1_tag_right_edge_band",
                id1_arrival_center_x_fraction=pair_reference["arrival_center_x_fraction"],
                photo_quality_policy="tag_edge_arrival_then_actual_photo_review",
                predicted_footprint_policy="diagnostic_only",
                framing_mismatch_policy="zero_then_gentle_correction_and_retry")
    return result


def prepare_config(path, profile, host=None):
    return configure_execution(load_config(path), profile, host)


def configure_execution(config, profile, host=None):
    """Validate both CLI and direct Python entry points without changing files."""
    validate_profile(profile)
    if not profile["layout_confirmed"]:
        raise ValueError("Confirmed wall layout is required for execution")
    if config.patrol is None or not config.camera.calibrated:
        raise ValueError("Existing patrol settings and calibrated camera intrinsics are required")
    floor = config.tag_map.get(0)
    if floor is None or not _number(floor.size_m, .001, 10.):
        raise ValueError("Existing private floor ID0 size is required for the ascent cross-check")
    address = host or config.network.host
    ipaddress.ip_address(address)
    if (not config.network.confirmation_token
            or config.network.confirmation_token.startswith("REPLACE_")):
        raise ValueError("Private phone confirmation token is required")
    # Preserve all size, pose and calibration facts verbatim. Wall detections
    # need no map entry, so the new home ID6 is never assigned a made-up size.
    return replace(config,
        network=replace(config.network, host=address, rate_hz=10.),
        patrol=replace(config.patrol, route_ids=tuple(ROUTE_IDS[:4]), outbound_direction="left", obstacle_stop_m=0.,
            cruise_altitude_m=profile["target_height_m"],
            angle_deg=profile["max_tilt_deg"], recovery_max_angle_deg=profile["max_tilt_deg"],
            leg_timeout_s=profile["leg_timeout_s"], acquire_timeout_s=12.,
            tag_confirm_s=CONFIRM_S, wall_center_tolerance_px=CENTER_TOLERANCE_PX,
            wall_view_x_min=.15, wall_view_x_max=.85, wall_view_y_min=.15, wall_view_y_max=.85,
            visit_pause_s=profile["visit_pause_s"], align_cruise_yaw=False))


@dataclass(frozen=True)
class PixelTag:
    tag_id: int
    center_px: tuple[float, float]
    corners_px: tuple[tuple[float, float], ...]
    decision_margin: float | None
    hamming: int | None
    T_C_T: None = None
    pose_error: None = None


class MixedDetector:
    """Wall pixels and calibrated floor ID0 pose have separate data paths.

    The no-pose pass recognizes unconfigured wall IDs, including ID6. Only
    floor ID0 can contribute metric output; its existing private size and the
    calibrated camera are passed unchanged to the tested floor pose adapter.
    """
    def __init__(self, config, detector_factory=AprilTagDetector):
        floor = config.tag_map.get(0)
        if floor is None or not config.camera.calibrated:
            raise ValueError("Calibrated camera and existing floor ID0 configuration required")
        self.floor_config = replace(config, tags=(floor,))
        self.base = detector_factory(self.floor_config)

    def detect(self, frame):
        base = self.base
        pixels = base._cv2.undistort(frame, base._matrix, base._distortion)
        self.last_input_frame, self.last_detection_frame = frame, pixels
        gray = (base._cv2.cvtColor(pixels, base._cv2.COLOR_BGR2GRAY)
                if getattr(pixels, "ndim", 0) == 3 else pixels)
        raw = base._detector.detect(gray, estimate_tag_pose=False)
        walls = []
        for item in raw:
            tag_id = int(item.tag_id)
            if tag_id not in WALL_IDS:
                continue
            center = tuple(float(value) for value in item.center)
            corners = tuple(tuple(float(value) for value in corner) for corner in item.corners)
            margin = float(item.decision_margin)
            walls.append(PixelTag(tag_id, center, corners,
                                  margin if math.isfinite(margin) else None, int(item.hamming)))
        # No wall tag is returned with a pose, even if a stale prior wall size
        # exists in the user's config. With no ID0 visible there is no pose pass.
        floor = ([tag for tag in base.detect(frame) if tag.tag_id == 0]
                 if any(int(item.tag_id) == 0 for item in raw) else [])
        return [*floor, *walls]


class ShuttleDetectionLogger(_DetectionLogger):
    @staticmethod
    def payload(detection, *, phase, expected_id, frame_age_s, telemetry, direction):
        if isinstance(detection, PixelTag):
            return {
                "tag_id": detection.tag_id, "phase": phase.value,
                "expected_id": expected_id, "center_px": list(detection.center_px),
                "corners_px": [list(corner) for corner in detection.corners_px],
                "decision_margin": detection.decision_margin, "hamming": detection.hamming,
                "camera_translation_m": None, "camera_to_tag_transform": None,
                "range_m": None, "pose_error": None, "metric_pose_available": False,
                "measurement": "image_only", "frame_age_s": frame_age_s,
                "height_m": None if telemetry is None else telemetry.height_m,
                "direction": direction,
            }
        payload = _DetectionLogger.payload(detection, phase=phase, expected_id=expected_id,
            frame_age_s=frame_age_s, telemetry=telemetry, direction=direction)
        payload.update(metric_pose_available=True, measurement="calibrated_floor_camera_relative",
                       camera_to_tag_transform=[list(row) for row in detection.T_C_T])
        return payload


class FramingCorrectionDeferred(InterruptedError):
    """An unsent correction needs a new observation, not a new mission."""


class ShuttleClient(MissionClient):
    """Keep the validated transport and enforce this trial's axes at dispatch."""
    flight_state_max_ms = FLIGHT_STATE_FRESH_S * 1000

    def __init__(self, config, cancel, on_snapshot):
        super().__init__(config, cancel, on_snapshot)
        self.phase = "preflight"
        self.video_generation = None
        self.max_tilt = config.patrol.angle_deg
        self.lateral_bounds = (-self.max_tilt, self.max_tilt)
        self.motion_valid_until_s = None
        self.pair_mode = False
        self._pair_dispatch_proof = None

    def arm(self, confirmation_token):
        """Arm once, then admit motion only after the SDK authority callback."""
        if self._armed:
            raise PermissionError("Already armed; no automatic re-arm")
        previous_phase = self.phase
        self.phase = "arming"
        ready = False
        try:
            super().arm(confirmation_token)
            # This local flag means motion-ready, not merely arm ACK received.
            # The arming phase permits no attitude commands while callbacks lag.
            self._armed = False
            deadline = time.monotonic() + 2.
            while time.monotonic() < deadline:
                self.status("standalone_wait_arm_authority")
                raw, t = self.raw, self.last_telemetry
                elapsed = time.perf_counter() - self.received
                if (t is None or raw.get("is_flying") is not True
                        or not _number(elapsed, 0., FRESH_S)
                        or not fresh(raw, "is_flying", max(0., (FLIGHT_STATE_FRESH_S-elapsed)*1000))):
                    raise InterruptedError("Fresh airborne state lost during arm transition")
                if t.rc_override_age_s is not None and 0 <= t.rc_override_age_s < 5:
                    raise InterruptedError("RC override during arm transition; no re-arm")
                ready = (raw.get("armed") is True and raw.get("vs_enabled") is True
                         and raw.get("vs_advanced_enabled") is True and raw.get("vs_authority") == "MSDK")
                self.log_event("standalone_arm_transition", {
                    "ready": ready, "armed": raw.get("armed"),
                    "vs_enabled": raw.get("vs_enabled"), "vs_authority": raw.get("vs_authority")})
                if ready:
                    return
                time.sleep(.1)
            raise TimeoutError("Virtual Stick authority did not become ready within 2s; no re-arm")
        finally:
            self._armed = ready
            self.phase = previous_phase

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
            permitted = forward == up == yaw == 0. and _number(right, *self.lateral_bounds)
        else:
            permitted = False
        if not permitted:
            raise PermissionError("Motion axes are not permitted in the current shuttle phase")
        if self.motion_valid_until_s is not None and time.monotonic() >= self.motion_valid_until_s:
            raise FramingCorrectionDeferred("Framing correction expired before dispatch")
        if self.pair_mode and self.phase == "lateral":
            if self._pair_dispatch_proof is None:
                speed, age = _horizontal_motion_evidence(self)
            else:
                speed, age, sampled_at = self._pair_dispatch_proof
                age = None if age is None else age + time.perf_counter() - sampled_at
            if not _number(age, 0., FRESH_S) or speed is None:
                raise InterruptedError("Fresh horizontal velocity required at pair dispatch")
            if self.motion_valid_until_s is not None and speed > .08:
                raise FramingCorrectionDeferred("Aircraft no longer settled before framing pulse dispatch")

    def send(self, kind, payload, timeout_s=None):
        # MissionClient.attitude() polls status before reaching this method:
        # admission here therefore rechecks the exact detection after that RTT.
        if kind == "attitude" and self.pair_mode:
            # MissionClient clears telemetry while awaiting this command's ACK.
            # Preserve only this dispatch's proof for the final log/write check.
            speed, age = _horizontal_motion_evidence(self)
            self._pair_dispatch_proof = (speed, age, time.perf_counter())
        try:
            self._guard_dispatch(kind, payload)
            return super().send(kind, payload, timeout_s)
        finally:
            self._pair_dispatch_proof = None

    def _log_event(self, event, data):
        super()._log_event(event, data)
        if event == "pc_request":
            # NDJSONClient logs just before sendall. Recheck after a slow log
            # write as well; an unsent refusal preserves the cleanup sequence.
            self._guard_dispatch(data.get("type"), data.get("payload", {}))


class HorizontalGate:
    """Accept the expected tag inside the tested broad capture window.

    Wall travel is either the planned direction or zero. No point-centering,
    reverse correction or height chasing is produced. Stationary home admission
    requires the whole tag in frame, without requiring pixel-center alignment.
    """
    def __init__(self, expected, direction, target_x, max_tilt, patrol, *, stationary_home=False):
        if direction not in ("left", "right"):
            raise ValueError("A lateral direction is required")
        self.expected, self.direction = expected, direction
        self.target_x, self.max_tilt = target_x, max_tilt
        self.patrol, self.stationary_home = patrol, stationary_home
        self.last_key = None
        self.target_seen = False
        self.centered_since = None
        self.framing_action = "not_observed"
        self.expected_center_px = None

    def update(self, detections, now_s, frame_age, frame_key, frame_shape):
        if not _number(frame_age, 0., FRESH_S) or frame_key is None:
            raise RuntimeError("Fresh identified camera frame required; lateral motion cancelled")
        if self.last_key is not None:
            if frame_key[0] != self.last_key[0]:
                raise InterruptedError("Video stream generation changed; no automatic flight resume")
            if frame_key[1] <= self.last_key[1]:
                self.framing_action = "duplicate_or_old_frame"
                return 0., None
        self.last_key = frame_key
        choices = [d for d in detections if isinstance(d, PixelTag) and d.tag_id == self.expected
                   and d.center_px is not None and len(d.center_px) == 2
                   and all(_number(v, -1e6, 1e6) for v in d.center_px)
                   and type(d.hamming) is int and 0 <= d.hamming <= 2
                   and _number(d.decision_margin, 0., 1e12)]
        observed = min(choices, key=lambda d: (d.hamming, -d.decision_margin,
                        abs(d.center_px[0] - self.target_x))) if choices else None
        self.expected_center_px = None if observed is None else observed.center_px
        if observed is None:
            self.centered_since = None
            self.framing_action = "expected_tag_not_visible_or_quality_invalid"
            return (0. if self.target_seen or self.stationary_home
                    else (-self.max_tilt if self.direction == "left" else self.max_tilt)), None
        self.target_seen = True
        if self.stationary_home:
            height, width = frame_shape[:2] if len(frame_shape) >= 2 else (0, 0)
            corners = observed.corners_px
            inside = (_number(height, 1., 1e6) and _number(width, 1., 1e6)
                      and corners is not None and len(corners) == 4
                      and all(len(point) == 2 and _number(point[0], .03*width, .97*width)
                              and _number(point[1], .03*height, .97*height) for point in corners))
            self.framing_action = "stationary_home_visible" if inside else "stationary_home_wait"
        else:
            action = wall_view_action(observed.center_px, frame_shape, self.direction, self.patrol)
            self.framing_action = action.value
            inside = action is WallViewAction.INSIDE
        if inside:
            self.centered_since = now_s if self.centered_since is None else self.centered_since
            if now_s - self.centered_since >= CONFIRM_S:
                return 0., observed
            return 0., None
        self.centered_since = None
        if self.stationary_home:
            return 0., None
        if action is WallViewAction.APPROACH:
            return (-1. if self.direction == "left" else 1.) * self.max_tilt * .35, None
        raise RuntimeError(f"ID{self.expected}: {action.value}; no reverse/vertical chasing")


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
            or not fresh(raw, "is_flying", max(0., (FLIGHT_STATE_FRESH_S - elapsed) * 1000))):
        raise InterruptedError("Fresh airborne state lost; no resume")
    if (raw.get("armed") is not True or raw.get("vs_enabled") is not True
            or raw.get("vs_advanced_enabled") is not True or raw.get("vs_authority") != "MSDK"):
        raise InterruptedError("RC/Virtual Stick authority changed; no re-arm")


def _wait_ground_video(client, limiter, stream, detector, logger):
    # At ground height the downward camera can crop the printed floor tag.
    # The tested takeoff sequence requires fresh video here, then ID0 after
    # takeoff before ascent/lateral motion. All visible IDs are still logged.
    deadline, previous_key, held = time.monotonic() + 20., None, None
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


def _takeoff_observation(client):
    """Read-only readiness evidence, aged through PC processing time."""
    t, raw = client.last_telemetry, client.raw
    elapsed = time.perf_counter() - client.received
    if (t is None or not _number(elapsed, 0., FRESH_S)
            or type(raw.get("is_flying")) is not bool
            or not fresh(raw, "is_flying", max(0., (FLIGHT_STATE_FRESH_S - elapsed) * 1000))):
        raise RuntimeError("Fresh takeoff flight-state observation unavailable")
    for name in ("height_age_s", "velocity_age_s", "flight_mode_age_s"):
        age = getattr(t, name, None)
        if not _number(age, 0., FRESH_S) or not _number(age + elapsed, 0., FRESH_S):
            raise RuntimeError("Fresh takeoff height, velocity and flight mode are required")
    if t.rc_override_age_s is not None and 0 <= t.rc_override_age_s < 5:
        raise InterruptedError("RC override during takeoff; no automatic arm")
    if not _number(t.height_m, 0., 1.8):
        raise RuntimeError("Takeoff height exceeds the standalone envelope or is unavailable")
    if not _number(t.velocity_down_mps, -1e3, 1e3) or not isinstance(t.flight_mode, str):
        raise RuntimeError("Takeoff vertical speed or flight mode unavailable")
    return {"is_flying": raw["is_flying"], "height_m": t.height_m,
            "flight_mode": t.flight_mode, "velocity_down_mps": t.velocity_down_mps}


def _wait_takeoff_settled(client, limiter):
    """Wait for completion of DJI's automatic takeoff before the sole arm.

    GPS_NORMAL is the observed position/idle mode name on this firmware; it
    does not assert that a GNSS fix is in use. No arm retries are performed.
    """
    client.phase = "takeoff"
    deadline, stable_since = time.monotonic() + TAKEOFF_SETTLE_TIMEOUT_S, None
    while time.monotonic() < deadline:
        limiter.wait()
        client.status("standalone_takeoff_settle")
        sample = _takeoff_observation(client)
        client.log_event("standalone_takeoff_settle_sample", sample)
        # Logging/processing time also ages the proof used for the next arm.
        sample = _takeoff_observation(client)
        now = time.monotonic()
        if now >= deadline:
            break
        ready = (sample["is_flying"] is True and .5 <= sample["height_m"] <= 1.8
                 and sample["flight_mode"] == "GPS_NORMAL"
                 and abs(sample["velocity_down_mps"]) <= .05)
        if ready:
            stable_since = now if stable_since is None else stable_since
            if now - stable_since >= TAKEOFF_STABLE_HOLD_S - 1e-8:
                return
        else:
            stable_since = None
    raise TimeoutError("DJI takeoff did not settle within 20s; no arm retry; RC landing required")


def _climb(client, limiter, stream, detector, logger, config, target):
    client.phase = "climb"
    deadline, held, previous_key = time.monotonic() + CLIMB_TIMEOUT_S, None, None
    generation = None
    def require_frame(snapshot):
        nonlocal generation
        if (snapshot is None or stream.last_detection_snapshot is not snapshot
                or not _number(time.monotonic() - snapshot.received_s, 0., FRESH_S)):
            raise InterruptedError("Exact floor frame expired or changed during climb")
        if generation is None:
            generation = snapshot.key[0]
        elif snapshot.key[0] != generation:
            raise InterruptedError("Video generation changed during climb")
    def fresh_height_command():
        _require_flight(client)
        telemetry = client.last_telemetry
        if telemetry is None:
            raise RuntimeError("Fresh climb telemetry unavailable")
        rc_age = telemetry.rc_override_age_s
        if rc_age is not None and 0 <= rc_age < 5:
            raise InterruptedError("RC override during climb; no resume")
        elapsed = time.perf_counter() - client.received
        age = None if telemetry.height_age_s is None else telemetry.height_age_s + elapsed
        return telemetry, elapsed, climb_command(telemetry.height_m, age, target)
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
        snapshot = stream.last_detection_snapshot
        # Detection/pose computation can consume the prior height's 500 ms
        # budget. Refresh telemetry, then re-age this exact processed image.
        client.status("standalone_climb_after_video")
        require_frame(snapshot)
        t, elapsed, up = fresh_height_command()
        now = time.monotonic()
        if now >= deadline:
            break
        row = {"target_height_m": target, "height_m": t.height_m,
               "floor_visual_height_m": visual_height, "up_mps": up,
               "source": "downward_ultrasonic_display"}
        client.log_event("standalone_climb_sample", row)
        require_frame(snapshot)
        t, elapsed, up = fresh_height_command()
        if up == 0.:
            client.zero()
            require_frame(snapshot)
            t, elapsed, post_zero_up = fresh_height_command()
            now = time.monotonic()
            if now >= deadline:
                break
            stable = (post_zero_up == 0. and _number(t.velocity_down_mps, -.05, .05)
                      and t.velocity_age_s is not None
                      and _number(t.velocity_age_s + elapsed, 0., FRESH_S))
            key = snapshot.key
            if not stable or key is None:
                held, previous_key = None, None
                continue
            if previous_key is not None and key[0] != previous_key[0]:
                raise InterruptedError("Video generation changed during climb")
            if previous_key is None or key[1] > previous_key[1]:
                held = now if held is None else held
                if now - held >= .5:
                    row.update(height_m=t.height_m, velocity_down_mps=t.velocity_down_mps,
                               height_age_s=t.height_age_s + elapsed,
                               velocity_age_s=t.velocity_age_s + elapsed,
                               confirmation_source="fresh_zero_ack", frame_key=key)
                    client.log_event("standalone_target_height_confirmed", row)
                    require_frame(snapshot)
                    final_t, final_elapsed, final_up = fresh_height_command()
                    if (final_up != 0. or not _number(final_t.velocity_down_mps, -.05, .05)
                            or final_t.velocity_age_s is None
                            or not _number(final_t.velocity_age_s + final_elapsed, 0., FRESH_S)):
                        raise InterruptedError("Climb confirmation changed or expired during logging")
                    return
                previous_key = key
        else:
            held, previous_key = None, None
            client.attitude(0., 0., up, 0.)
    raise RuntimeError(f"{target:g}m downward height target not confirmed within {CLIMB_TIMEOUT_S:g}s; no lateral command")


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


def acquire_wall_home(client, limiter, stream, detector, logger, config):
    """Confirm visible ID6 while stationary; no center alignment is required."""
    client.phase = "hover"
    gate = HorizontalGate(6, "left", config.camera.cx, config.patrol.angle_deg,
                          config.patrol, stationary_home=True)
    deadline = time.monotonic() + config.patrol.acquire_timeout_s
    while time.monotonic() < deadline:
        limiter.wait()
        client.zero()
        _require_flight(client)
        tags, age = _observe(client, stream, detector, logger, PatrolPhase.WALL_HOME, 6)
        now = time.monotonic()
        if now >= deadline:
            break
        snapshot = stream.last_detection_snapshot
        frame_shape = () if snapshot is None else snapshot.frame.shape
        _, confirmed = gate.update(tags, now, age, _snapshot_key(stream), frame_shape)
        if confirmed is not None:
            client.log_event("tag_visit_confirmed", logger.payload(confirmed,
                phase=PatrolPhase.WALL_HOME, expected_id=6, frame_age_s=age,
                telemetry=client.last_telemetry, direction=None))
            logger.save_confirmation_photo(stream, confirmed, phase=PatrolPhase.WALL_HOME)
            return confirmed
    raise TimeoutError("Wall home ID6 was not fully visible in fresh images; no lateral movement")


def traverse_horizontal(client, limiter, stream, detector, logger, config, profile,
                        departure, expected, phase, *, external_route=None):
    client.phase = "lateral"
    direction = (planned_direction(departure, expected) if external_route is None
                 else external_direction(external_route, departure, expected))
    gate = HorizontalGate(expected, direction, config.camera.cx, profile["max_tilt_deg"], config.patrol)
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
        snapshot = stream.last_detection_snapshot
        frame_shape = () if snapshot is None else snapshot.frame.shape
        try:
            right, confirmed = gate.update(tags, now, age, _snapshot_key(stream), frame_shape)
        except (RuntimeError, InterruptedError):
            client.zero()
            raise
        client.log_event("standalone_horizontal_sample", {
            "expected_id": expected, "visible_ids": [tag.tag_id for tag in tags],
            "frame_age_s": age, "frame_key": _snapshot_key(stream),
            "target_seen": gate.target_seen, "right_tilt_deg": right,
            "expected_center_px": gate.expected_center_px, "framing_action": gate.framing_action,
            "view_bounds_fraction": wall_view_bounds(config.patrol),
            "frame_size_px": [frame_shape[1], frame_shape[0]] if len(frame_shape) >= 2 else None,
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


def _horizontal_motion_evidence(client):
    t = client.last_telemetry
    elapsed = time.perf_counter() - client.received
    age = None if t is None or t.velocity_age_s is None else t.velocity_age_s + elapsed
    values = () if t is None else (t.velocity_north_mps, t.velocity_east_mps)
    speed = math.hypot(*values) if len(values) == 2 and all(_number(v, -100., 100.) for v in values) else None
    return speed, age


def capture_id1_pair(client, limiter, stream, detector, logger, config, profile, gate,
                     *, departure=6, expected=1, external_route=None,
                     on_capture=None, capture_count=1):
    """Frame the expected wall tag with its left-hand mock and capture.

    Used for every outbound visit (ID1, ID2, ID3): the first photo of each
    tag must include the whole mock beside it. The caller then advances.
    """
    direction = (planned_direction(departure, expected) if external_route is None
                 else external_direction(external_route, departure, expected))
    if external_route is not None and (gate.direction != direction or gate.tag_id != expected):
        raise ValueError("Pair gate must match the validated leg direction and tag")
    if type(capture_count) is not int or capture_count not in (1, 2):
        raise ValueError("One or two distinct capture frames are supported")
    if capture_count != 1 and on_capture is None:
        raise ValueError("Multiple captures require an exact-frame capture hook")
    captured_count, captured_key = 0, None
    client.phase = "lateral"
    client.pair_mode = True
    # Corrections back toward ID1 use the same tilt authority as the seek;
    # 0.25 deg pulses did not move the aircraft enough to recover an overshoot.
    client.lateral_bounds = (-.6, .6)
    deadline = time.monotonic() + profile["leg_timeout_s"]
    first_id1_recorded = False
    client.log_event("standalone_leg", {"from": departure, "to": expected, "direction": direction,
        "phase_scope": f"first_ID{expected}_pair_framing"})
    print(f"ID{departure} -> ID{expected}: frame ID{expected} and the entire left-hand mock", flush=True)
    while time.monotonic() < deadline:
        limiter.wait()
        client.status("id1_pair_framing")
        _require_flight(client)
        tags, age = _observe(client, stream, detector, logger, PatrolPhase.OUTBOUND, expected, direction)
        snapshot = stream.last_detection_snapshot
        shape = () if snapshot is None else snapshot.frame.shape
        if not first_id1_recorded and snapshot is not None and any(tag.tag_id == expected for tag in tags):
            first_id1_recorded = True
            # Persist the exact first detection, rather than relying on the
            # periodic recorder's possibly earlier frame. This is not a pass.
            try:
                path = logger.photo_root / f"diagnostic_first_ID{expected}.jpg"
                write_image(path, snapshot.frame)
                client.log_event("id1_first_detection_image", {"tag_id": expected, "photo_path": str(path),
                    "frame_key": snapshot.key, "diagnostic_only": True,
                    "capture_passed": False})
            except Exception as exc:
                client.log_event("id1_diagnostic_image_error", {"error_type": type(exc).__name__})
        client.status("id1_pair_after_detection")
        _require_flight(client)
        age = float("inf") if snapshot is None else time.monotonic() - snapshot.received_s
        speed, velocity_age = _horizontal_motion_evidence(client)
        right, confirmed = gate.update(tags, time.monotonic(), age, _snapshot_key(stream),
                                       shape, speed, velocity_age)
        diagnostic = dict(gate.diagnostic)
        client.log_event("id1_pair_framing_sample", {**diagnostic, "tag_id": expected,
            "visible_ids": [tag.tag_id for tag in tags], "right_tilt_deg": right,
            "frame_key": _snapshot_key(stream), "frame_age_s": age,
            "horizontal_speed_mps": speed, "velocity_age_s": velocity_age})
        pulse_deadline = diagnostic.get("motion_valid_until_s")
        client.motion_valid_until_s = pulse_deadline
        if right:
            try:
                client.attitude(0., right, 0., 0.)
                if pulse_deadline is not None:
                    # This is the requested PC pulse duration. An ACK delay can
                    # outlast it; physical duration is not asserted from this timer.
                    remaining = pulse_deadline - time.monotonic()
                    if remaining > 0:
                        time.sleep(min(remaining, .25))
            except FramingCorrectionDeferred as exc:
                # Only a local refusal before the wire write is recoverable.
                # ACK rejection, lost authority and uncertain transport outcomes
                # still propagate to mission cleanup; no command is replayed.
                client.log_event("id1_pair_correction_deferred", {
                    "reason": str(exc), "requested_right_tilt_deg": right,
                    "controller_state": diagnostic.get("state"),
                    "frame_key": _snapshot_key(stream), "command_sent": False})
            finally:
                if pulse_deadline is not None:
                    client.motion_valid_until_s = None
                    client.zero()
        else:
            client.zero()
        if confirmed is not None:
            edge_arrival = diagnostic.get("arrival_policy") == "tag_right_edge_band"
            if edge_arrival and diagnostic.get("arrival_ready") is not True:
                raise RuntimeError(f"ID{expected} edge arrival has not been confirmed")
            if not edge_arrival and diagnostic.get("footprint", {}).get("fits") is not True:
                raise RuntimeError(f"Full ID{expected} and mock footprint is required; no partial-photo pass")
            _require_flight(client)
            # The zero ACK can update velocity or consume the detection lifetime.
            # Capture only the same identified frame used for the fit decision.
            if (snapshot is None or stream.last_detection_snapshot is not snapshot
                    or not _number(time.monotonic() - snapshot.received_s, 0., FRESH_S)):
                raise InterruptedError(f"ID{expected} capture frame expired or changed after settling")
            speed, velocity_age = _horizontal_motion_evidence(client)
            if not _number(velocity_age, 0., FRESH_S) or speed is None:
                raise InterruptedError(f"Fresh horizontal velocity required for ID{expected} capture")
            if speed > .08:
                client.log_event("id1_pair_capture_deferred", {"reason": "motion_after_zero_ack",
                    "horizontal_speed_mps": speed, "velocity_age_s": velocity_age})
                continue
            if captured_key is not None:
                if snapshot.key[0] != captured_key[0]:
                    raise InterruptedError("Capture generation changed; no resume")
                if snapshot.key[1] <= captured_key[1]:
                    continue
            # The HTTP hook encodes these exact undistorted detection pixels,
            # before any optional JPEG logging can consume their freshness budget.
            if on_capture is not None and not on_capture(client, stream, snapshot, confirmed, diagnostic):
                continue
            photo = logger.save_confirmation_photo(stream, confirmed, phase=PatrolPhase.OUTBOUND)
            if photo is None and on_capture is None:
                raise RuntimeError(f"ID{expected} framing was ready but its required photo was not saved")
            record = {"tag_id": expected, "photo_path": str(photo),
                "predicted_pair_footprint_fits": diagnostic.get("footprint", {}).get("fits") is True,
                "photo_quality": diagnostic.get("photo_quality", "unverified"),
                "arrival_policy": diagnostic.get("arrival_policy", "strict_reference_footprint"),
                "tv_visibility_verified": False, "direction": direction,
                "reference_plane_estimate": True, "frame_key": _snapshot_key(stream),
                "diagnostic": diagnostic}
            captures = list(getattr(client, "pair_captures", None) or [])
            captures.append(record)
            client.pair_captures = captures
            if getattr(client, "pair_capture", None) is None:
                client.pair_capture = record  # first (ID1) capture, kept for compatibility
            client.log_event("id1_pair_capture", record)
            client.log_event("tag_visit_confirmed", logger.payload(confirmed,
                phase=PatrolPhase.OUTBOUND, expected_id=expected, frame_age_s=age,
                telemetry=client.last_telemetry, direction=direction))
            print(f"ID{expected} pair photo saved: {photo}", flush=True)
            captured_key, captured_count = snapshot.key, captured_count + 1
            if captured_count == capture_count:
                return
    raise TimeoutError(f"ID{expected} framing not confirmed within {profile['leg_timeout_s']:g}s "
                       f"after correction attempts; last state={gate.diagnostic.get('state')}")


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


def run(config, profile, cancel=None, pair_reference=None, continue_patrol=False, *,
        external_route=None, video_broker=None, on_snapshot=None, on_event=None,
        on_capture=None, capture_count=1):
    if (type(capture_count) is not int or capture_count not in (1, 2)
            or capture_count == 2 and on_capture is None
            or on_capture is not None and pair_reference is None):
        raise ValueError("Capture hooks require pair framing and one or two identified frames")
    if external_route is not None:
        external_route = validate_external_route(external_route)
        if (pair_reference is None or continue_patrol or on_capture is None
                or capture_count != 2):
            raise ValueError("External route requires pair framing and two exact-frame captures")
    if continue_patrol and pair_reference is None:
        raise ValueError("continue_patrol requires ID1 pair framing")
    config = configure_execution(config, profile)
    pair_gate = None
    if pair_reference is not None:
        from id1_pair_framing import PairFramingGate
        first_tag = 1 if external_route is None else external_route[1]
        pair_gate = PairFramingGate(pair_reference, direction="left",
            arrival_band=pair_reference.get("arrival_center_x_fraction"), tag_id=first_tag)
        profile = {**profile, "max_tilt_deg": .6}
        config = replace(config, patrol=replace(config.patrol, angle_deg=.6, recovery_max_angle_deg=.6))
    active_route = list(ROUTE_IDS) if continue_patrol or pair_gate is None else [6, 1]
    if external_route is not None:
        active_route = list(external_route)
    cancel = threading.Event() if cancel is None else cancel
    emit = on_event or (lambda **event: None)
    client = ShuttleClient(config, cancel, on_snapshot or (lambda snapshot: None))
    stream = logger = None
    visited, completed, error, diagnostic_errors = [], False, None, []
    release = {"control_released_to_rc": False, "ground_verified": False,
               "physical_stop_confirmed": False, "cleanup_errors": []}
    try:
        if cancel.is_set():
            raise InterruptedError("Cancelled before connection; no resume")
        emit(state="preflight")
        client.connect()
        client.deadline = time.perf_counter() + profile["total_timeout_s"]
        client.status("standalone_preflight")
        if not ground_verified(client.raw) or client.raw.get("bridge_build_id") != BUILD_ID:
            raise RuntimeError(f"Fresh motors-off grounded RC state and bridge {BUILD_ID} required")
        if not process_identity(client.raw) or client.raw.get("telemetry_generation") is None:
            raise RuntimeError("App process/generation identity required")
        if not _number(client.last_telemetry.battery_percent, 30., 100.):
            raise RuntimeError("At least 30% battery required")
        execution_plan = plan(profile, pair_reference, continue_patrol)
        if external_route is not None:
            execution_plan.update(mission_scope="external_ordered_pair_captures",
                active_route_ids=active_route,
                legs=[{"from": a, "to": b, "direction": external_direction(external_route, a, b)}
                      for a, b in zip(active_route, active_route[1:])],
                finish="hover_at_ID6_release_to_RC_manual_landing", captures_per_visit=2)
        execution_plan.pop("network_connections_opened")
        client.log_event("standalone_plan", {**execution_plan, "mode": "execute",
            "camera_calibrated": config.camera.calibrated,
            "body_camera_calibrated": config.body_camera.calibrated,
            "actual_measurements_confirmed": config.actual_measurements_confirmed,
            "world_pose_used": False})
        print(f"GROUND confirmed; log={client.log_path}", flush=True)
        detector = MixedDetector(config)
        stream = (video_broker.acquire_mission() if video_broker is not None else
                  FreshVideoStream(config.network.host, config.network.video_port, config.network.video_codec,
                                   initial_keyframe_timeout_s=15.))
        client.stream = stream
        logger = ShuttleDetectionLogger(client, photo_root=ROOT / "pc" / "captures" / client.session_id)
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
        print(f"TAKEOFF once; floor0 -> height{profile['target_height_m']:g}m -> wall6", flush=True)
        emit(state="taking_off")
        client.takeoff(config.network.confirmation_token)
        _wait_takeoff_settled(client, limiter)
        client.arm(config.network.confirmation_token)
        floor = VisitGate(0, PatrolPhase.FLOOR_HOME)
        _acquire_tag(client, limiter, stream, detector, logger, floor, config.patrol)
        _climb(client, limiter, stream, detector, logger, config, profile["target_height_m"])
        client.gimbal(0.)
        _pause(client, limiter, stream, detector, logger, 1., PatrolPhase.WALL_HOME, 6)
        acquire_wall_home(client, limiter, stream, detector, logger, config)
        _require_flight(client)
        visited.append(6)
        emit(state="running")
        _pause(client, limiter, stream, detector, logger, profile["visit_pause_s"], PatrolPhase.WALL_HOME, 6)
        for index, (departure, expected) in enumerate(zip(active_route, active_route[1:])):
            phase = PatrolPhase.OUTBOUND if index < 3 else PatrolPhase.RETURN
            if external_route is not None:
                emit(**({"state": "returning"} if expected == 6
                        else {"visit_index": index, "visit_state": "moving"}))
            if pair_gate is not None and phase is PatrolPhase.OUTBOUND:
                # First visit of every wall tag: frame the tag with its mock.
                # Return visits only need the tag in the broad view.
                direction = ("left" if external_route is None
                             else external_direction(external_route, departure, expected))
                gate = pair_gate if index == 0 else PairFramingGate(
                    pair_reference, direction=direction,
                    arrival_band=pair_reference.get("arrival_center_x_fraction"), tag_id=expected)
                capture_id1_pair(client, limiter, stream, detector, logger, config, profile, gate,
                    departure=departure, expected=expected, external_route=external_route,
                    capture_count=capture_count, on_capture=(None if on_capture is None else
                        lambda *args: on_capture(index, *args)))
                if continue_patrol or external_route is not None:
                    client.pair_mode = False
                    client.motion_valid_until_s = None
                    client.lateral_bounds = (-.6, .6)
                    client.log_event("id1_pair_patrol_continues", {"tag_id": expected,
                        "remaining_ids": active_route[index + 2:], "patrol_tilt_limit_deg": .6})
                    print(f"ID{expected} photo complete; continuing {active_route[index + 2:]}", flush=True)
            else:
                traverse_horizontal(client, limiter, stream, detector, logger, config, profile,
                                    departure, expected, phase,
                                    **({} if external_route is None else {"external_route": external_route}))
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
                  "active_route_ids": active_route,
                  "full_route_completed": completed and visited == (ROUTE_IDS if external_route is None else active_route),
                  "error": error, "log_path": str(client.log_path) if client.log_path else None,
                  "no_flight_action_dispatched": not client.attempted_action,
                  "manual_landing_required": client.attempted_action and not release["ground_verified"]}
        if pair_reference is not None:
            result.update(mission_scope=("full_patrol_with_first_ID1_pair_capture" if continue_patrol
                                         else "ID1_and_TV_pair_capture_only"),
                          pair_capture=getattr(client, "pair_capture", None),
                          pair_captures=list(getattr(client, "pair_captures", None) or []))
        if external_route is not None:
            result["mission_scope"] = "external_ordered_pair_captures"
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
            for resource in (logger, None if video_broker is not None else stream, client):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception as exc:
                        result["cleanup_errors"].append(f"close:{type(exc).__name__}")
            if stream is not None and video_broker is not None:
                try:
                    video_broker.release_mission()
                except Exception as exc:
                    result["cleanup_errors"].append(f"video_release:{type(exc).__name__}")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--config", type=Path, help="Private navigation JSON; required with --execute or --check")
    parser.add_argument("--host", help="Current phone IPv4/IPv6 address override")
    parser.add_argument("--id1-pair", action="store_true", help="Capture ID1 and adjacent mock, then hover for RC handover")
    parser.add_argument("--continue-patrol", action="store_true", help="After the first ID1 pair photo, continue 2,3,2,1,6")
    parser.add_argument("--framing-reference", type=Path, default=DEFAULT_PAIR_REFERENCE)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--execute", action="store_true", help="Connect and execute one supervised shuttle")
    modes.add_argument("--check", action="store_true", help="Validate configuration and installed vision libraries without connecting")
    args = parser.parse_args(argv)
    if args.continue_patrol and not args.id1_pair:
        parser.error("--continue-patrol requires --id1-pair")
    try:
        profile = load_profile(args.profile)
        pair_reference = (json.loads(args.framing_reference.read_text(encoding="utf-8-sig"))
                          if args.id1_pair else None)
        if args.id1_pair:
            from id1_pair_framing import PairFramingGate
            PairFramingGate(pair_reference, direction="left",
                arrival_band=pair_reference.get("arrival_center_x_fraction") if isinstance(pair_reference, dict) else None)
        if not args.execute and not args.check:
            print(json.dumps(plan(profile, pair_reference, args.continue_patrol), ensure_ascii=False, indent=2))
            return 0
        if args.config is None:
            parser.error("--config is required with --execute or --check")
        config = prepare_config(args.config, profile, args.host)
        if args.check:
            MixedDetector(config)
            if pair_reference is not None:
                from id1_pair_framing import PairFramingGate
                PairFramingGate(pair_reference, direction="left",
                    arrival_band=pair_reference.get("arrival_center_x_fraction"))
            import av
            print(json.dumps({"mode": "offline_check", "setup_ready": True,
                "wall_measurement": "image_only", "floor_size_source": "private_config",
                "floor_tag_size_m": config.tag_map[0].size_m,
                "camera_calibrated": config.camera.calibrated,
                "body_camera_calibrated": config.body_camera.calibrated,
                "actual_measurements_confirmed": config.actual_measurements_confirmed,
                "aircraft_connection_verified": False, "physical_flight_verified": False,
                "network_connections_opened": 0}, ensure_ascii=False, indent=2))
            return 0
        result = run(config, profile, pair_reference=pair_reference, continue_patrol=args.continue_patrol)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["manual_landing_required"]:
            print("RC pilot: land manually. No automatic repeat, reconnect, re-arm, or landing.", flush=True)
        return 0 if result["route_completed"] and result["control_released_to_rc"] else 1
    except (ValueError, OSError, ImportError, VisionDependencyError) as exc:
        print(f"Setup error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    for output in (sys.stdout, sys.stderr):
        if hasattr(output, "reconfigure"):
            output.reconfigure(encoding="utf-8", line_buffering=True)
    raise SystemExit(main())
