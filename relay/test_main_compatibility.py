"""Backend-only integration must preserve main's public browser and cloud contract."""
import ast
import asyncio
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from relay import config, server, tools
from relay.camera import FixtureCamera
from relay.survey import SurveySession


ROOT = Path(__file__).resolve().parents[1]


class MainCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnected_remote_pc_is_not_misreported_as_disabled_tools(self):
        with patch.object(config, "TRIAGE_MODE", "mock"), \
             patch.object(config, "DRONE_CONTROL_MODE", "mock"), \
             patch.object(config, "DRONE_CONTROL_TRANSPORT", "remote"), \
             patch.object(config, "DRONE_CONTROL_USE_TOOLS", True), \
             patch.object(server, "get_device_hub", return_value=SimpleNamespace(connected=False, mode="mock")):
            result = await server.api_config()
        self.assertFalse(result["remoteConnected"])
        self.assertFalse(result["droneReady"])
        self.assertNotIn("DRONE_CONTROL_USE_TOOLS=1", result["droneError"])

    async def test_existing_public_config_fields_remain_available_with_mock_default(self):
        with patch.object(config, "TRIAGE_MODE", "mock"), patch.object(config, "DRONE_CONTROL_MODE", "mock"):
            result = await server.api_config()
        self.assertTrue({"model", "voice", "region", "mode", "visionReady", "visionError"} <= result.keys())
        self.assertTrue(result["visionReady"])
        self.assertEqual(result["droneControlMode"], "mock")

    async def test_existing_tools_snapshot_and_full_frame_capture_contract_is_additive(self):
        self.assertEqual({tool["name"] for tool in tools.TOOLS}, {
            "confirm_prompt", "select_stop", "confirm_route", "clear_route",
            "launch_mission", "retry_mission", "abort_mission", "get_state"})
        session = SurveySession()
        baseline = {"phase", "draftRoute", "confirmedRoute", "runId", "revision", "missionPhase",
                    "mode", "elapsedMs", "clockRunning", "activeMonitorId", "people", "captures",
                    "score", "error", "promptPhase", "userPromptText",
                    "appearanceConstraints", "unsupportedAppearance"}
        self.assertTrue(baseline <= session.snapshot().keys())
        self.assertTrue(session.confirm_prompt("green shirt")["ok"])
        for destination in ("monitor-3", "monitor-1"):
            self.assertTrue(session.select_stop(destination)["ok"])
        self.assertTrue(session.confirm_route()["ok"])
        self.assertTrue(session.launch_mission()["ok"])
        self.assertTrue(session.set_operation("capturing", "monitor-3", session.run_id))
        frame = await FixtureCamera().capture("monitor-3")
        self.assertTrue(session.add_capture(frame, session.run_id))
        capture = session.snapshot()["captures"][0]
        self.assertTrue({"id", "monitorId", "imageUrl", "capturedAtMs", "status", "evidence", "mode"} <= capture.keys())
        self.assertTrue(capture["imageUrl"].startswith("data:image/png;base64,"))
        self.assertEqual(capture["monitorId"], "monitor-3")
        self.assertEqual(capture["status"], "captured")
        self.assertEqual(session.snapshot()["confirmedRoute"], ["monitor-3", "monitor-1", "monitor-2"])

    def test_cloud_image_import_needs_schema_but_not_pc_sdk_or_navigation_package(self):
        dockerfile = (ROOT / "relay" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "COPY drone-control/integration/speech_control_contract/tools.json "
            "./drone-control/integration/speech_control_contract/tools.json", dockerfile)
        with TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / "relay", root / "relay",
                            ignore=shutil.ignore_patterns(".venv", "__pycache__", ".env", "*.local.*"))
            shutil.copytree(ROOT / "data", root / "data")
            shutil.copytree(ROOT / "public" / "monitors", root / "public" / "monitors")
            schema = Path("drone-control/integration/speech_control_contract/tools.json")
            (root / schema).parent.mkdir(parents=True)
            shutil.copyfile(ROOT / schema, root / schema)
            script = """
import asyncio, sys
from relay import server, config
from relay import pc_connector
config.TRIAGE_MODE = config.DRONE_CONTROL_MODE = 'mock'
assert asyncio.run(server.api_config())['visionReady']
assert not any(name == 'drone_nav' or name.startswith('drone_nav.') for name in sys.modules)
"""
            result = subprocess.run([sys.executable, "-c", script], cwd=root, text=True,
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_main_voice_message_types_are_supported_without_frontend_edits(self):
        frontend = (ROOT / "lib" / "voiceClient.ts").read_text(encoding="utf-8")
        backend = (ROOT / "relay" / "server.py").read_text(encoding="utf-8")
        tree = ast.parse(backend)
        strings = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        for event in ("relay.ready", "route.state", "tool.started", "tool.finished",
                      "mission.launch", "mission.launch.response", "mission.launch.done",
                      "mission.debrief.response", "mission.debrief.done", "mission.debrief.failed"):
            self.assertIn(event, frontend)
            self.assertIn(event, strings)


if __name__ == "__main__":
    unittest.main()
