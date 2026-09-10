"""Pure ID1 + adjacent mock-screen framing using a reference-plane footprint.

The projected region is a prediction from the reference layout, not detection
of a physical television or a metric distance. This proposed control policy
has not been validated by a field flight. No imports or methods perform I/O.
"""
from __future__ import annotations

import copy
import math


FRESH_S = .5
SEEK_MAX_DEG = .6
# Corrections are continuous, re-evaluated on every fresh frame, with the same
# tilt authority as the seek. The 14:51 flight showed that 0.25 deg pulses of
# 0.25 s barely move this aircraft (about 0.04 m/s^2 for a quarter second),
# so a 20-40 cm overshoot was never recovered and the leg timed out hovering.
CORRECTION_MAX_DEG = .6
CORRECTION_MIN_DEG = .25
RECOVERY_SEEK_DEG = .6
CORRECTION_ZERO_S = .6
RECOVERY_SETTLE_S = .3
CAPTURE_ZERO_S = 1.
CAPTURE_HOLD_S = .5
STILL_SPEED_MPS = .08
MAX_REVERSE_PULSES = 3
MISSING_RECOVERY_S = 6.
RECOVERY_MOTION_EVIDENCE_S = 1.5


class PairFramingError(RuntimeError):
    """The caller must command zero/release; this gate never resumes itself."""


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _reference(reference):
    if not isinstance(reference, dict) or not isinstance(reference.get("roi_tag_bounds"), dict):
        raise ValueError("Reference requires roi_tag_bounds")
    bounds = reference["roi_tag_bounds"]
    if set(bounds) != {"x_min", "x_max", "y_min", "y_max"} or not all(_finite(v) for v in bounds.values()):
        raise ValueError("Reference ROI requires four finite tag-plane bounds")
    if not (bounds["x_min"] <= 0. < 1. <= bounds["x_max"]
            and bounds["y_min"] <= 0. < 1. <= bounds["y_max"]):
        raise ValueError("Reference ROI must include the whole canonical black tag")
    margin = reference.get("margin_fraction", .03)
    if not _finite(margin) or not .03 <= margin <= .2:
        raise ValueError("Frame margin must be between 3% and 20%")
    return dict(bounds), float(margin)


def ordered_tag_corners(corners):
    """Return geometric TL, TR, BR, BL for an upright convex wall tag.

    Pupil's raw TR, TL, BL, BR ordering is accepted. Reference geometry is
    aligned with the observed geometric square, not invented physical units.
    """
    if corners is None or len(corners) != 4:
        raise ValueError("Four tag corners are required")
    points = []
    for point in corners:
        if len(point) != 2 or not all(_finite(v) for v in point):
            raise ValueError("Tag corners must be finite image coordinates")
        points.append(tuple(float(v) for v in point))
    if len(set(points)) != 4:
        raise ValueError("Repeated tag corners cannot define a homography")
    cx, cy = (sum(p[axis] for p in points) / 4. for axis in (0, 1))
    points.sort(key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    start = min(range(4), key=lambda i: (sum(points[i]), points[i][1], points[i][0]))
    points = points[start:] + points[:start]
    crosses = []
    for i in range(4):
        a, b, c = points[i], points[(i+1) % 4], points[(i+2) % 4]
        crosses.append((b[0]-a[0])*(c[1]-b[1]) - (b[1]-a[1])*(c[0]-b[0]))
    if not all(value > 1e-6 for value in crosses):
        raise ValueError("Tag corners must form a nondegenerate convex quadrilateral")
    return tuple(points)


def _unit_square_homography(points):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = points
    dx1, dx2, dx3 = x1-x2, x3-x2, x0-x1+x2-x3
    dy1, dy2, dy3 = y1-y2, y3-y2, y0-y1+y2-y3
    if abs(dx3) + abs(dy3) < 1e-10:
        g = h = 0.
    else:
        divisor = dx1*dy2 - dx2*dy1
        if abs(divisor) < 1e-10:
            raise ValueError("Tag homography is singular")
        g = (dx3*dy2 - dx2*dy3) / divisor
        h = (dx1*dy3 - dx3*dy1) / divisor
    return ((x1-x0+g*x1, x3-x0+h*x3, x0),
            (y1-y0+g*y1, y3-y0+h*y3, y0), (g, h, 1.))


def project_pair_footprint(corners, frame_shape, reference):
    """Project the configured padded pair rectangle into actual frame pixels."""
    bounds, margin = _reference(reference)
    if frame_shape is None or len(frame_shape) < 2:
        raise ValueError("Actual decoded frame shape is required")
    height, width = frame_shape[:2]
    if not all(_finite(v) and v > 0 for v in (height, width)):
        raise ValueError("Actual frame dimensions must be positive and finite")
    corners = ordered_tag_corners(corners)
    matrix = _unit_square_homography(corners)
    roi = ((bounds["x_min"], bounds["y_min"]), (bounds["x_max"], bounds["y_min"]),
           (bounds["x_max"], bounds["y_max"]), (bounds["x_min"], bounds["y_max"]))
    projected = []
    for x, y in roi:
        denominator = matrix[2][0]*x + matrix[2][1]*y + 1.
        # A pole inside the extrapolated rectangle invalidates the whole
        # footprint; four finite-looking endpoints alone are insufficient.
        if denominator <= 1e-8 or not math.isfinite(denominator):
            raise ValueError("Reference ROI crosses an invalid homography horizon")
        point = tuple((row[0]*x + row[1]*y + row[2]) / denominator for row in matrix[:2])
        if not all(math.isfinite(v) for v in point):
            raise ValueError("Reference footprint projection is not finite")
        projected.append(point)
    left, right = min(p[0] for p in projected), max(p[0] for p in projected)
    top, bottom = min(p[1] for p in projected), max(p[1] for p in projected)
    allowed = {"left": margin*width, "right": (1.-margin)*width,
               "top": margin*height, "bottom": (1.-margin)*height}
    overflow = {"left": max(0., allowed["left"] - left),
                "right": max(0., right - allowed["right"]),
                "top": max(0., allowed["top"] - top),
                "bottom": max(0., bottom - allowed["bottom"])}
    return {"projected_corners_px": [list(p) for p in projected],
            "tag_corners_tl_tr_br_bl_px": [list(p) for p in corners],
            "bbox_px": {"left": left, "right": right, "top": top, "bottom": bottom},
            "allowed_bbox_px": allowed, "overflow_px": overflow,
            "frame_size_px": [width, height], "fits": all(v <= 1e-6 for v in overflow.values()),
            "too_wide": right-left > allowed["right"]-allowed["left"] + 1e-6,
            "vertical_outside": overflow["top"] > 1e-6 or overflow["bottom"] > 1e-6,
            "measurement": "predicted_reference_plane_footprint", "physical_distance_available": False}


class PairFramingGate:
    """ID1 framing that returns only a right tilt or zero, on every fresh frame.

    Motion is continuous and proportional: the seek, the correction back after
    an overshoot and the reacquire seek after losing the tag all hold a tilt
    until the next frame says otherwise. Direction reversals first zero and
    wait for the aircraft to be still, so momentum is never fought blindly.
    motion_valid_until_s is always None: the caller must not chop commands.
    Any raised PairFramingError requires caller cleanup, never automatic retry.
    """
    def __init__(self, reference, direction="left", arrival_band=None, tag_id=1):
        _reference(reference)
        if direction not in {"left", "right"}:
            raise ValueError("Direction must be left or right")
        if type(tag_id) is not int or tag_id < 0:
            raise ValueError("A non-negative integer tag id is required")
        self.tag_id = tag_id
        if arrival_band is not None and (not isinstance(arrival_band, (list, tuple))
                or len(arrival_band) != 2 or not all(_finite(v) for v in arrival_band)
                or not 0. < arrival_band[0] < arrival_band[1] < 1.):
            raise ValueError("Arrival band must contain two ordered fractions inside the frame")
        self.reference = copy.deepcopy(reference)
        self.direction = direction
        self.arrival_band = None if arrival_band is None else tuple(arrival_band)
        self._planned = -1. if direction == "left" else 1.
        self._last_key = None
        self._last_now = None
        self._seen = False
        self._last_command = 0.
        self._zero_since = None
        self._stable_since = None
        self._stable_frames = 0
        self._correction_sign = None
        self._recovery_sign = None
        self._reverse_pulses = 0
        self._ever_corrected = False
        self._needs_settle = False
        self._done = False
        self._stopped = False
        self._last_nonzero_motion = None
        self._last_tag_observation = None
        self._missing_active = False
        self._recovery_pulses = 0
        self._diagnostic = {"state": "WAIT_FRAME", "capture_ready": False}

    @property
    def diagnostic(self):
        return copy.deepcopy(self._diagnostic)

    def _stop(self, reason):
        self._stopped = True
        self._correction_sign = self._recovery_sign = None
        self._last_command = 0.
        self._diagnostic.update(state="STOPPED", reason=reason, requested_right_tilt_deg=0.,
                                motion_valid_until_s=None, capture_ready=False)
        raise PairFramingError(reason)

    def _zero(self, now, state, reason):
        if self._last_command != 0. or self._zero_since is None:
            self._zero_since = now
        self._last_command = 0.
        self._diagnostic.update(state=state, reason=reason, requested_right_tilt_deg=0.,
            motion_valid_until_s=None, next_update_due_s=now+.1,
            zero_hold_s=max(0., now-self._zero_since), capture_ready=False)
        return 0., None

    def _motion(self, tilt, now, state):
        self._last_command, self._zero_since = tilt, None
        self._last_nonzero_motion = (tilt, now)
        self._stable_since, self._stable_frames = None, 0
        self._diagnostic.update(state=state, reason=("tag_not_yet_in_arrival_band" if self.arrival_band
            else "pair_footprint_not_yet_inside"), requested_right_tilt_deg=tilt,
            motion_valid_until_s=None, next_update_due_s=now+.1,
            zero_hold_s=0., capture_ready=False, reverse_pulses_used=self._reverse_pulses)
        return tilt, None

    def _cancel_pulse(self):
        """End any continuous correction/recovery; the next motion settles first."""
        self._correction_sign = self._recovery_sign = None
        self._needs_settle = True

    @staticmethod
    def _proportional(amount, width, cap, floor):
        return min(cap, max(floor, cap*amount/(.2*width)))

    def _remember_tag_for_recovery(self, center, width, now):
        fraction = center[0] / width
        previous = self._last_tag_observation
        trend = (None if previous is None or not 0. <= now-previous["now_s"] <= RECOVERY_MOTION_EVIDENCE_S
                 else fraction - previous["center_fraction"])
        recent_motion = (self._last_nonzero_motion is not None
                         and 0. <= now-self._last_nonzero_motion[1] <= RECOVERY_MOTION_EVIDENCE_S)
        tilt = self._last_nonzero_motion[0] if recent_motion else 0.
        sign, evidence = None, None
        if fraction >= self.arrival_band[0] and (tilt < 0. or trend is not None and trend >= .005):
            sign, evidence = 1., "last_tag_near_right_with_left_motion_or_rightward_image_trend"
        elif fraction <= 1.-self.arrival_band[0] and (tilt > 0. or trend is not None and trend <= -.005):
            sign, evidence = -1., "last_tag_near_left_with_right_motion_or_leftward_image_trend"
        self._last_tag_observation = {"now_s": now, "center_fraction": fraction,
                                      "recovery_sign": sign, "evidence": evidence}

    def _recover_missing_tag(self, now, speed):
        """Bounded reacquire seek toward where the tag left the frame.

        The aircraft is first brought to a standstill, then tilted back in the
        evidence direction continuously until the tag is seen again or the
        recovery window after the last observation closes. Running out of the
        window is not fatal: the gate waits at zero for a fresh view.
        """
        observation = self._last_tag_observation
        self._stable_since, self._stable_frames = None, 0
        self._diagnostic.update(target_visible=False, recovery_pulses_used=self._recovery_pulses,
            recovery_window_s=MISSING_RECOVERY_S, recovery_tilt_deg=RECOVERY_SEEK_DEG,
            last_tag_observation=copy.deepcopy(observation))
        if not self._missing_active:
            self._missing_active = True
            self._cancel_pulse()
            return self._zero(now, "REACQUIRE_SETTLE", "target_lost_zero_before_evidence_based_recovery")
        recent = (observation is not None and observation["recovery_sign"] is not None
                  and 0. <= now-observation["now_s"] < MISSING_RECOVERY_S)
        if not recent:
            self._cancel_pulse()
            return self._zero(now, "WAIT_TARGET", "no_recent_direction_evidence_for_missing_target")
        if self._recovery_sign is None:
            self._zero(now, "REACQUIRE_SETTLE", "zero_and_low_speed_before_missing_target_correction")
            if now-self._zero_since < RECOVERY_SETTLE_S-1e-9 or speed > STILL_SPEED_MPS:
                return 0., None
            self._recovery_pulses += 1
            self._recovery_sign = observation["recovery_sign"]
            if self._recovery_sign*self._planned < 0.:
                self._reverse_pulses += 1
                self._ever_corrected = True
            self._needs_settle = False
        result = self._motion(self._recovery_sign*RECOVERY_SEEK_DEG, now, "BOUNDED_REACQUIRE_SEEK")
        self._diagnostic.update(reason=observation["evidence"], recovery_pulses_used=self._recovery_pulses,
                                recovery_window_ends_s=observation["now_s"]+MISSING_RECOVERY_S)
        return result

    def update(self, tags, now_s, frame_age, frame_key, frame_shape,
               horizontal_speed_mps, velocity_age_s):
        self._diagnostic = {"state": "OBSERVING", "expected_id": self.tag_id,
            "policy_validation": "unvalidated_field_trial", "predicted_footprint_only": True,
            "actual_mock_detection_verified": False, "reference_layout_assumed_unchanged": True,
            "arrival_policy": "tag_right_edge_band" if self.arrival_band else "strict_reference_footprint",
            "arrival_band_fraction": self.arrival_band, "arrival_ready": False,
            "physical_distance_available": False,
            "correction_mode": "continuous_proportional_until_inside_or_side_change",
            "seek_tilt_cap_deg": SEEK_MAX_DEG, "correction_tilt_cap_deg": CORRECTION_MAX_DEG,
            "correction_tilt_min_deg": CORRECTION_MIN_DEG, "reverse_pulses_used": self._reverse_pulses,
            "frame_age_s": frame_age, "frame_key": frame_key, "velocity_age_s": velocity_age_s,
            "horizontal_speed_mps": horizontal_speed_mps, "capture_ready": False}
        if self._stopped:
            self._stop("controller_already_stopped_no_resume")
        if not _finite(now_s) or (self._last_now is not None and now_s < self._last_now):
            self._stop("invalid_or_regressed_clock")
        self._last_now = now_s
        if not _finite(frame_age) or not 0. <= frame_age <= FRESH_S:
            self._stop("stale_or_invalid_detection_frame")
        if (not _finite(velocity_age_s) or not 0. <= velocity_age_s <= FRESH_S
                or not _finite(horizontal_speed_mps) or horizontal_speed_mps < 0.):
            self._stop("fresh_finite_horizontal_velocity_required")
        if (not isinstance(frame_key, (tuple, list)) or len(frame_key) != 2
                or any(type(v) is not int or v < 0 for v in frame_key)):
            self._stop("identified_detection_frame_required")
        key = tuple(frame_key)
        if self._last_key is not None:
            if key[0] != self._last_key[0]:
                self._stop("video_generation_changed_no_resume")
            if key[1] < self._last_key[1]:
                self._stop("detection_frame_sequence_regressed")
            if key == self._last_key:
                self._cancel_pulse()
                self._stable_since, self._stable_frames = None, 0
                return self._zero(now_s, "WAIT_FRESH_FRAME", "duplicate_frame_does_not_confirm_or_extend_motion")
        self._last_key = key
        if (frame_shape is None or len(frame_shape) < 2
                or not all(_finite(v) and v > 0 for v in frame_shape[:2])):
            self._stop("actual_decoded_frame_geometry_required")
        tags = list(tags)
        self._diagnostic["visible_ids"] = [getattr(t, "tag_id", None) for t in tags]
        choices = [t for t in tags if type(getattr(t, "tag_id", None)) is int and t.tag_id == self.tag_id
                   and type(getattr(t, "hamming", None)) is int and 0 <= t.hamming <= 2
                   and _finite(getattr(t, "decision_margin", None)) and t.decision_margin >= 0.]
        observed = min(choices, key=lambda t: (t.hamming, -t.decision_margin)) if choices else None
        if observed is None:
            self._stable_since, self._stable_frames = None, 0
            if self._seen or self._done:
                if self.arrival_band is not None:
                    self._done = False
                    return self._recover_missing_tag(now_s, horizontal_speed_mps)
                self._cancel_pulse()
                return self._zero(now_s, "WAIT_TARGET", f"ID{self.tag_id}_missing_or_decoding_quality_invalid")
            return self._motion(self._planned*SEEK_MAX_DEG, now_s, f"SEEK_ID{self.tag_id}")
        self._seen = True
        if self.arrival_band is not None:
            try:
                actual_corners = ordered_tag_corners(observed.corners_px)
                center = observed.center_px
                if len(center) != 2 or not all(_finite(v) for v in center):
                    raise ValueError("Finite actual tag center is required")
            except (ValueError, TypeError, AttributeError) as exc:
                self._stop(f"invalid_actual_tag:{exc}")
            self._remember_tag_for_recovery(center, frame_shape[1], now_s)
            if self._missing_active:
                # Reacquired after a loss: settle before any further correction
                # instead of continuing the reacquire seek at full tilt.
                self._missing_active = False
                self._cancel_pulse()
            self._diagnostic.update(target_visible=True, recovery_pulses_used=self._recovery_pulses)
        try:
            footprint = project_pair_footprint(observed.corners_px, frame_shape, self.reference)
        except (ValueError, TypeError, AttributeError) as exc:
            if self.arrival_band is None:
                self._stop(f"invalid_pair_projection:{exc}")
            # Arrival uses observed tag geometry. Extrapolation beyond a valid
            # tag can cross a projective horizon, which is diagnostic only here.
            footprint = {"fits": None, "projection_valid": False, "projection_error": str(exc),
                         "frame_size_px": [frame_shape[1], frame_shape[0]],
                         "measurement": "predicted_reference_plane_footprint",
                         "physical_distance_available": False}
        self._diagnostic["footprint"] = footprint
        if self.arrival_band is not None:
            width, height = frame_shape[1], frame_shape[0]
            overflow = {"left": max(0., self.arrival_band[0]*width-center[0]),
                        "right": max(0., center[0]-self.arrival_band[1]*width)}
            horizontal_inside = not any(overflow.values())
            tag_inside = all(.02*width <= x <= .98*width and .02*height <= y <= .98*height
                             for x, y in actual_corners)
            tag_overflow = {"left": max(0., .02*width-min(x for x, _ in actual_corners)),
                            "right": max(0., max(x for x, _ in actual_corners)-.98*width),
                            "top": max(0., .02*height-min(y for _, y in actual_corners)),
                            "bottom": max(0., max(y for _, y in actual_corners)-.98*height)}
            capture_candidate = horizontal_inside and tag_inside
            # CAPTURE_READY is provisional until the caller saves the photo.
            # A deferred post-ACK capture must be allowed to reframe/re-settle.
            if self._done and (not capture_candidate or horizontal_speed_mps > STILL_SPEED_MPS):
                self._done = False
                self._stable_since, self._stable_frames = None, 0
            self._diagnostic.update(photo_quality="tag_edge_arrival_pending_visual_review",
                actual_tag_inside_frame=tag_inside, actual_tag_margin_fraction=.02,
                actual_tag_center_px=list(center), actual_tag_overflow_px=tag_overflow)
            if horizontal_inside and not tag_inside:
                if tag_overflow["right"] > 0. or tag_overflow["left"] > 0.:
                    # Arrival is not complete until the actual black tag fits.
                    # A clipped edge is corrected like an overshoot, even if
                    # the tag center is already in-band.
                    overflow = {"left": tag_overflow["left"], "right": tag_overflow["right"]}
                else:
                    # Lateral-only control cannot repair vertical clipping;
                    # remain stopped awaiting a fresh usable view, never claim
                    # a photo or blindly move after losing the actual target.
                    self._cancel_pulse()
                    self._stable_since, self._stable_frames = None, 0
                    return self._zero(now_s, "WAIT_WHOLE_TAG", "actual_black_tag_vertical_crop_waiting_reacquire")
        else:
            overflow = footprint["overflow_px"]
            horizontal_inside = overflow["left"] <= 1e-6 and overflow["right"] <= 1e-6
            capture_candidate = footprint["fits"]
            self._diagnostic["photo_quality"] = "full_reference_footprint" if capture_candidate else "not_fully_framed"
            if footprint["too_wide"]:
                self._stop("pair_footprint_too_wide_for_lateral_framing")
            if horizontal_inside and footprint["vertical_outside"]:
                self._cancel_pulse()
                self._stable_since, self._stable_frames = None, 0
                self._zero(now_s, "VERTICAL_CROP_SETTLE", "horizontal_pair_inside_wait_for_still_vertical_verdict")
                if now_s-self._zero_since >= CORRECTION_ZERO_S-1e-9 and horizontal_speed_mps <= STILL_SPEED_MPS:
                    self._stop("pair_footprint_vertical_outside_no_height_chase")
                return 0., None
        if self._done:
            self._zero(now_s, "COMPLETE_HOVER", "capture_already_ready_no_further_motion")
            ready = capture_candidate and horizontal_speed_mps <= STILL_SPEED_MPS
            self._diagnostic.update(capture_ready=ready, arrival_ready=ready)
            return 0., observed if ready else None
        if capture_candidate:
            self._cancel_pulse()
            self._zero(now_s, "CAPTURE_SETTLE", "whole_tag_inside_arrival_band" if self.arrival_band
                       else "whole_predicted_pair_inside_margin")
            if horizontal_speed_mps <= STILL_SPEED_MPS:
                self._stable_since = now_s if self._stable_since is None else self._stable_since
                self._stable_frames += 1
            else:
                self._stable_since, self._stable_frames = None, 0
            stable_for = 0. if self._stable_since is None else now_s-self._stable_since
            self._diagnostic.update(stable_frame_hold_s=stable_for, stable_distinct_frames=self._stable_frames)
            if (now_s-self._zero_since >= CAPTURE_ZERO_S-1e-9
                    and stable_for >= CAPTURE_HOLD_S-1e-9 and self._stable_frames >= 2):
                self._done = True
                self._diagnostic.update(state="CAPTURE_READY", capture_ready=True, arrival_ready=True)
                return 0., observed
            return 0., None
        self._stable_since, self._stable_frames = None, 0
        near_border = "left" if self.direction == "left" else "right"
        far_border = "right" if self.direction == "left" else "left"
        overshot = overflow[far_border] > 1e-6
        wanted_sign = -self._planned if overshot else self._planned
        amount = overflow[far_border] if overshot else overflow[near_border]
        width = footprint["frame_size_px"][0]
        if self._correction_sign is not None and self._correction_sign != wanted_sign:
            # The tag crossed the target region: stop and settle before reversing.
            self._cancel_pulse()
        if wanted_sign == self._planned and not self._ever_corrected and not self._needs_settle:
            tilt = self._proportional(amount, width, SEEK_MAX_DEG, .12)
            return self._motion(self._planned*tilt, now_s, "APPROACH_PAIR_IN_ROUTE_DIRECTION")
        if self._correction_sign is None:
            self._zero(now_s, "CORRECTION_SETTLE", "zero_and_low_speed_required_before_correction")
            if now_s-self._zero_since < CORRECTION_ZERO_S-1e-9 or horizontal_speed_mps > STILL_SPEED_MPS:
                return 0., None
            if wanted_sign != self._planned:
                if self._reverse_pulses >= MAX_REVERSE_PULSES and self.arrival_band is None:
                    self._stop("reverse_pulse_limit_reached_without_framing")
                self._reverse_pulses += 1
                self._ever_corrected = True
            self._correction_sign = wanted_sign
            self._needs_settle = False
        tilt = self._proportional(amount, width, CORRECTION_MAX_DEG, CORRECTION_MIN_DEG)
        return self._motion(self._correction_sign*tilt, now_s, "CONTINUOUS_CORRECTION")
