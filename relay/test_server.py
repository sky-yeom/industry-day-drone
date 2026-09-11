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
        self.query_params = {"voice": voice}
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

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.incoming:
            raise StopAsyncIteration
        return json.dumps(self.incoming.pop(0))


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
        self.assertIn(SCENARIO["targetAppearance"]["description"], tools.SYSTEM_PROMPT)
        self.assertEqual(response["tool_choice"], "none")
        self.assertEqual(self.session.data["promptPhase"], "briefing")
        self.assertEqual(self.session.data["userPromptText"], "")
        self.assertEqual(self.session.state.draftRoute, [])

    async def test_narration_override_keeps_prompt_confirmation_rules(self):
        self.bridge.upstream = Upstream()
        await self.bridge.request_response("참가자에게 현재 상태를 설명하세요.")
        instructions = self.bridge.upstream.sent[0]["response"]["instructions"]
        self.assertTrue(instructions.startswith(tools.SYSTEM_PROMPT))
        self.assertIn("사용자의 프롬프트를 대신 만들거나 미리 채우지 않습니다", instructions)
        self.assertIn("정답으로 고치거나 빠진 특징을 채우지 않습니다", instructions)

    async def test_departure_closes_voice_but_mission_and_text_results_continue(self):
        ready(self.session)
        self.session.scenario.update(travelMs=0, captureMs=0)
        self.vision.block = asyncio.Event()
        upstream = Upstream()
        self.bridge.upstream = upstream
        self.bridge._response_active = True
        self.bridge._pending_tools = 1
        await self.bridge.handle_tool_call({"name": "launch_mission", "call_id": "launch", "arguments": "{}"})
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
        await self.bridge.handle_tool_call({"name": "launch_mission", "call_id": "launch", "arguments": "{}"})
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
            "arguments": '{"monitor":"monitor-3"}'})
        self.assertFalse(any(e["type"] == "response.create" for e in upstream.sent))
        self.assertEqual(upstream.sent[0]["item"]["call_id"], "voice-call")
        upstream.incoming = [{"type": "response.done", "response": {"id": "previous", "status": "completed"}}]
        await self.bridge.pump_upstream()
        self.assertEqual(sum(e["type"] == "response.create" for e in upstream.sent), 1)
        self.assertEqual(self.session.state.draftRoute, ["monitor-3"])
        self.assertEqual(next(e["id"] for e in self.browser.events if e["type"] == "tool.finished"), "voice-call")

    async def test_final_debrief_correlates_only_its_completed_response(self):
        upstream = Upstream()
        self.bridge.upstream = upstream
        self.bridge._response_active = True
        self.session.abort_mission()
        await self.bridge.runner._notify()
        self.assertFalse(upstream.sent)
        upstream.incoming = [{"type": "response.done", "response": {"id": "previous", "status": "completed"}}]
        await self.bridge.pump_upstream()
        request = upstream.sent[-1]
        self.assertEqual(request["response"]["metadata"]["runId"], self.session.run_id)
        self.assertEqual(request["response"]["tool_choice"], "none")
        self.assertFalse(any(e["type"] == "mission.debrief.done" for e in self.browser.events))
        response = {"id": "final", "metadata": request["response"]["metadata"]}
        upstream.incoming = [
            {"type": "response.created", "response": response},
            {"type": "response.audio.delta", "delta": "audio"},
            {"type": "response.done", "response": response | {"status": "completed"}}]
        await self.bridge.pump_upstream()
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

    async def test_cancelled_debrief_retries_and_never_signals_audio_done(self):
        upstream = Upstream()
        self.bridge.upstream = upstream
        self.session.abort_mission()
        await self.bridge.runner._notify()
        response = {"id": "cancelled-final", "metadata": upstream.sent[-1]["response"]["metadata"]}
        upstream.incoming = [
            {"type": "response.created", "response": response},
            {"type": "response.done", "response": response | {"status": "cancelled"}},
        ]
        await self.bridge.pump_upstream()
        self.assertFalse(any(e["type"] == "mission.debrief.done" for e in self.browser.events))
        self.assertEqual(sum(e["type"] == "response.create" for e in upstream.sent), 2)
        self.assertEqual(upstream.sent[-1]["response"]["metadata"]["missionDebrief"], "true")

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

    async def test_route_intro_holds_agent_turn_until_client_ready_signal(self):
        upstream = Upstream()
        self.bridge.upstream = upstream
        await self.bridge.handle_tool_call({
            "name": "confirm_prompt", "call_id": "prompt-1",
            "arguments": json.dumps(PROMPT_ARGS)})
        self.assertTrue(self.bridge._route_intro_pending)
        self.assertFalse(any(e["type"] == "response.create" for e in upstream.sent))
        self.browser.incoming.put_nowait(json.dumps({"type": "route_intro.ready"}))
        self.browser.incoming.put_nowait(None)
        with self.assertRaises(WebSocketDisconnect):
            await self.bridge.pump_browser()
        self.assertFalse(self.bridge._route_intro_pending)
        self.assertTrue(any(e["type"] == "response.create" for e in upstream.sent))

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


if __name__ == "__main__":
    unittest.main()
