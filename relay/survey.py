"""Deterministic, relay-owned emergency-triage state and monotonic clock."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json
import math
import time
from uuid import uuid4

try:
    from .appearance import (fixture_prompt_constraints, prompt_confidence,
                             validate_constraints, validate_search_prompt)
except ImportError:
    from appearance import (fixture_prompt_constraints, prompt_confidence,
                            validate_constraints, validate_search_prompt)

try:
    from . import config
except ImportError:
    import config

SCENARIO = json.loads(config.SCENARIO_FILE.read_text("utf-8"))
MONITOR_IDS = [p["monitorId"] for p in SCENARIO["people"]]
LABELS = {person["monitorId"]: person["label"] for person in SCENARIO["people"]}
SITE_NAMES = {person["monitorId"]: person["siteName"] for person in SCENARIO["people"]}
TERMINAL = {"complete", "aborted"}
ACTIVE = {"flying", "capturing", "analyzing"}


def _normalize_name(text):
    import re
    return re.sub(r"[\W_]+", "", str(text)).lower()


def names(ids, labels=LABELS):
    return " → ".join(labels[mid] for mid in ids)


def result(ok, facts, ask="", silent=False):
    outcome = {"ok": ok, "facts": facts, "ask": ask}
    if silent:
        outcome["silent"] = True
    return outcome


def validate_evidence(evidence):
    base_keys = {"targetPresent", "description", "confidence", "box"}
    if (not isinstance(evidence, dict)
            or set(evidence) not in (base_keys, base_keys | {"violatorCount"},
                                     base_keys | {"boxes"}, base_keys | {"violatorCount", "boxes"})
            or type(evidence.get("targetPresent")) is not bool):
        raise ValueError("이미지 분석 결과의 대상 확인 값이 올바르지 않습니다.")
    description = evidence.get("description")
    confidence = evidence.get("confidence")
    box = evidence.get("box")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("이미지 분석 결과에 시각적 근거가 없습니다.")
    if type(confidence) is bool or not isinstance(confidence, int) or not 0 <= confidence <= 100:
        raise ValueError("이미지 분석 결과의 확신도(confidence)는 0~100 정수여야 합니다.")
    violator_count = evidence.get("violatorCount")
    if "violatorCount" in evidence and (
        type(violator_count) is bool or not isinstance(violator_count, int) or violator_count < 0
    ):
        raise ValueError("이미지 분석 결과의 위반자 인원수(violatorCount)는 0 이상 정수여야 합니다.")
    if box is not None:
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)):
            raise ValueError("이미지 분석 영역이 올바르지 않습니다.")
        x, y, w, h = box
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1 or y + h > 1:
            raise ValueError("이미지 분석 영역이 이미지 밖에 있습니다.")
        if not evidence["targetPresent"]:
            raise ValueError("대상이 없다는 분석 결과에 탐지 영역이 포함되어 있습니다.")
    result = {"targetPresent": evidence["targetPresent"],
             "description": description.strip(), "confidence": confidence,
             "box": list(box) if box is not None else None}
    if "violatorCount" in evidence:
        result["violatorCount"] = violator_count
    boxes = evidence.get("boxes")
    if "boxes" in evidence:
        if (not evidence["targetPresent"] or not isinstance(boxes, list) or not boxes
                or any(not isinstance(candidate, (list, tuple)) or len(candidate) != 4 for candidate in boxes)):
            raise ValueError("이미지 분석 탐지 영역 목록이 올바르지 않습니다.")
        result["boxes"] = [list(candidate) for candidate in boxes]
    return result


@dataclass
class RouteState:
    phase: str = "selecting-destinations"
    draftRoute: list[str] = field(default_factory=list)
    confirmedRoute: list[str] = field(default_factory=list)


class SurveySession:
    def __init__(self, *, clock=time.monotonic, mode="mock", drone_control_mode="mock", scenario=None,
                run_id=None, kind="triage"):
        if mode not in ("mock", "azure"):
            raise ValueError("지원하지 않는 이미지 분석 모드입니다.")
        if drone_control_mode not in ("mock", "live"):
            raise ValueError("지원하지 않는 드론 제어 모드입니다.")
        self.clock = clock
        self.kind = "triage"
        self.scenario = deepcopy(scenario or SCENARIO)
        self.monitor_ids = [p["monitorId"] for p in self.scenario["people"]]
        self.labels = {p["monitorId"]: p["label"] for p in self.scenario["people"]}
        self.site_names = {p["monitorId"]: p["siteName"] for p in self.scenario["people"]}
        self._monitor_by_name = {}
        for monitor_id in self.monitor_ids:
            for name in (self.labels[monitor_id], self.site_names[monitor_id]):
                self._monitor_by_name[_normalize_name(name)] = monitor_id
        self.state = RouteState()
        self.pending_prompt = None
        self.pending_prompt_revision = 0
        self.data = {
            "runId": run_id or str(uuid4()), "revision": 0, "missionPhase": "briefing",
            "kind": self.kind,
            "promptPhase": "briefing", "activePromptMonitorId": self.monitor_ids[0],
            "userPromptText": "",
            "appearanceConstraints": [], "unsupportedAppearance": [],
            "promptConfidence": None, "promptConfidenceReason": "",
            "mode": mode, "elapsedMs": 0, "clockRunning": False,
            "droneControlMode": drone_control_mode, "droneMissionId": None,
            "droneState": "idle", "droneStopState": "not_requested", "droneErrorCode": None,
            "activeVisitIndex": None,
            "activeMonitorId": None, "people": [], "captures": [], "score": None, "error": None,
        }
        for person in self.scenario["people"]:
            self.data["people"].append({
                key: deepcopy(person[key]) for key in
                ("id", "monitorId", "label", "clue", "siteName")
            } | {
                "falseAlarm": person.get("falseAlarm", False),
                "falseAlarmReveal": person.get("falseAlarmReveal", ""),
                "reportDetail": person.get("reportDetail", ""),
                "targetDescription": person["targetAppearance"]["description"],
                "outcome": None, "resolvedAtMs": None, "captureId": None, "attempts": 0,
                "promptConfirmed": False, "promptText": "", "appearanceConstraints": [],
                "unsupportedAppearance": [], "promptConfidence": None,
                "promptConfidenceReason": ""})
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

    def _prompt_values(self, prompt_text, appearance_constraints=None, unsupported_appearance=None):
        text = validate_search_prompt(prompt_text)
        constraints, unsupported = validate_constraints(
            [] if appearance_constraints is None else appearance_constraints,
            [] if unsupported_appearance is None else unsupported_appearance)
        if self.data["mode"] == "mock":
            fixture_prompt_constraints(text)
        return {"prompt_text": text, "appearance_constraints": constraints,
                "unsupported_appearance": unsupported}

    def prepare_prompt(self, prompt_text, appearance_constraints=None, unsupported_appearance=None):
        if not self._editable():
            return result(False, "출발한 임무의 탐색 프롬프트는 바꿀 수 없습니다.")
        try:
            values = self._prompt_values(prompt_text, appearance_constraints, unsupported_appearance)
        except ValueError as exc:
            return result(False, str(exc), "참가자가 실제로 말한 외형 조건만 다시 확인할 것")
        self.pending_prompt = values
        self.pending_prompt_revision += 1
        return result(True, f"확인 대기 중인 참가자 설명: {values['prompt_text']}",
                      "이 설명만 짧게 되말하고 확인 질문 뒤 새 답변을 기다릴 것")

    def confirm_prompt(self, prompt_text, appearance_constraints=None, unsupported_appearance=None):
        if not self._editable():
            return result(False, "출발한 임무의 탐색 프롬프트는 바꿀 수 없습니다.")
        if self.data["promptPhase"] == "confirmed":
            return result(False, "이미 세 장소 모두 대상자 설명을 확인했습니다.", silent=True)
        try:
            values = self._prompt_values(prompt_text, appearance_constraints, unsupported_appearance)
        except ValueError as exc:
            return result(False, str(exc), "참가자가 실제로 말한 외형 조건만 다시 확인할 것")
        text = values["prompt_text"]
        confidence, reasoning = prompt_confidence(
            values["appearance_constraints"], values["unsupported_appearance"])
        for person in self.data["people"]:
            person.update(promptConfirmed=True, promptText=text,
                         appearanceConstraints=values["appearance_constraints"],
                         unsupportedAppearance=values["unsupported_appearance"],
                         promptConfidence=confidence, promptConfidenceReason=reasoning)
        self.data.update(userPromptText=text,
                         appearanceConstraints=values["appearance_constraints"],
                         unsupportedAppearance=values["unsupported_appearance"],
                         promptConfidence=confidence, promptConfidenceReason=reasoning,
                         promptPhase="confirmed")
        self.pending_prompt = None
        self.touch()
        cases = " / ".join(
            f"{self.labels[p['monitorId']]}: {p['clue']}" for p in self.data["people"])
        outcome = result(True,
                      f"세 곳의 신고 내용을 모두 확인했습니다. 장소별 신고 내용: {cases}.",
                      "세 곳의 신고 내용을 짧게 설명한 뒤 참가자에게 어디로 가야 할지 한 곳만 물어볼 것")
        outcome["confidence"] = confidence
        outcome["confidenceReason"] = reasoning
        return outcome

    def resolve_monitor(self, value):
        if not isinstance(value, str):
            return None
        if value in self.monitor_ids:
            return value
        return self._monitor_by_name.get(_normalize_name(value))

    def select_stop(self, monitor):
        if self.data["promptPhase"] != "confirmed":
            return self._prompt_required()
        if not self._editable():
            return result(False, "출발한 임무의 목적지는 바꿀 수 없습니다.")
        monitor = self.resolve_monitor(monitor)
        if monitor is None:
            return result(False, "장소를 확인하지 못했습니다.", "어느 장소인지 다시 물어볼 것")
        if self.state.draftRoute:
            return result(False, "이미 확인할 장소를 정했습니다.", "이 장소로 갈지 확인하거나 수정 여부를 물어볼 것")
        self.state.draftRoute = [monitor]
        self.state.confirmedRoute = []
        self.state.phase = "awaiting-confirmation"
        self.data["missionPhase"] = "briefing"
        self.touch()
        return result(True, f"선택한 위치: {names(self.state.draftRoute, self.labels)}",
                      "confirm_route로 목적지를 확정한 뒤 곧바로 그 위치로 출발할 것")

    def clear_route(self):
        if not self._editable():
            return result(False, "출발 후에는 목적지를 지울 수 없습니다. 임무 중단을 이용하세요.")
        self.state = RouteState()
        self.data.update(missionPhase="briefing", error=None)
        self.touch()
        return result(True, "목적지를 지웠습니다.", "어디로 갈지 다시 한 곳만 물어볼 것")

    def confirm_route(self):
        if self.data["promptPhase"] != "confirmed":
            return self._prompt_required()
        if not self._editable() or len(self.state.draftRoute) != 1:
            return result(False, "출발 전에 확인할 목적지 한 곳을 먼저 정해야 합니다.")
        self.state.confirmedRoute = self.state.draftRoute[:]
        self.state.phase = "confirmed"
        self.data["missionPhase"] = "ready"
        self.touch()
        return result(True, f"목적지 확정: {names(self.state.confirmedRoute, self.labels)}.",
                      "곧바로 그 위치로 출발한다고 안내할 것")

    def launch_mission(self, readiness_error=None):
        if self.data["promptPhase"] != "confirmed":
            return self._prompt_required()
        if self._started is not None:
            return result(True, "이미 출발한 임무입니다. 중복 출발하지 않습니다.")
        if self.phase != "ready":
            return result(False, "목적지를 먼저 정해야 출발할 수 있습니다.")
        if readiness_error:
            self.data["error"] = readiness_error
            self.touch()
            return result(False, readiness_error)
        self._started = self.clock()
        self.data.update(missionPhase="flying", clockRunning=True, error=None)
        self.touch()
        return result(True, "출발했습니다. 이동과 촬영, 분석은 자동으로 진행됩니다.")

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
            "droneControlMode": self.data["droneControlMode"],
            "missionId": getattr(capture, "mission_id", None),
            "visitIndex": getattr(capture, "visit_index", None),
            "destinationId": getattr(capture, "destination_id", None),
            "capturedAtUnixMs": getattr(capture, "captured_at_unix_ms", None),
        })
        self.person(capture.monitor_id)["attempts"] += 1
        self.touch()
        return True

    def analyzing(self, capture_id, run_id):
        if run_id != self.run_id or self.phase not in ACTIVE or capture_id != self._active_capture:
            return False
        self.data["captures"][-1]["status"] = "analyzing"
        return self.set_operation("analyzing", self.data["activeMonitorId"], run_id)

    def _release_pending_capture(self):
        if self._active_capture:
            capture = self.data["captures"][-1]
            if (capture["id"] == self._active_capture and capture["status"] == "analyzing"
                    and capture["evidence"] is None):
                capture["status"] = "captured"
            self._active_capture = None

    def apply_detection(self, run_id, capture_id, evidence):
        if (run_id != self.run_id or self.phase != "analyzing"
                or capture_id != self._active_capture):
            return False
        evidence = validate_evidence(evidence)
        capture = self.data["captures"][-1]
        if capture["status"] != "analyzing":
            return False
        now = self.elapsed_ms()
        capture.update(evidence=evidence, status="detected" if evidence["targetPresent"] else "not-found")
        person = self.person(capture["monitorId"])
        max_attempts = self.scenario["maxDetectionAttempts"]
        if evidence["targetPresent"] and person["outcome"] is None and not person.get("falseAlarm"):
            person.update(outcome="reported", resolvedAtMs=now, captureId=capture_id)
        elif person["outcome"] is None and person.get("falseAlarm") and (
            evidence["targetPresent"] or person["attempts"] >= max_attempts
        ):
            person.update(outcome="report_missed", resolvedAtMs=now, captureId=capture_id)
        elif person["outcome"] is None and person["attempts"] >= max_attempts:
            person.update(outcome="report_missed", resolvedAtMs=now, captureId=capture_id)
        self._active_capture = None
        self.touch()
        self._finish_if_resolved()
        return True

    def unjudged_capture(self, run_id, capture_id, note):
        if (run_id != self.run_id or self.phase != "analyzing"
                or capture_id != self._active_capture):
            return False
        capture = self.data["captures"][-1]
        if capture["status"] != "analyzing" or capture["evidence"] is not None:
            return False
        capture["analysisNote"] = note
        self._release_pending_capture()
        self.touch()
        self.expire()
        return True

    def expire(self):
        return False

    def _finish_if_resolved(self):
        monitors = set(self.state.confirmedRoute)
        if not monitors:
            return
        people = [p for p in self.data["people"] if p["monitorId"] in monitors]
        if all(p["outcome"] is not None for p in people):
            self._frozen_ms = self.elapsed_ms()
            score = {
                "reportedCount": sum(p["outcome"] == "reported" for p in people),
                "reportMissedCount": sum(p["outcome"] == "report_missed" for p in people),
                "falseAlarmCount": sum(bool(p.get("falseAlarm")) for p in people),
                "total": len(people),
            }
            self.data.update(missionPhase="complete", clockRunning=False, activeMonitorId=None,
                             error=None, score=score)
            self._release_pending_capture()
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
        self._release_pending_capture()
        self.touch()
        return result(True, self.data["error"])

    def get_state(self):
        timing = f"경과 시간 {self.elapsed_ms() / 1000:.1f}초. " if self._started is not None else "출발 전입니다. "
        return result(True, f"목적지: {names(self.state.confirmedRoute or self.state.draftRoute, self.labels)}. "
                      f"{timing}"
                      f"{self.data['error'] or ''}")

    def debrief(self):
        return self._debrief_triage()

    def _debrief_triage(self):
        mode = "모의 분석" if self.data["mode"] == "mock" else "Azure 이미지 분석"
        if self.phase == "aborted":
            return f"이번 {mode} 훈련은 여기서 멈췄어. 찾는 사람을 구조했는지는 아직 알 수 없어."
        score = self.data["score"]
        if not score or not self.state.confirmedRoute:
            return ""
        person = self.person(self.state.confirmedRoute[0])
        capture = next((c for c in self.data["captures"] if c["id"] == person["captureId"]), None)
        details = []
        if person["outcome"] == "reported":
            summary = f"작전이 끝났어. {self.labels[person['monitorId']]}에서 대상자를 확인해서 일일구에 위치를 신고했어."
            if capture and capture.get("evidence"):
                evidence = capture["evidence"]
                details.append(
                    f"{evidence['description']} 확신도는 {evidence['confidence']}%였어.")
        else:
            if person.get("falseAlarm"):
                summary = f"작전이 끝났어. 우리가 확인한 {self.labels[person['monitorId']]}은(는) 실제 구조 신고가 아니었어."
            else:
                summary = f"작전이 끝났어. 우리가 확인한 {self.labels[person['monitorId']]}에서 대상자를 끝내 확인하지 못했어."
            if capture and capture.get("evidence"):
                evidence = capture["evidence"]
                details.append(
                    f"마지막으로 확인한 장면에서는 {evidence['description']} 확신도는 {evidence['confidence']}%였어.")
            if person.get("falseAlarm") and person.get("falseAlarmReveal"):
                details.append(person["falseAlarmReveal"])
        details.append(f"이건 {mode}으로 진행한 가상 훈련이야.")
        return "\n\n".join([summary, *details])
