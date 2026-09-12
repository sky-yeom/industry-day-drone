"""Same September-12 UI protocol through embedded/HTTP mock and the actual relay."""
import asyncio
import base64
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from urllib.request import ProxyHandler, build_opener

import uvicorn
import websockets

from relay import config, server, tools
from relay.drone_client import DroneClient
from relay.live_mission import LiveMissionRunner
from relay.survey import SurveySession
from relay.test_server import Browser, Upstream, participant_turn, spoken_reply
from relay.test_mission_runner import FakeCamera, FakeVision
from relay.test_vision import completion, fake_http
from relay.camera import FixtureCamera
from relay.tool_target import resolve_tool_target
from relay.vision import AzureVision, ContractMockVision, MockVision, VisionError, create_providers

ROOT = Path(__file__).resolve().parents[1]
UI_REF = "a828ee0be17c14cd2444ec44d9c101b5c28f2421"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class IntegratedModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_mode_selects_only_backend_target_and_analysis_provider(self):
        with patch.object(config, "DRONE_RUN_MODE", "test"):
            self.assertIsInstance(create_providers("mock")[1], ContractMockVision)
            frame = await FakeCamera().capture("monitor-1")
            with self.assertRaises(VisionError):
                await ContractMockVision().analyze(frame, search_prompt="초록색 티셔츠를 입은 사람")
        with patch.object(config, "DRONE_RUN_MODE", "real"):
            self.assertIsInstance(create_providers("azure")[1], AzureVision)
        with patch.object(config, "DRONE_RUN_MODE", None):
            self.assertIsInstance(create_providers("mock")[1], MockVision)

    @unittest.skipUnless(shutil.which("node"), "Fixed mock requires Node")
    async def test_original_ws_commands_use_fixed_mock_and_keep_latest_map_contract(self):
        await self.run_ws_route(external=True)

    async def test_existing_dashboard_uses_embedded_mock_without_mock_process_or_http(self):
        with patch("subprocess.Popen", side_effect=AssertionError("No mock process")):
            await self.run_ws_route(external=False)

    async def test_original_images_and_azure_provider_use_the_same_ws_and_mock_tools_path(self):
        session, _ = fake_http(completion())
        with patch.multiple(config,
                AZURE_VISION_ENDPOINT="https://fixture-resource.openai.azure.com",
                AZURE_VISION_DEPLOYMENT="fixture-vision",
                AZURE_VISION_API_KEY="unit-test-key-not-a-secret"), \
                patch("relay.vision.aiohttp.ClientSession", return_value=session):
            await self.run_ws_route(external=False, analysis="azure")
        self.assertEqual(session.post.call_count, 3)
        camera = FixtureCamera()
        for call, monitor in zip(session.post.call_args_list, ("monitor-2", "monitor-3", "monitor-1")):
            payload = call.kwargs["json"]
            content = payload["messages"][1]["content"]
            self.assertIn(monitor, content[0]["text"])
            self.assertEqual(base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1]),
                             camera.read_image(monitor))
            self.assertEqual(payload["response_format"]["json_schema"]["schema"]["properties"]["box"]["type"], "null")
            self.assertIn("needsRescue", payload["response_format"]["json_schema"]["schema"]["required"])

    async def run_ws_route(self, *, external, analysis="mock"):
        process = None
        values = {"DRONE_RUN_MODE": "test", "TRIAGE_MODE": analysis}
        if external:
            port = free_port()
            values["DRONE_TEST_API_URL"] = f"http://127.0.0.1:{port}"
            process = subprocess.Popen(
                ["node", str(ROOT / "contracts" / "drone-tools" / "v1" / "mock.mjs"),
                 "--port", str(port), "--step-ms", "10", "--landing-ms", "40"],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        relay_socket = socket.socket()
        relay_socket.bind(("127.0.0.1", 0))
        relay_socket.listen()
        relay_port = relay_socket.getsockname()[1]
        app = None
        thread = None
        opener = build_opener(ProxyHandler({}))
        try:
            if process:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        with opener.open(values["DRONE_TEST_API_URL"] + "/health", timeout=.2):
                            break
                    except OSError:
                        if process.poll() is not None:
                            self.fail("Fixed mock exited during startup")
                        await asyncio.sleep(.05)
                else:
                    self.fail("Fixed mock did not start")
            target = resolve_tool_target(values)
            with patch.multiple(config, DRONE_RUN_MODE=target.run_mode, TRIAGE_MODE=target.triage_mode,
                                DRONE_CONTROL_MODE=target.control_mode, DRONE_CONTROL_API_URL=target.api_url,
                                DRONE_CONTROL_API_TOKEN=target.api_token, DRONE_CONTROL_USE_TOOLS=True,
                                DRONE_CONTROL_TRANSPORT=target.transport, RELAY_LOCAL_DIRECT=True,
                                HOST="127.0.0.1", PORT=relay_port), \
                    patch.object(server, "credential", side_effect=AssertionError("No Azure in protocol test")), \
                    (nullcontext() if external else patch.object(
                        DroneClient, "_http", side_effect=AssertionError("No HTTP mock server"))):
                app = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=relay_port, log_level="error"))
                thread = threading.Thread(target=app.run, kwargs={"sockets": [relay_socket]}, daemon=True)
                thread.start()
                deadline = time.monotonic() + 5
                while not app.started and time.monotonic() < deadline:
                    await asyncio.sleep(.02)
                self.assertTrue(app.started)
                def read_config():
                    with opener.open(f"http://127.0.0.1:{relay_port}/api/config", timeout=5) as response:
                        return json.load(response)
                info = await asyncio.to_thread(read_config)
                self.assertEqual(info["runMode"], "test")
                self.assertEqual(info["toolEndpoint"], target.api_url)
                self.assertTrue(info["droneReady"], info["droneError"])
                self.assertTrue(info["droneControlUseTools"])
                async with websockets.connect(f"ws://127.0.0.1:{relay_port}/ws?voice=0",
                                              origin="http://127.0.0.1:13001", proxy=None,
                                              max_size=20 * 1024 * 1024) as ws:
                    state = None
                    async def receive():
                        nonlocal state
                        event = json.loads(await asyncio.wait_for(ws.recv(), 20))
                        self.assertNotEqual(event["type"], "relay.error", event)
                        if event["type"] == "route.state":
                            state = event["state"]
                        return event
                    while state is None:
                        await receive()
                    self.assertEqual(state["droneControlMode"], "mock")
                    self.assertEqual(state["droneToolExecution"], "mock")
                    sequence = 0
                    async def command(name, args):
                        nonlocal sequence
                        sequence += 1
                        rid = f"command-{sequence}"
                        await ws.send(json.dumps({"type": "command", "name": name, "args": args, "requestId": rid}))
                        while True:
                            event = await receive()
                            if event["type"] == "tool.finished" and event["id"] == rid:
                                self.assertTrue(event["result"]["ok"], event)
                                return
                    await command("confirm_prompt", {"prompt_text": "초록색 티셔츠를 입은 사람",
                                                      "appearance_constraints": [], "unsupported_appearance": []})
                    await ws.send(json.dumps({"type": "route_intro.ready"}))
                    await command("select_stop", {"monitor": "monitor-2"})
                    await command("select_stop", {"monitor": "monitor-3"})
                    await command("confirm_route", {})
                    while state["missionPhase"] != "ready":
                        await receive()
                    self.assertEqual(state["confirmedRoute"], ["monitor-2", "monitor-3", "monitor-1"])
                    await command("launch_mission", {})
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        event = await receive()
                        if state["missionPhase"] == "complete" and state["droneState"] == "completed":
                            break
                    self.assertEqual(state["droneState"], "completed")
                    self.assertEqual(len(state["captures"]), 3)
                    self.assertTrue(all(c["mode"] == analysis and c["imageUrl"].startswith("data:image/png;base64,")
                                        for c in state["captures"]))
                    if analysis == "azure":
                        self.assertTrue(all(c["evidence"]["targetPresent"] and c["evidence"]["box"] is None
                                            for c in state["captures"]))
                    self.assertIsNotNone(state["droneMissionId"])
                    self.assertNotIn("PROFILE_UNAVAILABLE", str(state["error"]))
        finally:
            if app:
                app.should_exit = True
            if thread:
                await asyncio.to_thread(thread.join, 10)
            relay_socket.close()
            if process:
                process.terminate()
                await asyncio.to_thread(process.wait, 5)

    async def test_same_voice_and_animation_handshake_in_both_modes_without_commands_to_aircraft(self):
        for selected, wire_mode, analysis in (("test", "mock", "mock"), ("real", "live", "azure")):
            with self.subTest(mode=selected), patch.multiple(config, DRONE_RUN_MODE=selected,
                    DRONE_CONTROL_MODE=wire_mode, TRIAGE_MODE=analysis, DRONE_CONTROL_USE_TOOLS=True,
                    DRONE_CONTROL_TRANSPORT="local"), \
                    patch.object(server, "DroneClient", return_value=SimpleNamespace(readiness=lambda: None)):
                session = SurveySession(mode=analysis, drone_control_mode=wire_mode)
                browser = Browser()
                bridge = server.Bridge(browser, session, (FakeCamera(), FakeVision()))
                self.assertIsInstance(bridge.runner, LiveMissionRunner)
                self.assertEqual(bridge.runner.expected_mode, wire_mode)
                self.assertEqual(server.build_session()["session"]["instructions"],
                                 tools.voice_context()["instructions"])
                upstream = Upstream()
                bridge.upstream = upstream
                bridge.runner.close = AsyncMock()
                try:
                    participant_turn(bridge, "사람을 찾아줘")
                    self.assertTrue(session.prepare_prompt("사람을 찾아줘", [], [])["ok"])
                    spoken_reply(bridge)
                    await bridge.handle_tool_call({
                        "name": "confirm_prompt", "call_id": "prompt",
                        "arguments": "{}"},
                        turn=participant_turn(bridge, "응"))
                    self.assertTrue(bridge._route_intro_pending)
                    self.assertFalse(any(e["type"] == "response.create" for e in upstream.sent))
                    browser.incoming.put_nowait(json.dumps({"type": "route_intro.ready"}))
                    browser.incoming.put_nowait(None)
                    with self.assertRaises(server.WebSocketDisconnect):
                        await bridge.pump_browser()
                    self.assertFalse(bridge._route_intro_pending)
                    self.assertTrue(any(e["type"] == "response.create" for e in upstream.sent))
                finally:
                    await bridge.close()


class LatestUIContractTests(unittest.TestCase):
    def test_existing_deployment_copy_layout_runs_without_node_or_standalone_contract_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "relay", root / "relay",
                            ignore=shutil.ignore_patterns(".venv", "__pycache__", "test_*.py"))
            for name in ("data", "public/monitors"):
                shutil.copytree(ROOT / name, root / name)
            schema = Path("drone-control/integration/speech_control_contract/tools.json")
            (root / schema).parent.mkdir(parents=True)
            shutil.copy2(ROOT / schema, root / schema)
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("DRONE_", "RELAY_", "TRIAGE_", "VOICE_", "AZURE_"))
                   and key != "PYTHONPATH"}
            env.update(RELAY_HOST="0.0.0.0", TRIAGE_MODE="azure")
            code = (
                "import sys,asyncio,json; from unittest.mock import patch; sys.path.insert(0,sys.argv[1])\n"
                "with patch('subprocess.Popen',side_effect=AssertionError('No extra process')):\n"
                " from relay import server\n"
                " async def check():\n"
                "  with patch('socket.socket.connect',side_effect=AssertionError('No external connection')):\n"
                "   await server.validate_transport_configuration()\n"
                "   return await server.api_config()\n"
                " print(json.dumps(asyncio.run(check())))\n"
            )
            result = subprocess.run([sys.executable, "-B", "-I", "-c", code, str(root)],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            info = json.loads(result.stdout)
            self.assertEqual((info["runMode"], info["droneControlTransport"]), ("test", "inprocess"))
            self.assertTrue(info["droneReady"], info["droneError"])
            self.assertTrue(info["droneControlUseTools"])
            self.assertEqual(info["voice"], "shimmer")

    def test_latest_ui_and_voice_source_match_team_commit_ignoring_checkout_newlines(self):
        for relative in ("app/page.tsx", "lib/voiceClient.ts", "lib/types.ts",
                         "relay/tools.py", "data/emergency-triage.json"):
            result = subprocess.run(["git", "show", f"{UI_REF}:{relative}"], cwd=ROOT, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.replace(b"\r\n", b"\n"),
                             (ROOT / relative).read_bytes().replace(b"\r\n", b"\n"), relative)

    def test_unchanged_route_intro_ready_is_supported_by_merged_relay(self):
        frontend = (ROOT / "lib" / "voiceClient.ts").read_text(encoding="utf-8")
        backend = (ROOT / "relay" / "server.py").read_text(encoding="utf-8")
        self.assertIn('type: "route_intro.ready"', frontend)
        self.assertIn('mtype == "route_intro.ready"', backend)


@unittest.skipUnless(os.name == "nt", "Windows launcher")
class LauncherModeTests(unittest.TestCase):
    def test_test_preflight_never_requires_real_nav_or_control_python(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / "connections.env"
            env_path.write_text(
                "VOICE_LIVE_RESOURCE=offline-resource\n"
                "VOICE_LIVE_VOICE=wrong-legacy-voice\n"
                "DRONE_CONTROL_MODE=live\nDRONE_CONTROL_ENABLE_LIVE=1\n"
                "DRONE_CONTROL_CONFIG_PATH=C:\\does-not-exist\\nav.json\n"
                "DRONE_CONTROL_SITE_CONFIG=C:\\does-not-exist\\site.json\n"
                "DRONE_CONTROL_API_TOKEN=offline-secret-sentinel\n", encoding="utf-8")
            env = dict(os.environ, TEST_REPO=str(ROOT), TEST_SETTINGS=str(env_path), TEST_PYTHON=sys.executable)
            code = (
                "$ErrorActionPreference='Stop';$before=[Environment]::GetEnvironmentVariables('Process');"
                "& (Join-Path $env:TEST_REPO 'scripts\\start-integrated.ps1') -Mode Test -AnalysisMode Mock -NoWeb -CheckOnly "
                "-EnvFile $env:TEST_SETTINGS -RelayPython $env:TEST_PYTHON -ControlPython C:\\missing\\python.exe;"
                "if(-not $?){throw 'Test configuration failed'};"
                "foreach($k in @('DRONE_RUN_MODE','DRONE_CONTROL_API_TOKEN','VOICE_LIVE_VOICE','PYTHONPATH')){"
                "if($before[$k] -cne [Environment]::GetEnvironmentVariable($k,'Process')){throw ('Environment leaked: '+$k)}}"
            )
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", code],
                                    cwd=ROOT, env=env, text=True, encoding="utf-8", capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("runMode=test", result.stdout)
            self.assertIn("wireMode=mock", result.stdout)
            self.assertNotIn("offline-secret-sentinel", result.stdout + result.stderr)
