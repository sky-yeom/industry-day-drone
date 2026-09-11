"""Three-tag wall (2, 1, 6) at 1.2 m: profile validation, plan and configuration only."""
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch

from test_standalone_tag_shuttle import StandaloneTestCase, config, profile, shuttle
from test_id1_pair_integration import REFERENCE

PROFILE_PATH = Path(__file__).resolve().parents[2] / "trials" / "profiles" / "standalone_tag_216.json"


def profile_216(**changes):
    return profile(**{"wall_ids_left_to_right": [2, 1, 6], "route_ids": [6, 1, 2, 1, 6],
                      "target_height_m": 1.2, **changes})


class Profile216Tests(StandaloneTestCase):
    def test_shipped_profile_file_is_the_three_tag_layout_at_1p2m(self):
        shipped = json.loads(PROFILE_PATH.read_text(encoding="utf-8-sig"))
        self.assertEqual(shuttle.validate_profile(shipped), shipped)
        self.assertEqual(shipped["wall_ids_left_to_right"], [2, 1, 6])
        self.assertEqual(shipped["route_ids"], [6, 1, 2, 1, 6])
        self.assertEqual(shipped["target_height_m"], 1.2)
        self.assertTrue(shipped["layout_confirmed"])

    def test_plan_has_two_left_legs_then_two_right_legs_and_two_mock_photos(self):
        full = shuttle.plan(profile_216(), REFERENCE, continue_patrol=True)
        self.assertEqual([leg["direction"] for leg in full["legs"]], ["left", "left", "right", "right"])
        self.assertEqual(full["active_route_ids"], [6, 1, 2, 1, 6])
        self.assertEqual(full["pair_capture_ids"], [1, 2])
        pair_only = shuttle.plan(profile_216(), REFERENCE)
        self.assertEqual(pair_only["active_route_ids"], [6, 1])

    def test_configuration_uses_outbound_route_6_1_2_and_1p2m_cruise(self):
        with patch.object(shuttle, "load_config", return_value=config()):
            prepared = shuttle.prepare_config("unused", profile_216(), "127.0.0.2")
        self.assertEqual(prepared.patrol.route_ids, (6, 1, 2))
        self.assertEqual(prepared.patrol.cruise_altitude_m, 1.2)
        self.assertEqual(shuttle.outbound_leg_count(profile_216()), 2)

    def test_structural_route_rules_still_reject_bad_layouts(self):
        for changes in ({"route_ids": [6, 2, 1, 2, 6]},          # skips the adjacent tag
                        {"route_ids": [6, 1, 2, 1]},             # does not return home
                        {"route_ids": [6, 1, 6]},                # never reaches the far tag
                        {"wall_ids_left_to_right": [2, 6, 1]},   # home is not rightmost
                        {"wall_ids_left_to_right": [2, 1, 0, 6]},  # floor tag on the wall
                        {"target_height_m": .9}, {"target_height_m": 1.7}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                shuttle.validate_profile(profile_216(**changes))

    def test_legacy_four_tag_profile_and_directions_are_unchanged(self):
        legacy = shuttle.plan(profile())
        self.assertEqual([leg["direction"] for leg in legacy["legs"]], ["left"] * 3 + ["right"] * 3)
        self.assertEqual(shuttle.planned_direction(2, 1, [2, 1, 6], [6, 1, 2, 1, 6]), "right")
        self.assertEqual(shuttle.planned_direction(1, 2, [2, 1, 6], [6, 1, 2, 1, 6]), "left")
        with self.assertRaises(ValueError):
            shuttle.planned_direction(6, 2, [2, 1, 6], [6, 1, 2, 1, 6])

    def test_climb_command_accepts_settling_slightly_above_a_1p2m_target(self):
        from bounded_sonar_climb import climb_command
        self.assertEqual(climb_command(1.3, .01, 1.2), 0.)
        self.assertEqual(climb_command(1.2, .01, 1.2), 0.)
        self.assertGreater(climb_command(1.1, .01, 1.2), 0.)
        with self.assertRaises(RuntimeError):
            climb_command(1.6, .01, 1.2)
