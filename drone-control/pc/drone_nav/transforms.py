"""Rigid transforms.

``T_A_B`` maps homogeneous coordinates expressed in frame B into frame A.
Frames are W (world), B (aircraft body), C (camera), and T (tag).
Matrices are row-major immutable tuples and use metres/radians.
"""

from __future__ import annotations

import math
from typing import Iterable, TypeAlias

Matrix4: TypeAlias = tuple[tuple[float, float, float, float], ...]


def identity() -> Matrix4:
    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def matmul(a: Matrix4, b: Matrix4) -> Matrix4:
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4))
        for i in range(4)
    )


def inverse_rigid(t: Matrix4) -> Matrix4:
    r = tuple(tuple(t[i][j] for j in range(3)) for i in range(3))
    rt = tuple(tuple(r[j][i] for j in range(3)) for i in range(3))
    p = (t[0][3], t[1][3], t[2][3])
    q = tuple(-sum(rt[i][j] * p[j] for j in range(3)) for i in range(3))
    return (
        (rt[0][0], rt[0][1], rt[0][2], q[0]),
        (rt[1][0], rt[1][1], rt[1][2], q[1]),
        (rt[2][0], rt[2][1], rt[2][2], q[2]),
        (0.0, 0.0, 0.0, 1.0),
    )


def from_xyz_rpy(
    x: float, y: float, z: float, roll: float, pitch: float, yaw: float
) -> Matrix4:
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return (
        (cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr, x),
        (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr, y),
        (-sp, cp * sr, cp * cr, z),
        (0.0, 0.0, 0.0, 1.0),
    )


def translation(t: Matrix4) -> tuple[float, float, float]:
    return (t[0][3], t[1][3], t[2][3])


def yaw(t: Matrix4) -> float:
    return math.atan2(t[1][0], t[0][0])


def wrap_angle(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi


def blend_pose(previous: Matrix4, current: Matrix4, alpha: float) -> Matrix4:
    p0, p1 = translation(previous), translation(current)
    angle = yaw(previous) + alpha * wrap_angle(yaw(current) - yaw(previous))
    p = tuple((1.0 - alpha) * p0[i] + alpha * p1[i] for i in range(3))
    return from_xyz_rpy(p[0], p[1], p[2], 0.0, 0.0, angle)


def weighted_pose(poses: Iterable[tuple[Matrix4, float]]) -> Matrix4:
    items = list(poses)
    total = sum(weight for _, weight in items)
    if not items or total <= 0.0:
        raise ValueError("at least one positive-weight pose is required")
    xyz = tuple(
        sum(translation(pose)[axis] * weight for pose, weight in items) / total
        for axis in range(3)
    )
    sin_yaw = sum(math.sin(yaw(pose)) * weight for pose, weight in items)
    cos_yaw = sum(math.cos(yaw(pose)) * weight for pose, weight in items)
    return from_xyz_rpy(
        xyz[0], xyz[1], xyz[2], 0.0, 0.0, math.atan2(sin_yaw, cos_yaw)
    )
