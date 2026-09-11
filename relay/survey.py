"""Deterministic, relay-owned emergency-triage state and monotonic clock."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
import time
from uuid import uuid4

try:
    from .appearance import validate_constraints
except ImportError:
    from appearance import validate_constraints

SCENARIO = json.loads(
    (Path(__file__).resolve().parents[1] / "data/emergency-triage.json").read_text("utf-8")
)
MONITOR_IDS = [p["monitorId"] for p in SCENARIO["people"]]
LABELS = {person["monitorId"]: person["label"] for person in SCENARIO["people"]}
TERMINAL = {"complete", "aborted"}
ACTIVE = {"flying", "capturing", "analyzing"}
MAX_PROMPT_LENGTH = 2000


def names(ids):
    return " → ".join(LABELS[mid] for mid in ids)


def result(ok, facts, ask=""):
    return {"ok": ok, "facts": facts, "ask": ask}


def validate_evidence(evidence):
    if (not isinstance(evidence, dict)
            or set(evidence) != {"targetPresent", "description", "box"}
            or type(evidence.get("targetPresent")) is not bool):
        raise ValueError("이미지 분석 결과의 대상 확인 값이 올바르지 않습니다.")
    description = evidence.get("description")
    box = evidence.get("box")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("이미지 분석 결과에 시각적 근거가 없습니다.")
    if box is not None:
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)):
            raise ValueError("이미지 분석 영역이 올바르지 않습니다.")
        x, y, w, h = box
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1 or y + h > 1:
            raise ValueError("이미지 분석 영역이 이미지 밖에 있습니다.")
        if not evidence["targetPresent"]:
            raise ValueError("대상이 없다는 분석 결과에 탐지 영역이 포함되어 있습니다.")
    return {"targetPresent": evidence["targetPresent"],
            "description": description.strip(), "box": list(box) if box is not None else None}


@dataclass
class RouteState:
    phase: str = "selecting-destinations"
    draftRoute: list[str] = field(default_factory=list)
    confirmedRoute: list[str] = field(default_factory=list)


class SurveySession:
    def __init__(self, *, clock=time.monotonic, mode="mock", scenario=None, run_id=None):
        if mode not in ("mock", "azure"):
            raise ValueError("지원하지 않는 이미지 분석 모드입니다.")
        self.clock = clock
        self.scenario = deepcopy(scenario or SCENARIO)
        self.state = RouteState()
        self.data = {
            "runId": run_id or str(uuid4()), "revision": 0, "missionPhase": "briefing",
            "promptPhase": "briefing", "userPromptText": "",
            "appearanceConstraints": [], "unsupportedAppearance": [],
            "mode": mode, "elapsedMs": 0, "clockRunning": False,
            "activeMonitorId": None, "people": [], "captures": [], "score": None, "error": None,
        }
        for person in self.scenario["people"]:
            self.data["people"].append({
                key: deepcopy(person[key]) for key in
                ("id", "monitorId", "label", "clue",
                 "initiallyInjured", "deadlineMs")
            } | {"targetDescription": self.scenario["targetAppearance"]["description"],
                 "deteriorationMs": 0 if person["initiallyInjured"] else
                 max(0, person["deadlineMs"] - self.scenario["injuryWindowMs"]),
                 "outcome": None, "resolvedAtMs": None, "captureId": None, "attempts": 0})
        self._started = None
        self._paused_at = None
        self._paused_seconds = 0
        self._frozen_ms = 0
        self._resume_phase = None
        self._active_capture = None

    @property
    def phase(self):
        return self.data["missionPhase"]

    @property
    def run_id(self):
        return self.data["runId"]

    def elapsed_ms(self):
        if self._started is None or self.phase in TERMINAL:
            return self._frozen_ms
        now = self._paused_at if self._paused_at is not None else self.clock()
        return max(0, int((now - self._started - self._paused_seconds) * 1000))

    def touch(self):
        self.data["revision"] += 1

    def snapshot(self):
        return deepcopy(asdict(self.state) | self.data | {"elapsedMs": self.elapsed_ms()})

    def person(self, monitor):
        return next(p for p in self.data["people"] if p["monitorId"] == monitor)

    def _editable(self):
        return self.phase in {"briefing", "ready"}

    def _prompt_required(self):
        return result(False, "사람을 찾기 위한 탐색 프롬프트를 먼저 작성하고 확인해야 합니다.",
                      "이미지에서 사람을 어떻게 찾을지 사용자에게 물어볼 것")

    def confirm_prompt(self, prompt_text, appearance_constraints=None, unsupported_appearance=None):
        if not self._editable():
            return result(False, "출발한 임무의 탐색 프롬프트는 바꿀 수 없습니다.")
        if not isinstance(prompt_text, str) or not prompt_text.strip():
            return result(False, "사람을 어떻게 찾을지 탐색 프롬프트를 입력해 주세요.")
        text = prompt_text.strip()
        if len(text) > MAX_PROMPT_LENGTH:
            return result(False, f"탐색 프롬프트는 {MAX_PROMPT_LENGTH}자 이하로 입력해 주세요.")
        try:
            constraints, unsupported = validate_constraints(
                [] if appearance_constraints is None else appearance_constraints,
                [] if unsupported_appearance is None else unsupported_appearance)
        except ValueError as exc:
            return result(False, str(exc), "참가자가 실제로 말한 외형 조건만 다시 확인할 것")
        self.data.update(promptPhase="confirmed", userPromptText=text,
                         appearanceConstraints=constraints, unsupportedAppearance=unsupported)
        self.touch()
        cases = " / ".join(
            f"{LABELS[person['monitorId']]}: {person['clue']}" for person in self.data["people"])
        return result(True, f"사용자가 확인한 탐색 프롬프트: {text}. 장소별 신고 내용: {cases}",
                      "먼저 세 장소의 신고 내용을 모두 설명한 뒤 첫 번째로 갈 장소를 물어볼 것")

    def select_stop(self, monitor):
        if self.data["promptPhase"] != "confirmed":
            return self._prompt_required()
        if not self._editable():
            return result(False, "출발한 임무의 경로는 바꿀 수 없습니다.")
        if not isinstance(monitor, str) or monitor not in MONITOR_IDS:
            return result(False, "장소를 확인하지 못했습니다.", "어느 장소인지 다시 물어볼 것")
        if monitor in self.state.draftRoute:
            return result(False, "이미 경로에 있는 장소입니다.", "수정하려면 경로를 지울지 물어볼 것")
        self.state.draftRoute.append(monitor)
        self.state.confirmedRoute = []
        self.data["missionPhase"] = "briefing"
        if len(self.state.draftRoute) == 2:
            self.state.draftRoute += [m for m in MONITOR_IDS if m not in self.state.draftRoute]
            self.state.phase = "awaiting-confirmation"
            ask = "confirm_route로 준비한 뒤 전체 경로를 읽고 출발 동의를 물어볼 것"
        else:
            self.state.phase = "selecting-order"
            ask = "두 번째로 갈 장소를 물어볼 것"
        self.touch()
        return result(True, f"선택 경로: {names(self.state.draftRoute)}", ask)

    def clear_route(self):
        if not self._editable():
            return result(False, "출발 후에는 경로를 지울 수 없습니다. 임무 중단을 이용하세요.")
        self.state = RouteState()
        self.data.update(missionPhase="briefing", error=None)
        self.touch()
        return result(True, "경로를 지웠습니다.", "어디부터 갈지 물어볼 것")

    def confirm_route(self):
        if self.data["promptPhase"] != "confirmed":
            return self._prompt_required()
        if not self._editable() or len(self.state.draftRoute) != len(MONITOR_IDS):
            return result(False, "출발 전에 첫 번째와 두 번째 목적지를 정해야 합니다.")
        self.state.confirmedRoute = self.state.draftRoute[:]
        self.state.phase = "confirmed"
        self.data["missionPhase"] = "ready"
        self.touch()
        return result(True, f"준비된 경로: {names(self.state.confirmedRoute)}. 아직 출발하지 않았습니다.",
                      "이 경로로 출발할지 한 번 물어보고 명시적인 동의를 기다릴 것")

    def launch_mission(self, readiness_error=None):
        if self.data["promptPhase"] != "confirmed":
            return self._prompt_required()
        if self._started is not None:
            return result(True, "이미 출발한 임무입니다. 중복 출발하지 않습니다.")
        if self.phase != "ready":
            return result(False, "경로를 먼저 준비해야 출발할 수 있습니다.")
        if readiness_error:
            self.data["error"] = readiness_error
            self.touch()
            return result(False, readiness_error)
        self._started = self.clock()
        self.data.update(missionPhase="flying", clockRunning=True, error=None)
        self.touch()
        return result(True, "출발했습니다. 이동, 촬영, 분석 중에도 구조 시한이 줄어듭니다.")

    def set_operation(self, phase, monitor, run_id):
        if run_id != self.run_id or self.phase not in ACTIVE or phase not in ACTIVE:
            return False
        self.data.update(missionPhase=phase, activeMonitorId=monitor)
        self.touch()
        return True

    def add_capture(self, capture, run_id):
        if (run_id != self.run_id or self.phase != "capturing"
                or capture.monitor_id != self.data["activeMonitorId"]
                or not capture.image_bytes
                or any(c["id"] == capture.id for c in self.data["captures"])):
            return False
        self._active_capture = capture.id
        self.data["captures"].append({
            "id": capture.id, "monitorId": capture.monitor_id, "imageUrl": capture.image_url,
            "capturedAtMs": self.elapsed_ms(), "status": "captured", "evidence": None,
            "mode": self.data["mode"],
        })
        self.person(capture.monitor_id)["attempts"] += 1
        self.touch()
        return True

    def analyzing(self, capture_id, run_id):
        if run_id != self.run_id or self.phase not in ACTIVE or capture_id != self._active_capture:
            return False
        self.data["captures"][-1]["status"] = "analyzing"
        return self.set_operation("analyzing", self.data["activeMonitorId"], run_id)

    def apply_detection(self, run_id, capture_id, evidence):
        if (run_id != self.run_id or self.phase != "analyzing"
                or capture_id != self._active_capture):
            return False
        evidence = validate_evidence(evidence)
        capture = self.data["captures"][-1]
        if capture["status"] != "analyzing":
            return False
        now = self.elapsed_ms()
        self.expire()
        if self.phase in TERMINAL:
            return False
        capture.update(evidence=evidence, status="detected" if evidence["targetPresent"] else "not-found")
        person = self.person(capture["monitorId"])
        if evidence["targetPresent"] and person["outcome"] is None and now < person["deadlineMs"]:
            injured = person["initiallyInjured"] or now >= person["deteriorationMs"]
            person.update(outcome="rescued_but_hurt" if injured else "rescued",
                          resolvedAtMs=now, captureId=capture_id)
        self._active_capture = None
        self.touch()
        self._finish_if_resolved()
        return True

    def expire(self):
        if self.phase not in ACTIVE:
            return False
        now = self.elapsed_ms()
        changed = False
        for person in self.data["people"]:
            if person["outcome"] is None and now >= person["deadlineMs"]:
                person.update(outcome="too_late", resolvedAtMs=person["deadlineMs"])
                changed = True
        if changed:
            self.touch()
            self._finish_if_resolved()
        return changed

    def _finish_if_resolved(self):
        people = self.data["people"]
        if all(p["outcome"] is not None for p in people):
            self._frozen_ms = self.elapsed_ms()
            self.data.update(missionPhase="complete", clockRunning=False, activeMonitorId=None,
                             error=None, score={
                                 "rescuedCount": sum(p["outcome"] in ("rescued", "rescued_but_hurt") for p in people),
                                 "injuredCount": sum(p["outcome"] == "rescued_but_hurt" for p in people),
                                 "tooLateCount": sum(p["outcome"] == "too_late" for p in people),
                                 "total": len(people),
                             })
            self.touch()

    def pause(self, message):
        self.expire()
        if self.phase not in ACTIVE:
            return
        self._resume_phase = self.phase
        self._paused_at = self.clock()
        self.data.update(missionPhase="paused", clockRunning=False, error=message)
        if self._active_capture:
            self.data["captures"][-1]["status"] = "error"
        self.touch()

    def retry_mission(self):
        if self.phase != "paused":
            return result(False, "오류로 일시 정지한 임무만 재시도할 수 있습니다.")
        self._paused_seconds += self.clock() - self._paused_at
        self._paused_at = None
        self.data.update(missionPhase=self._resume_phase, clockRunning=True, error=None)
        if self._active_capture and self._resume_phase == "analyzing":
            self.data["captures"][-1]["status"] = "analyzing"
        self.touch()
        return result(True, "오류가 난 작업을 재시도합니다. 시계가 다시 흐릅니다.")

    def abort_mission(self):
        if self.phase in TERMINAL:
            return result(False, "이미 종료된 임무입니다.")
        self._frozen_ms = self.elapsed_ms()
        self.data.update(missionPhase="aborted", clockRunning=False, activeMonitorId=None,
                         error="임무가 중단되었습니다. 미확인 대상의 결과는 판정하지 않습니다.")
        self._active_capture = None
        self.touch()
        return result(True, self.data["error"])

    def get_state(self):
        timing = f"경과 시간 {self.elapsed_ms() / 1000:.1f}초. " if self._started is not None else "출발 전입니다. "
        return result(True, f"경로: {names(self.state.confirmedRoute or self.state.draftRoute)}. "
                      f"{timing}"
                      f"{self.data['error'] or ''}")

    def debrief(self):
        mode = "모의 분석" if self.data["mode"] == "mock" else "Azure 이미지 분석"
        if self.phase == "aborted":
            return f"{mode} 훈련을 중단했습니다. 미확인 대상의 결과는 판정하지 않았습니다."
        score = self.data["score"]
        if not score:
            return ""
        observations = []
        for person in self.data["people"]:
            if person["outcome"] != "too_late":
                continue
            frames = [frame for frame in self.data["captures"]
                      if frame["monitorId"] == person["monitorId"] and frame["evidence"] is not None]
            detail = "구조 시한 안에 유효한 이미지 확인을 마치지 못했습니다."
            if frames:
                evidence = frames[-1]["evidence"]
                detail = ("대상은 확인했지만 분석이 구조 시한 안에 끝나지 않았습니다."
                          if evidence["targetPresent"] else
                          f"시한 내 대상을 찾지 못했습니다. 마지막 이미지 관찰: {evidence['description']}")
            observations.append(f"{LABELS[person['monitorId']]}: {detail}")
        return (f"{mode} 훈련이 끝났습니다. 방문 경로: {names(self.state.confirmedRoute)}. "
                f"{score['total']}명 중 {score['rescuedCount']}명을 구조했고, "
                f"그중 {score['injuredCount']}명은 부상 상태입니다. "
                f"{score['tooLateCount']}명은 구조 시한을 넘겼습니다. "
                + " ".join(observations) +
                " 탐지 프롬프트, 이미지 확인, 방문 순서와 실제 이동·분석 시간이 결과에 반영되었습니다. 가상 훈련 결과입니다.")
