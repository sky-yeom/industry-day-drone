"""Actual loopback HTTP boundary; fake aircraft and vision only, no DJI/Azure IO."""
import asyncio
import base64
from http.server import ThreadingHTTPServer
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "drone-control" / "pc"))
from drone_nav.tool_control.server import Handler
from drone_nav.tool_control.service import MissionService, MockAdapter
from relay.camera import LiveCaptureCamera
from relay.drone_client import DroneClient, DroneError
from relay.live_mission import LiveMissionRunner
from relay.survey import SurveySession
from relay.test_live_mission import png
from relay.test_mission_runner import FakeVision
from relay.test_survey import Clock, ready


class FakeAircraft(MockAdapter):
    # Exercise live protocol semantics with no flight imports or SDK transport.
    mode, live_ready = "live", True
    profile_id, site_revision = "test-site", "test-revision"

    def __init__(self):
        self.arrive, self.finish, self.ground = (threading.Event() for _ in range(3))
        self.ground.set()
        self.cancel_seen = False
        self.run_count = 0

    def status(self):
        return {"connected": True, "ground_verified": self.ground.is_set(), "test_fake_aircraft": True}

    def run(self, mission, cancel, emit):
        self.run_count += 1
        self.ground.clear()
        emit(state="running")
        for visit in mission["visits"]:
            emit(visit_index=visit["visit_index"], visit_state="moving")
            while not self.arrive.wait(.005):
                if cancel.is_set():
                    return self.cancelled()
            if cancel.is_set():
                return self.cancelled()
            emit(visit_index=visit["visit_index"], visit_state="arrived", arrival_confirmed=True)
            for frame in range(2):
                emit(visit_index=visit["visit_index"], visit_state="captured", capture={
                    "capture_id": f"test-{visit['visit_index']}-{frame}", "arrival_confirmed": True,
                    "image_base64": base64.b64encode(png()).decode(),
                    "captured_at_unix_ms": int(time.time() * 1000)})
        while not self.finish.wait(.005):
            if cancel.is_set():
                return self.cancelled()
        return {"state": "awaiting_rc_landing", "route_completed": True,
            "ground_verified": False, "physical_stop_confirmed": True}

    def cancelled(self):
        self.cancel_seen = True
        return {"state": "outcome_unknown", "ground_verified": False,
            "physical_stop_confirmed": False, "verification_pending": True}


class RecordingService(MissionService):
    def __init__(self, *args, **kwargs):
        self.calls = []
        super().__init__(*args, **kwargs)

    def call(self, name, arguments, caller_id, request_id):
        self.calls.append((name, caller_id, request_id))
        return super().call(name, arguments, caller_id, request_id)


class HttpIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.adapter = FakeAircraft()
        self.service = RecordingService(Path(self.directory.name) / "test.sqlite3", self.adapter, lease_seconds=.3)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.service, self.http.api_token = self.service, "test-only-token-not-a-secret"
        self.server_thread = threading.Thread(target=lambda: self.http.serve_forever(poll_interval=.01), daemon=True)
        self.server_thread.start()
        real_connect = socket.socket.connect
        allowed = ("127.0.0.1", self.http.server_port)
        def loopback_only(sock, address):
            if address != allowed:
                raise AssertionError(f"No hardware/network IO allowed: {address}")
            return real_connect(sock, address)
        self.blocker = patch("socket.socket.connect", loopback_only)
        self.blocker.start()
        self.client = DroneClient("relay-http-test", token=self.http.api_token)
        # Production remains fixed to :8766. Only this local test owns a random port.
        self.client.base_url = f"http://127.0.0.1:{self.http.server_port}"
        self.session = SurveySession(clock=Clock(), mode="azure", drone_control_mode="live")
        ready(self.session)
        self.vision, self.events = FakeVision(), []
        async def publish(event):
            self.events.append(event)
        self.runner = LiveMissionRunner(self.session, LiveCaptureCamera(), self.vision, publish,
            drone_client=self.client, stop_verify_seconds=0)
        self.runner.lease_seconds = .04

    async def asyncTearDown(self):
        await self.runner.close()
        await asyncio.to_thread(self.http.shutdown)
        self.http.server_close()
        self.service.close()
        self.server_thread.join(timeout=1)
        self.blocker.stop()
        self.directory.cleanup()

    async def wait_for(self, condition):
        async def poll():
            while not condition():
                await asyncio.sleep(.01)
        await asyncio.wait_for(poll(), timeout=4)

    def count(self, name):
        return sum(call[0] == name for call in self.service.calls)

    async def test_http_arrival_capture_attribution_manual_landing_and_completion(self):
        self.assertTrue((await self.runner.launch())["ok"])
        await self.wait_for(lambda: self.adapter.run_count == 1)
        self.assertEqual(self.vision.calls, [])
        self.assertEqual(self.session.data["captures"], [])
        self.adapter.arrive.set()
        await self.wait_for(lambda: self.session.phase == "complete")
        self.assertEqual(self.count("drone_execute_route"), 1)
        self.assertEqual([c["monitorId"] for c in self.session.data["captures"]],
            ["monitor-3", "monitor-1", "monitor-2"])
        self.assertEqual([c["visitIndex"] for c in self.session.data["captures"]], [0, 1, 2])
        self.assertTrue(all(c["missionId"] == self.session.data["droneMissionId"] for c in self.session.data["captures"]))
        self.assertTrue(all(frame.image_bytes == png() for frame, _ in self.vision.calls))
        self.adapter.finish.set()
        await self.wait_for(lambda: self.session.data["droneStopState"] == "awaiting_manual")
        self.assertIsNotNone(self.service._active())
        self.adapter.ground.set()
        await self.wait_for(lambda: self.session.data["droneState"] == "completed")
        self.assertEqual(self.session.data["droneStopState"], "confirmed")
        self.assertIsNone(self.service._active())

    async def test_http_lease_survives_blocked_analysis_then_abort_retains_unknown_slot(self):
        self.vision.block = asyncio.Event()
        self.adapter.arrive.set()
        self.assertTrue((await self.runner.launch())["ok"])
        await self.wait_for(lambda: bool(self.vision.calls))
        before = self.count("drone_get_mission")
        await asyncio.sleep(.7)  # Exceeds the service lease while vision remains blocked.
        self.assertGreater(self.count("drone_get_mission"), before + 2)
        self.assertFalse(self.service.cancel.is_set())
        await self.runner.abort()
        await self.wait_for(lambda: self.adapter.cancel_seen)
        self.assertEqual(self.count("drone_stop_mission"), 1)
        self.assertNotEqual(self.session.data["droneStopState"], "confirmed")
        self.assertIsNotNone(self.service._active())
        self.assertFalse((await self.runner.retry())["ok"])
        self.assertEqual(self.adapter.run_count, 1)
        request_id = next(req for name, _, req in self.service.calls if name == "drone_execute_route")
        lookup = await self.client.lookup_request(request_id)
        self.assertEqual(lookup["mission"]["mission_id"], self.session.data["droneMissionId"])
        with self.assertRaises(DroneError) as error:
            await self.client.lookup_request("never-admitted")
        self.assertEqual(error.exception.code, "NOT_FOUND")
