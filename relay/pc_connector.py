"""Outbound-only PC connector. Run: python -m relay.pc_connector (no aircraft on startup)."""
from __future__ import annotations

import asyncio
from http.client import HTTPException
import logging
import os
import re
from urllib import error, request
from urllib.parse import quote, urlsplit
from uuid import uuid4

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

try:
    from .device_protocol import (GENERATION, MAX_INFLIGHT, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES,
        decode, encode, failure, identity, validate_envelope, validate_request, validate_result)
    from .drone_client import DroneError, WRITES, _NoRedirect
except ImportError:
    from device_protocol import (GENERATION, MAX_INFLIGHT, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES,
        decode, encode, failure, identity, validate_envelope, validate_request, validate_result)
    from drone_client import DroneError, WRITES, _NoRedirect

log = logging.getLogger("relay.pc_connector")


class NoRedirectConnect(connect):
    def process_redirect(self, exc: Exception) -> Exception:
        return exc  # Never forward device credentials to a redirect target.


def validate_cloud_url(url: str, allowed_host: str, *, allow_loopback_test: bool = False) -> None:
    try:
        parts = urlsplit(url)
        local = allow_loopback_test and parts.hostname in {"127.0.0.1", "localhost", "::1"}
        if (not allowed_host or parts.hostname != allowed_host or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment or parts.path != "/ws/device"
                or (parts.scheme != "wss" and not (local and parts.scheme == "ws"))
                or (not local and parts.port not in (None, 443)) or parts.netloc.endswith(":")):
            raise ValueError()
    except ValueError:
        raise DroneError("INVALID_CONFIGURATION",
            "DRONE_RELAY_URL requires wss://<DRONE_RELAY_ALLOWED_HOST>/ws/device without queries.") from None


class LocalAPI:
    def __init__(self, token: str, mode: str, *, allow_live: bool = False,
                 timeout: float = 5.0, _test_target: tuple[str, int] | None = None):
        if (type(token) is not str or re.fullmatch(r"[\x21-\x7e]{24,512}", token) is None or type(mode) is not str
                or mode not in {"mock", "live"} or (mode == "live" and allow_live is not True)):
            raise DroneError("INVALID_CONFIGURATION",
                "A separate local API token, expected mode, and explicit allow-live gate are required.")
        target = ("127.0.0.1", 8766) if _test_target is None else _test_target
        if target[0] != "127.0.0.1" or type(target[1]) is not int or not 1 <= target[1] <= 65535:
            raise DroneError("INVALID_CONFIGURATION")
        self.base_url = f"http://127.0.0.1:{target[1]}"
        self._token, self.mode, self.timeout = token, mode, timeout

    async def call(self, operation: str, envelope: dict) -> dict:
        validate_envelope(operation, envelope)

        def send() -> dict:
            lookup = operation == "_lookup_request"
            camera = operation.startswith("_camera_")
            path = ("/requests/" + quote(envelope["caller_id"], safe="") + "/"
                    + quote(envelope["request_id"], safe="") if lookup
                    else "/camera/" + operation.removeprefix("_camera_") if camera else "/tools/" + operation)
            payload = None if lookup else encode(envelope, MAX_REQUEST_BYTES).encode("utf-8")
            req = request.Request(self.base_url + path, data=payload, method="GET" if lookup else "POST",
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + self._token,
                         "X-Drone-Expected-Mode": self.mode})
            opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
            try:
                response = opener.open(req, timeout=self.timeout)
            except error.HTTPError as exc:
                response = exc
            with response:
                limit = 768 * 1024 if camera else MAX_RESPONSE_BYTES - MAX_REQUEST_BYTES
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise DroneError("INVALID_RESPONSE")
                try:
                    value = decode(data.decode("utf-8"), limit)
                except UnicodeError:
                    raise DroneError("INVALID_RESPONSE") from None
                validate_result(value, self.mode)
                if response.status != 200 and value["ok"]:
                    raise DroneError("INVALID_RESPONSE")
                return value
        try:
            return await asyncio.wait_for(asyncio.to_thread(send), timeout=self.timeout + .1)
        except (TimeoutError, OSError, error.URLError, HTTPException):
            raise DroneError("LOCAL_TRANSPORT_ERROR") from None

    async def check_mode(self) -> None:
        result = await self.call("drone_get_capabilities", {
            "arguments": {}, "caller_id": "pc-connector-check", "request_id": str(uuid4())})
        if not result["ok"]:
            raise DroneError("LOCAL_API_UNAVAILABLE")
        # Mode binding must also be enforced atomically by the local HTTP handler.
        if result.get("expected_mode_guard") is not True:
            raise DroneError("LOCAL_MODE_GUARD_REQUIRED")


class PCConnector:
    def __init__(self, url: str, allowed_host: str, device_id: str, token: str, local: LocalAPI, *,
                 allow_loopback_test: bool = False):
        validate_cloud_url(url, allowed_host, allow_loopback_test=allow_loopback_test)
        if (not identity(device_id) or type(token) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token) is None
                or token == local._token):
            raise DroneError("INVALID_CONFIGURATION", "Device and local API credentials must be distinct.")
        self.url, self.device_id, self._token, self.local = url, device_id, token, local

    async def run_once(self) -> None:
        await self.local.check_mode()
        async with NoRedirectConnect(self.url, additional_headers={
                "Authorization": "Bearer " + self._token, "X-Drone-Device-ID": self.device_id,
                "X-Drone-Execution-Mode": self.local.mode},
                proxy=None, compression=None, max_size=MAX_REQUEST_BYTES, max_queue=MAX_INFLIGHT,
                open_timeout=10, close_timeout=1, ping_interval=2, ping_timeout=5) as socket:
            await self.serve(socket)

    async def serve(self, socket) -> None:
        ready = decode(await asyncio.wait_for(socket.recv(), 5), MAX_REQUEST_BYTES)
        if (set(ready) != {"type", "generation", "execution_mode"} or ready["type"] != "device.ready"
                or ready["execution_mode"] != self.local.mode or type(ready["generation"]) is not str
                or GENERATION.fullmatch(ready["generation"]) is None):
            raise DroneError("INVALID_RPC")
        generation = ready["generation"]
        previous_id = 0
        tasks: set[asyncio.Task] = set()
        viewers: set[str] = set()
        lock = asyncio.Lock()

        async def forward(value: dict) -> None:
            envelope, operation = value["envelope"], value["operation"]
            try:
                if operation == "_camera_start":
                    if len(viewers) >= MAX_INFLIGHT and envelope["caller_id"] not in viewers:
                        raise DroneError("REMOTE_BUSY")
                    viewers.add(envelope["caller_id"])
                result = await self.local.call(operation, envelope)
                if operation == "_camera_stop" and result["ok"]:
                    viewers.discard(envelope["caller_id"])
            except (DroneError, TimeoutError, OSError, error.URLError) as exc:
                code = exc.code if isinstance(exc, DroneError) else "LOCAL_TRANSPORT_ERROR"
                result = failure("OUTCOME_UNKNOWN" if operation in WRITES | {"_camera_start", "_camera_stop"}
                                 else code, self.local.mode)
                log.warning("Local request failed (%s); no replay", code)
            response = {"type": "rpc.response", "generation": generation, "rpc_id": value["rpc_id"],
                        "caller_id": envelope["caller_id"], "request_id": envelope["request_id"], "result": result}
            async with lock:
                await socket.send(encode(response, MAX_RESPONSE_BYTES))

        async def cleanup_viewer(caller: str) -> None:
            try:
                async with asyncio.timeout(1):
                    result = await self.local.call("_camera_stop",
                        {"arguments": {}, "caller_id": caller, "request_id": str(uuid4())})
                    if not result["ok"]:
                        raise DroneError("CAMERA_CLEANUP_FAILED")
            except (DroneError, TimeoutError, OSError, error.URLError):
                log.warning("Camera cleanup unacknowledged; local viewer lease must expire")

        try:
            while True:
                value = decode(await socket.recv(), MAX_REQUEST_BYTES)
                # Observe every completed writer error before accepting more work.
                completed = {task for task in tasks if task.done()}
                for task in completed:
                    task.result()
                tasks.difference_update(completed)
                validate_request(value, generation, previous_id)
                previous_id = value["rpc_id"]
                if len(tasks) >= MAX_INFLIGHT:
                    raise DroneError("REMOTE_BUSY")
                tasks.add(asyncio.create_task(forward(value)))
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if viewers:
                await asyncio.gather(*(cleanup_viewer(caller) for caller in viewers))
            # Never renew mission ownership here: loss expires the PC's 10-second lease.
            log.info("Device session ended; abandoned requests will not be replayed")

    async def run(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except DroneError as exc:
                log.warning("Device channel unavailable (%s); reconnecting session only, never requests", exc.code)
            except (WebSocketException, TimeoutError, OSError):
                log.warning("Device channel unavailable; reconnecting session only, never requests")
            await asyncio.sleep(2)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Keep third-party handshake logging from exposing authorization headers.
    logging.getLogger("websockets").setLevel(logging.CRITICAL)
    try:
        local = LocalAPI(os.getenv("DRONE_CONTROL_API_TOKEN", ""), os.getenv("DRONE_REMOTE_EXECUTION_MODE", ""),
                         allow_live=os.getenv("DRONE_CONNECTOR_ALLOW_LIVE") == "1")
        connector = PCConnector(os.getenv("DRONE_RELAY_URL", ""), os.getenv("DRONE_RELAY_ALLOWED_HOST", ""),
            os.getenv("DRONE_REMOTE_DEVICE_ID", ""), os.getenv("DRONE_REMOTE_DEVICE_TOKEN", ""), local,
            allow_loopback_test=os.getenv("DRONE_CONNECTOR_ALLOW_LOOPBACK_TEST") == "1")
        asyncio.run(connector.run())
    except DroneError as exc:
        raise SystemExit(f"Connector configuration refused ({exc.code}); check relay/.env.example gates.") from None
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
