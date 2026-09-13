import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from relay import server
from relay.survey import SurveySession
from relay.test_mission_runner import FakeCamera, FakeVision
from relay.test_server import Browser, Upstream, participant_turn, spoken_reply
from relay.test_survey import PROMPT_ARGS, SEARCH_PROMPT, ready
from relay.voice_turns import VoiceTurns, names_stop


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

    async def test_silence_cannot_confirm_select_or_launch(self):
        self.assertFalse((await self.call("confirm_prompt", PROMPT_ARGS))["ok"])
        self.assertEqual(self.session.data["promptPhase"], "briefing")
        self.session.confirm_prompt(**PROMPT_ARGS)
        self.assertFalse((await self.call("select_stop", {"monitor": "monitor-1"}))["ok"])
        self.assertEqual(self.session.state.draftRoute, [])
        ready(self.session)
        spoken_reply(self.bridge, route_readback=True)
        with patch.object(self.bridge.runner, "launch", new_callable=AsyncMock) as launch:
            self.assertFalse((await self.call("launch_mission", {}))["ok"])
            launch.assert_not_awaited()
        self.assertFalse(self.session.data["clockRunning"])

    async def test_description_requires_readback_then_separate_consent(self):
        turn = participant_turn(self.bridge, SEARCH_PROMPT)
        self.assertFalse((await self.call("confirm_prompt", {}, turn))["ok"])
        self.assertTrue((await self.call("prepare_prompt", PROMPT_ARGS, turn))["ok"])
        context = next(event["session"] for event in reversed(self.bridge.upstream.sent)
                       if event["type"] == "session.update" and "tools" in event["session"])
        self.assertEqual({tool["name"] for tool in context["tools"]},
                         {"get_state", "prepare_prompt", "confirm_prompt"})
        self.assertEqual(self.session.data["promptPhase"], "briefing")
        spoken_reply(self.bridge)
        self.assertFalse((await self.call("confirm_prompt", {}, turn))["ok"])
        consent = participant_turn(self.bridge, "오케이")
        self.assertTrue((await self.call("confirm_prompt", {}, consent))["ok"])
        context = next(event["session"] for event in reversed(self.bridge.upstream.sent)
                       if event["type"] == "session.update" and "tools" in event["session"])
        self.assertEqual({tool["name"] for tool in context["tools"]},
                         {"get_state", "select_stop", "clear_route", "confirm_route"})
        self.assertIsNotNone(self.bridge._route_intro_id)
        self.assertEqual(self.session.data["userPromptText"], SEARCH_PROMPT)

    async def test_agreement_without_description_or_before_readback_is_rejected(self):
        turn = participant_turn(self.bridge, "응")
        self.assertFalse((await self.call("confirm_prompt", PROMPT_ARGS, turn))["ok"])
        participant_turn(self.bridge, SEARCH_PROMPT)
        self.session.prepare_prompt(**PROMPT_ARGS)
        turn = participant_turn(self.bridge, "응")
        self.assertFalse((await self.call("confirm_prompt", PROMPT_ARGS, turn))["ok"])
        self.assertEqual(self.session.data["promptPhase"], "briefing")

    async def test_prompt_correction_is_not_consent(self):
        participant_turn(self.bridge, SEARCH_PROMPT)
        self.session.prepare_prompt(**PROMPT_ARGS)
        spoken_reply(self.bridge)
        correction = participant_turn(self.bridge, "아니 빨간 옷")
        self.assertFalse((await self.call("confirm_prompt", PROMPT_ARGS, correction))["ok"])
        self.assertIsNone(self.session.pending_prompt)
        self.assertFalse(self.bridge._prompt_readback_pending)
        yes = participant_turn(self.bridge, "응")
        self.assertFalse((await self.call("confirm_prompt", {}, yes))["ok"])

    async def test_failed_confirmation_keeps_pending_description_for_retry(self):
        participant_turn(self.bridge, SEARCH_PROMPT)
        self.session.prepare_prompt(**PROMPT_ARGS)
        spoken_reply(self.bridge)
        invalid = PROMPT_ARGS | {"appearance_constraints": [{"attribute": "invalid"}]}
        turn = participant_turn(self.bridge, "응")
        self.assertFalse((await self.call("confirm_prompt", invalid, turn))["ok"])
        spoken_reply(self.bridge)
        turn = participant_turn(self.bridge, "오케이")
        self.assertTrue((await self.call("confirm_prompt", {}, turn))["ok"])

    async def test_late_correction_invalidates_newer_consent_for_the_same_revision(self):
        self.session.prepare_prompt(**PROMPT_ARGS)
        spoken_reply(self.bridge)
        self.bridge.voice_turns.stop("correction", self.session)
        consent = participant_turn(self.bridge, "응")
        pending = asyncio.create_task(self.bridge.run_tool(
            "confirm_prompt", {}, "late-correction", from_voice=True, turn=consent))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        self.bridge.voice_turns.transcribe("correction", "아니 빨간 옷")
        self.bridge.sync_prompt_correction()
        self.assertFalse((await pending)["ok"])
        self.assertIsNone(self.session.pending_prompt)
        self.assertEqual(self.session.data["userPromptText"], "")

    async def test_unresolved_preceding_input_cannot_be_skipped_by_newer_consent(self):
        self.session.prepare_prompt(**PROMPT_ARGS)
        spoken_reply(self.bridge)
        self.bridge.voice_turns.stop("unresolved", self.session)
        unresolved = self.bridge.voice_turns.latest
        unresolved.ready.wait = AsyncMock(side_effect=TimeoutError)
        spoken_reply(self.bridge)
        consent = participant_turn(self.bridge, "응")
        self.assertFalse((await self.call("confirm_prompt", {}, consent))["ok"])
        self.assertEqual(self.session.data["userPromptText"], "")

    async def test_empty_and_hesitant_replies_preserve_draft_and_recover_after_new_readback(self):
        for text in ("", "음", "아", "뭐라고"):
            with self.subTest(text=text):
                self.session.prepare_prompt(**PROMPT_ARGS)
                self.bridge.voice_turns.prepare_prompt()
                spoken_reply(self.bridge)
                retry = participant_turn(self.bridge, text)
                self.assertFalse((await self.call("confirm_prompt", {}, retry))["ok"])
                self.assertIsNotNone(self.session.pending_prompt)
                self.assertTrue(self.bridge._prompt_readback_pending)
                too_early = participant_turn(self.bridge, "응")
                self.assertFalse((await self.call("confirm_prompt", {}, too_early))["ok"])
                spoken_reply(self.bridge)
                consent = participant_turn(self.bridge, "응")
                self.assertTrue((await self.call("confirm_prompt", {}, consent))["ok"])
                self.assertEqual(self.session.data["userPromptText"], SEARCH_PROMPT)

    async def test_hesitation_queues_dedicated_retry_even_without_a_tool_call(self):
        self.session.prepare_prompt(**PROMPT_ARGS)
        spoken_reply(self.bridge)
        self.bridge.voice_turns.stop("hesitation", self.session)
        self.bridge.upstream.incoming = [
            {"type": "response.created", "response": {"id": "native-retry"}},
            {"type": "conversation.item.input_audio_transcription.completed",
             "item_id": "hesitation", "transcript": "음"},
            {"type": "response.done", "response": {"id": "native-retry", "status": "completed"}},
        ]
        await self.bridge.pump_upstream()
        self.assertIsNotNone(self.session.pending_prompt)
        readbacks = [event["response"] for event in self.bridge.upstream.sent
                     if event["type"] == "response.create"]
        self.assertEqual(readbacks[-1]["metadata"]["promptReadback"], str(self.session.pending_prompt_revision))

    async def test_late_hesitation_or_failed_transcription_flushes_retry_after_native_completion(self):
        for event_type in ("completed", "failed"):
            with self.subTest(event_type=event_type):
                self.session.prepare_prompt(**PROMPT_ARGS)
                self.bridge.voice_turns.prepare_prompt()
                spoken_reply(self.bridge)
                self.bridge.voice_turns.stop(event_type, self.session)
                self.bridge.upstream.incoming = [
                    {"type": "response.created", "response": {"id": event_type}},
                    {"type": "response.done", "response": {"id": event_type, "status": "completed"}},
                    {"type": f"conversation.item.input_audio_transcription.{event_type}",
                     "item_id": event_type, "transcript": "음"},
                ]
                await self.bridge.pump_upstream()
                readbacks = [event["response"] for event in self.bridge.upstream.sent
                             if event["type"] == "response.create"]
                self.assertEqual(readbacks[-1]["metadata"]["promptReadback"],
                                 str(self.session.pending_prompt_revision))

    async def test_short_and_natural_confirmations_during_readback(self):
        for text in ("응", "어", "엉", "네", "예", "맞아", "오케이", "응 맞아!", "네 그렇게 해줘"):
            with self.subTest(text=text):
                self.session.prepare_prompt(**PROMPT_ARGS)
                self.bridge.voice_turns.prepare_prompt()
                revision = self.session.pending_prompt_revision
                self.bridge.voice_turns.begin_prompt_readback("speaking", revision)
                self.bridge.voice_turns.hear_response("speaking")
                # No response.done: the participant is interrupting the readback.
                turn = participant_turn(self.bridge, text)
                self.assertTrue((await self.call("confirm_prompt", {}, turn))["ok"])
                self.assertIsNone(self.session.pending_prompt)

    async def test_native_reply_without_tool_still_confirms_once_with_early_or_late_transcript(self):
        for late in (False, True):
            with self.subTest(late=late):
                self.session.prepare_prompt(**PROMPT_ARGS)
                self.bridge.voice_turns.prepare_prompt()
                spoken_reply(self.bridge)
                item_id, response_id = f"consent-{late}", f"native-{late}"
                transcript = {"type": "conversation.item.input_audio_transcription.completed",
                              "item_id": item_id, "transcript": "어"}
                done = {"type": "response.done",
                        "response": {"id": response_id, "status": "completed", "output": []}}
                self.bridge.upstream.incoming = [
                    {"type": "input_audio_buffer.speech_started", "item_id": item_id},
                    {"type": "input_audio_buffer.speech_stopped", "item_id": item_id},
                    {"type": "response.created", "response": {"id": response_id}},
                    *([done, transcript] if late else [transcript, done]),
                ]
                await self.bridge.pump_upstream()
                await asyncio.gather(*self.bridge._tool_tasks)
                self.assertEqual(self.session.data["userPromptText"], SEARCH_PROMPT)
                self.assertIsNone(self.session.pending_prompt)
                self.bridge.browser.incoming.put_nowait(json.dumps({
                    "type": "route_intro.ready", "runId": self.session.run_id,
                    "introId": self.bridge._route_intro_id}))
                self.bridge.browser.incoming.put_nowait(None)
                with self.assertRaises(server.WebSocketDisconnect):
                    await self.bridge.pump_browser()
                continuation = next(event["response"] for event in reversed(self.bridge.upstream.sent)
                                    if event["type"] == "response.create")
                for person in self.session.scenario["people"]:
                    self.assertIn(person["clue"], continuation["instructions"])
                self.assertEqual(continuation["tool_choice"], "none")
                revision = self.session.data["revision"]
                self.assertTrue((await self.call("confirm_prompt", {}, self.bridge.voice_turns.latest))["ok"])
                self.assertEqual(self.session.data["revision"], revision)
                self.assertEqual(self.session.state.draftRoute, [])

    async def test_native_silence_hesitation_or_negative_never_auto_confirms(self):
        for index, text in enumerate(("", "음", "아니")):
            self.session.prepare_prompt(**PROMPT_ARGS)
            self.bridge.voice_turns.prepare_prompt()
            spoken_reply(self.bridge)
            participant_turn(self.bridge, text)
            response_id = f"non-consent-{index}"
            self.bridge.upstream.incoming = [
                {"type": "response.created", "response": {"id": response_id}},
                {"type": "response.done", "response": {"id": response_id, "status": "completed"}},
            ]
            await self.bridge.pump_upstream()
            await asyncio.gather(*self.bridge._tool_tasks)
            self.assertEqual(self.session.data["userPromptText"], "")
            self.assertEqual(self.session.state.draftRoute, [])

    async def test_corrected_pending_revision_cannot_use_old_agreement(self):
        self.session.prepare_prompt(**PROMPT_ARGS)
        spoken_reply(self.bridge)
        old = participant_turn(self.bridge, "응")
        revised = PROMPT_ARGS | {"prompt_text": "빨간 옷", "appearance_constraints": [
            {"attribute": "shirtColor", "operator": "include", "values": ["red"]}]}
        self.session.prepare_prompt(**revised)
        self.bridge.voice_turns.prepare_prompt()
        self.assertFalse((await self.call("confirm_prompt", {}, old))["ok"])
        spoken_reply(self.bridge)
        current = participant_turn(self.bridge, "어")
        self.assertTrue((await self.call("confirm_prompt", {}, current))["ok"])
        self.assertEqual(self.session.data["userPromptText"], "빨간 옷")

    async def test_early_transcript_is_retained_and_pending_prompt_does_not_depend_on_old_transcript(self):
        self.session.prepare_prompt(**PROMPT_ARGS)
        spoken_reply(self.bridge)
        self.bridge.voice_turns.transcribe("early", "응")
        self.bridge.voice_turns.stop("early", self.session)
        turn = self.bridge.voice_turns.latest
        self.assertTrue(turn.ready.is_set())
        self.assertTrue((await self.call("confirm_prompt", {}, turn))["ok"])

    async def test_invalid_prepare_does_not_consume_turn(self):
        turn = participant_turn(self.bridge, SEARCH_PROMPT)
        self.assertFalse((await self.call("prepare_prompt", {"prompt_text": ""}, turn))["ok"])
        self.assertFalse(turn.consumed)
        self.assertTrue((await self.call("prepare_prompt", PROMPT_ARGS, turn))["ok"])
        self.assertTrue(turn.consumed)

    async def test_only_one_stop_per_reply_and_only_third_stop_is_automatic(self):
        self.session.confirm_prompt(**PROMPT_ARGS)
        first = participant_turn(self.bridge, "먼저 바다부터 가자")
        self.assertTrue((await self.call("select_stop", {"monitor": "monitor-1"}, first))["ok"])
        self.assertFalse((await self.call("select_stop", {"monitor": "monitor-2"}, first))["ok"])
        self.assertEqual(self.session.state.draftRoute, ["monitor-1"])
        second = participant_turn(self.bridge, "잔해 아래 사람")
        self.assertTrue((await self.call("select_stop", {"monitor": "monitor-2"}, second))["ok"])
        self.assertEqual(self.session.state.confirmedRoute, ["monitor-1", "monitor-2", "monitor-3"])
        self.assertEqual(self.session.phase, "ready")
        with patch.object(self.bridge.runner, "launch", new_callable=AsyncMock) as launch:
            self.assertFalse((await self.call("launch_mission", {}, second))["ok"])
            launch.assert_not_awaited()

    async def test_agent_cannot_substitute_another_stop_or_reuse_prompt_consent(self):
        self.session.confirm_prompt(**PROMPT_ARGS)
        turn = participant_turn(self.bridge, "바다")
        self.assertFalse((await self.call("select_stop", {"monitor": "monitor-3"}, turn))["ok"])
        turn = participant_turn(self.bridge, "좋아")
        self.assertFalse((await self.call("select_stop", {"monitor": "monitor-1"}, turn))["ok"])
        self.assertEqual(self.session.state.draftRoute, [])

    async def test_launch_requires_new_explicit_consent_after_route_readback(self):
        ready(self.session)
        early = participant_turn(self.bridge, "출발해")
        spoken_reply(self.bridge, route_readback=True)
        with patch.object(self.bridge.runner, "launch", new_callable=AsyncMock,
                          return_value={"ok": True, "facts": "출발", "ask": ""}) as launch:
            self.assertFalse((await self.call("launch_mission", {}, early))["ok"])
            for text in ("", "음", "아니", "아직 출발하지 마", "응 아니 잠깐만", "바다"):
                with self.subTest(text=text):
                    turn = participant_turn(self.bridge, text)
                    self.assertFalse((await self.call("launch_mission", {}, turn))["ok"])
            launch.assert_not_awaited()
            turn = participant_turn(self.bridge, "엉")
            self.assertTrue((await self.call("launch_mission", {}, turn))["ok"])
            launch.assert_awaited_once()

    async def test_selection_reply_is_not_route_readback_and_cancelled_readback_does_not_count(self):
        self.session.confirm_prompt(**PROMPT_ARGS)
        first = participant_turn(self.bridge, "바다")
        await self.call("select_stop", {"monitor": "monitor-1"}, first)
        self.bridge._response_active = False
        second = participant_turn(self.bridge, "잔해")
        await self.call("select_stop", {"monitor": "monitor-2"}, second)
        readbacks = [event["response"] for event in self.bridge.upstream.sent
                     if event["type"] == "response.create" and
                     event["response"].get("metadata", {}).get("routeReadback") == "true"]
        self.assertEqual(len(readbacks), 1)
        self.assertIn("이 경로로 출발할까?", readbacks[0]["instructions"])
        spoken_reply(self.bridge)
        self.assertFalse(self.bridge.voice_turns.confirmed_route_replied)
        self.bridge.upstream.incoming = [
            {"type": "response.created", "response": {
                "id": "route-readback", "metadata": readbacks[0]["metadata"]}},
            {"type": "response.audio.delta", "response_id": "route-readback", "delta": "audio"},
            {"type": "response.done", "response": {"id": "route-readback", "status": "cancelled"}},
        ]
        await self.bridge.pump_upstream()
        with patch.object(self.bridge.runner, "launch", new_callable=AsyncMock) as launch:
            turn = participant_turn(self.bridge, "응")
            self.assertFalse((await self.call("launch_mission", {}, turn))["ok"])
            launch.assert_not_awaited()

    async def test_departure_waits_for_matching_browser_playback_acknowledgement(self):
        ready(self.session)
        turns = self.bridge.voice_turns
        turns.begin_route_readback("route", self.session)
        turns.hear_response("route")
        turns.finish_response({"id": "route", "status": "completed"}, self.session)
        self.assertFalse(turns.confirmed_route_replied)
        early = participant_turn(self.bridge, "응")
        turns.finish_route_playback("stale", self.session)
        self.assertFalse(turns.confirmed_route_replied)
        for run_id in ("old-run", self.session.run_id):
            self.bridge.browser.incoming.put_nowait(json.dumps({
                "type": "voice.reply_drained", "responseId": "route", "runId": run_id}))
            self.bridge.browser.incoming.put_nowait(None)
            with self.assertRaises(server.WebSocketDisconnect):
                await self.bridge.pump_browser()
            self.assertEqual(turns.confirmed_route_replied, run_id == self.session.run_id)
        self.assertIsNotNone(await turns.authorize("launch_mission", {}, early, self.session))
        current = participant_turn(self.bridge, "응")
        self.assertIsNone(await turns.authorize("launch_mission", {}, current, self.session))

    async def test_mutation_waits_for_late_transcript_without_gating_native_response(self):
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
