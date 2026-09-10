"""Operator credentials are backend-issued, not the frontend PIN or an Origin."""
from __future__ import annotations

import asyncio
import hmac
import re

from starlette.websockets import WebSocketDisconnect

try:
    from . import config
except ImportError:
    import config

PROTOCOL = "relay.operator.v1"
TOKEN_PREFIX = "relay.token."
MESSAGE = ("운영자 인증이 필요합니다. relay의 HTTPS /operator 페이지에서 명시적으로 로그인한 뒤 다시 연결하세요. "
           "웹 PIN/Origin은 드론 권한이 아닙니다. "
           "운영자별 토큰을 HTTP Bearer 또는 WebSocket relay.operator.v1 + relay.token.<token>으로 "
           "전달하는 승인된 클라이언트도 사용할 수 있습니다. 현재 기본 웹 UI는 토큰 인증을 전달하지 않으며, "
           "브라우저가 타사 쿠키를 차단하면 쿠키 로그인도 사용할 수 없습니다.")


def required() -> bool:
    return config.DRONE_CONTROL_MODE == "live" or config.DRONE_CONTROL_TRANSPORT == "remote"


def _valid(token: str) -> bool:
    expected = config.RELAY_OPERATOR_TOKEN
    return (bool(expected) and re.fullmatch(r"[A-Za-z0-9_-]{32,256}", expected) is not None
            and hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))
            and expected not in {config.DRONE_REMOTE_DEVICE_TOKEN, config.DRONE_CONTROL_API_TOKEN})


def authorized_http(request) -> bool:
    values = request.headers.getlist("authorization")
    if values:
        return len(values) == 1 and values[0].startswith("Bearer ") and _valid(values[0][7:])
    return _cookie_session(request) is not None


def _cookie_session(request):
    try:
        from .operator_sessions import cookie_session
    except ImportError:
        from operator_sessions import cookie_session
    return cookie_session(request)


async def authorize_websocket(browser) -> bool:
    if not required():
        return True
    protocols = browser.scope.get("subprotocols", [])
    credentials = [value[len(TOKEN_PREFIX):] for value in protocols if value.startswith(TOKEN_PREFIX)]
    if (protocols.count(PROTOCOL) == 1 and len(protocols) == 2 and len(credentials) == 1
            and _valid(credentials[0])):
        return True
    session = _cookie_session(browser)
    if session is not None:
        browser.scope["_operator_cookie_session"] = True
        return True
    await browser.accept()
    await browser.send_json({"type": "relay.error", "code": "OPERATOR_AUTH_REQUIRED", "message": MESSAGE})
    await browser.close(code=1008)
    return False


def selected_protocol(browser) -> str | None:
    return PROTOCOL if required() and PROTOCOL in browser.scope.get("subprotocols", []) else None


def session_watchdog(browser):
    if not required() or not getattr(browser, "scope", {}).get("_operator_cookie_session"):
        return None

    async def watch():
        while _cookie_session(browser) is not None:
            await asyncio.sleep(1)
        try:
            await browser.close(code=1008, reason="Operator session expired or signed out")
        except (RuntimeError, WebSocketDisconnect, OSError):
            pass
    return asyncio.create_task(watch())
