from __future__ import annotations

from pathlib import Path
import unittest

from drone_nav.config import load_config
from drone_nav.localization import TagDetection, TagLocalizer
from drone_nav.patrol import (
    ExpectedTagTracker,
    OrderedTagGate,
    PatrolPhase,
    PatrolRouteTracker,
    TagGateAction,
    VisualProgressTracker,
    _floor_alignment_velocity,
    _horizontal_oa_ready,
    _upward_obstacle_blocks_climb,
    _wrap_degrees,
)
from drone_nav.protocol import Telemetry
from drone_nav.transforms import identity


CONFIG = Path(__file__).parents[1] / "config.sample.json"


def detection(tag_id: int, x: float, error: float = 0.01) -> TagDetection:
    return TagDetection(tag_id, identity(), error, (x, 500.0))


class PatrolRouteTests(unittest.TestCase):
    def test_complete_route_is_strict_and_returns_through_id2(self) -> None:
        route = PatrolRouteTracker((2, 1, 3))
        self.assertEqual(route.phase, PatrolPhase.FLOOR_HOME)
        self.assertEqual(route.expected_id, 0)

        route.confirm(0)
        self.assertEqual(route.expected_id, 2)
        route.confirm(2)
        self.assertEqual(route.phase, PatrolPhase.OUTBOUND)
        self.assertEqual(route.expected_id, 1)
        route.confirm(1)
        self.assertEqual(route.expected_id, 3)
        route.confirm(3)
        self.assertEqual(route.phase, PatrolPhase.TURNAROUND)
        self.assertIsNone(route.expected_id)

        route.begin_return()
        self.assertEqual(route.expected_id, 1)
        route.confirm(1)
        self.assertEqual(route.expected_id, 2)
        route.confirm(2)
        self.assertEqual(route.phase, PatrolPhase.FLOOR_ALIGN)
        self.assertEqual(route.expected_id, 0)
        route.confirm(0)
        self.assertEqual(route.phase, PatrolPhase.COMPLETE)

    def test_later_visible_tag_cannot_skip_expected_tag(self) -> None:
        route = PatrolRouteTracker((2, 1, 3))
        route.confirm(0)
        route.confirm(2)
        with self.assertRaises(ValueError):
            route.confirm(3)
        self.assertEqual(route.expected_id, 1)


class ExpectedTagTrackerTests(unittest.TestCase):
    def test_requires_expected_tag_centered_for_continuous_hold(self) -> None:
        tracker = ExpectedTagTracker(1, 0.3, 960.0, 100.0)
        self.assertIsNone(tracker.update([detection(3, 960.0)], 0.0))
        self.assertIsNone(tracker.update([detection(1, 1200.0)], 0.1))
        self.assertIsNone(tracker.update([detection(1, 1000.0)], 0.2))
        self.assertIsNone(tracker.update([detection(1, 1010.0)], 0.49))
        confirmed = tracker.update([detection(1, 990.0)], 0.51)
        self.assertIsNotNone(confirmed)
        self.assertEqual(confirmed.tag_id, 1)

    def test_leaving_center_resets_hold_timer(self) -> None:
        tracker = ExpectedTagTracker(2, 0.3, 900.0, 50.0)
        self.assertIsNone(tracker.update([detection(2, 900.0)], 1.0))
        self.assertIsNone(tracker.update([detection(2, 1000.0)], 1.2))
        self.assertIsNone(tracker.update([detection(2, 900.0)], 1.3))
        self.assertIsNone(tracker.update([detection(2, 900.0)], 1.59))
        self.assertIsNotNone(tracker.update([detection(2, 900.0)], 1.61))


class VisualProgressTrackerTests(unittest.TestCase):
    def test_left_travel_moves_previous_tag_right(self) -> None:
        tracker = VisualProgressTracker(1, "left", 4.0, 20.0, 1920.0)
        self.assertFalse(tracker.update([detection(1, 900.0)], 0.0))
        self.assertFalse(tracker.update([detection(1, 915.0)], 1.0))
        self.assertTrue(tracker.update([detection(1, 921.0)], 2.0))
        self.assertFalse(tracker.stalled(5.9))
        self.assertTrue(tracker.stalled(6.0))

    def test_return_travel_moves_previous_tag_left(self) -> None:
        tracker = VisualProgressTracker(3, "right", 4.0, 20.0, 1920.0)
        self.assertFalse(tracker.update([detection(3, 900.0)], 0.0))
        self.assertTrue(tracker.update([detection(3, 875.0)], 1.0))
        self.assertFalse(tracker.stalled(4.9))
        self.assertTrue(tracker.stalled(5.0))

    def test_wrong_direction_does_not_count_as_progress(self) -> None:
        tracker = VisualProgressTracker(1, "left", 4.0, 20.0, 1920.0)
        tracker.update([detection(1, 900.0)], 0.0)
        self.assertFalse(tracker.update([detection(1, 850.0)], 1.0))
        self.assertTrue(tracker.stalled(4.0))

    def test_reference_reaching_exit_retires_stall_check(self) -> None:
        tracker = VisualProgressTracker(1, "left", 4.0, 20.0, 1920.0)
        tracker.update([detection(1, 1800.0)], 0.0)
        self.assertTrue(tracker.update([detection(1, 1845.0)], 1.0))
        self.assertTrue(tracker.retired)
        self.assertFalse(tracker.stalled(100.0))


class OrderedTagGateTests(unittest.TestCase):
    def test_two_tags_do_not_block_expected_tag_centering(self) -> None:
        gate = OrderedTagGate(1, 3, 0.3)
        self.assertEqual(
            gate.update({1}, 0.0), TagGateAction.SEEK_NEXT
        )
        self.assertEqual(
            gate.update({1, 3}, 1.0), TagGateAction.HOLD_EXPECTED_ONLY
        )
        self.assertEqual(
            gate.update({3}, 1.1), TagGateAction.HOLD_EXPECTED_ONLY
        )
        self.assertEqual(
            gate.update({1, 3}, 1.29), TagGateAction.HOLD_EXPECTED_ONLY
        )
        self.assertEqual(
            gate.update({1, 3}, 1.3), TagGateAction.CENTER_EXPECTED
        )

    def test_departure_reappearing_does_not_reset_hold(self) -> None:
        gate = OrderedTagGate(3, 1, 0.3)
        self.assertEqual(
            gate.update({3, 1}, 0.0), TagGateAction.HOLD_EXPECTED_ONLY
        )
        self.assertEqual(
            gate.update({1}, 0.1), TagGateAction.HOLD_EXPECTED_ONLY
        )
        self.assertEqual(
            gate.update({3, 1}, 0.2), TagGateAction.HOLD_EXPECTED_ONLY
        )
        self.assertEqual(
            gate.update({1}, 0.4), TagGateAction.CENTER_EXPECTED
        )
        self.assertEqual(
            gate.update({1}, 0.69), TagGateAction.CENTER_EXPECTED
        )
        self.assertEqual(
            gate.update({1}, 0.7), TagGateAction.CENTER_EXPECTED
        )

    def test_expected_tag_alone_can_center_without_prior_overlap(self) -> None:
        gate = OrderedTagGate(2, 1, 0.3)
        self.assertEqual(
            gate.update({1}, 0.0), TagGateAction.HOLD_EXPECTED_ONLY
        )
        self.assertEqual(
            gate.update({1}, 0.3), TagGateAction.CENTER_EXPECTED
        )

    def test_unrelated_tags_are_ignored(self) -> None:
        gate = OrderedTagGate(2, 1, 0.3)
        self.assertEqual(
            gate.update({1, 3}, 0.0), TagGateAction.HOLD_EXPECTED_ONLY
        )
        self.assertEqual(
            gate.update({1, 3}, 0.3), TagGateAction.CENTER_EXPECTED
        )
        self.assertEqual(
            gate.update({1, 3}, 0.4), TagGateAction.CENTER_EXPECTED
        )


class PatrolConfigTests(unittest.TestCase):
    def test_sample_config_includes_separate_150mm_home_without_changing_legacy_patrol(self) -> None:
        config = load_config(CONFIG)
        self.assertEqual(set(config.tag_map), {0, 1, 2, 3, 6})
        self.assertTrue(all(tag.size_m == 0.15 for tag in config.tags))
        self.assertIsNone(config.tag_map[6].world_pose)
        self.assertEqual(config.patrol.route_ids, (2, 3, 1))
        self.assertEqual(config.patrol.visit_pause_s, 3.0)
        self.assertEqual(config.patrol.outbound_direction, "left")
        self.assertEqual(config.patrol.cruise_yaw_deg, -95.0)
        self.assertEqual(config.patrol.wall_target_y_px, config.camera.cy)
        self.assertEqual(config.patrol.leg_timeout_s, 90.0)
        self.assertEqual(config.patrol.recovery_max_angle_deg, 4.0)

    def test_unmapped_patrol_tags_are_ignored_by_legacy_localizer(self) -> None:
        config = load_config(CONFIG)
        localizer = TagLocalizer(config)
        self.assertIsNone(localizer.update([detection(1, 960.0)]))
        with self.assertRaisesRegex(ValueError, "no surveyed world pose"):
            localizer.candidate_pose(detection(3, 960.0))

    def test_cruise_yaw_wrap(self) -> None:
        self.assertAlmostEqual(_wrap_degrees(-181.0), 179.0)
        self.assertAlmostEqual(_wrap_degrees(181.0), -179.0)
        self.assertAlmostEqual(_wrap_degrees(-4.0), -4.0)

    def test_floor_alignment_uses_live_verified_camera_axis_signs(self) -> None:
        config = load_config(CONFIG)
        patrol = config.patrol
        self.assertIsNotNone(patrol)

        # ID0 above/left of its saved departure pixel must command forward/left.
        forward, right = _floor_alignment_velocity(-300.0, -300.0, patrol)
        self.assertGreater(forward, 0.0)
        self.assertLess(right, 0.0)
        self.assertLessEqual(
            (forward * forward + right * right) ** 0.5,
            patrol.landing_max_speed_mps + 1e-12,
        )

        # The opposite image error must reverse both corrections.
        forward, right = _floor_alignment_velocity(300.0, 300.0, patrol)
        self.assertLess(forward, 0.0)
        self.assertGreater(right, 0.0)


class ObstacleAvoidancePreflightTests(unittest.TestCase):
    def test_supported_horizontal_switch_must_read_false(self) -> None:
        self.assertTrue(
            _horizontal_oa_ready(
                Telemetry(
                    oa_type="CLOSE",
                    oa_horizontal_switch_support="SUPPORTED",
                    oa_horizontal_enabled=False,
                    oa_upward_switch_support="SUPPORTED",
                    oa_upward_enabled=False,
                )
            )
        )
        self.assertFalse(
            _horizontal_oa_ready(
                Telemetry(
                    oa_type="CLOSE",
                    oa_horizontal_switch_support="SUPPORTED",
                    oa_horizontal_enabled=True,
                    oa_upward_switch_support="SUPPORTED",
                    oa_upward_enabled=False,
                )
            )
        )

    def test_mini4_unsupported_subswitch_accepts_verified_close(self) -> None:
        self.assertTrue(
            _horizontal_oa_ready(
                Telemetry(
                    oa_type="CLOSE",
                    oa_horizontal_switch_support="UNSUPPORTED",
                    oa_horizontal_enabled=True,
                    oa_upward_switch_support="UNSUPPORTED",
                    oa_upward_enabled=True,
                )
            )
        )

    def test_close_itself_is_always_required(self) -> None:
        self.assertFalse(
            _horizontal_oa_ready(
                Telemetry(
                    oa_type="BRAKE",
                    oa_horizontal_switch_support="UNSUPPORTED",
                    oa_horizontal_enabled=True,
                    oa_upward_switch_support="UNSUPPORTED",
                    oa_upward_enabled=True,
                )
            )
        )

    def test_upward_switch_does_not_change_known_good_horizontal_preflight(self) -> None:
        self.assertTrue(
            _horizontal_oa_ready(
                Telemetry(
                    oa_type="CLOSE",
                    oa_horizontal_switch_support="SUPPORTED",
                    oa_horizontal_enabled=False,
                    oa_upward_switch_support="SUPPORTED",
                    oa_upward_enabled=True,
                )
            )
        )

    def test_upward_climb_block_requires_fresh_close_range(self) -> None:
        self.assertTrue(
            _upward_obstacle_blocks_climb(
                Telemetry(
                    oa_upward_distance_mm=1200.0,
                    oa_obstacle_data_age_s=0.1,
                )
            )
        )
        self.assertFalse(
            _upward_obstacle_blocks_climb(
                Telemetry(
                    oa_upward_distance_mm=60000.0,
                    oa_obstacle_data_age_s=0.1,
                )
            )
        )
        self.assertFalse(
            _upward_obstacle_blocks_climb(
                Telemetry(
                    oa_upward_distance_mm=1200.0,
                    oa_obstacle_data_age_s=1.1,
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
