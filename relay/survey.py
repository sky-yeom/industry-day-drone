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

# 임시(placeholder) 정답 이상 징후 목록. data/scenario.ts의 GROUND_TRUTH_ANOMALIES와
# 반드시 같은 내용으로 맞춰둔다 (실제 시나리오가 정해지면 두 파일을 함께 교체).
GROUND_TRUTH_ANOMALIES = [
    {"id": "standing-water", "monitorId": "monitor-1", "label": "침수 구역",
     "keywords": ["침수", "물", "홍수", "고인 물"]},
    {"id": "structural-crack", "monitorId": "monitor-2", "label": "구조물 균열",
     "keywords": ["균열", "금", "구조물", "붕괴"]},
    {"id": "debris-field", "monitorId": "monitor-2", "label": "잔해물 산재",
     "keywords": ["잔해", "쓰레기", "파편"]},
    {"id": "crop-stress", "monitorId": "monitor-3", "label": "작물 손상",
     "keywords": ["작물", "농작물", "식물", "고사"]},
]


def score_prompt(prompt_text: str) -> dict:
    """사용자가 말한 주의사항과 정답 이상 징후 목록을 비교해 점수를 매긴다.

    실제 이미지 기반 판정이 아니라, 사용자의 문장에 정답 항목의 키워드가
    포함돼 있는지 보는 단순 매칭이다. 결과 탭을 끝까지 동작하게 만드는
    임시(placeholder) 채점 방식이며, 실제 시나리오/정답 데이터가 정해지면
    교체될 예정이다.
    """
    matched, missed = [], []
    for anomaly in GROUND_TRUTH_ANOMALIES:
        if any(kw in prompt_text for kw in anomaly["keywords"]):
            matched.append(anomaly)
        else:
            missed.append(anomaly)

    total = len(GROUND_TRUTH_ANOMALIES)
    accuracy = round((len(matched) / total) * 100) if total else 0
    return {
        "matchedCount": len(matched),
        "missedCount": len(missed),
        "totalGroundTruth": total,
        "accuracyPercent": accuracy,
        "matched": matched,
        "missed": missed,
    }


def resolve_monitor(raw: str) -> str | None:
    """모델이 넘긴 값을 표준 모니터 id로 맞춘다. 그 외에는 None.

    `select_stop`의 `monitor` 인자는 도구 스키마의 enum
    (`monitor-1`/`monitor-2`/`monitor-3`)으로 이미 제한돼 있으므로, 여기서는
    정확히 그 셋 중 하나인지만 확인한다. 예전에는 "첫번째", "1번" 같은 자연어
    문장을 정규식으로 직접 해석했지만, 그 로직은 모델이 이미 값을 정확히 넘겨주는
    실제 음성 경로에서는 쓰이지 않았고, 문장 아무 곳에 있는 숫자까지 주워 담는
    등 엉뚱하게 오판할 여지만 있었다. 입력이 정확히 셋 중 하나가 아니면 그냥
    거절한다."""
    if raw in MONITOR_IDS:
        return raw
    return None


def names(ids: list[str]) -> str:
    return ", ".join(LABELS[i] for i in ids)


@dataclass
class RouteState:
    # 필드명은 lib/types.ts와 정확히 일치시켜 React가 그대로 쓰게 한다.
    phase: str = "selecting-destinations"
    draftRoute: list[str] = field(default_factory=list)
    confirmedRoute: list[str] = field(default_factory=list)


@dataclass
class MissionState:
    # 필드명은 lib/types.ts의 MissionState와 정확히 일치시킨다.
    promptPhase: str = "briefing"
    userPromptText: str = ""
    detectionPhase: str = "idle"
    detectedAnomalies: list[dict] = field(default_factory=list)
    score: dict | None = None


class SurveySession:
    """대시보드 세션 하나의 경로 계획 + 임무(브리핑/탐지) 상태."""

    def __init__(self) -> None:
        self.state = RouteState()
        self.mission = MissionState()

    def snapshot(self) -> dict:
        return {**asdict(self.state), **asdict(self.mission)}

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

    # --- 임무(브리핑/탐지) 도구 -------------------------------------------------

    def confirm_prompt(self, prompt_text: str) -> dict:
        """사용자가 자기 말로 확인해준 주의사항을 확정한다."""
        text = prompt_text.strip()
        if not text:
            return {"ok": False,
                    "facts": "사용자가 뭘 주의 깊게 볼지 아직 말하지 않음",
                    "ask": "어떤 점을 특히 주의해서 볼지 물어볼 것"}

        self.mission.userPromptText = text
        self.mission.promptPhase = "confirmed"
        return {
            "ok": True,
            "facts": f"사용자가 확정한 주의사항: {text}",
            "ask": "이제 스캔을 시작한다고 안내하고 이어서 탐지 결과를 말할 것",
        }

    def report_detection(self) -> dict:
        """(임시) 탐지 결과를 채우고 사용자의 주의사항과 비교해 채점한다."""
        if self.mission.promptPhase != "confirmed":
            return {"ok": False,
                    "facts": "아직 주의사항이 확정되지 않아서 탐지를 시작할 수 없음",
                    "ask": "어떤 점을 주의해서 볼지 먼저 확인할 것"}

        self.mission.detectedAnomalies = [
            {"id": a["id"], "monitorId": a["monitorId"], "label": a["label"]}
            for a in GROUND_TRUTH_ANOMALIES
        ]
        self.mission.score = score_prompt(self.mission.userPromptText)
        self.mission.detectionPhase = "complete"

        found = ", ".join(a["label"] for a in self.mission.detectedAnomalies)
        return {
            "ok": True,
            "facts": f"탐지 완료. 발견된 이상 징후: {found}. "
                     f"사용자가 맞춘 개수: {self.mission.score['matchedCount']}"
                     f"/{self.mission.score['totalGroundTruth']}",
            "ask": "",
        }
