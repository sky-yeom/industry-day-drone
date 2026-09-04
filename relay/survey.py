"""경로 계획 상태와 조사 시뮬레이션.

`lib/types.ts`의 RoutePlanningState와 같은 형태(camelCase)를 그대로 사용하므로
브라우저는 변환 없이 렌더링한다. 상태를 React가 아니라 여기에 두면 에이전트와
대시보드가 서로 다른 경로를 보고 있을 수 없다.

각 도구는 `facts`(모델이 자기 말로 옮길 사실)와 `ask`(다음에 물어볼 것)를
돌려준다. 완성된 문장을 돌려주면 모델이 그 문장을 그대로 읽어버려서 말투가
기계처럼 들린다. 사실만 주고 표현은 모델에게 맡긴다.
"""

from __future__ import annotations

import random
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

ANOMALY_POOL = [
    ("crop-stress", "작물 스트레스", "medium",
     "약 0.4헥타르 구간에서 엽록소 수치가 떨어졌고, 관수 부족으로 보입니다."),
    ("debris-field", "잔해 산재", "high",
     "진입로에 단단한 잔해가 흩어져 있어 차량 통행 전에 정리가 필요합니다."),
    ("standing-water", "고인 물", "medium",
     "배수로 근처에 물이 고여 있고 지난 조사 이후로 빠지지 않았습니다."),
    ("structural-crack", "구조물 균열", "high",
     "옹벽을 따라 수직 균열이 진행 중이라 구조 검토가 필요합니다."),
    ("thermal-hotspot", "열점 감지", "high",
     "주변보다 표면 온도가 14도 높아 전기 결함이 의심됩니다."),
    ("wildlife-cluster", "야생동물 무리", "low",
     "울타리 안쪽에 소규모 무리가 쉬고 있습니다. 관찰만 하면 됩니다."),
]


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
    selectedMonitorIds: list[str] = field(default_factory=list)
    draftRoute: list[str] = field(default_factory=list)
    confirmedRoute: list[str] = field(default_factory=list)


class SurveySession:
    """대시보드 세션 하나: 계획 중인 경로와 조사 결과."""

    def __init__(self) -> None:
        self.state = RouteState()
        self.anomalies: list[dict] = []
        self.surveyed = False

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
        self.surveyed = False
        self.anomalies = []

        # 첫 번째 선택 -> 두 번째를 묻는다.
        if len(self.state.draftRoute) == 1:
            remaining = [m for m in MONITOR_IDS if m not in self.state.draftRoute]
            self.state.phase = "selecting-order"
            self.state.selectedMonitorIds = list(self.state.draftRoute)
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
            self.state.selectedMonitorIds = list(self.state.draftRoute)
            self.state.phase = "awaiting-confirmation"
            return {
                "ok": True,
                "facts": f"두 번째 경유지는 {LABELS[mid]}. "
                         f"남은 {LABELS[auto]}은 마지막 순서로 자동 추가됨. "
                         f"전체 경로는 {names(self.state.draftRoute)}",
                "ask": "이 경로가 맞는지, 확정할지 물어볼 것",
            }

        self.state.selectedMonitorIds = list(self.state.draftRoute)
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
        self.state.selectedMonitorIds = list(self.state.draftRoute)
        self.state.phase = "confirmed"
        return {"ok": True,
                "facts": f"경로 확정됨: {names(self.state.confirmedRoute)}",
                "ask": "조사를 시작할지 물어볼 것"}

    def clear_route(self) -> dict:
        self.state = RouteState()
        self.anomalies = []
        self.surveyed = False
        return {"ok": True,
                "facts": "경로를 모두 지웠음",
                "ask": "어느 모니터부터 갈지 다시 물어볼 것"}

    def run_survey(self) -> dict:
        if self.state.phase != "confirmed" or not self.state.confirmedRoute:
            return {"ok": False,
                    "facts": "경로가 확정되지 않아 조사를 시작할 수 없음",
                    "ask": "먼저 경로를 확정할지 물어볼 것"}

        # 경로로 시드를 고정해서 같은 경로는 같은 결과가 나오게 한다.
        rng = random.Random("|".join(self.state.confirmedRoute))
        results: list[dict] = []

        for idx, monitor_id in enumerate(self.state.confirmedRoute):
            for slug, label, severity, notes in rng.sample(ANOMALY_POOL, rng.randint(1, 2)):
                results.append({
                    "id": f"anomaly-{idx}-{slug}",
                    "monitorId": monitor_id,
                    "image": f"/anomalies/{slug}.svg",
                    "label": label,
                    "severity": severity,
                    "confidence": round(rng.uniform(0.71, 0.97), 2),
                    "notes": notes,
                })

        self.anomalies = results
        self.surveyed = True

        high = [a for a in results if a["severity"] == "high"]
        detail = (f"그중 심각 등급은 {len(high)}건이고 가장 급한 것은 "
                  f"{LABELS[high[0]['monitorId']]}의 {high[0]['label']}"
                  if high else "심각 등급은 없음")
        return {"ok": True,
                "facts": f"조사 완료. 이상 징후 {len(results)}건 발견. {detail}",
                "ask": "결과를 대시보드에서 볼 수 있다고 알려줄 것"}

    def get_state(self) -> dict:
        s = self.state
        if s.phase == "selecting-destinations":
            return {"ok": True,
                    "facts": "아직 경로 없음. 갈 수 있는 곳은 모니터 1, 2, 3",
                    "ask": "어디부터 갈지 물어볼 것"}
        if s.phase == "confirmed":
            extra = (f"조사에서 이상 징후 {len(self.anomalies)}건 발견됨"
                     if self.surveyed else "조사는 아직 안 함")
            return {"ok": True,
                    "facts": f"확정 경로는 {names(s.confirmedRoute)}. {extra}",
                    "ask": ""}
        return {"ok": True,
                "facts": f"임시 경로는 {names(s.draftRoute)}, 아직 확정 전",
                "ask": ""}
