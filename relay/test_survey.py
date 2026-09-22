from copy import deepcopy
import itertools
from types import SimpleNamespace
import unittest

from relay.survey import MONITOR_IDS, SCENARIO, SurveySession, validate_evidence
from relay.appearance import REVISION_REQUEST


POSITIVE = {"targetPresent": True, "description": "초록색 티셔츠를 입고 갈색 머리를 한 대상자가 보입니다.", "box": [0.1, 0.1, 0.2, 0.3], "confidence": 92}
NEGATIVE = {"targetPresent": False, "description": "대상자가 보이지 않습니다.", "box": None, "confidence": 15}
SEARCH_PROMPT = "초록색 티셔츠를 입고 갈색 머리를 한 사람을 찾아 주세요."
APPEARANCE = [
    {"attribute": "shirtColor", "operator": "include", "values": ["green"]},
    {"attribute": "hairColor", "operator": "include", "values": ["brown"]},
    {"attribute": "garment", "operator": "include", "values": ["t-shirt"]},
]
PROMPT_ARGS = {"prompt_text": SEARCH_PROMPT, "appearance_constraints": APPEARANCE,
               "unsupported_appearance": []}


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, ms):
        self.now += ms / 1000


def capture(monitor="monitor-1", id="frame-1", image_bytes=b"pixels"):
    return SimpleNamespace(id=id, monitor_id=monitor, image_bytes=image_bytes,
                           content_type="image/png", image_url="data:image/png;base64,cGl4ZWxz")


def confirm_all(session, prompt_args=PROMPT_ARGS):
    """Confirm every site's prompt (relay-controlled sequencing), returning the final outcome."""
    outcome = None
    for _ in MONITOR_IDS:
        outcome = session.confirm_prompt(**prompt_args)
    return outcome


def ready(session, route=("monitor-3", "monitor-1", "monitor-2")):
    confirm_all(session)
    session.select_stop(route[0])
    session.select_stop(route[1])
    session.confirm_route()


def detect(session, monitor, evidence=POSITIVE, frame_id="frame-1"):
    session.set_operation("capturing", monitor, session.run_id)
    frame = capture(monitor, frame_id)
    assert session.add_capture(frame, session.run_id)
    session.analyzing(frame.id, session.run_id)
    return session.apply_detection(session.run_id, frame.id, evidence)


class SurveyTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.session = SurveySession(clock=self.clock)

    def test_route_ready_is_not_launch_and_clear_prelaunch_only(self):
        s = self.session
        self.assertFalse(s.launch_mission()["ok"])
        confirm_all(s, {"prompt_text": SEARCH_PROMPT})
        self.assertFalse(s.select_stop(["monitor-1"])["ok"])
        self.assertFalse(s.select_stop("불난 집")["ok"])
        self.assertTrue(s.select_stop("monitor-3")["ok"])
        self.assertFalse(s.select_stop("monitor-3")["ok"])
        s.select_stop("monitor-2")
        self.assertEqual(s.state.draftRoute, ["monitor-3", "monitor-2", "monitor-1"])
        self.assertEqual(s.phase, "briefing")
        s.confirm_route()
        self.clock.advance(99999)
        self.assertEqual(s.elapsed_ms(), 0)
        self.assertEqual(s.phase, "ready")
        s.clear_route()
        self.assertEqual(s.state.draftRoute, [])
        ready(s)
        s.launch_mission()
        self.assertFalse(s.clear_route()["ok"])
        self.assertFalse(s.select_stop("monitor-1")["ok"])
        self.assertFalse(s.confirm_route()["ok"])

    def test_duplicate_launch_does_not_reset_clock(self):
        ready(self.session)
        self.session.launch_mission()
        self.clock.advance(7000)
        self.assertTrue(self.session.launch_mission()["ok"])
        self.assertEqual(self.session.elapsed_ms(), 7000)

    def test_prelaunch_voice_facts_do_not_reveal_timing(self):
        from relay import tools

        for prepared in (False, True):
            if prepared:
                ready(self.session)
            facts = self.session.get_state()["facts"]
            self.assertIn("출발 전", facts)
            self.assertNotIn("경과 시간", facts)
            self.assertNotRegex(facts, r"\d+(?:\.\d+)?초")
        self.assertIn("시간의 숫자를 말하지 않습니다", tools.SYSTEM_PROMPT)
        self.assertIn("익수자", tools.SYSTEM_PROMPT)

    def test_participant_prompt_required_before_every_route_launch_entry(self):
        s = self.session
        self.assertEqual(s.snapshot()["userPromptText"], "")
        self.assertEqual(s.snapshot()["promptPhase"], "briefing")
        self.assertFalse(s.select_stop("monitor-3")["ok"])
        s.state.draftRoute = ["monitor-3", "monitor-2", "monitor-1"]
        self.assertFalse(s.confirm_route()["ok"])
        s.data["missionPhase"] = "ready"
        self.assertFalse(s.launch_mission()["ok"])
        self.assertEqual(s.elapsed_ms(), 0)
        for invalid in ("", "   ", None, 42, "가" * 2001):
            self.assertFalse(s.confirm_prompt(invalid)["ok"])
        self.assertTrue(s.confirm_prompt("  사람을 찾아줘  ")["ok"])
        self.assertEqual(s.data["userPromptText"], "사람을 찾아줘")
        self.assertEqual(s.data["promptPhase"], "briefing")
        self.assertFalse(s.select_stop("monitor-3")["ok"])
        self.assertTrue(s.confirm_prompt("사람을 찾아줘")["ok"])
        self.assertTrue(s.confirm_prompt("사람을 찾아줘")["ok"])
        self.assertEqual(s.data["promptPhase"], "confirmed")
        self.assertTrue(all(p["outcome"] is None for p in s.data["people"]))
        s.confirm_route()
        s.launch_mission()
        self.assertFalse(s.confirm_prompt("출발 후 변경")["ok"])
        self.assertEqual(s.data["userPromptText"], "사람을 찾아줘")

    def test_unassessable_descriptions_require_neutral_revision_before_confirmation(self):
        for mode in ("mock", "azure"):
            for method in ("prepare_prompt", "confirm_prompt"):
                session = SurveySession(mode=mode)
                outcome = getattr(session, method)("백인")
                self.assertFalse(outcome["ok"])
                self.assertEqual(outcome["facts"], REVISION_REQUEST)
                self.assertIsNone(session.pending_prompt)
                self.assertEqual(session.data["promptPhase"], "briefing")
                self.assertFalse(session.select_stop("monitor-1")["ok"])

    def test_mock_ignores_unassessable_extras_but_azure_keeps_visual_text(self):
        """Extra descriptors the mock vision engine can't verify (e.g. glasses)
        no longer block prepare/confirm_prompt in mock mode — only the
        recognizable color/garment/hair signal is used for matching, and
        the raw text (including the unassessable part) is still recorded."""
        mock, azure = SurveySession(), SurveySession(mode="azure")
        self.assertTrue(mock.prepare_prompt("안경 쓴 사람", [], ["안경"])["ok"])
        self.assertTrue(mock.confirm_prompt("초록색 옷과 안경을 쓴 사람")["ok"])
        self.assertEqual(mock.data["userPromptText"], "초록색 옷과 안경을 쓴 사람")
        self.assertTrue(azure.prepare_prompt("안경 쓴 사람", [], ["안경"])["ok"])
        self.assertEqual(azure.pending_prompt["prompt_text"], "안경 쓴 사람")
        self.assertTrue(mock.prepare_prompt("빨간 옷")["ok"])

    def test_cases_disclosed_in_successful_prompt_confirmation_only(self):
        from relay import tools

        self.assertEqual(tools.GREETING,
                         "안녕! 난 Gibby야! 너는 119종합상황실 소속 상황요원이고, 방금 익명 문자로 사진이랑 같이 위급 신고가 "
                         "들어왔어. 바다, 잔해 아래, 불이 난 집, 이렇게 세 곳에 사람이 있는데 드론은 한 대뿐이라 한 곳씩 차례로 "
                         "찾아서 위치를 신고해야 해. 네가 오더만 내려주면 내가 드론 보낼게! 먼저 "
                         "바다에서 어떤 사람을 찾아줘야 하는지 말해줄 수 있어?")
        self.assertEqual(tools.OPENING_QUESTION, "바다에서 어떤 사람을 찾아줘야 하는지 말해줄 수 있어?")
        self.assertIn("하나씩", SCENARIO["briefing"][1])
        for hint in ("머리", "티셔츠", "초록", "갈색", "같은 외형"):
            self.assertNotIn(hint, tools.GREETING)
            self.assertNotIn(hint, " ".join(SCENARIO["briefing"]))
        rejected = self.session.confirm_prompt("")
        first = self.session.confirm_prompt(SEARCH_PROMPT)
        second = self.session.confirm_prompt(SEARCH_PROMPT)
        confirmed = self.session.confirm_prompt(SEARCH_PROMPT)
        for person in SCENARIO["people"]:
            self.assertNotIn(person["clue"], tools.GREETING)
            self.assertNotIn(person["clue"], rejected["facts"])
            self.assertNotIn(person["clue"], first["facts"])
            self.assertNotIn(person["clue"], second["facts"])
            self.assertIn(person["clue"], confirmed["facts"])
        self.assertIn("설명한 뒤", confirmed["ask"])

    def test_wrong_appearance_is_saved_without_correcting_or_blocking_route(self):
        wrong = [{"attribute": "hairColor", "operator": "include", "values": ["blond"]}]
        outcome = self.session.confirm_prompt("금발인 사람을 찾아줘", wrong, [])
        self.assertTrue(outcome["ok"])
        self.assertEqual(self.session.data["userPromptText"], "금발인 사람을 찾아줘")
        self.assertEqual(self.session.data["appearanceConstraints"], wrong)
        self.assertEqual(self.session.person("monitor-1")["appearanceConstraints"], wrong)
        confirm_all(self.session)
        self.assertTrue(self.session.select_stop("monitor-3")["ok"])
        self.assertTrue(all(person["outcome"] is None for person in self.session.data["people"]))

    def test_confirming_prompt_sets_a_deterministic_confidence_and_reasoning(self):
        s = self.session
        self.assertIsNone(s.data["promptConfidence"])
        self.assertEqual(s.data["promptConfidenceReason"], "")
        outcome = s.confirm_prompt(**PROMPT_ARGS)
        self.assertTrue(outcome["ok"])
        self.assertIsInstance(s.data["promptConfidence"], int)
        self.assertTrue(0 <= s.data["promptConfidence"] <= 100)
        self.assertTrue(s.data["promptConfidenceReason"])
        self.assertEqual(outcome["confidence"], s.data["promptConfidence"])
        self.assertEqual(outcome["confidenceReason"], s.data["promptConfidenceReason"])
        self.assertNotIn("확신도", outcome["facts"])
        # Fewer confirmed appearance attributes and an unsupported clause both
        # push the confidence for finding the right person down.
        weaker = SurveySession(mode="azure")
        weaker.confirm_prompt("안경 쓴 사람을 찾아줘", [], ["안경"], )
        self.assertLess(weaker.data["promptConfidence"], s.data["promptConfidence"])

    def test_invalid_appearance_does_not_confirm_prompt(self):
        self.assertFalse(self.session.confirm_prompt("사람을 찾아줘", [{"attribute": "shirtColor"}], [])["ok"])
        self.assertEqual(self.session.data["promptPhase"], "briefing")

    def test_configuration_blocks_without_fallback(self):
        s = SurveySession(mode="azure", clock=self.clock)
        ready(s)
        self.assertFalse(s.launch_mission("Azure 이미지 설정을 확인하세요.")["ok"])
        self.assertEqual(s.snapshot()["mode"], "azure")
        self.assertEqual(s.elapsed_ms(), 0)
        self.assertEqual(s.phase, "ready")
        self.assertIn("설정", s.snapshot()["error"])

    def test_debrief_uses_gibby_tone_without_changing_results(self):
        for mode in ("mock", "azure"):
            for reported, injured, missed in ((3, 1, 0), (3, 0, 0), (2, 1, 1), (0, 0, 3)):
                with self.subTest(mode=mode, reported=reported, injured=injured):
                    session = SurveySession(mode=mode)
                    ready(session)
                    session.data.update(missionPhase="complete", score={
                        "total": 3, "reportedCount": reported, "injuredCount": injured, "reportMissedCount": missed,
                    })
                    before = deepcopy(session.data)
                    text = session.debrief()
                    self.assertIn(f"3명 중 {reported}명의 위치를 119에 제때 신고했어." if reported
                                  else "3명 중 아무도 119에 제때 신고하지 못했어.", text)
                    if injured:
                        self.assertIn(f"그중 {injured}명은 부상이 확인돼서 119에도 함께 전달했어.", text)
                    elif reported:
                        self.assertIn("신고한 사람 중 다친 사람은 없어.", text)
                    self.assertIn(f"{missed}명은 신고할 수 있는 시간을 넘겼어." if missed
                                  else "신고 시한은 모두 지켰어.", text)
                    self.assertIn("모의 분석" if mode == "mock" else "Azure 이미지 분석", text)
                    self.assertIn("가상 훈련이야.", text)
                    self.assertIn("우리가 고른 순서는", text)
                    self.assertNotRegex(text, r"했습니다|입니다|되었습니다")
                    self.assertEqual(session.data, before)

    def test_aborted_debrief_stays_friendly_without_inventing_outcomes(self):
        session = SurveySession()
        session.abort_mission()
        text = session.debrief()
        self.assertIn("훈련은 여기서 멈췄어.", text)
        self.assertIn("아직 확인하지 못한 사람들의 신고 결과는 알 수 없어.", text)
        self.assertNotIn("신고했어", text)

    def test_all_six_route_permutations(self):
        outcomes = {}
        for route in itertools.permutations(MONITOR_IDS):
            with self.subTest(route=route):
                clock = Clock()
                s = SurveySession(clock=clock)
                ready(s, route)
                s.launch_mission()
                for i, monitor in enumerate(route):
                    s.expire()
                    if s.person(monitor)["outcome"]:
                        continue
                    clock.advance(sum(SCENARIO[k] for k in ("travelMs", "captureMs", "mockAnalysisMs")))
                    detect(s, monitor, frame_id=f"frame-{i}")
                clock.advance(max(person["deadlineMs"] for person in SCENARIO["people"]))
                s.expire()
                self.assertEqual(s.phase, "complete")
                outcomes[route] = s.snapshot()["score"]["reportedCount"]
                for person in s.data["people"]:
                    if person["outcome"] != "report_missed":
                        self.assertLess(person["resolvedAtMs"], person["deadlineMs"])
                        self.assertIsNotNone(person["captureId"])
        expected = {(1, 2, 3): 2, (1, 3, 2): 2, (2, 1, 3): 2,
                    (2, 3, 1): 1, (3, 1, 2): 3, (3, 2, 1): 2}
        self.assertEqual(outcomes, {
            tuple(f"monitor-{monitor}" for monitor in route): rescued
            for route, rescued in expected.items()
        })

    def test_baseline_and_deterioration_boundary(self):
        for monitor, ms, outcome in (
            ("monitor-1", 22999, "reported"),
            ("monitor-1", 23000, "reported_injured"),
            ("monitor-1", 27999, "reported_injured"),
            ("monitor-1", 28000, "report_missed"),
            ("monitor-2", 1, "reported_injured"),
            ("monitor-3", 12999, "reported"),
            ("monitor-3", 13000, "reported_injured"),
        ):
            with self.subTest(monitor=monitor, ms=ms):
                clock = Clock()
                s = SurveySession(clock=clock)
                ready(s)
                s.launch_mission()
                clock.advance(ms)
                detect(s, monitor)
                self.assertEqual(s.person(monitor)["outcome"], outcome)

    def test_last_five_seconds_apply_to_every_person(self):
        for person in SCENARIO["people"]:
            deadline = person["deadlineMs"]
            for remaining in (5001, 5000, 4999, 1, 0):
                with self.subTest(monitor=person["monitorId"], remaining=remaining):
                    clock = Clock()
                    session = SurveySession(clock=clock)
                    ready(session)
                    session.launch_mission()
                    clock.advance(deadline - remaining)
                    detect(session, person["monitorId"])
                    expected = ("report_missed" if remaining == 0 else
                                "reported_injured" if person["initiallyInjured"] or remaining <= 5000 else
                                "reported")
                    self.assertEqual(session.person(person["monitorId"])["outcome"], expected)

    def test_strict_deadline_and_late_analysis(self):
        for ms in (17999, 18000, 18001):
            with self.subTest(ms=ms):
                clock = Clock()
                s = SurveySession(clock=clock)
                ready(s)
                s.launch_mission()
                s.set_operation("capturing", "monitor-3", s.run_id)
                s.add_capture(capture("monitor-3"), s.run_id)
                s.analyzing("frame-1", s.run_id)
                clock.advance(ms)
                s.apply_detection(s.run_id, "frame-1", POSITIVE)
                self.assertEqual(s.person("monitor-3")["outcome"],
                                 "reported_injured" if ms < 18000 else "report_missed")

    def test_negative_arrival_and_unanswered_only_expire_at_deadline(self):
        s = self.session
        ready(s)
        s.launch_mission()
        s.set_operation("flying", "monitor-3", s.run_id)
        self.assertIsNone(s.person("monitor-3")["outcome"])
        detect(s, "monitor-3", NEGATIVE)
        detect(s, "monitor-3", NEGATIVE, "frame-2")
        self.assertEqual(s.person("monitor-3")["attempts"], 2)
        self.assertIsNone(s.person("monitor-3")["outcome"])
        self.clock.advance(18000)
        s.expire()
        self.assertEqual(s.person("monitor-3")["outcome"], "report_missed")
        self.assertIsNone(s.person("monitor-1")["outcome"])
        self.assertIsNone(s.data["score"])
        self.clock.advance(27000)
        s.expire()
        self.assertEqual(s.data["score"]["reportMissedCount"], 3)
        self.assertIn(NEGATIVE["description"], s.debrief())
        self.assertIn("마지막 사진의 관찰 내용은 이거야.", s.debrief())

    def test_deadline_or_abort_releases_pending_analysis_without_fabricating_evidence(self):
        for ending in ("deadline", "abort"):
            with self.subTest(ending=ending):
                clock = Clock()
                session = SurveySession(clock=clock)
                ready(session)
                session.launch_mission()
                session.set_operation("capturing", "monitor-1", session.run_id)
                session.add_capture(capture("monitor-1"), session.run_id)
                session.analyzing("frame-1", session.run_id)
                if ending == "deadline":
                    clock.advance(45000)
                    session.expire()
                    self.assertEqual(session.data["score"]["reportMissedCount"], 3)
                else:
                    session.abort_mission()
                    self.assertIsNone(session.data["score"])
                self.assertEqual(session.data["captures"][-1]["status"], "captured")
                self.assertIsNone(session.data["captures"][-1]["evidence"])
                self.assertIsNone(session._active_capture)
                self.assertFalse(session.apply_detection(session.run_id, "frame-1", POSITIVE))

    def test_pause_excludes_only_error_wait_and_abort_has_no_fake_outcomes(self):
        s = self.session
        ready(s)
        s.launch_mission()
        self.clock.advance(11000)
        s.pause("이미지 분석 오류")
        self.clock.advance(90000)
        s.expire()
        self.assertEqual(s.elapsed_ms(), 11000)
        self.assertFalse(s.data["clockRunning"])
        self.assertTrue(s.retry_mission()["ok"])
        self.clock.advance(2000)
        self.assertEqual(s.elapsed_ms(), 13000)
        s.abort_mission()
        self.clock.advance(90000)
        self.assertEqual(s.elapsed_ms(), 13000)
        self.assertTrue(all(p["outcome"] is None for p in s.data["people"]))
        self.assertIsNone(s.data["score"])

    def test_stale_duplicate_empty_and_mismatched_capture_defenses(self):
        s = self.session
        ready(s)
        s.launch_mission()
        self.assertFalse(s.apply_detection(s.run_id, "not-captured", POSITIVE))
        s.set_operation("capturing", "monitor-1", s.run_id)
        self.assertFalse(s.add_capture(capture("monitor-2"), s.run_id))
        self.assertFalse(s.add_capture(capture(image_bytes=b""), s.run_id))
        self.assertFalse(s.add_capture(capture(), "old-run"))
        self.assertTrue(s.add_capture(capture(), s.run_id))
        s.analyzing("frame-1", s.run_id)
        self.assertFalse(s.apply_detection("old-run", "frame-1", POSITIVE))
        self.assertFalse(s.apply_detection(s.run_id, "old-frame", POSITIVE))
        self.assertTrue(s.apply_detection(s.run_id, "frame-1", POSITIVE))
        revision = s.data["revision"]
        self.assertFalse(s.apply_detection(s.run_id, "frame-1", NEGATIVE))
        self.assertEqual(s.data["revision"], revision)
        s.abort_mission()
        self.assertFalse(s.apply_detection(s.run_id, "frame-1", POSITIVE))

    def test_evidence_validation(self):
        for evidence in (None, {}, {"targetPresent": "true"}, POSITIVE | {"description": ""},
                         POSITIVE | {"box": [0, 0, 2, 1]}, POSITIVE | {"box": [True, 0, 1, 1]},
                         POSITIVE | {"box": [float("nan"), 0, 1, 1]}, NEGATIVE | {"box": [0, 0, 1, 1]},
                         {k: v for k, v in POSITIVE.items() if k != "confidence"},
                         POSITIVE | {"confidence": 101}, POSITIVE | {"confidence": -1},
                         POSITIVE | {"confidence": 92.5}, POSITIVE | {"confidence": True}):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                validate_evidence(evidence)
        self.assertIsNone(validate_evidence(POSITIVE | {"box": None})["box"])
        self.assertEqual(validate_evidence(POSITIVE)["confidence"], 92)

    def test_flat_snapshot_and_independent_sessions(self):
        s = self.session
        other = SurveySession()
        self.assertNotEqual(s.run_id, other.run_id)
        snapshot = s.snapshot()
        expected = {"phase", "draftRoute", "confirmedRoute", "runId", "revision", "missionPhase",
                    "mode", "elapsedMs", "clockRunning", "activeMonitorId", "people", "captures",
                    "score", "error", "promptPhase", "activePromptMonitorId", "userPromptText",
                    "appearanceConstraints", "unsupportedAppearance", "promptConfidence",
                    "promptConfidenceReason", "droneControlMode", "droneStopState",
                    "droneErrorCode", "droneState", "activeVisitIndex", "droneMissionId",
                    "dangerOrder", "vulnerableAdjustedOrder", "kind"}
        self.assertEqual(set(snapshot), expected)
        snapshot["people"][0]["outcome"] = "reported"
        self.assertIsNone(s.person("monitor-1")["outcome"])
        self.assertIsNone(other.person("monitor-1")["outcome"])


if __name__ == "__main__":
    unittest.main()
