"""Straight-line and final visual-alignment controllers."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .config import ControllerConfig
from .transforms import Matrix4


@dataclass(frozen=True)
class Velocity:
    forward: float
    right: float
    up: float = 0.0
    yaw_rate: float = 0.0

    @property
    def speed(self) -> float:
        return math.hypot(self.forward, self.right)

    @property
    def is_zero(self) -> bool:
        return self.speed < 1e-12 and abs(self.up) < 1e-12 and abs(self.yaw_rate) < 1e-12

    def scaled(self, scale: float) -> "Velocity":
        scale = min(1.0, max(0.0, scale))
        return Velocity(
            self.forward * scale,
            self.right * scale,
            self.up * scale,
            self.yaw_rate * scale,
        )


class LineFollower:
    def __init__(
        self,
        start_xy: tuple[float, float],
        goal_xy: tuple[float, float],
        config: ControllerConfig,
    ) -> None:
        self.start = start_xy
        self.goal = goal_xy
        self.config = config
        dx, dy = goal_xy[0] - start_xy[0], goal_xy[1] - start_xy[1]
        length = math.hypot(dx, dy)
        if length <= 0:
            raise ValueError("start and goal must differ")
        self._along = (dx / length, dy / length)
        self._normal = (-self._along[1], self._along[0])

    def command(self, position_xy: tuple[float, float], body_yaw: float) -> Velocity:
        gx, gy = self.goal[0] - position_xy[0], self.goal[1] - position_xy[1]
        distance = math.hypot(gx, gy)
        if distance <= self.config.arrival_threshold_m:
            return Velocity(0.0, 0.0)
        from_start = (
            position_xy[0] - self.start[0],
            position_xy[1] - self.start[1],
        )
        cross_track = (
            from_start[0] * self._normal[0] + from_start[1] * self._normal[1]
        )
        world_x = self.config.gain * gx - self.config.cross_track_gain * cross_track * self._normal[0]
        world_y = self.config.gain * gy - self.config.cross_track_gain * cross_track * self._normal[1]
        speed_limit = math.nextafter(self.config.max_speed_mps, 0.0) * min(
            1.0, distance / self.config.slowdown_distance_m
        )
        magnitude = math.hypot(world_x, world_y)
        if magnitude > speed_limit:
            world_x *= speed_limit / magnitude
            world_y *= speed_limit / magnitude
        cos_yaw, sin_yaw = math.cos(body_yaw), math.sin(body_yaw)
        forward = cos_yaw * world_x + sin_yaw * world_y
        right = -sin_yaw * world_x + cos_yaw * world_y
        body_magnitude = math.hypot(forward, right)
        if body_magnitude > speed_limit:
            forward *= speed_limit / body_magnitude
            right *= speed_limit / body_magnitude
        return Velocity(forward, right)

    def arrived(self, position_xy: tuple[float, float]) -> bool:
        return math.dist(position_xy, self.goal) <= self.config.arrival_threshold_m


class GoalVisualAligner:
    def __init__(
        self,
        config: ControllerConfig,
        principal: tuple[float, float],
        T_B_C: Matrix4 | None = None,
    ) -> None:
        self.config = config
        self.principal = principal
        self._T_B_C = T_B_C

    def command(self, center_px: tuple[float, float]) -> Velocity:
        error_x = center_px[0] - self.principal[0]
        error_y = center_px[1] - self.principal[1]
        if math.hypot(error_x, error_y) <= self.config.visual_tolerance_px:
            return Velocity(0.0, 0.0)
        if self._T_B_C is None:
            forward = -error_y * self.config.visual_gain
            right = error_x * self.config.visual_gain
        else:
            forward = self.config.visual_gain * (
                self._T_B_C[0][0] * error_x + self._T_B_C[0][1] * error_y
            )
            right = self.config.visual_gain * (
                self._T_B_C[1][0] * error_x + self._T_B_C[1][1] * error_y
            )
        magnitude = math.hypot(forward, right)
        limit = min(self.config.max_speed_mps, 0.05)
        if magnitude > limit:
            forward *= limit / magnitude
            right *= limit / magnitude
        return Velocity(forward, right)
