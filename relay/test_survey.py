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
    """Confirm the shared target description once — all scenario kinds
    (triage/security/construction) now apply a single confirm_prompt call
    to every site at once, so no per-site looping is needed."""
    return session.confirm_prompt(**prompt_args)


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
        self.assertEqual(s.data["promptPhase"], "confirmed")
        s.state.draftRoute = []
        self.assertTrue(s.select_stop("monitor-3")["ok"])
        self.assertFalse(s.confirm_prompt("이미 확인했는데 다시")["ok"])
        self.assertTrue(all(p["outcome"] is None for p in s.data["people"]))
        s.select_stop("monitor-2")
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
                         "안녕! 난 Gibby야! 너는 119 종합상황실 소속 상황요원이고, 방금 익명 문자로 사진이랑 같이 위급 신고가 "
                         "들어왔어. 바다, 잔해 아래, 불이 난 집, 이렇게 세 곳에서 신고가 들어왔는데 드론은 한 대뿐이라 한 곳씩만 "
                         "확인할 수 있어. 그중 두 곳은 오인 신고고 한 곳에만 실제로 사람이 있어. 네가 오더만 내려주면 내가 드론 "
                         "보낼게! 먼저 찾는 사람이 어떤 모습인지 말해줄 수 있어?")
        self.assertIn("오인 신고", SCENARIO["briefing"][1])
        for hint in ("머리", "티셔츠", "초록", "갈색", "같은 외형"):
            self.assertNotIn(hint, tools.GREETING)
            self.assertNotIn(hint, " ".join(SCENARIO["briefing"]))
        rejected = self.session.confirm_prompt("")
        confirmed = self.session.confirm_prompt(SEARCH_PROMPT)
        for person in SCENARIO["people"]:
            self.assertNotIn(person["clue"], tools.GREETING)
            self.assertNotIn(person["clue"], rejected["facts"])
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
            for rescued in (True, False):
                with self.subTest(mode=mode, rescued=rescued):
                    clock = Clock()
                    session = SurveySession(mode=mode, clock=clock)
                    ready(session)
                    session.launch_mission()
                    if rescued:
                        detect(session, "monitor-3", POSITIVE)
                    clock.advance(50000)
                    session.expire()
                    text = session.debrief()
                    real_label = session.labels["monitor-3"]
                    if rescued:
                        self.assertIn(f"실제 사람이 있던 {real_label}에서 시간 안에 위치를 119에 신고해서 구조로 이어졌어.", text)
                        self.assertIn("확신도", text)
                    else:
                        self.assertIn(f"실제 사람이 있던 {real_label}을(를) 시간 안에 확인하지 못해서 신고 시한을 놓쳤어.", text)
                    for person in session.data["people"]:
                        if person.get("falseAlarm"):
                            self.assertIn(person["falseAlarmReveal"], text)
                    self.assertIn("모의 분석" if mode == "mock" else "Azure 이미지 분석", text)
                    self.assertIn("가상 훈련이야.", text)
                    self.assertIn("우리가 고른 확인 순서는", text)
                    self.assertNotRegex(text, r"했습니다|입니다|되었습니다")

    def test_aborted_debrief_stays_friendly_without_inventing_outcomes(self):
        session = SurveySession()
        session.abort_mission()
        text = session.debrief()
        self.assertIn("훈련은 여기서 멈췄어.", text)
        self.assertIn("찾는 사람을 구조했는지는 아직 알 수 없어.", text)
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
                outcomes[route] = s.person("monitor-3")["outcome"]
                self.assertEqual(s.data["score"]["falseAlarmCount"], 2)
                self.assertLessEqual(s.data["score"]["reportedCount"], 1)
        # monitor-3 (the real target) has the tightest deadline (24000ms); each
        # stop costs travelMs+captureMs+mockAnalysisMs=10000ms sequentially, so
        # it's only reachable in time as the 1st or 2nd stop of the route —
        # visited 3rd it always arrives after its deadline has already passed.
        for route, outcome in outcomes.items():
            position = route.index("monitor-3")
            expected = "reported" if position <= 1 else "report_missed"
            self.assertEqual(outcome, expected, route)

    def test_deadline_boundary_for_real_target_and_false_alarms_never_reported(self):
        for monitor, ms, outcome in (
            ("monitor-3", 23999, "reported"),
            ("monitor-3", 24000, "report_missed"),
            ("monitor-1", 29999, None),
            ("monitor-2", 39999, None),
        ):
            with self.subTest(monitor=monitor, ms=ms):
                clock = Clock()
                s = SurveySession(clock=clock)
                ready(s)
                s.launch_mission()
                clock.advance(ms)
                detect(s, monitor)
                self.assertEqual(s.person(monitor)["outcome"], outcome)

    def test_strict_deadline_and_late_analysis(self):
        for ms in (23999, 24000, 24001):
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
                                 "reported" if ms < 24000 else "report_missed")

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
        self.clock.advance(24000)
        s.expire()
        self.assertEqual(s.person("monitor-3")["outcome"], "report_missed")
        self.assertIsNone(s.person("monitor-1")["outcome"])
        self.assertIsNone(s.data["score"])
        self.clock.advance(16000)
        s.expire()
        self.assertEqual(s.data["score"]["reportMissedCount"], 3)
        self.assertEqual(s.data["score"]["falseAlarmCount"], 2)

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
