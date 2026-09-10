"""Strict JSON configuration and validation."""

from __future__ import annotations

from dataclasses import MISSING, dataclass, fields
import json
import math
from pathlib import Path
from typing import Any, TypeVar

from .transforms import Matrix4, from_xyz_rpy

T = TypeVar("T")


def _strict(cls: type[T], raw: Any) -> T:
    if not isinstance(raw, dict):
        raise ValueError(f"{cls.__name__} must be an object")
    all_fields = fields(cls)
    expected = {field.name for field in all_fields}
    # Fields with defaults may be omitted; everything else is still required
    # and unknown keys are still rejected.
    optional = {
        field.name for field in all_fields
        if field.default is not MISSING or field.default_factory is not MISSING
    }
    supplied = set(raw)
    if not supplied <= expected or not (expected - optional) <= supplied:
        raise ValueError(
            f"{cls.__name__} fields mismatch; "
            f"missing={sorted((expected - optional) - supplied)}, "
            f"unknown={sorted(supplied-expected)}"
        )
    return cls(**raw)


def _positive(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")


def _finite(name: str, value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be a finite number")


@dataclass(frozen=True)
class RoomConfig:
    length_m: float
    width_m: float
    height_m: float


@dataclass(frozen=True)
class PoseConfig:
    x_m: float
    y_m: float
    z_m: float
    yaw_rad: float
    # A tag on a wall is a vertical plane, which yaw alone cannot describe.
    # Optional, so existing floor-tag configurations stay valid unchanged.
    roll_rad: float = 0.0
    pitch_rad: float = 0.0

    def matrix(self) -> Matrix4:
        return from_xyz_rpy(
            self.x_m,
            self.y_m,
            self.z_m,
            self.roll_rad,
            self.pitch_rad,
            self.yaw_rad,
        )


@dataclass(frozen=True)
class TagConfig:
    id: int
    size_m: float
    # Wall patrol tags can be used as visual waypoints without pretending
    # their corridor coordinates have been surveyed.  Legacy world-pose
    # navigation still requires mapped poses for IDs 0 and 2.
    world_pose: PoseConfig | None


@dataclass(frozen=True)
class CameraConfig:
    fx: float
    fy: float
    cx: float
    cy: float
    distortion: list[float]
    calibrated: bool


@dataclass(frozen=True)
class ExtrinsicConfig:
    x_m: float
    y_m: float
    z_m: float
    roll_rad: float
    pitch_rad: float
    yaw_rad: float
    calibrated: bool

    def matrix(self) -> Matrix4:
        return from_xyz_rpy(
            self.x_m,
            self.y_m,
            self.z_m,
            self.roll_rad,
            self.pitch_rad,
            self.yaw_rad,
        )


@dataclass(frozen=True)
class ControllerConfig:
    max_speed_mps: float
    gain: float
    cross_track_gain: float
    slowdown_distance_m: float
    arrival_threshold_m: float
    visual_gain: float
    visual_tolerance_px: float


@dataclass(frozen=True)
class SafetyConfig:
    tag_hold_s: float
    tag_decelerate_s: float
    network_zero_s: float
    network_disable_s: float
    outlier_position_m: float
    outlier_yaw_rad: float
    ema_alpha: float


@dataclass(frozen=True)
class FlightConfig:
    """Takeoff and altitude-hold behaviour for the hardware runtime."""

    target_altitude_m: float
    altitude_gain: float
    max_vertical_speed_mps: float
    takeoff_timeout_s: float


@dataclass(frozen=True)
class DeadReckoningConfig:
    """Budgeted velocity integration through the tag-blind mid-section."""

    enabled: bool
    max_blind_s: float
    max_blind_m: float


@dataclass(frozen=True)
class NetworkConfig:
    host: str
    port: int
    video_port: int
    video_codec: str
    rate_hz: float
    confirmation_token: str
    protocol_version: int


@dataclass(frozen=True)
class TVFramingConfig:
    """Reference composition, not a measured TV pose or a TV detector."""

    tag_x_min: float = 0.65
    tag_x_max: float = 0.82
    tag_y_min: float = 0.25
    tag_y_max: float = 0.60
    left_tag_widths: float = 6.0
    right_tag_widths: float = 0.8
    top_tag_heights: float = 1.5
    bottom_tag_heights: float = 2.2
    frame_margin: float = 0.02
    approach_angle_deg: float = 0.5
    max_vertical_speed_mps: float = 0.0
    max_correction_s: float = 8.0
    max_correction_distance_m: float = 0.4
    max_height_offset_m: float = 0.15
    correction_interval_s: float = 0.3


@dataclass(frozen=True)
class PatrolConfig:
    """Visual ID0/2/1/3 corridor patrol parameters.

    The route is deliberately landmark-relative: no GPS and no fabricated
    world coordinates are needed for intermediate wall tags.
    """

    route_ids: tuple[int, ...]
    outbound_direction: str
    cruise_altitude_m: float
    speed_mps: float
    angle_deg: float
    cruise_yaw_deg: float
    tag_confirm_s: float
    wall_center_tolerance_px: float
    wall_target_y_px: float
    wall_center_tolerance_y_px: float
    wall_vertical_gain_mps_per_px: float
    wall_vertical_max_speed_mps: float
    acquire_timeout_s: float
    leg_timeout_s: float
    stall_window_s: float
    stall_min_progress_px: float
    recovery_angle_step_deg: float
    recovery_max_angle_deg: float
    recovery_pause_s: float
    turnaround_pause_s: float
    obstacle_stop_m: float
    landing_center_tolerance_px: float
    landing_confirm_s: float
    landing_gain: float
    landing_max_speed_mps: float
    landing_timeout_s: float
    # Wall tags only: accept a broad region, not one optical-centre point.
    wall_view_x_min: float = 0.15
    wall_view_x_max: float = 0.85
    wall_view_y_min: float = 0.15
    wall_view_y_max: float = 0.85
    # A historical corridor heading must never rotate a new setup implicitly.
    align_cruise_yaw: bool = False
    visit_pause_s: float = 3.0
    tv_framing: TVFramingConfig | None = None


@dataclass(frozen=True)
class AppConfig:
    room: RoomConfig
    tags: tuple[TagConfig, ...]
    camera: CameraConfig
    body_camera: ExtrinsicConfig
    controller: ControllerConfig
    safety: SafetyConfig
    flight: FlightConfig
    dead_reckoning: DeadReckoningConfig
    network: NetworkConfig
    actual_measurements_confirmed: bool
    patrol: PatrolConfig | None = None

    @property
    def tag_map(self) -> dict[int, TagConfig]:
        return {tag.id: tag for tag in self.tags}

    @property
    def start_tag(self) -> TagConfig:
        return self.tag_map[0]

    @property
    def goal_tag(self) -> TagConfig:
        return self.tag_map[2]

    def validate(self, *, image_only_walls=False) -> None:
        for name, value in (
            ("room.length_m", self.room.length_m),
            ("room.width_m", self.room.width_m),
            ("room.height_m", self.room.height_m),
            ("camera.fx", self.camera.fx),
            ("camera.fy", self.camera.fy),
        ):
            _positive(name, value)
        minimum_tags = 1 if image_only_walls else 2
        if len(self.tags) < minimum_tags or len({tag.id for tag in self.tags}) != len(self.tags):
            raise ValueError("at least one distinct AprilTag ID is required" if image_only_walls
                             else "at least two distinct AprilTag IDs are required")
        if any(isinstance(tag.id, bool) or not isinstance(tag.id, int) for tag in self.tags):
            raise ValueError("tag IDs must be integers")
        required_ids = {0} if image_only_walls else {0, 2}
        if not required_ids <= {tag.id for tag in self.tags}:
            raise ValueError("tag IDs must include floor=0" if image_only_walls
                             else "tag IDs must include start=0 and home/goal=2")
        for tag in self.tags:
            _positive("tag.size_m", tag.size_m)
            pose = tag.world_pose
            if pose is None:
                continue
            for name in ("x_m", "y_m", "z_m", "yaw_rad"):
                _finite(f"tag.{name}", getattr(pose, name))
            if not (0 <= pose.x_m <= self.room.length_m):
                raise ValueError(f"tag {tag.id} x is outside room")
            if not (0 <= pose.y_m <= self.room.width_m):
                raise ValueError(f"tag {tag.id} y is outside room")
        if len(self.camera.distortion) not in (4, 5, 8):
            raise ValueError("camera distortion must have 4, 5, or 8 coefficients")
        for value in self.camera.distortion:
            _finite("camera distortion coefficient", value)
        for name in ("cx", "cy"):
            _finite(f"camera.{name}", getattr(self.camera, name))
        if not isinstance(self.camera.calibrated, bool):
            raise ValueError("camera.calibrated must be boolean")
        for name in ("x_m", "y_m", "z_m", "roll_rad", "pitch_rad", "yaw_rad"):
            _finite(f"body_camera.{name}", getattr(self.body_camera, name))
        if not isinstance(self.body_camera.calibrated, bool):
            raise ValueError("body_camera.calibrated must be boolean")
        c = self.controller
        for name, value in (
            ("max_speed_mps", c.max_speed_mps),
            ("gain", c.gain),
            ("cross_track_gain", c.cross_track_gain),
            ("slowdown_distance_m", c.slowdown_distance_m),
            ("arrival_threshold_m", c.arrival_threshold_m),
            ("visual_gain", c.visual_gain),
            ("visual_tolerance_px", c.visual_tolerance_px),
        ):
            _positive(name, value)
        if c.max_speed_mps > 1.0:
            raise ValueError("max_speed_mps may not exceed 1.0")
        if c.arrival_threshold_m >= c.slowdown_distance_m:
            raise ValueError("arrival threshold must be smaller than slowdown distance")
        s = self.safety
        for name in (
            "tag_hold_s",
            "tag_decelerate_s",
            "network_zero_s",
            "network_disable_s",
            "outlier_position_m",
            "outlier_yaw_rad",
        ):
            _positive(name, getattr(s, name))
        if s.tag_hold_s != 0.3 or s.tag_decelerate_s != 1.0:
            raise ValueError("tag thresholds must be hold=0.3s and decelerate=1.0s")
        if s.network_zero_s != 0.5 or s.network_disable_s != 2.0:
            raise ValueError("network thresholds must be zero=0.5s and disable=2.0s")
        if not 0 < s.ema_alpha <= 1:
            raise ValueError("ema_alpha must be in (0, 1]")
        f = self.flight
        for name in ("target_altitude_m", "altitude_gain",
                     "max_vertical_speed_mps", "takeoff_timeout_s"):
            _positive(f"flight.{name}", getattr(f, name))
        if not 0.4 <= f.target_altitude_m <= 1.2:
            raise ValueError("flight.target_altitude_m must be within 0.4..1.2")
        if f.max_vertical_speed_mps > 0.5:
            raise ValueError("flight.max_vertical_speed_mps may not exceed 0.5")
        d = self.dead_reckoning
        if not isinstance(d.enabled, bool):
            raise ValueError("dead_reckoning.enabled must be boolean")
        _positive("dead_reckoning.max_blind_s", d.max_blind_s)
        _positive("dead_reckoning.max_blind_m", d.max_blind_m)
        if d.max_blind_s > 15.0:
            raise ValueError("dead_reckoning.max_blind_s may not exceed 15")
        if d.max_blind_m > self.room.length_m:
            raise ValueError("dead_reckoning.max_blind_m may not exceed room length")
        n = self.network
        if (
            not isinstance(n.host, str)
            or not n.host
            or isinstance(n.port, bool)
            or not isinstance(n.port, int)
            or not 1 <= n.port <= 65535
            or isinstance(n.video_port, bool)
            or not isinstance(n.video_port, int)
            or not 1 <= n.video_port <= 65535
        ):
            raise ValueError("network host/port invalid")
        if n.video_codec not in {"h264", "hevc"}:
            raise ValueError("network.video_codec must be h264 or hevc")
        _positive("network.rate_hz", n.rate_hz)
        if n.rate_hz != 10.0:
            raise ValueError("network rate_hz must be 10")
        if (
            isinstance(n.protocol_version, bool)
            or n.protocol_version != 1
            or not isinstance(n.confirmation_token, str)
            or not n.confirmation_token
        ):
            raise ValueError("protocol version must be 1 and token non-empty")
        if not image_only_walls and (self.start_tag.world_pose is None or self.goal_tag.world_pose is None):
            raise ValueError("legacy start=0 and goal=2 tags require world_pose")
        if self.patrol is not None:
            p = self.patrol
            if len(p.route_ids) < 2 or len(set(p.route_ids)) != len(p.route_ids):
                raise ValueError("patrol.route_ids must contain distinct IDs")
            if any(
                isinstance(tag_id, bool) or not isinstance(tag_id, int)
                for tag_id in p.route_ids
            ):
                raise ValueError("patrol.route_ids must contain integer IDs")
            if not image_only_walls and p.route_ids[0] != 2:
                raise ValueError("patrol.route_ids must start at wall-home ID 2")
            missing = set(p.route_ids) - set(self.tag_map)
            if missing and not image_only_walls:
                raise ValueError(f"patrol route references unknown tag IDs {sorted(missing)}")
            if p.outbound_direction not in {"left", "right"}:
                raise ValueError("patrol.outbound_direction must be left or right")
            for name in (
                "cruise_altitude_m",
                "speed_mps",
                "angle_deg",
                "tag_confirm_s",
                "wall_center_tolerance_px",
                "wall_target_y_px",
                "wall_center_tolerance_y_px",
                "wall_vertical_gain_mps_per_px",
                "wall_vertical_max_speed_mps",
                "acquire_timeout_s",
                "leg_timeout_s",
                "stall_window_s",
                "stall_min_progress_px",
                "recovery_angle_step_deg",
                "recovery_max_angle_deg",
                "recovery_pause_s",
                "turnaround_pause_s",
                "obstacle_stop_m",
                "landing_center_tolerance_px",
                "landing_confirm_s",
                "landing_gain",
                "landing_max_speed_mps",
                "landing_timeout_s",
            ):
                _positive(f"patrol.{name}", getattr(p, name))
            _finite("patrol.cruise_yaw_deg", p.cruise_yaw_deg)
            _positive("patrol.visit_pause_s", p.visit_pause_s)
            if p.visit_pause_s > 10:
                raise ValueError("patrol.visit_pause_s must be at most10 seconds")
            if not isinstance(p.align_cruise_yaw, bool):
                raise ValueError("patrol.align_cruise_yaw must be boolean")
            for axis in ("x", "y"):
                low = getattr(p, f"wall_view_{axis}_min")
                high = getattr(p, f"wall_view_{axis}_max")
                _finite(f"patrol.wall_view_{axis}_min", low)
                _finite(f"patrol.wall_view_{axis}_max", high)
                if not 0 < low < high < 1:
                    raise ValueError(f"wall {axis} view bounds must satisfy 0 < min < max < 1")
            _positive("patrol.wall_target_y_px", p.wall_target_y_px)
            if not -180.0 <= p.cruise_yaw_deg <= 180.0:
                raise ValueError("patrol.cruise_yaw_deg must be within [-180, 180]")
            if not 0.5 <= p.cruise_altitude_m <= self.room.height_m - 0.3:
                raise ValueError(
                    "patrol.cruise_altitude_m must leave at least 0.3 m below the ceiling"
                )
            if p.speed_mps > self.controller.max_speed_mps:
                raise ValueError("patrol.speed_mps exceeds controller.max_speed_mps")
            if not 0.5 <= p.angle_deg <= 3.0:
                raise ValueError("patrol.angle_deg must be within [0.5, 3.0]")
            if not p.angle_deg <= p.recovery_max_angle_deg <= 5.0:
                raise ValueError(
                    "patrol.recovery_max_angle_deg must be between angle_deg and 5.0"
                )
            if p.landing_max_speed_mps > 0.1:
                raise ValueError("patrol.landing_max_speed_mps may not exceed 0.1")
            if p.wall_vertical_max_speed_mps > 0.15:
                raise ValueError(
                    "patrol.wall_vertical_max_speed_mps may not exceed 0.15"
                )
            if p.tv_framing is not None:
                tv = p.tv_framing
                if not isinstance(tv, TVFramingConfig):
                    raise ValueError("patrol.tv_framing must be a TVFramingConfig")
                for axis in ("x", "y"):
                    low, high = getattr(tv, f"tag_{axis}_min"), getattr(tv, f"tag_{axis}_max")
                    _finite(f"tv_framing.tag_{axis}_min", low)
                    _finite(f"tv_framing.tag_{axis}_max", high)
                    if not 0 < low < high < 1:
                        raise ValueError("TV tag window must be inside the frame")
                if tv.tag_x_min < 0.5:
                    raise ValueError("TV-left framing requires the tag in the right half")
                for name in ("left_tag_widths", "right_tag_widths", "top_tag_heights", "bottom_tag_heights"):
                    value = getattr(tv, name)
                    _positive(f"tv_framing.{name}", value)
                    if not 0.5 <= value <= 12:
                        raise ValueError("TV framing extents must include the tag and be at most 12 tag units")
                _finite("tv_framing.frame_margin", tv.frame_margin)
                if not 0 < tv.frame_margin < 0.1:
                    raise ValueError("TV frame margin must be between 0 and 0.1")
                _positive("tv_framing.approach_angle_deg", tv.approach_angle_deg)
                if not 0.5 <= tv.approach_angle_deg <= p.angle_deg:
                    raise ValueError("TV approach angle must be between 0.5 and patrol.angle_deg")
                _finite("tv_framing.max_vertical_speed_mps", tv.max_vertical_speed_mps)
                if tv.max_vertical_speed_mps != 0:
                    raise ValueError("TV framing is horizontal-only; max_vertical_speed_mps must be zero")
                for name, maximum in (("max_correction_s", 10.0),
                                      ("max_correction_distance_m", 0.5), ("max_height_offset_m", 0.2)):
                    _positive(f"tv_framing.{name}", getattr(tv, name))
                    if getattr(tv, name) > maximum:
                        raise ValueError(f"tv_framing.{name} exceeds {maximum}")
                _positive("tv_framing.correction_interval_s", tv.correction_interval_s)
                if not 0.3 <= tv.correction_interval_s <= 1.0:
                    raise ValueError("TV correction interval must be within 0.3..1.0 seconds")

    def require_hardware_calibration(self) -> None:
        if not (
            self.camera.calibrated
            and self.body_camera.calibrated
            and self.actual_measurements_confirmed
        ):
            raise RuntimeError(
                "hardware refused: measured camera/extrinsic calibration and "
                "actual_measurements_confirmed=true are required"
            )


def load_config(path: str | Path, *, image_only_walls=False) -> AppConfig:
    """Load legacy config, or explicitly opt into floor-only metric field tags."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be an object")
    expected = {
        "room",
        "tags",
        "camera",
        "body_camera",
        "controller",
        "safety",
        "flight",
        "dead_reckoning",
        "network",
        "actual_measurements_confirmed",
    }
    optional = {"patrol"}
    if not set(raw) <= expected | optional or not expected <= set(raw):
        raise ValueError("configuration root fields mismatch")
    tags_raw = raw["tags"]
    minimum_tags = 1 if image_only_walls else 2
    if not isinstance(tags_raw, list) or len(tags_raw) < minimum_tags:
        raise ValueError("tags must be a list of at least one object" if image_only_walls
                         else "tags must be a list of at least two objects")
    tags: list[TagConfig] = []
    for item in tags_raw:
        if not isinstance(item, dict) or set(item) != {"id", "size_m", "world_pose"}:
            raise ValueError("TagConfig fields mismatch")
        tags.append(
            TagConfig(
                id=item["id"],
                size_m=item["size_m"],
                world_pose=(
                    None
                    if item["world_pose"] is None
                    else _strict(PoseConfig, item["world_pose"])
                ),
            )
        )
    patrol = None
    if "patrol" in raw:
        patrol_raw = raw["patrol"]
        if not isinstance(patrol_raw, dict):
            raise ValueError("PatrolConfig must be an object")
        route_ids = patrol_raw.get("route_ids")
        if not isinstance(route_ids, list):
            raise ValueError("patrol.route_ids must be a list")
        patrol = _strict(
            PatrolConfig, {
                **patrol_raw,
                "route_ids": tuple(route_ids),
                "tv_framing": None if patrol_raw.get("tv_framing") is None
                else _strict(TVFramingConfig, patrol_raw["tv_framing"]),
            }
        )
    config = AppConfig(
        room=_strict(RoomConfig, raw["room"]),
        tags=tuple(tags),
        camera=_strict(CameraConfig, raw["camera"]),
        body_camera=_strict(ExtrinsicConfig, raw["body_camera"]),
        controller=_strict(ControllerConfig, raw["controller"]),
        safety=_strict(SafetyConfig, raw["safety"]),
        flight=_strict(FlightConfig, raw["flight"]),
        dead_reckoning=_strict(DeadReckoningConfig, raw["dead_reckoning"]),
        network=_strict(NetworkConfig, raw["network"]),
        actual_measurements_confirmed=raw["actual_measurements_confirmed"],
        patrol=patrol,
    )
    if not isinstance(config.actual_measurements_confirmed, bool):
        raise ValueError("actual_measurements_confirmed must be boolean")
    config.validate(image_only_walls=image_only_walls)
    return config
