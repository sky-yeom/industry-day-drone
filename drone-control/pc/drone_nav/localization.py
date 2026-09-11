"""Tag-based localization with robust multi-detection fusion."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .config import AppConfig
from .transforms import (
    Matrix4,
    blend_pose,
    inverse_rigid,
    matmul,
    translation,
    weighted_pose,
    wrap_angle,
    yaw,
)


@dataclass(frozen=True)
class TagDetection:
    tag_id: int
    T_C_T: Matrix4
    pose_error: float
    center_px: tuple[float, float] | None = None
    frame_corners_px: tuple[tuple[float, float], ...] | None = None


class TagLocalizer:
    """Computes ``T_W_B = T_W_T * inv(T_C_T) * inv(T_B_C)``."""

    def __init__(self, config: AppConfig) -> None:
        self._tags = config.tag_map
        self._T_B_C = config.body_camera.matrix()
        self._position_limit = config.safety.outlier_position_m
        self._yaw_limit = config.safety.outlier_yaw_rad
        self._alpha = config.safety.ema_alpha
        self._filtered: Matrix4 | None = None

    def candidate_pose(self, detection: TagDetection) -> Matrix4:
        tag = self._tags.get(detection.tag_id)
        if tag is None:
            raise ValueError(f"unknown tag ID {detection.tag_id}")
        if tag.world_pose is None:
            raise ValueError(f"tag ID {detection.tag_id} has no surveyed world pose")
        return matmul(
            matmul(tag.world_pose.matrix(), inverse_rigid(detection.T_C_T)),
            inverse_rigid(self._T_B_C),
        )

    def update(self, detections: list[TagDetection]) -> Matrix4 | None:
        candidates: list[tuple[Matrix4, float]] = []
        for detection in detections:
            tag = self._tags.get(detection.tag_id)
            if (
                tag is not None
                and tag.world_pose is not None
                and math.isfinite(detection.pose_error)
            ):
                weight = 1.0 / max(abs(detection.pose_error), 1e-4)
                candidates.append((self.candidate_pose(detection), weight))
        if not candidates:
            return None
        consensus_groups: list[list[tuple[Matrix4, float]]] = []
        for seed, _ in candidates:
            seed_xyz = translation(seed)
            consensus_groups.append(
                [
                    (pose, weight)
                    for pose, weight in candidates
                    if math.dist(translation(pose), seed_xyz) <= self._position_limit
                    and abs(wrap_angle(yaw(pose) - yaw(seed))) <= self._yaw_limit
                ]
            )
        accepted = max(
            consensus_groups,
            key=lambda group: (len(group), sum(weight for _, weight in group)),
        )
        fused = weighted_pose(accepted)
        self._filtered = (
            fused
            if self._filtered is None
            else blend_pose(self._filtered, fused, self._alpha)
        )
        return self._filtered

    def reset(self) -> None:
        self._filtered = None
