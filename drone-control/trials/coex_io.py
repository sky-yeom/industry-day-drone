"""Explicit, bounded COEX I/O. Importing or constructing this module connects nowhere.

Receipt and deadline clocks use QueryPerformanceCounter via perf_counter on Windows.
The mission owns the control socket on its calling thread. A separate, read-only
worker may sample actual motor state; it never sends a flight command. SDK callback
ages and query completion times are receipt evidence, not sensor acquisition times.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import ipaddress
import math
from pathlib import Path
import socket
import threading
import time
from typing import Any
import uuid

from drone_nav.protocol import Message, NDJSONClient, Protocol
from drone_nav.sdk_api import SdkApiClient, parse_sdk_response


class ActionOutcomeUnknown(ConnectionError):
    """A write may have reached Android. Never replay it or automatically re-arm."""

    outcome_unknown = True


class MissingTelemetryError(ConnectionError):
    """The command was acknowledged, but its ACK supplied no usable snapshot."""

    command_acknowledged = True


class QueryLatchedError(ConnectionError):
    """A prior GET failed; this instance cannot issue another query."""


@dataclass(frozen=True)
class MotorsSnapshot:
    value: bool | None
    received_monotonic_s: float | None
    connection_epoch: int
    error: str | None


def _positive_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError("timeout must be a finite positive number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be a finite positive number")
    return float(value)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        raise TimeoutError("total request deadline exceeded")
    return remaining


def _connect_socket(host: str, port: int, deadline: float):
    # DNS resolution has no socket deadline. A verified phone IP is required so
    # connect + send + all partial reads really share one bounded request budget.
    address = ipaddress.ip_address(host)
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(_remaining(deadline))
        sock.connect((host, port))
        _remaining(deadline)
        return sock
    except BaseException:
        sock.close()
        raise


class _DeadlineSocket:
    """Recompute the remaining total budget before each socket operation."""

    def __init__(self, sock):
        self.sock = sock
        self.deadline = 0.0

    def settimeout(self, timeout):
        self.sock.settimeout(timeout)

    def sendall(self, data):
        self.sock.settimeout(_remaining(self.deadline))
        self.sock.sendall(data)
        _remaining(self.deadline)

    def recv(self, size):
        self.sock.settimeout(_remaining(self.deadline))
        result = self.sock.recv(size)
        _remaining(self.deadline)
        return result

    def shutdown(self, how):
        self.sock.shutdown(how)

    def close(self):
        self.sock.close()


class _LineReader:
    """A bounded raw reader, avoiding makefile.readline's per-read timeout reset."""

    def __init__(self, sock: _DeadlineSocket, limit: int = 1_048_576):
        self.sock = sock
        self.limit = limit
        self.buffer = b""
        self.received_monotonic_s: float | None = None

    def readline(self):
        while b"\n" not in self.buffer:
            if len(self.buffer) >= self.limit:
                raise ConnectionError("response line exceeds byte limit")
            chunk = self.sock.recv(min(65536, self.limit - len(self.buffer)))
            if not chunk:
                raise ConnectionError("bridge closed before a complete response")
            self.buffer += chunk
            self.received_monotonic_s = time.perf_counter()
        line, self.buffer = self.buffer.split(b"\n", 1)
        return line + b"\n"

    def close(self):
        self.buffer = b""


class ControlIO(NDJSONClient):
    """One explicit control connection, with no automatic reconnect or replay.

    ``timeout_s`` caps ordinary control requests at 0.4 seconds. Takeoff, arm and
    stick-mode selection have a 3-second cap, disarm a 1-second cap. A caller can
    shorten these deadlines, never extend them. ``command`` returns only the
    current ACK's copied telemetry plus reserved PC receipt/connection metadata.
    """

    QUERY_PERIOD_S = 0.5
    QUERY_DEADLINE_S = 0.4
    _COMMANDS = {"status", "zero", "velocity", "arm", "takeoff", "stick_mode", "disarm"}
    _READS = {"status"}
    _CAPS = {"takeoff": 3.0, "arm": 3.0, "stick_mode": 3.0, "disarm": 1.0}

    def __init__(self, host: str, port: int, query_port: int = 9997,
                 timeout_s: float = 0.4, log_dir: str | Path | None = None):
        ipaddress.ip_address(host)
        for number in (port, query_port):
            if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 65535:
                raise ValueError("port must be an integer in 1..65535")
        super().__init__(host, port, timeout_s=min(_positive_timeout(timeout_s), 0.4))
        self._query_port = query_port
        self._configured_log_dir = Path(log_dir) if log_dir is not None else None
        self._owner_id: int | None = None
        self._ever_connected = False
        self._control_failed = False
        self._connection_epoch = 0
        self._raw_snapshot: dict[str, Any] | None = None
        self._generation_seen = False
        self._generation = None
        self._secrets: set[str] = set()
        self._log_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._query_lock = threading.Lock()
        self._query_failure: str | None = None
        self._motors = MotorsSnapshot(None, None, 0, "not_sampled")
        self._motors_stop = threading.Event()
        self._motors_thread: threading.Thread | None = None

    def _assert_owner(self):
        if self._owner_id != threading.get_ident():
            raise RuntimeError("control I/O must stay on the thread that called connect")

    def connect(self):
        if self._ever_connected:
            raise RuntimeError("automatic reconnect is forbidden; create a new operator-owned run")
        if self._owner_id is not None:
            self._assert_owner()
        with self._state_lock:
            if self._query_failure:
                raise QueryLatchedError(self._query_failure)
        self._ever_connected = True
        self._owner_id = threading.get_ident()
        self._open_session_log()
        try:
            raw = _connect_socket(*self._address, time.perf_counter() + self._timeout)
            self._socket = _DeadlineSocket(raw)
            self._file = _LineReader(self._socket)
            self._connection_epoch += 1
            with self._state_lock:
                self._motors = MotorsSnapshot(None, None, self._connection_epoch,
                                              "awaiting_connection_bound_query")
            self._log_event("network_connected", {"address": list(self._address),
                                                  "connection_epoch": self._connection_epoch})
        except BaseException as error:
            self._control_failed = True
            self._log_event("connection_failed", {"error": type(error).__name__})
            self.close()
            raise

    def _open_session_log(self):
        if self._configured_log_dir is None:
            return super()._open_session_log()
        if self._log_file is not None:
            return
        self._configured_log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.log_path = self._configured_log_dir / f"{self.session_id}.jsonl"
        self._log_file = self.log_path.open("a", encoding="utf-8", buffering=1)
        self._log_event("session_started", {"protocol_version": self.protocol.version,
                                             "address": list(self._address)})

    def _scrub(self, value):
        if isinstance(value, dict):
            return {key: ("<redacted>" if str(key).lower() in
                          {"confirmation_token", "arm_token", "token", "password", "secret"}
                          else self._scrub(item)) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._scrub(item) for item in value]
        if isinstance(value, str):
            for token in tuple(self._secrets):
                value = value.replace(token, "<redacted>")
        return value

    def _log_event(self, event: str, data: Any):
        with self._log_lock:
            super()._log_event(event, self._scrub(data))

    def _clear_snapshot(self):
        self._raw_snapshot = None
        self.last_telemetry = None
        self.last_result = None
        self.last_motion_assessment = None

    def _invalidate_motors(self, reason: str):
        with self._state_lock:
            self._query_failure = self._query_failure or reason
            self._motors = MotorsSnapshot(None, None, self._connection_epoch, self._query_failure)
        self._motors_stop.set()

    def _discard_transport(self):
        self._control_failed = True
        self._clear_snapshot()
        self._invalidate_motors("control_connection_invalid")
        if self._socket is not None:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._socket.close()
        if self._file is not None:
            self._file.close()
        self._socket = self._file = None
        self._armed = False

    def send(self, kind: str, payload: dict[str, Any], timeout_s: float | None = None) -> Message:
        self._assert_owner()
        if self._control_failed or self._socket is None:
            raise ConnectionError("control connection is unavailable; no replay/reconnect")
        if self._file.buffer:
            self._discard_transport()
            raise ConnectionError("unsolicited response bytes cannot acknowledge a new request")
        if kind not in self._COMMANDS:
            raise ValueError("command is outside the COEX control boundary")
        # Validate without changing the real connection's sequence or sending bytes.
        Protocol(self.protocol.version).encode(kind, payload)
        if kind == "stick_mode" and payload["mode"] != "advanced":
            raise ValueError("COEX requires the explicitly validated advanced velocity mode")
        if kind == "velocity" and any(payload.values()) and not self._armed:
            raise PermissionError("nonzero velocity refused before acknowledged arm")
        if isinstance(payload.get("confirmation_token"), str):
            with self._log_lock:
                self._secrets.add(payload["confirmation_token"])
        cap = self._CAPS.get(kind, self._timeout)
        budget = cap if timeout_s is None else min(_positive_timeout(timeout_s), cap)
        self._clear_snapshot()
        deadline = time.perf_counter() + budget
        self._socket.deadline = deadline
        try:
            ack = super().send(kind, payload, timeout_s=budget)
            _remaining(deadline)
        except PermissionError:
            # A valid, correlated negative ACK is a known rejection, not a timeout.
            self._clear_snapshot()
            raise PermissionError(f"{kind}: bridge rejected command; see the redacted ACK log") from None
        except (OSError, ValueError, UnicodeError) as error:
            self._discard_transport()
            if kind not in self._READS:
                self._log_event("coex_outcome_unknown", {"command_type": kind,
                                                          "error": type(error).__name__})
                raise ActionOutcomeUnknown(f"{kind}: outcome unknown; automatic replay forbidden") from error
            raise ConnectionError("status failed; current observation unavailable") from error
        telemetry = ack.payload.get("telemetry")
        if not isinstance(telemetry, dict) or not telemetry:
            self._discard_transport()
            raise MissingTelemetryError(f"{kind}: acknowledged without current telemetry")
        received = self._file.received_monotonic_s
        with self._state_lock:
            generation = telemetry.get("telemetry_generation")
            changed = self._generation_seen and generation != self._generation
            if not self._generation_seen and generation != self._generation:
                # A GET before the first control ACK was not bound to the now
                # observed telemetry generation. Require a new GET in that scope.
                self._motors = MotorsSnapshot(None, None, self._connection_epoch,
                                              "awaiting_generation_bound_query")
            self._generation = generation
            self._generation_seen = True
        if changed:
            self._invalidate_motors("telemetry_generation_changed")
        snapshot = copy.deepcopy(telemetry)
        snapshot.update(_received_monotonic_s=received,
                        _connection_epoch=self._connection_epoch,
                        _request_sequence=ack.sequence)
        self._raw_snapshot = snapshot
        if kind == "arm":
            self._armed = True
        elif kind == "disarm":
            self._armed = False
        return ack

    def command(self, kind: str, payload: dict[str, Any], timeout_s: float | None = None) -> dict[str, Any]:
        self.send(kind, payload, timeout_s)
        return copy.deepcopy(self._raw_snapshot)

    def _query_bool(self, key: str) -> bool:
        if key not in {"IsFlying", "AreMotorsOn"}:
            raise ValueError("only read-only flight/motor state GETs are permitted")
        if not self._query_lock.acquire(blocking=False):
            raise RuntimeError("a read-only query is already in flight")
        raw_socket = None
        try:
            with self._state_lock:
                if self._query_failure is not None:
                    raise QueryLatchedError(self._query_failure)
                epoch = self._connection_epoch
                generation = self._generation
            deadline = time.perf_counter() + self.QUERY_DEADLINE_S
            command = SdkApiClient._command("GET", "FlightController", key, None, None)
            self._log_event("coex_query_request", {"module": "FlightController", "key": key,
                                                    "connection_epoch": epoch})
            raw_socket = _connect_socket(self._address[0], self._query_port, deadline)
            bounded = _DeadlineSocket(raw_socket)
            bounded.deadline = deadline
            bounded.sendall((command + "\n").encode("utf-8"))
            raw = _LineReader(bounded, limit=16384).readline().decode("utf-8").strip()
            _remaining(deadline)
            received = time.perf_counter()
            self._log_event("coex_query_response", {"module": "FlightController", "key": key,
                                                     "raw": raw, "connection_epoch": epoch})
            prefix = f"FlightController {key} "
            # Existing generic parser accepts unrelated prefixes and 'success'.
            # Neither is evidence for these exact boolean GETs.
            if not raw.startswith(prefix) or raw[len(prefix):].lower() not in {"true", "false"}:
                raise ValueError("query response did not match the requested boolean key")
            parsed = parse_sdk_response("GET", "FlightController", key, raw)
            if not parsed.ok or type(parsed.value) is not bool:
                raise ValueError("query did not return a boolean")
            with self._state_lock:
                if epoch != self._connection_epoch or generation != self._generation or self._query_failure:
                    raise QueryLatchedError("query belongs to invalidated telemetry/connection generation")
                if key == "AreMotorsOn":
                    self._motors = MotorsSnapshot(parsed.value, received, epoch, None)
            return parsed.value
        except QueryLatchedError as error:
            self._invalidate_motors(str(error))
            raise
        except Exception as error:
            reason = f"{key}:{type(error).__name__}"
            self._invalidate_motors(reason)
            self._log_event("coex_query_failed", {"key": key, "error": reason})
            raise QueryLatchedError(reason) from error
        finally:
            if raw_socket is not None:
                raw_socket.close()
            self._query_lock.release()

    def query_bool(self, key: str) -> bool:
        # Ground proof deliberately precedes the control connection. Opening a
        # query log is explicit I/O but cannot arm or take over flight control.
        if self._owner_id is None:
            self._owner_id = threading.get_ident()
        self._assert_owner()
        self._open_session_log()
        if self._motors_thread is not None and self._motors_thread.is_alive():
            raise RuntimeError("stop the motor sampler before synchronous pre/postflight GETs")
        return self._query_bool(key)

    def start_motors(self):
        self._assert_owner()
        if self._control_failed or self._socket is None:
            raise ConnectionError("motor sampling needs the current control connection")
        if self._motors_thread is not None and self._motors_thread.is_alive():
            raise RuntimeError("motor sampler is already running")
        with self._state_lock:
            if self._query_failure:
                raise QueryLatchedError(self._query_failure)
        self._motors_stop.clear()
        self._motors_thread = threading.Thread(target=self._motor_loop, name="coex-motors-get", daemon=True)
        self._motors_thread.start()

    def _motor_loop(self):
        while not self._motors_stop.is_set():
            started = time.perf_counter()
            try:
                self._query_bool("AreMotorsOn")
            except Exception:
                return  # The first failed request latches; no retry thread or queue.
            self._motors_stop.wait(max(0.0, started + self.QUERY_PERIOD_S - time.perf_counter()))

    def stop_motors(self):
        self._motors_stop.set()
        thread = self._motors_thread
        if thread is not None:
            thread.join(self.QUERY_DEADLINE_S + 0.1)
            if thread.is_alive():
                self._invalidate_motors("motor_worker_did_not_stop")
                raise RuntimeError("motor query worker did not terminate within its deadline")
        self._motors_thread = None

    def motors_snapshot(self) -> MotorsSnapshot:
        with self._state_lock:
            return self._motors

    def close(self):
        try:
            self.stop_motors()
        finally:
            self._discard_transport()
            with self._log_lock:
                super().close()
