"""Durable admission and a single cancellable mission worker.

No SDK command is retried here. An unknown outcome retains the flight slot;
only independently fresh ground evidence permits another physical mission.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import itertools
import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .camera import VideoBroker


TOOLS = (
    "drone_get_capabilities", "drone_get_status", "drone_execute_route",
    "drone_get_mission", "drone_stop_mission", "drone_get_sensor_snapshot",
    "drone_get_captures",
)
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ToolError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ToolError("INVALID_ARGUMENT", "Invalid caller, request or mission identifier")
    return value


class MockAdapter:
    mode = "mock"
    live_ready = False
    profile_id = "mock-apriltag-corridor-v1"
    site_revision = "mock-1"
    destination_ids = ["tag-1", "tag-2", "tag-3"]
    home_tag_id = 6
    floor_tag_id = 0
    target_height_m = 1.4

    def status(self):
        return {"connected": True, "ground_verified": True, "is_flying": False,
                "are_motors_on": False, "vs_enabled": False, "authority": "RC",
                "simulated": True}

    def run(self, mission, cancel, emit):
        emit(state="running")
        for visit in mission["visits"]:
            if cancel.wait(.05):
                return self.stop()
            emit(visit_index=visit["visit_index"], visit_state="arrived", arrival_confirmed=True)
        return {"state": "completed", "physical_stop_confirmed": False,
                "ground_verified": True, "simulated": True}

    def stop(self):
        return {"state": "stopped", "physical_stop_confirmed": False,
                "ground_verified": True, "simulated": True}


class MissionService:
    def __init__(self, db_path, adapter=None, lease_seconds=10.):
        self.adapter = adapter or MockAdapter()
        self.mode = self.adapter.mode
        self.camera_broker = getattr(self.adapter, "video_broker", None)
        if self.camera_broker is None:
            self.camera_broker = VideoBroker.mock() if self.mode == "mock" else VideoBroker(None)
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.db = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY, body TEXT NOT NULL, occupied INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS requests(caller TEXT, request TEXT, fingerprint TEXT NOT NULL,
                response TEXT NOT NULL, PRIMARY KEY(caller,request));
        """)
        self.pending = None
        self.running_id = None
        self.cancel = threading.Event()
        self.closing = False
        self.lease_seconds = lease_seconds
        self.lease_deadline = None
        # Persisted work is evidence, never a queue to replay after restart.
        for mid, body in self.db.execute("SELECT id,body FROM missions WHERE occupied=1").fetchall():
            mission = json.loads(body)
            mission.update(state="outcome_unknown", interrupted=True,
                           verification_pending=True, physical_stop_confirmed=False)
            self._save(mission, True)
        self.worker = threading.Thread(target=self._run_worker, name="drone-mission-owner", daemon=True)
        self.worker.start()
        self.lease_worker = threading.Thread(target=self._watch_lease, name="drone-caller-lease", daemon=True)
        self.lease_worker.start()

    def _save(self, mission, occupied=None):
        mission["updated_at_unix_ms"] = int(time.time() * 1000)
        if occupied is None:
            row = self.db.execute("SELECT occupied FROM missions WHERE id=?", (mission["mission_id"],)).fetchone()
            occupied = row[0] if row else True
        self.db.execute("INSERT OR REPLACE INTO missions VALUES (?,?,?)",
                        (mission["mission_id"], json.dumps(mission, allow_nan=False), int(occupied)))

    def _get(self, mid):
        identifier(mid)
        row = self.db.execute("SELECT body FROM missions WHERE id=?", (mid,)).fetchone()
        if not row:
            raise ToolError("NOT_FOUND", "Mission not found")
        return json.loads(row[0])

    def _active(self):
        row = self.db.execute("SELECT body FROM missions WHERE occupied=1 LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None

    def _reconcile_ground(self, active, status):
        if active and self.running_id is None and self.pending is None and status.get("ground_verified") is True:
            active.update(verification_pending=False, ground_verified=True,
                          physical_stop_confirmed=self.mode == "live")
            if active["state"] == "awaiting_rc_landing":
                active["state"] = "completed"
            elif active["state"] in {"stop_requested", "outcome_unknown"}:
                active["state"] = "stopped"
            self._save(active, False)
            return None
        return active

    def _response(self, **body):
        return {"schema_version": 1, "ok": True, "status": "ok",
                "execution_mode": self.mode, "physical_execution": self.mode == "live", **body}

    def capabilities(self):
        ids = list(self.adapter.destination_ids)
        return self._response(live_ready=self.adapter.live_ready,
            profile_id=self.adapter.profile_id, site_revision=self.adapter.site_revision,
            home_tag_id=self.adapter.home_tag_id, floor_tag_id=self.adapter.floor_tag_id,
            target_height_m=self.adapter.target_height_m,
            tools=list(TOOLS), destinations=[{"destination_id": d,
                "monitor_id": "monitor-" + d.split("-")[-1],
                "physical_definition": {"type": "apriltag", "marker_id": int(d.split("-")[-1])}}
                for d in ids], supported_ordered_sequences=[list(p) for p in itertools.permutations(ids)])

    def lookup_request(self, caller, request):
        identifier(caller)
        identifier(request)
        with self.lock:
            row = self.db.execute("SELECT response FROM requests WHERE caller=? AND request=?",
                                  (caller, request)).fetchone()
            if not row:
                raise ToolError("NOT_FOUND", "No durable admission for this caller/request")
            return json.loads(row[0])

    def camera(self, action, arguments, caller_id, request_id):
        identifier(caller_id)
        identifier(request_id)
        if action not in {"start", "frame", "stop"} or not isinstance(arguments, dict) or arguments:
            raise ToolError("INVALID_ARGUMENT", "Expected camera start, frame or stop with empty arguments")
        with self.lock:
            if self.closing and action != "stop":
                raise ToolError("SERVICE_SHUTTING_DOWN", "Service is closing; camera requests are refused")
        return self._response(camera=self.camera_broker.camera(action, caller_id))

    def call(self, name, arguments, caller_id, request_id):
        identifier(caller_id)
        identifier(request_id)
        if name not in TOOLS or not isinstance(arguments, dict):
            raise ToolError("INVALID_ARGUMENT", "Unknown tool or invalid arguments")
        fields = {"drone_execute_route": {"profile_id", "site_revision", "destination_ids"},
                  "drone_get_mission": {"mission_id"}, "drone_stop_mission": {"mission_id"},
                  "drone_get_captures": {"mission_id"}}
        if set(arguments) != fields.get(name, set()):
            raise ToolError("INVALID_ARGUMENT", "Unexpected or missing tool argument")
        with self.lock:
            if self.closing and name == "drone_execute_route":
                raise ToolError("SERVICE_SHUTTING_DOWN", "Service is closing; new physical execution is refused")
            if name == "drone_get_capabilities":
                return self.capabilities()
            if name in {"drone_get_status", "drone_get_sensor_snapshot"}:
                status = self.adapter.status()  # immutable cache during active flight
                active = self._active()
                # The worker has relinquished control. An independent, current
                # pair of FC values can resolve a prior unknown or manual landing.
                active = self._reconcile_ground(active, status)
                if name == "drone_get_sensor_snapshot":
                    return self._response(snapshot=status, sensor_semantics="raw ranges; no object classes")
                return self._response(**status, active_mission_id=active["mission_id"] if active else None,
                    active_mission=self._public(active) if active else None, active_request={k: active[k] for k in ("caller_id", "request_id", "mission_id")} if active else None)
            if name in {"drone_get_mission", "drone_get_captures"}:
                mission = self._get(arguments["mission_id"])
                if mission["caller_id"] != caller_id:
                    raise ToolError("OWNER_MISMATCH", "Mission belongs to another caller")
                if name == "drone_get_captures":
                    return self._response(mission_id=mission["mission_id"], captures=mission.get("captures", []))
                if self.running_id == mission["mission_id"] or self.pending == mission["mission_id"]:
                    if not mission["stop_requested"]:
                        now = time.perf_counter()
                        if self.lease_deadline is not None and now >= self.lease_deadline:
                            mission.update(stop_requested=True, state="stop_requested", stop_reason="caller_lease_expired")
                            self._save(mission)
                            self.cancel.set()
                            self.lease_deadline = None
                        else:
                            self.lease_deadline = now + self.lease_seconds
                elif (active := self._active()) and active["mission_id"] == mission["mission_id"]:
                    self._reconcile_ground(active, self.adapter.status())
                    mission = self._get(mission["mission_id"])
                return self._response(mission=self._public(mission))
            fingerprint = hashlib.sha256(json.dumps([name, arguments], sort_keys=True, allow_nan=False).encode()).hexdigest()
            old = self.db.execute("SELECT fingerprint,response FROM requests WHERE caller=? AND request=?", (caller_id, request_id)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise ToolError("IDEMPOTENCY_CONFLICT", "Request ID already has different arguments")
                return json.loads(old[1])
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if name == "drone_execute_route":
                    response, queued = self._admit(arguments, caller_id, request_id)
                else:
                    response, queued = self._stop(arguments["mission_id"], caller_id), None
                self.db.execute("INSERT INTO requests VALUES (?,?,?,?)", (caller_id, request_id, fingerprint, json.dumps(response)))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            if queued:
                self.cancel.clear()
                self.lease_deadline = time.perf_counter() + self.lease_seconds
                self.pending = queued
                self.changed.notify()
            return response

    @staticmethod
    def _public(mission):
        return {k: v for k, v in mission.items() if k != "captures"}

    def _admit(self, args, caller, request):
        caps = self.capabilities()
        if args["profile_id"] != caps["profile_id"] or args["site_revision"] != caps["site_revision"]:
            raise ToolError("SITE_MISMATCH", "Profile/site revision changed; read capabilities again")
        if args["destination_ids"] not in caps["supported_ordered_sequences"]:
            raise ToolError("UNSUPPORTED_ROUTE", "Route must visit each registered destination exactly once")
        if self.mode == "live" and not self.adapter.live_ready:
            raise ToolError("LIVE_NOT_READY", "A configured physical profile is required")
        if self._active():
            raise ToolError("MISSION_BUSY", "A mission is active or awaits independent ground verification")
        mid = uuid.uuid4().hex
        mission = dict(mission_id=mid, caller_id=caller, request_id=request,
            profile_id=args["profile_id"], site_revision=args["site_revision"],
            state="accepted", destination_ids=list(args["destination_ids"]),
            execution_mode=self.mode, physical_execution=self.mode == "live",
            stop_requested=False, physical_stop_confirmed=False, verification_pending=False,
            visits=[dict(visit_index=i, destination_id=d, state="pending", arrival_confirmed=False, capture_ids=[])
                    for i, d in enumerate(args["destination_ids"])], captures=[])
        self._save(mission, True)
        return self._response(mission=self._public(mission)), mid

    def _stop(self, mid, caller):
        mission = self._get(mid)
        if mission["caller_id"] != caller:
            raise ToolError("OWNER_MISMATCH", "Stop requires the original mission caller")
        active = self._active()
        if active and active["mission_id"] == mid:
            mission.update(stop_requested=True, state="stop_requested")
            self._save(mission)
            self.cancel.set()
        return self._response(mission=self._public(mission), stop_requested=mission["stop_requested"],
                              physical_stop_confirmed=mission["physical_stop_confirmed"])

    def _emit(self, mid, **event):
        with self.lock:
            mission = self._get(mid)
            if "visit_index" in event:
                i = event.pop("visit_index")
                visit = mission["visits"][i]
                if "capture" in event:
                    capture = event.pop("capture")
                    if len(visit["capture_ids"]) >= 2 or capture.get("capture_id") in visit["capture_ids"]:
                        raise ToolError("INVALID_CAPTURE", "Only two distinct frames per visit are permitted")
                    encoded = capture.get("image_base64")
                    if not isinstance(encoded, str) or len(encoded) > 4 * ((4 * 1024 * 1024 + 2) // 3):
                        raise ToolError("INVALID_CAPTURE", "Capture exceeds its encoded size limit")
                    raw = base64.b64decode(capture["image_base64"], validate=True)
                    if not raw.startswith(b"\x89PNG\r\n\x1a\n") or len(raw) > 4 * 1024 * 1024:
                        raise ToolError("INVALID_CAPTURE", "Camera capture is not a bounded PNG")
                    if not visit["arrival_confirmed"] or capture.get("arrival_confirmed") is not True:
                        raise ToolError("INVALID_CAPTURE", "A confirmed arrival is required")
                    capture.update(mission_id=mid, visit_index=i, destination_id=visit["destination_id"],
                                   content_type="image/png", sha256=hashlib.sha256(raw).hexdigest())
                    mission["captures"].append(capture)
                    visit["capture_ids"].append(capture["capture_id"])
                if "visit_state" in event:
                    visit["state"] = event.pop("visit_state")
                visit.update(event)
            elif not mission["stop_requested"]:
                mission.update(event)
            self._save(mission)

    def _run_worker(self):
        try:
            self._work()
        finally:
            self.camera_broker.close()

    def _work(self):
        while True:
            with self.changed:
                self.changed.wait_for(lambda: self.pending is not None or self.closing)
                if self.closing and self.pending is None:
                    return
                mid, self.pending = self.pending, None
                self.running_id = mid
                mission = self._get(mid)
            try:
                if self.closing or self.cancel.is_set():
                    result = {"state": "stopped", "ground_verified": False, "physical_stop_confirmed": False,
                              "cancelled_before_execution": True}
                else:
                    result = self.adapter.run(copy.deepcopy(mission), self.cancel,
                                              lambda **event: self._emit(mid, **event))
            except BaseException as exc:
                result = {"state": "outcome_unknown", "verification_pending": True,
                          "physical_stop_confirmed": False, "error": str(exc)}
            with self.lock:
                mission = self._get(mid)
                mission.update(result)
                self._save(mission, not (result.get("ground_verified", False) or result.get("cancelled_before_execution", False)))
                self.running_id = None
                self.lease_deadline = None

    def _watch_lease(self):
        while True:
            with self.changed:
                if self.closing:
                    return
                if (self.lease_deadline is not None and time.perf_counter() >= self.lease_deadline
                        and (mid := self.running_id or self.pending)):
                    mission = self._get(mid)
                    mission.update(stop_requested=True, state="stop_requested", stop_reason="caller_lease_expired")
                    self._save(mission)
                    self.cancel.set()
                    self.lease_deadline = None
                self.changed.wait(timeout=.1)

    def begin_shutdown(self):
        with self.changed:
            self.closing = True
            if mid := self.running_id or self.pending:
                mission = self._get(mid)
                mission.update(stop_requested=True, state="stop_requested", stop_reason="service_shutdown")
                self._save(mission)
            self.cancel.set()
            self.changed.notify_all()

    def close(self):
        self.begin_shutdown()
        self.worker.join(timeout=8)
        self.lease_worker.join(timeout=1)
        # Do not close SQLite under a still-running physical worker.
        if not self.worker.is_alive():
            self.camera_broker.close()
            self.db.close()
