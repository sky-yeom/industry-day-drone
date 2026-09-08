"""Broad wall-tag framing, with no point chasing or reverse correction.

Fractions refer to the actual decoded frame, not calibrated principal point.
This admits tag centres, not a claim that a neighbouring TV was detected.
Floor ID0 landing alignment deliberately does not use this policy.
"""
from enum import Enum
import math


class WallViewAction(str, Enum):
    INSIDE = "inside_view"
    APPROACH = "approach_in_route_direction"
    PASSED = "passed_view_no_reverse"
    VERTICAL_OUTSIDE = "vertical_outside_no_height_chase"
    INVALID = "invalid_tag_or_frame_geometry"


def wall_view_action(center_px, frame_shape, direction, patrol):
    if direction not in {"left", "right"}:
        raise ValueError("wall traversal direction must be left or right")
    if center_px is None or len(center_px) != 2 or len(frame_shape) < 2:
        return WallViewAction.INVALID
    height, width = frame_shape[:2]
    x, y = center_px
    if any(not math.isfinite(v) for v in (height, width, x, y)) or min(height, width) <= 0:
        return WallViewAction.INVALID
    x, y = x / width, y / height
    if not 0 <= x <= 1 or not 0 <= y <= 1:
        return WallViewAction.INVALID
    if not patrol.wall_view_y_min <= y <= patrol.wall_view_y_max:
        return WallViewAction.VERTICAL_OUTSIDE
    if patrol.wall_view_x_min <= x <= patrol.wall_view_x_max:
        return WallViewAction.INSIDE
    # While travelling left, a fixed wall tag moves right in the image.
    approaching = (direction == "left" and x < patrol.wall_view_x_min) or (
        direction == "right" and x > patrol.wall_view_x_max)
    return WallViewAction.APPROACH if approaching else WallViewAction.PASSED


def wall_view_bounds(patrol):
    return {"x_min":patrol.wall_view_x_min,"x_max":patrol.wall_view_x_max,
            "y_min":patrol.wall_view_y_min,"y_max":patrol.wall_view_y_max}
