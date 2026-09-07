"""Browser <-> Voice Live relay for the drone survey dashboard.

Why a relay exists at all: the Foundry resource has disableLocalAuth=true, so
Voice Live only accepts an Entra ID bearer token. A browser cannot set an
Authorization header on a WebSocket, and shipping a credential to the client
would be wrong even if it could. So the browser speaks plain WS to this process,
and this process holds the credential.

    browser  --ws-->  relay  --wss + Bearer-->  Voice Live (gpt-realtime)

Route state lives here too, so the agent and the dashboard cannot disagree about
what the route is. Tool calls execute here and the resulting state is pushed to
the browser to render.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time

import uvicorn
import websockets
from azure.identity.aio import DefaultAzureCredential
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

import config
import tools
from survey import SurveySession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("relay")

app = FastAPI(title="Drone survey Voice Live relay")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=config.WEB_ORIGIN_REGEX,
    allow_methods=["*"],
    allow_headers=["*"],
)

_credential: DefaultAzureCredential | None = None


def credential() -> DefaultAzureCredential:
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


@app.get("/api/config")
async def api_config() -> dict:
    return {
        "resource": config.RESOURCE,
        "model": config.MODEL,
        "voice": config.VOICE_NAME,
        "voiceType": config.VOICE_TYPE,
        "apiVersion": config.API_VERSION,
        "region": config.REGION,
        "sampleRate": config.SAMPLE_RATE,
    }


def build_session() -> dict:
    return {
        "type": "session.update",
        "session": {
            "instructions": tools.SYSTEM_PROMPT,
            "turn_detection": {
                # 의미 기반 VAD라 볼륨만 보지 않는다. 한국어 중간 호흡에서
                # 사용자를 끊지 않으려면 multilingual 쪽을 써야 한다.
                # (azure_semantic_vad는 문서상 영어 위주)
                "type": config.VAD_TYPE,
                "languages": config.VAD_LANGUAGES,
                "threshold": config.VAD_THRESHOLD,
                "prefix_padding_ms": config.PREFIX_PADDING_MS,
                "speech_duration_ms": config.SPEECH_DURATION_MS,
                "silence_duration_ms": config.SILENCE_DURATION_MS,
                # 사용자가 끼어들면 즉시 모델이 말을 멈춘다.
                "interrupt_response": True,
                # 오디오를 들은 모델이 전사 완료를 기다리지 않고 바로 답해야
                # speech-to-speech 특유의 낮은 지연과 자연스러운 턴 전환이 유지된다.
                "create_response": True,
                # "음", "어" 같은 소리로 잘못 끼어들기 판정되는 걸 줄인다.
                "remove_filler_words": True,
            },
            "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
            "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_sampling_rate": config.SAMPLE_RATE,
            "input_audio_transcription": {
                "model": config.TRANSCRIPTION_MODEL,
                "language": "ko",
                "prompt": config.TRANSCRIPTION_PROMPT,
            },
            "voice": {"name": config.VOICE_NAME, "type": config.VOICE_TYPE},
            "modalities": ["text", "audio"],
            "tools": tools.TOOLS,
            "tool_choice": "auto",
        },
    }


class Bridge:
    """One browser session bridged to one Voice Live session."""

    def __init__(self, browser: WebSocket, session: SurveySession) -> None:
        self.browser = browser
        self.session = session
        self.upstream = None
        self._speech_stopped_at: float | None = None
        self._ttfa_pending = False
        # 한 응답 안에서 도구가 여러 번 호출될 수 있다. 호출마다 response.create를
        # 보내면 두 번째가 "conversation already has an active response"로 거부된다.
        # 결과는 각각 돌려주되 response.create는 마지막 하나만 보낸다.
        self._pending_tools = 0
        self._tool_lock = asyncio.Lock()
        self._tool_tasks: set[asyncio.Task] = set()

    async def send_browser(self, payload: dict) -> None:
        with contextlib.suppress(Exception):
            await self.browser.send_text(json.dumps(payload))

    async def push_state(self) -> None:
        await self.send_browser({
            "type": "route.state",
            "state": self.session.snapshot(),
        })

    # --- upstream -> browser --------------------------------------------------

    async def pump_upstream(self) -> None:
        async for raw in self.upstream:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")

            if etype == "error":
                log.error("voice live error: %s", event.get("error"))

            # Time To First Audio, measured from the moment the user stopped
            # speaking to the first byte of audio coming back.
            if etype == "input_audio_buffer.speech_stopped":
                self._speech_stopped_at = time.perf_counter()
                self._ttfa_pending = True

            if etype in ("response.audio.delta", "response.output_audio.delta"):
                if self._ttfa_pending and self._speech_stopped_at is not None:
                    ttfa_ms = int((time.perf_counter() - self._speech_stopped_at) * 1000)
                    self._ttfa_pending = False
                    log.info("TTFA %d ms", ttfa_ms)
                    await self.send_browser({"type": "metrics.ttfa", "ms": ttfa_ms})

            if etype == "response.function_call_arguments.done":
                await self.send_browser(event)
                self._pending_tools += 1
                task = asyncio.create_task(self.handle_tool_call(event))
                # 참조를 들고 있어야 GC가 실행 중인 태스크를 회수하지 않는다.
                self._tool_tasks.add(task)
                task.add_done_callback(self._tool_tasks.discard)
                continue

            await self.send_browser(event)

    async def cancel_tool_tasks(self) -> None:
        """세션이 닫힐 때 아직 도는 도구 태스크를 정리한다."""
        for task in list(self._tool_tasks):
            task.cancel()

    # --- tool calls -----------------------------------------------------------

    async def run_tool(self, name: str, args: dict) -> dict:
        """Execute a route tool and publish its activity and resulting state."""
        log.info("tool call: %s(%s)", name, args)
        await self.send_browser({"type": "tool.started", "name": name, "args": args})

        started = time.perf_counter()
        try:
            result = tools.dispatch(self.session, name, args)
        except Exception:
            # 결과를 안 돌려주면 모델이 영원히 기다리다 대화가 멈춘 것처럼 보인다.
            log.exception("tool %s failed", name)
            result = {"ok": False, "facts": f"{name} 실행 중 오류가 났음", "ask": ""}
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        log.info("tool done: %s -> %s", name, result.get("facts"))
        await self.send_browser(
            {"type": "tool.finished", "name": name, "result": result, "ms": elapsed_ms}
        )
        await self.push_state()
        return result

    async def handle_tool_call(self, event: dict) -> None:
        name = event.get("name", "")
        call_id = event.get("call_id", "")
        try:
            args = json.loads(event.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}

        result = await self.run_tool(name, args)

        if self.upstream is None:
            self._pending_tools = max(0, self._pending_tools - 1)
            return

        # 같은 응답에서 나온 도구 호출들이 동시에 여기 들어올 수 있으므로,
        # 전송 순서와 pending 카운트를 락으로 보호한다.
        async with self._tool_lock:
            await self.upstream.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    # 상태 코드가 아니라 사실을 돌려준다. 그래야 모델이 실제로
                    # 일어난 일에 대해 말할 수 있다.
                    "output": json.dumps(result, ensure_ascii=False),
                },
            }))
            self._pending_tools = max(0, self._pending_tools - 1)
            # 마지막 도구만 응답을 시작시킨다. 남아 있는데 먼저 부르면
            # 두 번째가 "active response" 오류로 거부된다.
            if self._pending_tools == 0:
                await self.upstream.send(json.dumps({"type": "response.create"}))

    # --- browser -> upstream --------------------------------------------------

    async def pump_browser(self) -> None:
        while True:
            raw = await self.browser.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type", "")

            if mtype == "audio":
                if self.upstream is not None:
                    await self.upstream.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": msg["data"],
                    }))
                continue

            if mtype == "text":
                if self.upstream is not None:
                    await self.upstream.send(json.dumps({
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": msg.get("text", "")}],
                        },
                    }))
                    # Same path as a real voice turn: let the model decide
                    # whether/which tool to call, no server-side shortcuts.
                    await self.upstream.send(json.dumps({"type": "response.create"}))
                continue

            if mtype == "greet":
                await self.greet()
                continue

            if mtype and self.upstream is not None:
                await self.upstream.send(json.dumps(msg))

    async def greet(self) -> None:
        if self.upstream is None:
            return
        await self.upstream.send(json.dumps({
            "type": "response.create",
            "response": {"instructions": f"Say exactly this: {tools.GREETING}"},
        }))


@app.websocket("/ws")
async def ws_endpoint(browser: WebSocket) -> None:
    await browser.accept()
    session = SurveySession()
    bridge = Bridge(browser, session)

    try:
        token = await credential().get_token(config.TOKEN_SCOPE)
    except Exception as exc:
        log.exception("failed to acquire Entra token")
        await bridge.send_browser({
            "type": "relay.error",
            "message": f"Could not get an Entra token. Run 'az login' and retry. ({exc})",
        })
        await browser.close()
        return

    log.info("connecting upstream: %s", config.WS_URL)
    try:
        async with websockets.connect(
            config.WS_URL,
            additional_headers={"Authorization": f"Bearer {token.token}"},
            open_timeout=30,
            max_size=None,
        ) as upstream:
            bridge.upstream = upstream
            await upstream.send(json.dumps(build_session()))
            await bridge.send_browser({
                "type": "relay.ready",
                "model": config.MODEL,
                "voice": config.VOICE_NAME,
                "region": config.REGION,
            })
            await bridge.push_state()

            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(bridge.pump_upstream()),
                    asyncio.create_task(bridge.pump_browser()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, WebSocketDisconnect):
                    log.error("pump ended: %r", exc)

    except WebSocketDisconnect:
        log.info("browser disconnected")
    except Exception as exc:
        log.exception("relay failure")
        await bridge.send_browser({"type": "relay.error", "message": str(exc)})
    finally:
        await bridge.cancel_tool_tasks()
        with contextlib.suppress(Exception):
            await browser.close()
        log.info("session closed")


if __name__ == "__main__":
    log.info("=" * 68)
    log.info("Drone survey Voice Live relay")
    log.info("  model    %s", config.MODEL)
    log.info("  voice    %s (%s)", config.VOICE_NAME, config.VOICE_TYPE)
    log.info("  region   %s", config.REGION)
    log.info("  resource %s", config.RESOURCE)
    log.info("  ws       ws://%s:%d/ws", config.HOST, config.PORT)
    log.info("  dashboard: run 'npm run dev' and open http://localhost:3000")
    log.info("=" * 68)
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")
