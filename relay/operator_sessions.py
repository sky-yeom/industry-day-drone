"""Host-only operator sign-in; credentials never enter the dashboard bundle."""
from __future__ import annotations

from collections import deque
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import urlsplit

from starlette.responses import HTMLResponse, JSONResponse, Response

try:
    from . import config, operator_access
except ImportError:
    import config
    import operator_access

SESSION_COOKIE = "__Host-relay_operator"
CSRF_COOKIE = "__Host-relay_operator_csrf"
SESSION_SECONDS = 900
CSRF_SECONDS = 300
MAX_SESSIONS = 128
MAX_RATE_CLIENTS = 1024
OPAQUE = re.compile(r"[A-Za-z0-9_-]{43}")
SAFE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff"}


@dataclass(frozen=True)
class OperatorSession:
    expires: float
    credential_fingerprint: bytes


class SessionStore:
    def __init__(self, *, clock=time.monotonic, wall=time.time):
        self.clock, self.wall = clock, wall
        self._key = secrets.token_bytes(32)
        self.sessions: dict[bytes, OperatorSession] = {}
        self.attempts: dict[str, deque[float]] = {}
        self.global_attempts: deque[float] = deque()

    def _fingerprint(self) -> bytes:
        return hmac.digest(self._key, config.RELAY_OPERATOR_TOKEN.encode("utf-8"), "sha256")

    def _purge(self) -> None:
        now = self.clock()
        fingerprint = self._fingerprint()
        self.sessions = {key: value for key, value in self.sessions.items()
                         if value.expires > now and hmac.compare_digest(value.credential_fingerprint, fingerprint)}

    def issue(self) -> str | None:
        self._purge()
        if len(self.sessions) >= MAX_SESSIONS:
            return None
        token = secrets.token_urlsafe(32)
        self.sessions[hashlib.sha256(token.encode("ascii")).digest()] = OperatorSession(
            self.clock() + SESSION_SECONDS, self._fingerprint())
        return token

    def lookup(self, token: str | None) -> OperatorSession | None:
        if type(token) is not str or OPAQUE.fullmatch(token) is None:
            return None
        self._purge()
        value = self.sessions.get(hashlib.sha256(token.encode("ascii")).digest())
        if (value is None or not operator_access._valid(config.RELAY_OPERATOR_TOKEN)
                or not hmac.compare_digest(value.credential_fingerprint, self._fingerprint())):
            return None
        return value

    def revoke(self, token: str | None) -> None:
        if type(token) is str and OPAQUE.fullmatch(token):
            self.sessions.pop(hashlib.sha256(token.encode("ascii")).digest(), None)

    def allow_attempt(self, client: str) -> bool:
        now = self.clock()
        self.attempts = {key: values for key, values in self.attempts.items()
                         if values and values[-1] > now - 60}
        for values in (self.global_attempts, self.attempts.get(client, deque())):
            while values and values[0] <= now - 60:
                values.popleft()
        if (len(self.global_attempts) >= 32 or len(self.attempts.get(client, ())) >= 8
                or (client not in self.attempts and len(self.attempts) >= MAX_RATE_CLIENTS)):
            return False
        self.global_attempts.append(now)
        self.attempts.setdefault(client, deque()).append(now)
        return True

    def csrf(self) -> str:
        body = secrets.token_urlsafe(32) + "." + str(int(self.wall()) + CSRF_SECONDS)
        return body + "." + hmac.digest(self._key, body.encode("ascii"), "sha256").hex()

    def valid_csrf(self, value: str) -> bool:
        if type(value) is not str or len(value) > 160:
            return False
        parts = value.split(".")
        if (len(parts) != 3 or OPAQUE.fullmatch(parts[0]) is None
                or re.fullmatch(r"\d{1,12}", parts[1]) is None
                or re.fullmatch(r"[a-f0-9]{64}", parts[2]) is None):
            return False
        remaining = int(parts[1]) - self.wall()
        expected = hmac.digest(self._key, ".".join(parts[:2]).encode("ascii"), "sha256").hex()
        return 0 < remaining <= CSRF_SECONDS and hmac.compare_digest(parts[2], expected)


sessions = SessionStore()


def configured() -> bool:
    try:
        parts = urlsplit(config.RELAY_PUBLIC_ORIGIN)
        valid_origin = (parts.scheme == "https" and bool(parts.hostname) and not parts.username
                        and not parts.password and not parts.path and not parts.query and not parts.fragment
                        and not parts.netloc.endswith(":") and parts.port in (None, 443))
    except ValueError:
        return False
    return bool(valid_origin) and operator_access._valid(config.RELAY_OPERATOR_TOKEN)


def cookie_session(request) -> OperatorSession | None:
    if not configured():
        return None
    return sessions.lookup(request.cookies.get(SESSION_COOKIE))


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status, headers=SAFE_HEADERS)


def _origin_ok(request) -> bool:
    values = request.headers.getlist("origin")
    return len(values) == 1 and values[0] == config.RELAY_PUBLIC_ORIGIN


def _csrf_ok(request) -> bool:
    values = request.headers.getlist("x-csrf-token")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    return (len(values) == 1 and len(values[0]) <= 160 and len(cookie) <= 160
            and hmac.compare_digest(values[0].encode("utf-8"), cookie.encode("utf-8"))
            and sessions.valid_csrf(cookie))


async def session_info(request) -> Response:
    if not configured():
        return _error("OPERATOR_LOGIN_UNAVAILABLE", "Configure RELAY_PUBLIC_ORIGIN and a distinct RELAY_OPERATOR_TOKEN.", 503)
    if request.headers.getlist("origin") and not _origin_ok(request):
        return _error("ORIGIN_DENIED", "Open the relay's /operator page directly over HTTPS.", 403)
    csrf = sessions.csrf()
    response = JSONResponse({"authenticated": cookie_session(request) is not None, "csrfToken": csrf,
                            "sessionSeconds": SESSION_SECONDS}, headers=SAFE_HEADERS)
    response.set_cookie(CSRF_COOKIE, csrf, max_age=CSRF_SECONDS, secure=True, httponly=True,
                        samesite="strict", path="/")
    return response


async def create_session(request) -> Response:
    if not configured():
        return _error("OPERATOR_LOGIN_UNAVAILABLE", "Configure RELAY_PUBLIC_ORIGIN and a distinct RELAY_OPERATOR_TOKEN.", 503)
    if not _origin_ok(request) or not _csrf_ok(request):
        return _error("CSRF_DENIED", "Reload /operator and sign in from the relay's exact HTTPS origin.", 403)
    # Use the ASGI peer address, never an arbitrary forwarded header.
    client = request.client.host if request.client is not None else "unknown"
    if not sessions.allow_attempt(client[:128]):
        response = _error("RATE_LIMITED", "Too many sign-in attempts. Wait one minute.", 429)
        response.headers["Retry-After"] = "60"
        return response
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        return _error("INVALID_LOGIN", "Send a JSON object containing only token.", 400)
    body = bytearray()
    try:
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                if len(body) + len(chunk) > 2048:
                    return _error("INVALID_LOGIN", "Sign-in request is too large.", 413)
                body.extend(chunk)
    except TimeoutError:
        return _error("INVALID_LOGIN", "Sign-in request timed out.", 408)
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        value = json.loads(body, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError):
        return _error("INVALID_LOGIN", "Send a JSON object containing only token.", 400)
    if (type(value) is not dict or set(value) != {"token"} or type(value["token"]) is not str
            or not 32 <= len(value["token"]) <= 256):
        return _error("INVALID_LOGIN", "Send a JSON object containing only token.", 400)
    if not operator_access._valid(value["token"]):
        return _error("INVALID_CREDENTIAL", "Operator sign-in failed.", 401)
    sessions.revoke(request.cookies.get(SESSION_COOKIE))
    token = sessions.issue()
    if token is None:
        return _error("SESSION_LIMIT", "The session limit is reached. Sign out an existing session or wait for expiry.", 429)
    response = JSONResponse({"authenticated": True, "sessionSeconds": SESSION_SECONDS}, headers=SAFE_HEADERS)
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS,
                        expires=datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS),
                        secure=True, httponly=True, samesite="none", path="/")
    return response


async def delete_session(request) -> Response:
    if not configured() or not _origin_ok(request) or not _csrf_ok(request):
        return _error("CSRF_DENIED", "Reload /operator and sign out from the relay's exact HTTPS origin.", 403)
    sessions.revoke(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204, headers=SAFE_HEADERS)
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/", secure=True, httponly=True,
                               samesite="none" if name == SESSION_COOKIE else "strict")
    return response


async def login_page(_request) -> Response:
    if not configured():
        return _error("OPERATOR_LOGIN_UNAVAILABLE", "Configure RELAY_PUBLIC_ORIGIN and a distinct RELAY_OPERATOR_TOKEN.", 503)
    nonce = secrets.token_urlsafe(24)
    html = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><title>Relay operator sign-in</title>
<h1>Relay operator sign-in</h1>
<p>This is separate from the dashboard PIN. Enter your operator token explicitly.
Access expires after 15 minutes; aircraft safety gates remain unchanged.</p>
<form id="login" method="post" action="/api/operator/session"><label>Operator token <input id="token" type="password" autocomplete="off"
minlength="32" maxlength="256" required disabled></label><button id="submit" disabled>Sign in</button></form>
<button id="logout" type="button">Sign out</button><p id="status" role="status"></p>
<p>After sign-in, return to the dashboard and reconnect its WebSocket. This does not add new safety UI.
Browsers may block this relay cookie as a third-party cookie; if so the existing dashboard cannot
authenticate this way. No browser-security bypass is provided. Use an approved bearer/subprotocol client.</p>
<script nonce="NONCE">
const status = document.getElementById("status");
const input = document.getElementById("token");
const expectedOrigin = PUBLIC_ORIGIN_JSON;
let csrf = "";
async function refresh() {
  const response = await fetch("/api/operator/session", {credentials:"same-origin", cache:"no-store"});
  const data = await response.json(); csrf = data.csrfToken || "";
  status.textContent = response.ok ? (data.authenticated ? "Signed in." : "Not signed in.") : data.message;
}
document.getElementById("login").onsubmit = async (event) => {
  event.preventDefault();
  const token = input.value; input.value = "";
  document.getElementById("submit").disabled = true;
  try {
    const response = await fetch("/api/operator/session", {method:"POST", credentials:"same-origin",
      headers:{"Content-Type":"application/json", "X-CSRF-Token":csrf}, body:JSON.stringify({token})});
    const data = await response.json();
    status.textContent = response.ok ? "Signed in for 15 minutes. Return to the dashboard and reconnect." : data.message;
  } catch { status.textContent = "Sign-in connection failed. Reload this page."; }
  finally { document.getElementById("submit").disabled = false; }
};
document.getElementById("logout").onclick = async () => {
  try {
    const response = await fetch("/api/operator/session", {method:"DELETE", credentials:"same-origin",
      headers:{"X-CSRF-Token":csrf}});
    if (response.ok) { await refresh(); status.textContent = "Signed out. Operator sockets will close."; }
    else { status.textContent = "Sign-out failed. Reload this page."; }
  } catch { status.textContent = "Sign-out connection failed."; }
};
if (location.origin !== expectedOrigin) {
  location.replace(expectedOrigin + "/operator");
} else {
  refresh().then(() => { input.disabled = false; document.getElementById("submit").disabled = false; })
    .catch(() => { status.textContent = "Unable to initialize sign-in."; });
}
</script></html>""".replace("NONCE", nonce).replace("PUBLIC_ORIGIN_JSON", json.dumps(config.RELAY_PUBLIC_ORIGIN))
    headers = dict(SAFE_HEADERS, **{"Content-Security-Policy":
        f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
    return HTMLResponse(html, headers=headers)
