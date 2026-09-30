import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from relay import server
from relay.survey import MONITOR_IDS, SurveySession
from relay.test_mission_runner import FakeCamera, FakeVision
from relay.test_server import Browser, Upstream, participant_turn
from relay.test_survey import PROMPT_ARGS, SEARCH_PROMPT
from relay.voice_turns import VoiceTurns, is_affirmative, names_stop


class VoiceTurnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = SurveySession()
        self.bridge = server.Bridge(Browser(), self.session, (FakeCamera(), FakeVision()))
        self.bridge.upstream = Upstream()

    async def asyncTearDown(self):
        await self.bridge.close()

    async def call(self, name, args, turn=None, call_id=None):
        await self.bridge.handle_tool_call({
            "name": name, "arguments": json.dumps(args),
            "call_id": call_id or f"call-{len(self.bridge._completed_commands)}",
        }, turn=turn)
        outputs = [event for event in self.bridge.upstream.sent
                   if event["type"] == "conversation.item.create"]
        return json.loads(outputs[-1]["item"]["output"])

    async def test_prepare_prompt_auto_confirms_in_same_call(self):
        turn = participant_turn(self.bridge, SEARCH_PROMPT)
        self.assertTrue((await self.call("prepare_prompt", PROMPT_ARGS, turn))["ok"])
        self.assertEqual(self.session.data["promptPhase"], "confirmed")
        self.assertEqual(self.session.data["userPromptText"], SEARCH_PROMPT)
        self.assertIsNone(self.session.pending_prompt)
        self.assertIsNotNone(self.bridge._route_intro_id)
        context = next(event["session"] for event in reversed(self.bridge.upstream.sent)
                       if event["type"] == "session.update" and "tools" in event["session"])
        self.assertEqual({tool["name"] for tool in context["tools"]},
                         {"get_state", "set_route", "clear_route"})

    async def test_prepare_prompt_rejects_affirmative_or_retry_only_turn(self):
        for text in ("응", "음", "뭐라고"):
            with self.subTest(text=text):
                turn = participant_turn(self.bridge, text)
                outcome = await self.call("prepare_prompt", PROMPT_ARGS, turn)
                self.assertFalse(outcome["ok"])
                self.assertIn("탐색 설명이 아직 없습니다", outcome["facts"])
                self.assertEqual(self.session.data["promptPhase"], "briefing")
                self.assertEqual(self.session.data["userPromptText"], "")

    async def test_invalid_prepare_does_not_consume_turn(self):
        turn = participant_turn(self.bridge, SEARCH_PROMPT)
        self.assertFalse((await self.call("prepare_prompt", {"prompt_text": ""}, turn))["ok"])
        self.assertFalse(turn.consumed)
        self.assertTrue((await self.call("prepare_prompt", PROMPT_ARGS, turn))["ok"])
        self.assertTrue(turn.consumed)

    async def test_only_one_stop_per_reply_and_second_stop_auto_confirms_route_and_launches(self):
        self.session.confirm_prompt(**PROMPT_ARGS)
        async def fake_launch():
            return self.session.launch_mission()

        with patch.object(self.bridge.runner, "launch", new=AsyncMock(side_effect=fake_launch)) as launch:
            first = participant_turn(self.bridge, "먼저 바다부터 가자")
            self.assertTrue((await self.call("select_stop", {"monitor": "monitor-1"}, first))["ok"])
            self.assertFalse((await self.call("select_stop", {"monitor": "monitor-2"}, first))["ok"])
            self.assertEqual(self.session.state.draftRoute, ["monitor-1"])
            second = participant_turn(self.bridge, "잔해 아래 사람")
            self.assertTrue((await self.call("select_stop", {"monitor": "monitor-2"}, second))["ok"])
            launch.assert_awaited_once()
        self.assertEqual(self.session.state.confirmedRoute, ["monitor-1", "monitor-2", "monitor-3"])
        self.assertEqual(self.session.phase, "flying")
        self.assertTrue(self.session.data["clockRunning"])

    def _non_default_route(self, session):
        default = list(session.data["vulnerableAdjustedOrder"])
        return next(list(route) for route in (
            ("monitor-3", "monitor-1", "monitor-2"), ("monitor-2", "monitor-3", "monitor-1"),
        ) if list(route) != default)

    async def test_model_mapped_paraphrased_route_is_used_and_launches(self):
        self.session.confirm_prompt(**PROMPT_ARGS)
        chosen = self._non_default_route(self.session)
        heard = [{"monitor": monitor, "phrase": phrase} for monitor, phrase in zip(
            chosen, ("불난 곳", "물에 빠진 데", "무너진 건물 쪽"))]

        async def fake_launch():
            return self.session.launch_mission()

        with patch.object(self.bridge.runner, "launch", new=AsyncMock(side_effect=fake_launch)) as launch:
            # No registered alias appears verbatim: the model's semantic mapping is the source of truth.
            turn = participant_turn(self.bridge, "불난 곳 먼저 가고, 그다음 물에 빠진 데, 마지막은 무너진 건물 쪽")
            outcome = await self.call(
                "set_route", {"route": chosen, "complete": True, "heard": heard}, turn)
            self.assertTrue(outcome["ok"], outcome)
            launch.assert_awaited_once()

        self.assertEqual(self.session.state.confirmedRoute, chosen)
        self.assertEqual(self.session.phase, "flying")
        self.assertEqual(
            self.bridge._departure_announcement,
            server.tools.DEPARTURE_ANNOUNCEMENT_BY_KIND[self.session.kind],
        )

    async def test_unconfirmed_or_invalid_mapping_uses_recommendation(self):
        probe = SurveySession()
        probe.confirm_prompt(**PROMPT_ARGS)
        chosen = self._non_default_route(probe)
        for args in (
            {"route": chosen, "complete": False, "heard": []},
            {"route": chosen, "heard": []},
            {"route": chosen, "complete": "true", "heard": []},
            {"route": [chosen[0], chosen[0], chosen[1]], "complete": True, "heard": []},
            {"route": chosen[:2], "complete": True, "heard": []},
            {"route": ["monitor-9", chosen[1], chosen[2]], "complete": True, "heard": []},
            {"route": chosen[0], "complete": True, "heard": []},
            {},
        ):
            with self.subTest(args=args):
                session = SurveySession()
                bridge = server.Bridge(Browser(), session, (FakeCamera(), FakeVision()))
                bridge.upstream = Upstream()
                session.confirm_prompt(**PROMPT_ARGS)
                expected = list(session.data["vulnerableAdjustedOrder"])

                async def fake_launch(session=session):
                    return session.launch_mission()

                try:
                    with patch.object(bridge.runner, "launch", new=AsyncMock(side_effect=fake_launch)):
                        turn = participant_turn(bridge, "음 잘 모르겠어")
                        await bridge.handle_tool_call({
                            "name": "set_route", "arguments": json.dumps(args), "call_id": "route",
                        }, turn=turn)
                    self.assertEqual(session.state.confirmedRoute, expected)
                    self.assertEqual(session.phase, "flying")
                    self.assertIn("세 곳의 순서를 모두 확인하지 못했어", bridge._departure_announcement)
                    self.assertIn("급하니까 내가 추천한 기본 경로로 바로 갈게", bridge._departure_announcement)
                finally:
                    await bridge.close()

    def test_route_stage_context_lists_registered_sites_for_every_scenario(self):
        for kind in ("triage", "security", "construction"):
            with self.subTest(kind=kind):
                session = SurveySession(kind=kind)
                session.confirm_prompt(**PROMPT_ARGS)
                context = server.tools.voice_context(session)
                for person in session.scenario["people"]:
                    self.assertIn(person["monitorId"], context["instructions"])
                    self.assertIn(person["siteName"], context["instructions"])
                    self.assertIn(person["clue"], context["instructions"])
                tool = next(t for t in context["tools"] if t["name"] == "set_route")
                self.assertEqual(set(tool["parameters"]["required"]), {"route", "complete", "heard"})
                self.assertNotIn("select_stop에 전달", context["instructions"])

    async def test_auto_launch_keeps_stale_route_readback_inert(self):
        self.session.confirm_prompt(**PROMPT_ARGS)
        async def fake_launch():
            return self.session.launch_mission()

        with patch.object(self.bridge.runner, "launch", new=AsyncMock(side_effect=fake_launch)):
            first = participant_turn(self.bridge, "바다")
            await self.call("select_stop", {"monitor": "monitor-1"}, first)
            second = participant_turn(self.bridge, "잔해")
            await self.call("select_stop", {"monitor": "monitor-2"}, second)
        self.assertTrue(self.bridge._route_readback_pending)
        self.assertEqual(self.session.phase, "flying")
        readbacks = [event["response"] for event in self.bridge.upstream.sent
                     if event["type"] == "response.create"
                     and event["response"].get("metadata", {}).get("routeReadback") == "true"]
        self.assertEqual(readbacks, [])

    async def test_agent_cannot_substitute_another_stop_or_reuse_prompt_consent(self):
        self.session.confirm_prompt(**PROMPT_ARGS)
        turn = participant_turn(self.bridge, "바다")
        self.assertFalse((await self.call("select_stop", {"monitor": "monitor-3"}, turn))["ok"])
        turn = participant_turn(self.bridge, "좋아")
        self.assertFalse((await self.call("select_stop", {"monitor": "monitor-1"}, turn))["ok"])
        self.assertEqual(self.session.state.draftRoute, [])

    async def test_mutation_waits_for_late_transcript_without_gating_native_response(self):
        for _ in range(len(MONITOR_IDS)):
            self.session.confirm_prompt(**PROMPT_ARGS)
        upstream = self.bridge.upstream
        upstream.incoming = [
            {"type": "input_audio_buffer.speech_started", "item_id": "audio-1"},
            {"type": "input_audio_buffer.speech_stopped", "item_id": "audio-1"},
            {"type": "input_audio_buffer.committed", "item_id": "audio-1"},
            {"type": "response.created", "response": {"id": "response-1"}},
            {"type": "response.function_call_arguments.done", "response_id": "response-1",
             "call_id": "select", "name": "select_stop", "arguments": '{"monitor":"monitor-1"}'},
        ]
        await self.bridge.pump_upstream()
        await asyncio.sleep(0)
        self.assertEqual(self.session.state.draftRoute, [])
        self.assertTrue(server.build_session()["session"]["turn_detection"]["create_response"])
        self.assertEqual(len(self.bridge.voice_turns.turns), 1)
        upstream.incoming = [
            {"type": "conversation.item.input_audio_transcription.completed",
             "item_id": "audio-1", "transcript": "바다"},
        ]
        await self.bridge.pump_upstream()
        await asyncio.gather(*self.bridge._tool_tasks)
        self.assertEqual(self.session.state.draftRoute, ["monitor-1"])

    async def test_failed_transcription_and_stale_response_cannot_authorize_actions(self):
        self.session.confirm_prompt(**PROMPT_ARGS)
        old = participant_turn(self.bridge, "바다")
        self.bridge.voice_turns.bind_response("old-response")
        self.bridge.voice_turns.begin("failed", self.session)
        self.bridge.upstream.incoming = [
            {"type": "conversation.item.input_audio_transcription.failed", "item_id": "failed"},
            {"type": "response.function_call_arguments.done", "response_id": "old-response",
             "call_id": "stale", "name": "select_stop", "arguments": '{"monitor":"monitor-1"}'},
        ]
        await self.bridge.pump_upstream()
        await asyncio.gather(*self.bridge._tool_tasks)
        self.assertFalse((await self.call("select_stop", {"monitor": "monitor-1"},
                                          self.bridge.voice_turns.latest))["ok"])
        self.assertEqual(self.session.state.draftRoute, [])
        self.assertIsNot(old, self.bridge.voice_turns.latest)

    async def test_timeout_rejects_and_followup_cannot_chain_tools(self):
        self.session.confirm_prompt(**PROMPT_ARGS)
        self.bridge.voice_turns.begin("missing-transcript", self.session)
        turn = self.bridge.voice_turns.latest
        turn.ready.wait = AsyncMock(side_effect=TimeoutError)
        self.assertFalse((await self.call("select_stop", {"monitor": "monitor-1"}, turn))["ok"])
        self.assertEqual(self.session.state.draftRoute, [])
        response = next(event for event in self.bridge.upstream.sent if event["type"] == "response.create")
        self.assertEqual(response["response"]["tool_choice"], "none")

    async def test_typed_input_uses_the_same_turn_guard(self):
        for _ in range(len(MONITOR_IDS)):
            self.session.confirm_prompt(**PROMPT_ARGS)
        browser = self.bridge.browser
        browser.incoming.put_nowait(json.dumps({"type": "text", "text": "불난 집"}))
        browser.incoming.put_nowait(None)
        with self.assertRaises(server.WebSocketDisconnect):
            await self.bridge.pump_browser()
        turn = self.bridge.voice_turns.latest
        self.assertTrue((await self.call("select_stop", {"monitor": "monitor-3"}, turn))["ok"])
        self.assertFalse((await self.call("select_stop", {"monitor": "monitor-1"}, turn))["ok"])


class StopInterpretationTests(unittest.TestCase):
    def test_polite_and_natural_destination_choices_keep_one_exact_destination(self):
        for text, expected in (
            ("불난 집이요", "monitor-3"),
            ("불난 집으로 가고 싶어", "monitor-3"),
            ("불이 난 집부터 가고 싶어요", "monitor-3"),
            ("바다에 빠진 사람을 먼저 구하고 싶어요", "monitor-1"),
            ("잔해 쪽으로 갈게요", "monitor-2"),
        ):
            for monitor in ("monitor-1", "monitor-2", "monitor-3"):
                with self.subTest(text=text, monitor=monitor):
                    self.assertEqual(names_stop(text, monitor), monitor == expected)
        for text in ("불난 집으로 가고 싶지 않아", "바다 말고 잔해로 갈게요",
                     "불난 집이나 바다로 가고 싶어", "잔해로 갈까요?",
                     "불난 집으로 가고 싶으면 출발해", "불난 집으로 가지 마세요"):
            for monitor in ("monitor-1", "monitor-2", "monitor-3"):
                self.assertFalse(names_stop(text, monitor), text)

    def test_supported_aliases_and_negation(self):
        for text, monitor in (("바다", "monitor-1"), ("1번으로 가자", "monitor-1"),
                              ("다음은 잔해", "monitor-2"), ("불난 집부터", "monitor-3"),
                              ("바다 먼저", "monitor-1"), ("바다에 빠진 사람부터 구하자", "monitor-1"),
                              ("잔해 아래 있는 사람", "monitor-2"), ("첫째", "monitor-1"),
                              ("둘째", "monitor-2"), ("셋째", "monitor-3")):
            self.assertTrue(names_stop(text, monitor), text)
        for text in ("바다는 아니야", "잔해 말고", "불난 집 가지 마", "아무거나", "응",
                     "바다 아니면 잔해", "바다 먼저 잔해 다음", "1번과 2번"):
            for monitor in ("monitor-1", "monitor-2", "monitor-3"):
                self.assertFalse(names_stop(text, monitor), text)

    def test_destination_names_across_all_scenario_kinds_tolerate_filler(self):
        # Regression coverage for a bug reported live: naming a real site
        # ("서버실", "금고", ...) with ordinary hesitation filler or a verb
        # ending outside the old fixed vocabulary got rejected as
        # stop_mismatch forever, so the mission never advanced past the
        # first destination for any of the 3 scenario kinds.
        for text, expected in (
            # security (금고=1, 서버실=2, 임원실=3)
            ("음 서버실이요", "monitor-2"), ("저기 금고 갈래", "monitor-1"),
            ("어 임원실 확인할래", "monitor-3"), ("금고부터 가볼게", "monitor-1"),
            ("일단 서버실 확인해볼게", "monitor-2"),
            # construction (위쪽통로=1, 기초공사구역=2, 오른쪽플랫폼=3)
            ("음 위쪽통로요", "monitor-1"), ("저기 기초공사구역 갈래", "monitor-2"),
            ("어 플랫폼 점검할래", "monitor-3"),
            # triage (바다=1, 잔해=2, 불난집=3)
            ("음 바다요", "monitor-1"), ("저기 잔해 갈래", "monitor-2"),
            ("어 불난 집 확인할래", "monitor-3"),
        ):
            for monitor in ("monitor-1", "monitor-2", "monitor-3"):
                with self.subTest(text=text, monitor=monitor):
                    self.assertEqual(names_stop(text, monitor), monitor == expected)
        # Filler must not turn a negation/ambiguous turn into a false accept.
        for text in ("음 서버실은 아니야", "저기 금고 말고 서버실", "어 아무 데나"):
            for monitor in ("monitor-1", "monitor-2", "monitor-3"):
                self.assertFalse(names_stop(text, monitor), text)


class DepartureInterpretationTests(unittest.TestCase):
    def test_polite_departure_requires_unconditional_affirmation(self):
        for text in ("출발해 주세요", "네, 출발시켜 주세요", "응 출발시켜줘"):
            self.assertTrue(is_affirmative(text), text)
        for text in ("출발하지 마세요", "아직 출발하지 말아 주세요",
                     "불난 집부터 가면 출발해 주세요", "출발해도 될까"):
            self.assertFalse(is_affirmative(text), text)

    def test_affirmative_survives_harmless_filler_words(self):
        for text in ("음 맞아", "어 진짜 맞아요", "그러니까 맞아", "저기 좋아요"):
            self.assertTrue(is_affirmative(text), text)
        for text in ("음", "음 흠", "저기 그러니까", "음 아니야", "그러니까 아니야"):
            self.assertFalse(is_affirmative(text), text)

    def test_affirmative_recognizes_more_natural_phrasing(self):
        # These are ordinary ways to say "yes" that previously fell through
        # the hardcoded word list and were rejected as not_consent, forcing
        # the model to re-ask the same question forever.
        for text in ("아 네 맞아요", "네 그거 맞아요", "그럼 그렇게 하자",
                     "네 그렇게 해주세요", "응 그렇게 해줘 고마워", "아 응 맞아 고마워"):
            self.assertTrue(is_affirmative(text), text)
        # Negatives and hesitations must still be rejected after the same change.
        for text in ("그건 아니고", "아니 그게 아니라", "음... 잠깐만", "뭐라고요",
                     "네 근데", "그건 아닌 것 같아"):
            self.assertFalse(is_affirmative(text), text)


class VoiceTimingTests(unittest.TestCase):
    def test_first_matching_audio_after_tool_only_response_is_measured_once(self):
        turns = VoiceTurns()
        session = SurveySession()
        turns.stop("participant", session)
        turns.mark_item("participant", "speech_stopped")
        turns.bind_response("tool")
        turns.mark_response("tool", "created")
        turns.mark_response("tool", "done")
        turns.bind_response("followup")
        turns.mark_response("followup", "created")
        turn, ms = turns.first_audio_latency("followup")
        self.assertEqual(turn.item_id, "participant")
        self.assertEqual(turn.ttfa_response_id, "followup")
        self.assertGreaterEqual(ms, 0)
        self.assertIsNone(turns.first_audio_latency("followup"))
        self.assertIsNone(turns.first_audio_latency("unmapped"))

    def test_response_timestamps_are_bounded_and_first_receipt_is_preserved(self):
        turns = VoiceTurns()
        turns.mark_response("first", "created")
        first = turns.response_timestamps["first"]["created"]
        turns.mark_response("first", "created")
        self.assertEqual(turns.response_timestamps["first"]["created"], first)
        for index in range(130):
            turns.mark_response(str(index), "created")
        self.assertEqual(len(turns.response_timestamps), 128)
        self.assertNotIn("first", turns.response_timestamps)


if __name__ == "__main__":
    unittest.main()
