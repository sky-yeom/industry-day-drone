"""Pure, bounded COEX controller. No sockets, SDK, video, or tag dependencies."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path

PROFILE_PATH = Path(__file__).with_name("profiles") / "coex_tagless_left_return_1m_1p8m.json"


class MissionFault(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}")


def number(value, name="value"):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise MissionFault("INVALID_TELEMETRY", name)
    return float(value)


def wrap_deg(value):
    return (value + 180.0) % 360.0 - 180.0


def load_profile(path=None):
    p = json.loads(Path(path or PROFILE_PATH).read_text(encoding="utf-8"))
    validate_profile(p)
    return p


def validate_profile(p):
    """This runner supports one bounded mission, never arbitrary profile actions."""
    if (p.get("schema_version") != 1 or
            p.get("profile_id") != "coex-tagless-left-return-1m-height-1p8-v1"):
        raise ValueError("unsupported profile")
    h, x, e, f, o = (p[k] for k in ("height", "horizontal", "execution_conditions", "finish", "obstacle_policy"))
    exact = [
        (h["target_m"], 1.8), (h["source"], "FlightControllerKey.KeyUltrasonicHeight"),
        (h["application_altitude_fallback"], "none"),
        (x["waypoints_right_m"], [-1.0, 0.0]), (x["waypoints_forward_m"], [0.0, 0.0]),
        (x["command_mode"], "advanced_body_velocity"),
        (x["position_estimator"], "signed_sdk_ned_velocity_integration"),
        (f["landing"], "manual_rc"),
    ]
    if any(a != b for a, b in exact):
        raise ValueError("profile changes mission semantics")
    for value in (p["april_tag_required"], x["time_based_distance_fallback"],
                  x["automatic_position_correction_loops"], f["automatic_land"],
                  f["automatic_return_to_home"], o["pc_obstacle_distance_stop_enabled"],
                  o["change_aircraft_avoidance_settings"], e["automatic_retry_or_rearm"]):
        if value is not False:
            raise ValueError("unsupported automatic behavior")
    if o["record_all_raw_sensor_values"] is not True or f["release_virtual_stick"] is not True:
        raise ValueError("logging and RC release are required")
    bounds = {
        "height": {"target_m": (1.8, 1.8), "settle_tolerance_m": (.05, .1),
                   "settle_duration_s": (2, 5), "settle_vertical_speed_mps": (.02, .05), "max_up_mps": (.05, .18),
                   "max_down_mps": (.03, .1), "climb_timeout_s": (8, 20),
                   "climb_height_upper_m": (2.0, 2.1)},
        "horizontal": {"max_lateral_speed_mps": (.1, .2), "slowdown_distance_m": (.25, .4),
                       "ramp_time_s": (1, 2), "left_arrival_tolerance_m": (.05, .1),
                       "return_arrival_tolerance_m": (.05, .15),
                       "max_estimated_forward_drift_m": (.1, .2),
                       "max_estimated_distance_from_origin_m": (1.15, 1.3),
                       "max_heading_deviation_deg": (5, 15), "max_yaw_rate_dps": (1, 10),
                       "yaw_gain_per_s": (.1, 1), "no_progress_window_s": (2, 3),
                       "no_progress_min_signed_distance_m": (.02, .05),
                       "each_leg_timeout_s": (10, 25), "stationary_speed_mps": (.02, .05),
                       "stationary_duration_s": (1, 3), "turnaround_hover_min_s": (2, 5)},
        "finish": {"hover_duration_s": (3, 5)},
        "execution_conditions": {"nominal_pc_command_rate_hz": (10, 10),
                                 "max_control_ack_age_s": (.2, .5),
                                 "max_height_sample_age_s": (.2, .5),
                                 "max_velocity_sample_age_s": (.2, .5),
                                 "max_attitude_sample_age_s": (.2, .5),
                                 "max_is_flying_sample_age_s": (.2, .5),
                                 "actual_motors_query_period_s": (.5, .5),
                                 "actual_motors_query_deadline_s": (.4, .4),
                                 "max_actual_motors_sample_age_s": (.5, 1),
                                 "max_integration_gap_s": (.2, .25),
                                 "min_start_battery_percent": (30, 100),
                                 "min_inflight_battery_percent": (20, 30),
                                 "mission_timeout_s": (60, 90)},
    }
    for group, keys in bounds.items():
        for key, (low, high) in keys.items():
            v = number(p[group][key], f"profile.{group}.{key}")
            if not low <= v <= high:
                raise ValueError(f"profile {group}.{key} outside [{low}, {high}]")
    if h["translation_height_band_m"] != [1.5, 2.1]:
        raise ValueError("unsupported translation height band")
    if e["max_inflight_readonly_queries"] != 1:
        raise ValueError("only one query may be outstanding")


@dataclass(frozen=True)
class Setpoint:
    forward_mps: float
    right_mps: float
    up_mps: float
    yaw_rate_rps: float
    phase: str
    done: bool = False

    def payload(self):
        return {k: getattr(self, k) for k in
                ("forward_mps", "right_mps", "up_mps", "yaw_rate_rps")}


def fresh(raw, field, age_field, max_age_s, now):
    value = raw.get(field)
    age = number(raw.get(age_field), age_field) / 1000.0
    received = number(raw.get("_received_monotonic_s"), "ACK received time")
    if now < received or age < 0 or age + now - received > max_age_s + 1e-8:
        raise MissionFault("STALE_TELEMETRY", field)
    return value


def rc_override(raw):
    age = raw.get("rc_override_age_ms")
    if age is not None and 0 <= number(age, "rc_override_age_ms") < 5000:
        return True
    # The local snapshot can reflect a stick before override's age is published.
    for key in ("rc_stick_left_vertical", "rc_stick_left_horizontal",
                "rc_stick_right_vertical", "rc_stick_right_horizontal"):
        if raw.get(key) is not None and abs(number(raw[key], key)) >= 150:
            return True
    return False


def check_observation(raw, motors, now, p, require_authority=True):
    """Read only this ACK, not NDJSONClient's potentially retained last sample."""
    if not isinstance(raw, dict) or not raw:
        raise MissionFault("INVALID_TELEMETRY", "missing ACK telemetry")
    e = p["execution_conditions"]
    received = number(raw.get("_received_monotonic_s"), "ACK received time")
    if not 0 <= now - received <= e["max_control_ack_age_s"]:
        raise MissionFault("STALE_TELEMETRY", "ACK")
    if rc_override(raw):
        raise MissionFault("RC_OVERRIDE", "no automatic rearm")
    flying = fresh(raw, "is_flying", "is_flying_age_ms", e["max_is_flying_sample_age_s"], now)
    if flying is not True:
        raise MissionFault("INVALID_TELEMETRY", "airborne state unconfirmed")
    if (motors.error is not None or motors.value is not True or
            motors.received_monotonic_s is None or
            not 0 <= now - motors.received_monotonic_s <= e["max_actual_motors_sample_age_s"] or
            motors.connection_epoch != raw.get("_connection_epoch")):
        raise MissionFault("MOTORS_UNCONFIRMED", str(motors.error or "motor evidence missing/stale"))
    if require_authority and not (
        raw.get("armed") is True and raw.get("vs_enabled") is True and
        raw.get("vs_advanced_enabled") is True and raw.get("vs_authority") == "MSDK"
    ):
        raise MissionFault("AUTHORITY_LOST", "VS/MSDK authority unconfirmed")
    if require_authority and (raw.get("stick_mode") != "OFFICIAL_ADVANCED" or
                              raw.get("sdk_roll_pitch_mode") != "VELOCITY"):
        raise MissionFault("AUTHORITY_LOST", "BODY velocity mode unconfirmed")
    battery = number(raw.get("battery_percent"), "battery_percent")
    if not e["min_inflight_battery_percent"] <= battery <= 100:
        raise MissionFault("BATTERY_LOW", str(battery))
    result = {}
    for name, age_field, threshold in (
        ("height_m", "height_age_ms", "max_height_sample_age_s"),
        ("velocity_north_mps", "velocity_age_ms", "max_velocity_sample_age_s"),
        ("velocity_east_mps", "velocity_age_ms", "max_velocity_sample_age_s"),
        ("velocity_down_mps", "velocity_age_ms", "max_velocity_sample_age_s"),
        ("yaw_deg", "attitude_age_ms", "max_attitude_sample_age_s"),
    ):
        result[name] = number(fresh(raw, name, age_field, e[threshold], now), name)
    for key in ("telemetry_generation", "_connection_epoch"):
        val = raw.get(key)
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            raise MissionFault("INVALID_TELEMETRY", key)
    if not -180 <= result["yaw_deg"] <= 180:
        raise MissionFault("INVALID_TELEMETRY", "yaw range")
    return result


class Controller:
    def __init__(self, profile):
        validate_profile(profile)
        self.p = profile
        self.phase = "CLIMB"
        self.x_right_m = self.x_forward_m = 0.0
        self.started = self.phase_started = self.last_received = None
        self.generation = self.epoch = self.yaw_reference = None
        self.height_stable_since = self.stationary_since = None
        self.previous_v_right = self.previous_v_forward = 0.0
        self.progress_time = self.progress_position = None
        self.wrong_direction_since = None
        self.last_fault = None

    def _transition(self, phase, now):
        self.phase, self.phase_started = phase, now
        self.stationary_since = None
        self.progress_time, self.progress_position = now, self.x_right_m
        self.wrong_direction_since = None

    def step(self, raw, motors, now):
        if self.last_fault is not None:
            raise self.last_fault
        if self.phase == "DONE":
            return Setpoint(0, 0, 0, 0, "DONE", True)
        try:
            return self._step(raw, motors, number(now, "monotonic time"))
        except MissionFault as fault:
            self.last_fault = fault
            raise

    def _step(self, raw, motors, now):
        t = check_observation(raw, motors, now, self.p)
        h, x, e = self.p["height"], self.p["horizontal"], self.p["execution_conditions"]
        received = raw["_received_monotonic_s"]
        if self.started is None:
            self.started = self.phase_started = now
            self.generation, self.epoch = raw["telemetry_generation"], raw["_connection_epoch"]
            self.yaw_reference = t["yaw_deg"]
        if (raw["telemetry_generation"], raw["_connection_epoch"]) != (self.generation, self.epoch):
            raise MissionFault("GENERATION_CHANGED")
        if now - self.started > e["mission_timeout_s"]:
            raise MissionFault("MISSION_TIMEOUT")
        dt = 0 if self.last_received is None else received - self.last_received
        if self.last_received is not None and dt <= 0:
            raise MissionFault("DUPLICATE_OBSERVATION")
        if dt > e["max_integration_gap_s"] + 1e-8:
            raise MissionFault("INTEGRATION_GAP", str(dt))
        self.last_received = received
        height = t["height_m"]
        minimum = .2 if self.phase == "CLIMB" else h["translation_height_band_m"][0]
        if not minimum <= height <= h["climb_height_upper_m"]:
            raise MissionFault("HEIGHT_OUT_OF_BOUNDS", str(height))
        yaw_error = wrap_deg(self.yaw_reference - t["yaw_deg"])
        if abs(yaw_error) > x["max_heading_deviation_deg"]:
            raise MissionFault("HEADING_DEVIATION", str(yaw_error))
        yaw = math.radians(max(-x["max_yaw_rate_dps"], min(x["max_yaw_rate_dps"],
                            x["yaw_gain_per_s"] * yaw_error)))
        error = h["target_m"] - height
        at_height = abs(error) <= h["settle_tolerance_m"] + 1e-8
        up = 0.0 if at_height else max(-h["max_down_mps"], min(h["max_up_mps"], .8 * error))
        n, east = t["velocity_north_mps"], t["velocity_east_mps"]
        speed = math.hypot(n, east)
        stopped = (speed <= x["stationary_speed_mps"] + 1e-8 and
                   abs(t["velocity_down_mps"]) <= h["settle_vertical_speed_mps"] + 1e-8)
        if stopped:
            if self.stationary_since is None:
                self.stationary_since = now
        else:
            self.stationary_since = None
        stable_stop = self.stationary_since is not None and now - self.stationary_since >= x["stationary_duration_s"] - 1e-8
        angle = math.radians(self.yaw_reference)
        vr = -n * math.sin(angle) + east * math.cos(angle)
        vf = n * math.cos(angle) + east * math.sin(angle)
        if self.phase == "CLIMB":
            if now - self.phase_started > h["climb_timeout_s"]:
                raise MissionFault("CLIMB_TIMEOUT")
            if at_height:
                if self.height_stable_since is None:
                    self.height_stable_since = now
                if now - self.height_stable_since >= h["settle_duration_s"] - 1e-8 and stable_stop:
                    self.yaw_reference = t["yaw_deg"]
                    angle = math.radians(self.yaw_reference)
                    vr = -n * math.sin(angle) + east * math.cos(angle)
                    vf = n * math.cos(angle) + east * math.sin(angle)
                    self._transition("LEFT", now)
            else:
                self.height_stable_since = None
            self.previous_v_right, self.previous_v_forward = vr, vf
            return Setpoint(0, 0, up, yaw, self.phase)

        self.x_right_m += .5 * (self.previous_v_right + vr) * dt
        self.x_forward_m += .5 * (self.previous_v_forward + vf) * dt
        self.previous_v_right, self.previous_v_forward = vr, vf
        if (abs(self.x_forward_m) > x["max_estimated_forward_drift_m"] or
                math.hypot(self.x_forward_m, self.x_right_m) > x["max_estimated_distance_from_origin_m"]):
            raise MissionFault("DRIFT_LIMIT")
        elapsed = now - self.phase_started
        right = 0.0
        if self.phase in ("LEFT", "RIGHT"):
            sign, target, tolerance = (-1, -1.0, x["left_arrival_tolerance_m"]) if self.phase == "LEFT" else (1, 0.0, x["return_arrival_tolerance_m"])
            remaining = sign * (target - self.x_right_m)
            if elapsed > x["each_leg_timeout_s"]:
                raise MissionFault("LEG_TIMEOUT", self.phase)
            if remaining < -tolerance:
                raise MissionFault("OVERSHOOT", self.phase)
            if abs(target - self.x_right_m) <= tolerance + 1e-8:
                self._transition("BRAKE_LEFT" if self.phase == "LEFT" else "FINAL_HOVER", now)
            else:
                if sign * vr < -.05:
                    if self.wrong_direction_since is None:
                        self.wrong_direction_since = now
                    if now - self.wrong_direction_since >= .5 - 1e-8:
                        raise MissionFault("WRONG_DIRECTION")
                else:
                    self.wrong_direction_since = None
                if now - self.progress_time >= x["no_progress_window_s"] - 1e-8:
                    if sign * (self.x_right_m - self.progress_position) < x["no_progress_min_signed_distance_m"]:
                        raise MissionFault("MOTION_UNCONFIRMED")
                    self.progress_time, self.progress_position = now, self.x_right_m
                right = sign * min(x["max_lateral_speed_mps"],
                                   x["max_lateral_speed_mps"] * elapsed / x["ramp_time_s"],
                                   x["max_lateral_speed_mps"] * remaining / x["slowdown_distance_m"])
        elif self.phase in ("BRAKE_LEFT", "FINAL_HOVER"):
            target = -1.0 if self.phase == "BRAKE_LEFT" else 0.0
            tolerance = x["left_arrival_tolerance_m"] if self.phase == "BRAKE_LEFT" else x["return_arrival_tolerance_m"]
            minimum_hover = x["turnaround_hover_min_s"] if self.phase == "BRAKE_LEFT" else self.p["finish"]["hover_duration_s"]
            if elapsed > 8.0:
                raise MissionFault("SETTLE_TIMEOUT", self.phase)
            if stable_stop and elapsed >= minimum_hover - 1e-8 and at_height:
                if abs(self.x_right_m - target) > tolerance + 1e-8:
                    raise MissionFault("OVERSHOOT", self.phase)
                self._transition("RIGHT" if self.phase == "BRAKE_LEFT" else "DONE", now)
        return Setpoint(0.0, right, up if self.phase != "DONE" else 0,
                        yaw if self.phase != "DONE" else 0, self.phase, self.phase == "DONE")

    def summary(self):
        return {"phase": self.phase, "estimated_right_m": self.x_right_m,
                "estimated_forward_m": self.x_forward_m,
                "position_source": "sdk_velocity_integration", "position_is_estimated": True,
                "fault": None if self.last_fault is None else self.last_fault.code}
