"""Bounded private RPC contract. Business identities are not transport identities."""
from __future__ import annotations

import json
import re
from typing import Any

try:
    from .drone_client import DroneError, MAX_RESPONSE_BYTES, SCHEMAS, validate_arguments
except ImportError:
    from drone_client import DroneError, MAX_RESPONSE_BYTES, SCHEMAS, validate_arguments

MAX_REQUEST_BYTES = 64 * 1024
MAX_INFLIGHT = 32
MAX_RPC_ID = 2**53 - 1
OPERATIONS = frozenset(SCHEMAS) | {"_lookup_request", "_camera_start", "_camera_frame", "_camera_stop"}
IDENTITY = re.compile(r"[A-Za-z0-9_-]{1,128}")
GENERATION = re.compile(r"[a-f0-9]{32}")


def identity(value: Any) -> bool:
    return type(value) is str and IDENTITY.fullmatch(value) is not None


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise DroneError("INVALID_RPC")
        result[key] = value
    return result


def decode(raw: str, limit: int) -> dict[str, Any]:
    if type(raw) is not str or len(raw) > limit or len(raw.encode("utf-8")) > limit:
        raise DroneError("INVALID_RPC")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, RecursionError):
        raise DroneError("INVALID_RPC") from None
    if type(value) is not dict:
        raise DroneError("INVALID_RPC")
    return value


def encode(value: dict[str, Any], limit: int) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise DroneError("INVALID_RPC") from None
    if len(raw) > limit:
        raise DroneError("INVALID_RPC")
    return raw


def validate_envelope(operation: str, envelope: Any) -> None:
    if (type(operation) is not str or operation not in OPERATIONS
            or type(envelope) is not dict or set(envelope) != {"arguments", "caller_id", "request_id"}
            or not identity(envelope["caller_id"]) or not identity(envelope["request_id"])):
        raise DroneError("INVALID_RPC")
    if operation in SCHEMAS:
        validate_arguments(operation, envelope["arguments"])
    elif type(envelope["arguments"]) is not dict or envelope["arguments"]:
        raise DroneError("INVALID_RPC")


def validate_request(value: dict[str, Any], generation: str, previous_id: int) -> None:
    if (set(value) != {"type", "generation", "rpc_id", "operation", "envelope"}
            or value["type"] != "rpc.request" or value["generation"] != generation
            or type(value["rpc_id"]) is not int or not previous_id < value["rpc_id"] <= MAX_RPC_ID):
        raise DroneError("INVALID_RPC")
    validate_envelope(value["operation"], value["envelope"])


def validate_result(value: Any, mode: str) -> None:
    if (type(value) is not dict or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1 or type(value.get("ok")) is not bool
            or value.get("execution_mode") != mode
            or type(value.get("physical_execution")) is not bool
            or (mode == "mock" and value["physical_execution"])):
        raise DroneError("MODE_MISMATCH")


def failure(code: str, mode: str) -> dict[str, Any]:
    return {"schema_version": 1, "ok": False, "execution_mode": mode,
            "physical_execution": False, "error": {"code": code}}
