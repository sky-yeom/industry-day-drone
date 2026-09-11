"""In-memory simulator only; contains no flight driver or network implementation."""
import asyncio
import copy
import re
from contract import ROUTES, error, response, valid_request_id, validate

PROFILE = "trial-23132-v1"
SITE = "mock-site-r1"
SEQUENCE = ["tag-2", "tag-3", "tag-1", "tag-3", "tag-2"]


class MockService:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._missions = {}
        self._requests = {}
        self._active = None

    async def request(self, method, path, body, *, caller_id=None, request_id=None):
        # Reconstruct and validate arguments at the service boundary independently.
        tool, args = None, copy.deepcopy(body)
        if type(args) is not dict:
            return error("INVALID_ARGUMENTS")
        for name, (verb, template) in ROUTES.items():
            pattern = re.escape(template).replace(re.escape("{mission_id}"), "([A-Za-z0-9_-]{1,64})")
            match = re.fullmatch(pattern, path) if type(path) is str else None
            if method == verb and match:
                tool = name
                if match.groups():
                    if "mission_id" in args:
                        return error("INVALID_ARGUMENTS")
                    args["mission_id"] = match.group(1)
                break
        try:
            validate(tool, args)
        except ValueError:
            return error("INVALID_ARGUMENTS")
        if method == "POST" and not (valid_request_id(caller_id) and valid_request_id(request_id)):
            return error("INVALID_REQUEST_CONTEXT")
        async with self._lock:
            key = (caller_id, request_id)
            fingerprint = (tool, args)
            if method == "POST" and key in self._requests:
                previous, result = self._requests[key]
                return copy.deepcopy(result) if previous == fingerprint else error("IDEMPOTENCY_CONFLICT")
            result = self._dispatch(tool, args)
            if method == "POST":
                self._requests[key] = copy.deepcopy((fingerprint, result))
            return copy.deepcopy(result)

    def _dispatch(self, tool, args):
        if tool == "drone_get_capabilities":
            return response(ok=True, live_ready=False, profile_id=PROFILE, site_revision=SITE,
                destinations=[{"destination_id": f"tag-{n}", "physical_definition": {
                    "type": "mock_fiducial_marker", "marker_id": n, "coordinates": None}}
                    for n in (1, 2, 3)], supported_ordered_sequences=[SEQUENCE])
        if tool == "drone_get_status":
            return response(ok=True, active_mission_id=self._active,
                flight_state="unknown", is_flying=None, are_motors_on=None,
                armed=None, vs_enabled=None, control_authority=None,
                battery_percent=None, observed_at=None, snapshot_age_ms=None)
        if tool == "drone_get_sensor_snapshot":
            return response(ok=True, observed_at=None, snapshot_age_ms=None,
                readings={"oa_horizontal_distances_mm": None,
                    "oa_horizontal_angle_interval_deg": None,
                    "oa_upward_distance_mm": None, "oa_downward_distance_mm": None,
                    "oa_obstacle_data_age_ms": None},
                unavailable_reason="mock_no_sensor_data")
        if tool == "drone_execute_route":
            if args["site_revision"] != SITE:
                return error("STALE_SITE_REVISION")
            if args["profile_id"] != PROFILE:
                return error("PROFILE_UNSUPPORTED")
            if args["destination_ids"] != SEQUENCE:
                return error("ROUTE_UNSUPPORTED")
            if self._active is not None:
                return error("DRONE_BUSY")
            mission_id = f"mock-mission-{len(self._missions) + 1}"
            mission = dict(args, mission_id=mission_id, state="accepted")
            self._missions[mission_id] = copy.deepcopy(mission)
            self._active = mission_id
            return response(ok=True, mission=mission)
        mission = self._missions.get(args["mission_id"])
        if mission is None:
            return error("MISSION_NOT_FOUND")
        if tool == "drone_stop_mission":
            if mission["state"] in ("accepted", "stop_requested"):
                mission["state"] = "stop_requested"
            return response(ok=True, mission=mission, physical_stop_confirmed=False)
        if tool == "drone_get_captures":
            return response(ok=True, mission_id=mission["mission_id"], captures=[])
        return response(ok=True, mission=mission)

    async def test_only_set_terminal(self, mission_id, state):
        """Test fixture operation, intentionally absent from the model tool registry."""
        if state not in ("completed", "cancelled", "failed"):
            raise ValueError("terminal state required")
        async with self._lock:
            self._missions[mission_id]["state"] = state
            if self._active == mission_id:
                self._active = None
