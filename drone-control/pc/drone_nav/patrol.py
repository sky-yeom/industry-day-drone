"""Landmark-relative four-AprilTag corridor patrol.

The mission deliberately does not use GPS or invented corridor coordinates.
Wall tags are ordered visual gates, while the floor ID0 and wall ID2
observations captured at departure become the return anchors.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
import math
from pathlib import Path
import time
from typing import Iterable

from .config import AppConfig, PatrolConfig
from .controller import Velocity
from .localization import TagDetection
from .protocol import NDJSONClient, RateLimiter, Telemetry
from .runtime import (
    FRESH_FRAME_S,
    _arm_when_ready,
    _directional_obstacle_m,
    _opposite_direction,
    _require_takeoff_battery,
    _send_test_setpoint,
    _test_shutdown,
    _wait_for_takeoff,
)
from .transforms import translation
from .vision import AprilTagDetector, TcpVideoStream
from .observation import FrameRecorder, write_image
from .wall_framing import (
    WallViewAction, tag_view_action, tag_view_bounds, uses_tv_framing,
    tv_frame_window, tv_frame_correction,
)


YAW_ALIGN_TOLERANCE_DEG = 1.0
YAW_ALIGN_MAX_RATE_DPS = 8.0
YAW_ALIGN_TIMEOUT_S = 8.0
YAW_ALIGN_HOLD_S = 0.4
UPWARD_CLEARANCE_MIN_MM = 1300.0
CLIMB_STALL_CONTINUE_S = 5.0
CLIMB_PROGRESS_EPSILON_M = 0.01


def _upward_obstacle_blocks_climb(telemetry: Telemetry | None) -> bool:
    """Diagnostic only; this reading does not alter the proven climb path."""
    return (
        telemetry is not None
        and telemetry.oa_obstacle_data_age_s is not None
        and telemetry.oa_obstacle_data_age_s <= 1.0
        and telemetry.oa_upward_distance_mm is not None
        and 0.0 < telemetry.oa_upward_distance_mm < UPWARD_CLEARANCE_MIN_MM
    )
def _horizontal_oa_ready(telemetry: Telemetry | None) -> bool:
    """Accept the strongest horizontal/upward OA state the aircraft exposes.

    Mini 4 Pro supports the global CLOSE mode but returns UNSUPPORTED for the
    separate directional switches. For supported sub-switches, require a
    false read-back; for unsupported ones, global CLOSE is the strongest
    public control available.
    """

    if telemetry is None or telemetry.oa_type != "CLOSE":
        return False
    if telemetry.oa_horizontal_switch_support in {None, "UNSUPPORTED"}:
        horizontal_ready = True
    else:
        horizontal_ready = telemetry.oa_horizontal_enabled is False
    return horizontal_ready


class PatrolPhase(str, Enum):
    FLOOR_HOME = "floor_home"
    WALL_HOME = "wall_home"
    OUTBOUND = "outbound"
    TURNAROUND = "turnaround"
    RETURN = "return"
    FLOOR_ALIGN = "floor_align"
    COMPLETE = "complete"
    ABORTED = "aborted"


class TagGateAction(str, Enum):
    """Action selected from the ordered visibility of two adjacent tags."""

    SEEK_NEXT = "seek_next"
    CLEAR_DEPARTURE = "clear_departure"
    HOLD_EXPECTED_ONLY = "hold_expected_only"
    CENTER_EXPECTED = "center_expected"


class PatrolRouteTracker:
    """Strict route sequencer; seeing a later tag can never skip a waypoint."""

    def __init__(self, route_ids: tuple[int, ...]) -> None:
        if len(route_ids) < 2 or len(set(route_ids)) != len(route_ids):
            raise ValueError("route_ids must contain at least two distinct IDs")
        if route_ids[0] != 2:
            raise ValueError("route must begin at wall-home ID 2")
        self.route_ids = route_ids
        self.phase = PatrolPhase.FLOOR_HOME
        self._index = 0

    @property
    def expected_id(self) -> int | None:
        if self.phase in {PatrolPhase.FLOOR_HOME, PatrolPhase.FLOOR_ALIGN}:
            return 0
        if self.phase is PatrolPhase.WALL_HOME:
            return self.route_ids[0]
        if self.phase in {PatrolPhase.OUTBOUND, PatrolPhase.RETURN}:
            return self.route_ids[self._index]
        return None

    def confirm(self, tag_id: int) -> PatrolPhase:
        if tag_id != self.expected_id:
            raise ValueError(
                f"expected tag {self.expected_id}, cannot confirm tag {tag_id}"
            )
        if self.phase is PatrolPhase.FLOOR_HOME:
            self.phase = PatrolPhase.WALL_HOME
            self._index = 0
        elif self.phase is PatrolPhase.WALL_HOME:
            self.phase = PatrolPhase.OUTBOUND
            self._index = 1
        elif self.phase is PatrolPhase.OUTBOUND:
            if self._index == len(self.route_ids) - 1:
                self.phase = PatrolPhase.TURNAROUND
            else:
                self._index += 1
        elif self.phase is PatrolPhase.RETURN:
            if self._index == 0:
                self.phase = PatrolPhase.FLOOR_ALIGN
            else:
                self._index -= 1
        elif self.phase is PatrolPhase.FLOOR_ALIGN:
            self.phase = PatrolPhase.COMPLETE
        return self.phase

    def begin_return(self) -> None:
        if self.phase is not PatrolPhase.TURNAROUND:
            raise ValueError("return can begin only after the final outbound tag")
        self.phase = PatrolPhase.RETURN
        self._index = len(self.route_ids) - 2

    def resume_outbound(self, expected_tag_id: int) -> None:
        """Resume an airborne run at one visible wall tag."""
        index = self.route_ids.index(expected_tag_id)
        self._index = index
        self.phase = PatrolPhase.WALL_HOME if index == 0 else PatrolPhase.OUTBOUND

    def abort(self) -> None:
        self.phase = PatrolPhase.ABORTED


class ExpectedTagTracker:
    """Confirm one expected tag only after a continuous centered hold."""

    def __init__(
        self,
        expected_id: int,
        hold_s: float,
        target_x: float | None,
        tolerance_px: float | None,
    ) -> None:
        self.expected_id = expected_id
        self.hold_s = hold_s
        self.target_x = target_x
        self.tolerance_px = tolerance_px
        self._started_s: float | None = None
        self._last_frame_key = None

    def update(
        self, detections: Iterable[TagDetection], now_s: float, *, frame_key=None
    ) -> TagDetection | None:
        choices = [
            item
            for item in detections
            if item.tag_id == self.expected_id and item.center_px is not None
        ]
        if not choices:
            self._started_s = None
            self._last_frame_key = None
            return None
        if frame_key is not None:
            if frame_key == self._last_frame_key:
                return None
            if self._last_frame_key is not None and frame_key[0] != self._last_frame_key[0]:
                self._started_s = None
            self._last_frame_key = frame_key
        best = min(choices, key=lambda item: abs(item.pose_error))
        if (
            self.target_x is not None
            and self.tolerance_px is not None
            and abs(best.center_px[0] - self.target_x) > self.tolerance_px
        ):
            self._started_s = None
            return None
        if self.hold_s <= 0.0:
            return best
        if self._started_s is None:
            self._started_s = now_s
            return None
        if now_s - self._started_s >= self.hold_s:
            return best
        return None


@dataclass
class OrderedTagGate:
    """Lock onto this leg's expected ID regardless of other visible tags.

    Field/action names retained for log compatibility. 'expected_only' means
    only the expected ID is used, not that it is alone in the camera image.
    """

    departure_tag_id: int | None
    expected_tag_id: int
    expected_only_hold_s: float

    def __post_init__(self) -> None:
        self._pair_seen = False
        self._expected_only_since_s: float | None = None
        self._center_ready = False
        self._last_frame_key = None

    def update(self, visible_ids: set[int], now_s: float, *, frame_key=None) -> TagGateAction:
        expected_visible = self.expected_tag_id in visible_ids
        if not expected_visible:
            self._expected_only_since_s = None
            self._center_ready = False
            self._last_frame_key = None
            return TagGateAction.SEEK_NEXT
        if frame_key is not None:
            if self._last_frame_key == frame_key:
                return TagGateAction.CENTER_EXPECTED if self._center_ready else TagGateAction.HOLD_EXPECTED_ONLY
            if self._last_frame_key is not None and frame_key[0] != self._last_frame_key[0]:
                self._expected_only_since_s = None
                self._center_ready = False
            self._last_frame_key = frame_key
        if self._center_ready:
            return TagGateAction.CENTER_EXPECTED
        if self._expected_only_since_s is None:
            self._expected_only_since_s = now_s
            return TagGateAction.HOLD_EXPECTED_ONLY
        if (
            now_s - self._expected_only_since_s + 1e-9
            < self.expected_only_hold_s
        ):
            return TagGateAction.HOLD_EXPECTED_ONLY
        self._center_ready = True
        return TagGateAction.CENTER_EXPECTED


@dataclass
class VisualProgressTracker:
    """Measure lateral travel from the previous wall tag's image motion.

    When the aircraft moves left, a fixed wall tag moves right in the image;
    the signs reverse on the return leg.  This gives the patrol a real visual
    progress signal even before the next expected tag enters the frame.
    """

    reference_tag_id: int | None
    direction: str
    stall_window_s: float
    min_progress_px: float
    frame_width_px: float
    exit_margin_px: float = 80.0

    def __post_init__(self) -> None:
        if self.direction not in {"left", "right"}:
            raise ValueError("direction must be left or right")
        self._anchor_x: float | None = None
        self._last_x: float | None = None
        self._last_progress_s: float | None = None
        self._retired = self.reference_tag_id is None

    @property
    def last_x(self) -> float | None:
        return self._last_x

    @property
    def retired(self) -> bool:
        return self._retired

    def update(
        self, detections: Iterable[TagDetection], now_s: float
    ) -> bool:
        if self._retired or self.reference_tag_id is None:
            return False
        choices = [
            item
            for item in detections
            if item.tag_id == self.reference_tag_id and item.center_px is not None
        ]
        if not choices:
            return False
        observed = min(choices, key=lambda item: abs(item.pose_error))
        x = observed.center_px[0]
        self._last_x = x
        if self._anchor_x is None:
            self._anchor_x = x
            self._last_progress_s = now_s
            return False

        signed_progress = (
            x - self._anchor_x
            if self.direction == "left"
            else self._anchor_x - x
        )
        progressed = signed_progress >= self.min_progress_px
        if progressed:
            self._anchor_x = x
            self._last_progress_s = now_s

        reached_exit = (
            self.direction == "left"
            and x >= self.frame_width_px - self.exit_margin_px
        ) or (self.direction == "right" and x <= self.exit_margin_px)
        if reached_exit:
            # Disappearing after reaching the expected edge is itself evidence
            # of travel.  From here the next tag is the progress reference.
            self._retired = True
            self._last_progress_s = now_s
            return True
        return progressed

    def stalled(self, now_s: float) -> bool:
        return (
            not self._retired
            and self._last_progress_s is not None
            and now_s - self._last_progress_s >= self.stall_window_s
        )

    def restart_window(self, now_s: float) -> None:
        if self._last_x is not None:
            self._anchor_x = self._last_x
        self._last_progress_s = now_s


@dataclass
class _DetectionLogger:
    client: NDJSONClient
    period_s: float = 0.5
    photo_root: Path | None = None

    def __post_init__(self) -> None:
        self._last_s: dict[int, float] = {}
        self._photo_index = 0
        self.capture = None
        self.recorder = None
        if self.photo_root is None:
            session = datetime.now().strftime("%Y%m%dT%H%M%S")
            self.photo_root = (
                Path(__file__).resolve().parents[1] / "captures" / session
            )
        self.photo_root.mkdir(parents=True, exist_ok=True)
        self.client.log_event(
            "tag_photo_directory", {"path": str(self.photo_root)}
        )

    def attach_capture(self, capture):
        self.capture = capture
        self.recorder = FrameRecorder(self.photo_root / "observations", capture)

    def close(self):
        if self.recorder is not None:
            self.recorder.close()

    @staticmethod
    def payload(
        detection: TagDetection,
        *,
        phase: PatrolPhase,
        expected_id: int | None,
        frame_age_s: float,
        telemetry: Telemetry | None,
        direction: str | None,
    ) -> dict[str, object]:
        tx, ty, tz = translation(detection.T_C_T)
        return {
            "tag_id": detection.tag_id,
            "phase": phase.value,
            "expected_id": expected_id,
            "center_px": list(detection.center_px) if detection.center_px else None,
            "camera_translation_m": {"x": tx, "y": ty, "z": tz},
            "range_m": math.sqrt(tx * tx + ty * ty + tz * tz),
            "pose_error": detection.pose_error,
            "frame_age_s": frame_age_s,
            "height_m": None if telemetry is None else telemetry.height_m,
            "direction": direction,
        }

    def observations(
        self,
        detections: Iterable[TagDetection],
        now_s: float,
        *,
        phase: PatrolPhase,
        expected_id: int | None,
        frame_age_s: float,
        telemetry: Telemetry | None,
        direction: str | None,
    ) -> None:
        if self.recorder is not None:
            self.recorder.update_context({
                "pc_observed_monotonic_s": now_s,
                "phase": phase.value, "expected_id": expected_id,
                "direction": direction,
                "visible_ids": [item.tag_id for item in detections],
                "detection_frame_key": _frame_key(self.capture),
                "telemetry": asdict(telemetry) if telemetry is not None else None,
                "motion": asdict(self.client.last_motion_assessment) if self.client.last_motion_assessment else None,
            })
        for detection in detections:
            if now_s - self._last_s.get(detection.tag_id, float("-inf")) < self.period_s:
                continue
            self._last_s[detection.tag_id] = now_s
            self.client.log_event(
                "tag_detected",
                self.payload(
                    detection,
                    phase=phase,
                    expected_id=expected_id,
                    frame_age_s=frame_age_s,
                    telemetry=telemetry,
                    direction=direction,
                ),
            )

    def save_confirmation_photo(
        self,
        capture: TcpVideoStream,
        detection: TagDetection,
        *,
        phase: PatrolPhase,
    ) -> Path | None:
        """Save the fresh camera image associated with a confirmed visit.

        Photo failure is logged but never changes flight control.  The raw
        camera frame is kept unannotated so it remains useful for later pose
        and calibration checks.
        """
        try:
            snapshot = capture.last_detection_snapshot
            frame = None if snapshot is None else snapshot.frame
            frame_age_s = float("inf") if snapshot is None else time.monotonic() - snapshot.received_s
            ok = snapshot is not None
            if not ok or frame is None or frame_age_s > FRESH_FRAME_S:
                raise RuntimeError(f"camera frame is stale ({frame_age_s:.3f}s)")
            self._photo_index += 1
            captured_at = datetime.now().strftime("%Y%m%dT%H%M%S_%f")[:-3]
            path = self.photo_root / (
                f"{self._photo_index:02d}_{phase.value}_ID{detection.tag_id}_"
                f"{captured_at}.jpg"
            )
            write_image(path, frame)
            payload = {
                "tag_id": detection.tag_id,
                "phase": phase.value,
                "path": str(path),
                "frame_age_s": frame_age_s,
                "frame_key": snapshot.key,
                "center_px": (
                    list(detection.center_px) if detection.center_px else None
                ),
            }
            self.client.log_event("tag_photo_saved", payload)
            print(f"ID {detection.tag_id} photo saved: {path}")
            return path
        except Exception as error:
            self.client.log_event(
                "tag_photo_failed",
                {
                    "tag_id": detection.tag_id,
                    "phase": phase.value,
                    "error": repr(error),
                },
            )
            print(f"ID {detection.tag_id} photo failed: {error}")
            return None


def _fresh_detections(
    capture: TcpVideoStream, detector: AprilTagDetector
) -> tuple[list[TagDetection], float]:
    return capture.detect_latest(detector, FRESH_FRAME_S)


def _frame_key(capture):
    snapshot = None if capture is None else capture.last_detection_snapshot
    return None if snapshot is None else snapshot.key


def _check_control(telemetry: Telemetry | None) -> None:
    if (
        telemetry is not None
        and telemetry.rc_override_age_s is not None
        and telemetry.rc_override_age_s < 5.0
    ):
        raise InterruptedError("physical RC-N2 stick override detected")
    if telemetry is not None and telemetry.vs_authority not in (None, "MSDK"):
        raise InterruptedError(
            f"flight-control authority moved to {telemetry.vs_authority}"
        )


def _confirm_advanced_authority(
    client: NDJSONClient, limiter: RateLimiter, timeout_s: float = 3.0
) -> None:
    deadline = time.monotonic() + timeout_s
    telemetry = client.last_telemetry
    while time.monotonic() < deadline:
        if (
            telemetry is not None
            and telemetry.vs_enabled is True
            and telemetry.vs_advanced_enabled is True
            and telemetry.vs_authority == "MSDK"
        ):
            return
        limiter.wait()
        client.zero()
        telemetry = client.last_telemetry
    raise RuntimeError(
        "Virtual Stick Advanced authority was not confirmed "
        f"(enabled={None if telemetry is None else telemetry.vs_enabled}, "
        f"advanced={None if telemetry is None else telemetry.vs_advanced_enabled}, "
        f"authority={None if telemetry is None else telemetry.vs_authority})"
    )


def _acquire_tag(
    client: NDJSONClient,
    limiter: RateLimiter,
    capture: TcpVideoStream,
    detector: AprilTagDetector,
    logger: _DetectionLogger,
    route: PatrolRouteTracker,
    patrol: PatrolConfig,
    *,
    target_x: float | None = None,
    tolerance_px: float | None = None,
    direction: str | None = None,
) -> TagDetection:
    expected = route.expected_id
    if expected is None:
        raise RuntimeError(f"phase {route.phase.value} has no expected tag")
    tv_capture = uses_tv_framing(expected, patrol) and route.phase is PatrolPhase.OUTBOUND
    tracker = ExpectedTagTracker(
        expected,
        patrol.tag_confirm_s,
        None if tv_capture else target_x,
        None if tv_capture else tolerance_px,
    )
    deadline = time.monotonic() + patrol.acquire_timeout_s
    while time.monotonic() < deadline:
        limiter.wait()
        client.zero()
        telemetry = client.last_telemetry
        _check_control(telemetry)
        now = time.monotonic()
        detections, frame_age = _fresh_detections(capture, detector)
        logger.observations(
            detections,
            now,
            phase=route.phase,
            expected_id=expected,
            frame_age_s=frame_age,
            telemetry=telemetry,
            direction=direction,
        )
        if tv_capture:
            snapshot = capture.last_detection_snapshot
            expected_tag = next((tag for tag in detections if tag.tag_id == expected), None)
            if (expected_tag is not None and snapshot is not None
                    and tag_view_action(expected_tag, snapshot.frame.shape,
                                        direction or patrol.outbound_direction, patrol) is not WallViewAction.INSIDE):
                _align_tv_composition(client, limiter, capture, detector, expected, patrol, deadline=deadline)
                tracker = ExpectedTagTracker(expected, patrol.tag_confirm_s)
                continue
            detections = [
                tag for tag in detections
                if (snapshot is not None and math.isfinite(frame_age) and 0 <= frame_age <= FRESH_FRAME_S
                    and tag_view_action(tag, snapshot.frame.shape, direction or patrol.outbound_direction, patrol)
                    is WallViewAction.INSIDE)
            ]
        confirmed = tracker.update(detections, now, frame_key=_frame_key(capture))
        if confirmed is not None:
            payload = logger.payload(
                confirmed,
                phase=route.phase,
                expected_id=expected,
                frame_age_s=frame_age,
                telemetry=telemetry,
                direction=direction,
            )
            client.log_event("tag_visit_confirmed", payload)
            logger.save_confirmation_photo(
                capture, confirmed, phase=route.phase
            )
            print(
                f"ID {expected} confirmed in {route.phase.value}: "
                f"center=({confirmed.center_px[0]:.0f}, {confirmed.center_px[1]:.0f})"
            )
            route.confirm(expected)
            return confirmed
    raise RuntimeError(
        f"ID {expected} was not confirmed within {patrol.acquire_timeout_s:.1f}s"
    )


def _visual_floor_height_m(config: AppConfig, detection: TagDetection) -> float:
    # With the gimbal at -90 degrees, camera +Z points at the floor.  Include
    # the configured camera-below-body offset to report aircraft height.
    return abs(translation(detection.T_C_T)[2]) + abs(config.body_camera.z_m)


def _climb_over_id0(
    config: AppConfig,
    client: NDJSONClient,
    limiter: RateLimiter,
    capture: TcpVideoStream,
    detector: AprilTagDetector,
    logger: _DetectionLogger,
    route: PatrolRouteTracker,
) -> None:
    patrol = config.patrol
    assert patrol is not None
    deadline = time.monotonic() + max(config.flight.takeoff_timeout_s, 30.0)
    reached_since: float | None = None
    last_log_s = float("-inf")
    best_height_m: float | None = None
    last_climb_progress_s = time.monotonic()
    while time.monotonic() < deadline:
        limiter.wait()
        telemetry = client.last_telemetry
        _check_control(telemetry)
        now = time.monotonic()
        detections, frame_age = _fresh_detections(capture, detector)
        floor = min(
            (item for item in detections if item.tag_id == 0),
            key=lambda item: abs(item.pose_error),
            default=None,
        )
        logger.observations(
            detections,
            now,
            phase=PatrolPhase.FLOOR_HOME,
            expected_id=0,
            frame_age_s=frame_age,
            telemetry=telemetry,
            direction=None,
        )
        source = "ultrasonic"
        height = None if telemetry is None else telemetry.height_m
        if floor is not None:
            height = _visual_floor_height_m(config, floor)
            source = "ID0_pose"
        if height is not None:
            if best_height_m is None:
                best_height_m = height
                last_climb_progress_s = now
            elif height >= best_height_m + CLIMB_PROGRESS_EPSILON_M:
                best_height_m = height
                last_climb_progress_s = now
        if height is None:
            up_mps = 0.0
        else:
            altitude_error_m = patrol.cruise_altitude_m - height
            up_mps = max(
                -config.flight.max_vertical_speed_mps,
                min(
                    config.flight.max_vertical_speed_mps,
                    config.flight.altitude_gain
                    * altitude_error_m,
                ),
            )
            # Successful flights 20260904T145843/153454/154411 used
            # 0.16-0.20 m/s. The 0.12 m/s trial stalled at 1.55 m. Keep the
            # measured known-good 0.18 m/s until the 2 cm arrival band, then
            # zero immediately.
            if altitude_error_m > 0.02:
                up_mps = min(config.flight.max_vertical_speed_mps, max(0.18, up_mps))
        _send_test_setpoint(
            client,
            "advanced_angle",
            Velocity(0.0, 0.0, up_mps),
            patrol.speed_mps,
            patrol.angle_deg,
        )
        if now - last_log_s >= 0.5:
            client.log_event(
                "patrol_altitude_sample",
                {
                    "target_m": patrol.cruise_altitude_m,
                    "height_m": height,
                    "source": source,
                    "up_mps": up_mps,
                    "ultrasonic_height_m": (
                        telemetry.height_m if telemetry is not None else None
                    ),
                    "oa_upward_distance_mm": (
                        telemetry.oa_upward_distance_mm
                        if telemetry is not None
                        else None
                    ),
                    "oa_obstacle_data_age_s": (
                        telemetry.oa_obstacle_data_age_s
                        if telemetry is not None
                        else None
                    ),
                },
            )
            last_log_s = now
        # The prior 8 cm tolerance ended a requested +15 cm climb after only
        # 4-8 cm.  Hold within 2 cm so the measured climb actually completes.
        if height is not None and abs(height - patrol.cruise_altitude_m) <= 0.02:
            reached_since = now if reached_since is None else reached_since
            if now - reached_since >= 0.5:
                client.zero()
                print(
                    f"Cruise altitude reached: {height:.2f} m "
                    f"(target {patrol.cruise_altitude_m:.2f} m, source {source})"
                )
                return
        else:
            reached_since = None
        if (
            height is not None
            and height < patrol.cruise_altitude_m - 0.02
            and now - last_climb_progress_s >= CLIMB_STALL_CONTINUE_S
        ):
            # Vertical firmware suppression must not deadlock the horizontal
            # tag route. Preserve hover altitude and proceed to wall ID2;
            # the RC remains able to take over at all times.
            client.zero()
            client.log_event(
                "patrol_altitude_stalled_continue",
                {
                    "target_m": patrol.cruise_altitude_m,
                    "height_m": height,
                    "best_height_m": best_height_m,
                    "source": source,
                    "stalled_s": now - last_climb_progress_s,
                    "oa_upward_distance_mm": (
                        telemetry.oa_upward_distance_mm
                        if telemetry is not None
                        else None
                    ),
                },
            )
            print(
                f"Climb stalled at {height:.2f} m; continuing wall route "
                f"instead of blocking left movement"
            )
            return
    raise RuntimeError(
        f"cruise altitude {patrol.cruise_altitude_m:.2f} m was not reached"
    )


def _traverse_to_expected(
    client: NDJSONClient,
    limiter: RateLimiter,
    capture: TcpVideoStream,
    detector: AprilTagDetector,
    logger: _DetectionLogger,
    route: PatrolRouteTracker,
    patrol: PatrolConfig,
    *,
    direction: str,
    target_x: float,
    target_y: float,
    departure_tag_id: int | None = None,
) -> TagDetection:
    expected = route.expected_id
    if expected is None:
        raise RuntimeError(f"phase {route.phase.value} has no expected tag")
    started_s = time.monotonic()
    deadline = started_s + patrol.leg_timeout_s
    cruise_right = -patrol.speed_mps if direction == "left" else patrol.speed_mps
    right = cruise_right
    up = 0.0
    centering = False
    last_seen_s: float | None = None
    active_angle_deg = patrol.angle_deg
    max_recovery_reported = False
    previous_gate_action: TagGateAction | None = None
    gate = OrderedTagGate(departure_tag_id, expected, patrol.tag_confirm_s)
    progress = VisualProgressTracker(
        departure_tag_id,
        direction,
        patrol.stall_window_s,
        patrol.stall_min_progress_px,
        target_x * 2.0,
    )
    no_frame_since = None
    centered_since = None
    centered_frame_key = None
    # Do not pretend a 4-degree PC request reaches an Android 3-degree clamp.
    supported_max = (client.last_telemetry.max_tilt_angle_deg if client.last_telemetry else None) or 3.0
    recovery_max = min(patrol.recovery_max_angle_deg, supported_max)
    if uses_tv_framing(expected, patrol):
        recovery_max = min(recovery_max, patrol.tv_framing.approach_angle_deg)
    active_angle_deg = min(active_angle_deg, recovery_max)
    client.log_event("patrol_effective_limits", {"requested_max_deg": patrol.recovery_max_angle_deg, "applied_max_deg": recovery_max})
    print(f"Moving {direction}; accepting ID {expected} inside view {tag_view_bounds(expected, patrol)}")
    while time.monotonic() < deadline:
        limiter.wait()
        telemetry = client.last_telemetry
        _check_control(telemetry)
        now = time.monotonic()
        detections, frame_age = _fresh_detections(capture, detector)
        logger.observations(
            detections,
            now,
            phase=route.phase,
            expected_id=expected,
            frame_age_s=frame_age,
            telemetry=telemetry,
            direction=direction,
        )
        if not math.isfinite(frame_age) or frame_age > FRESH_FRAME_S:
            centered_since = None
            centered_frame_key = None
            client.zero()
            if no_frame_since is None:
                no_frame_since = now
                client.log_event("patrol_frame_unavailable", {"expected_id": expected, "video": capture.diagnostics()})
            if now - no_frame_since > 8.0:
                raise ConnectionError("video recovery did not produce a fresh frame within 8 seconds")
            continue
        if no_frame_since is not None:
            client.log_event("patrol_frame_recovered", {"expected_id": expected, "video": capture.diagnostics()})
            gate = OrderedTagGate(departure_tag_id, expected, patrol.tag_confirm_s)
            centering = False
            no_frame_since = None
        progressed = progress.update(detections, now)
        if progressed:
            max_recovery_reported = False
            client.log_event(
                "patrol_visual_progress",
                {
                    "phase": route.phase.value,
                    "expected_id": expected,
                    "reference_tag_id": departure_tag_id,
                    "direction": direction,
                    "reference_x_px": progress.last_x,
                    "active_angle_deg": active_angle_deg,
                },
            )
        visible_ids = {item.tag_id for item in detections}
        choices = [item for item in detections if item.tag_id == expected
                   and item.center_px is not None and math.isfinite(item.pose_error)]
        observed = min(choices, key=lambda item: abs(item.pose_error)) if choices else None
        if observed is not None and uses_tv_framing(expected, patrol):
            last_seen_s = now
        snapshot = capture.last_detection_snapshot
        frame_shape = () if snapshot is None else snapshot.frame.shape
        view_action = None if observed is None else tag_view_action(
            observed, frame_shape, direction, patrol)
        if uses_tv_framing(expected, patrol) and observed is not None and view_action is not WallViewAction.INSIDE:
            _align_tv_composition(client, limiter, capture, detector, expected, patrol, deadline=deadline)
            gate = OrderedTagGate(departure_tag_id, expected, patrol.tag_confirm_s)
            centering = False
            previous_gate_action = None
            continue
        # Start the distinct-frame hold only inside the broad capture region.
        # Other tags never block or reset acceptance of the expected ID.
        eligible_ids = visible_ids if view_action is WallViewAction.INSIDE else visible_ids - {expected}
        gate_action = gate.update(eligible_ids, now, frame_key=_frame_key(capture))
        if gate_action is not previous_gate_action:
            client.log_event(
                "patrol_tag_gate",
                {
                    "phase": route.phase.value,
                    "departure_tag_id": departure_tag_id,
                    "expected_id": expected,
                    "visible_ids": sorted(visible_ids),
                    "direction": direction,
                    "action": gate_action.value,
                    "framing_action": None if view_action is None else view_action.value,
                    "framing_mode": "tv_left_reference" if uses_tv_framing(expected, patrol) else "broad_view",
                    "view_bounds_fraction": tag_view_bounds(expected, patrol),
                },
            )
            if (
                gate_action is TagGateAction.CENTER_EXPECTED
                and previous_gate_action is TagGateAction.HOLD_EXPECTED_ONLY
            ):
                print(f"ID {expected} verified inside capture region (other IDs ignored)")
            previous_gate_action = gate_action
        if observed is not None and view_action is not WallViewAction.INSIDE:
            centered_since = None
            up = 0.0
            if view_action is not WallViewAction.APPROACH:
                client.zero()
                client.log_event("wall_framing_stopped", {
                    "expected_id":expected,"visible_ids":sorted(visible_ids),
                    "reason":view_action.value,"center_px":list(observed.center_px),
                    "view_bounds_fraction":tag_view_bounds(expected, patrol)})
                raise RuntimeError(f"ID {expected}: {view_action.value}; no reverse/vertical chasing")
            # Only approach the near edge in the already-authorized direction.
            centering = True
            last_seen_s = now
            right = cruise_right * 0.35
        elif gate_action is TagGateAction.HOLD_EXPECTED_ONLY:
            # Debounce the expected ID across distinct decoded frames.
            # Other visible IDs do not reset this hold.
            centering = False
            centered_since = None
            right = 0.0
            up = 0.0
        elif observed is not None and gate_action is TagGateAction.CENTER_EXPECTED:
            # CENTER_EXPECTED is a legacy enum name: this now means an
            # accepted broad-view hold, not optical-centre alignment.
            client.zero()
            payload = logger.payload(observed, phase=route.phase,
                expected_id=expected, frame_age_s=frame_age,
                telemetry=telemetry, direction=direction)
            payload["framing_mode"] = "tv_left_reference" if uses_tv_framing(expected, patrol) else "broad_view"
            payload["view_bounds_fraction"] = tag_view_bounds(expected, patrol)
            payload["frame_size_px"] = [frame_shape[1],frame_shape[0]]
            payload["tv_visibility_verified"] = False
            client.log_event("tag_view_confirmed", payload)
            observed = _pause_for_tag_photo(client, limiter, capture, detector,
                logger, route.phase, expected, direction, patrol)
            # Visit is complete only after the pause and a current-frame photo.
            payload.update(logger.payload(observed, phase=route.phase,
                expected_id=expected, frame_age_s=max(0.0,
                    time.monotonic()-capture.last_detection_snapshot.received_s),
                telemetry=client.last_telemetry, direction=direction))
            payload["visit_pause_s"] = patrol.visit_pause_s
            client.log_event("tag_visit_confirmed", payload)
            print(f"ID {expected} pause and photo complete: xy={observed.center_px}")
            route.confirm(expected)
            return observed
        elif uses_tv_framing(expected, patrol) and last_seen_s is not None:
            client.zero()
            raise RuntimeError(f"ID {expected} lost after TV framing; no blind correction")
        elif centering:
            centered_since = None
            # Detector gaps are usually motion blur. Hovering restores a sharp
            # frame instead of blindly flying past the tag.  If it does not
            # reappear, resume the route instead of hovering until timeout.
            missing_s = float("inf") if last_seen_s is None else now - last_seen_s
            if missing_s <= 0.4:
                right = right
            elif missing_s <= 1.2:
                right = 0.0
            else:
                centering = False
                right = cruise_right
                active_angle_deg = min(patrol.angle_deg, recovery_max)
                client.log_event(
                    "tag_centering_lost_resume",
                    {
                        "phase": route.phase.value,
                        "expected_id": expected,
                        "direction": direction,
                        "missing_s": missing_s,
                    },
                )
                print(
                    f"ID {expected} was lost during centering; "
                    f"resuming {direction} search"
                )
            up = 0.0
        else:
            centered_since = None
            right = cruise_right
            up = 0.0

        # Keep stall recovery active after the expected tag enters the frame.
        # In the real corridor ID1 and ID3 are visible together at the exact
        # location where the Mini 4 Pro stops responding to 1.5 degrees.  The
        # previous tag remains a valid image-motion reference while ID3 is
        # being centred, so disabling recovery here recreated the same stall.
        if (
            gate_action is not TagGateAction.HOLD_EXPECTED_ONLY
            and progress.stalled(now)
        ):
            previous_angle = active_angle_deg
            active_angle_deg = min(
                recovery_max,
                active_angle_deg + patrol.recovery_angle_step_deg,
            )
            event = {
                "phase": route.phase.value,
                "expected_id": expected,
                "reference_tag_id": departure_tag_id,
                "direction": direction,
                "reference_x_px": progress.last_x,
                "previous_angle_deg": previous_angle,
                "active_angle_deg": active_angle_deg,
                "stall_window_s": patrol.stall_window_s,
            }
            if active_angle_deg > previous_angle:
                client.log_event("patrol_stall_recovery", event)
                print(
                    f"No visual {direction} progress for "
                    f"{patrol.stall_window_s:.1f}s; increasing tilt "
                    f"{previous_angle:.1f} -> {active_angle_deg:.1f} deg"
                )
                _pause_zero(client, limiter, patrol.recovery_pause_s)
            elif not max_recovery_reported:
                client.log_event("patrol_stall_at_max_angle", event)
                print(
                    f"No visual {direction} progress at maximum "
                    f"{active_angle_deg:.1f} deg; continuing to seek ID {expected}"
                )
                max_recovery_reported = True
            progress.restart_window(time.monotonic())

        command_direction = "left" if right < 0.0 else "right" if right > 0.0 else None
        if command_direction is not None:
            obstacle_m = _directional_obstacle_m(telemetry, command_direction)
            if obstacle_m is not None and obstacle_m <= patrol.obstacle_stop_m:
                client.zero()
                raise RuntimeError(
                    f"direct {command_direction} obstacle {obstacle_m:.2f} m <= "
                    f"stop threshold {patrol.obstacle_stop_m:.2f} m"
                )
        _send_test_setpoint(
            client,
            "advanced_angle",
            Velocity(0.0, right, up),
            patrol.speed_mps,
            active_angle_deg,
        )
    client.zero()
    raise RuntimeError(
        f"timed out after {patrol.leg_timeout_s:.0f}s moving {direction}: "
        f"ID {expected} was not accepted inside view (departure ID {departure_tag_id}, "
        f"last reference x={progress.last_x}, max tilt={active_angle_deg:.1f} deg)"
    )


def _align_tv_composition(client, limiter, capture, detector, expected_id, patrol, *, deadline):
    """Stop/observe/lateral pulse; never change height, depth, yaw, or arm state."""
    tv = patrol.tv_framing
    if not uses_tv_framing(expected_id, patrol):
        raise ValueError("TV correction is only available for configured scenario destinations")
    deadline = min(deadline, time.monotonic() + tv.max_correction_s)
    prior_time, prior_speed = None, 0.0
    travel = 0.0
    generation, last_frame, initial_yaw = None, None, None
    next_adjust_at = 0.0
    client.log_event("tv_framing_correction_started", {"tag_id": expected_id,
        "max_tilt_deg": tv.approach_angle_deg, "axes": "horizontal_only", "max_up_mps": 0.0})
    while time.monotonic() < deadline:
        limiter.wait()
        client.zero()
        t = client.last_telemetry
        _check_control(t)
        observed_at = time.monotonic()
        tags, age = _fresh_detections(capture, detector)
        now = time.monotonic()
        if now >= deadline:
            break
        snapshot = capture.last_detection_snapshot
        tag = min((tag for tag in tags if tag.tag_id == expected_id and math.isfinite(tag.pose_error)),
                  key=lambda tag: abs(tag.pose_error), default=None)
        if (snapshot is None or tag is None or not math.isfinite(age) or not 0 <= age <= FRESH_FRAME_S):
            raise RuntimeError(f"ID {expected_id}: fresh tag evidence lost during TV correction")
        if generation is not None and snapshot.generation != generation:
            raise RuntimeError("Camera generation changed during TV correction")
        generation = snapshot.generation
        if (t is None or t.armed is not True or t.is_flying is not True
                or t.vs_enabled is not True or t.vs_advanced_enabled is not True or t.vs_authority != "MSDK"):
            raise InterruptedError("Airborne/Virtual Stick authority required for TV correction")
        values = (t.height_m, t.yaw_deg, t.velocity_north_mps, t.velocity_east_mps, t.velocity_down_mps,
                  t.height_age_s, t.attitude_age_s, t.velocity_age_s, t.is_flying_age_s, t.battery_percent)
        if any(value is None or not math.isfinite(value) for value in values):
            raise RuntimeError("Finite fresh height, attitude and velocity required for TV correction")
        if any(not 0 <= value + now - observed_at <= .5
               for value in (t.height_age_s, t.attitude_age_s, t.velocity_age_s, t.is_flying_age_s)):
            raise RuntimeError("Telemetry expired during TV correction")
        if t.battery_percent < 30:
            raise RuntimeError("Battery below 30% during TV correction")
        if initial_yaw is None:
            initial_yaw = t.yaw_deg
        if abs(_wrap_degrees(t.yaw_deg - initial_yaw)) > 5:
            raise RuntimeError("Heading changed during TV correction")
        speed = math.hypot(t.velocity_north_mps, t.velocity_east_mps, t.velocity_down_mps)
        if prior_time is not None:
            travel += max(prior_speed, speed) * (now - prior_time)
        prior_time, prior_speed = now, speed
        if travel >= tv.max_correction_distance_m:
            raise RuntimeError("TV correction travel budget exhausted")
        window = tv_frame_window(tag, snapshot.frame.shape, patrol)
        if window is None or not window.feasible:
            raise RuntimeError("TV reference cannot fit; no automatic distance correction")
        if not window.y_min <= window.y <= window.y_max:
            raise RuntimeError("TV vertical framing is outside view; height correction is disabled")
        if window.inside:
            client.log_event("tv_framing_correction_complete", {"tag_id": expected_id,
                "travel_m": travel, "tv_visibility_verified": False})
            return tag
        if snapshot.key == last_frame:
            continue
        last_frame = snapshot.key
        if now < next_adjust_at or speed > .15:
            continue
        right, _ = tv_frame_correction(window, patrol)
        low = max(1.0, patrol.cruise_altitude_m - tv.max_height_offset_m)
        high = min(1.6, patrol.cruise_altitude_m + tv.max_height_offset_m)
        if not low <= t.height_m <= high:
            raise RuntimeError("TV correction height outside configured envelope")
        direction = "right" if right > 0 else "left" if right < 0 else None
        if direction:
            obstacle = _directional_obstacle_m(t, direction)
            if obstacle is not None and obstacle <= patrol.obstacle_stop_m:
                raise RuntimeError("Obstacle blocks TV framing correction")
        supported = t.max_tilt_angle_deg
        if supported is not None and (not math.isfinite(supported) or abs(right) > supported):
            raise RuntimeError("TV correction exceeds reported tilt support")
        client.attitude(0.0, right, 0.0, 0.0)
        next_adjust_at = now + tv.correction_interval_s
    client.zero()
    raise RuntimeError(f"ID {expected_id}: TV framing correction timed out; no repeated search")


def _pause_for_tag_photo(client, limiter, capture, detector, logger, phase,
                         expected_id, direction, patrol):
    """Zero -> measured low-speed dwell -> fresh full-frame capture.

    TV destinations may first correct toward their composition window.
    Timing alone is not evidence of a stationary aircraft.
    """
    started = time.monotonic()
    deadline = started + patrol.visit_pause_s + 4.0
    correction_deadline = deadline + (
        patrol.tv_framing.max_correction_s if uses_tv_framing(expected_id, patrol) else 0.0)
    stable_since = None
    last_frame_key = None
    last_reason = "waiting for fresh velocity and expected tag"
    client.log_event("tag_pause_started", {"tag_id":expected_id,"phase":phase.value,
        "duration_s":patrol.visit_pause_s,"tv_visibility_verified":False})
    print(f"ID {expected_id}: pausing {patrol.visit_pause_s:.1f}s for full-frame photo")
    while time.monotonic() < deadline:
        limiter.wait()
        client.zero()
        t = client.last_telemetry
        _check_control(t)
        if (t is None or t.armed is not True or t.vs_authority != "MSDK"
                or t.vs_enabled is not True or t.vs_advanced_enabled is not True
                or t.is_flying is not True):
            raise InterruptedError("control/airborne state lost during photo pause")
        if t.battery_percent is None or t.battery_percent < 30:
            raise RuntimeError("battery below30%/unknown during photo pause")
        tags, frame_age = _fresh_detections(capture, detector)
        now = time.monotonic()
        logger.observations(tags, now, phase=phase, expected_id=expected_id,
            frame_age_s=frame_age, telemetry=t, direction=direction)
        tag = min((x for x in tags if x.tag_id==expected_id and x.center_px
                   and math.isfinite(x.pose_error)),key=lambda x:abs(x.pose_error),default=None)
        snapshot = capture.last_detection_snapshot
        fresh = math.isfinite(frame_age) and 0 <= frame_age <= FRESH_FRAME_S
        inside = fresh and tag is not None and snapshot is not None and tag_view_action(
            tag,snapshot.frame.shape,direction,patrol) is WallViewAction.INSIDE
        if fresh and tag is not None and snapshot is not None and not inside and uses_tv_framing(expected_id, patrol):
            _align_tv_composition(client, limiter, capture, detector, expected_id, patrol, deadline=correction_deadline)
            deadline = min(correction_deadline, time.monotonic() + patrol.visit_pause_s + 4.0)
            stable_since, last_frame_key = None, None
            continue
        values = (t.velocity_north_mps,t.velocity_east_mps,t.velocity_down_mps,t.velocity_age_s)
        velocity_valid = all(v is not None and math.isfinite(v) for v in values)
        speed = math.hypot(values[0],values[1]) if velocity_valid else None
        slow = (velocity_valid and 0 <= t.velocity_age_s <= .5 and speed <= .15
                and abs(t.velocity_down_mps) <= .15)
        if not inside or not slow:
            stable_since = None
            last_frame_key = None
            last_reason = "expected tag outside fresh capture view" if not inside else "velocity not fresh/low"
            continue
        key = snapshot.key
        if key == last_frame_key:
            continue
        if last_frame_key is not None and last_frame_key[0] != key[0]:
            stable_since = None
        last_frame_key = key
        stable_since = now if stable_since is None else stable_since
        if now-started < patrol.visit_pause_s or now-stable_since < .5:
            continue
        photo = logger.save_confirmation_photo(capture,tag,phase=phase)
        if photo is None:
            raise RuntimeError(f"ID {expected_id} pause finished but photo write failed")
        client.log_event("tag_pause_complete", {"tag_id":expected_id,"phase":phase.value,
            "elapsed_s":now-started,"horizontal_speed_mps":speed,
            "vertical_speed_mps":t.velocity_down_mps,"frame_age_s":frame_age,
            "frame_key":key,"photo_path":str(photo),"tv_visibility_verified":False})
        return tag
    client.zero()
    raise RuntimeError(f"ID {expected_id} photo pause incomplete: {last_reason}; no next waypoint")


def _pause_zero(
    client: NDJSONClient, limiter: RateLimiter, seconds: float
) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        limiter.wait()
        client.zero()
        _check_control(client.last_telemetry)


def _wrap_degrees(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


def _align_cruise_yaw(
    client: NDJSONClient,
    limiter: RateLimiter,
    target_yaw_deg: float,
) -> None:
    """Reproduce the successful corridor heading without using GPS position."""

    deadline = time.monotonic() + YAW_ALIGN_TIMEOUT_S
    centered_since: float | None = None
    last_yaw: float | None = None
    while time.monotonic() < deadline:
        limiter.wait()
        telemetry = client.last_telemetry
        _check_control(telemetry)
        if telemetry is None or telemetry.yaw_deg is None:
            client.zero()
            continue
        last_yaw = telemetry.yaw_deg
        error_deg = _wrap_degrees(target_yaw_deg - telemetry.yaw_deg)
        now = time.monotonic()
        if abs(error_deg) <= YAW_ALIGN_TOLERANCE_DEG:
            centered_since = now if centered_since is None else centered_since
            client.zero()
            if now - centered_since >= YAW_ALIGN_HOLD_S:
                client.log_event(
                    "cruise_yaw_aligned",
                    {
                        "target_yaw_deg": target_yaw_deg,
                        "actual_yaw_deg": telemetry.yaw_deg,
                        "error_deg": error_deg,
                    },
                )
                print(
                    f"Cruise yaw aligned: {telemetry.yaw_deg:.1f} deg "
                    f"(target {target_yaw_deg:.1f})"
                )
                return
            continue
        centered_since = None
        yaw_rate_dps = max(
            -YAW_ALIGN_MAX_RATE_DPS,
            min(YAW_ALIGN_MAX_RATE_DPS, error_deg * 1.5),
        )
        client.attitude(0.0, 0.0, 0.0, math.radians(yaw_rate_dps))
    client.zero()
    raise RuntimeError(
        "cruise yaw alignment timed out "
        f"(target={target_yaw_deg:.1f}, last={last_yaw!r})"
    )


def _align_floor_id0(
    config: AppConfig,
    client: NDJSONClient,
    limiter: RateLimiter,
    capture: TcpVideoStream,
    detector: AprilTagDetector,
    logger: _DetectionLogger,
    route: PatrolRouteTracker,
    target_center: tuple[float, float],
) -> TagDetection:
    patrol = config.patrol
    assert patrol is not None
    deadline = time.monotonic() + patrol.landing_timeout_s
    centered_since: float | None = None
    last_seen: TagDetection | None = None
    last_frame_key = None
    while time.monotonic() < deadline:
        limiter.wait()
        telemetry = client.last_telemetry
        _check_control(telemetry)
        now = time.monotonic()
        detections, frame_age = _fresh_detections(capture, detector)
        floor = min(
            (item for item in detections if item.tag_id == 0 and item.center_px),
            key=lambda item: abs(item.pose_error),
            default=None,
        )
        logger.observations(
            detections,
            now,
            phase=route.phase,
            expected_id=0,
            frame_age_s=frame_age,
            telemetry=telemetry,
            direction=None,
        )
        if floor is None:
            centered_since = None
            last_frame_key = None
            client.zero()
            continue
        frame_key = _frame_key(capture)
        if frame_key == last_frame_key:
            client.zero()
            continue
        if last_frame_key is not None and frame_key[0] != last_frame_key[0]:
            centered_since = None
        last_frame_key = frame_key
        last_seen = floor
        error_x = floor.center_px[0] - target_center[0]
        error_y = floor.center_px[1] - target_center[1]
        error_px = math.hypot(error_x, error_y)
        if error_px <= patrol.landing_center_tolerance_px:
            centered_since = now if centered_since is None else centered_since
            client.zero()
            if now - centered_since >= patrol.landing_confirm_s:
                client.log_event(
                    "landing_alignment_confirmed",
                    {
                        **logger.payload(
                            floor,
                            phase=route.phase,
                            expected_id=0,
                            frame_age_s=frame_age,
                            telemetry=telemetry,
                            direction=None,
                        ),
                        "departure_center_px": list(target_center),
                        "error_px": error_px,
                    },
                )
                logger.save_confirmation_photo(
                    capture, floor, phase=route.phase
                )
                route.confirm(0)
                print(f"ID 0 landing alignment confirmed: error={error_px:.1f}px")
                return floor
            continue
        centered_since = None
        forward, right = _floor_alignment_velocity(error_x, error_y, patrol)
        _send_test_setpoint(
            client,
            "advanced_angle",
            Velocity(forward, right, 0.0),
            patrol.speed_mps,
            patrol.angle_deg,
        )
    client.zero()
    detail = "never detected" if last_seen is None else f"last at {last_seen.center_px}"
    raise RuntimeError(f"ID 0 landing alignment timed out ({detail})")


def _floor_alignment_velocity(
    error_x_px: float,
    error_y_px: float,
    patrol: PatrolConfig,
) -> tuple[float, float]:
    """Map downward-camera ID0 image error to body-frame velocity.

    Live flight evidence from 2026-09-04 showed that positive body-forward
    motion moves the floor tag down in the image.  Image Y therefore has the
    opposite sign from body-forward: a tag above the saved departure point
    requires positive forward motion.  Image X and body-right retain the same
    sign for the current calibrated camera/gimbal orientation.
    """

    forward = max(
        -patrol.landing_max_speed_mps,
        min(patrol.landing_max_speed_mps, -patrol.landing_gain * error_y_px),
    )
    right = max(
        -patrol.landing_max_speed_mps,
        min(patrol.landing_max_speed_mps, patrol.landing_gain * error_x_px),
    )
    magnitude = math.hypot(forward, right)
    if magnitude > patrol.landing_max_speed_mps:
        scale = patrol.landing_max_speed_mps / magnitude
        forward *= scale
        right *= scale
    return forward, right


def _wait_for_landing(
    client: NDJSONClient, limiter: RateLimiter, timeout_s: float = 30.0
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        limiter.wait()
        client.status(PatrolPhase.COMPLETE.value)
        telemetry = client.last_telemetry
        if telemetry is not None and telemetry.is_flying is False:
            return
    raise RuntimeError("DJI landing command did not report is_flying=false in time")


def run_tag_patrol(
    config: AppConfig,
    arm_token: str,
    *,
    auto_takeoff: bool,
    manual_takeoff_confirmed: bool,
    land_after_hover: bool,
    resume_wall_id: int | None = None,
) -> None:
    """Run ID0 -> ID2 -> ... -> ID2 -> ID0 without GPS."""
    if config.patrol is None:
        raise RuntimeError("configuration has no patrol section")
    if not config.camera.calibrated:
        raise RuntimeError("tag patrol requires calibrated camera intrinsics")
    if arm_token != config.network.confirmation_token:
        raise PermissionError("CLI arm confirmation token does not match configuration")
    if not auto_takeoff and not manual_takeoff_confirmed:
        raise RuntimeError(
            "either --takeoff or --manual-takeoff-confirmed is required"
        )
    required = {0, *config.patrol.route_ids}
    missing = required - set(config.tag_map)
    if missing:
        raise RuntimeError(f"patrol configuration is missing tag IDs {sorted(missing)}")

    patrol = config.patrol
    route = PatrolRouteTracker(patrol.route_ids)
    client = NDJSONClient(
        config.network.host,
        config.network.port,
        config.network.protocol_version,
    )
    limiter = RateLimiter(config.network.rate_hz)
    outbound = patrol.outbound_direction
    inbound = _opposite_direction(outbound)

    client.connect()
    capture = None
    logger = None
    print(f"Control log: {client.log_path}")
    print(
        "Tag patrol: ID0 floor -> "
        + " -> ".join(str(tag_id) for tag_id in patrol.route_ids)
        + " -> "
        + " -> ".join(str(tag_id) for tag_id in reversed(patrol.route_ids[:-1]))
        + " -> ID0 floor"
    )
    print(
        f"Outbound {outbound}, return {inbound}, advanced-angle "
        f"{patrol.angle_deg:.1f} deg at {config.network.rate_hz:.0f} Hz. "
        "Any RC-N2 stick releases app control."
    )
    client.log_event(
        "patrol_started",
        {
            "route_ids": list(patrol.route_ids),
            "outbound_direction": outbound,
            "return_direction": inbound,
            "cruise_altitude_m": patrol.cruise_altitude_m,
            "speed_mps": patrol.speed_mps,
            "angle_deg": patrol.angle_deg,
            "leg_timeout_s": patrol.leg_timeout_s,
            "stall_window_s": patrol.stall_window_s,
            "stall_min_progress_px": patrol.stall_min_progress_px,
            "recovery_angle_step_deg": patrol.recovery_angle_step_deg,
            "recovery_max_angle_deg": patrol.recovery_max_angle_deg,
            "auto_takeoff": auto_takeoff,
            "auto_land": land_after_hover,
            "resume_wall_id": resume_wall_id,
        },
    )

    try:
        detector = AprilTagDetector(config)
        capture = TcpVideoStream(
            config.network.host,
            config.network.video_port,
            config.network.video_codec,
        )
        logger = _DetectionLogger(client)
        logger.attach_capture(capture)
        client.stick_mode("advanced_angle")
        # Apply global CLOSE plus the documented HORIZONTAL and UPWARD
        # directional controls. Mini 4 Pro may report a sub-switch as
        # UNSUPPORTED; that product limitation is accepted only with CLOSE.
        client.obstacle_avoidance_off()
        client.status("verify_directional_oa_off")
        telemetry = client.last_telemetry
        if not _horizontal_oa_ready(telemetry):
            client.log_event(
                "directional_obstacle_avoidance_not_disabled",
                {
                    "oa_type": telemetry.oa_type if telemetry else None,
                    "oa_horizontal_switch_support": (
                        telemetry.oa_horizontal_switch_support
                        if telemetry
                        else None
                    ),
                    "oa_horizontal_enabled": (
                        telemetry.oa_horizontal_enabled if telemetry else None
                    ),
                    "oa_upward_switch_support": (
                        telemetry.oa_upward_switch_support
                        if telemetry
                        else None
                    ),
                    "oa_upward_enabled": (
                        telemetry.oa_upward_enabled if telemetry else None
                    ),
                    "action": "abort_before_takeoff",
                },
            )
            raise RuntimeError(
                "Horizontal obstacle avoidance was not verifiably disabled "
                f"(type={telemetry.oa_type if telemetry else None!r}, "
                "switch_support="
                f"{telemetry.oa_horizontal_switch_support if telemetry else None!r}, "
                "horizontal="
                f"{telemetry.oa_horizontal_enabled if telemetry else None!r}, "
                "upward_support="
                f"{telemetry.oa_upward_switch_support if telemetry else None!r}, "
                "upward="
                f"{telemetry.oa_upward_enabled if telemetry else None!r})"
            )
        if telemetry.oa_horizontal_switch_support in {None, "UNSUPPORTED"}:
            print(
                "Obstacle avoidance: CLOSE verified; this Mini 4 Pro does not "
                "support every directional sub-switch. Firmware may still brake "
                "in confined space."
            )
        else:
            print(
                "Obstacle avoidance verified: CLOSE, "
                "horizontal=false, upward=false"
            )
        client.gimbal_down()
        print("Gimbal: -90 deg (floor ID0)")
        if auto_takeoff:
            _require_takeoff_battery(client, limiter)
            client.takeoff(arm_token)
            height = _wait_for_takeoff(client, limiter, config.flight.takeoff_timeout_s)
            print(f"Takeoff complete: ultrasonic height {height:.2f} m")
        _arm_when_ready(client, arm_token, limiter)
        _confirm_advanced_authority(client, limiter)

        if resume_wall_id is not None:
            route.resume_outbound(resume_wall_id)
            floor_center = (config.camera.cx, config.camera.cy)
            client.log_event(
                "patrol_resumed_at_wall_tag",
                {"expected_id": resume_wall_id, "floor_return_target_px": list(floor_center)},
            )
            print(
                f"Resuming airborne patrol at wall ID{resume_wall_id}; "
                "floor ID0 acquisition skipped"
            )
        else:
            floor_home = _acquire_tag(
                client,
                limiter,
                capture,
                detector,
                logger,
                route,
                patrol,
            )
            floor_center = floor_home.center_px
            assert floor_center is not None
            client.log_event(
                "floor_home_anchor_saved",
                {
                    **logger.payload(
                        floor_home,
                        phase=PatrolPhase.FLOOR_HOME,
                        expected_id=0,
                        frame_age_s=0.0,
                        telemetry=client.last_telemetry,
                        direction=None,
                    ),
                    "target_center_px": list(floor_center),
                },
            )
            _climb_over_id0(
                config,
                client,
                limiter,
                capture,
                detector,
                logger,
                route,
            )
        client.gimbal(0.0)
        print("Gimbal: 0 deg (wall tags)")
        _pause_zero(client, limiter, 1.0)
        # A historical -95deg heading is not a wall-normal measurement.
        # Preserve the operator's departure orientation unless alignment was
        # explicitly configured for a verified setup.
        if patrol.align_cruise_yaw:
            _align_cruise_yaw(client, limiter, patrol.cruise_yaw_deg)
        else:
            client.log_event("cruise_yaw_preserved", {
                "actual_yaw_deg":None if client.last_telemetry is None else client.last_telemetry.yaw_deg,
                "historical_target_ignored_deg":patrol.cruise_yaw_deg})
        if route.phase is PatrolPhase.WALL_HOME:
            wall_home = _traverse_to_expected(
                client,
                limiter,
                capture,
                detector,
                logger,
                route,
                patrol,
                direction=outbound,
                target_x=config.camera.cx,
                # Retained argument for old callers; broad-view framing uses
                # actual decoded frame dimensions, not this point target.
                target_y=patrol.wall_target_y_px,
            )
            client.log_event(
                "wall_home_anchor_saved",
                logger.payload(
                    wall_home,
                    phase=PatrolPhase.WALL_HOME,
                    expected_id=2,
                    frame_age_s=0.0,
                    telemetry=client.last_telemetry,
                    direction=None,
                ),
            )
            last_wall_id = patrol.route_ids[0]
        else:
            assert resume_wall_id is not None
            resume_index = patrol.route_ids.index(resume_wall_id)
            last_wall_id = patrol.route_ids[resume_index - 1]
        while route.phase is PatrolPhase.OUTBOUND:
            expected = route.expected_id
            assert expected is not None
            _traverse_to_expected(
                client,
                limiter,
                capture,
                detector,
                logger,
                route,
                patrol,
                direction=outbound,
                target_x=config.camera.cx,
                target_y=patrol.wall_target_y_px,
                departure_tag_id=last_wall_id,
            )
            last_wall_id = expected

        client.log_event("patrol_turnaround", {"at_tag_id": patrol.route_ids[-1]})
        print(f"ID {patrol.route_ids[-1]} is the outbound endpoint; reversing")
        _pause_zero(client, limiter, patrol.turnaround_pause_s)
        route.begin_return()

        while route.phase is PatrolPhase.RETURN:
            expected = route.expected_id
            assert expected is not None
            _traverse_to_expected(
                client,
                limiter,
                capture,
                detector,
                logger,
                route,
                patrol,
                direction=inbound,
                target_x=config.camera.cx,
                target_y=patrol.wall_target_y_px,
                departure_tag_id=last_wall_id,
            )
            last_wall_id = expected

        client.gimbal_down()
        print("Wall-home ID2 reached; gimbal -90 deg for floor ID0")
        _pause_zero(client, limiter, 1.0)
        _align_floor_id0(
            config,
            client,
            limiter,
            capture,
            detector,
            logger,
            route,
            floor_center,
        )
        if land_after_hover:
            print("ID0 aligned; commanding DJI landing")
            client.log_event("landing_commanded", {"tag_id": 0})
            client.land(arm_token)
            _wait_for_landing(client, limiter)
            print("Landing complete")
        else:
            print("ID0 aligned; hovering. Use the RC to land.")
        client.log_event(
            "patrol_complete",
            {"landed": land_after_hover, "final_phase": route.phase.value},
        )
    except InterruptedError as error:
        route.abort()
        client.log_event("patrol_rc_override", {"reason": str(error)})
        print(f"{error}; control stays with the RC pilot")
    except BaseException as error:
        failed_phase = route.phase.value
        route.abort()
        client.log_event(
            "patrol_aborted",
            {"phase": failed_phase, "error": repr(error)},
        )
        raise
    finally:
        try:
            _test_shutdown(client, capture)
        finally:
            if logger is not None:
                logger.close()
