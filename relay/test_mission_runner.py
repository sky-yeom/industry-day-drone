import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

from relay.mission_runner import MissionRunner
from relay.survey import SCENARIO, SurveySession
from relay.test_survey import Clock, NEGATIVE, POSITIVE, capture, ready
from relay import tools


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
        self.results = []
        self.block = None
        self.cancelled = False
        self.error = None

    def readiness(self):
        return self.error

    async def analyze(self, frame, target, *, search_prompt="", appearance_constraints=None, unsupported_appearance=None):
        self.calls.append((frame, target))
        self.search_prompts.append(search_prompt)
        self.appearance_constraints.append((appearance_constraints, unsupported_appearance))
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
        self.session.confirm_prompt(participant_instruction, requested, [])
        first, second = await asyncio.gather(self.runner.launch(), self.runner.launch())
        self.assertTrue(first["ok"] and second["ok"])
        await settle(lambda: self.session.phase == "complete")
        self.assertEqual(self.camera.calls, ["monitor-3", "monitor-1", "monitor-2"])
        self.assertEqual(self.session.data["score"]["rescuedCount"], 3)
        self.assertEqual(self.session.data["score"]["injuredCount"], 1)
        self.assertEqual(self.vision.search_prompts, [participant_instruction] * 3)
        self.assertEqual(self.vision.appearance_constraints, [(requested, [])] * 3)
        for frame, target in self.vision.calls:
            displayed = next(c for c in self.session.data["captures"] if c["id"] == frame.id)
            self.assertEqual(displayed["imageUrl"], frame.image_url)
            self.assertEqual(target, self.session.person(frame.monitor_id)["targetDescription"])
        await settle(lambda: any(e["type"] == "mission.debrief" for e in self.events))
        debrief = next(i for i, e in enumerate(self.events) if e["type"] == "mission.debrief")
        self.assertEqual(self.events[debrief - 1]["state"]["missionPhase"], "complete")
        self.assertEqual(sum(e["type"] == "mission.debrief" for e in self.events), 1)

    async def test_negative_once_recaptured_then_unresolved_until_expiry(self):
        self.vision.results = [NEGATIVE, NEGATIVE, POSITIVE, POSITIVE]
        await self.runner.launch()
        await settle(lambda: self.runner._work.done())
        self.assertEqual(self.camera.calls, ["monitor-3", "monitor-3", "monitor-1", "monitor-2"])
        self.assertIsNone(self.session.person("monitor-3")["outcome"])
        self.assertNotEqual(self.session.phase, "complete")
        await self.advance(18000)
        await settle(lambda: self.session.phase == "complete")
        self.assertEqual(self.session.data["score"]["tooLateCount"], 1)

    async def test_debrief_survives_deadline_cancellation_during_publication(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def publish(event):
            if event["type"] == "mission.debrief":
                entered.set()
                await release.wait()
            self.events.append(event)

        self.runner.publish = publish
        await self.runner.launch()
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
        self.session.confirm_prompt("빨간색 티셔츠를 입은 사람을 찾아줘", wrong, [])
        self.runner.camera = FixtureCamera()
        self.runner.vision = MockVision()
        with patch.dict(CAMERA_SCENARIO, {"mockAnalysisMs": 0}):
            await self.runner.launch()
            await settle(lambda: self.runner._work.done())
        self.assertEqual(len(self.session.data["captures"]), 6)
        self.assertTrue(all(not frame["evidence"]["targetPresent"]
                            for frame in self.session.data["captures"]))
        self.assertTrue(all(person["outcome"] is None for person in self.session.data["people"]))
        await self.advance(max(person["deadlineMs"] for person in self.session.data["people"]))
        await settle(lambda: self.session.phase == "complete")
        self.assertEqual(self.session.data["score"]["rescuedCount"], 0)
        self.assertEqual(self.session.data["score"]["tooLateCount"], 3)
        self.assertIn("요청한 외형 조건", self.session.debrief())
        self.assertIn("마지막 이미지 관찰", self.session.debrief())

    async def test_normal_work_and_negative_recapture_time_all_count(self):
        self.session.scenario.update(travelMs=7000, captureMs=1000)
        self.vision.results = [NEGATIVE, POSITIVE, POSITIVE, POSITIVE]
        analyze = self.vision.analyze

        async def timed_analysis(frame, target, **kwargs):
            self.clock.advance(2000)
            return await analyze(frame, target, **kwargs)

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
        await settle(lambda: self.session.phase == "complete")
        self.assertEqual(self.session.elapsed_ms(), 33000)
        self.assertEqual(self.session.person("monitor-3")["resolvedAtMs"], 13000)
        self.assertEqual(self.session.person("monitor-3")["outcome"], "rescued_but_hurt")
        self.assertEqual(self.session.person("monitor-1")["resolvedAtMs"], 23000)
        self.assertEqual(self.session.person("monitor-1")["outcome"], "rescued_but_hurt")
        self.assertEqual(self.session.person("monitor-2")["resolvedAtMs"], 33000)

    async def test_expiry_independent_of_blocked_inference_and_cleanup(self):
        self.vision.block = asyncio.Event()
        await self.runner.launch()
        await settle(lambda: len(self.vision.calls) == 1)
        await self.advance(18000)
        self.assertEqual(self.session.person("monitor-3")["outcome"], "too_late")
        self.assertFalse(self.runner._work.done())
        await self.advance(27000)
        await settle(lambda: self.runner._work.done())
        self.assertTrue(self.vision.cancelled)
        self.assertEqual(self.session.phase, "complete")
        self.assertEqual(self.session.data["score"]["rescuedCount"], 0)

    async def test_late_positive_does_not_reverse_expiry(self):
        self.vision.block = asyncio.Event()
        await self.runner.launch()
        await settle(lambda: len(self.vision.calls) == 1)
        await self.advance(18000)
        self.vision.block.set()
        await settle(lambda: self.session.phase == "complete")
        self.assertEqual(self.session.person("monitor-3")["outcome"], "too_late")
        self.assertIsNone(self.session.person("monitor-3")["captureId"])

    async def test_camera_error_pause_retry_preserves_clock(self):
        self.camera.fail = True
        await self.runner.launch()
        await settle(lambda: self.session.phase == "paused")
        await self.advance(90000)
        self.assertEqual(self.session.elapsed_ms(), 0)
        self.assertIsNotNone(self.session.data["error"])
        self.assertTrue((await self.runner.retry())["ok"])
        await settle(lambda: self.session.phase == "complete")
        self.assertEqual(self.session.data["score"]["rescuedCount"], 3)
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

        async def late_analysis(frame, target, **kwargs):
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
