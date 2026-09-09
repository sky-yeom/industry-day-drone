"""Local mock relay smoke flow; --voice explicitly opts into live Azure speech.

Run the relay separately, then: relay/.venv/bin/python -m relay.smoke_test
The default never authenticates or calls an external model.
"""

import argparse
import asyncio
import json

import websockets
try:
    from .tools import GREETING
except ImportError:
    from tools import GREETING

SEARCH_PROMPT = "초록색 티셔츠를 입고 갈색 머리를 한 사람을 찾아 주세요."
APPEARANCE = [
    {"attribute": "shirtColor", "operator": "include", "values": ["green"]},
    {"attribute": "hairColor", "operator": "include", "values": ["brown"]},
]


async def receive_until(ws, predicate, timeout=90):
    events = []
    async with asyncio.timeout(timeout):
        while True:
            event = json.loads(await ws.recv())
            events.append(event)
            if event.get("type") == "relay.error":
                raise AssertionError(event.get("message"))
            if predicate(event):
                return events


async def command(ws, name, args, request_id):
    await ws.send(json.dumps({
        "type": "command", "name": name, "args": args, "requestId": request_id}))
    events = await receive_until(ws, lambda e: e.get("type") == "tool.finished" and e.get("id") == request_id)
    if not events[-1]["result"]["ok"]:
        raise AssertionError(events[-1]["result"]["facts"])
    return await receive_until(ws, lambda e: e.get("type") == "route.state")


async def voice_turn(ws, text):
    await ws.send(json.dumps({"type": "text", "text": text}))
    events = []
    quiet_since = None
    loop = asyncio.get_running_loop()
    async with asyncio.timeout(90):
        while True:
            timeout = 30 if quiet_since is None else max(0.001, 2 - (loop.time() - quiet_since))
            try:
                event = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            except asyncio.TimeoutError:
                return events
            events.append(event)
            if event.get("type") == "response.done":
                quiet_since = loop.time()
            elif event.get("type") in {"response.created", "response.function_call_arguments.done", "tool.finished"}:
                quiet_since = None
            if event.get("type") in {"error", "relay.error"}:
                raise AssertionError(f"음성 경로 오류: {event.get('error') or event.get('message')}")
            if quiet_since is not None and loop.time() - quiet_since >= 2:
                return events


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8080/ws")
    parser.add_argument("--voice", action="store_true", help="명시적으로 Azure 음성 연결을 사용")
    parser.add_argument("--greeting-only", action="store_true", help="고정 음성 인사말만 확인")
    parser.add_argument("--bad-priority", action="store_true")
    parser.add_argument("--wrong-description", action="store_true")
    args = parser.parse_args()
    if args.greeting_only and not args.voice:
        parser.error("--greeting-only requires --voice")
    prompt = "빨간색 티셔츠를 입은 사람을 찾아 주세요." if args.wrong_description else SEARCH_PROMPT
    appearance = [{"attribute": "shirtColor", "operator": "include", "values": ["red"]}] if args.wrong_description else APPEARANCE
    url = args.url + ("&" if "?" in args.url else "?") + f"voice={int(args.voice)}"
    async with websockets.connect(url, open_timeout=30, max_size=8 * 1024 * 1024) as ws:
        events = await receive_until(ws, lambda e: e.get("type") == "route.state")
        ready_event = next(e for e in events if e["type"] == "relay.ready")
        if ready_event["mode"] != "mock" and not args.voice:
            raise AssertionError("기본 점검은 TRIAGE_MODE=mock에서 실행하세요. 실제 분석은 명시적으로 별도 검증해야 합니다.")
        if args.voice:
            await ws.send(json.dumps({"type": "greet"}))
            opening = await receive_until(ws, lambda e: e.get("type") == "response.done")
            spoken = "".join(e.get("delta", "") for e in opening
                             if e["type"] in {"response.audio_transcript.delta", "response.output_audio_transcript.delta"})
            assert spoken.strip() == GREETING, f"고정 인사말과 다릅니다: {spoken}"
            assert not any(word in spoken for word in ("빨간", "붉은", "금발", "초록", "갈색", "줄무늬")), spoken
            assert not any(e["type"] == "tool.finished" for e in opening), "첫 안내가 참가자의 프롬프트를 대신 확정했습니다."
            print("Opening:", spoken)
            if args.greeting_only:
                return
            for text in ("오늘 점심 뭐 드실 거예요?", "아무거나요."):
                events = await voice_turn(ws, text)
                assert not any(e["type"] == "tool.finished" for e in events), "불명확한 말로 임무 변경"
            events = await voice_turn(ws, prompt)
            assert not any(e["type"] == "tool.finished" for e in events), "프롬프트 확인 동의 전에 도구 실행"
            events = await voice_turn(ws, "네, 제가 말한 탐색 프롬프트가 맞아요.")
            assert any(e["type"] == "tool.finished" and e["name"] == "confirm_prompt"
                       and e["result"]["ok"] for e in events), "참여자 탐색 프롬프트 미확인"
            await voice_turn(ws, "모니터 삼부터 가요.")
            events = await voice_turn(ws, "그 다음은 바다에 빠진 사람이요.")
            states = [e["state"] for e in events if e["type"] == "route.state"]
            assert states and states[-1]["missionPhase"] == "ready", "출발 동의 전에 시작했거나 경로 미준비"
            launch_events = await voice_turn(ws, "네, 이 경로로 출발하세요.")
        else:
            route = ("monitor-3", "monitor-2") if args.bad_priority else ("monitor-3", "monitor-1")
            await command(ws, "confirm_prompt", {
                "prompt_text": prompt, "appearance_constraints": appearance, "unsupported_appearance": [],
            }, "participant-prompt")
            await command(ws, "select_stop", {"monitor": route[0]}, "first")
            await command(ws, "select_stop", {"monitor": route[1]}, "second")
            events = await command(ws, "confirm_route", {}, "ready")
            assert events[-1]["state"]["missionPhase"] == "ready"
            assert events[-1]["state"]["elapsedMs"] == 0
            await command(ws, "launch_mission", {}, "launch")
            launch_events = []
        events = launch_events
        if not any(e["type"] == "mission.debrief" for e in events):
            events += await receive_until(ws, lambda e: e.get("type") == "mission.debrief")
        states = [e["state"] for e in events if e["type"] == "route.state"]
        final = states[-1]
        assert final["missionPhase"] == "complete"
        assert final["score"]["total"] == 3
        assert all(p["outcome"] is not None for p in final["people"])
        if not args.voice:
            assert final["score"]["rescuedCount"] == (0 if args.wrong_description else 2 if args.bad_priority else 3)
        for person in final["people"]:
            if person["outcome"] != "too_late":
                frame = next(c for c in final["captures"] if c["id"] == person["captureId"])
                assert frame["evidence"]["targetPresent"] and frame["imageUrl"].startswith("data:image/")
                assert person["resolvedAtMs"] < person["deadlineMs"]
        if args.voice:
            assert any(e["type"] == "mission.launch.done" for e in events), "출발 음성 안내가 완료되지 않았습니다."
            assert not any(e["type"] == "mission.debrief.response" for e in events), "재생 준비 전에 결과 음성이 시작되었습니다."
            await ws.send(json.dumps({"type": "results.ready", "runId": final["runId"]}))
            result_voice = await receive_until(ws, lambda e: e["type"] in {"mission.debrief.done", "mission.debrief.failed"})
            assert result_voice[-1]["type"] == "mission.debrief.done", result_voice[-1]
            spoken = "".join(e.get("delta", "") for e in result_voice
                             if e["type"] in {"response.audio_transcript.delta", "response.output_audio_transcript.delta"})
            assert "구조" in spoken and "?" not in spoken, spoken
            print("Results voice:", spoken)
        print("PASS:", next(e["text"] for e in events if e["type"] == "mission.debrief"))


if __name__ == "__main__":
    asyncio.run(main())
