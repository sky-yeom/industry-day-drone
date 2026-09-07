"""릴레이 전체 경로 점검: 잡담 무시 -> 말 -> 되묻기 확인 -> 도구 -> 상태 ->
자동 3번째 -> 확정.

첫 줄은 회귀 테스트: 모니터 얘기가 전혀 아닌 잡담(그 안에 "하나", "네" 같은
낱말이 우연히 섞여 있음)을 보내고, 그게 절대 monitor를 고른 것으로 처리되지
않는지 확인한다. 그다음 각 경유지는 되묻기 확인을 거친다 (모델이 "모니터 N
맞으시죠?" 하고 물으면 "네"로 답해야 실제로 select_stop이 호출된다).

도구를 부르면 응답이 두 번 생긴다(짧은 예고, 그리고 결과 설명). 그래서 한 턴은
대화가 조용해질 때까지 다 받아야 한다. 일찍 다음 줄을 보내면
"conversation already has an active response"가 난다.
"""
import asyncio, json, sys
import websockets

RELAY = "ws://127.0.0.1:8080/ws"

# (발화, 이 턴에서 select_stop/confirm_route가 실제로 불려야 하는지)
SCRIPT = [
    ("아 맞다, 하나만 물어볼게요. 오늘 점심 뭐 드실 거예요?", False),
    ("첫번째부터 보여줘.", False),
    ("네.", True),
    ("두 번째로 가자.", False),
    ("네.", True),
    ("네.", True),
]


async def turn(ws, line, timeout=50.0):
    await ws.send(json.dumps({"type": "text", "text": line}))

    text, tools, state, errors = "", [], None, []
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
        elif t == "error":
            errors.append(msg.get("error"))
        elif t == "response.done":
            if tool_pending:
                tool_pending = False
                continue
            break

    return text.strip(), tools, state, errors


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

        all_errors, final_state = [], None
        turn_checks = []

        for line, expect_tool in SCRIPT:
            print(f"\n>>> {line}")
            text, tools, state, errors = await turn(ws, line)
            for name, ok, facts in tools:
                print(f"    [도구] {name:<14} ok={ok}  {facts}")
            if state:
                final_state = state
                print(f"    [상태] {state['phase']}  draft={state['draftRoute']}")
            print(f"    [음성] {text}")
            all_errors += errors

            called_a_tool = len(tools) > 0
            turn_ok = called_a_tool == expect_tool
            if not turn_ok:
                print(
                    f"    [!!] 기대={'도구 호출' if expect_tool else '도구 호출 없음'} "
                    f"실제={'도구 호출' if called_a_tool else '도구 호출 없음'}"
                )
            turn_checks.append(turn_ok)

        print("\n--- 결과 ---")
        print("최종 phase :", final_state and final_state["phase"])
        print("확정 경로  :", final_state and final_state["confirmedRoute"])
        print("오류       :", all_errors)

        route = final_state["confirmedRoute"] if final_state else []
        ok = (
            all(turn_checks)
            and final_state
            and final_state["phase"] == "confirmed"
            # 사용자가 2개만 골랐는데 3번째가 자동으로 채워져야 한다
            and route[:2] == ["monitor-1", "monitor-2"]
            and len(route) == 3
            and route[2] == "monitor-3"
            and not all_errors
        )
        print("\n" + ("PASS" if ok else "FAIL"))
        return 0 if ok else 1


sys.exit(asyncio.run(main()))
