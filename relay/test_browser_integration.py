"""Offline browser-origin, preview lifecycle and status-boundary coverage."""

import asyncio
import base64
import copy
import unittest
from unittest.mock import patch

from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from relay.browser_access import BrowserAccessMiddleware
from relay.camera_preview import camera_event, serve_camera
from relay.drone_client import DroneClient, DroneError
from relay.drone_status import read_drone_status


ORIGIN = "https://idd-web.livelysky-9813bb2d.southeastasia.azurecontainerapps.io"
LOCAL = r"http://(localhost|127\.0\.0\.1)(:\d+)?"


class OriginTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, origin, *, websocket=False, preflight=False):
        reached = []
        messages = []

        async def app(scope, receive, send):
            reached.append(True)
            if scope["type"] == "websocket":
                await send({"type": "websocket.accept"})
            else:
                await JSONResponse({"ok": True})(scope, receive, send)

        stack = BrowserAccessMiddleware(
            CORSMiddleware(app, allow_origins=[ORIGIN], allow_origin_regex=LOCAL,
                           allow_methods=["*"], allow_headers=["*"], allow_private_network=True),
            origins=[ORIGIN], origin_regex=LOCAL)
        headers = [] if origin is None else [(b"origin", origin.encode())]
        if preflight:
            headers.extend([(b"access-control-request-method", b"GET"),
                            (b"access-control-request-private-network", b"true")])
        scope = {"type": "websocket" if websocket else "http", "method": "OPTIONS" if preflight else "GET",
                 "path": "/ws/camera" if websocket else "/api/drone/status", "headers": headers}

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            messages.append(message)

        await stack(scope, receive, send)
        return reached, messages

    async def test_exact_deployed_and_local_origins_allowed(self):
        for origin in (ORIGIN, "http://127.0.0.1:3000", "http://localhost:3000", None):
            with self.subTest(origin=origin):
                reached, messages = await self.request(origin)
                self.assertTrue(reached)
                self.assertEqual(messages[0]["status"], 200)
                reached, messages = await self.request(origin, websocket=True)
                self.assertTrue(reached)
                self.assertEqual(messages[0]["type"], "websocket.accept")

    async def test_other_origins_cannot_reach_http_or_websocket_handler(self):
        for origin in ("https://other.invalid", ORIGIN + ".evil.invalid", "null", "http://localhost.evil:3000"):
            with self.subTest(origin=origin):
                reached, messages = await self.request(origin)
                self.assertFalse(reached)
                self.assertEqual(messages[0]["status"], 403)
                reached, messages = await self.request(origin, websocket=True)
                self.assertFalse(reached)
                self.assertEqual(messages, [{"type": "websocket.close", "code": 1008}])

    async def test_private_network_preflight_only_for_allowed_origin(self):
        _, messages = await self.request(ORIGIN, preflight=True)
        headers = dict(messages[0]["headers"])
        self.assertEqual(headers[b"access-control-allow-origin"], ORIGIN.encode())
        self.assertEqual(headers[b"access-control-allow-private-network"], b"true")
        _, denied = await self.request("https://other.invalid", preflight=True)
        self.assertEqual(denied[0]["status"], 403)
        self.assertNotIn(b"access-control-allow-private-network", dict(denied[0]["headers"]))


def preview_response(**camera):
    return {"schema_version": 1, "ok": True, "execution_mode": "mock", "physical_execution": False,
            "camera": {"state": "streaming", "simulated": True, "frame_id": "fixture-1", "age_ms": 0,
                "content_type": "image/png", "image_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode(),
                "message": None, **camera}}


class PreviewTests(unittest.IsolatedAsyncioTestCase):
    def test_mock_label_and_stale_image_clear(self):
        event = camera_event(preview_response())
        self.assertEqual(event["mode"], "mock")
        self.assertIn("모의", event["message"])
        self.assertTrue(event["frame"]["imageUrl"].startswith("data:image/png;base64,"))
        stale = camera_event(preview_response(age_ms=501))
        self.assertEqual(stale["state"], "waiting")
        self.assertIsNone(stale["frame"])
        stopped = camera_event(preview_response(state="stopped"))
        self.assertIsNone(stopped["frame"])

    def test_invalid_or_disguised_preview_never_reaches_browser(self):
        for fields in ({"simulated": False}, {"age_ms": float("nan")}, {"age_ms": True},
                       {"content_type": "image/svg+xml"}, {"image_base64": "not-base64"},
                       {"image_base64": "A" * 800_000}, {"frame_id": []}, {"state": "unknown"}):
            with self.subTest(fields=tuple(fields)):
                with self.assertRaises(DroneError):
                    camera_event(preview_response(**fields))

    async def test_preview_disconnect_stops_only_preview_and_does_not_call_flight_tools(self):
        actions = []
        disconnect = asyncio.Event()

        class Client:
            async def camera(self, action):
                actions.append(action)
                return preview_response(state="stopped" if action == "stop" else "streaming")

        class Browser:
            def __init__(self):
                self.events, self.closed = [], False

            async def accept(self):
                pass

            async def send_json(self, value):
                self.events.append(value)
                if value["state"] == "streaming":
                    disconnect.set()

            async def receive(self):
                await disconnect.wait()
                return {"type": "websocket.disconnect"}

            async def close(self):
                self.closed = True

        browser = Browser()
        with patch("relay.camera_preview.DroneClient", return_value=Client()):
            await asyncio.wait_for(serve_camera(browser), 1)
        self.assertEqual(actions, ["start", "stop"])
        self.assertTrue(browser.closed)
        self.assertEqual(browser.events[-1]["state"], "streaming")

    async def test_camera_client_restricts_action_and_uses_backend_envelope(self):
        calls = []

        async def transport(name, envelope):
            calls.append((name, envelope))
            return preview_response()

        client = DroneClient("preview-test", token="test-only", transport=transport)
        for action in ("start", "frame", "stop"):
            await client.camera(action)
        self.assertEqual([name for name, _ in calls], ["_camera_start", "_camera_frame", "_camera_stop"])
        self.assertTrue(all(value["arguments"] == {} and value["caller_id"] == "preview-test" for _, value in calls))
        with self.assertRaises(DroneError):
            await client.camera("takeoff")
        self.assertEqual(len(calls), 3)


class StatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_status_is_not_physical_connection_or_automatic_mission(self):
        calls = []
        caps = {"execution_mode": "mock", "live_ready": False,
                "home_tag_id": 6, "floor_tag_id": 0, "target_height_m": 1.4,
                "destinations": [{"destination_id": f"tag-{tag}", "monitor_id": f"monitor-{tag}",
                    "physical_definition": {"type": "apriltag", "marker_id": tag}} for tag in (1, 2, 3)]}

        class Client:
            async def call(self, name, args):
                calls.append(name)
                return copy.deepcopy(caps) if name == "drone_get_capabilities" else {
                    "execution_mode": "mock", "connected": True, "active_mission_id": None,
                    "secret_diagnostic": "must-not-reach-browser"}

        with patch("relay.drone_status.DroneClient", return_value=Client()):
            status = await read_drone_status()
        self.assertEqual(calls, ["drone_get_capabilities", "drone_get_status"])
        self.assertTrue(status["apiConnected"])
        self.assertIsNone(status["physicalConnected"])
        self.assertFalse(status["liveReady"])
        self.assertEqual(status["homeTagId"], 6)
        self.assertNotIn("secret_diagnostic", status)

    async def test_unreachable_api_is_explicit_not_mock_success(self):
        class Client:
            async def call(self, *args):
                raise DroneError("TRANSPORT_ERROR")

        with patch("relay.drone_status.DroneClient", return_value=Client()):
            status = await read_drone_status()
        self.assertFalse(status["apiConnected"])
        self.assertIsNone(status["executionMode"])
        self.assertIn("TRANSPORT_ERROR", status["error"])


if __name__ == "__main__":
    unittest.main()
