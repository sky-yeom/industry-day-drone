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
# One real stop measured in the field: fly the leg, hold the pair gate, shoot the
# proof pair, then wait on Azure. The full three-stop route ran 77-100s, so this
# is the worst observed stop rounded up. The mock timeline is a third of it,
# which is why the two scenarios cannot share one set of deadlines.
LIVE_STOP_MS = 34000


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


class LiveScenarioContractTests(unittest.TestCase):
    """The live scenario exists because the mock clock kills real flights.

    All three mock deadlines expire at 45s, which marks the run complete and
    stops a drone that is still 30-55s from home. Only the deadlines may differ:
    everything else is the same training run, so any other drift is a bug.
    """

    def test_default_stays_the_committed_mock_scenario(self):
        self.assertEqual(config.SCENARIO_FILE, ROOT / "data" / "emergency-triage.json")

    def test_real_mode_launcher_selects_the_live_scenario_by_itself(self):
        # start-integrated.ps1 clears every RELAY_* process variable before it
        # applies the settings file, so exporting RELAY_SCENARIO_FILE in the
        # shell silently does nothing. Real mode has to choose the live file,
        # or the mock stopwatch lands the drone on the first voice flight.
        script = (ROOT / "scripts" / "start-integrated.ps1").read_text(encoding="utf-8")
        # The name also appears in the mock branch's removal list, so anchor on
        # its last occurrence, which is the real-mode assignment.
        real_block = script.rsplit("DRONE_CONTROL_FIELD_REFERENCE", 1)[-1].split("\n}", 1)[0]
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

    def test_live_scenario_differs_from_the_mock_one_only_in_its_clock(self):
        timing = {"deadlineMs"}
        self.assertEqual(set(LIVE_SCENARIO), set(SCENARIO))
        for key, value in SCENARIO.items():
            if key not in ("people", "injuryWindowMs"):
                with self.subTest(key=key):
                    self.assertEqual(LIVE_SCENARIO[key], value)
        self.assertEqual(len(LIVE_SCENARIO["people"]), len(SCENARIO["people"]))
        for live, mock in zip(LIVE_SCENARIO["people"], SCENARIO["people"]):
            with self.subTest(monitor=mock["monitorId"]):
                self.assertEqual(set(live), set(mock))
                self.assertEqual({k: v for k, v in live.items() if k not in timing},
                                 {k: v for k, v in mock.items() if k not in timing})

    def test_live_windows_outlast_a_real_flight_without_losing_the_triage_order(self):
        people = LIVE_SCENARIO["people"]
        self.assertLess(people[2]["deadlineMs"], people[0]["deadlineMs"])
        self.assertLess(people[0]["deadlineMs"], people[1]["deadlineMs"])
        for person in people:
            with self.subTest(monitor=person["monitorId"]):
                self.assertGreater(person["deadlineMs"], LIVE_SCENARIO["injuryWindowMs"])
                self.assertGreater(person["deadlineMs"], LIVE_STOP_MS)
        by_monitor = {person["monitorId"]: person for person in people}
        for position, monitor in enumerate(("monitor-3", "monitor-1", "monitor-2"), 1):
            self.assertLess(position * LIVE_STOP_MS, by_monitor[monitor]["deadlineMs"])
        # The whole route has to be survivable, or the watchdog lands the drone
        # mid-flight exactly as the mock clock did.
        self.assertGreater(max(p["deadlineMs"] for p in people), 3 * LIVE_STOP_MS)
        # ...but it still has to hurt: the tightest window must be unreachable
        # if it is visited last, or the triage choice carries no weight.
        self.assertLess(by_monitor["monitor-3"]["deadlineMs"], 3 * LIVE_STOP_MS)


if __name__ == "__main__":
    unittest.main()
