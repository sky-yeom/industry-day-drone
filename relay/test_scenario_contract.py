"""Shared scenario and actual image fixtures used by both runtimes."""

import json
import hashlib
from pathlib import Path
import struct
import subprocess
import sys
import unittest
from relay import config
from relay.fixture_observations import FIXTURE_OBSERVATIONS
from relay.vision import validate_evidence


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = json.loads((ROOT / "data" / "emergency-triage.json").read_text("utf-8"))
LIVE_SCENARIO = json.loads((ROOT / "data" / "emergency-triage-live.json").read_text("utf-8"))


class ScenarioContractTests(unittest.TestCase):
    def test_three_unique_targets_and_false_alarm_reveals(self):
        people = SCENARIO["people"]
        self.assertEqual(
            [person["monitorId"] for person in people],
            ["monitor-1", "monitor-2", "monitor-3"],
        )
        self.assertEqual(len({person["id"] for person in people}), 3)
        self.assertFalse(people[2]["falseAlarm"])
        self.assertTrue(people[0]["falseAlarm"])
        self.assertTrue(people[1]["falseAlarm"])
        self.assertIn("바다", SCENARIO["people"][0]["siteName"])
        for person in people:
            with self.subTest(monitor=person["monitorId"]):
                self.assertTrue(person["clue"].strip())
                self.assertNotIn("deadlineMs", person)
                self.assertNotIn("dramaticRank", person)
                self.assertNotIn("clueRank", person)
                if person["falseAlarm"]:
                    self.assertTrue(person["falseAlarmReveal"].strip())
                    self.assertNotIn(person["falseAlarmReveal"], person["clue"])

    def test_reference_photo_is_retained_without_constraining_detection(self):
        for person in SCENARIO["people"]:
            with self.subTest(monitor=person["monitorId"]):
                reference = (ROOT / "public"
                             / person["targetAppearance"]["referenceImage"].lstrip("/")).read_bytes()
                self.assertEqual(reference[:8], b"\x89PNG\r\n\x1a\n")
                self.assertNotIn(hashlib.sha256(reference).hexdigest(), FIXTURE_OBSERVATIONS)

    def test_current_raster_images_have_pinned_per_person_observations(self):
        for person in SCENARIO["people"]:
            with self.subTest(monitor=person["monitorId"]):
                path = ROOT / "public" / person["image"].lstrip("/")
                image = path.read_bytes()
                self.assertEqual(image[:8], b"\x89PNG\r\n\x1a\n")
                self.assertTrue(all(0 < n <= 4096 for n in struct.unpack(">II", image[16:24])))
                observations = FIXTURE_OBSERVATIONS[hashlib.sha256(image).hexdigest()]
                self.assertEqual(len(observations), 3)
                self.assertGreater(len({item["appearance"]["shirtColor"] for item in observations}), 1)
                for observation in observations:
                    validate_evidence({"targetPresent": True, "description": observation["description"],
                                       "box": observation["box"], "confidence": 92})

    def test_monitor_images_are_distinct(self):
        positive = {
            (ROOT / "public" / person["image"].lstrip("/")).read_bytes()
            for person in SCENARIO["people"]
        }
        self.assertEqual(len(positive), 3)


class LiveScenarioContractTests(unittest.TestCase):
    def test_default_stays_the_committed_mock_scenario(self):
        self.assertEqual(config.SCENARIO_FILE, ROOT / "data" / "emergency-triage.json")

    def test_real_mode_launcher_selects_the_live_scenario_by_itself(self):
        script = (ROOT / "scripts" / "start-integrated.ps1").read_text(encoding="utf-8")
        real_block = script.rsplit("DRONE_REMOTE_SINGLE_REPLICA", 1)[-1]
        self.assertIn("RELAY_SCENARIO_FILE", real_block, "Real mode does not select a scenario")
        self.assertIn("emergency-triage-live.json", real_block,
                      "Real mode does not select the live scenario")

    def test_a_scenario_path_that_is_not_there_is_refused_by_name(self):
        missing = ROOT / "data" / "no-such-scenario.json"
        self.assertFalse(missing.exists())
        probe = (f"import os; os.environ['RELAY_SCENARIO_FILE'] = r'{missing}'; "
                 "import sys; sys.path.insert(0, r'" + str(ROOT) + "'); from relay import config")
        finished = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
        self.assertNotEqual(finished.returncode, 0)
        self.assertIn("RELAY_SCENARIO_FILE", finished.stderr)

    def test_live_scenario_matches_mock_shape(self):
        self.assertEqual(set(LIVE_SCENARIO), set(SCENARIO))
        self.assertEqual(len(LIVE_SCENARIO["people"]), len(SCENARIO["people"]))
        for live, mock in zip(LIVE_SCENARIO["people"], SCENARIO["people"]):
            with self.subTest(monitor=mock["monitorId"]):
                self.assertEqual(set(live), set(mock))


if __name__ == "__main__":
    unittest.main()
