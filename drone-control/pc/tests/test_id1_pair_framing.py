"""Offline geometry/control tests; no camera, network, SDK or physical flight."""
import itertools
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "trials"))
from id1_pair_framing import PairFramingError, PairFramingGate, project_pair_footprint


REFERENCE = {"roi_tag_bounds": {"x_min": -5.090921, "x_max": 1.406036,
                              "y_min": -1.011650, "y_max": 2.623666},
             "margin_fraction": .03}
SHAPE = (1080, 1920, 3)


def tag(left=1150., top=300., size=180., tag_id=1):
    # Pupil's raw TR, TL, BL, BR order; no metric pose or error is invented.
    return SimpleNamespace(tag_id=tag_id, hamming=0, decision_margin=80.,
        corners_px=((left+size, top), (left, top), (left, top+size), (left+size, top+size)),
        center_px=(left+size/2., top+size/2.), T_C_T=None, pose_error=None)


def recorded_entry_tag(shift_x=0.):
    # Actual undistorted ID1 observation from 20260910T143223, before the old
    # controller wrongly aborted on vertical clipping while entering from left.
    corners = ((324.7035217285156, 459.53295898437494),
               (135.3188934326172, 466.12887573242193),
               (145.17221069335935, 720.2081909179688),
               (331.68634033203125, 743.0567626953124))
    return SimpleNamespace(tag_id=1, hamming=0, decision_margin=70.09883880615234,
        corners_px=tuple((x+shift_x, y) for x, y in corners),
        center_px=(229.08707508202826+shift_x, 598.3657857627543), T_C_T=None, pose_error=None)


class GeometryTests(unittest.TestCase):
    def test_padded_reference_projects_in_pixels_without_wall_size(self):
        result = project_pair_footprint(tag(700., 250., 150.).corners_px, SHAPE, REFERENCE)
        self.assertAlmostEqual(result["bbox_px"]["left"], 700. - 5.090921*150.)
        self.assertAlmostEqual(result["bbox_px"]["right"], 700. + 1.406036*150.)
        self.assertAlmostEqual(result["bbox_px"]["top"], 250. - 1.011650*150.)
        self.assertAlmostEqual(result["bbox_px"]["bottom"], 250. + 2.623666*150.)
        self.assertAlmostEqual(result["allowed_bbox_px"]["left"], 57.6)
        self.assertAlmostEqual(result["allowed_bbox_px"]["top"], 32.4)
        self.assertFalse(result["fits"])
        self.assertFalse(result["physical_distance_available"])

    def test_corner_permutations_have_same_geometric_projection(self):
        corners = tag().corners_px
        expected = project_pair_footprint(corners, SHAPE, REFERENCE)
        for permuted in itertools.permutations(corners):
            with self.subTest(corners=permuted):
                actual = project_pair_footprint(permuted, SHAPE, REFERENCE)
                self.assertEqual(actual, expected)

    def test_perspective_homography_maps_canonical_tag_to_observed_quad(self):
        quad = ((320., 210.), (100., 200.), (120., 420.), (290., 390.))
        canonical = {"roi_tag_bounds": {"x_min": 0., "x_max": 1., "y_min": 0., "y_max": 1.},
                     "margin_fraction": .03}
        result = project_pair_footprint(quad, SHAPE, canonical)
        expected = ((100., 200.), (320., 210.), (290., 390.), (120., 420.))
        for actual, wanted in zip(result["projected_corners_px"], expected):
            for value, target in zip(actual, wanted):
                self.assertAlmostEqual(value, target)

    def test_resolution_scaling_preserves_normalized_footprint(self):
        full = project_pair_footprint(tag().corners_px, SHAPE, REFERENCE)
        half = project_pair_footprint(tag(575., 150., 90.).corners_px, (540, 960, 3), REFERENCE)
        for key in ("left", "right", "top", "bottom"):
            self.assertAlmostEqual(full["bbox_px"][key], 2*half["bbox_px"][key])
        self.assertEqual(full["fits"], half["fits"])

    def test_invalid_reference_or_quad_never_returns_a_fabricated_roi(self):
        cases = [(((0., 0.),) * 4, SHAPE, REFERENCE),
                 (((0., 0.), (1., 1.), (2., 2.), (3., 3.)), SHAPE, REFERENCE),
                 (tag().corners_px, (0, 1920, 3), REFERENCE),
                 (tag().corners_px, SHAPE, {}),
                 (tag().corners_px, SHAPE, {**REFERENCE, "margin_fraction": 0.})]
        for corners, shape, reference in cases:
            with self.subTest(corners=corners, shape=shape), self.assertRaises(ValueError):
                project_pair_footprint(corners, shape, reference)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.gate = PairFramingGate(REFERENCE)
        self.sequence = 0

    def update(self, now, observed=None, *, speed=0., frame_age=.01, velocity_age=.01,
               key=None, shape=SHAPE, tags=None):
        self.sequence += 1
        if tags is None:
            tags = [] if observed is None else [observed]
        return self.gate.update(tags, now, frame_age, key or (1, self.sequence), shape, speed, velocity_age)

    def test_seek_only_expected_id1_with_left_cap(self):
        right, found = self.update(0., tags=[tag(tag_id=2), tag(tag_id=6)])
        self.assertEqual(right, -.6)
        self.assertIsNone(found)
        self.assertEqual(self.gate.diagnostic["state"], "SEEK_ID1")

    def test_tag_right_of_center_but_mock_left_clipped_still_requests_left(self):
        observed = tag(1050., 300., 200.)
        self.assertGreater(observed.center_px[0], SHAPE[1]/2.)
        right, found = self.update(0., observed)
        self.assertLess(right, 0.)
        self.assertGreaterEqual(right, -.6)
        self.assertIsNone(found)
        self.assertGreater(self.gate.diagnostic["footprint"]["overflow_px"]["left"], 0.)

    def test_seek_slows_as_pair_left_overflow_shrinks(self):
        far, _ = self.update(0., tag(700., 300., 200.))
        near, _ = self.update(.1, tag(1050., 300., 200.))
        self.assertLess(far, 0.)
        self.assertLess(near, 0.)
        self.assertLess(abs(near), abs(far))

    def test_tag_visibility_alone_never_confirms_clipped_pair(self):
        for i in range(15):
            right, found = self.update(i*.1, tag(1050., 300., 200.))
            self.assertIsNone(found)
            self.assertLessEqual(right, 0.)
        self.assertFalse(self.gate.diagnostic["capture_ready"])

    def test_fit_requires_one_second_zero_and_half_second_stable_fresh_frames(self):
        observed = tag()
        for i in range(10):
            right, found = self.update(i*.1, observed, speed=.2 if i < 4 else .02)
            self.assertEqual(right, 0.)
            self.assertIsNone(found)
        right, found = self.update(1., observed, speed=.02)
        self.assertEqual(right, 0.)
        self.assertIs(found, observed)
        self.assertEqual(self.gate.diagnostic["state"], "CAPTURE_READY")
        self.assertGreaterEqual(self.gate.diagnostic["stable_frame_hold_s"], .5)

    def test_velocity_instability_restarts_capture_hold(self):
        observed = tag()
        for i in range(11):
            self.update(i*.1, observed, speed=.2 if i == 9 else .01)
        self.assertFalse(self.gate.diagnostic["capture_ready"])
        right, found = self.update(1.5, observed)
        self.assertEqual(right, 0.)
        self.assertIs(found, observed)

    def test_overshoot_zeroes_until_settled_then_corrects_continuously(self):
        self.update(0., tag(900., 300., 200.), speed=.2)
        right, found = self.update(.1, tag(1730., 250., 150.), speed=.2)
        self.assertEqual(right, 0.)
        self.assertIsNone(found)
        self.assertEqual(self.update(.7, tag(1730., 250., 150.), speed=.2)[0], 0.)
        right, _ = self.update(.8, tag(1730., 250., 150.), speed=.02)
        self.assertGreater(right, 0.)
        self.assertLessEqual(right, .6)
        self.assertIsNone(self.gate.diagnostic["motion_valid_until_s"])
        self.assertEqual(self.gate.diagnostic["state"], "CONTINUOUS_CORRECTION")
        # A correction is held frame after frame, not chopped into one pulse.
        self.assertEqual(self.update(.9, tag(1730., 250., 150.))[0], right)
        self.assertEqual(self.update(1.4, tag(1730., 250., 150.))[0], right)

    def test_numeric_overshoot_trajectory_ends_in_full_pair_capture(self):
        commands = []
        commands.append(self.update(0., tag(1050., 300., 200.), speed=.2)[0])
        commands.append(self.update(.1, tag(1730., 250., 150.), speed=.2)[0])
        commands.append(self.update(.7, tag(1730., 250., 150.), speed=.01)[0])
        self.assertGreater(commands[-1], 0.)
        self.assertLessEqual(commands[-1], .6)
        fitted = tag(1650., 250., 150.)
        for i in range(12):
            right, found = self.update(.8 + i*.1, fitted)
            commands.append(right)
        self.assertTrue(self.gate.diagnostic["footprint"]["fits"])
        self.assertTrue(self.gate.diagnostic["capture_ready"])
        self.assertIs(found, fitted)
        self.assertEqual(commands[1], 0.)
        self.assertTrue(all(right == 0. for right in commands[3:]))

    def test_three_reversals_are_not_renewed_into_an_unbounded_correction(self):
        overshot, left_clipped = tag(1730., 250., 150.), tag(1050., 300., 200.)
        now = 0.
        for reversal in range(3):
            self.assertEqual(self.update(now, overshot)[0], 0.)
            right, _ = self.update(now+.6, overshot)
            self.assertGreater(right, 0.)
            self.assertLessEqual(right, .6)
            self.assertEqual(self.gate.diagnostic["reverse_pulses_used"], reversal+1)
            # Crossing back over the target zeroes and settles before reversing.
            self.assertEqual(self.update(now+.7, left_clipped)[0], 0.)
            self.assertLess(self.update(now+1.3, left_clipped)[0], 0.)
            now += 1.4
        self.assertEqual(self.update(now, overshot)[0], 0.)
        with self.assertRaisesRegex(PairFramingError, "reverse_pulse_limit"):
            self.update(now+.6, overshot)
        self.assertEqual(self.gate.diagnostic["reverse_pulses_used"], 3)

    def test_opposite_correction_side_never_causes_immediate_bang_bang(self):
        overshot, left_clipped = tag(1730., 250., 150.), tag(1050., 300., 200.)
        self.update(0., overshot)
        self.assertGreater(self.update(.6, overshot)[0], 0.)
        self.assertEqual(self.update(.7, left_clipped)[0], 0.)
        self.assertEqual(self.update(1., left_clipped)[0], 0.)
        right, _ = self.update(1.3, left_clipped)
        self.assertLess(right, 0.)
        self.assertGreaterEqual(right, -.6)

    def test_duplicate_frame_cannot_complete_capture_or_extend_a_pulse(self):
        fitted = tag()
        self.update(0., fitted, key=(1, 1))
        self.assertIsNone(self.update(1., fitted, key=(1, 1))[1])
        self.assertEqual(self.gate.diagnostic["state"], "WAIT_FRESH_FRAME")
        self.gate = PairFramingGate(REFERENCE)
        overshot = tag(1730., 250., 150.)
        self.update(0., overshot, key=(1, 1))
        self.assertGreater(self.update(.6, overshot, key=(1, 2))[0], 0.)
        self.assertEqual(self.update(.7, overshot, key=(1, 2))[0], 0.)
        self.assertIsNone(self.gate.diagnostic["motion_valid_until_s"])

    def test_missing_id1_after_observation_zeroes_without_blind_recovery(self):
        self.update(0., tag(1050., 300., 200.))
        for i in range(1, 4):
            self.assertEqual(self.update(i*.1, tags=[tag(tag_id=2)])[0], 0.)
        self.assertEqual(self.gate.diagnostic["state"], "WAIT_TARGET")

    def test_wide_or_vertical_clipped_pair_stops_without_other_axes(self):
        for observed, reason in ((tag(1100., 300., 400.), "too_wide"),
                                 (tag(1150., 70., 150.), "vertical_outside")):
            with self.subTest(reason=reason):
                self.gate = PairFramingGate(REFERENCE)
                if reason == "vertical_outside":
                    self.assertEqual(self.update(0., observed), (0., None))
                with self.assertRaisesRegex(PairFramingError, reason):
                    self.update(.6, observed)
                self.assertEqual(self.gate.diagnostic["requested_right_tilt_deg"], 0.)

    def test_stale_frame_velocity_bad_geometry_or_generation_change_stops(self):
        cases = ({"frame_age": .501}, {"frame_age": float("nan")},
                 {"velocity_age": .501}, {"speed": float("nan")}, {"speed": -.01},
                 {"shape": (0, 1920)}, {"key": (2, 2)})
        for change in cases:
            with self.subTest(change=change):
                self.gate = PairFramingGate(REFERENCE)
                self.update(0., tag(), key=(1, 1))
                with self.assertRaises(PairFramingError):
                    self.update(.1, tag(), **change)
                with self.assertRaisesRegex(PairFramingError, "already_stopped"):
                    self.update(.2, tag())

    def test_completed_capture_never_restarts_motion_if_pair_later_drifts(self):
        for i in range(11):
            self.update(i*.1, tag())
        self.assertTrue(self.gate.diagnostic["capture_ready"])
        right, found = self.update(1.2, tag(1050., 300., 200.))
        self.assertEqual(right, 0.)
        self.assertIsNone(found)
        self.assertEqual(self.gate.diagnostic["state"], "COMPLETE_HOVER")

    def test_diagnostic_explicitly_limits_claim_to_unvalidated_predicted_footprint(self):
        self.update(0., tag())
        diag = self.gate.diagnostic
        self.assertTrue(diag["predicted_footprint_only"])
        self.assertFalse(diag["actual_mock_detection_verified"])
        self.assertFalse(diag["physical_distance_available"])
        self.assertEqual(diag["policy_validation"], "unvalidated_field_trial")
        diag["state"] = "tampered"
        self.assertNotEqual(self.gate.diagnostic["state"], "tampered")

    def test_recorded_left_entry_continues_left_despite_vertical_photo_clipping(self):
        for index in range(4):
            right, found = self.update(index*.1, recorded_entry_tag())
            self.assertLess(right, 0.)
            self.assertGreaterEqual(right, -.6)
            self.assertIsNone(found)
            footprint = self.gate.diagnostic["footprint"]
            self.assertLess(footprint["bbox_px"]["left"], 0.)
            self.assertGreater(footprint["bbox_px"]["bottom"], 1080.)
            self.assertFalse(footprint["too_wide"])

    def test_strict_vertical_failure_requires_horizontal_framing_then_zero_settle(self):
        self.assertLess(self.update(0., recorded_entry_tag())[0], 0.)
        self.assertEqual(self.update(.1, recorded_entry_tag(600.)), (0., None))
        self.assertEqual(self.update(.7, recorded_entry_tag(600.), speed=.2), (0., None))
        self.assertFalse(self.gate.diagnostic["capture_ready"])
        with self.assertRaisesRegex(PairFramingError, "vertical_outside"):
            self.update(.8, recorded_entry_tag(600.))

    def test_vertical_crop_that_recovers_during_settle_requires_full_pair_capture(self):
        self.assertLess(self.update(0., recorded_entry_tag())[0], 0.)
        self.assertEqual(self.update(.1, recorded_entry_tag(600.)), (0., None))
        for index in range(10):
            right, found = self.update(.2+index*.1, tag())
            self.assertEqual(right, 0.)
        self.assertIsNotNone(found)
        self.assertTrue(self.gate.diagnostic["footprint"]["fits"])
        self.assertEqual(self.gate.diagnostic["photo_quality"], "full_reference_footprint")

    def test_edge_band_continues_left_at_center671_even_if_predicted_width_is1823(self):
        self.gate = PairFramingGate(REFERENCE, arrival_band=(.85, .95))
        size = 1823. / (REFERENCE["roi_tag_bounds"]["x_max"]-REFERENCE["roi_tag_bounds"]["x_min"])
        observed = tag(671.-size/2., 300., size)
        right, found = self.update(0., observed)
        self.assertLess(right, 0.)
        self.assertIsNone(found)
        self.assertTrue(self.gate.diagnostic["footprint"]["too_wide"])
        self.assertFalse(self.gate.diagnostic["arrival_ready"])

    def test_edge_band_arrives_with_full_actual_tag_despite_vertical_roi_crop(self):
        self.gate = PairFramingGate(REFERENCE, arrival_band=(.85, .95))
        observed = tag(.88*1920.-100., 700., 200.)
        for index in range(11):
            right, found = self.update(index*.1, observed)
            self.assertEqual(right, 0.)
            self.assertEqual(self.gate.diagnostic["arrival_ready"], index == 10)
        self.assertIs(found, observed)
        diagnostic = self.gate.diagnostic
        self.assertEqual(diagnostic["arrival_policy"], "tag_right_edge_band")
        self.assertEqual(diagnostic["photo_quality"], "tag_edge_arrival_pending_visual_review")
        self.assertFalse(diagnostic["footprint"]["fits"])
        self.assertTrue(diagnostic["footprint"]["vertical_outside"])
        self.assertFalse(diagnostic["actual_mock_detection_verified"])

    def test_edge_band_cannot_photo_actual_tag_clipped_at_top(self):
        observed = tag(.88*1920.-100., 0., 200.)
        self.gate = PairFramingGate(REFERENCE, arrival_band=(.85, .95))
        for index in range(15):
            self.assertEqual(self.update(index*.1, observed), (0., None))
            self.assertFalse(self.gate.diagnostic["arrival_ready"])
            self.assertFalse(self.gate.diagnostic["actual_tag_inside_frame"])

    def test_edge_band_clipped_right_tag_settles_and_retries_right_instead_of_photo(self):
        observed = tag(.94*1920.-100., 300., 200.)
        self.gate = PairFramingGate(REFERENCE, arrival_band=(.85, .95))
        self.assertEqual(self.update(0., observed), (0., None))
        self.assertEqual(self.update(.5, observed), (0., None))
        right, found = self.update(.6, observed)
        self.assertGreater(right, 0.)
        self.assertLessEqual(right, .6)
        self.assertIsNone(found)
        self.assertFalse(self.gate.diagnostic["arrival_ready"])
        self.assertFalse(self.gate.diagnostic["actual_tag_inside_frame"])
        self.assertGreater(self.update(.85, observed)[0], 0.)
        arrived = tag(.90*1920.-100., 300., 200.)
        for index in range(11):
            right, found = self.update(.95+index*.1, arrived)
            self.assertEqual(right, 0.)
        self.assertIs(found, arrived)
        self.assertTrue(self.gate.diagnostic["arrival_ready"])

    def test_edge_band_correction_holds_without_pulse_budget_within_caller_deadline(self):
        self.gate = PairFramingGate(REFERENCE, arrival_band=(.85, .95))
        overshot = tag(.97*1920.-75., 300., 150.)
        self.assertEqual(self.update(0., overshot), (0., None))
        for now in (.6, 1.0, 2.0, 3.0, 4.0):
            right, found = self.update(now, overshot)
            self.assertGreater(right, 0.)
            self.assertLessEqual(right, .6)
            self.assertIsNone(found)
            self.assertIsNone(self.gate.diagnostic["motion_valid_until_s"])
        self.assertEqual(self.gate.diagnostic["reverse_pulses_used"], 1)

    def test_edge_band_target_loss_stops_and_waits_for_fresh_reacquisition(self):
        self.gate = PairFramingGate(REFERENCE, arrival_band=(.85, .95))
        self.assertLess(self.update(0., tag(600., 300., 150.))[0], 0.)
        for index in range(1, 8):
            self.assertEqual(self.update(index*.1, tags=[]), (0., None))
            self.assertFalse(self.gate.diagnostic["arrival_ready"])
        observed = tag(.88*1920.-100., 300., 200.)
        for index in range(11):
            right, found = self.update(.8+index*.1, observed)
            self.assertEqual(right, 0.)
        self.assertIs(found, observed)

    def test_edge_band_overshoot_zeroes_before_gentle_right_pulse(self):
        self.gate = PairFramingGate(REFERENCE, arrival_band=(.85, .95))
        self.assertLess(self.update(0., tag(600., 300., 150.))[0], 0.)
        overshot = tag(.97*1920.-75., 300., 150.)
        self.assertEqual(self.update(.1, overshot, speed=.2), (0., None))
        self.assertEqual(self.update(.7, overshot, speed=.2), (0., None))
        right, found = self.update(.8, overshot, speed=.01)
        self.assertGreater(right, 0.)
        self.assertLessEqual(right, .6)
        self.assertIsNone(found)
        self.assertFalse(self.gate.diagnostic["arrival_ready"])

    def test_edge_band_keeps_fresh_velocity_frame_and_generation_guards(self):
        for changed in ({"frame_age": .501}, {"velocity_age": .501}, {"key": (2, 2)}):
            self.gate = PairFramingGate(REFERENCE, arrival_band=(.85, .95))
            self.update(0., tag(), key=(1, 1))
            with self.subTest(changed=changed), self.assertRaises(PairFramingError):
                self.update(.1, tag(), **changed)

    def start_recorded_right_loss(self):
        self.gate = PairFramingGate(REFERENCE, arrival_band=(.85, .95))
        self.assertLess(self.update(0., tag(571., 300., 200.), speed=.282843)[0], 0.)
        # Actual last-seen center from145104:1682.259/1920=87.62%.
        # Lost154ms later; horizontal speed first zero781ms afterlastseen.
        seen = tag(1682.259-100., 300., 200.)
        self.assertEqual(self.update(.2, seen, speed=.282843), (0., None))
        self.assertEqual(self.update(.354, tags=[], speed=.282843), (0., None))

    def test_recorded145104_right_loss_recovers_with_bounded_reverse_seek_then_captures(self):
        self.start_recorded_right_loss()
        right, found = self.update(.981, tags=[], speed=0.)
        self.assertEqual(right, .6)
        self.assertIsNone(found)
        self.assertFalse(self.gate.diagnostic["arrival_ready"])
        self.assertEqual(self.gate.diagnostic["state"], "BOUNDED_REACQUIRE_SEEK")
        self.assertIsNone(self.gate.diagnostic["motion_valid_until_s"])
        # The reacquire seek is held until the tag is seen again.
        self.assertEqual(self.update(1.231, tags=[])[0], .6)
        observed = tag(.90*1920.-100., 300., 200.)
        for index in range(11):
            right, found = self.update(1.331+index*.1, observed)
            self.assertEqual(right, 0.)
        self.assertIs(found, observed)
        self.assertTrue(self.gate.diagnostic["arrival_ready"])

    def test_missing_target_recovery_seeks_until_window_closes_then_waits_not_fatal(self):
        self.start_recorded_right_loss()
        for start in (.981, 1.831, 2.681, 3.531, 6.1):
            self.assertEqual(self.update(start, tags=[])[0], .6)
        self.assertEqual(self.update(6.3, tags=[]), (0., None))
        self.assertEqual(self.gate.diagnostic["state"], "WAIT_TARGET")
        self.assertIn("no_recent_direction_evidence", self.gate.diagnostic["reason"])
        self.assertEqual(self.gate.diagnostic["recovery_pulses_used"], 1)

    def test_missing_target_recovery_requires_recent_direction_evidence(self):
        self.gate = PairFramingGate(REFERENCE, arrival_band=(.85, .95))
        self.update(0., tag(.90*1920.-100., 300., 200.))  # No approach/trend evidence.
        for index in range(1, 15):
            self.assertEqual(self.update(index*.1, tags=[]), (0., None))
        self.start_recorded_right_loss()
        self.assertEqual(self.update(6.3, tags=[]), (0., None))
        self.assertIn("no_recent_direction_evidence", self.gate.diagnostic["reason"])

    def test_missing_target_correction_direction_follows_observed_left_exit(self):
        self.gate = PairFramingGate(REFERENCE, arrival_band=(.85, .95))
        overshot = tag(.97*1920.-75., 300., 150.)
        self.update(0., overshot)
        self.assertGreater(self.update(.6, overshot)[0], 0.)
        self.assertEqual(self.update(.7, tag(.10*1920.-50., 300., 100.)), (0., None))
        self.assertEqual(self.update(.8, tags=[]), (0., None))
        self.assertEqual(self.update(1.4, tags=[])[0], -.6)
        self.assertIn("near_left", self.gate.diagnostic["reason"])

    def test_capture_ready_can_reframe_when_photo_was_deferred_by_caller(self):
        self.gate = PairFramingGate(REFERENCE, arrival_band=(.85, .95))
        observed = tag(.90*1920.-100., 300., 200.)
        for index in range(11):
            self.update(index*.1, observed)
        self.assertTrue(self.gate.diagnostic["arrival_ready"])
        overshot = tag(.97*1920.-75., 300., 150.)
        right, found = self.update(1.1, overshot)
        self.assertGreater(right, 0.)
        self.assertLessEqual(right, .6)
        self.assertIsNone(found)
        self.assertFalse(self.gate.diagnostic["arrival_ready"])

    def test_capture_ready_speed_change_restarts_stable_hold_if_photo_deferred(self):
        self.gate = PairFramingGate(REFERENCE, arrival_band=(.85, .95))
        observed = tag(.90*1920.-100., 300., 200.)
        for index in range(11):
            self.update(index*.1, observed)
        self.assertEqual(self.update(1.1, observed, speed=.2), (0., None))
        self.assertEqual(self.update(1.2, observed, speed=0.), (0., None))
        self.assertEqual(self.update(1.6, observed, speed=0.), (0., None))
        self.assertIs(self.update(1.7, observed)[1], observed)


if __name__ == "__main__":
    unittest.main()
