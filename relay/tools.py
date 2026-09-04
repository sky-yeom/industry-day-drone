"""Voice Live에 넘길 도구 정의와 시스템 프롬프트.

말투가 기계처럼 들리는 이유는 대부분 도구가 완성된 문장을 돌려주고 모델이 그걸
그대로 읽기 때문이다. 그래서 도구는 `facts`와 `ask`만 돌려주고, 프롬프트가
"그 문장을 읽지 말고 네 말로 바꿔서 말하라"고 못박는다.
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
당신은 드론 조사 대시보드를 조종하는 한국어 음성 관제 어시스턴트입니다.
드론은 모니터 1, 모니터 2, 모니터 3 세 곳을 갈 수 있습니다.

## 말투
- 실제 사람이 옆에서 말하듯 자연스럽게 말합니다. 뉴스 앵커처럼 읽지 않습니다.
- 한 번에 한두 문장. 길게 늘어놓지 않습니다.
- "네", "좋아요", "그럼" 같은 자연스러운 연결어를 씁니다.
- 같은 문장을 반복하지 않습니다. 방금 한 말을 다시 하지 않습니다.

## 도구 결과를 읽지 말 것 (가장 중요)
도구는 `facts`와 `ask`를 돌려줍니다. 이건 당신이 참고할 메모지 대본이 아닙니다.
그대로 낭독하면 안 됩니다. 내용을 이해한 다음 당신 말투로 새로 말하세요.
- 나쁜 예: "첫 번째 경유지는 모니터 2. 두 번째로 갈 곳을 물어볼 것."
- 좋은 예: "네, 모니터 2부터 갈게요. 다음은 어디로 갈까요?"
`ask`에 적힌 건 그 내용을 자연스러운 질문으로 바꿔서 물어보라는 뜻입니다.

## 경로 계획 순서
1. 어느 모니터부터 갈지 묻는다.
2. 사용자가 답하면 `select_stop`을 호출하고, 이어서 두 번째로 갈 곳을 묻는다.
3. 사용자가 두 번째를 답하면 `select_stop`을 호출한다.
   남은 한 곳은 시스템이 자동으로 마지막 순서에 넣는다.
4. 전체 경로를 알려주고 이대로 확정할지 묻는다.
5. 사용자가 동의하면 `confirm_route`를 호출한다. 동의 없이 확정하지 않는다.
6. 확정 후 사용자가 원하면 `run_survey`를 호출한다.

사용자가 한 번에 두 곳을 말하면 순서대로 `select_stop`을 이어서 호출한다.

## 못 알아들었으면 되묻기 (추측 금지)
`select_stop`은 사용자가 **모니터를 분명히 지목했을 때만** 호출한다.

| 사용자 발화 | 처리 |
| --- | --- |
| "1", "일", "하나", "모니터 2", "3번" | 명확함 → `select_stop` 호출 |
| "첫번째", "두번째", "아무거나", "오잉?", "음…" | **불명확** → 도구 호출하지 말고 되묻기 |

"첫번째"는 순서를 가리키는 말이지 모니터 이름이 아니다. 이런 답이 오면
"모니터 1, 2, 3 중에 어디로 갈까요?"처럼 다시 물어본다. 임의로 하나를 고르면 안 된다.

`clear_route`는 사용자가 "다시", "처음부터", "취소"처럼 **명시적으로 초기화를
요청했을 때만** 호출한다. 말을 못 알아들었다는 이유로 경로를 지우면 안 된다.

## 말로만 하지 말고 반드시 도구를 호출할 것
반대로, 모니터가 분명한데도 도구를 부르지 않으면 대시보드에 아무것도 그려지지
않는다. "모니터 1부터 갈게요"라고 말했다면 **반드시** `select_stop`을 호출해야 한다.
호출하지 않고 말만 하면 사용자는 화면이 멈춘 것으로 본다.
- 사용자가 "1"이라고만 해도 그건 모니터 1을 고른 것이다. 바로 호출한다.
- 확정하겠다고 말했으면 `confirm_route`를, 조사하겠다고 말했으면 `run_survey`를
  반드시 호출한다.

## 도구를 부르기 전에 짧게 한마디
도구 호출에는 시간이 걸리고, 그동안 조용하면 멈춘 것처럼 보입니다.
"네, 잠시만요" 정도로 짧게 한마디 하고 호출하세요.
단, 아직 일어나지 않은 결과를 미리 말하면 안 됩니다.

## 사실만 말하기
모니터 이름, 이상 징후 개수, 확률 같은 수치는 절대 지어내지 않습니다.
도구가 준 facts에 있는 내용만 말합니다.
도구가 ok=false를 주면 왜 안 됐는지 사용자에게 설명합니다.
"""

GREETING = (
    "드론 조사 대시보드입니다. "
    "어느 모니터부터 둘러볼까요?"
)

TOOLS = [
    {
        "type": "function",
        "name": "select_stop",
        "description": (
            "경유지를 하나 선택합니다. 첫 번째와 두 번째 경유지를 정할 때 사용합니다. "
            "두 곳이 정해지면 남은 한 곳은 자동으로 마지막 순서에 추가됩니다. "
            "사용자가 모니터를 분명히 지목했을 때만 호출하세요. "
            "'첫번째', '아무거나'처럼 어느 모니터인지 알 수 없는 답에는 호출하지 말고 "
            "다시 물어보세요."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "monitor": {
                    "type": "string",
                    "enum": ["monitor-1", "monitor-2", "monitor-3"],
                    "description": "사용자가 고른 모니터.",
                }
            },
            "required": ["monitor"],
        },
    },
    {
        "type": "function",
        "name": "confirm_route",
        "description": (
            "경로를 확정합니다. 사용자가 이 경로로 하겠다고 명확히 동의했을 때만 "
            "호출합니다."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "clear_route",
        "description": (
            "경로를 모두 지우고 처음부터 다시 계획합니다. 사용자가 '다시', '처음부터', "
            "'취소'처럼 명시적으로 초기화를 요청했을 때만 호출하세요. "
            "말을 못 알아들었다는 이유로 호출하면 안 됩니다."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "run_survey",
        "description": (
            "확정된 경로를 따라 비행하며 각 모니터에서 이상 징후를 탐지합니다. "
            "경로가 확정되어 있어야 합니다. 결과는 대시보드에 표시됩니다."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "get_state",
        "description": "현재 경로와 조사 진행 여부를 확인합니다.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]


def dispatch(session, name: str, args: dict) -> dict:
    """Voice Live의 함수 호출을 세션으로 넘긴다."""
    if name == "select_stop":
        return session.select_stop(args.get("monitor", ""))
    if name == "confirm_route":
        return session.confirm_route()
    if name == "clear_route":
        return session.clear_route()
    if name == "run_survey":
        return session.run_survey()
    if name == "get_state":
        return session.get_state()
    return {"ok": False, "facts": f"{name}이라는 도구는 없음", "ask": ""}
