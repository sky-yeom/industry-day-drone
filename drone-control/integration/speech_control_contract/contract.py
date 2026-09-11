"""Mock-only speech boundary. Transport is supplied by trusted application code."""
import copy
import json
from pathlib import Path

TOOLS = json.loads(Path(__file__).with_name("tools.json").read_text(encoding="utf-8"))
SCHEMAS = {tool["name"]: tool["parameters"] for tool in TOOLS}
ROUTES = {
    "drone_get_capabilities": ("GET", "/v1/drone/capabilities"),
    "drone_get_status": ("GET", "/v1/drone/status"),
    "drone_execute_route": ("POST", "/v1/drone/missions"),
    "drone_get_mission": ("GET", "/v1/drone/missions/{mission_id}"),
    "drone_stop_mission": ("POST", "/v1/drone/missions/{mission_id}/stop"),
    "drone_get_sensor_snapshot": ("GET", "/v1/drone/sensors/snapshot"),
    "drone_get_captures": ("GET", "/v1/drone/missions/{mission_id}/captures"),
}


def response(**fields):
    status = fields.get("status", fields.get("mission", {}).get("state", "ok"))
    return copy.deepcopy(dict(fields, schema_version=1, status=status,
        execution_mode="mock", physical_execution=False))


def error(code):
    status = ("outcome_unknown" if code == "OUTCOME_UNKNOWN" else
              "unavailable" if code == "TRANSPORT_ERROR" else "rejected")
    return response(ok=False, status=status, error={"code": code})


def validate(tool, args):
    """Validate the small, explicitly supported schema vocabulary on both sides."""
    if type(tool) is not str or tool not in SCHEMAS or type(args) is not dict:
        raise ValueError("unknown tool or arguments")
    schema = SCHEMAS[tool]
    if set(args) != set(schema["required"]):
        raise ValueError("missing or unknown fields")
    for key, value in args.items():
        spec = schema["properties"][key]
        if spec["type"] == "array":
            if type(value) is not list or not spec["minItems"] <= len(value) <= spec["maxItems"]:
                raise ValueError("invalid array")
            values, spec = value, spec["items"]
        else:
            values = [value]
        for item in values:
            if type(item) is not str or not spec["minLength"] <= len(item) <= spec["maxLength"]:
                raise ValueError("invalid string")
            if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in item):
                raise ValueError("invalid identifier")


class Gateway:
    def __init__(self, transport):
        self.transport = transport

    async def call(self, tool, args, *, caller_id=None, request_id=None):
        """caller_id + request_id are trusted context, never model tool arguments."""
        try:
            validate(tool, args)
        except ValueError:
            return error("INVALID_ARGUMENTS")
        method, path = ROUTES[tool]
        payload = copy.deepcopy(args)
        if "mission_id" in payload:
            path = path.format(mission_id=payload.pop("mission_id"))
        if method == "POST" and not (valid_request_id(caller_id) and valid_request_id(request_id)):
            return error("INVALID_REQUEST_CONTEXT")
        try:
            result = await self.transport.request(method, path, payload, caller_id=caller_id, request_id=request_id)
            return copy.deepcopy(result)
        except TimeoutError:
            return error("OUTCOME_UNKNOWN" if method == "POST" else "TRANSPORT_ERROR")


def valid_request_id(value):
    return type(value) is str and 1 <= len(value) <= 128 and value.isascii() and value.isprintable()
