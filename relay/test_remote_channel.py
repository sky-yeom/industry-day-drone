"""Real loopback ASGI/WebSocket/HTTP chain. No cloud, DJI, or non-loopback IO."""
from __future__ import annotations

import asyncio
import base64
from http.cookies import SimpleCookie
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from urllib import error, request
from uuid import uuid4

import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus
from starlette.responses import RedirectResponse

from relay import config, server
from relay import operator_sessions
from relay.camera import LiveCaptureCamera
from relay.device_hub import DeviceHub, get_device_hub
from relay.device_protocol import MAX_INFLIGHT, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, decode, encode, validate_request
from relay.drone_client import DroneClient, DroneError
from relay.live_mission import LiveMissionRunner
from relay.pc_connector import LocalAPI, PCConnector, validate_cloud_url
from relay.survey import SurveySession
from relay.test_drone_http_integration import Handler, RecordingService, ThreadingHTTPServer
from relay.test_mission_runner import FakeVision
from relay.test_survey import PROMPT_ARGS, ready
from drone_nav.tool_control.service import CaptureMockAdapter

DEVICE_TOKEN = "test-device-" + "d" * 40
LOCAL_TOKEN = "test-local-" + "l" * 40
OPERATOR_TOKEN = "test-operator-" + "o" * 40
ROOT = Path(__file__).resolve().parents[1]


class GatedCaptureAdapter(CaptureMockAdapter):
    def __init__(self):
        self.finish = threading.Event()
        self.finish.set()
        self.before_capture = threading.Event()
        self.before_capture.set()

    def run(self, mission, cancel, emit):
        while not self.before_capture.wait(.01):
            if cancel.is_set():
                return self.stop()
        result = super().run(mission, cancel, emit)
        while not self.finish.wait(.01):
            if cancel.is_set():
                return self.stop()
        return result


class DelayedService(RecordingService):
    reply_gate = None

    def call(self, name, *args, **kwargs):
        result = super().call(name, *args, **kwargs)
        if name == "drone_execute_route" and self.reply_gate is not None:
            self.reply_gate.wait(3)
        return result


class ProtocolTests(unittest.TestCase):
    def test_six_maximum_png_payloads_fit_but_larger_rpc_is_rejected(self):
        encoded = base64.b64encode(b"\0" * (4 * 1024 * 1024)).decode()
        value = {"captures": [{"image_base64": encoded} for _ in range(6)]}
        self.assertLess(len(encode(value, MAX_RESPONSE_BYTES)), MAX_RESPONSE_BYTES)
        with self.assertRaises(DroneError):
            encode({"oversized": "x" * MAX_RESPONSE_BYTES}, MAX_RESPONSE_BYTES)

    def test_url_paths_hosts_and_live_gates_are_fail_closed(self):
        validate_cloud_url("wss://relay.example/ws/device", "relay.example")
        for url in ("ws://relay.example/ws/device", "wss://relay.example.evil/ws/device",
                    "wss://relay.example/ws/device?token=bad", "wss://user@relay.example/ws/device",
                    "wss://relay.example/other", "wss://relay.example:8443/ws/device",
                    "ws://127.0.0.1:1234/ws/device"):
            with self.subTest(url=url), self.assertRaises(DroneError):
                validate_cloud_url(url, "relay.example")
        with self.assertRaises(DroneError):
            validate_cloud_url("ws://127.0.0.1:1234/ws/device", "127.0.0.1")
        validate_cloud_url("ws://127.0.0.1:1234/ws/device", "127.0.0.1", allow_loopback_test=True)
        with self.assertRaises(DroneError):
            LocalAPI(LOCAL_TOKEN, "live")
        with self.assertRaises(DroneError):
            LocalAPI(LOCAL_TOKEN, "mock", _test_target=("other.example", 8766))
        with self.assertRaises(DroneError):
            PCConnector("wss://relay.example/ws/device", "relay.example", "pc-1", LOCAL_TOKEN,
                        LocalAPI(LOCAL_TOKEN, "mock"))

    def test_exact_schemas_reject_replays_paths_extra_headers_and_bad_identities(self):
        value = {"type": "rpc.request", "generation": "a" * 32, "rpc_id": 1,
                 "operation": "drone_get_status", "envelope": {
                     "arguments": {}, "caller_id": "caller-1", "request_id": "business-1"}}
        validate_request(value, "a" * 32, 0)
        bad = [
            dict(value, rpc_id=True), dict(value, rpc_id=0), dict(value, generation="b" * 32),
            dict(value, operation="/tools/drone_get_status"), dict(value, headers={}),
            dict(value, envelope=dict(value["envelope"], caller_id="../escape")),
            dict(value, envelope=dict(value["envelope"], arguments={"url": "http://elsewhere"})),
        ]
        for request in bad:
            with self.subTest(request=request), self.assertRaises(DroneError):
                validate_request(request, "a" * 32, 0)
        with self.assertRaises(DroneError):
            validate_request(value, "a" * 32, 1)
        for raw in ('{"type":"x","type":"x"}', '{"x":NaN}', "[]" , "x" * (MAX_REQUEST_BYTES + 1)):
            with self.assertRaises(DroneError):
                decode(raw, MAX_REQUEST_BYTES)

    def test_remote_replica_ack_and_exact_device_are_mandatory(self):
        with patch.multiple(config, DRONE_CONTROL_TRANSPORT="remote", DRONE_CONTROL_MODE="mock",
                            DRONE_REMOTE_EXECUTION_MODE="mock", DRONE_REMOTE_SINGLE_REPLICA=False):
            with self.assertRaises(DroneError):
                get_device_hub()
        with self.assertRaises(DroneError):
            DeviceHub("", DEVICE_TOKEN, "mock")


class ConnectorProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_replayed_rpc_and_stale_generation_never_forward_twice(self):
        for stale_generation in (False, True):
            calls, sent = [], []
            generation = "a" * 32
            rpc = {"type": "rpc.request", "generation": generation, "rpc_id": 1,
                   "operation": "drone_get_status",
                   "envelope": {"arguments": {}, "caller_id": "caller", "request_id": "business"}}
            packets = [{"type": "device.ready", "generation": generation, "execution_mode": "mock"},
                       rpc, dict(rpc, generation="b" * 32) if stale_generation else rpc]

            class Local:
                mode, _token = "mock", LOCAL_TOKEN

                async def call(self, operation, envelope):
                    calls.append((operation, envelope))
                    return {"schema_version": 1, "ok": True, "execution_mode": "mock", "physical_execution": False}

            class Socket:
                async def recv(self):
                    await asyncio.sleep(.01)
                    return json.dumps(packets.pop(0))

                async def send(self, value):
                    sent.append(json.loads(value))

            connector = PCConnector("wss://relay.example/ws/device", "relay.example", "pc-1", DEVICE_TOKEN, Local())
            with self.assertRaises(DroneError):
                await connector.serve(Socket())
            self.assertEqual(calls, [("drone_get_status", rpc["envelope"])])
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["rpc_id"], 1)
            self.assertEqual(sent[0]["request_id"], "business")


class RemoteChannelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory(dir=ROOT)
        self.adapter = GatedCaptureAdapter()
        # Leave headroom for shared Windows CI scheduling and fixture PNG work.
        self.service = DelayedService(Path(self.directory.name) / "remote.sqlite3", self.adapter, lease_seconds=1.5)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.service, self.http.api_token = self.service, LOCAL_TOKEN
        self.http_thread = threading.Thread(target=lambda: self.http.serve_forever(poll_interval=.01), daemon=True)
        self.http_thread.start()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.port = self.listener.getsockname()[1]
        self.url = f"ws://127.0.0.1:{self.port}"
        real_connect = socket.socket.connect
        allowed = {("127.0.0.1", self.port), ("127.0.0.1", self.http.server_port)}

        def loopback_only(sock, address):
            if address not in allowed:
                raise AssertionError("Test attempted non-allowlisted network IO")
            return real_connect(sock, address)

        self.blocker = patch("socket.socket.connect", loopback_only)
        self.blocker.start()
        self.settings = patch.multiple(config, DRONE_CONTROL_TRANSPORT="remote",
            DRONE_CONTROL_USE_TOOLS=True, DRONE_CONTROL_MODE="mock", TRIAGE_MODE="mock",
            DRONE_REMOTE_EXECUTION_MODE="mock", DRONE_REMOTE_DEVICE_ID="pc-1",
            DRONE_REMOTE_DEVICE_TOKEN=DEVICE_TOKEN, DRONE_REMOTE_SINGLE_REPLICA=True,
            RELAY_OPERATOR_TOKEN=OPERATOR_TOKEN, DRONE_CONTROL_API_TOKEN="")
        self.settings.start()
        self.hub = DeviceHub("pc-1", DEVICE_TOKEN, "mock")
        self.hub_patch = patch("relay.device_hub._hub", self.hub)
        self.hub_patch.start()
        self.key_patch = patch("relay.device_hub._settings", ("pc-1", DEVICE_TOKEN, "mock"))
        self.key_patch.start()
        self.asgi = uvicorn.Server(uvicorn.Config(server.app, log_level="critical", access_log=False,
            lifespan="off", ws_max_size=40 * 1024 * 1024, ws_max_queue=2))
        self.asgi_task = asyncio.create_task(self.asgi.serve(sockets=[self.listener]))
        await self.wait_for(lambda: self.asgi.started)
        self.local = LocalAPI(LOCAL_TOKEN, "mock", _test_target=("127.0.0.1", self.http.server_port))
        self.connector = PCConnector(self.url + "/ws/device", "127.0.0.1", "pc-1",
            DEVICE_TOKEN, self.local, allow_loopback_test=True)
        self.connector_task = None
        self.runner = None

    async def asyncTearDown(self):
        if self.runner:
            await self.runner.close()
        if self.service.reply_gate:
            self.service.reply_gate.set()
        if self.connector_task:
            self.connector_task.cancel()
            await asyncio.gather(self.connector_task, return_exceptions=True)
        if self.hub.connection:
            await self.hub.disconnect(self.hub.connection)
        self.asgi.should_exit = True
        await asyncio.wait_for(self.asgi_task, 4)
        self.listener.close()
        await asyncio.to_thread(self.http.shutdown)
        self.http.server_close()
        self.service.close()
        self.http_thread.join(timeout=1)
        for patcher in (self.key_patch, self.hub_patch, self.settings, self.blocker):
            patcher.stop()
        self.directory.cleanup()

    async def wait_for(self, predicate, seconds=4):
        async def poll():
            while not predicate():
                await asyncio.sleep(.01)
        await asyncio.wait_for(poll(), seconds)

    async def start_connector(self):
        self.connector_task = asyncio.create_task(self.connector.run_once())
        await self.wait_for(lambda: self.hub.connected or self.connector_task.done())
        if self.connector_task.done():
            self.connector_task.result()

    def device(self, **headers):
        return connect(self.url + "/ws/device", proxy=None, compression=None,
            additional_headers={"Authorization": "Bearer " + DEVICE_TOKEN, "X-Drone-Device-ID": "pc-1",
                                "X-Drone-Execution-Mode": "mock", **headers})

    def count(self, name):
        return sum(call[0] == name for call in self.service.calls)

    async def test_wrong_auth_wrong_device_wrong_mode_and_duplicate_are_denied(self):
        for headers in ({"Authorization": "Bearer wrong"}, {"X-Drone-Device-ID": "pc-other"},
                        {"X-Drone-Execution-Mode": "live"}):
            with self.subTest(headers=tuple(headers)), self.assertRaises(InvalidStatus):
                async with self.device(**headers):
                    self.fail("untrusted device admitted")
        async with self.device() as device:
            ready_message = json.loads(await device.recv())
            generation = self.hub.connection.generation
            self.assertEqual(ready_message["generation"], generation)
            with self.assertRaises(InvalidStatus):
                async with self.device():
                    self.fail("duplicate takeover admitted")
            self.assertEqual(self.hub.connection.generation, generation)

    async def test_public_remote_sessions_refused_before_runner_and_camera(self):
        for path in ("/ws?voice=0", "/ws/camera"):
            with patch("relay.server.Bridge") as bridge, patch("relay.server.serve_camera") as camera:
                async with connect(self.url + path, proxy=None,
                                   origin="http://localhost:3000") as browser:
                    event = json.loads(await browser.recv())
                    self.assertEqual(event["type"], "relay.error")
                    self.assertEqual(event["code"], "OPERATOR_AUTH_REQUIRED")
                bridge.assert_not_called()
                camera.assert_not_called()
        with patch.object(config, "DRONE_CONTROL_TRANSPORT", "local"), \
                patch.object(config, "DRONE_CONTROL_USE_TOOLS", False):
            async with connect(self.url + "/ws?voice=0", proxy=None) as browser:
                self.assertEqual(json.loads(await browser.recv())["type"], "relay.ready")
                state = json.loads(await browser.recv())["state"]
                self.assertEqual(state["droneControlMode"], "mock")
                self.assertNotIn("droneToolExecution", state)
        self.assertEqual(self.count("drone_execute_route"), 0)

    async def test_physical_diagnostics_require_operator_bearer_and_hide_tokens(self):
        def get(token=None):
            headers = {} if token is None else {"Authorization": "Bearer " + token}
            req = request.Request(f"http://127.0.0.1:{self.port}/api/drone/status", headers=headers)
            opener = request.build_opener(request.ProxyHandler({}))
            try:
                response = opener.open(req, timeout=2)
            except error.HTTPError as exc:
                response = exc
            with response:
                return response.status, response.read().decode()
        with patch.object(config, "DRONE_CONTROL_MODE", "live"), \
                patch("relay.server.read_drone_status", return_value={"apiConnected": True}) as status:
            for token in (None, DEVICE_TOKEN, LOCAL_TOKEN, "wrong"):
                code, body = await asyncio.to_thread(get, token)
                self.assertEqual(code, 401)
                self.assertIn("OPERATOR_AUTH_REQUIRED", body)
            status.assert_not_called()
            code, body = await asyncio.to_thread(get, OPERATOR_TOKEN)
            self.assertEqual(code, 200)
            self.assertNotIn(OPERATOR_TOKEN, body)
            status.assert_awaited_once()

    async def test_remote_mock_without_explicit_tools_refuses_simulation_fallback(self):
        with patch.object(config, "DRONE_CONTROL_USE_TOOLS", False):
            async with connect(self.url + "/ws?voice=0", proxy=None,
                subprotocols=["relay.operator.v1", "relay.token." + OPERATOR_TOKEN]) as browser:
                self.assertEqual(json.loads(await browser.recv())["code"], "MOCK_TOOLS_OPT_IN_REQUIRED")
        self.assertEqual(self.count("drone_execute_route"), 0)

    async def test_redirects_cannot_forward_cloud_or_local_bearers(self):
        class RedirectHub:
            async def serve(self, socket):
                await socket.send_denial_response(RedirectResponse("https://never-contact.invalid/ws/device"))
        with patch("relay.server.get_device_hub", return_value=RedirectHub()):
            with self.assertRaises(InvalidStatus) as redirect:
                await self.connector.run_once()
            self.assertEqual(redirect.exception.response.status_code, 307)

        def local_redirect(handler):
            handler.send_response(302)
            handler.send_header("Location", "http://never-contact.invalid/tools/drone_get_capabilities")
            handler.send_header("Content-Length", "2")
            handler.end_headers()
            handler.wfile.write(b"{}")
        with patch.object(Handler, "do_POST", local_redirect), self.assertRaises(DroneError):
            await self.local.check_mode()
        self.assertFalse(self.hub.connected)

    async def test_timeout_abandons_connection_and_latches_unknown_write(self):
        async with self.device() as device:
            await device.recv()
            connection = self.hub.connection
            client = DroneClient("timeout-owner", timeout=.1)
            arguments = {"profile_id": "test", "site_revision": "test",
                         "destination_ids": ["tag-3", "tag-1", "tag-2"]}
            write = asyncio.create_task(client.call("drone_execute_route", arguments, request_id="timeout-intent"))
            value = json.loads(await device.recv())
            self.assertNotEqual(value["rpc_id"], value["envelope"]["request_id"])
            with self.assertRaises(DroneError) as outcome:
                await write
            self.assertEqual(outcome.exception.code, "OUTCOME_UNKNOWN")
            await self.wait_for(lambda: not connection.pending)
            self.assertFalse(self.hub.connected)
        async with self.device() as device:
            next_generation = json.loads(await device.recv())["generation"]
            self.assertNotEqual(next_generation, connection.generation)
            with self.assertRaises(DroneError) as repeated:
                await client.call("drone_execute_route", arguments, request_id="timeout-intent")
            self.assertEqual(repeated.exception.code, "OUTCOME_UNKNOWN")
            self.assertEqual(self.hub.connection.counter, 0)

    async def test_full_existing_voice_commands_use_remote_tools_and_attributed_mock_photos(self):
        await self.start_connector()
        with patch.dict("relay.vision.SCENARIO", {"mockAnalysisMs": 1}):
            async with connect(self.url + "/ws?voice=0", proxy=None, max_size=40 * 1024 * 1024,
                    subprotocols=["relay.operator.v1", "relay.token." + OPERATOR_TOKEN]) as browser:
                self.assertEqual(browser.subprotocol, "relay.operator.v1")
                await browser.recv()
                await browser.recv()
                for name, args in (("confirm_prompt", PROMPT_ARGS), ("select_stop", {"monitor": "monitor-3"}),
                                   ("select_stop", {"monitor": "monitor-1"}), ("confirm_route", {}),
                                   ("launch_mission", {})):
                    await browser.send(json.dumps({"type": "command", "name": name, "args": args,
                                                   "requestId": str(uuid4())}))
                async with asyncio.timeout(10):
                    while True:
                        event = json.loads(await browser.recv())
                        if event["type"] == "route.state":
                            state = event["state"]
                            self.assertIsNone(state["droneErrorCode"])
                            if state["missionPhase"] == "complete" and state["droneState"] == "completed":
                                break
                self.assertEqual(state["droneToolExecution"], "mock")
                self.assertEqual([c["monitorId"] for c in state["captures"]],
                                 ["monitor-3", "monitor-1", "monitor-2"])
                self.assertEqual([c["visitIndex"] for c in state["captures"]], [0, 1, 2])
                self.assertTrue(all(c["missionId"] == state["droneMissionId"] for c in state["captures"]))
                self.assertEqual(state["confirmedRoute"], ["monitor-3", "monitor-1", "monitor-2"])
                for token in (DEVICE_TOKEN, LOCAL_TOKEN, OPERATOR_TOKEN):
                    self.assertNotIn(token, json.dumps(state))
        self.assertEqual(self.count("drone_execute_route"), 1)
        caps = await DroneClient("caps-check").call("drone_get_capabilities", {})
        self.assertIs(caps["physical_execution"], False)
        self.assertTrue(caps["mock_capture_ready"])
        sensors = await DroneClient("sensor-check").call("drone_get_sensor_snapshot", {})
        self.assertIs(sensors["physical_execution"], False)

    async def test_authorized_remote_camera_stops_viewer_on_channel_loss_without_flight(self):
        await self.start_connector()
        async with connect(self.url + "/ws/camera", proxy=None, max_size=1024 * 1024,
            subprotocols=["relay.operator.v1", "relay.token." + OPERATOR_TOKEN]) as browser:
            self.assertEqual(browser.subprotocol, "relay.operator.v1")
            async with asyncio.timeout(3):
                while True:
                    value = json.loads(await browser.recv())
                    if value.get("state") == "streaming":
                        break
            self.assertEqual(value["mode"], "mock")
            self.assertTrue(value["frame"]["imageUrl"].startswith("data:image/png;base64,"))
            self.assertTrue(self.service.camera_broker.viewers)
            await self.hub.disconnect(self.hub.connection)
            await self.wait_for(lambda: not self.service.camera_broker.viewers)
        self.assertEqual(self.count("drone_execute_route"), 0)

    async def test_public_config_exposes_only_safe_remote_connection_metadata(self):
        before = await server.api_config()
        self.assertFalse(before["remoteConnected"])
        self.assertEqual(before["remoteExecutionMode"], "mock")
        self.assertFalse(before["droneReady"])
        await self.start_connector()
        after = await server.api_config()
        self.assertTrue(after["remoteConnected"])
        self.assertTrue(after["droneReady"])
        for secret in (DEVICE_TOKEN, LOCAL_TOKEN, OPERATOR_TOKEN, "pc-1"):
            self.assertNotIn(secret, json.dumps(after))

    async def test_explicit_operator_login_cookie_authenticates_unchanged_browser_sockets(self):
        await self.start_connector()
        public_origin = "https://relay.example"
        dashboard_origin = "https://dashboard.example"
        middleware = server.app.middleware_stack
        while not isinstance(middleware, server.BrowserAccessMiddleware):
            middleware = middleware.app

        def http(method, *, cookies=None, csrf=None, token=None):
            headers = {"Origin": public_origin}
            if cookies:
                headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in cookies.items())
            if csrf:
                headers["X-CSRF-Token"] = csrf
            body = None
            if token is not None:
                headers["Content-Type"] = "application/json"
                body = json.dumps({"token": token}).encode()
            req = request.Request(f"http://127.0.0.1:{self.port}/api/operator/session",
                                  data=body, headers=headers, method=method)
            opener = request.build_opener(request.ProxyHandler({}))
            with opener.open(req, timeout=2) as response:
                cookie = SimpleCookie()
                for raw in response.headers.get_all("Set-Cookie", []):
                    cookie.load(raw)
                return response.status, response.read(), {name: value.value for name, value in cookie.items()}

        # Simulate the configured relay HTTPS origin at this loopback ASGI test
        # boundary; Secure cookie flags are separately asserted in unit tests.
        with patch.object(config, "RELAY_PUBLIC_ORIGIN", public_origin), \
                patch.object(middleware, "origins", middleware.origins | {public_origin, dashboard_origin}), \
                patch.object(operator_sessions, "sessions", operator_sessions.SessionStore()):
            code, body, cookies = await asyncio.to_thread(http, "GET")
            self.assertEqual(code, 200)
            csrf = json.loads(body)["csrfToken"]
            code, body, issued = await asyncio.to_thread(http, "POST", cookies=cookies, csrf=csrf, token=OPERATOR_TOKEN)
            self.assertEqual(code, 200)
            cookies.update(issued)
            cookie_header = operator_sessions.SESSION_COOKIE + "=" + cookies[operator_sessions.SESSION_COOKIE]
            async with connect(self.url + "/ws?voice=0", proxy=None, origin=dashboard_origin,
                    additional_headers={"Cookie": cookie_header}) as browser, \
                    connect(self.url + "/ws/camera", proxy=None, origin=dashboard_origin,
                    additional_headers={"Cookie": cookie_header}) as camera:
                self.assertIsNone(browser.subprotocol)
                self.assertEqual(json.loads(await browser.recv())["type"], "relay.ready")
                await browser.recv()
                async with asyncio.timeout(3):
                    while json.loads(await camera.recv()).get("state") != "streaming":
                        pass
                code, _, _ = await asyncio.to_thread(http, "DELETE", cookies=cookies, csrf=csrf)
                self.assertEqual(code, 204)
                await asyncio.wait_for(asyncio.gather(browser.wait_closed(), camera.wait_closed()), 3)
                self.assertEqual(browser.close_code, 1008)
                self.assertEqual(camera.close_code, 1008)
            await self.wait_for(lambda: not self.service.camera_broker.viewers)
        self.assertEqual(self.count("drone_execute_route"), 0)

    async def test_stale_or_mismatched_responses_drop_connection_and_pending_reads(self):
        for field, changed in (("caller_id", "different"), ("request_id", "different"),
                               ("generation", "f" * 32), ("rpc_id", 99)):
            async with self.device() as device:
                await device.recv()
                client = DroneClient("correlation")
                read = asyncio.create_task(client.call("drone_get_status", {}))
                value = json.loads(await device.recv())
                response = {"type": "rpc.response", "generation": value["generation"], "rpc_id": value["rpc_id"],
                    "caller_id": value["envelope"]["caller_id"], "request_id": value["envelope"]["request_id"],
                    "result": {"schema_version": 1, "ok": True, "execution_mode": "mock", "physical_execution": False}}
                response[field] = changed
                await device.send(json.dumps(response))
                with self.assertRaises(DroneError):
                    await read
                await self.wait_for(lambda: not self.hub.connected)

    async def test_cancelled_preview_read_does_not_drop_shared_mission_channel(self):
        async with self.device() as device:
            await device.recv()
            preview = asyncio.create_task(DroneClient("preview").camera("frame"))
            rpc = json.loads(await device.recv())
            preview.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await preview
            self.assertTrue(self.hub.connected)
            result = {"schema_version": 1, "ok": True, "execution_mode": "mock", "physical_execution": False}

            def reply(value):
                return json.dumps({"type": "rpc.response", "generation": value["generation"],
                    "rpc_id": value["rpc_id"], "caller_id": value["envelope"]["caller_id"],
                    "request_id": value["envelope"]["request_id"], "result": result})

            await device.send(reply(rpc))
            await self.wait_for(lambda: not self.hub.connection.pending)
            owner = asyncio.create_task(DroneClient("owner").call("drone_get_status", {}))
            next_rpc = json.loads(await device.recv())
            await device.send(reply(next_rpc))
            self.assertEqual(await owner, result)
            self.assertTrue(self.hub.connected)
            # The discarded completion isn't permission to replay that RPC.
            await device.send(reply(rpc))
            await self.wait_for(lambda: not self.hub.connected)

    async def test_bounded_pending_reads_and_no_write_replay_after_disconnect(self):
        async with self.device() as device:
            await device.recv()
            connection = self.hub.connection
            requests = [asyncio.create_task(self.hub.request("drone_get_status",
                {"arguments": {}, "caller_id": "limit", "request_id": str(uuid4())}, timeout=3))
                for _ in range(MAX_INFLIGHT)]
            for _ in requests:
                await device.recv()
            with self.assertRaises(DroneError) as busy:
                await self.hub.request("drone_get_status",
                    {"arguments": {}, "caller_id": "limit", "request_id": str(uuid4())}, timeout=3)
            self.assertEqual(busy.exception.code, "REMOTE_BUSY")
            await self.hub.disconnect(connection)
            self.assertTrue(all(isinstance(value, DroneError) for value in
                                await asyncio.gather(*requests, return_exceptions=True)))
            self.assertFalse(connection.pending)
        await self.start_connector()
        self.service.reply_gate = threading.Event()
        client = DroneClient("no-replay")
        caps = await client.call("drone_get_capabilities", {})
        arguments = {"profile_id": caps["profile_id"], "site_revision": caps["site_revision"],
                     "destination_ids": ["tag-3", "tag-1", "tag-2"]}
        write = asyncio.create_task(client.call("drone_execute_route", arguments, request_id="intent-once"))
        await self.wait_for(lambda: self.count("drone_execute_route") == 1)
        await self.hub.disconnect(self.hub.connection)
        with self.assertRaises(DroneError) as outcome:
            await write
        self.assertEqual(outcome.exception.code, "OUTCOME_UNKNOWN")
        self.service.reply_gate.set()
        await asyncio.gather(self.connector_task, return_exceptions=True)
        await self.start_connector()
        with self.assertRaises(DroneError) as repeated:
            await client.call("drone_execute_route", arguments, request_id="intent-once")
        self.assertEqual(repeated.exception.code, "OUTCOME_UNKNOWN")
        self.assertEqual(self.count("drone_execute_route"), 1)
        with self.assertRaises(DroneError) as stale:
            await client.lookup_request("intent-once")
        self.assertEqual(stale.exception.code, "REMOTE_SESSION_LOST")
        # An explicit recovery client may look up evidence; the old mission
        # client's generation can never silently resume its ownership lease.
        lookup = await DroneClient("no-replay").lookup_request("intent-once")
        self.assertEqual(lookup["mission"]["caller_id"], "no-replay")

    async def test_reconnected_channel_cannot_renew_an_old_owner_lease(self):
        self.adapter.finish.clear()
        self.adapter.before_capture.clear()
        await self.start_connector()
        client = DroneClient("old-owner")
        caps = await client.call("drone_get_capabilities", {})
        admitted = await client.call("drone_execute_route", {
            "profile_id": caps["profile_id"], "site_revision": caps["site_revision"],
            "destination_ids": ["tag-3", "tag-1", "tag-2"]}, request_id="old-mission")
        await self.hub.disconnect(self.hub.connection)
        await asyncio.gather(self.connector_task, return_exceptions=True)
        await self.start_connector()
        before = self.count("drone_get_mission")
        with self.assertRaises(DroneError) as stale:
            await client.call("drone_get_mission", {"mission_id": admitted["mission"]["mission_id"]})
        self.assertEqual(stale.exception.code, "REMOTE_SESSION_LOST")
        self.assertEqual(self.count("drone_get_mission"), before)
        await self.wait_for(lambda: self.service.cancel.is_set())
        self.assertEqual(self.count("drone_execute_route"), 1)

    async def test_blocked_inference_renews_lease_until_channel_loss(self):
        self.adapter.finish.clear()
        await self.start_connector()
        session = SurveySession(mode="mock", drone_control_mode="mock")
        ready(session)
        vision = FakeVision()
        vision.block = asyncio.Event()
        async def publish(_event):
            pass
        self.runner = LiveMissionRunner(session, LiveCaptureCamera(), vision, publish,
            drone_client=DroneClient("lease-owner"), expected_mode="mock", allow_mock_tools=True,
            lease_seconds=.04, tick_seconds=.01, stop_verify_seconds=0)
        self.assertTrue((await self.runner.launch())["ok"])
        await self.wait_for(lambda: bool(vision.calls) or self.runner._work.done())
        self.assertTrue(vision.calls, session.data["error"])
        reads = self.count("drone_get_mission")
        await asyncio.sleep(2.2)  # Longer than the service lease, while inference stays blocked.
        self.assertGreater(self.count("drone_get_mission"), reads + 2)
        self.assertFalse(self.service.cancel.is_set())
        await self.hub.disconnect(self.hub.connection)
        await self.wait_for(lambda: self.service.cancel.is_set())
        self.assertEqual(self.count("drone_execute_route"), 1)
        await self.wait_for(lambda: self.runner._work.done())
        self.assertEqual(session.phase, "aborted")
        self.assertIn("MOCK", session.data["error"])

    async def test_local_mode_switch_refused_before_write_and_preflight(self):
        self.service.mode = "live"
        with self.assertRaises(DroneError):
            await self.connector.run_once()
        self.assertFalse(self.hub.connected)
        with self.assertRaises(DroneError):
            await self.local.call("drone_execute_route", {"caller_id": "mock-only", "request_id": "never",
                "arguments": {"profile_id": "mock-apriltag-corridor-v1", "site_revision": "mock-1",
                              "destination_ids": ["tag-3", "tag-1", "tag-2"]}})
        self.assertEqual(self.count("drone_execute_route"), 0)


if __name__ == "__main__":
    unittest.main()
