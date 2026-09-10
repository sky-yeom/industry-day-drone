"""Offline operator login, CSRF, expiry and bounded-state tests."""
import asyncio
from http.cookies import SimpleCookie
import json
import unittest
from unittest.mock import patch

from starlette.requests import Request

from relay import config, operator_access, operator_sessions as auth

OPERATOR_TOKEN = "operator-" + "o" * 40
DEVICE_TOKEN = "device-" + "d" * 40
LOCAL_TOKEN = "local-" + "l" * 40
ORIGIN = "https://relay.example"


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def request(*, origin=ORIGIN, cookies=None, csrf=None, value=None, raw=None,
            content_type="application/json", client="127.0.0.1"):
    headers = [(b"content-type", content_type.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if cookies:
        headers.append((b"cookie", "; ".join(f"{key}={value}" for key, value in cookies.items()).encode()))
    if csrf is not None:
        headers.append((b"x-csrf-token", csrf.encode()))
    body = raw if raw is not None else json.dumps(value or {}).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "https", "path": "/api/operator/session", "raw_path": b"/api/operator/session",
        "query_string": b"", "headers": headers, "server": ("relay.example", 443), "client": (client, 1234)}, receive)


def response_cookies(response):
    result = SimpleCookie()
    for key, value in response.raw_headers:
        if key.lower() == b"set-cookie":
            result.load(value.decode())
    return result


class OperatorSessionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.blocker = patch("socket.socket.connect", side_effect=AssertionError("offline operator test"))
        self.blocker.start()
        self.addCleanup(self.blocker.stop)
        self.config = patch.multiple(config, RELAY_PUBLIC_ORIGIN=ORIGIN, RELAY_OPERATOR_TOKEN=OPERATOR_TOKEN,
            DRONE_REMOTE_DEVICE_TOKEN=DEVICE_TOKEN, DRONE_CONTROL_API_TOKEN=LOCAL_TOKEN,
            DRONE_CONTROL_MODE="live", DRONE_CONTROL_TRANSPORT="local")
        self.config.start()
        self.addCleanup(self.config.stop)
        self.clock = Clock()
        self.store = auth.SessionStore(clock=self.clock, wall=self.clock)
        self.manager = patch.object(auth, "sessions", self.store)
        self.manager.start()
        self.addCleanup(self.manager.stop)

    async def challenge(self):
        result = await auth.session_info(request(origin=None))
        csrf = json.loads(result.body)["csrfToken"]
        return csrf, {auth.CSRF_COOKIE: response_cookies(result)[auth.CSRF_COOKIE].value}

    async def login(self, token=OPERATOR_TOKEN, *, cookies=None):
        csrf, challenge_cookie = await self.challenge()
        response = await auth.create_session(request(csrf=csrf,
            cookies={**(cookies or {}), **challenge_cookie}, value={"token": token}))
        return response

    async def test_login_page_contains_no_credentials_or_persistent_browser_storage(self):
        response = await auth.login_page(request())
        page = response.body.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('type="password"', page)
        self.assertIn('input.value = ""', page)
        self.assertIn("third-party cookie", page)
        self.assertIn("location.origin !== expectedOrigin", page)
        for secret in (OPERATOR_TOKEN, DEVICE_TOKEN, LOCAL_TOKEN, "localStorage", "sessionStorage"):
            self.assertNotIn(secret, page)
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")

    async def test_cookie_is_secure_httponly_host_only_non_sliding_and_revoked_on_rotation(self):
        response = await self.login()
        self.assertEqual(response.status_code, 200)
        cookie = response_cookies(response)[auth.SESSION_COOKIE]
        self.assertTrue(cookie["secure"] and cookie["httponly"])
        self.assertEqual(cookie["path"], "/")
        self.assertEqual(cookie["domain"], "")
        self.assertEqual(cookie["samesite"], "none")
        self.assertEqual(cookie["max-age"], "900")
        self.assertNotEqual(cookie.value, OPERATOR_TOKEN)
        client = request(cookies={auth.SESSION_COOKIE: cookie.value})
        self.assertTrue(operator_access.authorized_http(client))
        self.clock.now += 899
        self.assertTrue(operator_access.authorized_http(client))
        self.clock.now += 2
        self.assertFalse(operator_access.authorized_http(client))
        response = await self.login()
        client = request(cookies={auth.SESSION_COOKIE: response_cookies(response)[auth.SESSION_COOKIE].value})
        with patch.object(config, "RELAY_OPERATOR_TOKEN", "rotated-" + "r" * 40):
            self.assertFalse(operator_access.authorized_http(client))

    async def test_origin_and_signed_double_submit_are_required_even_with_correct_password(self):
        csrf, cookie = await self.challenge()
        for origin, header, cookies in ((None, csrf, cookie), ("https://other.example", csrf, cookie),
                                       (ORIGIN, None, cookie), (ORIGIN, csrf, {}),
                                       (ORIGIN, csrf + "bad", cookie)):
            response = await auth.create_session(request(origin=origin, csrf=header, cookies=cookies,
                                                          value={"token": OPERATOR_TOKEN}))
            self.assertEqual(response.status_code, 403)
        fake = "n" * 43 + ".1300." + "a" * 64
        response = await auth.create_session(request(csrf=fake, cookies={auth.CSRF_COOKIE: fake},
                                                     value={"token": OPERATOR_TOKEN}))
        self.assertEqual(response.status_code, 403)
        self.clock.now += 301
        response = await auth.create_session(request(csrf=csrf, cookies=cookie, value={"token": OPERATOR_TOKEN}))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.store.sessions, {})

    async def test_wrong_and_device_credentials_never_create_sessions(self):
        for token in (DEVICE_TOKEN, LOCAL_TOKEN, "wrong-" + "w" * 40):
            response = await self.login(token)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn(token, response.body.decode())
            self.assertNotIn(auth.SESSION_COOKIE, response_cookies(response))
        self.assertFalse(self.store.sessions)
        with patch.object(config, "RELAY_OPERATOR_TOKEN", DEVICE_TOKEN):
            self.assertEqual((await auth.login_page(request())).status_code, 503)

    async def test_body_shape_size_and_content_type_are_bounded(self):
        csrf, cookie = await self.challenge()
        for raw, kind, status in ((b'{"token":"x","token":"y"}', "application/json", 400),
            (json.dumps({"token": OPERATOR_TOKEN, "admin": True}).encode(), "application/json", 400),
            (b'{"token":true}', "application/json", 400), (b"x" * 2049, "application/json", 413),
            (b"token=x", "application/x-www-form-urlencoded", 400)):
            response = await auth.create_session(request(csrf=csrf, cookies=cookie, raw=raw, content_type=kind))
            self.assertEqual(response.status_code, status)
        self.assertFalse(self.store.sessions)

    async def test_failed_login_rate_limit_and_recovery(self):
        for _ in range(8):
            self.assertEqual((await self.login("wrong-" + "w" * 40)).status_code, 401)
        response = await self.login()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "60")
        self.clock.now += 61
        self.assertEqual((await self.login()).status_code, 200)

    def test_session_and_rate_state_are_bounded(self):
        for _ in range(auth.MAX_SESSIONS):
            self.assertIsNotNone(self.store.issue())
        self.assertIsNone(self.store.issue())
        self.assertEqual(len(self.store.sessions), auth.MAX_SESSIONS)
        for n in range(10000):
            self.store.allow_attempt(str(n))
        self.assertLessEqual(len(self.store.attempts), auth.MAX_RATE_CLIENTS)
        self.assertLessEqual(len(self.store.global_attempts), 32)
        self.clock.now += 901
        self.assertIsNotNone(self.store.issue())
        self.assertEqual(len(self.store.sessions), 1)

    async def test_logout_and_relogin_revoke_previous_cookie(self):
        response = await self.login()
        old = response_cookies(response)[auth.SESSION_COOKIE].value
        response = await self.login(cookies={auth.SESSION_COOKIE: old})
        new = response_cookies(response)[auth.SESSION_COOKIE].value
        self.assertIsNone(self.store.lookup(old))
        self.assertIsNotNone(self.store.lookup(new))
        csrf, cookie = await self.challenge()
        response = await auth.delete_session(request(csrf=csrf,
            cookies={**cookie, auth.SESSION_COOKIE: new}))
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(self.store.lookup(new))
        self.assertEqual(response_cookies(response)[auth.SESSION_COOKIE]["max-age"], "0")

    async def test_cookie_socket_watchdog_closes_after_expiry_without_subprotocol(self):
        response = await self.login()
        token = response_cookies(response)[auth.SESSION_COOKIE].value

        class Browser:
            scope = {"subprotocols": []}
            cookies = {auth.SESSION_COOKIE: token}

            async def close(self, *, code, reason):
                self.closed = code

        browser = Browser()
        self.assertTrue(await operator_access.authorize_websocket(browser))
        self.assertIsNone(operator_access.selected_protocol(browser))
        self.clock.now += 901
        watchdog = operator_access.session_watchdog(browser)
        await asyncio.wait_for(watchdog, 1)
        self.assertEqual(browser.closed, 1008)


if __name__ == "__main__":
    unittest.main()
