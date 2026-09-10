"""Shared scenario and actual image fixtures used by both runtimes."""

import json
from pathlib import Path
import struct
import unittest
import xml.etree.ElementTree as ET


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

    def test_shared_appearance_and_reference_match_each_scene(self):
        appearance = SCENARIO["targetAppearance"]
        self.assertEqual(appearance["shirtColor"], "green")
        self.assertEqual(appearance["hairColor"], "brown")
        self.assertEqual(appearance["garment"], "t-shirt")
        self.assertIn("갈색", appearance["description"])
        for name in ("monitor-1", "monitor-2", "monitor-3", "reference-person"):
            svg = (ROOT / "public" / "monitors" / f"{name}.svg").read_text("utf-8")
            self.assertIn('fill="#4bb98a"', svg)
            self.assertIn('fill="#6b4634"', svg)
        reference = (ROOT / "public" / appearance["referenceImage"].lstrip("/")).read_bytes()
        self.assertEqual(reference[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", reference[16:24]), (400, 400))

    def test_real_raster_images_and_normalized_mock_boxes(self):
        for person in SCENARIO["people"]:
            with self.subTest(monitor=person["monitorId"]):
                path = ROOT / "public" / person["image"].lstrip("/")
                image = path.read_bytes()
                self.assertEqual(image[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(struct.unpack(">II", image[16:24]), (640, 400))
                self.assertLess(len(image), 1024 * 1024)
                ET.parse(path.with_suffix(".svg"))
                x, y, width, height = person["mockBox"]
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertGreater(width, 0)
                self.assertGreater(height, 0)
                self.assertLessEqual(x + width, 1)
                self.assertLessEqual(y + height, 1)

    def test_negative_images_are_distinct_from_rescue_frames(self):
        positive = {
            (ROOT / "public" / person["image"].lstrip("/")).read_bytes()
            for person in SCENARIO["people"]
        }
        self.assertEqual(len(positive), 3)
        for name in ("empty-scene", "wrong-target", "wrong-hair"):
            image = (ROOT / "public" / "monitors" / f"{name}.png").read_bytes()
            self.assertEqual(image[:8], b"\x89PNG\r\n\x1a\n")
            self.assertNotIn(image, positive)


if __name__ == "__main__":
    unittest.main()
