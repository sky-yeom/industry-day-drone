"""Authoritative mission relay with optional native Azure Voice Live audio."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from uuid import uuid4

import uvicorn
import websockets
from azure.identity.aio import DefaultAzureCredential
from azure.core.exceptions import AzureError
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from websockets.exceptions import WebSocketException

try:
    from . import config, tools
    from .survey import SurveySession
    from .mission_runner import MissionRunner
    from .vision import create_providers
except ImportError:
    import config
    import tools
    from survey import SurveySession
    from mission_runner import MissionRunner
    from vision import create_providers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("relay")
app = FastAPI(title="긴급 구조 훈련 관제")
app.add_middleware(CORSMiddleware, allow_origin_regex=config.WEB_ORIGIN_REGEX,
                   allow_methods=["*"], allow_headers=["*"])
_credential = None


def credential():
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


@app.get("/api/config")
async def api_config():
    _, vision = create_providers(config.TRIAGE_MODE)
    error = vision.readiness()
    return {
        "resource": config.RESOURCE, "model": config.MODEL, "voice": config.VOICE_NAME,
        "voiceType": config.VOICE_TYPE, "apiVersion": config.API_VERSION,
        "region": config.REGION, "sampleRate": config.SAMPLE_RATE,
        "mode": config.TRIAGE_MODE, "visionReady": error is None, "visionError": error,
    }


def build_session():
    return {"type": "session.update", "session": {
        "instructions": tools.SYSTEM_PROMPT,
        "turn_detection": {
            "type": config.VAD_TYPE, "languages": config.VAD_LANGUAGES,
            "threshold": config.VAD_THRESHOLD, "prefix_padding_ms": config.PREFIX_PADDING_MS,
            "speech_duration_ms": config.SPEECH_DURATION_MS,
            "silence_duration_ms": config.SILENCE_DURATION_MS,
            "interrupt_response": False,
            # Native audio must not wait for the separate subtitle transcription.
            "create_response": True, "remove_filler_words": True,
        },
        "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
        "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
        "input_audio_format": "pcm16", "output_audio_format": "pcm16",
        "input_audio_sampling_rate": config.SAMPLE_RATE,
        "input_audio_transcription": {
            "model": config.TRANSCRIPTION_MODEL, "language": "ko",
            "prompt": config.TRANSCRIPTION_PROMPT,
        },
        "voice": {"name": config.VOICE_NAME, "type": config.VOICE_TYPE},
        "modalities": ["text", "audio"], "tools": tools.TOOLS, "tool_choice": "auto",
    }}


class Bridge:
    def __init__(self, browser, session, providers=None):
        self.browser, self.session = browser, session
        self.upstream = None
        self._speech_stopped_at = None
        self._ttfa_pending = False
        self._pending_tools = 0
        self._tool_tasks = set()
        self._browser_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._tool_lock = asyncio.Lock()
        self._response_lock = asyncio.Lock()
        self._response_active = False
        self._native_response_pending = False
        self._response_requested = False
        self._user_speaking = False
        self._narration = []
        self._debrief_pending = False
        self._debrief_response_id = None
        self._last_response = None
        self._greet_requested = False
        self._greeting_pending = False
        self._launch_pending = False
        self._launch_response_id = None
        self._launch_attempts = 0
        self._voice_stopped = False
        self._departure_voice_finished = False
        self._results_task = None
        self._result_text = None
        self._debrief_completed = False
        self._debrief_attempts = 0
        self._completed_commands = {}
        self._closing = False
        camera, vision = providers or create_providers(session.data["mode"])
        self.runner = MissionRunner(session, camera, vision, self.publish_mission)

    async def send_browser(self, payload):
        async with self._browser_lock:
            with contextlib.suppress(Exception):
                await self.browser.send_text(json.dumps(payload, ensure_ascii=False))

    async def push_state(self):
        await self.send_browser({"type": "route.state", "state": self.session.snapshot()})

    @property
    def departure_started(self):
        return self._launch_pending or self._launch_response_id is not None or self._departure_voice_finished or self._voice_stopped

    async def stop_departure_voice(self, *, failed=False):
        if self._departure_voice_finished:
            return
        self._departure_voice_finished = True
        self._voice_stopped = True
        self._response_requested = False
        self._narration.clear()
        if failed:
            await self.send_browser({
                "type": "mission.launch.failed", "runId": self.session.run_id,
                "message": "출발 음성 안내를 완료하지 못했습니다. 자동 작전은 계속 진행되며 화면에서 확인할 수 있습니다."})
        if self.upstream:
            await self.upstream.close()

    async def publish_mission(self, event):
        if event["type"] == "mission.debrief":
            self._result_text = event["text"]
        await self.send_browser(event)
        if event["type"] in {"mission.progress", "mission.debrief"} and self.upstream and not self.departure_started:
            # Coalesce obsolete progress rather than queueing a long spoken backlog.
            self._narration = [event["text"]]
            if event["type"] == "mission.debrief":
                self._debrief_pending = True
            await self.request_response()

    async def start_result_audio(self, run_id):
        if run_id == self.session.run_id and self._results_task is not None:
            return
        if (run_id != self.session.run_id or not self._result_text
                or self.session.phase not in {"complete", "aborted"} or not self._voice_stopped):
            log.warning("Rejected premature or stale result-audio request")
            return
        if self._results_task is None:
            self._results_task = asyncio.create_task(self.speak_results())

    async def speak_results(self):
        try:
            token = await credential().get_token(config.TOKEN_SCOPE)
            async with websockets.connect(
                config.WS_URL, additional_headers={"Authorization": f"Bearer {token.token}"},
                open_timeout=30, max_size=None,
            ) as upstream:
                self.upstream = upstream
                session = build_session()
                session["session"]["turn_detection"].update(create_response=False, interrupt_response=False)
                session["session"].update(tools=[], tool_choice="none")
                await upstream.send(json.dumps(session))
                self._voice_stopped = False
                self._launch_pending = False
                self._response_active = False
                self._native_response_pending = False
                self._user_speaking = False
                self._debrief_pending = True
                self._debrief_completed = False
                self._debrief_response_id = None
                self._debrief_attempts = 0
                self._narration = [self._result_text]
                await self.request_response()
                await asyncio.wait_for(self.pump_upstream(), timeout=60)
                if not self._debrief_completed:
                    raise OSError("Result speech ended before completion")
        except (AzureError, WebSocketException, OSError, asyncio.TimeoutError):
            log.exception("result voice connection failed")
            await self.send_browser({
                "type": "mission.debrief.failed", "runId": self.session.run_id,
                "message": "결과 음성 안내에 연결하지 못했습니다. 화면의 최종 구조 결과를 확인해 주세요."})
        finally:
            self.upstream = None

    async def request_response(self, instructions=None):
        if self._voice_stopped:
            return
        if instructions:
            self._narration.append(instructions)
        self._response_requested = True
        await self.flush_response()

    async def flush_response(self):
        async with self._response_lock:
            if (self.upstream is None or self._closing or self._voice_stopped or self._response_active
                    or self._native_response_pending or self._pending_tools
                    or self._user_speaking or not self._response_requested):
                return
            response = {"type": "response.create"}
            if self._narration:
                response["response"] = {
                    # Response instructions replace, rather than extend, session instructions.
                    "instructions": tools.SYSTEM_PROMPT
                    + "\n## 이번 응답의 관제 사실과 지시\n"
                    + "아래 내용을 안내자로서 한국어로 전달하세요. 참가자를 대신해 대답하지 마세요.\n"
                    + " ".join(self._narration)}
            if self._greeting_pending:
                response.setdefault("response", {})["tool_choice"] = "none"
                self._greeting_pending = False
            if self._debrief_pending:
                self._debrief_attempts += 1
                response.setdefault("response", {})["metadata"] = {
                    "missionDebrief": "true", "runId": self.session.run_id}
                # A final summary must not start another tool chain.
                response["response"]["tool_choice"] = "none"
                response["response"]["instructions"] += (
                    "\n지금은 이미 종료된 작전의 최종 결과 안내입니다. 새로운 첫 인사가 아닙니다. "
                    "위 결과 사실에서 구조 인원, 부상 인원, 시한 초과를 짧게 요약하고 끝내세요. "
                    "프롬프트를 다시 묻거나 다음 행동을 질문하지 마세요.")
            if self._launch_pending:
                response["response"] = {
                    "instructions": tools.SYSTEM_PROMPT + "\n이번 응답에서는 다음 두 문장만 그대로 말하고 끝내세요: "
                    + tools.DEPARTURE_ANNOUNCEMENT + " 추가 설명, 질문, 도구 호출은 하지 마세요.",
                    "tool_choice": "none",
                    "metadata": {"missionLaunch": "true", "runId": self.session.run_id},
                }
                self._launch_attempts += 1
            self._last_response = response
            self._narration.clear()
            self._response_requested = False
            self._response_active = True
            await self.upstream.send(json.dumps(response, ensure_ascii=False))

    async def pump_upstream(self):
        async for raw in self.upstream:
            if self._voice_stopped:
                return
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            etype = event.get("type", "")
            if self.departure_started and etype.startswith("input_audio_buffer."):
                continue
            if etype == "error":
                log.error("Voice Live error: %s", event.get("error"))
                if self._results_task is not None:
                    raise OSError("Voice Live rejected result narration")
                if (event.get("error") or {}).get("code") == "conversation_already_has_active_response":
                    self._response_requested = True
                    if self._last_response:
                        instructions = self._last_response.get("response", {}).get("instructions")
                        if instructions and not self._narration:
                            self._narration = [instructions]
            if etype == "response.created":
                self._response_active = True
                self._native_response_pending = False
                response = event.get("response") or {}
                metadata = response.get("metadata") or {}
                if metadata.get("missionLaunch") == "true" and metadata.get("runId") == self.session.run_id:
                    self._launch_pending = False
                    self._launch_response_id = response.get("id")
                    await self.send_browser({
                        "type": "mission.launch.response", "runId": self.session.run_id,
                        "responseId": self._launch_response_id})
                if metadata.get("missionDebrief") == "true" and metadata.get("runId") == self.session.run_id:
                    self._debrief_pending = False
                    self._debrief_response_id = response.get("id")
                    await self.send_browser({
                        "type": "mission.debrief.response", "runId": self.session.run_id,
                        "responseId": self._debrief_response_id})
            elif etype == "input_audio_buffer.speech_started":
                self._user_speaking = True
            elif etype == "input_audio_buffer.speech_stopped":
                self._user_speaking = False
                # VAD creates the next response itself; do not race its native turn.
                self._response_active = True
                self._native_response_pending = True
                self._speech_stopped_at = time.perf_counter()
                self._ttfa_pending = True
            elif etype == "response.done":
                self._response_active = False
            if etype in {"response.audio.delta", "response.output_audio.delta"} and self._ttfa_pending:
                self._ttfa_pending = False
                await self.send_browser({
                    "type": "metrics.ttfa",
                    "ms": int((time.perf_counter() - self._speech_stopped_at) * 1000)})
            if etype == "response.function_call_arguments.done":
                if self.departure_started:
                    log.warning("Ignoring a voice tool call after departure")
                    continue
                await self.send_browser(event)
                self._pending_tools += 1
                task = asyncio.create_task(self.handle_tool_call(event))
                self._tool_tasks.add(task)
                task.add_done_callback(self._tool_tasks.discard)
                continue
            await self.send_browser(event)
            if etype == "response.done":
                response = event.get("response") or {}
                if self._launch_response_id and response.get("id") == self._launch_response_id:
                    if response.get("status") == "completed":
                        await self.send_browser({
                            "type": "mission.launch.done", "runId": self.session.run_id,
                            "responseId": self._launch_response_id})
                        await self.stop_departure_voice()
                        return
                    if self._launch_attempts >= 2:
                        await self.stop_departure_voice(failed=True)
                        return
                    self._launch_pending = True
                    self._launch_response_id = None
                    self._response_requested = True
                if self._debrief_response_id and response.get("id") == self._debrief_response_id:
                    if response.get("status") == "completed":
                        self._debrief_completed = True
                        await self.send_browser({
                            "type": "mission.debrief.done", "runId": self.session.run_id,
                            "responseId": self._debrief_response_id})
                        if self._results_task is not None:
                            return
                    else:
                        if self._results_task is not None and self._debrief_attempts >= 2:
                            raise OSError("Result narration failed after retry")
                        self._debrief_pending = True
                        self._narration = [self.session.debrief()]
                        self._response_requested = True
                await self.flush_response()

    async def run_tool(self, name, args, activity_id=None):
        activity_id = activity_id if isinstance(activity_id, str) and activity_id else str(uuid4())
        async with self._command_lock:
            await self.send_browser({"type": "tool.started", "id": activity_id, "name": name, "args": args})
            started = time.perf_counter()
            prior = self._completed_commands.get(activity_id)
            if prior is not None:
                outcome = prior[2] if prior[:2] == (name, args) else {
                    "ok": False, "facts": "같은 요청 번호로 다른 명령을 실행할 수 없습니다.", "ask": ""}
            else:
                try:
                    outcome = await tools.dispatch(self.session, self.runner, name, args)
                except Exception:
                    log.exception("tool execution failed")
                    outcome = {"ok": False, "facts": "명령 실행 중 오류가 발생했습니다.", "ask": ""}
                self._completed_commands[activity_id] = (name, args, outcome)
                if len(self._completed_commands) > 256:
                    del self._completed_commands[next(iter(self._completed_commands))]
            if name == "launch_mission" and outcome["ok"] and self.upstream and not self.departure_started:
                self._launch_pending = True
                self._narration.clear()
                self._response_requested = True
                self._user_speaking = False
                self._native_response_pending = False
                await self.send_browser({"type": "mission.launch", "runId": self.session.run_id})
                turn_detection = build_session()["session"]["turn_detection"] | {
                    "create_response": False, "interrupt_response": False}
                await self.upstream.send(json.dumps({"type": "session.update", "session": {"turn_detection": turn_detection}}))
                await self.upstream.send(json.dumps({"type": "input_audio_buffer.clear"}))
            await self.send_browser({
                "type": "tool.finished", "id": activity_id, "name": name, "result": outcome,
                "ms": int((time.perf_counter() - started) * 1000)})
            await self.push_state()
            return outcome

    async def handle_tool_call(self, event):
        call_id = event.get("call_id", "")
        try:
            args = json.loads(event.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            args = None
        try:
            outcome = await self.run_tool(event.get("name", ""), args, call_id)
            if self.upstream:
                async with self._tool_lock:
                    await self.upstream.send(json.dumps({
                        "type": "conversation.item.create", "item": {
                            "type": "function_call_output", "call_id": call_id,
                            "output": json.dumps(outcome, ensure_ascii=False)}}))
        finally:
            self._pending_tools = max(0, self._pending_tools - 1)
        await self.request_response()

    async def pump_browser(self):
        while True:
            raw = await self.browser.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            mtype = msg.get("type", "")
            if mtype == "command":
                await self.run_tool(msg.get("name", ""), msg.get("args", {}), msg.get("requestId"))
            elif mtype == "results.ready":
                await self.start_result_audio(msg.get("runId"))
            elif mtype == "audio" and self.upstream and not self.departure_started:
                await self.upstream.send(json.dumps({
                    "type": "input_audio_buffer.append", "audio": msg.get("data", "")}))
            elif mtype == "text" and self.upstream and not self.departure_started:
                await self.upstream.send(json.dumps({
                    "type": "conversation.item.create", "item": {
                        "type": "message", "role": "user",
                        "content": [{"type": "input_text", "text": msg.get("text", "")}]}}))
                await self.request_response()
            elif mtype == "greet":
                await self.greet()
            # Browser events cannot replace instructions/tools or fabricate outputs.
            elif mtype in {"input_audio_buffer.clear", "response.cancel", "conversation.item.truncate"} and self.upstream and not self.departure_started:
                await self.upstream.send(json.dumps(msg))

    async def greet(self):
        if self.upstream and not self._greet_requested:
            self._greet_requested = True
            self._greeting_pending = True
            await self.request_response(
                "이번은 고정된 첫 인사입니다. 아래 세 문장을 처음부터 끝까지 정확히 그대로 읽으세요. "
                "한두 문장으로 줄이라는 일반 말투 규칙은 이 고정 인사에는 적용하지 않습니다. "
                "요약, 의역, 생략, 추가 인사, 모의 훈련 설명은 금지합니다. 단어를 바꾸지 마세요.\n"
                f"읽을 문장: {tools.GREETING}\n"
                "마지막 질문 뒤에는 참가자의 답을 기다리세요. 이 질문에 스스로 답하거나 "
                "인물의 외형, 모니터별 상황, 경로를 덧붙이지 마세요. "
                "아직 참가자가 탐지 프롬프트를 말하거나 확인한 적이 없습니다.")

    async def cancel_tool_tasks(self):
        tasks = list(self._tool_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        self._closing = True
        if self._results_task is not None:
            self._results_task.cancel()
            await asyncio.gather(self._results_task, return_exceptions=True)
        await self.cancel_tool_tasks()
        if self.session.phase not in {"complete", "aborted"}:
            self.session.abort_mission()
        await self.runner.close()


@app.websocket("/ws")
async def ws_endpoint(browser: WebSocket):
    await browser.accept()
    session = SurveySession(mode=config.TRIAGE_MODE)
    bridge = Bridge(browser, session)
    pumps = []
    try:
        if browser.query_params.get("voice", "1") == "0":
            await bridge.send_browser({
                "type": "relay.ready", "model": None, "voice": None,
                "region": config.REGION, "mode": config.TRIAGE_MODE})
            await bridge.push_state()
            await bridge.pump_browser()
            return
        try:
            token = await credential().get_token(config.TOKEN_SCOPE)
        except Exception:
            log.exception("failed to acquire Entra token")
            await bridge.send_browser({
                "type": "relay.error",
                "message": "Azure 음성 인증에 실패했습니다. az login과 설정을 확인하거나 음성 없이 시작하세요."})
            return
        async with websockets.connect(
            config.WS_URL, additional_headers={"Authorization": f"Bearer {token.token}"},
            open_timeout=30, max_size=None,
        ) as upstream:
            bridge.upstream = upstream
            await upstream.send(json.dumps(build_session()))
            await bridge.send_browser({
                "type": "relay.ready", "model": config.MODEL, "voice": config.VOICE_NAME,
                "region": config.REGION, "mode": config.TRIAGE_MODE})
            await bridge.push_state()
            pumps = [asyncio.create_task(bridge.pump_upstream()),
                     asyncio.create_task(bridge.pump_browser())]
            done, pending = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            if pumps[0] in done and pumps[1] not in done and bridge.departure_started:
                # Voice intentionally ends at departure; the browser still owns a live mission.
                if not bridge._departure_voice_finished:
                    await bridge.stop_departure_voice(failed=True)
                await pumps[1]
                return
            for task in pending:
                task.cancel()
            for task in done:
                if not task.cancelled():
                    exc = task.exception()
                    if exc and not isinstance(exc, WebSocketDisconnect):
                        log.error("pump ended: %r", exc)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("relay failure")
        await bridge.send_browser({
            "type": "relay.error", "message": "관제 연결 중 오류가 발생했습니다. 연결과 설정을 확인하세요."})
    finally:
        for task in pumps:
            task.cancel()
        if pumps:
            await asyncio.gather(*pumps, return_exceptions=True)
        await bridge.close()
        with contextlib.suppress(Exception):
            await browser.close()


if __name__ == "__main__":
    log.info("긴급 구조 관제: ws://%s:%s/ws (mode=%s)", config.HOST, config.PORT, config.TRIAGE_MODE)
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")
