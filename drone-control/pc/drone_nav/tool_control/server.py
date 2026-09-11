"""Loopback-only HTTP facade; run with python -m drone_nav.tool_control.server."""
from __future__ import annotations
import hmac
import json
import os
from pathlib import Path
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .service import MissionService, MockAdapter, CaptureMockAdapter, ToolError


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "DroneTools/1"

    def log_message(self, format, *args):
        pass  # Request bodies, bearer and captures are not HTTP access logs.

    def _write(self, code, data):
        body = json.dumps(data, allow_nan=False, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _authenticate(self):
        self.connection.settimeout(5)
        # No browser cross-origin access. Speech relay is the authenticated caller.
        if self.headers.get("Origin") is not None:
            raise ToolError("FORBIDDEN", "Browser origins are not accepted by the flight service")
        host = self.headers.get("Host", "").split(":")[0].lower()
        if host not in {"127.0.0.1", "localhost"}:
            raise ToolError("FORBIDDEN", "Loopback Host is required")
        expected = "Bearer " + self.server.api_token
        if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), expected.encode()):
            raise ToolError("UNAUTHORIZED", "A backend API token is required")
        expected_modes = self.headers.get_all("X-Drone-Expected-Mode", [])
        if expected_modes:
            if len(expected_modes) != 1 or expected_modes[0] not in {"mock", "live"}:
                raise ToolError("INVALID_ARGUMENT", "Expected mode must be one mock or live header")
            if (expected_modes[0] != self.server.service.mode
                    or expected_modes[0] != self.server.service.adapter.mode):
                raise ToolError("MODE_MISMATCH", "Service execution mode changed; request was not dispatched")

    def _handle(self):
        try:
            self._authenticate()
            path = urlsplit(self.path)
            if path.query or path.fragment:
                raise ToolError("INVALID_ARGUMENT", "Query arguments are not supported")
            parts = [unquote(s) for s in path.path.split("/") if s]
            if self.command == "GET" and len(parts) == 3 and parts[0] == "requests":
                response = self.server.service.lookup_request(parts[1], parts[2])
            elif self.command == "POST" and len(parts) == 2 and parts[0] in {"tools", "camera"}:
                if self.headers.get("Transfer-Encoding"):
                    raise ToolError("INVALID_ARGUMENT", "Chunked requests are not supported")
                size = int(self.headers.get("Content-Length", "0"))
                if not 1 <= size <= 65536:
                    raise ToolError("INVALID_ARGUMENT", "Request size must be 1..65536 bytes")
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ToolError("INVALID_ARGUMENT", "JSON content type is required")
                body = json.loads(self.rfile.read(size), object_pairs_hook=unique_fields,
                                  parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                if not isinstance(body, dict) or set(body) != {"arguments", "caller_id", "request_id"}:
                    raise ToolError("INVALID_ARGUMENT", "Expected arguments, caller_id, request_id")
                dispatch = self.server.service.camera if parts[0] == "camera" else self.server.service.call
                response = dispatch(parts[1], **body)
            else:
                raise ToolError("NOT_FOUND", "Unknown endpoint")
            self._write(200, response)
        except ToolError as exc:
            code = {"UNAUTHORIZED": 401, "FORBIDDEN": 403, "NOT_FOUND": 404,
                    "MISSION_BUSY": 409, "IDEMPOTENCY_CONFLICT": 409,
                    "MODE_MISMATCH": 409}.get(exc.code, 400)
            self._write(code, {"schema_version": 1, "ok": False,
                               "execution_mode": self.server.service.mode,
                               "physical_execution": self.server.service.mode == "live",
                               "error": {"code": exc.code, "message": str(exc)}})
        except (ValueError, TypeError, KeyError, UnicodeError):
            self._write(400, {"schema_version": 1, "ok": False, "execution_mode": self.server.service.mode,
                "physical_execution": self.server.service.mode == "live",
                "error": {"code": "INVALID_ARGUMENT", "message": "Malformed request"}})
        except Exception:
            self._write(500, {"schema_version": 1, "ok": False, "execution_mode": self.server.service.mode,
                "physical_execution": self.server.service.mode == "live",
                "error": {"code": "INTERNAL_ERROR", "message": "Tool service failed; do not retry movement"}})

    do_GET = _handle
    do_POST = _handle


class SingleInstance:
    def __init__(self, path):
        self.file = open(str(path) + ".lock", "a+b")
        self.file.seek(0)
        if os.name == "nt":
            import msvcrt
            if not self.file.read(1):
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def close(self):
        self.file.close()


def adapter_from_environment(environ=None):
    """Resolve a mode explicitly, without opening a server or hardware socket."""
    environ = os.environ if environ is None else environ
    mode = environ.get("DRONE_CONTROL_MODE", "mock")
    if mode not in {"mock", "live"}:
        raise SystemExit("DRONE_CONTROL_MODE must be mock or live")
    selected = environ.get("DRONE_CONTROL_ADAPTER", "legacy")
    if selected not in {"legacy", "field"}:
        raise SystemExit("DRONE_CONTROL_ADAPTER must be legacy or field; no fallback")
    mock_captures = environ.get("DRONE_CONTROL_MOCK_CAPTURES", "0")
    if mock_captures not in {"0", "1"}:
        raise SystemExit("DRONE_CONTROL_MOCK_CAPTURES must be 0 or 1")
    if mode == "mock":
        return CaptureMockAdapter() if mock_captures == "1" else MockAdapter()
    if mock_captures == "1":
        raise SystemExit("Mock captures cannot be mixed with physical execution")
    if environ.get("DRONE_CONTROL_ENABLE_LIVE") != "1":
        raise SystemExit("Live mode requires explicit DRONE_CONTROL_ENABLE_LIVE=1")
    site, config = (Path(environ[key]) for key in ("DRONE_CONTROL_SITE_CONFIG", "DRONE_CONTROL_CONFIG_PATH"))
    if selected == "field":
        from .field import FieldAdapter
        return FieldAdapter(site, config, Path(environ["DRONE_CONTROL_FIELD_PROFILE"]),
                            Path(environ["DRONE_CONTROL_FIELD_REFERENCE"]))
    from .live import LiveAdapter
    return LiveAdapter(site, config)


def main():
    token = os.environ.get("DRONE_CONTROL_API_TOKEN", "")
    if len(token) < 24 or token == "REPLACE_ME":
        raise SystemExit("Set a private DRONE_CONTROL_API_TOKEN of at least 24 characters in both backends")
    adapter = adapter_from_environment()
    mode = adapter.mode
    db = Path(os.environ.get("DRONE_CONTROL_DB", str(Path(__file__).resolve().parents[2] / "logs" / "tools.sqlite3")))
    db.parent.mkdir(parents=True, exist_ok=True)
    guard = SingleInstance(db)
    server = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("DRONE_CONTROL_PORT", "8766"))), Handler)
    service = MissionService(db, adapter)
    server.service, server.api_token = service, token
    def stop(signum, frame):
        service.begin_shutdown()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print(f"Drone tools listening on 127.0.0.1:{server.server_port}; mode={mode}; no mission starts on launch", flush=True)
    try:
        server.serve_forever(poll_interval=.1)
    finally:
        service.close()
        server.server_close()
        guard.close()


if __name__ == "__main__":
    main()
