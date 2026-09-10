"""Backend-only seven-tool HTTP boundary. Writes are never automatically replayed."""
from __future__ import annotations

import asyncio
import copy
from http.client import HTTPException
import json
from pathlib import Path
import re
from urllib import error, request
from urllib.parse import quote, urlsplit
from uuid import uuid4

try:
    from . import config
except ImportError:
    import config

SCHEMAS = {t["name"]: t["parameters"] for t in json.loads((Path(__file__).resolve().parents[1]
    / "drone-control/integration/speech_control_contract/tools.json").read_text("utf-8"))}
WRITES = {"drone_execute_route", "drone_stop_mission"}
MAX_RESPONSE_BYTES = 40 * 1024 * 1024  # At most six bounded PNG captures, base64 encoded.


class DroneError(Exception):
    def __init__(self, code, message="드론 제어 요청을 완료하지 못했습니다."):
        self.code = code
        super().__init__(message)


def validate_arguments(name, arguments):
    if type(name) is not str or name not in SCHEMAS or type(arguments) is not dict:
        raise DroneError("INVALID_ARGUMENTS")
    schema = SCHEMAS[name]
    if set(arguments) != set(schema["required"]):
        raise DroneError("INVALID_ARGUMENTS")
    for key, value in arguments.items():
        spec = schema["properties"][key]
        if spec["type"] == "array":
            if type(value) is not list or not spec["minItems"] <= len(value) <= spec["maxItems"]:
                raise DroneError("INVALID_ARGUMENTS")
            values, spec = value, spec["items"]
        else:
            values = [value]
        for item in values:
            if (type(item) is not str or not spec["minLength"] <= len(item) <= spec["maxLength"]
                    or re.fullmatch(r"[A-Za-z0-9_-]+", item) is None):
                raise DroneError("INVALID_ARGUMENTS")


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DroneClient:
    def __init__(self, caller_id, *, base_url=None, token=None, timeout=None, transport=None,
                 expected_mode=None):
        self.base_url = config.DRONE_CONTROL_API_URL if base_url is None else base_url
        self._token = config.DRONE_CONTROL_API_TOKEN if token is None else token
        self.timeout = config.DRONE_CONTROL_TIMEOUT_SECONDS if timeout is None else timeout
        parts = urlsplit(self.base_url)
        if (parts.scheme != "http" or parts.hostname != "127.0.0.1" or parts.port != 8766
                or parts.path not in ("", "/") or parts.query or parts.fragment or parts.username):
            raise DroneError("INVALID_CONFIGURATION", "드론 API는 로컬 127.0.0.1:8766 주소로 설정해야 합니다.")
        if type(caller_id) is not str or not 1 <= len(caller_id) <= 128:
            raise DroneError("INVALID_REQUEST_CONTEXT")
        self.caller_id = caller_id
        self.expected_mode = expected_mode or config.DRONE_CONTROL_MODE
        self._hub = None
        self._remote_generation = None
        if transport is not None:
            self._transport = transport
        elif config.DRONE_CONTROL_TRANSPORT == "remote":
            try:
                from .device_hub import get_device_hub
            except ImportError:
                from device_hub import get_device_hub
            self._hub = get_device_hub()
            self._transport = self._remote
        elif config.DRONE_CONTROL_TRANSPORT == "local":
            self._transport = self._http
        else:
            raise DroneError("INVALID_CONFIGURATION", "DRONE_CONTROL_TRANSPORT는 local 또는 remote여야 합니다.")
        self._writes = {}

    def readiness(self):
        if self._hub is not None:
            return None if self._hub.connected else "원격 PC가 연결되어 있지 않습니다. PC 커넥터를 확인하세요."
        return None if self._token else "DRONE_CONTROL_API_TOKEN을 relay와 PC 서비스에 설정해야 합니다."

    async def _remote(self, name, envelope):
        connection = self._hub.connection
        if connection is None or not connection.ready:
            raise DroneError("REMOTE_UNAVAILABLE")
        if self._remote_generation is None:
            self._remote_generation = connection.generation
        elif self._remote_generation != connection.generation:
            # Reconnection is a new device session, not permission to renew an old
            # aircraft owner's lease or resume a suspended mission.
            raise DroneError("REMOTE_SESSION_LOST")
        return await self._hub.request(name, envelope, timeout=self.timeout)

    async def _http(self, name, envelope):
        def send():
            lookup = name == "_lookup_request"
            camera = name.startswith("_camera_")
            path = ("/requests/" + quote(self.caller_id, safe="") + "/" + quote(envelope["request_id"], safe="")
                if lookup else "/camera/" + name.removeprefix("_camera_") if camera else "/tools/" + name)
            req = request.Request(self.base_url.rstrip("/") + path,
                data=None if lookup else json.dumps(envelope, separators=(",", ":")).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + self._token,
                         "X-Drone-Expected-Mode": self.expected_mode},
                method="GET" if lookup else "POST")
            # No environment proxy or redirect may forward the backend bearer token.
            opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
            try:
                response = opener.open(req, timeout=self.timeout)
            except error.HTTPError as exc:
                response = exc
            with response:
                limit = 768 * 1024 if camera else MAX_RESPONSE_BYTES
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise DroneError("INVALID_RESPONSE")
                value = json.loads(body)
                if (type(value) is not dict or value.get("execution_mode") != self.expected_mode
                        or (self.expected_mode == "mock" and value.get("physical_execution") is not False)):
                    raise DroneError("MODE_MISMATCH")
                return value
        return await asyncio.to_thread(send)

    async def _request(self, name, args, request_id):
        try:
            value = await asyncio.wait_for(self._transport(name, {
                "arguments": copy.deepcopy(args), "caller_id": self.caller_id, "request_id": request_id}), self.timeout)
        except DroneError as exc:
            raise DroneError("OUTCOME_UNKNOWN" if name in WRITES else exc.code) from None
        except (TimeoutError, OSError, error.URLError, HTTPException):
            raise DroneError("OUTCOME_UNKNOWN" if name in WRITES else "TRANSPORT_ERROR",
                "드론 명령 결과를 확인하지 못했습니다. 자동 재전송하지 않습니다.") from None
        except (ValueError, TypeError):
            raise DroneError("OUTCOME_UNKNOWN" if name in WRITES else "INVALID_RESPONSE") from None
        if (type(value) is not dict or type(value.get("schema_version")) is not int or value["schema_version"] != 1
                or type(value.get("ok")) is not bool
                or value.get("execution_mode") not in ("mock", "live")
                or type(value.get("physical_execution")) is not bool):
            raise DroneError("OUTCOME_UNKNOWN" if name in WRITES else "INVALID_RESPONSE")
        if not value["ok"]:
            detail = value.get("error") or {}
            code = detail.get("code") if type(detail) is dict else None
            raise DroneError(code if type(code) is str else "INVALID_RESPONSE")
        return copy.deepcopy(value)

    async def camera(self, action):
        if action not in {"start", "frame", "stop"}:
            raise DroneError("INVALID_ARGUMENTS")
        if self.readiness():
            raise DroneError("INVALID_CONFIGURATION", self.readiness())
        return await self._request("_camera_" + action, {}, str(uuid4()))

    async def lookup_request(self, request_id):
        """Read admission by the original caller/business intent; never execute it."""
        if type(request_id) is not str or not 1 <= len(request_id) <= 128:
            raise DroneError("INVALID_REQUEST_CONTEXT")
        return await self._request("_lookup_request", {}, request_id)

    async def call(self, name, arguments, *, request_id=None):
        validate_arguments(name, arguments)
        if name not in WRITES:
            if self.readiness():
                raise DroneError("INVALID_CONFIGURATION", self.readiness())
            return await self._request(name, arguments, request_id or str(uuid4()))
        if type(request_id) is not str or not 1 <= len(request_id) <= 128:
            raise DroneError("INVALID_REQUEST_CONTEXT")
        fingerprint = json.dumps([name, arguments], sort_keys=True, separators=(",", ":"))
        previous = self._writes.get(request_id)
        if previous and previous[0] != fingerprint:
            raise DroneError("IDEMPOTENCY_CONFLICT")
        if previous is None:
            if self.readiness():
                raise DroneError("INVALID_CONFIGURATION", self.readiness())
            task = asyncio.create_task(self._request(name, copy.deepcopy(arguments), request_id))
            self._writes[request_id] = (fingerprint, task)
        else:
            task = previous[1]
        # Cancellation of the browser task cannot erase an in-flight write result.
        return copy.deepcopy(await asyncio.shield(task))
