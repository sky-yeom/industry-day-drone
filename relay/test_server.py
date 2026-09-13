import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import WebSocketDisconnect
from relay import server
from relay import tools
from relay.survey import SCENARIO, SurveySession
from relay.test_mission_runner import FakeCamera, FakeVision, settle
from relay.test_survey import PROMPT_ARGS, SEARCH_PROMPT, ready


class Browser:
    def __init__(self, messages=(), voice="0"):
        self.events = []
        self.incoming = asyncio.Queue()
        for message in messages:
            self.incoming.put_nowait(json.dumps(message))
        self.query_params = {"voice": voice, "turnTaking": "after-playback-v1"}
        self.closed = False

    async def accept(self):
        pass

    async def receive_text(self):
        value = await self.incoming.get()
        if value is None:
            raise WebSocketDisconnect()
        return value

    async def send_text(self, text):
        self.events.append(json.loads(text))

    async def close(self):
        self.closed = True


class Upstream:
    def __init__(self, incoming=()):
        self.sent = []
        self.incoming = list(incoming)
        self.closed = False

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self):
        return json.dumps({"type": "session.updated"})

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.incoming:
            raise StopAsyncIteration
        return json.dumps(self.incoming.pop(0))


def participant_turn(bridge, text):
    item_id = f"participant-{len(bridge.voice_turns.turns)}"
    bridge.voice_turns.stop(item_id, bridge.session)
    bridge.voice_turns.transcribe(item_id, text)
    bridge.sync_prompt_correction()
    return bridge.voice_turns.latest


def spoken_reply(bridge, *, route_readback=False):
    response_id = f"reply-{len(bridge.voice_turns.responses)}"
    bridge.voice_turns.bind_response(response_id)
    if route_readback:
        bridge.voice_turns.begin_route_readback(response_id, bridge.session)
    elif bridge.session.pending_prompt:
        bridge.voice_turns.begin_prompt_readback(response_id, bridge.session.pending_prompt_revision)
        bridge.voice_turns.hear_response(response_id)
    bridge.voice_turns.finish_response({
        "id": response_id, "status": "completed",
        "output": [{"type": "message", "role": "assistant",
                    "content": [{"type": "audio", "transcript": "확인 질문"}]}],
    }, bridge.session)
    if route_readback:
        bridge.voice_turns.finish_route_playback(response_id, bridge.session)


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.browser = Browser()
        self.session = SurveySession()
        self.camera, self.vision = FakeCamera(), FakeVision()
        self.bridge = server.Bridge(self.browser, self.session, (self.camera, self.vision))

    async def asyncTearDown(self):
        await self.bridge.close()

    async def test_stable_command_ids_idempotence_and_validation(self):
        self.session.confirm_prompt(SEARCH_PROMPT)
        args = {"monitor": "monitor-3"}
        first = await self.bridge.run_tool("select_stop", args, "request-1")
        second = await self.bridge.run_tool("select_stop", args, "request-1")
        self.assertEqual(first, second)
        self.assertEqual(self.session.state.draftRoute, ["monitor-3"])
        self.assertFalse((await self.bridge.run_tool("clear_route", {}, "request-1"))["ok"])
        activity = [e for e in self.browser.events if e["type"].startswith("tool.")]
        self.assertTrue(all(e["id"] == "request-1" for e in activity))
        self.assertFalse((await self.bridge.run_tool("launch_mission", {"mode": "azure"}, "bad"))["ok"])

    async def test_opening_preserves_rules_and_asks_for_participant_description(self):
        vad = server.build_session()["session"]["turn_detection"]
        self.assertFalse(vad["interrupt_response"])
        self.assertTrue(vad["create_response"])
        self.assertNotIn("remove_filler_words", vad)
        upstream = Upstream()
        self.bridge.upstream = upstream
        await self.bridge.greet()
        await self.bridge.greet()
        self.assertEqual(len(upstream.sent), 1)
        response = upstream.sent[0]["response"]
        self.assertTrue(response["instructions"].startswith(tools.SYSTEM_PROMPT))
        self.assertIn(tools.OPENING_QUESTION, response["instructions"])
        self.assertIn(f"읽을 문장: {tools.GREETING}", response["instructions"])
        self.assertIn("정확히 그대로", response["instructions"])
        self.assertNotIn("상황을 한 문장으로만 안내하세요", response["instructions"])
        self.assertNotIn(SCENARIO["targetAppearance"]["description"], response["instructions"])
        self.assertNotIn("초록색 티셔츠 입은 사람 찾으면", response["instructions"])
        self.assertIn("특징의 종류, 예시, 추천 답변을 말하지 않습니다", response["instructions"])
        self.assertEqual(response["tool_choice"], "none")
        self.assertEqual(self.session.data["promptPhase"], "briefing")
        self.assertEqual(self.session.data["userPromptText"], "")
        self.assertEqual(self.session.state.draftRoute, [])

    async def test_voice_confirms_saved_draft_without_replacement_arguments(self):
        definitions = {tool["name"]: tool for tool in tools.TOOLS}
        self.assertEqual(definitions["confirm_prompt"]["parameters"]["properties"], {})
        self.assertIn("prompt_text", definitions["prepare_prompt"]["parameters"]["required"])

    async def test_vad_options_match_selected_provider_contract(self):
        with patch.object(server.config, "VAD_TYPE", "server_vad"):
            vad = server.build_session()["session"]["turn_detection"]
            self.assertNotIn("speech_duration_ms", vad)
            self.assertNotIn("languages", vad)
            self.assertFalse(vad["interrupt_response"])
            self.assertTrue(vad["create_response"])
        with patch.object(server.config, "VAD_TYPE", "azure_semantic_vad_multilingual"):
            vad = server.build_session()["session"]["turn_detection"]
            self.assertFalse(vad["remove_filler_words"])
            self.assertFalse(vad["interrupt_response"])
            self.assertTrue(vad["create_response"])
            self.assertEqual(vad["languages"], ["ko"])
            self.assertEqual(vad["speech_duration_ms"], server.config.SPEECH_DURATION_MS)

    async def test_narration_override_keeps_prompt_confirmation_rules(self):
        self.bridge.upstream = Upstream()
        await self.bridge.request_response("참가자에게 현재 상태를 설명하세요.")
        instructions = self.bridge.upstream.sent[0]["response"]["instructions"]
        self.assertTrue(instructions.startswith(tools.SYSTEM_PROMPT))
        self.assertIn("사용자의 프롬프트를 대신 만들거나 미리 채우지 않습니다", instructions)
        self.assertIn("정답으로 고치거나 빠진 특징을 채우지 않습니다", instructions)

    async def test_confirm_prompt_preserves_participant_words_without_target_defaults(self):
        self.session.data["mode"] = "azure"
        for text, constraints, unsupported in (
            ("안경 쓴 사람", [], ["안경 쓴 사람"]),
            ("빨간 옷", [{"attribute": "shirtColor", "operator": "include",
                        "values": ["red"]}], []),
        ):
            with self.subTest(text=text):
                outcome = await self.bridge.run_tool("confirm_prompt", {
                    "prompt_text": text,
                    "appearance_constraints": constraints,
                    "unsupported_appearance": unsupported,
                })
                self.assertTrue(outcome["ok"])
                snapshot = self.session.snapshot()
                self.assertEqual(snapshot["userPromptText"], text)
                self.assertEqual(snapshot["appearanceConstraints"], constraints)
                self.assertEqual(snapshot["unsupportedAppearance"], unsupported)
                self.assertIn(text, outcome["facts"])
                self.assertNotIn(SCENARIO["targetAppearance"]["description"], outcome["facts"])

    async def test_departure_closes_voice_but_mission_and_text_results_continue(self):
        ready(self.session)
        self.session.scenario.update(travelMs=0, captureMs=0)
        self.vision.block = asyncio.Event()
        upstream = Upstream()
        self.bridge.upstream = upstream
        self.bridge._response_active = True
        self.bridge._pending_tools = 1
        spoken_reply(self.bridge, route_readback=True)
        await self.bridge.handle_tool_call(
            {"name": "launch_mission", "call_id": "launch", "arguments": "{}"},
            turn=participant_turn(self.bridge, "응"))
        await settle(lambda: len(self.vision.calls) == 1)
        self.assertTrue(any(e["type"] == "mission.launch" for e in self.browser.events))
        self.assertFalse(any(e["type"] == "response.create" for e in upstream.sent))
        upstream.incoming = [{"type": "response.done", "response": {"id": "tool-response", "status": "completed"}}]
        await self.bridge.pump_upstream()
        request = upstream.sent[-1]["response"]
        self.assertEqual(request["metadata"], {"missionLaunch": "true", "runId": self.session.run_id})
        self.assertIn(tools.DEPARTURE_ANNOUNCEMENT, request["instructions"])
        self.assertEqual(request["tool_choice"], "none")
        response = {"id": "departure", "metadata": request["metadata"]}
        upstream.incoming = [
            {"type": "response.created", "response": response},
            {"type": "response.done", "response": {"id": "unrelated", "status": "completed"}},
            {"type": "response.audio.delta", "delta": "audio"},
            {"type": "response.done", "response": response | {"status": "completed"}},
        ]
        await self.bridge.pump_upstream()
        self.assertTrue(upstream.closed)
        self.assertFalse(self.browser.closed)
        self.assertEqual(self.session.phase, "analyzing")
        self.assertEqual([e["responseId"] for e in self.browser.events if e["type"] == "mission.launch.done"], ["departure"])
        self.vision.block.set()
        await settle(lambda: any(e["type"] == "mission.debrief" for e in self.browser.events))
        self.assertEqual(self.session.data["score"]["rescuedCount"], 3)
        self.assertEqual(sum(e["type"] == "response.create" for e in upstream.sent), 1)
        self.assertFalse(any(e["type"] == "mission.debrief.response" for e in self.browser.events))

    async def test_failed_departure_retries_once_without_ending_mission(self):
        ready(self.session)
        self.vision.block = asyncio.Event()
        upstream = Upstream()
        self.bridge.upstream = upstream
        self.bridge._pending_tools = 1
        spoken_reply(self.bridge, route_readback=True)
        await self.bridge.handle_tool_call(
            {"name": "launch_mission", "call_id": "launch", "arguments": "{}"},
            turn=participant_turn(self.bridge, "출발해"))
        for attempt in range(2):
            response = {"id": f"departure-{attempt}", "metadata": upstream.sent[-1]["response"]["metadata"]}
            upstream.incoming = [
                {"type": "response.created", "response": response},
                {"type": "response.done", "response": response | {"status": "cancelled"}},
            ]
            await self.bridge.pump_upstream()
        self.assertFalse(any(e["type"] == "mission.launch.done" for e in self.browser.events))
        self.assertEqual(sum(e["type"] == "mission.launch.failed" for e in self.browser.events), 1)
        self.assertEqual(sum(e["type"] == "response.create" for e in upstream.sent), 2)
        self.assertTrue(upstream.closed)
        self.assertNotIn(self.session.phase, ("complete", "aborted"))

    async def test_results_reconnect_output_only_once_after_playback_ready(self):
        self.session.abort_mission()
        self.bridge._voice_stopped = True
        self.bridge._launch_response_id = "departure"
        await self.bridge.runner._notify()
        self.assertIsNone(self.bridge._results_task)
        await self.bridge.start_result_audio("stale-run")
        self.assertIsNone(self.bridge._results_task)
        response = {"id": "results", "metadata": {"missionDebrief": "true", "runId": self.session.run_id}}
        upstream = Upstream([
            {"type": "response.created", "response": response},
            {"type": "response.audio.delta", "delta": "audio"},
            {"type": "response.done", "response": response | {"status": "completed"}},
        ])

        @asynccontextmanager
        async def connect(*args, **kwargs):
            try:
                yield upstream
            finally:
                await upstream.close()

        token = SimpleNamespace(get_token=AsyncMock(return_value=SimpleNamespace(token="test-token")))
        with patch.object(server, "credential", return_value=token), \
                patch.object(server.websockets, "connect", side_effect=connect) as connection:
            await self.bridge.start_result_audio(self.session.run_id)
            await self.bridge.start_result_audio(self.session.run_id)
            await self.bridge._results_task
        self.assertEqual(connection.call_count, 1)
        setup = upstream.sent[0]["session"]
        self.assertFalse(setup["turn_detection"]["create_response"])
        self.assertFalse(setup["turn_detection"]["interrupt_response"])
        self.assertEqual(setup["tools"], [])
        request = next(e["response"] for e in upstream.sent if e["type"] == "response.create")
        self.assertEqual(request["metadata"]["missionDebrief"], "true")
        self.assertEqual(request["tool_choice"], "none")
        self.assertTrue(any(e["type"] == "mission.debrief.done" for e in self.browser.events))
        self.assertTrue(upstream.closed)
        self.assertFalse(self.browser.closed)

    async def test_result_voice_failure_is_visible_without_changing_outcome(self):
        self.session.abort_mission()
        self.bridge._voice_stopped = True
        await self.bridge.runner._notify()
        token = SimpleNamespace(get_token=AsyncMock(side_effect=OSError("unavailable")))
        with patch.object(server, "credential", return_value=token):
            await self.bridge.start_result_audio(self.session.run_id)
            await self.bridge._results_task
        self.assertEqual(self.session.phase, "aborted")
        self.assertTrue(any(e["type"] == "mission.debrief.failed" for e in self.browser.events))
        self.assertFalse(any(e["type"] == "mission.debrief.done" for e in self.browser.events))

    async def test_result_voice_cancelled_when_browser_disconnects(self):
        self.session.abort_mission()
        self.bridge._voice_stopped = True
        await self.bridge.runner._notify()
        started = asyncio.Event()

        async def get_token(scope):
            started.set()
            await asyncio.Event().wait()

        with patch.object(server, "credential", return_value=SimpleNamespace(get_token=get_token)):
            await self.bridge.start_result_audio(self.session.run_id)
            await started.wait()
            await self.bridge.close()
        self.assertTrue(self.bridge._results_task.cancelled())
        self.assertFalse(any(e["type"] == "mission.debrief.failed" for e in self.browser.events))

    async def test_result_voice_stops_after_failed_retry(self):
        self.session.abort_mission()
        self.bridge._voice_stopped = True
        await self.bridge.runner._notify()
        metadata = {"missionDebrief": "true", "runId": self.session.run_id}
        upstream = Upstream([
            {"type": "response.created", "response": {"id": "first", "metadata": metadata}},
            {"type": "response.done", "response": {"id": "first", "status": "failed"}},
            {"type": "response.created", "response": {"id": "retry", "metadata": metadata}},
            {"type": "response.done", "response": {"id": "retry", "status": "failed"}},
        ])
        connection = AsyncMock()
        connection.__aenter__.return_value = upstream
        token = SimpleNamespace(get_token=AsyncMock(return_value=SimpleNamespace(token="test-token")))
        with patch.object(server, "credential", return_value=token), \
                patch.object(server.websockets, "connect", return_value=connection):
            await self.bridge.start_result_audio(self.session.run_id)
            await self.bridge._results_task
        self.assertEqual(sum(e["type"] == "response.create" for e in upstream.sent), 2)
        self.assertTrue(any(e["type"] == "mission.debrief.failed" for e in self.browser.events))
        self.assertFalse(any(e["type"] == "mission.debrief.done" for e in self.browser.events))

    async def test_voice_tool_and_browser_share_dispatch_no_response_overlap(self):
        self.session.confirm_prompt(SEARCH_PROMPT)
        upstream = Upstream()
        self.bridge.upstream = upstream
        self.bridge._response_active = True
        self.bridge._pending_tools = 1
        await self.bridge.handle_tool_call({
            "name": "select_stop", "call_id": "voice-call",
            "arguments": '{"monitor":"monitor-3"}'}, turn=participant_turn(self.bridge, "불난 집"))
        self.assertFalse(any(e["type"] == "response.create" for e in upstream.sent))
        output = next(event for event in upstream.sent if event["type"] == "conversation.item.create")
        self.assertEqual(output["item"]["call_id"], "voice-call")
        upstream.incoming = [{"type": "response.done", "response": {"id": "previous", "status": "completed"}}]
        await self.bridge.pump_upstream()
        self.assertEqual(sum(e["type"] == "response.create" for e in upstream.sent), 1)
        self.assertEqual(self.session.state.draftRoute, ["monitor-3"])
        self.assertEqual(next(e["id"] for e in self.browser.events if e["type"] == "tool.finished"), "voice-call")

    async def test_final_debrief_correlates_only_its_completed_response(self):
        original = Upstream()
        self.bridge.upstream = original
        self.bridge._response_active = True
        self.session.abort_mission()
        await self.bridge.runner._notify()
        self.assertFalse(original.sent)
        self.assertTrue(original.closed)
        self.assertTrue(self.bridge._voice_stopped)
        self.assertIsNone(self.bridge._results_task)
        self.assertFalse(any(e["type"] == "mission.debrief.done" for e in self.browser.events))
        response = {"id": "final", "metadata": {"missionDebrief": "true", "runId": self.session.run_id}}
        upstream = Upstream([
            {"type": "response.created", "response": response},
            {"type": "response.done", "response": {"id": "unrelated", "status": "completed"}},
            {"type": "response.audio.delta", "delta": "audio"},
            {"type": "response.done", "response": response | {"status": "completed"}}])
        connection = AsyncMock()
        connection.__aenter__.return_value = upstream
        token = SimpleNamespace(get_token=AsyncMock(return_value=SimpleNamespace(token="test-token")))
        with patch.object(server, "credential", return_value=token), \
                patch.object(server.websockets, "connect", return_value=connection):
            await self.bridge.start_result_audio(self.session.run_id)
            await self.bridge.start_result_audio(self.session.run_id)
            await self.bridge._results_task
        request = next(e["response"] for e in upstream.sent if e["type"] == "response.create")
        self.assertEqual(request["metadata"]["runId"], self.session.run_id)
        self.assertEqual(request["tool_choice"], "none")
        debrief_events = [e for e in self.browser.events if e["type"].startswith("mission.debrief")]
        self.assertEqual([e["type"] for e in debrief_events],
                         ["mission.debrief", "mission.debrief.response", "mission.debrief.done"])
        self.assertEqual(debrief_events[-1]["responseId"], "final")
        self.assertFalse(self.browser.closed)

    async def test_native_audio_create_response_not_transcription_gated(self):
        self.assertTrue(server.build_session()["session"]["turn_detection"]["create_response"])
        upstream = Upstream([
            {"type": "input_audio_buffer.speech_stopped"},
            {"type": "response.done", "response": {"id": "interrupted"}},
        ])
        self.bridge.upstream = upstream
        self.bridge._response_requested = True
        await self.bridge.pump_upstream()
        self.assertFalse(upstream.sent)
        upstream.incoming = [
            {"type": "response.created", "response": {"id": "native"}},
            {"type": "response.done", "response": {"id": "native"}},
        ]
        await self.bridge.pump_upstream()
        self.assertEqual(len(upstream.sent), 1)

    async def test_vad_does_not_cancel_generation_automatically(self):
        self.bridge.upstream = Upstream([
            {"type": "response.created", "response": {"id": "route-question"}},
            {"type": "input_audio_buffer.speech_started", "item_id": "noise"},
            {"type": "input_audio_buffer.speech_stopped", "item_id": "noise"},
        ])
        await self.bridge.pump_upstream()
        self.assertEqual(self.bridge._active_response_id, "route-question")
        self.assertTrue(self.bridge._native_response_pending)
        self.assertFalse(self.bridge.upstream.sent)
        self.assertTrue(any(e["type"] == "input_audio_buffer.speech_started" for e in self.browser.events))

    async def test_interrupt_retains_generation_and_does_not_mutate_consent(self):
        ready(self.session)
        participant_turn(self.bridge, "출발해")
        upstream = Upstream([{"type": "response.created", "response": {"id": "route-question"}}])
        self.bridge.upstream = upstream
        await self.bridge.pump_upstream()
        snapshot = self.session.snapshot()
        turn = self.bridge.voice_turns.latest
        self.browser.incoming.put_nowait(json.dumps({
            "type": "voice.interrupt", "runId": self.session.run_id,
            "responseIds": ["already-finished", "route-question"]}))
        self.browser.incoming.put_nowait(None)
        with self.assertRaises(WebSocketDisconnect):
            await self.bridge.pump_browser()
        self.assertFalse(upstream.sent, "provider cancellation can incorrectly cancel a newer response")
        self.assertEqual(self.session.snapshot(), snapshot)
        self.assertFalse(turn.consumed)
        self.assertEqual(self.bridge._active_response_id, "route-question")
        self.assertTrue(self.bridge._response_active, "wait for provider completion, not browser assertions")
        self.assertFalse(self.bridge._response_requested)

    async def test_interrupt_rejects_finished_response_and_never_cancels_new_native_reply(self):
        upstream = Upstream([{"type": "response.created", "response": {"id": "old"}}])
        self.bridge.upstream = upstream
        await self.bridge.pump_upstream()
        msg = {"runId": self.session.run_id, "responseIds": ["old"]}
        upstream.incoming = [{"type": "response.done", "response": {"id": "old", "status": "completed"}}]
        await self.bridge.pump_upstream()
        with self.assertLogs("relay", level="WARNING"):
            await self.bridge.interrupt_voice(msg)
        upstream.incoming = [{"type": "response.created", "response": {"id": "new-native"}}]
        await self.bridge.pump_upstream()
        with self.assertLogs("relay", level="WARNING"):
            await self.bridge.interrupt_voice(msg)
        self.assertFalse(upstream.sent)
        self.assertEqual(self.bridge._active_response_id, "new-native")
        self.assertTrue(self.bridge._response_active)

    async def test_interrupt_rejects_malformed_stale_and_output_only_requests(self):
        self.bridge.upstream = Upstream()
        self.bridge._active_response_id = "active"
        self.bridge._response_active = True
        snapshot = self.session.snapshot()
        valid = {"runId": self.session.run_id, "responseIds": ["active"]}
        malformed = [
            {}, valid | {"runId": None}, valid | {"runId": "stale"},
            valid | {"responseIds": None}, valid | {"responseIds": "active"},
            valid | {"responseIds": []}, valid | {"responseIds": ["active"] * 65},
            valid | {"responseIds": [""]}, valid | {"responseIds": [" "]},
            valid | {"responseIds": ["active", 1]},
        ]
        for msg in malformed:
            with self.subTest(msg=msg), self.assertLogs("relay", level="WARNING"):
                await self.bridge.interrupt_voice(msg)
        for flag in ("_launch_pending", "_departure_voice_finished", "_voice_stopped", "_closing"):
            setattr(self.bridge, flag, True)
            with self.subTest(flag=flag), self.assertLogs("relay", level="WARNING"):
                await self.bridge.interrupt_voice(valid)
            setattr(self.bridge, flag, False)
        self.browser.incoming.put_nowait(json.dumps({"type": "response.cancel", "response_id": "active"}))
        self.browser.incoming.put_nowait(None)
        with self.assertLogs("relay", level="WARNING"), self.assertRaises(WebSocketDisconnect):
            await self.bridge.pump_browser()
        self.assertFalse(self.bridge.upstream.sent)
        self.assertEqual(self.session.snapshot(), snapshot)
        await self.bridge.interrupt_voice(valid | {"responseIds": ["active"] * 64})
        self.assertFalse(self.bridge.upstream.sent)

    async def test_native_collision_recovers_once_after_completion_or_matched_cancellation(self):
        for status in ("completed", "cancelled"):
            with self.subTest(status=status):
                upstream = Upstream([
                    {"type": "response.created", "response": {"id": "old"}},
                    {"type": "input_audio_buffer.speech_started", "item_id": "participant"},
                    {"type": "input_audio_buffer.speech_stopped", "item_id": "participant"},
                    {"type": "error", "error": {"code": "conversation_already_has_active_response"}},
                ])
                self.bridge.upstream = upstream
                self.bridge._last_response = {"response": {"instructions": "obsolete route readback"}}
                self.bridge._response_requested = False
                with self.assertLogs("relay", level="ERROR"):
                    await self.bridge.pump_upstream()
                self.assertFalse(self.bridge._native_response_pending)
                self.assertTrue(self.bridge._native_response_retry)
                self.assertFalse(upstream.sent)
                self.assertFalse(self.bridge.voice_turns.latest.ready.is_set(), "recovery need not wait for ASR")
                if status == "cancelled":
                    await self.bridge.interrupt_voice({"runId": self.session.run_id, "responseIds": ["old"]})
                    self.assertFalse(upstream.sent)
                upstream.incoming = [{"type": "response.done", "response": {"id": "old", "status": status}}]
                await self.bridge.pump_upstream()
                requests = [e for e in upstream.sent if e["type"] == "response.create"]
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0]["response"]["tool_choice"], "auto")
                self.assertEqual(requests[0]["response"]["metadata"]["participantItemId"], "participant")
                self.assertNotIn("instructions", requests[0]["response"])
                self.assertFalse(self.bridge._native_response_retry)
                upstream.incoming = [
                    {"type": "response.created", "response": {"id": "reply"}},
                    {"type": "response.done", "response": {"id": "reply", "status": "completed"}},
                ]
                await self.bridge.pump_upstream()
                await self.bridge.flush_response()
                self.assertEqual(sum(e["type"] == "response.create" for e in upstream.sent), 1)
        self.assertFalse(any(e["type"] == "error" for e in self.browser.events),
                         "recoverable native collisions must not end browser playback")

    async def test_native_collision_after_old_done_recovers_without_asr(self):
        self.bridge.upstream = Upstream([
            {"type": "response.created", "response": {"id": "old"}},
            {"type": "input_audio_buffer.speech_stopped", "item_id": "participant"},
            {"type": "response.done", "response": {"id": "old", "status": "completed"}},
            {"type": "error", "error": {"code": "conversation_already_has_active_response"}},
        ])
        with self.assertLogs("relay", level="ERROR"):
            await self.bridge.pump_upstream()
        self.assertFalse(self.bridge._native_response_pending)
        self.assertFalse(self.bridge._native_response_retry)
        self.assertEqual(sum(e["type"] == "response.create" for e in self.bridge.upstream.sent), 1)

    async def test_overlapping_native_turn_recovers_even_without_provider_error(self):
        self.bridge.upstream = Upstream([
            {"type": "response.created", "response": {"id": "old"}},
            {"type": "input_audio_buffer.speech_started", "item_id": "participant"},
            {"type": "input_audio_buffer.speech_stopped", "item_id": "participant"},
            {"type": "response.done", "response": {"id": "old", "status": "completed"}},
        ])
        await self.bridge.pump_upstream()
        requests = [event for event in self.bridge.upstream.sent if event["type"] == "response.create"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["response"]["metadata"]["nativeTurnRetry"], "true")
        self.assertFalse(self.bridge._native_response_pending)
        self.bridge.upstream.incoming = [
            {"type": "response.created", "response": {"id": "native"}},
            {"type": "error", "error": {"code": "conversation_already_has_active_response"}},
            {"type": "response.done", "response": {"id": "native", "status": "completed"}},
        ]
        with self.assertLogs("relay", level="ERROR"):
            await self.bridge.pump_upstream()
        self.assertEqual(sum(event["type"] == "response.create" for event in self.bridge.upstream.sent), 1)

    async def test_native_reply_creation_satisfies_pending_collision_retry(self):
        self.bridge.upstream = Upstream([
            {"type": "response.created", "response": {"id": "old"}},
            {"type": "input_audio_buffer.speech_stopped", "item_id": "participant"},
            {"type": "error", "error": {"code": "conversation_already_has_active_response"}},
            {"type": "response.created", "response": {"id": "native"}},
            {"type": "response.done", "response": {"id": "old", "status": "cancelled"}},
            {"type": "response.done", "response": {"id": "native", "status": "completed"}},
        ])
        with self.assertLogs("relay", level="ERROR"):
            await self.bridge.pump_upstream()
        self.assertFalse(self.bridge._native_response_pending)
        self.assertFalse(self.bridge._native_response_retry)
        self.assertFalse(self.bridge.upstream.sent)

    async def test_native_collision_preserves_pending_prompt_readback_and_tool_barrier(self):
        self.session.prepare_prompt(**PROMPT_ARGS)
        self.bridge._prompt_readback_pending = True
        self.bridge._pending_tools = 1
        collision = {"type": "error", "error": {"code": "conversation_already_has_active_response"}}
        self.bridge.upstream = Upstream([
            {"type": "response.created", "response": {"id": "old"}},
            {"type": "input_audio_buffer.speech_stopped", "item_id": "participant"},
            collision, collision,
            {"type": "response.done", "response": {"id": "old", "status": "completed"}},
        ])
        with self.assertLogs("relay", level="ERROR"):
            await self.bridge.pump_upstream()
        self.assertFalse(self.bridge.upstream.sent)
        self.assertTrue(self.bridge._native_response_retry)
        self.bridge._pending_tools = 0
        await self.bridge.flush_response()
        response = self.bridge.upstream.sent[0]["response"]
        self.assertEqual(response["tool_choice"], "none")
        self.assertEqual(response["metadata"]["promptReadback"], str(self.session.pending_prompt_revision))
        self.assertIsNotNone(self.session.pending_prompt)
        self.assertFalse(self.bridge._native_response_retry)
        await self.bridge.flush_response()
        self.assertEqual(len(self.bridge.upstream.sent), 1)

    async def test_early_debrief_waits_for_departure_and_rejects_stale_results(self):
        upstream = Upstream()
        self.bridge.upstream = upstream
        self.bridge._launch_pending = True
        self.session.abort_mission()
        await self.bridge.runner._notify()
        self.assertFalse(upstream.closed)
        self.assertFalse(upstream.sent)
        self.assertFalse(self.bridge._debrief_pending)
        await self.bridge.start_result_audio(self.session.run_id)
        self.assertIsNone(self.bridge._results_task)
        text = self.bridge._result_text
        await self.bridge.publish_mission({"type": "mission.debrief", "runId": "stale", "text": "old result"})
        self.assertEqual(self.bridge._result_text, text)
        self.assertFalse(any(e["type"] == "mission.debrief.done" for e in self.browser.events))

    async def test_result_reconnect_waits_for_original_pump_retirement(self):
        self.session.abort_mission()
        self.bridge.upstream = Upstream()
        await self.bridge.runner._notify()
        retired = asyncio.Event()
        self.bridge._original_pump = asyncio.create_task(retired.wait())
        token = SimpleNamespace(get_token=AsyncMock(side_effect=OSError("unavailable")))
        with patch.object(server, "credential", return_value=token):
            await self.bridge.start_result_audio(self.session.run_id)
            await asyncio.sleep(0)
            token.get_token.assert_not_awaited()
            retired.set()
            with self.assertLogs("relay", level="ERROR"):
                await self.bridge._results_task
        token.get_token.assert_awaited_once()

    async def test_browser_cannot_reconfigure_or_fabricate_upstream_results(self):
        self.bridge.upstream = Upstream()
        self.browser.incoming.put_nowait(json.dumps({"type": "session.update", "session": {"tools": []}}))
        self.browser.incoming.put_nowait(json.dumps({"type": "conversation.item.create", "item": {"type": "function_call_output"}}))
        self.browser.incoming.put_nowait(None)
        with self.assertRaises(WebSocketDisconnect):
            await self.bridge.pump_browser()
        self.assertFalse(self.bridge.upstream.sent)

    async def test_disconnect_cancels_and_awaits_all_work(self):
        ready(self.session)
        self.session.scenario["travelMs"] = 0
        self.session.scenario["captureMs"] = 0
        self.vision.block = asyncio.Event()
        await self.bridge.runner.launch()
        await settle(lambda: len(self.vision.calls) == 1)
        await self.bridge.close()
        self.assertTrue(self.vision.cancelled)
        self.assertTrue(self.bridge.runner._work.done())
        self.assertTrue(self.bridge.runner._deadlines.done())
        self.assertEqual(self.session.phase, "aborted")
        self.assertIsNone(self.session.data["score"])

    async def test_voice_auth_failure_reports_setup_action_without_opening_upstream(self):
        browser = Browser(voice="1")
        token = SimpleNamespace(get_token=AsyncMock(side_effect=RuntimeError("private credential details")))
        with patch.object(server, "credential", return_value=token), \
                patch.object(server.websockets, "connect") as connect, \
                self.assertLogs("relay", level="ERROR"):
            await server.ws_endpoint(browser)
        connect.assert_not_called()
        self.assertTrue(browser.closed)
        self.assertEqual(len(browser.events), 1)
        error = browser.events[0]
        self.assertEqual(error["type"], "relay.error")
        self.assertIn("Azure 음성 인증", error["message"])
        self.assertIn("Azure 로그인", error["message"])
        self.assertNotIn("private credential", error["message"])
        self.assertNotIn("음성 없이", error["message"])

    async def test_voice_rejects_unsupported_turn_taking_before_authentication(self):
        for version in (None, "permissive"):
            browser = Browser(voice="1")
            browser.query_params["turnTaking"] = version
            with patch.object(server, "credential") as credential:
                await server.ws_endpoint(browser)
            credential.assert_not_called()
            self.assertTrue(browser.closed)
            self.assertEqual(browser.events[0]["code"], "VOICE_TURN_TAKING_UNSUPPORTED")

    async def test_voice_ready_advertises_strict_protocol_after_configuration(self):
        browser = Browser(voice="1")
        upstream = Upstream()
        connection = AsyncMock()
        connection.__aenter__.return_value = upstream
        token = SimpleNamespace(get_token=AsyncMock(return_value=SimpleNamespace(token="test-token")))
        with patch.object(server, "credential", return_value=token), \
                patch.object(server.websockets, "connect", return_value=connection), \
                patch.object(server.config, "VOICE_DIAGNOSTICS", True):
            await server.ws_endpoint(browser)
        advertised = next(e for e in browser.events if e["type"] == "relay.ready")
        self.assertEqual(advertised["turnTaking"], "after-playback-v1")
        self.assertIs(advertised["diagnostics"], True)
        self.assertTrue(upstream.sent[0]["session"]["turn_detection"]["create_response"])
        self.assertFalse(upstream.sent[0]["session"]["turn_detection"]["interrupt_response"])

    async def test_voice_zero_complete_fixture_flow_without_credentials(self):
        scenario = deepcopy(SCENARIO)
        scenario.update(travelMs=0, captureMs=0)
        session = SurveySession(scenario=scenario)
        messages = [
            {"type": "command", "name": name, "args": args, "requestId": str(i)}
            for i, (name, args) in enumerate((
                ("confirm_prompt", PROMPT_ARGS),
                ("select_stop", {"monitor": "monitor-3"}),
                ("select_stop", {"monitor": "monitor-2"}),
                ("confirm_route", {}),
                ("launch_mission", {}),
            ))
        ]
        browser = Browser(messages)
        with patch.object(server, "SurveySession", return_value=session), \
                patch.object(server, "create_providers", return_value=(self.camera, self.vision)), \
                patch.object(server, "credential", side_effect=AssertionError("must not authenticate")), \
                patch.object(server.websockets, "connect", side_effect=AssertionError("must not connect externally")):
            task = asyncio.create_task(server.ws_endpoint(browser))
            await settle(lambda: any(e["type"] == "mission.debrief" for e in browser.events))
            browser.incoming.put_nowait(None)
            await task
        self.assertEqual(browser.events[0]["type"], "relay.ready")
        self.assertEqual(browser.events[1]["state"]["missionPhase"], "briefing")
        self.assertEqual(session.phase, "complete")
        self.assertEqual(session.data["score"]["rescuedCount"], 3)
        self.assertTrue(browser.closed)

    async def test_route_intro_prefetches_before_client_ready_signal(self):
        upstream = Upstream()
        self.bridge.upstream = upstream
        participant_turn(self.bridge, SEARCH_PROMPT)
        self.session.prepare_prompt(**PROMPT_ARGS)
        spoken_reply(self.bridge)
        await self.bridge.handle_tool_call({
            "name": "confirm_prompt", "call_id": "prompt-1",
            "arguments": "{}"}, turn=participant_turn(self.bridge, "좋아"))
        self.assertFalse(self.bridge._route_intro_pending)
        self.assertEqual(sum(e["type"] == "response.create" for e in upstream.sent), 1)
        pending = next(e for e in self.browser.events if e["type"] == "route_intro.pending")
        state_index = next(i for i, e in enumerate(self.browser.events)
                           if e["type"] == "route.state")
        self.assertLess(self.browser.events.index(pending), state_index)
        self.browser.incoming.put_nowait(json.dumps({
            "type": "route_intro.ready", "runId": pending["runId"], "introId": pending["introId"]}))
        self.browser.incoming.put_nowait(None)
        with self.assertRaises(WebSocketDisconnect):
            await self.bridge.pump_browser()
        self.assertFalse(self.bridge._route_intro_pending)
        self.assertEqual(sum(e["type"] == "response.create" for e in upstream.sent), 1)

    async def test_command_prompt_first_guard_and_validation(self):
        for name, args in (("select_stop", {"monitor": "monitor-3"}), ("confirm_route", {}),
                           ("launch_mission", {}), ("confirm_prompt", {"prompt_text": " "})):
            self.assertFalse((await self.bridge.run_tool(name, args))["ok"])
        self.assertTrue((await self.bridge.run_tool(
            "confirm_prompt", PROMPT_ARGS, "participant-prompt"))["ok"])
        self.assertEqual(self.session.data["userPromptText"], SEARCH_PROMPT)
        self.assertTrue((await self.bridge.run_tool("select_stop", {"monitor": "monitor-3"}))["ok"])

    async def test_config_readiness(self):
        self.vision.error = "Azure 이미지 설정을 확인하세요."
        with patch.object(server, "create_providers", return_value=(self.camera, self.vision)), \
                patch.object(server.config, "TRIAGE_MODE", "azure"):
            config = await server.api_config()
        self.assertFalse(config["visionReady"])
        self.assertEqual(config["mode"], "azure")
        self.assertEqual(config["visionError"], self.vision.error)


class StrictVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.browser = Browser(voice="1")
        self.session = SurveySession()
        self.bridge = server.Bridge(
            self.browser, self.session, (FakeCamera(), FakeVision()), strict_turn_taking=True)
        self.upstream = self.bridge.upstream = Upstream()

    async def asyncTearDown(self):
        await self.bridge.close()

    async def browser_messages(self, *messages):
        for message in messages:
            self.browser.incoming.put_nowait(json.dumps(message))
        self.browser.incoming.put_nowait(None)
        with self.assertRaises(WebSocketDisconnect):
            await self.bridge.pump_browser()

    async def provider_events(self, *events):
        self.upstream.incoming = list(events)
        await self.bridge.pump_upstream()

    def events(self, kind):
        return [event for event in self.browser.events if event["type"] == kind]

    def requests(self):
        return [event["response"] for event in self.upstream.sent if event["type"] == "response.create"]

    async def open_window(self):
        await self.bridge.offer_input()
        window = self.events("voice.input.ready")[-1]["windowId"]
        await self.browser_messages({
            "type": "voice.input.open", "runId": self.session.run_id, "windowId": window})
        self.assertTrue(self.bridge._input_open)
        return window

    async def admit(self, item_id="input"):
        window = await self.open_window()
        await self.browser_messages({"type": "audio", "windowId": window, "data": "pcm"})
        await self.provider_events(
            {"type": "input_audio_buffer.speech_started", "item_id": item_id},
            {"type": "input_audio_buffer.speech_stopped", "item_id": item_id, "audio_end_ms": 1000})
        return window

    async def finish(self, response_id, **metadata):
        await self.provider_events(
            {"type": "response.created", "response": {"id": response_id, "metadata": metadata}},
            {"type": "response.done", "response": {"id": response_id, "status": "completed"}})

    async def intro(self):
        self.session.prepare_prompt(**PROMPT_ARGS)
        spoken_reply(self.bridge)
        turn = participant_turn(self.bridge, "응")
        self.bridge._response_active = True
        self.bridge._pending_tools = 1
        await self.bridge.handle_tool_call({
            "name": "confirm_prompt", "call_id": "confirm", "arguments": "{}"}, turn=turn)
        self.assertFalse(self.requests())
        pending = self.events("route_intro.pending")[-1]
        await self.provider_events({
            "type": "response.done", "response": {"id": "confirm-tool", "status": "completed"}})
        self.assertEqual(len(self.requests()), 1)
        metadata = self.requests()[-1]["metadata"]
        self.assertEqual(metadata, {"runId": self.session.run_id, "routeIntro": pending["introId"]})
        await self.provider_events({
            "type": "response.created", "response": {"id": "intro-first", "metadata": metadata}})
        return pending

    async def test_ready_open_and_audio_admission_are_separate_and_run_scoped(self):
        await self.bridge.offer_input()
        window = self.events("voice.input.ready")[-1]["windowId"]
        await self.browser_messages(
            {"type": "voice.input_state", "muted": False},
            {"type": "audio", "windowId": window, "data": "before-open"},
            {"type": "voice.input.open", "runId": "old", "windowId": window},
            {"type": "voice.input.open", "runId": self.session.run_id, "windowId": "old"})
        self.assertFalse(self.upstream.sent)
        window = await self.open_window()
        await self.browser_messages(
            {"type": "voice.input.open", "runId": self.session.run_id, "windowId": window},
            {"type": "audio", "data": "no-token"},
            {"type": "audio", "windowId": "old", "data": "old"},
            {"type": "audio", "windowId": window, "data": "admitted"},
            {"type": "input_audio_buffer.clear"})
        self.assertEqual(self.upstream.sent, [
            {"type": "input_audio_buffer.clear"},
            {"type": "input_audio_buffer.append", "audio": "admitted"}])
        await self.bridge.request_response("Question")
        await self.browser_messages({"type": "audio", "windowId": window, "data": "late"})
        self.assertFalse(self.bridge._input_open)
        self.assertEqual(self.events("voice.input.closed")[-1]["windowId"], window)
        self.assertEqual(sum(e["type"] == "input_audio_buffer.append" for e in self.upstream.sent), 1)

    async def test_rejected_input_open_closes_requested_identity_without_closing_newer_window(self):
        window = await self.open_window()
        await self.browser_messages({
            "type": "voice.input.open", "runId": self.session.run_id, "windowId": "expired"})
        self.assertEqual(self.events("voice.input.closed")[-1], {
            "type": "voice.input.closed", "runId": self.session.run_id, "windowId": "expired"})
        self.assertEqual(self.bridge._input_window, window)
        self.assertTrue(self.bridge._input_open)
        await self.bridge.request_response("Continue")
        await self.browser_messages({
            "type": "voice.input.open", "runId": self.session.run_id, "windowId": window})
        self.assertEqual(self.events("voice.input.closed")[-1]["windowId"], window)
        self.assertFalse(self.bridge._input_open)

    async def test_malformed_input_open_closes_current_window_and_reports_protocol_error(self):
        window = await self.open_window()
        await self.browser_messages({"type": "voice.input.open", "runId": self.session.run_id})
        self.assertEqual(self.events("voice.input.closed")[-1]["windowId"], window)
        self.assertEqual(self.events("relay.error")[-1]["code"], "VOICE_INPUT_PROTOCOL_INVALID")
        self.assertFalse(self.bridge._input_open)
        self.assertIsNone(self.bridge._input_window)

    async def test_input_open_race_during_provider_clear_sends_closed(self):
        await self.bridge.offer_input()
        window = self.events("voice.input.ready")[-1]["windowId"]
        async def invalidate_on_send(message):
            await self.bridge.invalidate_input()
        self.upstream.send = AsyncMock(side_effect=invalidate_on_send)
        await self.browser_messages({
            "type": "voice.input.open", "runId": self.session.run_id, "windowId": window})
        self.assertEqual(self.events("voice.input.closed")[-1]["windowId"], window)
        self.assertFalse(self.bridge._input_open)
        self.assertNotEqual(self.bridge._input_window, window)

    async def test_playback_metrics_are_opt_in_scoped_numeric_and_do_not_authorize_input(self):
        await self.finish("reply")
        message = {
            "type": "voice.playback.metrics", "runId": self.session.run_id,
            "responseId": "reply", "event": "started", "ms": 23,
            "contextTime": 1.5, "performanceTime": float("inf"),
            "baseLatency": True, "outputLatency": "private timing",
            "transcript": "private transcript", "audio": "private audio", "secret": "private credential",
        }
        with patch.object(server.config, "VOICE_DIAGNOSTICS", False), \
                patch.object(self.bridge, "trace_voice") as trace:
            await self.browser_messages(message)
        trace.assert_not_called()
        with patch.object(server.config, "VOICE_DIAGNOSTICS", True), self.assertLogs("relay", level="INFO") as logs:
            await self.browser_messages(message)
        record = json.loads(logs.records[-1].message.split("voice.flow ", 1)[1])
        self.assertEqual(record["event"], "playback")
        self.assertEqual(record["playback_event"], "started")
        self.assertEqual(record["response_id"], "reply")
        self.assertEqual(record["ms"], 23)
        self.assertEqual(record["contextTime"], 1.5)
        self.assertFalse(set(record) & {"transcript", "audio", "secret", "performanceTime",
                                       "baseLatency", "outputLatency"})
        self.assertNotIn("private", logs.output[0])
        self.assertFalse(self.bridge._input_open)
        with patch.object(server.config, "VOICE_DIAGNOSTICS", True), \
                patch.object(self.bridge, "trace_voice") as trace:
            await self.browser_messages(
                message | {"runId": "old"}, message | {"responseId": "unknown"},
                message | {"responseId": []}, message | {"event": "unsupported"},
                message | {"event": []})
        trace.assert_not_called()
        self.assertFalse(self.bridge._input_open)

    async def test_playback_metrics_share_bounded_trace_budget(self):
        await self.finish("reply")
        self.bridge._voice_diagnostics_remaining = 2
        with patch.object(server.config, "VOICE_DIAGNOSTICS", True), self.assertLogs("relay", level="INFO") as logs:
            await self.browser_messages(*({
                "type": "voice.playback.metrics", "runId": self.session.run_id,
                "responseId": "reply", "event": event,
            } for event in ("received", "started", "drained", "route_ready")))
        self.assertEqual(len(logs.records), 2)
        self.assertEqual(self.bridge._voice_diagnostics_remaining, 0)

    async def test_capture_closes_immediately_and_unknown_vad_cannot_supersede_late_asr(self):
        window = await self.admit()
        self.assertEqual(self.events("voice.input.closed")[-1]["windowId"], window)
        await self.provider_events(
            {"type": "input_audio_buffer.speech_started", "item_id": "noise"},
            {"type": "input_audio_buffer.speech_stopped", "item_id": "noise"},
            {"type": "input_audio_buffer.committed", "item_id": "noise"},
            {"type": "conversation.item.input_audio_transcription.completed",
             "item_id": "noise", "transcript": "응"})
        await self.finish("native")
        self.assertEqual(len(self.events("voice.input.ready")), 1)
        self.assertEqual(self.bridge.voice_turns.latest.item_id, "input")
        self.assertNotIn("noise", self.bridge.voice_turns.turns)
        self.assertNotIn("noise", self.bridge.voice_turns.early_transcripts)
        await self.provider_events({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "input", "transcript": SEARCH_PROMPT})
        self.assertEqual(self.bridge.voice_turns.latest.text, SEARCH_PROMPT)
        offered = self.events("voice.input.ready")[-1]
        self.assertNotEqual(offered["windowId"], window)
        self.assertEqual(offered["responseIds"], ["native"])
        self.assertEqual(sum(e["type"] == "input_audio_buffer.clear" for e in self.upstream.sent), 1)

    async def test_committed_without_stop_closes_once_and_binds_native_turn(self):
        window = await self.open_window()
        await self.browser_messages({"type": "audio", "windowId": window, "data": "pcm"})
        await self.provider_events(
            {"type": "input_audio_buffer.speech_started", "item_id": "item"},
            {"type": "input_audio_buffer.committed", "item_id": "item"},
            {"type": "input_audio_buffer.speech_stopped", "item_id": "item"},
            {"type": "response.created", "response": {"id": "reply"}})
        self.assertEqual(len(self.events("voice.input.closed")), 1)
        self.assertIs(self.bridge.voice_turns.responses["reply"], self.bridge.voice_turns.turns["item"])

    async def test_tool_and_readback_continuations_share_one_closed_turn(self):
        await self.admit()
        await self.provider_events(
            {"type": "conversation.item.input_audio_transcription.completed",
             "item_id": "input", "transcript": SEARCH_PROMPT},
            {"type": "response.created", "response": {"id": "tool-only"}},
            {"type": "response.function_call_arguments.done", "response_id": "tool-only",
             "call_id": "prepare", "name": "prepare_prompt", "arguments": json.dumps(PROMPT_ARGS)},
            {"type": "response.done", "response": {"id": "tool-only", "status": "completed"}})
        self.assertEqual(len(self.events("voice.input.ready")), 1)
        await asyncio.gather(*self.bridge._tool_tasks)
        self.assertEqual(len(self.events("voice.input.ready")), 1)
        request = self.requests()[-1]
        self.assertEqual(request["metadata"]["promptReadback"], str(self.session.pending_prompt_revision))
        await self.finish("readback", **request["metadata"])
        self.assertEqual(self.events("voice.input.ready")[-1]["responseIds"], ["tool-only", "readback"])
        self.assertEqual(len(self.events("voice.input.ready")), 2)

    async def test_late_admitted_consent_prefetches_once_without_opening_between_turns(self):
        self.session.prepare_prompt(**PROMPT_ARGS)
        spoken_reply(self.bridge)
        await self.admit("consent")
        await self.finish("native-consent")
        self.assertEqual(len(self.events("voice.input.ready")), 1)
        await self.provider_events(
            {"type": "input_audio_buffer.speech_started", "item_id": "late-noise"},
            {"type": "conversation.item.input_audio_transcription.completed",
             "item_id": "consent", "transcript": "응"})
        await asyncio.gather(*self.bridge._tool_tasks)
        self.assertEqual(self.session.data["userPromptText"], SEARCH_PROMPT)
        self.assertEqual(len(self.events("route_intro.pending")), 1)
        self.assertEqual(len(self.requests()), 1)
        self.assertEqual(self.requests()[0]["metadata"]["routeIntro"], self.bridge._route_intro_id)
        self.assertEqual(len(self.events("voice.input.ready")), 1)
        await self.finish("intro", **self.requests()[0]["metadata"])
        self.assertEqual(self.events("voice.input.ready")[-1]["responseIds"], ["native-consent", "intro"])

    async def test_missing_or_failed_asr_recovers_without_discarding_input(self):
        self.bridge.INPUT_TIMEOUT_SECONDS = 0.01
        await self.admit("missing")
        await self.finish("reply")
        await self.bridge._input_timeout_task
        self.assertEqual(len(self.events("voice.input.failed")), 1)
        self.assertTrue(self.bridge.voice_turns.latest.ready.is_set())
        self.assertEqual(len(self.events("voice.input.ready")), 2)
        await self.admit("failed")
        await self.provider_events({
            "type": "conversation.item.input_audio_transcription.failed", "item_id": "failed"})
        await self.finish("second-reply")
        await self.bridge._input_timeout_task
        self.assertEqual(len(self.events("voice.input.failed")), 2)
        self.assertEqual(len(self.events("voice.input.ready")), 3)
        self.assertFalse(any(e["type"] == "response.cancel" for e in self.upstream.sent))

    async def test_missing_native_response_has_bounded_tool_free_recovery(self):
        self.bridge.INPUT_TIMEOUT_SECONDS = 0.01
        await self.admit()
        await self.bridge._input_timeout_task
        self.assertFalse(self.bridge._native_response_pending)
        self.assertEqual(len(self.requests()), 1)
        self.assertEqual(self.requests()[0]["tool_choice"], "none")
        await self.finish("recovery")
        self.assertEqual(len(self.events("voice.input.ready")), 2)

    async def test_empty_transcription_is_reported_once_and_recovers(self):
        await self.admit()
        await self.provider_events(
            {"type": "conversation.item.input_audio_transcription.completed",
             "item_id": "input", "transcript": ""},
            {"type": "conversation.item.input_audio_transcription.failed", "item_id": "input"})
        await self.finish("empty-reply")
        self.assertEqual(len(self.events("voice.input.failed")), 1)
        self.assertEqual(len(self.events("voice.input.ready")), 2)

    async def test_rejected_response_create_reopens_only_after_input_processing_finishes(self):
        await self.admit()
        await self.provider_events({"type": "error", "error": {"code": "response_failed"}})
        self.assertFalse(self.bridge._native_response_pending)
        self.assertEqual(len(self.events("voice.input.ready")), 1)
        await self.provider_events({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "input", "transcript": SEARCH_PROMPT})
        self.assertEqual(len(self.events("voice.input.ready")), 2)
        window = self.events("voice.input.ready")[-1]["windowId"]
        await self.bridge.request_response("Followup")
        await self.provider_events({"type": "error", "error": {"code": "invalid_request"}})
        self.assertFalse(self.bridge._response_active)
        self.assertNotEqual(self.events("voice.input.ready")[-1]["windowId"], window)
        self.assertFalse(self.upstream.closed)

    async def test_generic_error_waits_for_generation_terminal_then_emits_ready_with_failed_id(self):
        await self.admit()
        await self.provider_events(
            {"type": "conversation.item.input_audio_transcription.completed",
             "item_id": "input", "transcript": SEARCH_PROMPT},
            {"type": "response.created", "response": {"id": "failed-reply"}},
            {"type": "response.audio.delta", "response_id": "failed-reply", "delta": "partial"},
            {"type": "error", "error": {"code": "response_failed"}})
        self.assertTrue(self.bridge._response_active)
        self.assertEqual(len(self.events("voice.input.ready")), 1)
        await self.provider_events({
            "type": "response.done", "response": {"id": "failed-reply", "status": "failed"}})
        self.assertEqual(len(self.events("voice.input.ready")), 2)
        self.assertEqual(self.events("voice.input.ready")[-1]["responseIds"], ["failed-reply"])
        self.assertFalse(self.bridge._response_active)
        self.assertIsNone(self.bridge._response_failure_task)
        self.assertFalse(self.upstream.closed)
        self.assertFalse(self.requests())

    async def test_generic_error_without_terminal_event_has_bounded_visible_failure(self):
        await self.bridge.request_response("Question")
        await self.provider_events({"type": "response.created", "response": {"id": "stalled"}})
        with patch.object(server.asyncio, "sleep", new_callable=AsyncMock):
            await self.provider_events({"type": "error", "error": {"code": "response_failed"}})
            await self.bridge._response_failure_task
        self.assertTrue(self.upstream.closed)
        self.assertEqual(self.events("relay.error")[-1]["code"], "VOICE_RESPONSE_STALLED")
        self.assertFalse(self.events("voice.input.ready"))
        self.assertFalse(any(e["type"] == "response.cancel" for e in self.upstream.sent))

    async def test_unsolicited_response_cannot_reuse_admitted_participant_evidence(self):
        self.session.confirm_prompt(**PROMPT_ARGS)
        await self.admit()
        await self.provider_events({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "input", "transcript": "바다"})
        await self.finish("valid-reply")
        await self.provider_events(
            {"type": "input_audio_buffer.speech_started", "item_id": "noise"},
            {"type": "input_audio_buffer.speech_stopped", "item_id": "noise"},
            {"type": "response.created", "response": {"id": "unsolicited"}},
            {"type": "response.function_call_arguments.done", "response_id": "unsolicited",
             "call_id": "noise-pick", "name": "select_stop", "arguments": '{"monitor":"monitor-1"}'})
        await asyncio.gather(*self.bridge._tool_tasks)
        self.assertIsNone(self.bridge.voice_turns.responses["unsolicited"])
        self.assertEqual(self.session.state.draftRoute, [])

    async def test_ttfa_ignores_previous_response_audio_and_is_item_correlated(self):
        await self.finish("old")
        await self.admit()
        await self.provider_events(
            {"type": "response.audio.delta", "response_id": "old", "delta": "old-audio"},
            {"type": "response.created", "response": {"id": "new"}},
            {"type": "response.audio.delta", "response_id": "new", "delta": "new-audio"},
            {"type": "response.audio.delta", "response_id": "new", "delta": "more"})
        metrics = self.events("metrics.ttfa")
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0]["responseId"], "new")
        self.assertEqual(metrics[0]["itemId"], "input")
        self.assertEqual(metrics[0]["timingBasis"], "speech_stopped_event_receipt")
        stamps = self.bridge.voice_turns.latest.timestamps
        self.assertLessEqual(stamps["speech_started"], stamps["speech_stopped"])
        self.assertLessEqual(stamps["speech_stopped"], stamps["response_created"])

    async def test_intro_mapping_precedes_created_and_prefetch_does_not_wait_for_map(self):
        pending = await self.intro()
        request = self.requests()[0]
        self.assertEqual(request["tool_choice"], "none")
        for person in self.session.scenario["people"]:
            self.assertIn(person["clue"], request["instructions"])
        mapped = self.events("route_intro.response")[0]
        created = self.events("response.created")[0]
        self.assertLess(self.browser.events.index(mapped), self.browser.events.index(created))
        self.assertLess(self.browser.events.index(pending),
                        next(i for i, e in enumerate(self.browser.events) if e["type"] == "route.state"))
        await self.provider_events({
            "type": "response.done", "response": {"id": "intro-first", "status": "completed"}})
        await self.browser_messages(
            pending | {"type": "route_intro.ready"},
            pending | {"type": "route_intro.ready"})
        self.assertEqual(len(self.requests()), 1)
        self.assertEqual(self.events("voice.input.ready")[-1]["responseIds"], ["intro-first"])

    async def test_intro_buffer_retry_waits_for_done_and_map_and_only_retries_once(self):
        pending = await self.intro()
        retry = pending | {"type": "route_intro.retry", "responseId": "intro-first", "reason": "buffer_limit"}
        await self.browser_messages(retry | {"responseId": "old"}, retry, retry)
        self.assertEqual(len(self.events("route_intro.failed")), 1)
        self.assertTrue(self.events("route_intro.failed")[-1]["retrying"])
        await self.provider_events({
            "type": "response.done", "response": {"id": "intro-first", "status": "completed"}})
        self.assertEqual(len(self.requests()), 1)
        self.assertFalse(self.events("voice.input.ready"))
        await self.browser_messages(pending | {"type": "route_intro.ready", "runId": "old"})
        self.assertEqual(len(self.requests()), 1)
        await self.browser_messages(pending | {"type": "route_intro.ready"})
        self.assertEqual(len(self.requests()), 2)
        await self.provider_events({
            "type": "response.created", "response": {"id": "intro-retry", "metadata": self.requests()[-1]["metadata"]}})
        await self.browser_messages(retry | {"responseId": "intro-retry"})
        await self.provider_events({
            "type": "response.done", "response": {"id": "intro-retry", "status": "completed"}})
        self.assertEqual(len(self.requests()), 2)
        self.assertFalse(self.events("route_intro.failed")[-1]["retrying"])
        self.assertEqual(self.events("voice.input.ready")[-1]["responseIds"], ["intro-first", "intro-retry"])
        self.assertFalse(any(e["type"] == "response.cancel" for e in self.upstream.sent))

    async def test_intro_provider_failure_and_abort_retire_pending_retry(self):
        pending = await self.intro()
        await self.provider_events({
            "type": "response.done", "response": {"id": "intro-first", "status": "failed"}})
        self.assertTrue(self.events("route_intro.failed")[-1]["retrying"])
        self.assertEqual(len(self.requests()), 1)
        self.session.abort_mission()
        await self.bridge.runner._notify()
        await self.browser_messages(pending | {"type": "route_intro.ready"})
        self.assertIsNone(self.bridge._route_intro_id)
        self.assertEqual(len(self.requests()), 1)
        self.assertFalse(self.events("voice.input.ready"))

    async def test_intro_provider_error_waits_for_terminal_response_without_cancel(self):
        pending = await self.intro()
        await self.provider_events({"type": "error", "error": {"code": "response_failed"}})
        await self.browser_messages(pending | {"type": "route_intro.ready"})
        self.assertEqual(len(self.requests()), 1)
        self.assertTrue(self.bridge._response_active)
        await self.provider_events({
            "type": "response.done", "response": {"id": "intro-first", "status": "failed"}})
        self.assertEqual(len(self.requests()), 2)
        self.assertEqual(len(self.events("route_intro.failed")), 1)
        self.assertFalse(any(e["type"] == "response.cancel" for e in self.upstream.sent))

    async def test_intro_create_rejection_without_response_id_can_retry_after_map(self):
        self.session.confirm_prompt(**PROMPT_ARGS)
        self.bridge._route_intro_id = "intro"
        self.bridge._route_intro_pending = True
        await self.bridge.request_response()
        await self.provider_events({"type": "error", "error": {"code": "response_failed"}})
        self.assertFalse(self.bridge._response_active)
        self.assertEqual(self.events("route_intro.failed")[-1]["responseId"], None)
        await self.browser_messages({
            "type": "route_intro.ready", "runId": self.session.run_id, "introId": "intro"})
        self.assertEqual(len(self.requests()), 2)
        await self.provider_events({"type": "error", "error": {"code": "response_failed"}})
        self.assertFalse(self.events("route_intro.failed")[-1]["retrying"])
        self.assertEqual(len(self.events("voice.input.ready")), 1)

    async def test_intro_error_without_terminal_event_reports_and_retires_broken_connection(self):
        await self.intro()
        with patch.object(server.asyncio, "sleep", new_callable=AsyncMock):
            await self.provider_events({"type": "error", "error": {"code": "response_failed"}})
            await self.bridge._response_failure_task
        self.assertTrue(self.upstream.closed)
        self.assertEqual(self.events("relay.error")[-1]["code"], "ROUTE_INTRO_RESPONSE_STALLED")
        self.assertFalse(self.events("voice.input.ready"))
        self.assertFalse(any(e["type"] == "response.cancel" for e in self.upstream.sent))

    async def test_departure_rejects_input_and_preserves_fresh_playback_consent(self):
        ready(self.session)
        self.bridge.voice_turns.begin_route_readback("route", self.session)
        self.bridge.voice_turns.hear_response("route")
        self.bridge.voice_turns.finish_response({"id": "route", "status": "completed"}, self.session)
        await self.bridge.offer_input()
        window = self.events("voice.input.ready")[-1]["windowId"]
        await self.browser_messages(
            {"type": "voice.reply_drained", "runId": self.session.run_id, "responseId": "route"},
            {"type": "voice.input.open", "runId": self.session.run_id, "windowId": window})
        await self.browser_messages({"type": "audio", "windowId": window, "data": "pcm"})
        await self.provider_events({"type": "input_audio_buffer.speech_started", "item_id": "consent"})
        self.assertTrue(self.bridge.voice_turns.latest.route_readback_done)
        await self.bridge.stop_departure_voice()
        await self.browser_messages({"type": "audio", "windowId": window, "data": "late"})
        self.assertTrue(self.upstream.closed)
        self.assertEqual(sum(e["type"] == "input_audio_buffer.append" for e in self.upstream.sent), 1)


if __name__ == "__main__":
    unittest.main()
