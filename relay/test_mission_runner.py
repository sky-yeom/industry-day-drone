import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

from relay.mission_runner import MissionRunner
from relay.survey import MONITOR_IDS, SCENARIO, SurveySession
from relay.test_survey import Clock, NEGATIVE, POSITIVE, capture, ready
from relay import tools


def reconfirm_all(session, prompt_text, appearance_constraints=None, unsupported_appearance=None):
    """Re-open and re-confirm the shared target description with new wording.

    Used by tests that need real (non-default) participant wording applied
    to all three already-routed sites, e.g. to exercise real vision
    matching. All scenario kinds now confirm every site in a single call.
    """
    session.data["promptPhase"] = "briefing"
    session.data["activePromptMonitorId"] = MONITOR_IDS[0]
    for person in session.data["people"]:
        person["promptConfirmed"] = False
    return session.confirm_prompt(prompt_text, appearance_constraints, unsupported_appearance)


class FakeCamera:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def capture(self, monitor):
        self.calls.append(monitor)
        if self.fail:
            self.fail = False
            raise RuntimeError("fake camera failure")
        return capture(monitor, f"frame-{len(self.calls)}")


class FakeVision:
    def __init__(self):
        self.calls = []
        self.search_prompts = []
        self.appearance_constraints = []
        self.scene_contexts = []
        self.results = []
        self.block = None
        self.cancelled = False
        self.error = None

    def readiness(self):
        return self.error

    async def analyze(self, frame, *, search_prompt, appearance_constraints=None,
                      unsupported_appearance=None, scene_context=None, kind="triage"):
        self.calls.append((frame, search_prompt))
        self.search_prompts.append(search_prompt)
        self.appearance_constraints.append((appearance_constraints, unsupported_appearance))
        self.scene_contexts.append(scene_context)
        if self.block is not None:
            try:
                await self.block.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        outcome = self.results.pop(0) if self.results else POSITIVE
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def settle(predicate):
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("async condition did not settle")


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = Clock()
        scenario = deepcopy(SCENARIO)
        scenario.update(travelMs=0, captureMs=0)
        self.session = SurveySession(clock=self.clock, scenario=scenario)
        ready(self.session)
        self.camera, self.vision = FakeCamera(), FakeVision()
        self.events = []
        self.tick = asyncio.Event()

        async def publish(event):
            self.events.append(event)

        async def sleep(seconds):
            if seconds:
                await self.tick.wait()
                self.tick.clear()
            else:
                await asyncio.sleep(0)

        self.runner = MissionRunner(self.session, self.camera, self.vision, publish, sleep=sleep)

    async def asyncTearDown(self):
        await self.runner.close()

    async def advance(self, ms):
        self.clock.advance(ms)
        self.tick.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def test_auto_complete_and_idempotent_launch_exact_capture(self):
        participant_instruction = "제가 쓴 지시: 화면 구석까지 살피고 사람이 보이는 위치를 알려 주세요."
        requested = [{"attribute": "hairColor", "operator": "include", "values": ["brown"]}]
        self.session.data["mode"] = "azure"
        self.assertTrue(reconfirm_all(self.session, participant_instruction, requested, [])["ok"])
        first, second = await asyncio.gather(self.runner.launch(), self.runner.launch())
        self.assertTrue(first["ok"] and second["ok"])
        # monitor-3 (the real target) resolves immediately on its first
        # positive detection; monitor-1/monitor-2 are false alarms and never
        # resolve as "reported" no matter what the image analysis returns —
        # they only settle once their own deadline passes. Wait for every
        # site to actually be visited before advancing the clock, so the
        # background deadline watcher can't mark a still-unvisited false
        # alarm site "missed" before the runner ever gets to capture it.
        await settle(lambda: {"monitor-1", "monitor-2", "monitor-3"} <= set(self.camera.calls))
        self.assertEqual(self.session.person("monitor-3")["outcome"], "reported")
        await self.advance(max(p["deadlineMs"] for p in self.session.data["people"]) + 1000)
        await settle(lambda: self.session.phase == "complete")
        self.assertIn("monitor-1", self.camera.calls)
        self.assertIn("monitor-2", self.camera.calls)
        self.assertEqual(self.session.data["score"]["reportedCount"], 1)
        self.assertEqual(self.session.data["score"]["falseAlarmCount"], 2)
        self.assertTrue(all(prompt == participant_instruction for prompt in self.vision.search_prompts))
        self.assertTrue(all(constraint == (requested, []) for constraint in self.vision.appearance_constraints))
        for frame, prompt in self.vision.calls:
            displayed = next(c for c in self.session.data["captures"] if c["id"] == frame.id)
            self.assertEqual(displayed["imageUrl"], frame.image_url)
            self.assertEqual(prompt, participant_instruction)
        await settle(lambda: any(e["type"] == "mission.debrief" for e in self.events))
        debrief = next(i for i, e in enumerate(self.events) if e["type"] == "mission.debrief")
        self.assertEqual(self.events[debrief - 1]["state"]["missionPhase"], "complete")
        self.assertEqual(self.events[debrief]["text"], self.session.debrief())
        self.assertIn("실제 사람이 있던", self.events[debrief]["text"])
        self.assertIn("구조로 이어졌어", self.events[debrief]["text"])
        self.assertEqual(sum(e["type"] == "mission.debrief" for e in self.events), 1)

    async def test_negative_once_recaptured_then_unresolved_until_expiry(self):
        self.vision.results = [NEGATIVE, NEGATIVE, POSITIVE, POSITIVE]
        await self.runner.launch()
        await settle(lambda: self.runner._work.done())
        self.assertEqual(self.camera.calls, ["monitor-3", "monitor-3", "monitor-1", "monitor-2"])
        self.assertIsNone(self.session.person("monitor-3")["outcome"])
        self.assertNotEqual(self.session.phase, "complete")
        await self.advance(max(p["deadlineMs"] for p in self.session.data["people"]) + 1000)
        await settle(lambda: self.session.phase == "complete")
        self.assertEqual(self.session.data["score"]["reportMissedCount"], 3)

    async def test_debrief_survives_deadline_cancellation_during_publication(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def publish(event):
            if event["type"] == "mission.debrief":
                entered.set()
                await release.wait()
            self.events.append(event)

        self.runner.publish = publish
        await self.runner.launch()
        await self.advance(max(p["deadlineMs"] for p in self.session.data["people"]) + 1000)
        await settle(entered.is_set)
        self.tick.set()
        await settle(lambda: self.runner._work.done())
        self.assertFalse(any(e["type"] == "mission.debrief" for e in self.events))
        release.set()
        await settle(lambda: self.runner._deadlines.done())
        self.assertEqual(sum(e["type"] == "mission.debrief" for e in self.events), 1)
        self.assertTrue(self.runner._terminal_task.done())

    async def test_close_cleans_up_blocked_terminal_publication(self):
        entered = asyncio.Event()

        async def publish(event):
            if event["type"] == "mission.debrief":
                entered.set()
                await asyncio.Event().wait()

        self.runner.publish = publish
        await self.runner.launch()
        await self.advance(max(p["deadlineMs"] for p in self.session.data["people"]) + 1000)
        await settle(entered.is_set)
        terminal = self.runner._terminal_task
        await self.runner.close()
        self.assertTrue(terminal.cancelled())
        self.assertTrue(self.runner._work.done())
        self.assertTrue(self.runner._deadlines.done())

    async def test_wrong_spoken_appearance_with_real_fixtures_saves_nobody(self):
        from relay.camera import FixtureCamera, SCENARIO as CAMERA_SCENARIO
        from relay.vision import MockVision

        wrong = [{"attribute": "shirtColor", "operator": "include", "values": ["red"]}]
        reconfirm_all(self.session, "빨간색 티셔츠를 입은 사람을 찾아줘", wrong, [])
        self.runner.camera = FixtureCamera()
        self.runner.vision = MockVision()
        with patch.dict(CAMERA_SCENARIO, {"mockAnalysisMs": 0}):
            await self.runner.launch()
            await settle(lambda: self.runner._work.done())
        self.assertEqual(len(self.session.data["captures"]), 6)
        self.assertTrue(all(not frame["evidence"]["targetPresent"]
                            for frame in self.session.data["captures"]))
        # The real target's site stays unresolved (wrong description never
        # matches it), but the 2 false-alarm sites resolve as soon as every
        # allowed detection attempt comes back empty — no need to wait out
        # their own deadline timers once they've actually been checked.
        self.assertTrue(all(person["outcome"] is None for person in self.session.data["people"]
                            if not person.get("falseAlarm")))
        self.assertTrue(all(person["outcome"] == "report_missed" for person in self.session.data["people"]
                            if person.get("falseAlarm")))
        await self.advance(max(person["deadlineMs"] for person in self.session.data["people"]))
        await settle(lambda: self.session.phase == "complete")
        self.assertEqual(self.session.data["score"]["reportedCount"], 0)
        self.assertEqual(self.session.data["score"]["reportMissedCount"], 3)
        debrief = self.session.debrief()
        self.assertIn("시한을 놓쳤어", debrief)
        self.assertIn("착각", debrief)

    async def test_unassessable_request_aborts_safely_for_fresh_confirmation(self):
        from relay.appearance import REVISION_REQUEST
        from relay.vision import PromptRevisionRequired
        self.vision.results = [PromptRevisionRequired(REVISION_REQUEST)]
        await self.runner.launch()
        await settle(lambda: self.session.phase == "aborted")
        self.assertIn(REVISION_REQUEST, self.session.data["error"])
        self.assertIn("처음으로", self.session.data["error"])
        self.assertFalse(self.session.data["clockRunning"])
        self.assertTrue(all(person["outcome"] is None for person in self.session.data["people"]))
        self.assertFalse((await self.runner.retry())["ok"])

    async def test_normal_work_and_negative_recapture_time_all_count(self):
        self.session.scenario.update(travelMs=7000, captureMs=1000)
        self.vision.results = [NEGATIVE, POSITIVE, POSITIVE, POSITIVE]
        analyze = self.vision.analyze

        async def timed_analysis(frame, **kwargs):
            self.clock.advance(2000)
            return await analyze(frame, **kwargs)

        async def timed_sleep(seconds):
            if seconds == self.runner.tick_seconds:
                await self.tick.wait()
                self.tick.clear()
            else:
                self.clock.advance(seconds * 1000)
                await asyncio.sleep(0)

        self.vision.analyze = timed_analysis
        self.runner.sleep = timed_sleep
        await self.runner.launch()
        await settle(lambda: self.runner._work.done())
        # monitor-3 (real target) resolves on its 2nd (recaptured) attempt;
        # monitor-1's deadline (30000ms) passes while monitor-2 is still
        # being travelled to, so it's expired inline rather than reported;
        # monitor-2's own positive detection never resolves it (false alarm).
        self.assertEqual(self.session.elapsed_ms(), 33000)
        self.assertEqual(self.session.person("monitor-3")["resolvedAtMs"], 13000)
        self.assertEqual(self.session.person("monitor-3")["outcome"], "reported")
        self.assertEqual(self.session.person("monitor-1")["resolvedAtMs"], 30000)
        self.assertEqual(self.session.person("monitor-1")["outcome"], "report_missed")
        self.assertIsNone(self.session.person("monitor-2")["outcome"])
        await self.advance(self.session.person("monitor-2")["deadlineMs"] - 33000 + 1000)
        await settle(lambda: self.session.phase == "complete")
        self.assertEqual(self.session.person("monitor-2")["outcome"], "report_missed")

    async def test_expiry_independent_of_blocked_inference_and_cleanup(self):
        self.vision.block = asyncio.Event()
        await self.runner.launch()
        await settle(lambda: len(self.vision.calls) == 1)
        await self.advance(25000)
        self.assertEqual(self.session.person("monitor-3")["outcome"], "report_missed")
        self.assertFalse(self.runner._work.done())
        await self.advance(16000)
        await settle(lambda: self.runner._work.done())
        self.assertTrue(self.vision.cancelled)
        self.assertEqual(self.session.phase, "complete")
        self.assertEqual(self.session.data["score"]["reportedCount"], 0)

    async def test_late_positive_does_not_reverse_expiry(self):
        self.vision.block = asyncio.Event()
        await self.runner.launch()
        await settle(lambda: len(self.vision.calls) == 1)
        await self.advance(25000)
        self.vision.block.set()
        await self.advance(16000)
        await settle(lambda: self.session.phase == "complete")
        self.assertEqual(self.session.person("monitor-3")["outcome"], "report_missed")
        self.assertIsNone(self.session.person("monitor-3")["captureId"])

    async def test_camera_error_pause_retry_preserves_clock(self):
        self.camera.fail = True
        await self.runner.launch()
        await settle(lambda: self.session.phase == "paused")
        await self.advance(90000)
        self.assertEqual(self.session.elapsed_ms(), 0)
        self.assertIsNotNone(self.session.data["error"])
        self.assertTrue((await self.runner.retry())["ok"])
        await settle(lambda: self.session.person("monitor-3")["outcome"] == "reported")
        await self.advance(max(p["deadlineMs"] for p in self.session.data["people"]) + 1000)
        await settle(lambda: self.session.phase == "complete")
        self.assertEqual(self.session.data["score"]["reportedCount"], 1)
        self.assertEqual(self.session.data["score"]["falseAlarmCount"], 2)
        self.assertEqual(self.camera.calls.count("monitor-3"), 2)

    async def test_analysis_error_and_malformed_result_retry_same_capture(self):
        self.vision.results = [RuntimeError("fake inference failure"), {"targetPresent": "yes"}, POSITIVE]
        await self.runner.launch()
        await settle(lambda: self.session.phase == "paused")
        self.assertEqual(self.session.data["captures"][0]["status"], "error")
        await self.advance(40000)
        await self.runner.retry()
        await settle(lambda: self.session.phase == "paused")
        self.assertEqual(len(self.camera.calls), 1)
        await self.runner.retry()
        await settle(lambda: self.session.person("monitor-3")["outcome"] == "reported")
        await self.advance(max(p["deadlineMs"] for p in self.session.data["people"]) + 1000)
        await settle(lambda: self.session.phase == "complete")
        self.assertEqual(self.session.person("monitor-3")["attempts"], 1)
        self.assertEqual([f.id for f, _ in self.vision.calls[:3]], ["frame-1"] * 3)

    async def test_abort_cancels_waiting_analysis_without_outcomes(self):
        self.vision.block = asyncio.Event()
        await self.runner.launch()
        await settle(lambda: len(self.vision.calls) == 1)
        await self.runner.abort()
        self.assertTrue(self.vision.cancelled)
        self.assertTrue(self.runner._work.done())
        self.assertTrue(self.runner._deadlines.done())
        self.assertEqual(self.session.phase, "aborted")
        self.assertTrue(all(p["outcome"] is None for p in self.session.data["people"]))

    async def test_provider_result_after_cancellation_is_discarded(self):
        started = asyncio.Event()

        async def late_analysis(frame, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return {"targetPresent": "malformed stale response"}

        self.vision.analyze = late_analysis
        await self.runner.launch()
        await started.wait()
        await self.runner.abort()
        self.assertEqual(self.session.phase, "aborted")
        self.assertIsNone(self.session.data["captures"][0]["evidence"])
        self.assertTrue(all(p["outcome"] is None for p in self.session.data["people"]))
        self.assertIn("미확인", self.session.data["error"])

    async def test_dispatch_rejects_overrides_and_legacy_fake_detection(self):
        for name, args in (("report_detection", {}), ("confirm_prompt", {"prompt_text": ""}),
                           ("launch_mission", {"mode": "mock"}), ("launch_mission", {"outcome": "rescued"}),
                           ("clear_route", []), ("select_stop", {}), ("select_stop", {"monitor": 3})):
            self.assertFalse((await tools.dispatch(self.session, self.runner, name, args))["ok"])
        self.assertEqual(self.session.phase, "ready")
        self.vision.error = "Azure 이미지 분석 설정이 없습니다."
        self.assertFalse((await tools.dispatch(self.session, self.runner, "launch_mission", {}))["ok"])
        self.assertIsNone(self.runner._work)


if __name__ == "__main__":
    unittest.main()
