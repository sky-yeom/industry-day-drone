"""경로 계획 상태.

`lib/types.ts`의 RoutePlanningState와 같은 형태(camelCase)를 그대로 사용하므로
브라우저는 변환 없이 렌더링한다. 상태를 React가 아니라 여기에 두면 에이전트와
대시보드가 서로 다른 경로를 보고 있을 수 없다.

각 도구는 `facts`(모델이 자기 말로 옮길 사실)와 `ask`(다음에 물어볼 것)를
돌려준다. 완성된 문장을 돌려주면 모델이 그 문장을 그대로 읽어버려서 말투가
기계처럼 들린다. 사실만 주고 표현은 모델에게 맡긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

MONITOR_IDS = ["monitor-1", "monitor-2", "monitor-3"]

LABELS = {
    "monitor-1": "모니터 1",
    "monitor-2": "모니터 2",
    "monitor-3": "모니터 3",
}

# 음성 인식은 "monitor-1" 같은 문자열을 거의 만들지 않는다.
# 사람이 실제로 말하는 형태를 받아주되, 순서를 가리키는 말("첫번째")은 일부러
# 넣지 않는다. 그건 "어느 모니터"에 대한 답이 아니라 위치를 말한 것이라서,
# 임의로 모니터 1로 해석하면 사용자가 고르지도 않은 곳이 경로에 들어간다.
ALIASES = {
    "1": "monitor-1", "one": "monitor-1", "일": "monitor-1", "하나": "monitor-1",
    "1번": "monitor-1", "monitor 1": "monitor-1",
    "모니터 1": "monitor-1", "모니터1": "monitor-1", "monitor-1": "monitor-1",
    "2": "monitor-2", "two": "monitor-2", "이": "monitor-2", "둘": "monitor-2",
    "2번": "monitor-2", "monitor 2": "monitor-2",
    "모니터 2": "monitor-2", "모니터2": "monitor-2", "monitor-2": "monitor-2",
    "3": "monitor-3", "three": "monitor-3", "삼": "monitor-3", "셋": "monitor-3",
    "3번": "monitor-3", "monitor 3": "monitor-3",
    "모니터 3": "monitor-3", "모니터3": "monitor-3", "monitor-3": "monitor-3",
}

# 순서를 가리키는 말. 모니터 이름으로 오해하면 안 되므로 명시적으로 거절한다.
ORDINALS = {"첫번째", "첫 번째", "두번째", "두 번째", "세번째", "세 번째",
            "처음", "다음", "마지막", "아무거나", "아무데나"}

def resolve_monitor(raw: str) -> str | None:
    """모델이 넘긴 값을 표준 모니터 id로 맞춘다. 불명확하면 None."""
    if not raw:
        return None
    key = str(raw).strip().lower()

    if key in ORDINALS:
        return None
    if key in ALIASES:
        return ALIASES[key]

    # "모니터 2번으로 가주세요" 같은 문장에서 숫자를 뽑되, 서로 다른 숫자가
    # 여러 개 섞여 있으면(예: "1이랑 3") 어느 쪽인지 알 수 없으므로 거절한다.
    found = {d for d in ("1", "2", "3") if d in key}
    if len(found) == 1:
        return f"monitor-{found.pop()}"
    return None


def names(ids: list[str]) -> str:
    return ", ".join(LABELS[i] for i in ids)


@dataclass
class RouteState:
    # 필드명은 lib/types.ts와 정확히 일치시켜 React가 그대로 쓰게 한다.
    phase: str = "selecting-destinations"
    draftRoute: list[str] = field(default_factory=list)
    confirmedRoute: list[str] = field(default_factory=list)


class SurveySession:
    """대시보드 세션 하나의 경로 계획 상태."""

    def __init__(self) -> None:
        self.state = RouteState()

    def snapshot(self) -> dict:
        return asdict(self.state)

    # --- 도구 -----------------------------------------------------------------

    def select_stop(self, monitor: str) -> dict:
        """경유지를 하나 고른다. 두 개가 정해지면 남은 하나는 자동으로 붙는다."""
        mid = resolve_monitor(monitor)
        if not mid:
            return {"ok": False,
                    "facts": "사용자가 말한 모니터를 알아듣지 못함",
                    "ask": "모니터 1, 2, 3 중 어디인지 다시 물어볼 것"}

        if mid in self.state.draftRoute:
            remaining = [m for m in MONITOR_IDS if m not in self.state.draftRoute]
            return {"ok": False,
                    "facts": f"{LABELS[mid]}은 이미 경로에 있음. "
                             f"현재 경로는 {names(self.state.draftRoute)}",
                    "ask": f"남은 곳은 {names(remaining)}"}

        self.state.draftRoute.append(mid)
        self.state.confirmedRoute = []

        # 첫 번째 선택 -> 두 번째를 묻는다.
        if len(self.state.draftRoute) == 1:
            remaining = [m for m in MONITOR_IDS if m not in self.state.draftRoute]
            self.state.phase = "selecting-order"
            return {
                "ok": True,
                "facts": f"첫 번째 경유지는 {LABELS[mid]}",
                "ask": f"두 번째로 갈 곳을 물어볼 것. 선택지는 {names(remaining)}",
            }

        # 두 번째 선택 -> 남은 한 곳은 대시보드가 자동으로 채운다.
        if len(self.state.draftRoute) == 2:
            leftover = [m for m in MONITOR_IDS if m not in self.state.draftRoute]
            auto = leftover[0]
            self.state.draftRoute.append(auto)
            self.state.phase = "awaiting-confirmation"
            return {
                "ok": True,
                "facts": f"두 번째 경유지는 {LABELS[mid]}. "
                         f"남은 {LABELS[auto]}은 마지막 순서로 자동 추가됨. "
                         f"전체 경로는 {names(self.state.draftRoute)}",
                "ask": "이 경로가 맞는지, 확정할지 물어볼 것",
            }

        self.state.phase = "awaiting-confirmation"
        return {"ok": True,
                "facts": f"경로는 {names(self.state.draftRoute)}",
                "ask": "확정할지 물어볼 것"}

    def confirm_route(self) -> dict:
        if len(self.state.draftRoute) < 3:
            return {"ok": False,
                    "facts": "아직 경로가 다 정해지지 않아서 확정할 수 없음",
                    "ask": "어느 모니터부터 갈지 물어볼 것"}

        self.state.confirmedRoute = list(self.state.draftRoute)
        self.state.phase = "confirmed"
        return {"ok": True,
                "facts": f"경로 확정됨: {names(self.state.confirmedRoute)}",
                "ask": ""}

    def clear_route(self) -> dict:
        self.state = RouteState()
        return {"ok": True,
                "facts": "경로를 모두 지웠음",
                "ask": "어느 모니터부터 갈지 다시 물어볼 것"}

    def get_state(self) -> dict:
        s = self.state
        if s.phase == "selecting-destinations":
            return {"ok": True,
                    "facts": "아직 경로 없음. 갈 수 있는 곳은 모니터 1, 2, 3",
                    "ask": "어디부터 갈지 물어볼 것"}
        if s.phase == "confirmed":
            return {"ok": True,
                    "facts": f"확정 경로는 {names(s.confirmedRoute)}",
                    "ask": ""}
        return {"ok": True,
                "facts": f"임시 경로는 {names(s.draftRoute)}, 아직 확정 전",
                "ask": ""}
