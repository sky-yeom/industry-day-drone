"""Resolve the relay's internal tool target without loading aircraft configuration."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
import re


REAL_API_URL = "http://127.0.0.1:8766"
TEST_API_URL = "http://127.0.0.1:18767"
EMBEDDED_API_URL = "inprocess://drone-tools/v1"
_RESERVED_PORTS = {8766, 9997, 9998, 9999}


@dataclass(frozen=True)
class ToolTarget:
    run_mode: str | None
    control_mode: str
    api_url: str
    api_token: str = field(repr=False)
    transport: str
    use_tools: bool
    triage_mode: str


def validate_test_api_url(value: str) -> str:
    """Return a canonical test origin, never an aircraft or discovery endpoint."""
    match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})/?", value) if isinstance(value, str) else None
    if match:
        port = int(match[1])
        if 1024 <= port <= 65535 and port not in _RESERVED_PORTS:
            return f"http://127.0.0.1:{port}"
    raise ValueError(
        "DRONE_TEST_API_URL must be http://127.0.0.1:<port> with port 1024-65535, "
        "excluding 8766/9997/9998/9999, and no credentials, path, query or fragment."
    )


def resolve_tool_target(environ: Mapping[str, str] | None = None) -> ToolTarget:
    env = os.environ if environ is None else environ
    # Existing hosted deployments need no extra process or deployment setting.
    # Explicit legacy live deployments remain live; never infer permission to fly.
    if ("DRONE_RUN_MODE" not in env and env.get("RELAY_HOST") == "0.0.0.0"
            and env.get("DRONE_CONTROL_MODE", "mock").strip().lower() == "mock"):
        env = dict(env, DRONE_RUN_MODE="test")
    if "DRONE_RUN_MODE" not in env:
        return ToolTarget(
            run_mode=None,
            control_mode=env.get("DRONE_CONTROL_MODE", "mock").strip().lower(),
            api_url=env.get("DRONE_CONTROL_API_URL", REAL_API_URL).strip(),
            api_token=env.get("DRONE_CONTROL_API_TOKEN", "").strip(),
            transport=env.get("DRONE_CONTROL_TRANSPORT", "local").strip().lower(),
            use_tools=env.get("DRONE_CONTROL_USE_TOOLS", "0") == "1",
            triage_mode=env.get("TRIAGE_MODE", "mock").strip().lower(),
        )
    mode = env["DRONE_RUN_MODE"].strip().lower()
    if mode not in {"test", "real"}:
        raise ValueError("DRONE_RUN_MODE must be test or real when set; unset it for legacy behavior.")
    if mode == "test":
        external = "DRONE_TEST_API_URL" in env
        return ToolTarget(
            run_mode=mode,
            control_mode="mock",
            api_url=validate_test_api_url(env["DRONE_TEST_API_URL"].strip()) if external else EMBEDDED_API_URL,
            api_token=env.get("DRONE_TEST_API_TOKEN", "").strip() if external else "",
            transport="local" if external else "inprocess",
            use_tools=True,
            triage_mode="mock",
        )
    transport = env.get("DRONE_REAL_TRANSPORT",
                        "remote" if env.get("RELAY_HOST") == "0.0.0.0" else "local").strip().lower()
    if transport not in {"local", "remote"}:
        raise ValueError("DRONE_REAL_TRANSPORT must be local or remote.")
    token = env.get("DRONE_CONTROL_API_TOKEN", "").strip() if transport == "local" else ""
    if transport == "local" and not token:
        raise ValueError("DRONE_CONTROL_API_TOKEN is required for DRONE_RUN_MODE=real.")
    return ToolTarget(
        run_mode=mode,
        control_mode="live",
        api_url=REAL_API_URL,
        api_token=token,
        transport=transport,
        use_tools=True,
        triage_mode="azure",
    )
