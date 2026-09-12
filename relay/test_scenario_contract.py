"""Shared scenario and actual image fixtures used by both runtimes."""

import json
import hashlib
from pathlib import Path
import struct
import unittest
from relay.fixture_observations import FIXTURE_OBSERVATIONS
from relay.vision import validate_evidence


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = json.loads((ROOT / "data" / "emergency-triage.json").read_text("utf-8"))


class ScenarioContractTests(unittest.TestCase):
    def test_three_unique_targets_and_finite_urgency_windows(self):
        people = SCENARIO["people"]
        self.assertEqual(
            [person["monitorId"] for person in people],
            ["monitor-1", "monitor-2", "monitor-3"],
        )
        self.assertEqual(len({person["id"] for person in people}), 3)
        self.assertLess(people[2]["deadlineMs"], people[0]["deadlineMs"])
        self.assertLess(people[0]["deadlineMs"], people[1]["deadlineMs"])
        self.assertIn("바다", people[0]["clue"])
        self.assertEqual(people[0]["deadlineMs"], 28000)
        self.assertEqual(SCENARIO["injuryWindowMs"], 5000)
        self.assertEqual(people[1]["deadlineMs"], 45000)
        self.assertFalse(people[0]["initiallyInjured"])
        self.assertTrue(people[1]["initiallyInjured"])
        for person in people:
            with self.subTest(monitor=person["monitorId"]):
                self.assertTrue(person["clue"].strip())
                self.assertGreater(person["deadlineMs"], 0)
                self.assertGreater(person["deadlineMs"], SCENARIO["injuryWindowMs"])

    def test_nominal_priority_route_fits_each_window(self):
        stop_cost = sum(
            SCENARIO[key] for key in ("travelMs", "captureMs", "mockAnalysisMs")
        )
        self.assertGreater(stop_cost, 0)
        by_monitor = {person["monitorId"]: person for person in SCENARIO["people"]}
        for position, monitor in enumerate(("monitor-3", "monitor-1", "monitor-2"), 1):
            self.assertLess(position * stop_cost, by_monitor[monitor]["deadlineMs"])
        self.assertGreaterEqual(2 * stop_cost, by_monitor["monitor-3"]["deadlineMs"])
        self.assertGreaterEqual(3 * stop_cost, by_monitor["monitor-1"]["deadlineMs"])

    def test_reference_photo_is_retained_without_constraining_detection(self):
        reference = (ROOT / "public" / SCENARIO["targetAppearance"]["referenceImage"].lstrip("/")).read_bytes()
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
                                       "box": observation["box"]})

    def test_monitor_images_are_distinct(self):
        positive = {
            (ROOT / "public" / person["image"].lstrip("/")).read_bytes()
            for person in SCENARIO["people"]
        }
        self.assertEqual(len(positive), 3)


if __name__ == "__main__":
    unittest.main()
