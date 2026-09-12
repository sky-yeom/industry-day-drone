"""Single-replica, device-bound outbound channel; no durable queue or write replay."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hmac
import re
from typing import Any
from uuid import uuid4

from starlette.websockets import WebSocket, WebSocketDisconnect

try:
    from . import config
    from .device_protocol import (MAX_INFLIGHT, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, MAX_RPC_ID,
        decode, encode, identity, validate_envelope, validate_result)
    from .drone_client import DroneError, WRITES
except ImportError:
    import config
    from device_protocol import (MAX_INFLIGHT, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, MAX_RPC_ID,
        decode, encode, identity, validate_envelope, validate_result)
    from drone_client import DroneError, WRITES


@dataclass
class Pending:
    caller_id: str
    request_id: str
    future: asyncio.Future
    abandoned: bool = False
    expiry: asyncio.TimerHandle | None = None


@dataclass
class Connection:
    socket: WebSocket
    generation: str = field(default_factory=lambda: uuid4().hex)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: dict[int, Pending] = field(default_factory=dict)
    counter: int = 0
    ready: bool = False


class DeviceHub:
    def __init__(self, device_id: str, token: str, mode: str):
        if (not identity(device_id) or type(token) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token) is None
                or type(mode) is not str or mode not in {"mock", "live"}):
            raise DroneError("INVALID_CONFIGURATION", "원격 장치 ID, 전용 인증 토큰과 실행 모드를 설정하세요.")
        self.device_id, self._token, self.mode = device_id, token, mode
        self.connection: Connection | None = None

    @property
    def connected(self) -> bool:
        return self.connection is not None and self.connection.ready

    def _authorized(self, socket: WebSocket) -> bool:
        headers = socket.headers
        names = ("authorization", "x-drone-device-id", "x-drone-execution-mode")
        if socket.query_params or any(len(headers.getlist(name)) != 1 for name in names):
            return False
        supplied = headers["authorization"].encode("utf-8")
        expected = ("Bearer " + self._token).encode("utf-8")
        return (hmac.compare_digest(supplied, expected)
                and hmac.compare_digest(headers["x-drone-device-id"].encode("utf-8"), self.device_id.encode("utf-8"))
                and headers["x-drone-execution-mode"] == self.mode)

    async def serve(self, socket: WebSocket) -> None:
        if not self._authorized(socket) or self.connection is not None:
            await socket.close(code=1008)
            return
        # Reserve before the first await, so simultaneous handshakes cannot take over.
        connection = Connection(socket)
        self.connection = connection
        try:
            await socket.accept()
            async with connection.lock:
                await socket.send_text(encode({"type": "device.ready", "generation": connection.generation,
                                              "execution_mode": self.mode}, MAX_REQUEST_BYTES))
                connection.ready = True
            while True:
                value = decode(await socket.receive_text(), MAX_RESPONSE_BYTES)
                if (set(value) != {"type", "generation", "rpc_id", "caller_id", "request_id", "result"}
                        or value["type"] != "rpc.response" or value["generation"] != connection.generation
                        or type(value["rpc_id"]) is not int):
                    raise DroneError("INVALID_RPC")
                pending = connection.pending.get(value["rpc_id"])
                if (pending is None or (pending.future.done() and not pending.abandoned)
                        or value["caller_id"] != pending.caller_id
                        or value["request_id"] != pending.request_id):
                    raise DroneError("INVALID_RPC")
                validate_result(value["result"], self.mode)
                if pending.abandoned:
                    connection.pending.pop(value["rpc_id"])
                    if pending.expiry:
                        pending.expiry.cancel()
                else:
                    pending.future.set_result(value["result"])
        except (WebSocketDisconnect, DroneError, OSError, RuntimeError, KeyError):
            pass  # Never log peer data, credentials, or frames.
        finally:
            await self.disconnect(connection)

    async def disconnect(self, connection: Connection) -> None:
        connection.ready = False
        # Detach before closing; callers cannot enqueue behind a dead connection.
        if self.connection is connection:
            self.connection = None
        for pending in connection.pending.values():
            if pending.expiry:
                pending.expiry.cancel()
            if not pending.future.done():
                pending.future.set_exception(DroneError("REMOTE_DISCONNECTED"))
        connection.pending.clear()
        try:
            async with asyncio.timeout(1):
                async with connection.lock:
                    await connection.socket.close(code=1008)
        except (TimeoutError, OSError, RuntimeError, WebSocketDisconnect):
            pass

    async def request(self, operation: str, envelope: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        validate_envelope(operation, envelope)
        connection = self.connection
        if connection is None or not connection.ready:
            raise DroneError("REMOTE_UNAVAILABLE", "원격 PC가 연결되어 있지 않습니다. PC 커넥터를 확인하세요.")
        if len(connection.pending) >= MAX_INFLIGHT or connection.counter >= MAX_RPC_ID:
            raise DroneError("REMOTE_BUSY")
        connection.counter += 1
        rpc_id = connection.counter
        future = asyncio.get_running_loop().create_future()
        pending = Pending(envelope["caller_id"], envelope["request_id"], future)
        connection.pending[rpc_id] = pending
        try:
            async with asyncio.timeout(timeout):
                async with connection.lock:
                    if self.connection is not connection or not connection.ready:
                        raise DroneError("REMOTE_DISCONNECTED")
                    await connection.socket.send_text(encode({
                        "type": "rpc.request", "generation": connection.generation, "rpc_id": rpc_id,
                        "operation": operation, "envelope": envelope}, MAX_REQUEST_BYTES))
                return await future
        except asyncio.CancelledError:
            if operation in WRITES:
                await self.disconnect(connection)
            elif self.connection is connection and connection.ready:
                # Closing a camera viewer must not sever a different mission's
                # channel. Retain a bounded tombstone and discard its one reply.
                pending.abandoned = True
                pending.expiry = asyncio.get_running_loop().call_later(
                    timeout + 1, connection.pending.pop, rpc_id, None)
            raise
        except TimeoutError:
            # A timed-out channel is abandoned, not drained/replayed on its successor.
            await self.disconnect(connection)
            raise
        except (OSError, RuntimeError, WebSocketDisconnect):
            await self.disconnect(connection)
            raise DroneError("REMOTE_DISCONNECTED") from None
        finally:
            if not pending.abandoned:
                connection.pending.pop(rpc_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()


_hub: DeviceHub | None = None
_settings: tuple | None = None


def get_device_hub() -> DeviceHub:
    global _hub, _settings
    if (config.DRONE_CONTROL_TRANSPORT != "remote" or not config.DRONE_REMOTE_SINGLE_REPLICA
            or config.DRONE_REMOTE_EXECUTION_MODE != config.DRONE_CONTROL_MODE):
        raise DroneError("INVALID_CONFIGURATION",
            "원격 연결은 실행 모드 일치와 DRONE_REMOTE_SINGLE_REPLICA=1 (min=1, max=1) 설정이 필요합니다.")
    settings = (config.DRONE_REMOTE_DEVICE_ID, config.DRONE_REMOTE_DEVICE_TOKEN,
                config.DRONE_REMOTE_EXECUTION_MODE)
    if _hub is None or _settings != settings:
        if _hub is not None and _hub.connection is not None:
            raise DroneError("INVALID_CONFIGURATION")
        _hub, _settings = DeviceHub(*settings), settings
    return _hub
