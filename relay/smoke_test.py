"""릴레이 전체 경로 점검: 말 -> 도구 -> 상태 -> 자동 3번째 -> 확정 -> 조사.

도구를 부르면 응답이 두 번 생긴다(짧은 예고, 그리고 결과 설명). 그래서 한 턴은
대화가 조용해질 때까지 다 받아야 한다. 일찍 다음 줄을 보내면
"conversation already has an active response"가 난다.
"""
import asyncio, json, sys
import websockets

RELAY = "ws://127.0.0.1:8080/ws"

SCRIPT = [
    "모니터 2부터 갈게요.",
    "그다음은 모니터 1이요.",
    "네 맞아요, 그걸로 확정해주세요.",
    "조사 시작해주세요.",
]


async def turn(ws, line, timeout=50.0):
    await ws.send(json.dumps({"type": "text", "text": line}))

    text, tools, state, anomalies, errors = "", [], None, None, []
    tool_pending = False
    end = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < end:
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=18))
        except asyncio.TimeoutError:
            break

        t = msg.get("type", "")
        if t.endswith("audio_transcript.delta"):
            text += msg.get("delta", "")
        elif t == "response.function_call_arguments.done":
            tool_pending = True
        elif t == "tool.finished":
            tools.append((msg["name"], msg["result"].get("ok"), msg["result"].get("facts")))
        elif t == "route.state":
            state = msg.get("state")
            anomalies = msg.get("anomalies", [])
        elif t == "error":
            errors.append(msg.get("error"))
        elif t == "response.done":
            if tool_pending:
                tool_pending = False
                continue
            break

    return text.strip(), tools, state, anomalies, errors


async def main():
    async with websockets.connect(RELAY, open_timeout=30, max_size=None) as ws:
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            if msg.get("type") == "relay.ready":
                print(f"relay.ready  model={msg.get('model')} voice={msg.get('voice')}")
                break
            if msg.get("type") == "relay.error":
                print("relay.error:", msg.get("message"))
                return 1

        all_errors, final_state, final_anomalies = [], None, []

        for line in SCRIPT:
            print(f"\n>>> {line}")
            text, tools, state, anomalies, errors = await turn(ws, line)
            for name, ok, facts in tools:
                print(f"    [도구] {name:<14} ok={ok}  {facts}")
            if state:
                final_state = state
                print(f"    [상태] {state['phase']}  draft={state['draftRoute']}")
            if anomalies:
                final_anomalies = anomalies
            print(f"    [음성] {text}")
            all_errors += errors

        print("\n--- 결과 ---")
        print("최종 phase :", final_state and final_state["phase"])
        print("확정 경로  :", final_state and final_state["confirmedRoute"])
        print("이상 징후  :", len(final_anomalies))
        if final_anomalies:
            a = final_anomalies[0]
            print(f"  예시: {a['label']} @ {a['monitorId']} ({a['severity']}, {a['confidence']})")
        print("오류       :", all_errors)

        route = final_state["confirmedRoute"] if final_state else []
        ok = (
            final_state
            and final_state["phase"] == "confirmed"
            # 사용자가 2개만 골랐는데 3번째가 자동으로 채워져야 한다
            and route[:2] == ["monitor-2", "monitor-1"]
            and len(route) == 3
            and route[2] == "monitor-3"
            and len(final_anomalies) > 0
            and not all_errors
        )
        print("\n" + ("PASS" if ok else "FAIL"))
        return 0 if ok else 1


sys.exit(asyncio.run(main()))
