"""Broad tag windows and bounded TV-left composition targets.

Fractions refer to the actual decoded frame, not calibrated principal point.
This admits tag centres, not a claim that a neighbouring TV was detected.
Floor ID0 landing alignment deliberately does not use this policy.
"""
from dataclasses import dataclass
from enum import Enum
import math


class WallViewAction(str, Enum):
    INSIDE = "inside_view"
    APPROACH = "approach_in_route_direction"
    PASSED = "passed_view_no_reverse"
    VERTICAL_OUTSIDE = "vertical_outside_no_height_chase"
    INVALID = "invalid_tag_or_frame_geometry"
    ENVELOPE_OUTSIDE = "tv_reference_outside_no_distance_or_height_chase"


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


def uses_tv_framing(tag_id, patrol):
    return patrol.tv_framing is not None and tag_id in (1, 2, 3)


def tag_view_bounds(tag_id, patrol):
    if not uses_tv_framing(tag_id, patrol):
        return wall_view_bounds(patrol)
    tv = patrol.tv_framing
    return {"x_min": tv.tag_x_min, "x_max": tv.tag_x_max,
            "y_min": tv.tag_y_min, "y_max": tv.tag_y_max}


@dataclass(frozen=True)
class TVFrameWindow:
    x: float
    y: float
    width: float
    height: float
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    @property
    def feasible(self):
        return self.x_min < self.x_max and self.y_min < self.y_max

    @property
    def inside(self):
        return self.feasible and self.x_min <= self.x <= self.x_max and self.y_min <= self.y <= self.y_max


def tv_frame_window(detection, frame_shape, patrol):
    corners = detection.frame_corners_px
    if corners is None or len(corners) != 4 or len(frame_shape) < 2:
        return None
    height, width = frame_shape[:2]
    if (any(len(point) != 2 for point in corners)
            or any(not math.isfinite(v) for v in (height, width, *(v for point in corners for v in point)))
            or min(height, width) <= 0):
        return None
    xs, ys = [point[0] for point in corners], [point[1] for point in corners]
    tag_width, tag_height = max(xs) - min(xs), max(ys) - min(ys)
    if tag_width <= 1 or tag_height <= 1 or min(xs) < 0 or min(ys) < 0 or max(xs) > width or max(ys) > height:
        return None
    x, y = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    tv = patrol.tv_framing
    margin_x, margin_y = tv.frame_margin * width, tv.frame_margin * height
    # Intersect the broad tag window with space reserved for the entire reference.
    low = max(tv.tag_x_min * width, margin_x + tv.left_tag_widths * tag_width)
    high = min(tv.tag_x_max * width, width - margin_x - tv.right_tag_widths * tag_width)
    top = max(tv.tag_y_min * height, margin_y + tv.top_tag_heights * tag_height)
    bottom = min(tv.tag_y_max * height, height - margin_y - tv.bottom_tag_heights * tag_height)
    return TVFrameWindow(x, y, width, height, low, high, top, bottom)


def tv_frame_correction(window, patrol):
    """Return body-right tilt and zero vertical velocity."""
    if window is None or not window.feasible:
        raise ValueError("TV reference cannot fit; adjust the physical camera/TV distance on the ground")
    if not window.y_min <= window.y <= window.y_max:
        raise ValueError("TV vertical framing is outside view; height correction is disabled")
    if window.inside:
        return 0.0, 0.0
    tv = patrol.tv_framing

    def error(value, low, high, size):
        if low <= value <= high:
            return 0.0
        inset = (high - low) * 0.15
        target = low + inset if value < low else high - inset
        return (value - target) / size

    # Rightward flight shifts a fixed tag left in the image.
    right = max(-tv.approach_angle_deg, min(tv.approach_angle_deg,
                                          error(window.x, window.x_min, window.x_max, window.width) * 4))
    return right, 0.0


def tag_view_action(detection, frame_shape, direction, patrol):
    if not uses_tv_framing(detection.tag_id, patrol):
        return wall_view_action(detection.center_px, frame_shape, direction, patrol)
    if direction not in {"left", "right"}:
        raise ValueError("wall traversal direction must be left or right")
    window = tv_frame_window(detection, frame_shape, patrol)
    if window is None:
        return WallViewAction.INVALID
    if not window.feasible:
        return WallViewAction.ENVELOPE_OUTSIDE
    if not window.y_min <= window.y <= window.y_max:
        return WallViewAction.VERTICAL_OUTSIDE
    if window.inside:
        return WallViewAction.INSIDE
    approaching = (direction == "left" and window.x < window.x_min) or (
        direction == "right" and window.x > window.x_max)
    return WallViewAction.APPROACH if approaching else WallViewAction.PASSED
