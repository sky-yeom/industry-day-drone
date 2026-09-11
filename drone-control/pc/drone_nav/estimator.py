"""World-frame pose estimation: AprilTag fixes bridged by dead reckoning.

The downward camera cannot see either tag through the middle of the 3.6 m
run, so between fixes the estimate is propagated from DJI NED velocity
telemetry (preferred) or the last commanded body velocity (fallback).
Dead reckoning is budgeted: once the blind time or distance budget is
exhausted the estimate is declared invalid and the caller must stop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import DeadReckoningConfig
from .controller import Velocity
from .protocol import Telemetry


@dataclass(frozen=True)
class EstimatorState:
    x_m: float
    y_m: float
    yaw_rad: float
    blind_time_s: float
    blind_distance_m: float


class PoseEstimator:
    """Tracks (x, y, yaw) in the world frame.

    The tag pipeline's world frame is z-DOWN: pupil_apriltags reports the
    tag z-axis pointing INTO the tag face, i.e. into the floor for a
    face-up floor tag, and the world frame is a yaw-only rotation of the
    tag frame. World yaw therefore increases clockwise viewed from above —
    the SAME handedness as DJI's NED yaw. The frame constant is
    ``offset = yaw_ned - yaw_world`` and any NED-frame angle maps to the
    world as ``theta_w = theta_ned - offset``.
    """

    def __init__(self, config: DeadReckoningConfig) -> None:
        self._config = config
        self._x = 0.0
        self._y = 0.0
        self._yaw = 0.0
        self._have_fix = False
        self._ned_offset: float | None = None
        self._blind_time = 0.0
        self._blind_dist = 0.0

    @property
    def has_fix(self) -> bool:
        return self._have_fix

    @property
    def blind_time_s(self) -> float:
        return self._blind_time

    @property
    def state(self) -> EstimatorState:
        return EstimatorState(
            self._x, self._y, self._yaw, self._blind_time, self._blind_dist
        )

    def update_fix(
        self,
        x_m: float,
        y_m: float,
        yaw_rad: float,
        telemetry: Telemetry | None,
    ) -> None:
        """Absolute fix from tag localization; resets the blind budget."""
        self._x, self._y, self._yaw = x_m, y_m, yaw_rad
        self._have_fix = True
        self._blind_time = 0.0
        self._blind_dist = 0.0
        if (
            telemetry is not None
            and telemetry.yaw_deg is not None
            and telemetry.attitude_age_s is not None
            and telemetry.attitude_age_s <= 0.5
        ):
            self._ned_offset = math.radians(telemetry.yaw_deg) - yaw_rad

    def update_blind(
        self,
        dt_s: float,
        telemetry: Telemetry | None,
        commanded: Velocity | None,
    ) -> bool:
        """Propagate without a tag fix.

        Returns True while the estimate remains inside the blind budget.
        """
        if not self._have_fix or not self._config.enabled:
            return False

        world_velocity = self._world_velocity_from_telemetry(telemetry)
        if world_velocity is None and commanded is not None:
            cos_yaw, sin_yaw = math.cos(self._yaw), math.sin(self._yaw)
            world_velocity = (
                cos_yaw * commanded.forward - sin_yaw * commanded.right,
                sin_yaw * commanded.forward + cos_yaw * commanded.right,
            )
        if world_velocity is None:
            world_velocity = (0.0, 0.0)

        self._x += world_velocity[0] * dt_s
        self._y += world_velocity[1] * dt_s
        self._blind_time += dt_s
        self._blind_dist += math.hypot(*world_velocity) * dt_s

        if (
            telemetry is not None
            and telemetry.yaw_deg is not None
            and telemetry.attitude_age_s is not None
            and telemetry.attitude_age_s <= 0.5
            and self._ned_offset is not None
        ):
            self._yaw = _wrap(math.radians(telemetry.yaw_deg) - self._ned_offset)

        return (
            self._blind_time <= self._config.max_blind_s
            and self._blind_dist <= self._config.max_blind_m
        )

    def world_velocity_from_telemetry(
        self, telemetry: Telemetry | None
    ) -> tuple[float, float] | None:
        """Public for pre-flight validation: NED telemetry mapped to world."""
        return self._world_velocity_from_telemetry(telemetry)

    def _world_velocity_from_telemetry(
        self, telemetry: Telemetry | None
    ) -> tuple[float, float] | None:
        if (
            telemetry is None
            or self._ned_offset is None
            or telemetry.velocity_north_mps is None
            or telemetry.velocity_east_mps is None
            or telemetry.velocity_age_s is None
            or telemetry.velocity_age_s > 0.5
        ):
            return None
        north, east = telemetry.velocity_north_mps, telemetry.velocity_east_mps
        magnitude = math.hypot(north, east)
        if magnitude == 0.0:
            return (0.0, 0.0)
        theta_world = math.atan2(east, north) - self._ned_offset
        return (magnitude * math.cos(theta_world), magnitude * math.sin(theta_world))


def _wrap(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi
