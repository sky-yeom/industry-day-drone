"""Direct, operator-visible access to the phone's DJI KeyManager bridge."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
import socket
from typing import Any, Iterator


_ERROR_FIELD = re.compile(r"([A-Za-z]+)='([^']*)'")


@dataclass(frozen=True)
class SdkApiResult:
    schema_version: int
    ok: bool
    status: str
    message_ko: str
    method: str
    module: str | None
    key: str | None
    value: Any
    dji_error: dict[str, str] | None
    raw_dji_response: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class SdkApiClient:
    """One-request-at-a-time client for the diagnostic server on port 9997.

    The Android server is intentionally independent from the flight command
    socket. GET/LISTEN are read-only. SET/ACTION require the same operator arm
    token used by the control protocol.
    """

    def __init__(self, host: str, port: int = 9997, timeout_s: float = 4.0):
        self.address = (host, port)
        self.timeout_s = timeout_s

    def request(
        self,
        method: str,
        module: str | None = None,
        key: str | None = None,
        *,
        parameter: str | None = None,
        arm_token: str | None = None,
    ) -> SdkApiResult:
        method = method.upper()
        command = self._command(method, module, key, parameter, arm_token)
        with socket.create_connection(self.address, timeout=self.timeout_s) as sock:
            sock.settimeout(self.timeout_s)
            sock.sendall((command + "\n").encode("utf-8"))
            raw = sock.makefile("rb").readline()
        if not raw:
            raise ConnectionError("DJI SDK query bridge closed without a response")
        return parse_sdk_response(method, module, key, raw.decode("utf-8", errors="replace").strip())

    def listen(self, module: str, key: str) -> Iterator[SdkApiResult]:
        """Yield change notifications until the caller closes the iterator."""

        command = self._command("LISTEN", module, key, None, None)
        with socket.create_connection(self.address, timeout=self.timeout_s) as sock:
            sock.settimeout(None)
            sock.sendall((command + "\n").encode("utf-8"))
            reader = sock.makefile("rb")
            for raw in reader:
                yield parse_sdk_response(
                    "LISTEN", module, key, raw.decode("utf-8", errors="replace").strip()
                )

    @staticmethod
    def _command(
        method: str,
        module: str | None,
        key: str | None,
        parameter: str | None,
        arm_token: str | None,
    ) -> str:
        if method == "CALL":
            method = "ACTION"
        if method == "HELP":
            return " ".join(part for part in (method, module, key) if part)
        if method not in {"GET", "LISTEN", "UNLISTEN", "SET", "ACTION"}:
            raise ValueError(f"unsupported SDK method {method!r}")
        if not module or not key:
            raise ValueError(f"{method} requires module and key")
        parts = [method, module, key]
        if method in {"SET", "ACTION"}:
            if not arm_token:
                raise PermissionError(f"{method} requires --arm-token")
            parts.append(f"TOKEN={arm_token}")
        if parameter is not None and parameter != "":
            parts.append(parameter)
        return " ".join(parts)


def parse_sdk_response(
    method: str,
    module: str | None,
    key: str | None,
    raw: str,
) -> SdkApiResult:
    method = "ACTION" if method.upper() == "CALL" else method.upper()
    prefix = f"{module} {key} " if module and key else ""
    payload = raw[len(prefix):] if prefix and raw.startswith(prefix) else raw
    error = (
        {name: value for name, value in _ERROR_FIELD.findall(payload)}
        if "ErrorImp{" in payload
        else None
    )
    lowered = payload.lower()
    failed = (
        error is not None
        or "authorization_rejected" in lowered
        or lowered.startswith("cannot ")
        or lowered.startswith("unknown ")
        or "could not cast" in lowered
    )
    value = _parse_value(payload) if not failed else None
    if failed:
        status = f"SDK_{method}_FAILED"
        error_code = (error or {}).get("errorCode")
        message = f"DJI {method} 호출이 실패했습니다"
        if error_code:
            message += f". 오류 코드: {error_code}"
        elif "authorization_rejected" in lowered:
            message += ". 유효한 arm token이 필요합니다"
        else:
            message += f". 원문: {payload}"
    else:
        status = f"SDK_{method}_SUCCEEDED"
        if method in {"GET", "LISTEN"}:
            message = f"DJI {module}.{key} 값은 {value!r}입니다."
        else:
            message = f"DJI {method} 호출이 완료되었습니다. 반환값: {value!r}"
    return SdkApiResult(
        schema_version=1,
        ok=not failed,
        status=status,
        message_ko=message,
        method=method,
        module=module,
        key=key,
        value=value,
        dji_error=error,
        raw_dji_response=raw,
    )


def _parse_value(payload: str) -> Any:
    if payload == "success":
        return True
    if payload == "null":
        return None
    if payload.lower() in {"true", "false"}:
        return payload.lower() == "true"
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        pass
    try:
        return float(payload) if any(ch in payload for ch in ".eE") else int(payload)
    except ValueError:
        return payload
