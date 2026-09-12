"""Session-local, memory-only v1 simulation. No listener, worker, SDK or process.

Pass create_transport(caller_id) to DroneClient's transport argument. Keep that
instance for the client's lifetime: histories are bounded and never silently
evicted, and a new instance deliberately starts an independent mock session.
Elapsed monotonic deadlines drive progress, not polling frequency. Due events
are materialized when observed, so abandoned sessions need no background tasks.
"""
from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
import hashlib
import itertools
import json
from pathlib import Path
import re
import struct
import time
from uuid import uuid4
import zlib

if __package__:
    from .camera import CaptureError, FixtureCamera
else:
    from camera import CaptureError, FixtureCamera

CONTRACT_VERSION = "1.0.0"
PROFILE_ID = "contract-mock-v1"
SITE_REVISION = "v1"
SCENARIOS = (
    "nominal", "preflight-failure", "camera-unavailable", "connection-loss",
    "manual-landing", "lost-ack",
)
_SCHEMA_PATH = (Path(__file__).resolve().parents[1] / "drone-control"
                / "integration" / "speech_control_contract" / "tools.json")
_SCHEMAS = {tool["name"]: tool["parameters"]
            for tool in json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))}
_DESTINATIONS = ("tag-1", "tag-2", "tag-3")
_ROUTES = tuple(itertools.permutations(_DESTINATIONS))
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_BASE = dict(contract_version=CONTRACT_VERSION, schema_version=1,
             execution_mode="mock", physical_execution=False)
_LETTERS = (
    ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
)


class _MockError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _fail(code, message):
    raise _MockError(code, message)


def _success(**fields):
    return dict(_BASE, ok=True, status="ok", **fields)


def _error(exc):
    return dict(_BASE, ok=False, error={"code": exc.code, "message": str(exc)})


def _valid_id(value):
    return type(value) is str and _ID.fullmatch(value) is not None


def _validate_arguments(schema, value):
    """Validate the deliberately small shared seven-tool argument vocabulary."""
    kind = schema["type"]
    if kind == "object":
        if type(value) is not dict or set(value) != set(schema["required"]):
            _fail("INVALID_ARGUMENT", "Request does not match the fixed tool schema")
        for key, item in value.items():
            _validate_arguments(schema["properties"][key], item)
    elif kind == "array":
        if type(value) is not list or not schema["minItems"] <= len(value) <= schema["maxItems"]:
            _fail("INVALID_ARGUMENT", "Invalid tool argument array")
        for item in value:
            _validate_arguments(schema["items"], item)
    elif kind == "string":
        if (type(value) is not str or not schema["minLength"] <= len(value) <= schema["maxLength"]
                or re.fullmatch(schema["pattern"], value) is None):
            _fail("INVALID_ARGUMENT", "Invalid tool argument identifier")
    else:
        raise ValueError("Unsupported shared tool schema type")


def _chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def _capture(mission_id, visit_index, destination_id, ordinal, timestamp, generation):
    """Generate the frozen mock's original artwork and identifying PNG tEXt."""
    width, height = 640, 360
    capture_id = f"{mission_id}:{visit_index}:{ordinal}"
    identity = hashlib.sha256(capture_id.encode("ascii")).digest()
    tag = int(destination_id[-1])
    stride = width * 3 + 1
    raw = bytearray(stride * height)
    stripe = b"".join(bytes((byte, 50 + ordinal * 50, tag * 60)) * 20 for byte in identity)
    for y in range(height):
        row = bytearray(stripe if y > 315 else b"".join(
            bytes((30 + tag * 35, 35 + ordinal * 35, 70 + (col + y // 32) * 3)) * 32
            for col in range(20)))
        if 112 <= y < 252:
            for letter, glyph in enumerate(_LETTERS):
                for col, bit in enumerate(glyph[(y - 112) // 20]):
                    if bit == "1":
                        at = (88 + letter * 120 + col * 20) * 3
                        row[at:at + 60] = b"\xff" * 60
        at = y * stride + 1
        raw[at:at + width * 3] = row
    image = (b"\x89PNG\r\n\x1a\n"
             + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
             + _chunk(b"tEXt", f"Description\0MOCK synthetic fixture {capture_id}".encode("ascii"))
             + _chunk(b"IDAT", zlib.compress(raw))
             + _chunk(b"IEND", b""))
    return _image_record(image, mission_id, visit_index, destination_id, ordinal, timestamp, generation,
                         "synthetic_fixture")


def _image_record(image, mission_id, visit_index, destination_id, ordinal, timestamp, generation, source):
    capture_id = f"{mission_id}:{visit_index}:{ordinal}"
    tag = int(destination_id[-1])
    width, height = struct.unpack_from(">II", image, 16)
    digest = hashlib.sha256(image).hexdigest()
    return dict(
        capture_id=capture_id, mission_id=mission_id, visit_index=visit_index,
        destination_id=destination_id, monitor_id=f"monitor-{tag}", arrival_confirmed=True,
        image_base64=base64.b64encode(image).decode("ascii"), content_type="image/png",
        captured_at_unix_ms=timestamp, frame_generation=generation,
        frame_id=visit_index * 2 + ordinal, simulated=True, capture_source=source,
        fixture_sha256=digest, sha256=digest, image_sha256=digest,
        fixture_variant=f"synthetic-{ordinal}" if source == "synthetic_fixture" else f"monitor-{tag}",
        repeated_fixture=source == "monitor_fixture" and ordinal == 2, width=width, height=height,
        aircraft_exposure_timestamp_available=False, tv_visibility_verified=False,
        framing_mode="simulated_fixture", physical_stop_confirmed=False,
    )


@dataclass
class _Runtime:
    work_at: float | None
    lease_at: float | None
    step: int = 0
    stop_at: float | None = None
    landing_at: float | None = None
    airborne: bool = False
    connected: bool = True
    cancelled: bool = False
    relinquished: bool = False


class ContractMockTransport:
    """Async callable compatible with DroneClient, isolated even for equal callers.

    Timing options use milliseconds and match the standalone mock. Only owning
    get_mission reads renew leases. Faults never imply real device failure or
    physical stop. simulate_landing is a test-control method, not an eighth tool.
    """

    def __init__(self, caller_id, *, scenario="nominal", step_ms=200, landing_ms=500,
                 lease_ms=10000, max_missions=128, max_requests=1024,
                 clock=time.monotonic, wall_clock=time.time, capture_source="synthetic_fixture"):
        if not _valid_id(caller_id):
            raise ValueError("Invalid mock caller_id")
        if type(scenario) is not str or scenario not in SCENARIOS:
            raise ValueError("Unknown mock scenario")
        if capture_source not in {"synthetic_fixture", "monitor_fixture"}:
            raise ValueError("Unknown mock capture source")
        self._camera = FixtureCamera() if capture_source == "monitor_fixture" else None
        self.capture_source = capture_source
        for name, value, maximum in (
                ("step_ms", step_ms, 30000), ("landing_ms", landing_ms, 30000),
                ("lease_ms", lease_ms, 60000), ("max_missions", max_missions, 128),
                ("max_requests", max_requests, 1024)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"Invalid {name}")
        self.caller_id, self.scenario = caller_id, scenario
        self.step_seconds, self.landing_seconds, self.lease_seconds = (
            step_ms / 1000, landing_ms / 1000, lease_ms / 1000)
        self.max_missions, self.max_requests = max_missions, max_requests
        self._clock = clock
        self._epoch, self._wall_epoch = clock(), wall_clock()
        self._missions = {}
        self._admissions = {}
        self._current = None
        self._runtime = None
        self._run_count = 0
        self._dropped_ack = self._closed = False

    def readiness(self):
        return self._camera.readiness() if self._camera else None

    def _timestamp(self, at):
        return max(1, int((self._wall_epoch + at - self._epoch) * 1000))

    def _touch(self, mission, at):
        mission["updated_at_unix_ms"] = self._timestamp(at)

    def _public(self, mission):
        return copy.deepcopy({key: value for key, value in mission.items() if key != "captures"})

    def _owned(self, mission_id):
        mission = self._missions.get(mission_id)
        if mission is None:
            _fail("NOT_FOUND", "Mission not found in this mock session")
        if mission["caller_id"] != self.caller_id:
            _fail("OWNER_MISMATCH", "Use the original mission caller")
        return mission

    def _check_caller(self, caller_id):
        if not _valid_id(caller_id):
            _fail("INVALID_REQUEST_CONTEXT", "Invalid caller identifier")
        if caller_id != self.caller_id:
            _fail("OWNER_MISMATCH", "Transport belongs to another mock caller")
        if self._closed:
            _fail("SERVICE_SHUTTING_DOWN", "Mock session is closed")

    def _land(self, mission, at):
        mission.update(ground_verified=True, verification_pending=False,
                       state="failed" if mission.get("error_code") else
                       "completed" if mission["route_completed"] and not mission["stop_requested"] else "stopped")
        self._touch(mission, at)
        self._current = self._runtime = None

    def _relinquish(self, mission, state, at):
        runtime = self._runtime
        runtime.work_at = runtime.lease_at = None
        runtime.relinquished = True
        mission.update(state=state, verification_pending=True)
        self._touch(mission, at)
        if self.scenario not in {"manual-landing", "connection-loss"}:
            runtime.landing_at = at + self.landing_seconds

    def _stop(self, mission, reason, at):
        if self._current != mission["mission_id"] or mission["stop_requested"]:
            return
        runtime = self._runtime
        runtime.work_at = runtime.lease_at = runtime.landing_at = None
        runtime.cancelled = True
        runtime.stop_at = at + self.step_seconds
        mission.update(stop_requested=True, stop_reason=reason, state="stop_requested")
        self._touch(mission, at)

    def _step(self, mission, at):
        runtime = self._runtime
        index = runtime.step
        runtime.step += 1
        runtime.work_at = at + self.step_seconds
        if index == 0:
            mission["state"] = "preflight"
        elif index == 1:
            if self.scenario == "preflight-failure":
                mission["error_code"] = "PREFLIGHT_FAILED"
                self._land(mission, at)
            else:
                runtime.airborne = True
                mission["state"] = "taking_off"
        elif index == 2:
            mission["state"] = "running"
        elif index < 15:
            visit_index, action = divmod(index - 3, 4)
            visit = mission["visits"][visit_index]
            if action == 0:
                visit["state"] = "moving"
            elif action == 1:
                if self.scenario == "connection-loss":
                    runtime.connected = False
                    mission["error_code"] = "CONNECTION_LOST"
                    self._relinquish(mission, "outcome_unknown", at)
                else:
                    visit.update(state="arrived", arrival_confirmed=True)
                    mission["visited_ids"].append(int(visit["destination_id"][-1]))
            elif self.scenario == "camera-unavailable":
                mission["error_code"] = "CAMERA_UNAVAILABLE"
                self._relinquish(mission, "failed", at)
            else:
                if self._camera:
                    try:
                        image = self._camera.read_image(f"monitor-{visit['destination_id'][-1]}")
                    except CaptureError:
                        mission["error_code"] = "CAMERA_UNAVAILABLE"
                        self._relinquish(mission, "failed", at)
                        self._touch(mission, at)
                        return
                    capture = _image_record(image, mission["mission_id"], visit_index, visit["destination_id"],
                                            action - 1, self._timestamp(at), self._run_count, self.capture_source)
                else:
                    capture = _capture(mission["mission_id"], visit_index, visit["destination_id"],
                                       action - 1, self._timestamp(at), self._run_count)
                mission["captures"].append(capture)
                visit["capture_ids"].append(capture["capture_id"])
                visit["state"] = "captured"
        elif index == 15:
            mission["state"] = "returning"
        else:
            mission["visited_ids"].append(6)
            mission["route_completed"] = all(len(v["capture_ids"]) == 2 for v in mission["visits"])
            self._relinquish(mission, "awaiting_rc_landing", at)
        self._touch(mission, at)

    def _advance(self, now):
        # Process deadlines chronologically, including expiry before a late read.
        # At most 17 route steps and stop/landing events exist per active mission.
        while self._current is not None:
            runtime = self._runtime
            events = [(at, priority, name) for priority, (name, at) in enumerate((
                ("lease", runtime.lease_at), ("work", runtime.work_at),
                ("stop", runtime.stop_at), ("landing", runtime.landing_at)))
                if at is not None]
            if not events:
                return
            at, _, event = min(events)
            if at > now:
                return
            mission = self._missions[self._current]
            if event == "lease":
                self._stop(mission, "caller_lease_expired", at)
            elif event == "work":
                self._step(mission, at)
            elif event == "stop":
                runtime.stop_at = None
                if not runtime.connected:
                    self._relinquish(mission, "outcome_unknown", at)
                elif runtime.airborne:
                    self._relinquish(mission, "awaiting_rc_landing", at)
                else:
                    self._land(mission, at)
            else:
                self._land(mission, at)

    def _status(self):
        mission, runtime = self._missions.get(self._current), self._runtime
        connected = runtime.connected if runtime else True
        airborne = runtime.airborne if runtime else False
        controlled = airborne and not runtime.relinquished if runtime else False
        return dict(
            connected=connected, ground_verified=mission["ground_verified"] if mission else True,
            is_flying=airborne if connected else None, are_motors_on=airborne if connected else None,
            vs_enabled=controlled if connected else None,
            authority="UNKNOWN" if not connected else "MSDK" if controlled else "RC",
            simulated=True, physical_stop_confirmed=False, hardware_connected=False,
            state_source="contract_mock_simulation", flight_phase=mission["state"] if mission else "idle",
            visited_ids=list(mission["visited_ids"]) if mission else [],
            route_completed=mission["route_completed"] if mission else False,
        )

    def _dispatch(self, name, args, request_id, now):
        if name == "drone_get_capabilities":
            return _success(
                live_ready=False, mock_capture_ready=self.readiness() is None, expected_mode_guard=True,
                capture_source=self.capture_source,
                readiness_issues=[], adapter="contract-mock", profile_id=PROFILE_ID,
                site_revision=SITE_REVISION, home_tag_id=6, floor_tag_id=0, target_height_m=1.5,
                tools=list(_SCHEMAS),
                destinations=[dict(destination_id=dest, monitor_id=f"monitor-{i}",
                                   physical_definition=dict(type="apriltag", marker_id=i))
                              for i, dest in enumerate(_DESTINATIONS, 1)],
                supported_ordered_sequences=[list(route) for route in _ROUTES],
            )
        if name == "drone_get_sensor_snapshot":
            return _success(snapshot=dict(self._status(), ranges=None),
                            sensor_semantics="Synthetic state; ranges unavailable, no object classes")
        if name == "drone_get_status":
            mission = self._missions.get(self._current)
            return _success(**self._status(), active_mission_id=self._current,
                            active_mission=self._public(mission) if mission else None,
                            active_request=dict(caller_id=self.caller_id, request_id=mission["request_id"],
                                                mission_id=self._current) if mission else None)
        if name == "_lookup_request":
            original = self._admissions.get(request_id)
            if original is None:
                _fail("NOT_FOUND", "No admission for this caller/request")
            return copy.deepcopy(original[1])
        if name in {"drone_get_mission", "drone_get_captures"}:
            mission = self._owned(args["mission_id"])
            if name == "drone_get_captures":
                return _success(mission_id=mission["mission_id"], captures=copy.deepcopy(mission["captures"]))
            runtime = self._runtime
            if (self._current == mission["mission_id"] and not runtime.cancelled
                    and not runtime.relinquished):
                runtime.lease_at = now + self.lease_seconds
            return _success(mission=self._public(mission))
        fingerprint = json.dumps([name, args], sort_keys=True, separators=(",", ":"))
        previous = self._admissions.get(request_id)
        if previous:
            if previous[0] != fingerprint:
                _fail("IDEMPOTENCY_CONFLICT", "Request ID has different intent")
            return copy.deepcopy(previous[1])
        if len(self._admissions) >= self.max_requests:
            _fail("HISTORY_LIMIT", "Request history is full; start a new mock session")
        if name == "drone_execute_route":
            if args["profile_id"] != PROFILE_ID or args["site_revision"] != SITE_REVISION:
                _fail("SITE_MISMATCH", "Read current capabilities before execution")
            if tuple(args["destination_ids"]) not in _ROUTES:
                _fail("UNSUPPORTED_ROUTE", "Visit each registered destination exactly once")
            if self._current is not None:
                _fail("MISSION_BUSY", "Current mission has not finished simulated landing")
            if len(self._missions) >= self.max_missions:
                _fail("HISTORY_LIMIT", "Mission history is full; start a new mock session")
            mission_id = str(uuid4())
            mission = dict(
                mission_id=mission_id, caller_id=self.caller_id, request_id=request_id,
                **copy.deepcopy(args), state="accepted", execution_mode="mock", physical_execution=False,
                simulated=True, stop_requested=False, physical_stop_confirmed=False,
                verification_pending=False, ground_verified=False, route_completed=False,
                visited_ids=[6], updated_at_unix_ms=self._timestamp(now), captures=[],
                visits=[dict(visit_index=i, destination_id=dest, state="pending",
                             arrival_confirmed=False, capture_ids=[])
                        for i, dest in enumerate(args["destination_ids"])],
            )
            self._missions[mission_id] = mission
            self._current = mission_id
            self._run_count += 1
            self._runtime = _Runtime(now + self.step_seconds, now + self.lease_seconds)
            response = _success(mission=self._public(mission))
        else:
            mission = self._owned(args["mission_id"])
            self._stop(mission, "caller_requested", now)
            response = _success(mission=self._public(mission),
                                stop_requested=mission["stop_requested"], physical_stop_confirmed=False)
        self._admissions[request_id] = (fingerprint, copy.deepcopy(response))
        return response

    async def __call__(self, name, envelope):
        # No await between admission and its durable-in-session lookup record.
        try:
            if type(name) is not str or name not in {*_SCHEMAS, "_lookup_request"}:
                _fail("NOT_FOUND", "Unknown mock tool")
            if type(envelope) is not dict or set(envelope) != {"arguments", "caller_id", "request_id"}:
                _fail("INVALID_ARGUMENT", "Expected arguments, caller_id and request_id only")
            self._check_caller(envelope["caller_id"])
            if not _valid_id(envelope["request_id"]):
                _fail("INVALID_REQUEST_CONTEXT", "Invalid request identifier")
            schema = (_SCHEMAS[name] if name != "_lookup_request" else
                      dict(type="object", required=[], properties={}))
            _validate_arguments(schema, envelope["arguments"])
            now = self._clock()
            self._advance(now)
            response = self._dispatch(name, envelope["arguments"], envelope["request_id"], now)
        except _MockError as exc:
            return _error(exc)
        if (self.scenario == "lost-ack" and name == "drone_execute_route"
                and not self._dropped_ack):
            self._dropped_ack = True
            raise TimeoutError("MOCK lost acknowledgement; lookup original caller/request, never replay")
        return response

    async def simulate_landing(self, mission_id, *, caller_id):
        """Explicit nonphysical RC/reset fixture for a relinquished owned mission."""
        try:
            self._check_caller(caller_id)
            _validate_arguments(_SCHEMAS["drone_get_mission"], {"mission_id": mission_id})
            now = self._clock()
            self._advance(now)
            mission = self._owned(mission_id)
            if (self._current != mission_id or not self._runtime.relinquished
                    or mission["state"] not in {"awaiting_rc_landing", "outcome_unknown", "failed"}):
                _fail("INVALID_STATE", "Only the current relinquished simulated mission may land")
            self._land(mission, now)
            return _success(mission_id=mission_id, action="simulated_rc_landing",
                            simulated=True, ground_verified=True, physical_stop_confirmed=False)
        except _MockError as exc:
            return _error(exc)

    def close(self):
        """Discard session memory; no background resources exist to terminate."""
        self._closed = True
        self._current = self._runtime = None
        self._missions.clear()
        self._admissions.clear()


def create_transport(caller_id, **options):
    """Return a fresh async callable; options are ContractMockTransport keywords."""
    return ContractMockTransport(caller_id, **options)
