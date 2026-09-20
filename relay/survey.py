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
SECURITY_SCENARIO = json.loads(config.SECURITY_SCENARIO_FILE.read_text("utf-8"))
CONSTRUCTION_SCENARIO = json.loads(config.CONSTRUCTION_SCENARIO_FILE.read_text("utf-8"))
# Registry of scenario kinds a session can be launched with (see
# SurveySession(kind=...) below and the `scenario` WS query param in
# server.py). All three scenarios share the same monitor ids (monitor-1/2/3)
# and people[] shape, so no other module-level global needs a per-kind
# variant; only the exact scenario document differs.
SCENARIOS_BY_KIND = {"triage": SCENARIO, "security": SECURITY_SCENARIO, "construction": CONSTRUCTION_SCENARIO}
TERMINAL = {"complete", "aborted"}
ACTIVE = {"flying", "capturing", "analyzing"}


def _normalize_name(text):
    import re
    return re.sub(r"[\W_]+", "", str(text)).lower()


def names(ids, labels=LABELS):
    return " → ".join(labels[mid] for mid in ids)


def site_question(monitor, site_names=SITE_NAMES):
    """The exact opening question read for one site, reused verbatim for
    every site (first one included in GREETING, later ones read after each
    confirm_prompt) so the participant always hears the same phrasing:
    '<site>에서 어떤 사람을 찾아줘야 하는지 말해줄 수 있어?'."""
    return f"{site_names[monitor]}에서 어떤 사람을 찾아줘야 하는지 말해줄 수 있어?"


def suggested_orders_security(people):
    """Two deterministic, relay-owned zone-check-order suggestions for the
    "security" scenario kind, mirroring suggested_orders() above but keyed
    off each zone's dramaticRank/clueRank instead of deadlineMs/vulnerable.

    Reuses the dangerOrder/vulnerableAdjustedOrder field names on the session
    snapshot (see confirm_prompt) so the frontend/type surface stays a single
    shape across both scenario kinds; only the meaning differs per kind:
    dangerOrder here holds the naive "dramatic-sounding zone first" order,
    vulnerableAdjustedOrder holds the "careful clue-analysis" order.
    """
    dramatic = sorted((p["monitorId"] for p in people),
                      key=lambda mid: next(p["dramaticRank"] for p in people if p["monitorId"] == mid))
    clue = sorted((p["monitorId"] for p in people),
                 key=lambda mid: next(p["clueRank"] for p in people if p["monitorId"] == mid))
    return {"dangerOrder": dramatic, "vulnerableAdjustedOrder": clue}


def suggested_orders_construction(people):
    """Two deterministic, relay-owned zone-check-order suggestions for the
    "construction" scenario kind, mirroring suggested_orders_security() but
    keyed off each zone's noticeableRank/carefulRank instead of
    dramaticRank/clueRank: noticeableOrder is "check whichever zone's pink
    workwear stands out most first", carefulOrder ranks by actual fall/
    injury risk of the zone's location (e.g. an open floor edge outranks a
    merely eye-catching walkway).
    """
    noticeable = sorted((p["monitorId"] for p in people),
                        key=lambda mid: next(p["noticeableRank"] for p in people if p["monitorId"] == mid))
    careful = sorted((p["monitorId"] for p in people),
                     key=lambda mid: next(p["carefulRank"] for p in people if p["monitorId"] == mid))
    return {"dangerOrder": noticeable, "vulnerableAdjustedOrder": careful}


def suggested_orders(people):
    """Two deterministic, relay-owned visit-order suggestions for the voice
    layer to read aloud verbatim (never invent an order itself).

    dangerOrder: pure current-visual-severity order (tightest deadline first).
    vulnerableAdjustedOrder: the same order, but each vulnerable/elderly
    person is moved one slot earlier, reflecting that raw urgency alone does
    not account for who is least able to help themselves while waiting.
    """
    danger = sorted((p["monitorId"] for p in people),
                    key=lambda mid: next(p["deadlineMs"] for p in people if p["monitorId"] == mid))
    adjusted = danger[:]
    vulnerable_ids = {p["monitorId"] for p in people if p.get("vulnerable")}
    for monitor in list(vulnerable_ids):
        index = adjusted.index(monitor)
        if index > 0:
            adjusted[index - 1], adjusted[index] = adjusted[index], adjusted[index - 1]
    return {"dangerOrder": danger, "vulnerableAdjustedOrder": adjusted}


def result(ok, facts, ask=""):
    return {"ok": ok, "facts": facts, "ask": ask}


def validate_evidence(evidence):
    if (not isinstance(evidence, dict)
            or set(evidence) != {"targetPresent", "description", "confidence", "box"}
            or type(evidence.get("targetPresent")) is not bool):
        raise ValueError("이미지 분석 결과의 대상 확인 값이 올바르지 않습니다.")
    description = evidence.get("description")
    confidence = evidence.get("confidence")
    box = evidence.get("box")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("이미지 분석 결과에 시각적 근거가 없습니다.")
    if type(confidence) is bool or not isinstance(confidence, int) or not 0 <= confidence <= 100:
        raise ValueError("이미지 분석 결과의 확신도(confidence)는 0~100 정수여야 합니다.")
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
            "description": description.strip(), "confidence": confidence,
            "box": list(box) if box is not None else None}


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
        if kind not in SCENARIOS_BY_KIND:
            raise ValueError("지원하지 않는 시나리오 종류입니다.")
        self.clock = clock
        self.kind = kind
        self.scenario = deepcopy(scenario or SCENARIOS_BY_KIND[kind])
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
            "kind": kind,
            "promptPhase": "briefing", "activePromptMonitorId": self.monitor_ids[0],
            "userPromptText": "",
            "appearanceConstraints": [], "unsupportedAppearance": [],
            "promptConfidence": None, "promptConfidenceReason": "",
            "mode": mode, "elapsedMs": 0, "clockRunning": False,
            "droneControlMode": drone_control_mode, "droneMissionId": None,
            "droneState": "idle", "droneStopState": "not_requested", "droneErrorCode": None,
            "activeVisitIndex": None,
            "activeMonitorId": None, "people": [], "captures": [], "score": None, "error": None,
            "dangerOrder": [], "vulnerableAdjustedOrder": [],
        }
        for person in self.scenario["people"]:
            extra = {}
            if kind == "security":
                extra = {"falseAlarm": person.get("falseAlarm", False),
                         "falseAlarmReveal": person.get("falseAlarmReveal", ""),
                         "dramaticRank": person["dramaticRank"], "clueRank": person["clueRank"]}
            elif kind == "construction":
                extra = {"noticeableRank": person["noticeableRank"], "carefulRank": person["carefulRank"],
                         "reportDetail": person.get("reportDetail", "")}
            self.data["people"].append({
                key: deepcopy(person[key]) for key in
                ("id", "monitorId", "label", "clue",
                 "initiallyInjured", "vulnerable", "deadlineMs")
            } | extra | {"targetDescription": person["targetAppearance"]["description"],
                 "deteriorationMs": 0 if person["initiallyInjured"] else
                 max(0, person["deadlineMs"] - self.scenario["injuryWindowMs"]),
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
            # Extra descriptors the mock vision engine has no ground truth for
            # (e.g. "모자", "안경") are kept in unsupported_appearance for
            # reference only — they no longer block confirmation. Only a
            # prompt with nothing usable at all for matching is rejected,
            # inside fixture_prompt_constraints below.
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
            return result(False, "이미 세 장소 모두 대상자 설명을 확인했습니다.")
        try:
            values = self._prompt_values(prompt_text, appearance_constraints, unsupported_appearance)
        except ValueError as exc:
            return result(False, str(exc), "참가자가 실제로 말한 외형 조건만 다시 확인할 것")
        text = values["prompt_text"]
        confidence, reasoning = prompt_confidence(
            values["appearance_constraints"], values["unsupported_appearance"])
        monitor = self.data["activePromptMonitorId"]
        person = self.person(monitor)
        person.update(promptConfirmed=True, promptText=text,
                      appearanceConstraints=values["appearance_constraints"],
                      unsupportedAppearance=values["unsupported_appearance"],
                      promptConfidence=confidence, promptConfidenceReason=reasoning)
        self.data.update(userPromptText=text,
                         appearanceConstraints=values["appearance_constraints"],
                         unsupportedAppearance=values["unsupported_appearance"],
                         promptConfidence=confidence, promptConfidenceReason=reasoning)
        self.pending_prompt = None
        self.touch()
        if self.kind in ("security", "construction"):
            # One suspect/one target description, described once; it applies
            # to every zone at once instead of advancing through a 3x
            # per-site loop.
            for other in self.data["people"]:
                if other["monitorId"] == monitor:
                    continue
                other.update(promptConfirmed=True, promptText=text,
                             appearanceConstraints=values["appearance_constraints"],
                             unsupportedAppearance=values["unsupported_appearance"],
                             promptConfidence=confidence, promptConfidenceReason=reasoning)
            self.data["promptPhase"] = "confirmed"
            cases = " / ".join(
                f"{self.labels[p['monitorId']]}: {p['clue']}" for p in self.data["people"])
            if self.kind == "construction":
                orders = suggested_orders_construction(self.data["people"])
                first_text = names(orders["dangerOrder"], self.labels)
                second_text = names(orders["vulnerableAdjustedOrder"], self.labels)
                self.data["dangerOrder"] = orders["dangerOrder"]
                self.data["vulnerableAdjustedOrder"] = orders["vulnerableAdjustedOrder"]
                outcome = result(True,
                              f"세 구역의 점검 내용을 모두 확인했습니다. 구역별 내용: {cases}. "
                              f"눈에 띄는 정도만 보면 확인 순서는 {first_text}. "
                              f"실제 위험도를 분석하면 추천 순서는 {second_text}.",
                              "먼저 세 구역의 점검 내용을 모두 설명하고, 이어서 두 추천 순서를 각각 설명한 뒤 "
                              "참가자에게 직접 어떤 순서로 확인하고 싶은지 물어볼 것")
                outcome["confidence"] = confidence
                outcome["confidenceReason"] = reasoning
                return outcome
            orders = suggested_orders_security(self.data["people"])
            dramatic_text = names(orders["dangerOrder"], self.labels)
            clue_text = names(orders["vulnerableAdjustedOrder"], self.labels)
            self.data["dangerOrder"] = orders["dangerOrder"]
            self.data["vulnerableAdjustedOrder"] = orders["vulnerableAdjustedOrder"]
            outcome = result(True,
                          f"세 구역의 경보 내용을 모두 확인했습니다. 구역별 경보 내용: {cases}. "
                          f"자극적인 상황만 보면 확인 순서는 {dramatic_text}. "
                          f"단서를 분석하면 추천 순서는 {clue_text}.",
                          "먼저 세 구역의 경보 내용을 모두 설명하고, 이어서 두 추천 순서를 각각 설명한 뒤 "
                          "참가자에게 직접 어떤 순서로 확인하고 싶은지 물어볼 것")
            outcome["confidence"] = confidence
            outcome["confidenceReason"] = reasoning
            return outcome
        remaining = [p for p in self.data["people"] if not p["promptConfirmed"]]
        if remaining:
            next_monitor = remaining[0]["monitorId"]
            self.data["activePromptMonitorId"] = next_monitor
            outcome = result(True,
                          f"{self.site_names[monitor]}에 있는 사람 설명 확인: {text}. "
                          f"이제 '{site_question(next_monitor, self.site_names)}'라고 물어볼 것",
                          f"다음으로 '{site_question(next_monitor, self.site_names)}'라고 그대로 물어볼 것")
        else:
            self.data["promptPhase"] = "confirmed"
            cases = " / ".join(
                f"{self.labels[p['monitorId']]}: {p['clue']}" for p in self.data["people"])
            orders = suggested_orders(self.data["people"])
            danger_text = names(orders["dangerOrder"], self.labels)
            adjusted_text = names(orders["vulnerableAdjustedOrder"], self.labels)
            self.data["dangerOrder"] = orders["dangerOrder"]
            self.data["vulnerableAdjustedOrder"] = orders["vulnerableAdjustedOrder"]
            outcome = result(True,
                          f"세 장소의 대상자 설명을 모두 확인했습니다. 장소별 신고 내용: {cases}. "
                          f"지금 위험도만 보면 추천 순서는 {danger_text}. "
                          f"노약자·거동 취약자를 고려하면 추천 순서는 {adjusted_text}.",
                          "먼저 세 장소의 신고 내용을 모두 설명하고, 이어서 두 추천 순서를 각각 설명한 뒤 "
                          "참가자에게 직접 어떤 순서로 신고하고 싶은지 물어볼 것")
        # Kept separate from facts/ask: the confidence explanation is spoken and
        # shown on the prompting page, before the case briefing above (which is
        # deferred to the route/map page via the relay's route-intro step).
        outcome["confidence"] = confidence
        outcome["confidenceReason"] = reasoning
        return outcome

    def resolve_monitor(self, value):
        """Accept either the canonical monitor id or the zone's spoken
        label/site name (the model sometimes passes the name it just said
        instead of the internal id, even when instructed otherwise) and
        return the matching monitor id, or None if it names no zone."""
        if not isinstance(value, str):
            return None
        if value in self.monitor_ids:
            return value
        return self._monitor_by_name.get(_normalize_name(value))

    def select_stop(self, monitor):
        if self.data["promptPhase"] != "confirmed":
            return self._prompt_required()
        if not self._editable():
            return result(False, "출발한 임무의 경로는 바꿀 수 없습니다.")
        monitor = self.resolve_monitor(monitor)
        if monitor is None:
            return result(False, "장소를 확인하지 못했습니다.", "어느 장소인지 다시 물어볼 것")
        if monitor in self.state.draftRoute:
            return result(False, "이미 경로에 있는 장소입니다.", "수정하려면 경로를 지울지 물어볼 것")
        self.state.draftRoute.append(monitor)
        self.state.confirmedRoute = []
        self.data["missionPhase"] = "briefing"
        if len(self.state.draftRoute) == 2:
            self.state.draftRoute += [m for m in self.monitor_ids if m not in self.state.draftRoute]
            self.state.phase = "awaiting-confirmation"
            ask = "confirm_route로 준비한 뒤 전체 경로를 읽고 출발 동의를 물어볼 것"
        else:
            self.state.phase = "selecting-order"
            ask = "두 번째로 갈 장소를 물어볼 것"
        self.touch()
        return result(True, f"선택 경로: {names(self.state.draftRoute, self.labels)}", ask)

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
        if not self._editable() or len(self.state.draftRoute) != len(self.monitor_ids):
            return result(False, "출발 전에 첫 번째와 두 번째 목적지를 정해야 합니다.")
        self.state.confirmedRoute = self.state.draftRoute[:]
        self.state.phase = "confirmed"
        self.data["missionPhase"] = "ready"
        self.touch()
        return result(True, f"준비된 경로: {names(self.state.confirmedRoute, self.labels)}. 아직 출발하지 않았습니다.",
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
        return result(True, "출발했습니다. 이동, 촬영, 분석 중에도 시간이 흐릅니다.")

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
                # A cancelled analysis leaves a captured image, not a fabricated detection or technical error.
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
        self.expire()
        if self.phase in TERMINAL:
            return False
        capture.update(evidence=evidence, status="detected" if evidence["targetPresent"] else "not-found")
        person = self.person(capture["monitorId"])
        if (evidence["targetPresent"] and person["outcome"] is None and now < person["deadlineMs"]
                and not person.get("falseAlarm")):
            if self.kind == "security":
                person.update(outcome="caught", resolvedAtMs=now, captureId=capture_id)
            elif self.kind == "construction":
                person.update(outcome="reported", resolvedAtMs=now, captureId=capture_id)
            else:
                injured = person["initiallyInjured"] or now >= person["deteriorationMs"]
                person.update(outcome="reported_injured" if injured else "reported",
                              resolvedAtMs=now, captureId=capture_id)
        elif (self.kind == "construction" and not evidence["targetPresent"] and person["outcome"] is None
              and person["attempts"] >= self.scenario["maxDetectionAttempts"]):
            # No deadline pressure in this scenario, so a zone that ran out of
            # detection attempts without a match resolves as genuinely
            # checked-but-empty rather than staying unresolved forever.
            person.update(outcome="not_found", resolvedAtMs=now, captureId=capture_id)
        self._active_capture = None
        self.touch()
        self._finish_if_resolved()
        return True

    def unjudged_capture(self, run_id, capture_id, note):
        """Release an analysis that reached no verdict, inventing neither outcome.

        A capture nobody could judge is still a real photograph. Recording it as
        "not found" would claim the person is absent and recording it as found
        would claim the opposite; both are fabrications. The image stays captured
        with the reason attached, the person keeps no outcome, and their report
        deadline keeps running exactly as it would have.
        """
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
        if self.phase not in ACTIVE:
            return False
        now = self.elapsed_ms()
        changed = False
        missed_outcome = ("escaped" if self.kind == "security"
                         else "unchecked" if self.kind == "construction" else "report_missed")
        for person in self.data["people"]:
            if person["outcome"] is None and now >= person["deadlineMs"]:
                person.update(outcome=missed_outcome, resolvedAtMs=person["deadlineMs"])
                changed = True
        if changed:
            self.touch()
            self._finish_if_resolved()
        return changed

    def _finish_if_resolved(self):
        people = self.data["people"]
        if all(p["outcome"] is not None for p in people):
            self._frozen_ms = self.elapsed_ms()
            if self.kind == "security":
                score = {
                    "caughtCount": sum(p["outcome"] == "caught" for p in people),
                    "escapedCount": sum(p["outcome"] == "escaped" and not p.get("falseAlarm") for p in people),
                    "falseAlarmCount": sum(bool(p.get("falseAlarm")) for p in people),
                    "total": len(people),
                }
            elif self.kind == "construction":
                score = {
                    "violationsReportedCount": sum(p["outcome"] == "reported" for p in people),
                    "notFoundCount": sum(p["outcome"] == "not_found" for p in people),
                    "uncheckedCount": sum(p["outcome"] == "unchecked" for p in people),
                    "total": len(people),
                }
            else:
                score = {
                    "reportedCount": sum(p["outcome"] in ("reported", "reported_injured") for p in people),
                    "injuredCount": sum(p["outcome"] == "reported_injured" for p in people),
                    "reportMissedCount": sum(p["outcome"] == "report_missed" for p in people),
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
        return result(True, f"경로: {names(self.state.confirmedRoute or self.state.draftRoute, self.labels)}. "
                      f"{timing}"
                      f"{self.data['error'] or ''}")

    def debrief(self):
        if self.kind == "security":
            return self._debrief_security()
        if self.kind == "construction":
            return self._debrief_construction()
        mode = "모의 분석" if self.data["mode"] == "mock" else "Azure 이미지 분석"
        if self.phase == "aborted":
            return f"이번 {mode} 훈련은 여기서 멈췄어. 아직 확인하지 못한 사람들의 신고 결과는 알 수 없어."
        score = self.data["score"]
        if not score:
            return ""
        observations = []
        confidence_notes = []
        for person in self.data["people"]:
            capture = next((c for c in self.data["captures"] if c["id"] == person["captureId"]), None)
            if person["outcome"] in ("reported", "reported_injured") and capture and capture["evidence"]:
                confidence_notes.append(
                    f"{self.labels[person['monitorId']]}: 확신도 {capture['evidence']['confidence']}%로 위치를 119에 신고했어.")
            if person["outcome"] != "report_missed":
                continue
            frames = [frame for frame in self.data["captures"]
                      if frame["monitorId"] == person["monitorId"] and frame["evidence"] is not None]
            detail = "119에 신고할 수 있는 시간 안에 이미지 확인을 마치지 못했어."
            if frames:
                evidence = frames[-1]["evidence"]
                detail = ("찾는 사람은 확인했지만, 이미지 분석이 신고 시한 안에 끝나지 않았어."
                          if evidence["targetPresent"] else
                          f"시간 안에 찾는 사람을 확인하지 못했어. 마지막 사진의 관찰 내용은 이거야.\n"
                          f"\"{evidence['description']}\"")
            observations.append(f"{self.labels[person['monitorId']]}: {detail}")
        summary = ["작전이 끝났어."]
        if score["reportedCount"]:
            summary.append(f"우리 함께 {score['total']}명 중 {score['reportedCount']}명의 위치를 119에 제때 신고했어.")
            summary.append(f"그중 {score['injuredCount']}명은 부상이 확인돼서 119에도 함께 전달했어."
                           if score["injuredCount"] else "신고한 사람 중 다친 사람은 없어.")
        else:
            summary.append(f"이번에는 {score['total']}명 중 아무도 119에 제때 신고하지 못했어.")
        summary.append(f"{score['reportMissedCount']}명은 신고할 수 있는 시간을 넘겼어."
                       if score["reportMissedCount"] else "신고 시한은 모두 지켰어.")
        return "\n\n".join([
            " ".join(summary),
            f"우리가 고른 순서는 {names(self.state.confirmedRoute, self.labels)}였어.",
            *confidence_notes,
            *observations,
            "어떤 모습을 찾을지, 어디부터 갈지, 이동하고 사진을 확인하는 데 얼마나 걸렸는지가 결과에 반영됐어. "
            f"이건 {mode}으로 진행한 가상 훈련이야.",
        ])

    def _debrief_security(self):
        mode = "모의 분석" if self.data["mode"] == "mock" else "Azure 이미지 분석"
        if self.phase == "aborted":
            return f"이번 {mode} 훈련은 여기서 멈췄어. 진짜 침입자를 확인했는지는 아직 알 수 없어."
        score = self.data["score"]
        if not score:
            return ""
        reveals = []
        confidence_notes = []
        real_zone = next((p for p in self.data["people"] if not p.get("falseAlarm")), None)
        for person in self.data["people"]:
            capture = next((c for c in self.data["captures"] if c["id"] == person["captureId"]), None)
            if person["outcome"] == "caught" and capture and capture["evidence"]:
                confidence_notes.append(
                    f"{self.labels[person['monitorId']]}: 확신도 {capture['evidence']['confidence']}%로 위치를 112(경찰 신고 대표번호)에 신고했어.")
            if person.get("falseAlarm"):
                reveals.append(f"{self.labels[person['monitorId']]}: {person.get('falseAlarmReveal', '오경보였어.')}")
        summary = ["작전이 끝났어."]
        if real_zone and real_zone["outcome"] == "caught":
            summary.append(f"진짜 침입자가 있던 {self.labels[real_zone['monitorId']]}에서 시간 안에 위치를 112(경찰 신고 대표번호)에 전달했어.")
        elif real_zone and real_zone["outcome"] == "escaped":
            summary.append(f"진짜 침입자가 있던 {self.labels[real_zone['monitorId']]}을(를) 시간 안에 확인하지 못해서 침입자를 놓쳤어.")
        else:
            summary.append("진짜 침입자가 있던 구역의 결과가 아직 확실하지 않아.")
        return "\n\n".join([
            " ".join(summary),
            f"우리가 고른 확인 순서는 {names(self.state.confirmedRoute, self.labels)}였어.",
            *confidence_notes,
            *reveals,
            "어떤 순서로 구역을 확인할지, 이동하고 사진을 확인하는 데 얼마나 걸렸는지가 결과에 반영됐어. "
            f"이건 {mode}으로 진행한 가상 훈련이야.",
        ])

    def _debrief_construction(self):
        mode = "모의 분석" if self.data["mode"] == "mock" else "Azure 이미지 분석"
        if self.phase == "aborted":
            return f"이번 {mode} 훈련은 여기서 멈췄어. 아직 확인하지 못한 구역의 결과는 알 수 없어."
        score = self.data["score"]
        if not score:
            return ""
        confidence_notes = []
        observations = []
        for person in self.data["people"]:
            capture = next((c for c in self.data["captures"] if c["id"] == person["captureId"]), None)
            if person["outcome"] == "reported" and capture and capture["evidence"]:
                confidence_notes.append(
                    f"{self.labels[person['monitorId']]}: 확신도 {capture['evidence']['confidence']}%로 "
                    f"{person.get('reportDetail', '위반 사항')}을(를) 현장 안전관리자에게 전달했어.")
            elif person["outcome"] == "not_found":
                observations.append(f"{self.labels[person['monitorId']]}: 확인했지만 안전모 미착용자를 찾지 못했어.")
            elif person["outcome"] == "unchecked":
                observations.append(f"{self.labels[person['monitorId']]}: 확인하지 못한 구역이야.")
        summary = ["점검이 끝났어."]
        if score["violationsReportedCount"]:
            summary.append(f"세 구역 중 {score['violationsReportedCount']}곳에서 안전모 미착용자를 찾아 "
                           "현장 안전관리자에게 전달했어.")
        else:
            summary.append("이번에는 안전모 미착용자를 찾아 전달하지 못했어.")
        if score["uncheckedCount"]:
            summary.append(f"{score['uncheckedCount']}곳은 확인하지 못했어.")
        return "\n\n".join([
            " ".join(summary),
            f"우리가 고른 확인 순서는 {names(self.state.confirmedRoute, self.labels)}였어.",
            *confidence_notes,
            *observations,
            "어떤 순서로 구역을 확인할지, 이동하고 사진을 확인하는 데 얼마나 걸렸는지가 결과에 반영됐어. "
            f"이건 {mode}으로 진행한 가상 훈련이야.",
        ])
