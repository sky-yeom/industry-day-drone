"""Construction Site Safety ("construction" scenario kind) coverage,
mirroring the shared-prompt/order-suggestion flow already exercised for
"security" in test_survey.py, but for the no-deadline, headwear-based
safety-violation scenario."""

from types import SimpleNamespace
import unittest

from relay.survey import SurveySession


HEADWEAR_PROMPT = "핑크색 작업복을 입고 안전모를 쓰지 않은 사람을 찾아 주세요."
HEADWEAR_APPEARANCE = [
    {"attribute": "shirtColor", "operator": "include", "values": ["pink"]},
    {"attribute": "headwear", "operator": "include", "values": ["bare"]},
]
PROMPT_ARGS = {"prompt_text": HEADWEAR_PROMPT, "appearance_constraints": HEADWEAR_APPEARANCE,
               "unsupported_appearance": []}
POSITIVE = {"targetPresent": True, "description": "핑크색 작업복을 입고 안전모를 쓰지 않은 사람이 통로에 있습니다.",
           "box": [0.1, 0.1, 0.2, 0.3], "confidence": 88}
NEGATIVE = {"targetPresent": False, "description": "요청한 조건에 맞는 사람이 보이지 않습니다.",
           "box": None, "confidence": 12}


def capture(monitor="monitor-1", id="frame-1", image_bytes=b"pixels"):
    return SimpleNamespace(id=id, monitor_id=monitor, image_bytes=image_bytes,
                           content_type="image/png", image_url="data:image/png;base64,cGl4ZWxz")


def detect(session, monitor, evidence, frame_id="frame-1"):
    session.set_operation("capturing", monitor, session.run_id)
    frame = capture(monitor, frame_id)
    assert session.add_capture(frame, session.run_id)
    session.analyzing(frame.id, session.run_id)
    return session.apply_detection(session.run_id, frame.id, evidence)


class ConstructionScenarioTests(unittest.TestCase):
    def setUp(self):
        self.session = SurveySession(kind="construction")

    def test_shared_prompt_confirms_all_three_zones_at_once(self):
        s = self.session
        outcome = s.confirm_prompt(**PROMPT_ARGS)
        self.assertTrue(outcome["ok"])
        self.assertEqual(s.data["promptPhase"], "confirmed")
        for person in s.data["people"]:
            self.assertTrue(person["promptConfirmed"])
            self.assertEqual(person["promptText"], HEADWEAR_PROMPT)
            self.assertEqual(person["appearanceConstraints"], HEADWEAR_APPEARANCE)
        # A single shared confirmation already produced both AI-proposed
        # orders (no per-zone looping, unlike "triage").
        self.assertEqual(len(s.data["dangerOrder"]), 3)
        self.assertEqual(len(s.data["vulnerableAdjustedOrder"]), 3)

    def test_noticeable_and_careful_orders_follow_scenario_ranks(self):
        s = self.session
        s.confirm_prompt(**PROMPT_ARGS)
        # From data/construction-safety.json: noticeableRank 2/1/3,
        # carefulRank 3/2/1 for zone-1/2/3 (monitor-1/2/3).
        self.assertEqual(s.data["dangerOrder"], ["monitor-2", "monitor-1", "monitor-3"])
        self.assertEqual(s.data["vulnerableAdjustedOrder"], ["monitor-3", "monitor-2", "monitor-1"])

    def test_no_deadline_mechanic_zones_never_expire(self):
        s = self.session
        s.confirm_prompt(**PROMPT_ARGS)
        for person in s.data["people"]:
            # Effectively infinite: this is the mechanism (not a new "no
            # deadline" code path) that keeps expire()/deterioration from
            # ever firing for this scenario kind in realistic playtime.
            self.assertGreater(person["deadlineMs"], 10 ** 8)

    def test_matched_zone_resolves_reported_not_injured_or_caught(self):
        s = self.session
        s.confirm_prompt(**PROMPT_ARGS)
        s.select_stop("monitor-1")
        s.select_stop("monitor-2")
        s.select_stop("monitor-3")
        s.confirm_route()
        s.launch_mission()
        self.assertTrue(detect(s, "monitor-1", POSITIVE))
        person = s.person("monitor-1")
        self.assertEqual(person["outcome"], "reported")
        self.assertIsNotNone(person["resolvedAtMs"])

    def test_exhausted_attempts_without_match_resolve_not_found(self):
        s = self.session
        s.confirm_prompt(**PROMPT_ARGS)
        s.select_stop("monitor-1")
        s.select_stop("monitor-2")
        s.select_stop("monitor-3")
        s.confirm_route()
        s.launch_mission()
        max_attempts = s.scenario["maxDetectionAttempts"]
        for attempt in range(max_attempts):
            frame_id = f"frame-{attempt}"
            detect(s, "monitor-1", NEGATIVE, frame_id=frame_id)
        person = s.person("monitor-1")
        self.assertEqual(person["outcome"], "not_found")

    def test_debrief_mentions_reported_and_not_found_zones(self):
        s = self.session
        s.confirm_prompt(**PROMPT_ARGS)
        s.select_stop("monitor-1")
        s.select_stop("monitor-2")
        s.select_stop("monitor-3")
        s.confirm_route()
        s.launch_mission()
        detect(s, "monitor-1", POSITIVE)
        max_attempts = s.scenario["maxDetectionAttempts"]
        for attempt in range(max_attempts):
            detect(s, "monitor-2", NEGATIVE, frame_id=f"frame-m2-{attempt}")
        detect(s, "monitor-3", POSITIVE, frame_id="frame-m3")
        # All three zones resolved (reported/not_found/reported), so the
        # mission auto-completes without needing an explicit abort.
        self.assertEqual(s.phase, "complete")
        debrief = s.debrief()
        self.assertIn("안전관리자", debrief)
        self.assertIn("찾지 못했", debrief)
