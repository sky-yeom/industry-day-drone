"""Supervised AprilTag corridor adapter using the tested navigation primitives.

Site configuration is explicit, private and revisioned. No import or constructor
sends SDK commands. A route is never resumed after an uncertain write or RC input.
"""
from __future__ import annotations
import base64
import copy
from dataclasses import replace
import ipaddress
import json
import math
from pathlib import Path
import socket
import threading
import time
import uuid

from ..config import load_config
from ..protocol import NDJSONClient, RateLimiter
from ..vision import TcpVideoStream
from .camera import VideoBroker
from .service import ToolError

BUILD_ID = "5.18-connectivity.20260910.6"


def fresh(raw, key, max_ms=500):
    age = raw.get(key + "_age_ms")
    return isinstance(age, (int, float)) and not isinstance(age, bool) and math.isfinite(age) and 0 <= age <= max_ms


def ground_verified(raw):
    return (raw.get("is_flying") is False and raw.get("are_motors_on") is False
            and fresh(raw, "is_flying") and fresh(raw, "are_motors_on")
            and raw.get("armed") is False and raw.get("vs_enabled") is False
            and raw.get("vs_authority") == "RC")


def process_identity(raw):
    health = raw.get("bridge_health")
    return raw.get("process_start_id") or (health.get("process_start_id") if isinstance(health, dict) else None)


class FreshVideoStream(TcpVideoStream):
    def __init__(self, host, port, codec="h264", *, initial_keyframe_timeout_s=5.0):
        super().__init__(host, port, codec, reconnect=False,
                         initial_keyframe_timeout_s=initial_keyframe_timeout_s)

    def detect_latest(self, detector, max_age_s):
        detections, _ = super().detect_latest(detector, max_age_s)
        snapshot = self.last_detection_snapshot
        # Detection itself can be expensive. Admission uses age after detection.
        age = float("inf") if snapshot is None else time.monotonic() - snapshot.received_s
        return (detections if 0 <= age <= max_age_s else []), age


class DeadlineTransport:
    """Socket and bounded line reader sharing one total request deadline."""
    def __init__(self, raw):
        self.raw = raw
        self.deadline = time.perf_counter() + .4
        self.buffer = b""
        self.before_write = lambda: None

    def _budget(self):
        remaining = self.deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError("total ACK deadline exceeded")
        self.raw.settimeout(remaining)

    def settimeout(self, value):
        self.raw.settimeout(value)

    def sendall(self, encoded):
        self._budget()
        self.before_write()
        self.raw.sendall(encoded)

    def readline(self):
        while b"\n" not in self.buffer:
            self._budget()
            chunk = self.raw.recv(min(65536, 1048576 - len(self.buffer)))
            if not chunk:
                raise ConnectionError("incomplete ACK")
            self.buffer += chunk
            if len(self.buffer) >= 1048576:
                raise ConnectionError("oversized ACK")
        line, self.buffer = self.buffer.split(b"\n", 1)
        self._budget()
        return line + b"\n"

    def close(self):
        self.raw.close()


class MissionClient(NDJSONClient):
    flight_state_max_ms = 500

    def __init__(self, config, cancel, on_snapshot):
        super().__init__(config.network.host, config.network.port, timeout_s=.4)
        self._secrets = {config.network.confirmation_token} if config.network.confirmation_token else set()
        self.cancel = cancel
        self.on_snapshot = on_snapshot
        self.raw = {}
        self.stream = None
        self.deadline = None
        self.generation = None
        self.failed = False
        self.cleaning = False
        self.attempted_action = False
        self.owner = None
        self.received = 0.0
        self.ever_connected = False
        self.write_started = False

    def _scrub(self, value):
        if isinstance(value, dict):
            return {key: ("<redacted>" if str(key).lower() in {"confirmation_token", "arm_token", "token", "password", "secret"}
                          else self._scrub(item)) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [self._scrub(item) for item in value]
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, "<redacted>")
        return value

    def _log_event(self, event, data):
        super()._log_event(event, self._scrub(data))

    def connect(self):
        if self.ever_connected:
            raise RuntimeError("A mission connection cannot be reopened")
        self.ever_connected = True
        ipaddress.ip_address(self._address[0])  # avoid unbounded DNS
        self.owner = threading.get_ident()
        self._open_session_log()
        raw = socket.create_connection(self._address, .4)
        self._socket = self._file = DeadlineTransport(raw)
        self._log_event("network_connected", {"address": list(self._address)})

    def send(self, kind, payload, timeout_s=None):
        if isinstance(payload.get("confirmation_token"), str) and payload["confirmation_token"]:
            self._secrets.add(payload["confirmation_token"])
        if self.owner != threading.get_ident():
            raise RuntimeError("Only mission worker may own control socket")
        if self.failed or self._socket is None:
            raise ConnectionError("Control transport failed; no reconnect/replay")
        if kind not in {"status", "zero", "attitude", "gimbal", "takeoff", "arm", "disarm", "stick_mode"}:
            raise PermissionError("Command not exposed by the physical mission profile")
        if not self.cleaning and (self.cancel.is_set() or (self.deadline and time.perf_counter() >= self.deadline)):
            raise InterruptedError("Mission cancelled/deadline reached; no resume")
        if kind == "stick_mode" and payload.get("mode") != "advanced_angle":
            raise ValueError("This site uses Advanced BODY ANGLE only")
        if self._file.buffer:
            self.failed = True
            raise ConnectionError("Unsolicited ACK; new action refused")
        budget = {"takeoff": 3., "arm": 3., "gimbal": 2., "stick_mode": 3., "disarm": 1.}.get(kind, .4)
        self._socket.deadline = time.perf_counter() + budget
        prior_raw, prior_received = copy.deepcopy(self.raw), self.received
        self.last_telemetry = None
        self.raw = {}
        self.write_started = False
        def before_write():
            if self.cleaning:
                if kind not in {"zero", "disarm", "status"}:
                    raise PermissionError("Cleanup cannot dispatch a flight action")
            elif self.cancel.is_set() or (self.deadline and time.perf_counter() >= self.deadline):
                raise InterruptedError("Cancelled before network write")
            if kind == "takeoff":
                elapsed_ms = (time.perf_counter() - prior_received) * 1000
                proof = dict(prior_raw)
                for age_key in ("is_flying_age_ms", "are_motors_on_age_ms"):
                    if isinstance(proof.get(age_key), (int, float)) and not isinstance(proof.get(age_key), bool):
                        proof[age_key] += elapsed_ms
                if elapsed_ms < 0 or not ground_verified(proof):
                    raise PermissionError("Fresh motors-off ground proof expired before takeoff write")
            self.write_started = True
            if kind not in {"status", "zero", "disarm"}:
                self.attempted_action = True
        self._socket.before_write = before_write
        reserved_sequence, reserved_timestamp = self.protocol._out_sequence, self.protocol._out_timestamp
        try:
            ack = super().send(kind, payload, timeout_s=budget)
        except PermissionError:
            if not self.write_started:
                self.protocol._out_sequence, self.protocol._out_timestamp = reserved_sequence, reserved_timestamp
            raise PermissionError(f"{kind} rejected; consult redacted ACK log") from None
        except Exception:
            if not self.write_started:
                self.protocol._out_sequence, self.protocol._out_timestamp = reserved_sequence, reserved_timestamp
                raise
            self.failed = True
            self.on_snapshot({"connected": False, "ground_verified": False, "error": "transport_or_protocol_failure"})
            raise ConnectionError(f"{kind} outcome unknown; never replay") from None
        raw = ack.payload.get("telemetry")
        if not isinstance(raw, dict) or not raw:
            self.failed = True
            raise ConnectionError("ACK missing telemetry")
        self.raw = copy.deepcopy(raw)
        self.received = time.perf_counter()
        generation = (process_identity(raw), raw.get("telemetry_generation"))
        if self.generation is not None and generation != self.generation:
            self.failed = True
            raise ConnectionError("App process/telemetry generation changed; no flight resume")
        self.generation = generation
        self.on_snapshot(self._scrub({"connected": True, "ground_verified": ground_verified(raw),
                          "raw_telemetry": self.raw, **{k: raw.get(k) for k in
                          ("is_flying", "are_motors_on", "vs_enabled", "vs_authority", "bridge_build_id")}}))
        if self._armed and not self.cleaning and kind != "disarm":
            if (raw.get("armed") is not True or raw.get("vs_authority") != "MSDK"
                    or raw.get("vs_enabled") is not True):
                raise InterruptedError("Control authority changed; no automatic re-arm")
            override = self.last_telemetry.rc_override_age_s
            if override is not None and 0 <= override < 5:
                raise InterruptedError("RC stick override; no automatic resume")
        return ack

    def attitude(self, forward_tilt_deg, right_tilt_deg, up_mps=0., yaw_rate_rps=0.):
        self.status("tool_mission_before_motion")
        raw, t = self.raw, self.last_telemetry
        if raw.get("is_flying") is not True or not fresh(raw, "is_flying", self.flight_state_max_ms):
            raise InterruptedError("Fresh airborne state is required")
        if raw.get("armed") is not True or raw.get("vs_enabled") is not True or raw.get("vs_advanced_enabled") is not True or raw.get("vs_authority") != "MSDK":
            raise InterruptedError("Virtual Stick authority lost")
        if t is None or any(age is None or not math.isfinite(age) or not 0 <= age <= .5
                            for age in (t.height_age_s, t.velocity_age_s, t.attitude_age_s)):
            raise InterruptedError("Fresh height, velocity and attitude required")
        if any(value is None or not math.isfinite(value) for value in
               (t.velocity_north_mps, t.velocity_east_mps, t.velocity_down_mps, t.yaw_deg)):
            raise InterruptedError("Actual finite velocity and yaw values required")
        if t.rc_override_age_s is not None and t.rc_override_age_s < 5:
            raise InterruptedError("RC override: mission cancelled")
        if t.height_m is None or not .5 <= t.height_m <= 1.8:
            raise InterruptedError("Height outside validated corridor envelope")
        if up_mps > 0 and t.height_m >= 1.6 or up_mps < 0 and t.height_m <= 1.0:
            raise InterruptedError("Vertical correction exceeds corridor envelope")
        if t.battery_percent is None or t.battery_percent < 30:
            raise InterruptedError("Battery below configured 30% mission minimum")
        if self.stream is None or not 0 <= self.stream.read()[2] <= .5:
            raise InterruptedError("No fresh camera frame")
        values = (forward_tilt_deg, right_tilt_deg, up_mps, yaw_rate_rps)
        if not all(math.isfinite(v) for v in values) or max(abs(values[0]), abs(values[1])) > 1.50001 or abs(up_mps) > .18001 or abs(yaw_rate_rps) > math.radians(15):
            raise ValueError("Setpoint exceeds validated limits")
        # OA remains raw observation only; aircraft settings are never altered.
        super().attitude(*values)


class VisitGate:
    """Minimal route interface accepted by the tested visual gate helpers."""
    def __init__(self, tag, phase):
        self.expected_id, self.phase, self.confirmed = tag, phase, False

    def confirm(self, tag):
        if tag != self.expected_id:
            raise ValueError("Unexpected tag cannot advance route")
        self.confirmed = True


class LiveAdapter:
    adapter_name = "legacy"
    mode = "live"
    destination_ids = ["tag-1", "tag-2", "tag-3"]

    def __init__(self, site_path, config_path):
        site = json.loads(Path(site_path).read_text(encoding="utf-8-sig"))
        required = {"profile_id", "site_revision", "physical_leftward_tag_ids", "home_tag_id",
                    "floor_tag_id", "target_height_m", "layout_confirmed", "expected_bridge_build_id"}
        if set(site) != required or site["layout_confirmed"] is not True:
            raise ValueError("Site must be explicitly measured/confirmed; sample profile is not a live site")
        if (type(site["home_tag_id"]) is not int or site["home_tag_id"] not in (2, 6)
                or type(site["floor_tag_id"]) is not int or site["floor_tag_id"] != 0
                or site["target_height_m"] != 1.4
                or site["expected_bridge_build_id"] != BUILD_ID):
            raise ValueError("Live adapter requires floor ID0, Home ID2 or ID6, and the 1.4m profile")
        order = site["physical_leftward_tag_ids"]
        required_wall_ids = {1, 2, 3, site["home_tag_id"]}
        if (type(order) is not list or any(type(tag) is not int for tag in order)
                or len(order) != len(required_wall_ids) or set(order) != required_wall_ids
                or [tag for tag in order if tag in (1, 2, 3)] != [2, 1, 3]):
            raise ValueError("Measured physical_leftward_tag_ids must include Home and each destination "
                             "exactly once, preserving destination order [2, 1, 3]")
        from .service import identifier
        self.profile_id, self.site_revision = identifier(site["profile_id"]), identifier(site["site_revision"])
        self.home_tag_id = site["home_tag_id"]
        self.floor_tag_id = site["floor_tag_id"]
        self.target_height_m = site["target_height_m"]
        self.config = load_config(config_path)
        self.config.require_hardware_calibration()
        if (not self.config.actual_measurements_confirmed or self.config.patrol is None
                or not (required_wall_ids | {self.floor_tag_id}) <= set(self.config.tag_map)):
            raise ValueError("Measured camera/tag configuration is required")
        ipaddress.ip_address(self.config.network.host)
        if not self.config.network.confirmation_token or self.config.network.confirmation_token.startswith("REPLACE_"):
            raise ValueError("Private phone arm token is required")
        self.config = replace(self.config, network=replace(self.config.network, rate_hz=10),
            patrol=replace(self.config.patrol, obstacle_stop_m=0., cruise_altitude_m=self.target_height_m,
                angle_deg=1.5, recovery_max_angle_deg=1.5, leg_timeout_s=45,
                align_cruise_yaw=False))
        self.site = site
        self.live_ready = True  # configured adapter; fresh hardware proof still required at admission
        self.lock = threading.Lock()
        self.busy = False
        self.cache = {"connected": False, "ground_verified": False}
        self.cache_at = 0.
        self.video_broker = VideoBroker(lambda: FreshVideoStream(
            self.config.network.host, self.config.network.video_port, self.config.network.video_codec))

    def _direction(self, departure, destination):
        order = self.site["physical_leftward_tag_ids"]
        return "left" if order.index(destination) > order.index(departure) else "right"

    def _snapshot(self, data):
        with self.lock:
            self.cache, self.cache_at = copy.deepcopy(data), time.perf_counter()

    def status(self):
        with self.lock:
            busy = self.busy
            cached = copy.deepcopy(self.cache)
            age = time.perf_counter() - self.cache_at
        if not busy and age > .2:
            client = MissionClient(self.config, threading.Event(), self._snapshot)
            try:
                client.connect()
                client.status("tool_service_independent_ground_read")
            except Exception:
                self._snapshot({"connected": False, "ground_verified": False, "error": "status_unavailable"})
            finally:
                client.close()
            with self.lock:
                cached, age = copy.deepcopy(self.cache), time.perf_counter() - self.cache_at
        cached["received_age_s"] = age
        raw = cached.get("raw_telemetry")
        if isinstance(raw, dict):
            aged = dict(raw)
            for key in ("is_flying_age_ms", "are_motors_on_age_ms"):
                if isinstance(aged.get(key), (int, float)) and not isinstance(aged.get(key), bool):
                    aged[key] += age * 1000
            cached["ground_verified"] = ground_verified(aged)
        if age > .5:
            cached["ground_verified"] = False
            cached["current_observation"] = False
        else:
            cached["current_observation"] = True
        return cached

    def _release(self, client):
        client.cleaning = True
        errors = []
        for action in ("zero", "disarm"):
            try:
                getattr(client, action)()
            except Exception as exc:
                errors.append(f"{action}:{type(exc).__name__}")
        try:
            client.status("tool_release_verification")
            raw, t = client.raw, client.last_telemetry
            rc = raw.get("vs_authority") == "RC" and raw.get("vs_enabled") is False and raw.get("armed") is False
            speeds = (t.velocity_north_mps, t.velocity_east_mps, t.velocity_down_mps) if t else ()
            low_speed = bool(speeds) and t.velocity_age_s is not None and 0 <= t.velocity_age_s <= .5 and all(v is not None and abs(v) <= .08 for v in speeds)
            return {"physical_stop_confirmed": rc and (ground_verified(raw) or low_speed),
                    "control_released_to_rc": rc, "ground_verified": ground_verified(raw), "cleanup_errors": errors}
        except Exception as exc:
            return {"physical_stop_confirmed": False, "control_released_to_rc": False,
                    "ground_verified": False, "cleanup_errors": errors + [type(exc).__name__]}

    def run(self, mission, cancel, emit):
        from ..vision import AprilTagDetector
        from ..patrol import (PatrolPhase, _DetectionLogger, _acquire_tag,
            _traverse_to_expected, _pause_zero, _visual_floor_height_m)
        with self.lock:
            self.busy = True
        client = MissionClient(self.config, cancel, self._snapshot)
        stream = logger = None
        completed = False
        error = None
        result = {}
        try:
            emit(state="preflight")
            client.connect()
            client.status("tool_mission_preflight")
            if not ground_verified(client.raw) or client.raw.get("bridge_build_id") != BUILD_ID:
                raise RuntimeError("Fresh motors-off, grounded, RC state and new bridge build required")
            if not process_identity(client.raw) or client.raw.get("telemetry_generation") is None:
                raise RuntimeError("Missing app process/generation identity")
            if client.last_telemetry.battery_percent is None or client.last_telemetry.battery_percent < 30:
                raise RuntimeError("30% battery required for the complete camera route")
            client.log_event("tool_mission_plan", {"mission_id": mission["mission_id"],
                "destinations": mission["destination_ids"], "site": self.site,
                "pc_obstacle_distance_policy": "observe_only", "aircraft_oa_changes": False})
            detector = AprilTagDetector(self.config)
            stream = self.video_broker.acquire_mission()
            client.stream = stream
            logger = _DetectionLogger(client)
            logger.attach_capture(stream)
            client.gimbal_down()
            deadline = time.perf_counter() + 12
            while time.perf_counter() < deadline:
                if cancel.wait(.1):
                    raise InterruptedError("Cancelled before takeoff")
                tags, age = stream.detect_latest(detector, .5)
                if age <= .5 and any(t.tag_id == self.floor_tag_id for t in tags):
                    break
            else:
                raise RuntimeError("Fresh live image with floor ID0 required before takeoff")
            client.stick_mode("advanced_angle")
            client.status("tool_immediate_takeoff_ground_proof")
            if not ground_verified(client.raw):
                raise RuntimeError("Ground/motor state changed before takeoff")
            if client.last_telemetry.rc_override_age_s is not None and 0 <= client.last_telemetry.rc_override_age_s < 5:
                raise InterruptedError("RC stick activity before takeoff")
            emit(state="taking_off")
            client.takeoff(self.config.network.confirmation_token)  # once only
            limiter = RateLimiter(10)
            deadline = time.perf_counter() + 12
            while time.perf_counter() < deadline:
                limiter.wait()
                client.status("tool_takeoff_confirmation")
                if client.raw.get("is_flying") is True and fresh(client.raw, "is_flying") and client.last_telemetry.height_m is not None and client.last_telemetry.height_m >= .5:
                    break
            else:
                raise RuntimeError("Takeoff not confirmed; no retry")
            client.arm(self.config.network.confirmation_token)  # once only
            client.deadline = time.perf_counter() + 180
            floor = VisitGate(self.floor_tag_id, PatrolPhase.FLOOR_HOME)
            _acquire_tag(client, limiter, stream, detector, logger, floor, self.config.patrol)
            self._climb(client, limiter, stream, detector, _visual_floor_height_m)
            client.gimbal(0)
            _pause_zero(client, limiter, 1.)
            emit(state="running")
            previous = self.home_tag_id
            # First establish the physical home tag before choosing route direction.
            home = VisitGate(self.home_tag_id, PatrolPhase.WALL_HOME)
            _acquire_tag(client, limiter, stream, detector, logger, home, self.config.patrol,
                         target_x=self.config.camera.cx, tolerance_px=self.config.patrol.wall_center_tolerance_px)
            if not home.confirmed:
                raise RuntimeError("Visual Home acquisition was not confirmed")
            for visit in mission["visits"]:
                i, expected = visit["visit_index"], int(visit["destination_id"].split("-")[-1])
                emit(visit_index=i, visit_state="moving")
                gate = VisitGate(expected, PatrolPhase.OUTBOUND)
                if previous == expected:
                    _acquire_tag(client, limiter, stream, detector, logger, gate, self.config.patrol,
                                 target_x=self.config.camera.cx, tolerance_px=self.config.patrol.wall_center_tolerance_px)
                else:
                    _traverse_to_expected(client, limiter, stream, detector, logger, gate, self.config.patrol,
                        direction=self._direction(previous, expected),
                        target_x=self.config.camera.cx, target_y=self.config.patrol.wall_target_y_px,
                        departure_tag_id=previous)
                if not gate.confirmed:
                    raise RuntimeError("Visual arrival was not confirmed")
                emit(visit_index=i, visit_state="arrived", arrival_confirmed=True)
                self._capture(client, limiter, stream, detector, expected, i, emit)
                previous = expected
            emit(state="returning")
            if previous != self.home_tag_id:
                gate = VisitGate(self.home_tag_id, PatrolPhase.RETURN)
                _traverse_to_expected(client, limiter, stream, detector, logger, gate, self.config.patrol,
                    direction=self._direction(previous, self.home_tag_id),
                    target_x=self.config.camera.cx, target_y=self.config.patrol.wall_target_y_px,
                    departure_tag_id=previous)
                if not gate.confirmed:
                    raise RuntimeError("Visual Home return was not confirmed")
            client.zero()
            completed = True
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc).replace(self.config.network.confirmation_token, '<redacted>')}"
            client.log_event("tool_mission_interrupted", {"error": error})
        finally:
            if client._socket is not None and client.attempted_action:
                result = self._release(client)
            else:
                proven = bool(client.raw) and time.perf_counter() - client.received <= .1 and ground_verified(client.raw)
                result = {"physical_stop_confirmed": proven, "ground_verified": proven,
                          "no_flight_action_dispatched": not client.attempted_action}
            client.close()
            if stream:
                self.video_broker.release_mission()
            if logger:
                logger.close()
            with self.lock:
                self.busy = False
        result.update(route_completed=completed, error=error)
        if result["ground_verified"]:
            result["state"] = "completed" if completed else "stopped" if cancel.is_set() else "failed"
        elif completed and not cancel.is_set() and result.get("control_released_to_rc"):
            result["state"] = "awaiting_rc_landing"
        elif result["physical_stop_confirmed"]:
            result["state"] = "stopped"
        else:
            result.update(state="outcome_unknown", verification_pending=True)
        return result

    def _climb(self, client, limiter, stream, detector, visual_height):
        deadline, held = time.perf_counter() + 8., None
        previous_frame = None
        while time.perf_counter() < deadline:
            limiter.wait()
            client.status("tool_bounded_climb")
            tags, age = stream.detect_latest(detector, .5)
            floor = next((tag for tag in tags if tag.tag_id == self.floor_tag_id), None)
            if floor is None or age > .5 or visual_height(self.config, floor) > min(2.1, self.config.room.height_m - .3):
                raise RuntimeError("Floor ID0 visual height cross-check unavailable")
            t = client.last_telemetry
            elapsed = time.perf_counter() - client.received
            if t.height_m is None or t.height_age_s is None or not 0 <= t.height_age_s + elapsed <= .5:
                raise RuntimeError("No fresh downward display height")
            delta = self.target_height_m - t.height_m
            if delta < -.051:
                raise RuntimeError("Above ascent target; no automatic descent")
            now = time.perf_counter()
            if now >= deadline:
                break
            stable_vertical = (t.velocity_down_mps is not None and math.isfinite(t.velocity_down_mps)
                and abs(t.velocity_down_mps) <= .05 and t.velocity_age_s is not None
                and 0 <= t.velocity_age_s + elapsed <= .5)
            if abs(delta) <= .051:
                client.zero()
                key = stream.last_detection_snapshot.key
                if not stable_vertical:
                    held, previous_frame = None, None
                    continue
                if key != previous_frame:
                    held = now if held is None else held
                    if now - held >= .5:
                        return
                previous_frame = key
            else:
                held, previous_frame = None, None
                client.attitude(0., 0., min(.18, max(.10, delta * .8)), 0.)
        raise RuntimeError(f"{self.target_height_m:g}m ascent target unconfirmed after 8s")

    def _capture(self, client, limiter, stream, detector, tag, index, emit):
        import cv2
        from ..wall_framing import WallViewAction, tag_view_action, uses_tv_framing
        from ..patrol import _align_tv_composition
        last_key, count, held = None, 0, None
        held_generation = None
        deadline = time.monotonic() + 6 + (
            self.config.patrol.tv_framing.max_correction_s if uses_tv_framing(tag, self.config.patrol) else 0.0)
        while time.monotonic() < deadline:
            limiter.wait()
            client.zero()
            tags, age = stream.detect_latest(detector, .5)
            snapshot = stream.last_detection_snapshot
            t = client.last_telemetry
            speeds = (t.velocity_north_mps, t.velocity_east_mps, t.velocity_down_mps) if t else ()
            telemetry_elapsed = time.perf_counter() - client.received
            stable = (t and t.velocity_age_s is not None and 0 <= t.velocity_age_s + telemetry_elapsed <= .5
                      and all(v is not None and abs(v) <= .08 for v in speeds))
            framed = snapshot is not None and any(
                d.tag_id == tag and math.isfinite(d.pose_error)
                and (not uses_tv_framing(tag, self.config.patrol)
                     or tag_view_action(d, snapshot.frame.shape, "left", self.config.patrol)
                     is WallViewAction.INSIDE)
                for d in tags)
            if (stable and snapshot is not None and math.isfinite(age) and 0 <= age <= .5
                    and not framed and uses_tv_framing(tag, self.config.patrol)
                    and any(d.tag_id == tag for d in tags)):
                _align_tv_composition(client, limiter, stream, detector, tag, self.config.patrol, deadline=deadline)
                held, held_generation = None, None
                continue
            if not stable or not math.isfinite(age) or not 0 <= age <= .5 or not framed:
                held, held_generation = None, None
                continue
            if held_generation != snapshot.generation:
                held = None
                held_generation = snapshot.generation
            held = time.perf_counter() if held is None else held
            if time.perf_counter() - held < .5 or snapshot is None or snapshot.key == last_key:
                continue
            ok, data = cv2.imencode(".png", snapshot.frame)
            if not ok or data.size > 4 * 1024 * 1024:
                raise RuntimeError("Camera PNG encoding failed/exceeds 4MiB")
            if (not 0 <= time.monotonic() - snapshot.received_s <= .5
                    or not 0 <= t.velocity_age_s + time.perf_counter() - client.received <= .5):
                held = None
                continue
            capture = {"capture_id": uuid.uuid4().hex, "arrival_confirmed": True,
                "image_base64": base64.b64encode(data.tobytes()).decode("ascii"),
                "captured_at_unix_ms": int(time.time() * 1000),
                "capture_source": "pc_decoded_camera_frame", "frame_generation": snapshot.generation,
                "frame_id": snapshot.frame_id, "frame_decoded_pc_monotonic_s": snapshot.received_s,
                "aircraft_exposure_timestamp_available": False,
                "framing_mode": "tv_left_reference" if uses_tv_framing(tag, self.config.patrol) else "tag_only",
                "tv_visibility_verified": False}
            emit(visit_index=index, capture=capture, visit_state="captured")
            last_key, count = snapshot.key, count + 1
            if count == 2:
                return
        raise RuntimeError("Two fresh stationary camera captures were not confirmed")
