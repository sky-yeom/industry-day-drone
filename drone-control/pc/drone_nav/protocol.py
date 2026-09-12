"""Versioned newline-delimited JSON protocol and TCP transport."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import socket
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .controller import Velocity

MESSAGE_TYPES = {
    "arm", "takeoff", "land", "heartbeat", "velocity", "attitude", "zero", "gimbal",
    "status", "disarm", "emergency_stop", "stick_mode", "obstacle_avoidance",
    "ack",
}

# DJI enable-virtual-stick / takeoff / landing complete asynchronously and can
# take several seconds before Android acknowledges.
SLOW_COMMAND_TIMEOUT_S = 20.0


def _validate_payload(kind: str, payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    expected = {
        "arm": {"confirmation_token"},
        "takeoff": {"confirmation_token"},
        "land": {"confirmation_token"},
        "heartbeat": set(),
        "velocity": {"forward_mps", "right_mps", "up_mps", "yaw_rate_rps"},
        "attitude": {"forward_tilt_deg", "right_tilt_deg", "up_mps", "yaw_rate_rps"},
        "zero": set(),
        "gimbal": {"pitch_deg"},
        "status": {"state"},
        "disarm": set(),
        "emergency_stop": set(),
        "stick_mode": {"mode"},
        "obstacle_avoidance": set(),
        "ack": {"ok", "detail"},
    }[kind]
    supplied = set(payload)
    if kind == "ack":
        # Android attaches optional semantic and telemetry data to every ACK.
        optional = {"telemetry", "result_schema_version", "status", "message_ko"}
        if supplied - optional != expected:
            raise ValueError("invalid ack payload fields")
        if "telemetry" in payload and not isinstance(payload["telemetry"], dict):
            raise ValueError("ack telemetry must be an object")
        if "result_schema_version" in payload and (
            isinstance(payload["result_schema_version"], bool)
            or not isinstance(payload["result_schema_version"], int)
        ):
            raise ValueError("ack result_schema_version must be an integer")
        for name in ("status", "message_ko"):
            if name in payload and not isinstance(payload[name], str):
                raise ValueError(f"ack {name} must be a string")
    elif supplied != expected:
        raise ValueError(f"invalid {kind} payload fields")
    if kind in {"arm", "takeoff", "land"} and (
        not isinstance(payload["confirmation_token"], str)
        or not payload["confirmation_token"]
    ):
        raise ValueError("confirmation_token must be a non-empty string")
    if kind == "status" and (
        not isinstance(payload["state"], str) or not payload["state"]
    ):
        raise ValueError("state must be a non-empty string")
    if kind == "ack" and (
        not isinstance(payload["ok"], bool)
        or not isinstance(payload["detail"], str)
    ):
        raise ValueError("invalid ack payload")
    if kind in {"velocity", "attitude", "gimbal"}:
        for name in (
            ("forward_mps", "right_mps", "up_mps", "yaw_rate_rps")
            if kind == "velocity"
            else (
                ("forward_tilt_deg", "right_tilt_deg", "up_mps", "yaw_rate_rps")
                if kind == "attitude"
                else ("pitch_deg",)
            )
        ):
            value = payload[name]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{kind} values must be finite numbers")
    if kind == "gimbal" and not (-90.0 <= payload["pitch_deg"] <= 35.0):
        raise ValueError("gimbal pitch must be within [-90, 35] degrees")
    if kind == "attitude" and (
        abs(payload["forward_tilt_deg"]) > 5.0
        or abs(payload["right_tilt_deg"]) > 5.0
    ):
        raise ValueError("attitude tilt must be within [-5, 5] degrees")


@dataclass(frozen=True)
class Message:
    version: int
    sequence: int
    timestamp_ns: int
    type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ControlResult:
    """Semantic result intended for tools and natural-language agents."""

    schema_version: int | None
    status: str
    message_ko: str
    detail: str

    @classmethod
    def from_ack_payload(cls, payload: dict[str, Any]) -> "ControlResult":
        schema = payload.get("result_schema_version")
        return cls(
            schema_version=(schema if isinstance(schema, int) and not isinstance(schema, bool) else None),
            status=str(payload.get("status") or "LEGACY_ACK"),
            message_ko=str(payload.get("message_ko") or payload.get("detail") or ""),
            detail=str(payload.get("detail") or ""),
        )


@dataclass(frozen=True)
class MotionAssessment:
    """Human-readable verdict derived from a correlated telemetry snapshot.

    This deliberately distinguishes "Android submitted the setpoint" from
    "the aircraft moved".  The official Advanced manager API has no per-frame
    completion callback, so telemetry is the only flight-controller evidence.
    """

    status: str
    message_ko: str
    command_sequence: int | None
    evidence: dict[str, Any]


@dataclass(frozen=True)
class Telemetry:
    """Flight telemetry parsed from an ACK. Absent values are None."""

    velocity_north_mps: float | None = None
    velocity_east_mps: float | None = None
    velocity_down_mps: float | None = None
    velocity_age_s: float | None = None
    yaw_deg: float | None = None
    pitch_deg: float | None = None
    roll_deg: float | None = None
    attitude_age_s: float | None = None
    height_m: float | None = None
    height_age_s: float | None = None
    is_flying: bool | None = None
    is_flying_age_s: float | None = None
    are_motors_on: bool | None = None
    are_motors_on_age_s: float | None = None
    bridge_health: dict[str, Any] | None = None
    fc_health: dict[str, Any] | None = None
    oa_diagnostics: dict[str, Any] | None = None
    video: dict[str, Any] | None = None
    telemetry_started: bool | None = None
    telemetry_generation: int | None = None
    telemetry_poll_failures: int | None = None
    telemetry_expired_gets: int | None = None
    sdk_reads_pending: int | None = None
    telemetry_last_error: str | None = None
    telemetry_last_error_age_s: float | None = None
    parse_issues: tuple[str, ...] = ()
    armed: bool | None = None
    battery_percent: float | None = None
    rc_override_age_s: float | None = None
    rc_override_threshold: float | None = None
    rc_stick_left_vertical: float | None = None
    rc_stick_left_horizontal: float | None = None
    rc_stick_right_vertical: float | None = None
    rc_stick_right_horizontal: float | None = None
    # DJI's own view of Virtual Stick. Without these, "the flight controller
    # ignored our commands" is indistinguishable from "all good": the bridge
    # ACKs either way.
    vs_enabled: bool | None = None
    vs_advanced_enabled: bool | None = None
    vs_authority: str | None = None
    vs_change_reason: str | None = None
    flight_mode: str | None = None
    flight_mode_age_s: float | None = None
    stick_mode: str | None = None
    control_path: str | None = None
    speed_level: float | None = None
    oa_type: str | None = None
    oa_sensors_working: str | None = None
    oa_horizontal_switch_support: str | None = None
    oa_upward_switch_support: str | None = None
    oa_horizontal_enabled: bool | None = None
    oa_upward_enabled: bool | None = None
    oa_downward_enabled: bool | None = None
    vision_positioning_enabled: bool | None = None
    oa_horizontal_angle_interval_deg: float | None = None
    oa_horizontal_distances_mm: tuple[int, ...] | None = None
    oa_horizontal_sample_count: int | None = None
    oa_upward_distance_mm: float | None = None
    oa_downward_distance_mm: float | None = None
    oa_obstacle_data_age_s: float | None = None
    time_watchdog_enabled: bool | None = None
    disconnect_release_enabled: bool | None = None
    direct_frames_sent: int | None = None
    direct_frames_succeeded: int | None = None
    direct_frames_failed: int | None = None
    direct_consecutive_failures: int | None = None
    direct_callback_age_s: float | None = None
    direct_last_error: str | None = None
    official_advanced_frames_sent: int | None = None
    official_advanced_frame_age_s: float | None = None
    active_command_sequence: int | None = None
    active_command_age_s: float | None = None
    requested_forward_mps: float | None = None
    requested_right_mps: float | None = None
    requested_up_mps: float | None = None
    requested_yaw_rate_dps: float | None = None
    setpoint_forward_mps: float | None = None
    setpoint_right_mps: float | None = None
    setpoint_up_mps: float | None = None
    setpoint_yaw_rate_dps: float | None = None
    requested_forward_tilt_deg: float | None = None
    requested_right_tilt_deg: float | None = None
    setpoint_forward_tilt_deg: float | None = None
    setpoint_right_tilt_deg: float | None = None
    sdk_roll: float | None = None
    sdk_pitch: float | None = None
    sdk_roll_pitch_units: str | None = None
    sdk_roll_pitch_mode: str | None = None
    sdk_submit_result: str | None = None
    sdk_submit_sequence: int | None = None
    sdk_submit_frame: int | None = None
    sdk_submit_age_s: float | None = None
    sdk_result: str | None = None
    sdk_result_sequence: int | None = None
    sdk_result_frame: int | None = None
    sdk_result_age_s: float | None = None
    sdk_result_error: str | None = None
    bridge_build_id: str | None = None
    max_tilt_angle_deg: float | None = None

    @classmethod
    def from_ack_payload(cls, payload: dict[str, Any]) -> "Telemetry | None":
        raw = payload.get("telemetry")
        if not isinstance(raw, dict) or not raw:
            return None
        issues = []

        def number(name: str, scale: float = 1.0) -> float | None:
            value = raw.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            if not math.isfinite(value):
                return None
            if name.endswith("_age_ms") and value < 0:
                return None
            return float(value) * scale

        def integer(name):
            value = raw.get(name)
            return value if type(value) is int and value >= 0 else None

        def diagnostics(name):
            value = raw.get(name)
            if not isinstance(value, dict):
                return None
            def clean(item, key=""):
                if isinstance(item, dict):
                    return {k: clean(v, k) for k, v in item.items() if isinstance(k, str)}
                if isinstance(item, list):
                    return [clean(v) for v in item]
                if key.endswith("_age_ms") or key == "age_ms":
                    return item if type(item) in (int, float) and math.isfinite(item) and item >= 0 else None
                if key in {"generation", "connection_generation", "callback_sequence", "consecutive_failures"}:
                    return item if type(item) is int and item >= 0 else None
                if isinstance(item, float) and not math.isfinite(item):
                    return None
                return deepcopy(item)
            return clean(value)

        def boolean(name: str) -> bool | None:
            value = raw.get(name)
            return value if isinstance(value, bool) else None

        def text(name: str) -> str | None:
            value = raw.get(name)
            return value if isinstance(value, str) and value else None

        def integer_tuple(name: str) -> tuple[int, ...] | None:
            value = raw.get(name)
            if not isinstance(value, list):
                if value is not None:
                    issues.append(name + ": expected integer array")
                return None
            parsed: list[int] = []
            for item in value:
                if isinstance(item, bool) or not isinstance(item, int):
                    issues.append(name + ": invalid element; whole array unavailable")
                    return None
                parsed.append(item)
            return tuple(parsed)

        return cls(
            velocity_north_mps=number("velocity_north_mps"),
            velocity_east_mps=number("velocity_east_mps"),
            velocity_down_mps=number("velocity_down_mps"),
            velocity_age_s=number("velocity_age_ms", 1e-3),
            yaw_deg=number("yaw_deg"),
            pitch_deg=number("pitch_deg"),
            roll_deg=number("roll_deg"),
            attitude_age_s=number("attitude_age_ms", 1e-3),
            height_m=number("height_m"),
            height_age_s=number("height_age_ms", 1e-3),
            is_flying=boolean("is_flying"),
            is_flying_age_s=number("is_flying_age_ms", 1e-3),
            are_motors_on=boolean("are_motors_on"),
            are_motors_on_age_s=number("are_motors_on_age_ms", 1e-3),
            bridge_health=diagnostics("bridge_health"), fc_health=diagnostics("fc_health"),
            oa_diagnostics=diagnostics("oa_diagnostics"), video=diagnostics("video"),
            telemetry_started=boolean("telemetry_started"), telemetry_generation=integer("telemetry_generation"),
            telemetry_poll_failures=integer("telemetry_poll_failures"),
            telemetry_expired_gets=integer("telemetry_expired_gets"), sdk_reads_pending=integer("sdk_reads_pending"),
            telemetry_last_error=text("telemetry_last_error"),
            telemetry_last_error_age_s=number("telemetry_last_error_age_ms", 1e-3),
            armed=boolean("armed"),
            battery_percent=number("battery_percent"),
            rc_override_age_s=number("rc_override_age_ms", 1e-3),
            rc_override_threshold=number("rc_override_threshold"),
            rc_stick_left_vertical=number("rc_stick_left_vertical"),
            rc_stick_left_horizontal=number("rc_stick_left_horizontal"),
            rc_stick_right_vertical=number("rc_stick_right_vertical"),
            rc_stick_right_horizontal=number("rc_stick_right_horizontal"),
            vs_enabled=boolean("vs_enabled"),
            vs_advanced_enabled=boolean("vs_advanced_enabled"),
            vs_authority=text("vs_authority"),
            vs_change_reason=text("vs_change_reason"),
            flight_mode=text("flight_mode"),
            flight_mode_age_s=number("flight_mode_age_ms", 1e-3),
            stick_mode=text("stick_mode"),
            control_path=text("control_path"),
            speed_level=number("speed_level"),
            oa_type=text("oa_type"),
            oa_horizontal_switch_support=text(
                "oa_horizontal_switch_support"
            ),
            oa_upward_switch_support=text("oa_upward_switch_support"),
            oa_horizontal_enabled=boolean("oa_horizontal_enabled"),
            oa_upward_enabled=boolean("oa_upward_enabled"),
            oa_downward_enabled=boolean("oa_downward_enabled"),
            vision_positioning_enabled=boolean("vision_positioning_enabled"),
            oa_horizontal_angle_interval_deg=number(
                "oa_horizontal_angle_interval_deg"
            ),
            oa_horizontal_distances_mm=integer_tuple(
                "oa_horizontal_distances_mm"
            ),
            oa_horizontal_sample_count=integer("oa_horizontal_sample_count"),
            oa_upward_distance_mm=number("oa_upward_distance_mm"),
            oa_downward_distance_mm=number("oa_downward_distance_mm"),
            oa_obstacle_data_age_s=number("oa_obstacle_data_age_ms", 1e-3),
            time_watchdog_enabled=boolean("time_watchdog_enabled"),
            disconnect_release_enabled=boolean("disconnect_release_enabled"),
            direct_frames_sent=integer("direct_frames_sent"),
            direct_frames_succeeded=integer("direct_frames_succeeded"),
            direct_frames_failed=integer("direct_frames_failed"),
            direct_consecutive_failures=integer("direct_consecutive_failures"),
            direct_callback_age_s=number("direct_callback_age_ms", 1e-3),
            direct_last_error=text("direct_last_error"),
            official_advanced_frames_sent=integer("official_advanced_frames_sent"),
            official_advanced_frame_age_s=number(
                "official_advanced_frame_age_ms", 1e-3
            ),
            active_command_sequence=integer("active_command_sequence"),
            active_command_age_s=number("active_command_age_ms", 1e-3),
            requested_forward_mps=number("requested_forward_mps"),
            requested_right_mps=number("requested_right_mps"),
            requested_up_mps=number("requested_up_mps"),
            requested_yaw_rate_dps=number("requested_yaw_rate_dps"),
            setpoint_forward_mps=number("setpoint_forward_mps"),
            setpoint_right_mps=number("setpoint_right_mps"),
            setpoint_up_mps=number("setpoint_up_mps"),
            setpoint_yaw_rate_dps=number("setpoint_yaw_rate_dps"),
            requested_forward_tilt_deg=number("requested_forward_tilt_deg"),
            requested_right_tilt_deg=number("requested_right_tilt_deg"),
            setpoint_forward_tilt_deg=number("setpoint_forward_tilt_deg"),
            setpoint_right_tilt_deg=number("setpoint_right_tilt_deg"),
            sdk_roll=number("sdk_roll"),
            sdk_pitch=number("sdk_pitch"),
            sdk_roll_pitch_units=text("sdk_roll_pitch_units"),
            sdk_roll_pitch_mode=text("sdk_roll_pitch_mode"),
            sdk_submit_result=text("sdk_submit_result"),
            sdk_submit_sequence=integer("sdk_submit_sequence"),
            sdk_submit_frame=integer("sdk_submit_frame"),
            sdk_submit_age_s=number("sdk_submit_age_ms", 1e-3),
            sdk_result=text("sdk_result"),
            sdk_result_sequence=integer("sdk_result_sequence"),
            sdk_result_frame=integer("sdk_result_frame"),
            sdk_result_age_s=number("sdk_result_age_ms", 1e-3),
            sdk_result_error=text("sdk_result_error"),
            bridge_build_id=text("bridge_build_id"),
            max_tilt_angle_deg=number("max_tilt_angle_deg"),
            # An empty string is meaningful here: no directional sensor
            # currently reports working. Keep it instead of using text().
            oa_sensors_working=(
                raw.get("oa_sensors_working")
                if isinstance(raw.get("oa_sensors_working"), str)
                else None
            ),
            parse_issues=tuple(issues),
        )


def assess_motion(
    telemetry: Telemetry | None,
    *,
    command_age_s: float | None = None,
    command_sequence: int | None = None,
) -> MotionAssessment:
    """Turn one ACK telemetry snapshot into a semantic motion verdict."""

    if telemetry is None or telemetry.active_command_sequence is None:
        return MotionAssessment(
            "NO_ACTIVE_COMMAND",
            "평가할 활성 이동 명령이 없습니다.",
            None,
            {},
        )

    command_sequence = (
        int(telemetry.active_command_sequence)
        if command_sequence is None
        else command_sequence
    )
    command_age_s = (
        telemetry.active_command_age_s
        if command_age_s is None
        else command_age_s
    )
    angle_mode = telemetry.sdk_roll_pitch_mode == "ANGLE"
    requested = (
        (telemetry.setpoint_forward_tilt_deg if angle_mode else telemetry.setpoint_forward_mps)
        or 0.0,
        (telemetry.setpoint_right_tilt_deg if angle_mode else telemetry.setpoint_right_mps)
        or 0.0,
        telemetry.setpoint_up_mps or 0.0,
    )
    evidence: dict[str, Any] = {
        "command_age_s": command_age_s,
        "setpoint_forward_mps": telemetry.setpoint_forward_mps,
        "setpoint_right_mps": telemetry.setpoint_right_mps,
        "setpoint_up_mps": requested[2],
        "setpoint_forward_tilt_deg": telemetry.setpoint_forward_tilt_deg,
        "setpoint_right_tilt_deg": telemetry.setpoint_right_tilt_deg,
        "sdk_roll": telemetry.sdk_roll,
        "sdk_pitch": telemetry.sdk_pitch,
        "sdk_roll_pitch_mode": telemetry.sdk_roll_pitch_mode,
        "sdk_submit_result": telemetry.sdk_submit_result,
        "sdk_result": telemetry.sdk_result,
        "sdk_result_error": telemetry.sdk_result_error,
        "flight_mode": telemetry.flight_mode,
        "vs_enabled": telemetry.vs_enabled,
        "vs_advanced_enabled": telemetry.vs_advanced_enabled,
        "vs_authority": telemetry.vs_authority,
    }

    result_is_current = (
        telemetry.sdk_result_sequence is not None
        and telemetry.sdk_result_sequence == telemetry.active_command_sequence
        and telemetry.sdk_result_age_s is not None
        and 0 <= telemetry.sdk_result_age_s <= 0.5
    )
    evidence["sdk_result_matches_active_command"] = result_is_current
    if telemetry.sdk_result == "FAILED" and result_is_current:
        return MotionAssessment(
            "SDK_COMMAND_FAILED",
            f"DJI 직접 전송이 실패했습니다: {telemetry.sdk_result_error or '원인 미상'}",
            command_sequence,
            evidence,
        )

    if telemetry.vs_authority not in (None, "MSDK") or telemetry.armed is False:
        return MotionAssessment("AUTHORITY_LOST", "앱의 비행 조종권이 없습니다.", command_sequence, evidence)

    if max(abs(component) for component in requested) < 0.02:
        if abs(telemetry.setpoint_yaw_rate_dps or 0.0) >= 0.02:
            return MotionAssessment("YAW_OBSERVATION_REQUIRED", "회전 명령입니다. 연속 yaw 관측으로 회전 여부를 판단해야 합니다.", command_sequence, evidence)
        return MotionAssessment(
            "SETPOINT_ZERO",
            "활성 목표 속도가 0이므로 정지 명령 상태입니다.",
            command_sequence,
            evidence,
        )

    if command_age_s is None or command_age_s < 0.6:
        return MotionAssessment(
            "WAITING_FOR_MOTION",
            "속도 명령이 제출되었으며 기체 반응을 기다리는 중입니다.",
            command_sequence,
            evidence,
        )

    velocity_is_fresh = (
        telemetry.velocity_age_s is not None
        and telemetry.velocity_age_s <= 0.5
        and telemetry.velocity_north_mps is not None
        and telemetry.velocity_east_mps is not None
        and telemetry.velocity_down_mps is not None
    )
    attitude_is_fresh = (
        telemetry.attitude_age_s is not None
        and telemetry.attitude_age_s <= 0.5
        and telemetry.yaw_deg is not None
    )
    if not velocity_is_fresh or not attitude_is_fresh:
        evidence["velocity_age_s"] = telemetry.velocity_age_s
        evidence["attitude_age_s"] = telemetry.attitude_age_s
        return MotionAssessment(
            "TELEMETRY_STALE",
            "명령은 제출됐지만 속도 또는 자세 텔레메트리가 오래되어 실제 이동을 판정할 수 없습니다.",
            command_sequence,
            evidence,
        )

    yaw_rad = math.radians(telemetry.yaw_deg or 0.0)
    north = telemetry.velocity_north_mps or 0.0
    east = telemetry.velocity_east_mps or 0.0
    body_forward = north * math.cos(yaw_rad) + east * math.sin(yaw_rad)
    body_right = -north * math.sin(yaw_rad) + east * math.cos(yaw_rad)
    body_up = -(telemetry.velocity_down_mps or 0.0)
    # Do not weight degrees against m/s in mixed ANGLE + vertical commands.
    projection_vector = requested
    if angle_mode:
        projection_vector = tuple(
            math.copysign(1.0, component) if abs(component) >= 0.02 else 0.0
            for component in requested
        )
    command_magnitude = math.sqrt(sum(component * component for component in projection_vector))
    projected_speed = sum(
        command * actual
        for command, actual in zip(projection_vector, (body_forward, body_right, body_up))
    ) / command_magnitude
    # ANGLE magnitude is measured in degrees, not m/s; only its direction is
    # used for projection and the motion threshold remains deliberately low.
    threshold = 0.04 if angle_mode else max(0.04, min(0.10, command_magnitude * 0.25))
    evidence.update(
        {
            "measured_body_forward_mps": body_forward,
            "measured_body_right_mps": body_right,
            "measured_body_up_mps": body_up,
            "projected_speed_mps": projected_speed,
            "motion_threshold_mps": threshold,
            "velocity_age_s": telemetry.velocity_age_s,
            "attitude_age_s": telemetry.attitude_age_s,
        }
    )
    if projected_speed >= threshold:
        if angle_mode:
            axes = dict(zip(("forward", "right", "up"), (
                None if sign == 0 else sign * actual >= threshold
                for sign, actual in zip(projection_vector, (body_forward, body_right, body_up))
            )))
            evidence["commanded_axis_motion_confirmed"] = axes
            if any(confirmed is False for confirmed in axes.values()):
                return MotionAssessment("PARTIAL_MOTION", "일부 명령 축의 이동만 확인됐습니다. 전체 이동 성공은 아닙니다.", command_sequence, evidence)
        return MotionAssessment(
            "MOTION_CONFIRMED",
            f"명령 방향의 실제 기체 이동이 확인되었습니다 ({projected_speed:.2f} m/s).",
            command_sequence,
            evidence,
        )

    return MotionAssessment(
        "SDK_SUBMITTED_NO_MOTION",
        "명령은 활성 상태지만 명령 방향의 실제 이동은 확인되지 않았습니다. 원인은 아직 확정되지 않았습니다.",
        command_sequence,
        evidence,
    )


class Protocol:
    def __init__(self, version: int = 1) -> None:
        self.version = version
        self._out_sequence = 0
        self._out_timestamp = -1
        self._in_sequence = -1
        self._in_timestamp = -1

    def encode(self, kind: str, payload: dict[str, Any]) -> bytes:
        if kind not in MESSAGE_TYPES:
            raise ValueError(f"unsupported message type {kind}")
        _validate_payload(kind, payload)
        timestamp = max(time.time_ns(), self._out_timestamp + 1)
        self._out_timestamp = timestamp
        document = {
            "version": self.version,
            "sequence": self._out_sequence,
            "timestamp_ns": timestamp,
            "type": kind,
            "payload": payload,
        }
        self._out_sequence += 1
        return (
            json.dumps(document, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode("utf-8")

    def decode(self, line: bytes) -> Message:
        raw = json.loads(line)
        if not isinstance(raw, dict) or set(raw) != {
            "version",
            "sequence",
            "timestamp_ns",
            "type",
            "payload",
        }:
            raise ValueError("invalid protocol envelope")
        if raw["version"] != self.version:
            raise ValueError("protocol version mismatch")
        if (
            isinstance(raw["sequence"], bool)
            or not isinstance(raw["sequence"], int)
            or raw["sequence"] != self._in_sequence + 1
        ):
            raise ValueError("non-contiguous sequence")
        if (
            isinstance(raw["timestamp_ns"], bool)
            or not isinstance(raw["timestamp_ns"], int)
            or raw["timestamp_ns"] <= self._in_timestamp
        ):
            raise ValueError("timestamp must be strictly increasing")
        if raw["type"] not in MESSAGE_TYPES:
            raise ValueError("invalid type or payload")
        _validate_payload(raw["type"], raw["payload"])
        self._in_sequence = raw["sequence"]
        self._in_timestamp = raw["timestamp_ns"]
        return Message(
            raw["version"],
            raw["sequence"],
            raw["timestamp_ns"],
            raw["type"],
            raw["payload"],
        )


class NDJSONClient:
    def __init__(self, host: str, port: int, version: int = 1, timeout_s: float = 1.0):
        self._address = (host, port)
        self._timeout = timeout_s
        self.protocol = Protocol(version)
        self._socket: socket.socket | None = None
        self._file = None
        self._armed = False
        self.last_telemetry: Telemetry | None = None
        self.last_known_telemetry: Telemetry | None = None
        self.last_telemetry_received_pc_monotonic_ns: int | None = None
        self.pc_connection_epoch = 0
        self._health_baseline = None
        self._telemetry_scope = None
        self.last_health_delta = None
        self.last_result: ControlResult | None = None
        self.last_motion_assessment: MotionAssessment | None = None
        self.session_id: str | None = None
        self.log_path: Path | None = None
        self._log_file = None
        self._motion_signature: tuple | None = None
        self._motion_previous_ns: int | None = None
        self._motion_streak_started_ns: int | None = None
        self._motion_streak_sequence: int | None = None

    def connect(self) -> None:
        # Android resets its sequence state per connection; match that scope.
        if self._socket is not None or self._file is not None:
            self.close()
        self.protocol = Protocol(self.protocol.version)
        self._invalidate_telemetry()
        self._health_baseline = None
        self._telemetry_scope = None
        self.last_health_delta = None
        self._open_session_log()
        try:
            self._socket = socket.create_connection(self._address, self._timeout)
            self._socket.settimeout(self._timeout)
            self._file = self._socket.makefile("rb")
            self.pc_connection_epoch += 1
            self._log_event("network_connected", {"address": list(self._address),
                                                  "pc_connection_epoch": self.pc_connection_epoch})
        except BaseException as error:
            self._log_event("connection_failed", {"error": repr(error)})
            if self._socket is not None:
                self._socket.close()
            self._socket = None
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None
            raise

    def close(self) -> None:
        if self._file:
            self._file.close()
        if self._socket:
            self._socket.close()
        self._file = self._socket = None
        self._armed = False
        self._invalidate_telemetry()
        self._log_event("session_closed", {})
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def _invalidate_telemetry(self):
        if self.last_telemetry is not None:
            self.last_known_telemetry = self.last_telemetry
        self.last_telemetry = None
        self.last_telemetry_received_pc_monotonic_ns = None
        self.last_result = None
        self.last_motion_assessment = None
        self._motion_signature = self._motion_previous_ns = None
        self._motion_streak_started_ns = self._motion_streak_sequence = None

    def _record_health_delta(self, telemetry):
        health = telemetry.bridge_health or {}
        process = health.get("process_start_id")
        generation, failures = telemetry.telemetry_generation, telemetry.telemetry_poll_failures
        scope = (process, generation)
        if self._telemetry_scope is not None and scope != self._telemetry_scope:
            self._motion_signature = self._motion_previous_ns = None
            self._motion_streak_started_ns = self._motion_streak_sequence = None
        self._telemetry_scope = scope
        current = (process, generation, failures)
        previous = self._health_baseline
        delta = None
        if not isinstance(process, str) or not process or generation is None or failures is None:
            status = "EVIDENCE_INCOMPLETE"
            self._health_baseline = None
        else:
            status = "BASELINE" if previous is None else "GENERATION_CHANGED"
            if previous and previous[:2] == current[:2]:
                status = "COUNTER_RESET" if failures < previous[2] else "DELTA"
                delta = None if failures < previous[2] else failures - previous[2]
            self._health_baseline = current
        self.last_health_delta = {"status": status, "poll_failures_delta": delta,
            "process_start_id": process, "telemetry_generation": generation,
            "telemetry_poll_failures": failures}
        self._log_event("telemetry_health_delta", self.last_health_delta)

    def send(
        self,
        kind: str,
        payload: dict[str, Any],
        timeout_s: float | None = None,
    ) -> Message:
        if self._socket is None or self._file is None:
            self._invalidate_telemetry()
            raise ConnectionError("client is not connected")
        expected_sequence = self.protocol._out_sequence
        if timeout_s is not None:
            self._socket.settimeout(timeout_s)
        try:
            encoded = self.protocol.encode(kind, payload)
            self._log_event("pc_request", self._redacted_document(encoded))
            sent_at = time.monotonic_ns()
            self._socket.sendall(encoded)
            line = self._file.readline()
            received_at = time.monotonic_ns()
            try:
                response_for_log: Any = json.loads(line) if line else None
            except (json.JSONDecodeError, UnicodeDecodeError):
                response_for_log = {"raw_utf8": line.decode("utf-8", errors="replace")}
            self._log_event(
                "android_ack",
                {
                    "rtt_ms": (received_at - sent_at) / 1e6,
                    "response": response_for_log,
                },
            )
        except BaseException as error:
            self._invalidate_telemetry()
            self._log_event(
                "transport_error",
                {"command_type": kind, "error": repr(error)},
            )
            raise
        finally:
            if timeout_s is not None:
                self._socket.settimeout(self._timeout)
        if not line:
            self._invalidate_telemetry()
            raise ConnectionError("Android bridge closed before acknowledgement")
        try:
            acknowledgement = self.protocol.decode(line)
            if acknowledgement.type != "ack" or acknowledgement.sequence != expected_sequence:
                raise ConnectionError("invalid acknowledgement")
        except (ValueError, TypeError, KeyError, UnicodeError, ConnectionError) as error:
            self._invalidate_telemetry()
            actual = response_for_log.get("sequence") if isinstance(response_for_log, dict) else None
            self._log_event("protocol_error", {"expected_sequence": expected_sequence,
                "actual_sequence": actual, "pc_connection_epoch": getattr(self, "_connection_epoch", self.pc_connection_epoch),
                "error": type(error).__name__})
            raise
        telemetry = Telemetry.from_ack_payload(acknowledgement.payload)
        if telemetry is not None:
            self.last_telemetry = telemetry
            self.last_known_telemetry = telemetry
            self.last_telemetry_received_pc_monotonic_ns = received_at
            self._record_health_delta(telemetry)
            streak_age_s, streak_sequence = self._update_motion_streak(telemetry)
            self.last_motion_assessment = assess_motion(
                telemetry,
                command_age_s=streak_age_s,
                command_sequence=streak_sequence,
            )
            self._log_event(
                "motion_assessment",
                {
                    "status": self.last_motion_assessment.status,
                    "message_ko": self.last_motion_assessment.message_ko,
                    "command_sequence": self.last_motion_assessment.command_sequence,
                    "evidence": self.last_motion_assessment.evidence,
                },
            )
        else:
            self._invalidate_telemetry()
            self._log_event("telemetry_unavailable", {"ack_sequence": acknowledgement.sequence,
                "pc_connection_epoch": getattr(self, "_connection_epoch", self.pc_connection_epoch)})
        self.last_result = ControlResult.from_ack_payload(acknowledgement.payload)
        if not acknowledgement.payload["ok"]:
            raise PermissionError(
                f"Android bridge rejected {kind}: {acknowledgement.payload['detail']}"
            )
        return acknowledgement

    def _update_motion_streak(
        self, telemetry: Telemetry
    ) -> tuple[float | None, int | None]:
        """Track a stable command direction across 10 Hz request sequences.

        Each velocity refresh has a new protocol sequence, so Android's raw
        command age is normally below 100 ms forever.  A direction signature
        survives ramped magnitude changes and gives motion telemetry enough
        time to prove or disprove a physical response.
        """

        angle = telemetry.sdk_roll_pitch_mode == "ANGLE"
        values = (
            (telemetry.setpoint_forward_tilt_deg if angle else telemetry.setpoint_forward_mps) or 0.0,
            (telemetry.setpoint_right_tilt_deg if angle else telemetry.setpoint_right_mps) or 0.0,
            telemetry.setpoint_up_mps or 0.0,
            telemetry.setpoint_yaw_rate_dps or 0.0,
        )
        direction = tuple(1 if value > 0.02 else -1 if value < -0.02 else 0 for value in values)
        signature = (telemetry.sdk_roll_pitch_mode, telemetry.control_path, direction)
        if not any(direction) or telemetry.armed is False or telemetry.vs_authority not in (None, "MSDK"):
            self._motion_signature = None
            self._motion_previous_ns = None
            self._motion_streak_started_ns = None
            self._motion_streak_sequence = None
            return telemetry.active_command_age_s, (
                int(telemetry.active_command_sequence)
                if telemetry.active_command_sequence is not None
                else None
            )

        now_ns = time.monotonic_ns()
        gap = self._motion_previous_ns is None or not 0 <= now_ns - self._motion_previous_ns <= 500_000_000
        if signature != self._motion_signature or self._motion_streak_started_ns is None or gap:
            self._motion_signature = signature
            self._motion_streak_started_ns = now_ns
            self._motion_streak_sequence = (
                int(telemetry.active_command_sequence)
                if telemetry.active_command_sequence is not None
                else None
            )
        self._motion_previous_ns = now_ns
        return (
            (now_ns - self._motion_streak_started_ns) / 1e9,
            self._motion_streak_sequence,
        )

    def _open_session_log(self) -> None:
        if self._log_file is not None:
            return
        configured = os.environ.get("DRONE_NAV_LOG_DIR")
        log_dir = (
            Path(configured)
            if configured
            else Path(__file__).resolve().parents[1] / "logs"
        )
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S")
        self.session_id = f"{stamp}-{uuid.uuid4().hex[:8]}"
        self.log_path = log_dir / f"{self.session_id}.jsonl"
        self._log_file = self.log_path.open("a", encoding="utf-8", buffering=1)
        self._log_event(
            "session_started",
            {
                "protocol_version": self.protocol.version,
                "address": list(self._address),
            },
        )

    def _log_event(self, event: str, data: Any) -> None:
        if self._log_file is None:
            return
        document = {
            "schema_version": 1,
            "session_id": self.session_id,
            "event": event,
            "pc_wall_time_ns": time.time_ns(),
            "pc_monotonic_ns": time.monotonic_ns(),
            "data": data,
        }
        self._log_file.write(
            json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
        )

    def log_event(self, event: str, data: Any) -> None:
        """Append a mission-level event to the same correlated JSONL log.

        Android ACKs, PC requests, actual motion evidence, and AprilTag mission
        decisions can therefore be reconstructed on one monotonic timeline.
        """
        if not isinstance(event, str) or not event:
            raise ValueError("event must be a non-empty string")
        self._log_event(event, data)

    @staticmethod
    def _redacted_document(encoded: bytes) -> dict[str, Any]:
        document = json.loads(encoded)
        payload = document.get("payload")
        if isinstance(payload, dict) and "confirmation_token" in payload:
            payload["confirmation_token"] = "<redacted>"
        return document

    def arm(self, confirmation_token: str) -> None:
        if not confirmation_token:
            raise PermissionError("arm confirmation token is required")
        self.send(
            "arm",
            {"confirmation_token": confirmation_token},
            timeout_s=SLOW_COMMAND_TIMEOUT_S,
        )
        self._armed = True

    def takeoff(self, confirmation_token: str) -> None:
        if not confirmation_token:
            raise PermissionError("takeoff confirmation token is required")
        self.send(
            "takeoff",
            {"confirmation_token": confirmation_token},
            timeout_s=SLOW_COMMAND_TIMEOUT_S,
        )

    def land(self, confirmation_token: str) -> None:
        if not confirmation_token:
            raise PermissionError("land confirmation token is required")
        self.send(
            "land",
            {"confirmation_token": confirmation_token},
            timeout_s=SLOW_COMMAND_TIMEOUT_S,
        )
        self._armed = False

    def stick_mode(self, mode: str) -> None:
        """Select official Advanced or an explicit diagnostic control path."""
        if mode not in {"basic", "advanced", "advanced_angle", "advanced_direct"}:
            raise ValueError(f"unknown stick mode {mode!r}")
        self.send("stick_mode", {"mode": mode}, timeout_s=SLOW_COMMAND_TIMEOUT_S)

    def heartbeat(self) -> None:
        self.send("heartbeat", {})

    def velocity(self, velocity: Velocity) -> None:
        if not velocity.is_zero and not self._armed:
            raise PermissionError("nonzero velocity refused until arm confirmation")
        self.send(
            "velocity" if not velocity.is_zero else "zero",
            {} if velocity.is_zero else {
                "forward_mps": velocity.forward,
                "right_mps": velocity.right,
                "up_mps": velocity.up,
                "yaw_rate_rps": velocity.yaw_rate,
            },
        )

    def attitude(
        self,
        forward_tilt_deg: float,
        right_tilt_deg: float,
        up_mps: float = 0.0,
        yaw_rate_rps: float = 0.0,
    ) -> None:
        """Send explicit BODY-angle Advanced control.

        Positive semantic signs mean forward/right/up/clockwise. Android maps
        these onto DJI's BODY+ANGLE Pitch/Roll signs and records both values.
        """
        values = (forward_tilt_deg, right_tilt_deg, up_mps, yaw_rate_rps)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("attitude values must be finite")
        if max(abs(forward_tilt_deg), abs(right_tilt_deg)) > 5.0:
            raise ValueError("attitude tilt must be within [-5, 5] degrees")
        if any(value != 0.0 for value in values) and not self._armed:
            raise PermissionError("nonzero attitude refused until arm confirmation")
        if all(value == 0.0 for value in values):
            self.send("zero", {})
            return
        self.send(
            "attitude",
            {
                "forward_tilt_deg": forward_tilt_deg,
                "right_tilt_deg": right_tilt_deg,
                "up_mps": up_mps,
                "yaw_rate_rps": yaw_rate_rps,
            },
        )

    def zero(self) -> None:
        self.velocity(Velocity(0.0, 0.0))

    def obstacle_avoidance_off(self) -> None:
        """Ask DJI to stop braking and bypassing on its own.

        Uses both documented controls: setObstacleAvoidanceType(CLOSE) and
        setObstacleAvoidanceEnabled(false, HORIZONTAL), followed by a GET
        read-back. Downward vision positioning remains enabled for indoor
        stability and landing. The private FlightController VisionAvoidEnable
        / UserAvoidEnable keys are deliberately not used.
        """
        self.send("obstacle_avoidance", {}, timeout_s=SLOW_COMMAND_TIMEOUT_S)

    def gimbal_down(self) -> None:
        self.gimbal(-90.0)

    def gimbal(self, pitch_deg: float) -> None:
        """Aim the gimbal. -90 looks straight down, 0 straight ahead.

        Part-way angles matter: at -45 the camera sees floor from 0.7 m to
        3.3 m away, so one floor tag can cover a whole traverse that a
        straight-down camera would lose after half a metre.
        """
        self.send(
            "gimbal", {"pitch_deg": float(pitch_deg)},
            timeout_s=SLOW_COMMAND_TIMEOUT_S,
        )

    def status(self, state: str) -> None:
        self.send("status", {"state": state})

    def emergency_stop(self) -> None:
        self.send("emergency_stop", {}, timeout_s=SLOW_COMMAND_TIMEOUT_S)
        self._armed = False

    def disarm(self) -> None:
        self.send("disarm", {}, timeout_s=SLOW_COMMAND_TIMEOUT_S)
        self._armed = False


class RateLimiter:
    def __init__(self, rate_hz: float) -> None:
        self.period = 1.0 / rate_hz
        self.next_tick = time.monotonic()

    def wait(self) -> None:
        now = time.monotonic()
        self.next_tick += self.period
        if self.next_tick < now:
            # A slow command stalled the loop; never "catch up" by bursting
            # ticks faster than the nominal rate.
            self.next_tick = now
        time.sleep(max(0.0, self.next_tick - now))
