import asyncio
import base64
from copy import deepcopy
import hashlib
import struct
import unittest
from unittest.mock import patch
import zlib

from relay.camera import CaptureError, LiveCaptureCamera
from relay.drone_client import DroneClient
from relay.live_mission import LiveMissionRunner
from relay.survey import SurveySession
from relay.test_drone_client import response
from relay.test_mission_runner import FakeVision, settle
from relay.test_survey import Clock, NEGATIVE, ready


def png(rgb=b"\0\xff\0"):
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0" + rgb)) + chunk(b"IEND", b""))


class Backend:
    def __init__(self):
        self.calls = []
        self.mission = None
        self.arrived = False
        self.capture_ready = True
        self.frames_per_visit = 2
        self.capture_patch = {}
        self.accept_timeout = False
        self.stop_timeout = False
        self.execution_mode = "live"
        self.active_request = None
        self.fail_reads = False

    async def call(self, name, envelope):
        self.calls.append((name, deepcopy(envelope)))
        await asyncio.sleep(0)
        if name == "drone_get_mission" and self.fail_reads:
            raise OSError("fake read transport failure")
        if name == "drone_get_capabilities":
            value = response(live_ready=True, profile_id="site-v1", site_revision="rev-1",
                destinations=[dict(destination_id=f"tag-{n}", monitor_id=f"monitor-{n}") for n in (1, 2, 3)],
                supported_ordered_sequences=[["tag-3", "tag-1", "tag-2"]])
            value["execution_mode"] = self.execution_mode
            return value
        if name == "drone_get_status":
            return response(active_mission_id=self.mission["mission_id"] if self.mission else None,
                active_request=self.active_request)
        if name == "drone_execute_route":
            route = envelope["arguments"]["destination_ids"]
            self.mission = dict(mission_id="live-1", state="running", destination_ids=route,
                stop_requested=False, physical_stop_confirmed=False,
                visits=[dict(visit_index=i, destination_id=dest, state="pending", arrival_confirmed=False,
                    capture_ids=[]) for i, dest in enumerate(route)])
            self.active_request = dict(caller_id=envelope["caller_id"], request_id=envelope["request_id"], mission_id="live-1")
            if self.accept_timeout:
                raise TimeoutError()
        if name == "drone_stop_mission":
            if self.stop_timeout:
                raise TimeoutError()
            self.mission.update(state="stop_requested", stop_requested=True)
        if self.arrived:
            for visit in self.mission["visits"]:
                visit.update(state="captured", arrival_confirmed=True,
                    capture_ids=[f"frame-{visit['visit_index']}-{n}" for n in range(self.frames_per_visit)])
        if name == "drone_get_captures":
            records = []
            if self.capture_ready:
                for visit in self.mission["visits"]:
                    for cid in visit["capture_ids"]:
                        records.append(dict(mission_id="live-1", visit_index=visit["visit_index"],
                            destination_id=visit["destination_id"], capture_id=cid, arrival_confirmed=True,
                            content_type="image/png", image_base64=base64.b64encode(png()).decode(),
                            captured_at_unix_ms=1234567890000, sha256=hashlib.sha256(png()).hexdigest()) | self.capture_patch)
            return response(mission_id="live-1", captures=records)
        return response(mission=deepcopy(self.mission))


class LiveMissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        blocker = patch("socket.socket.connect", side_effect=AssertionError("offline test"))
        blocker.start()
        self.addCleanup(blocker.stop)
        self.clock = Clock()
        self.session = SurveySession(clock=self.clock, mode="azure", drone_control_mode="live")
        ready(self.session)
        self.backend, self.vision = Backend(), FakeVision()
        self.client = DroneClient("relay-test", token="test-only", transport=self.backend.call)
        self.events = []
        async def publish(event):
            self.events.append(event)
        async def sleep(_seconds):
            await asyncio.sleep(0)
        self.runner = LiveMissionRunner(self.session, LiveCaptureCamera(), self.vision, publish,
            drone_client=self.client, sleep=sleep, stop_verify_seconds=0)

    async def asyncTearDown(self):
        await self.runner.close()

    def commands(self, name):
        return [call for call in self.backend.calls if call[0] == name]

    async def test_whole_route_once_waits_for_actual_arrival_and_matching_frames(self):
        first, second = await asyncio.gather(self.runner.launch(), self.runner.launch())
        self.assertTrue(first["ok"] and second["ok"])
        await settle(lambda: len(self.commands("drone_get_mission")) >= 3)
        self.assertEqual(self.session.data["captures"], [])
        self.assertEqual(self.vision.calls, [])
        self.assertEqual(len(self.commands("drone_execute_route")), 1)
        self.assertEqual(self.commands("drone_execute_route")[0][1]["arguments"]["destination_ids"], ["tag-3", "tag-1", "tag-2"])
        self.backend.arrived = True
        await settle(lambda: self.session.phase == "complete")
        self.assertEqual([c["monitorId"] for c in self.session.data["captures"]], ["monitor-3", "monitor-1", "monitor-2"])
        self.assertEqual([c["visitIndex"] for c in self.session.data["captures"]], [0, 1, 2])
        self.assertTrue(all(frame.image_bytes == png() for frame, _ in self.vision.calls))
        self.assertEqual(self.session.data["droneStopState"], "not_requested")
        self.backend.mission.update(state="awaiting_rc_landing", physical_stop_confirmed=True)
        await settle(lambda: self.session.data["droneStopState"] == "awaiting_manual")
        self.backend.mission.update(state="completed", physical_stop_confirmed=True)
        await settle(lambda: self.runner._work.done())
        self.assertEqual(self.session.data["droneStopState"], "confirmed")

    async def test_negative_recapture_uses_second_pc_frame_without_new_movement(self):
        self.backend.arrived = True
        self.vision.results = [NEGATIVE]
        await self.runner.launch()
        await settle(lambda: self.session.phase == "complete")
        self.assertEqual([f.id for f, _ in self.vision.calls][:2], ["frame-0-0", "frame-0-1"])
        self.assertEqual(len(self.commands("drone_execute_route")), 1)

    async def test_negative_result_waits_for_second_frame_publication(self):
        self.backend.arrived = True
        self.backend.frames_per_visit = 1
        self.vision.results = [NEGATIVE]
        await self.runner.launch()
        await settle(lambda: len(self.commands("drone_get_captures")) >= 4)
        self.assertEqual([f.id for f, _ in self.vision.calls], ["frame-0-0"])
        self.backend.frames_per_visit = 2
        await settle(lambda: self.session.phase == "complete")
        self.assertEqual([f.id for f, _ in self.vision.calls][:2], ["frame-0-0", "frame-0-1"])
        self.assertEqual(len(self.commands("drone_execute_route")), 1)

    async def test_execute_timeout_reconciles_exact_caller_intent_and_requests_stop(self):
        self.backend.accept_timeout = True
        self.assertFalse((await self.runner.launch())["ok"])
        self.assertEqual(len(self.commands("drone_execute_route")), 1)
        self.assertEqual(len(self.commands("drone_stop_mission")), 1)
        self.assertEqual(self.session.data["droneStopState"], "stop_requested")
        self.assertFalse((await self.runner.retry())["ok"])
        self.assertFalse((await self.runner.launch())["ok"])
        self.assertEqual(len(self.commands("drone_execute_route")), 1)

    async def test_abort_and_close_request_one_stop_without_claiming_landing(self):
        await self.runner.launch()
        await self.runner.abort()
        await self.runner.close()
        self.assertEqual(len(self.commands("drone_stop_mission")), 1)
        self.assertEqual(self.session.data["droneStopState"], "stop_requested")
        self.assertEqual(self.session.phase, "aborted")

    async def test_deadline_stops_actual_mission(self):
        await self.runner.launch()
        self.clock.advance(60000)
        await settle(lambda: self.runner._deadlines.done())
        self.assertEqual(len(self.commands("drone_stop_mission")), 1)
        self.assertEqual(self.session.phase, "complete")

    async def test_unknown_stop_never_claims_physical_confirmation(self):
        await self.runner.launch()
        self.backend.stop_timeout = True
        await self.runner.abort()
        self.assertEqual(self.session.data["droneStopState"], "unknown")
        self.assertEqual(len(self.commands("drone_stop_mission")), 1)

    async def test_lease_renews_during_blocked_analysis_and_failure_stops(self):
        self.runner.lease_seconds = 0.002
        self.backend.arrived = True
        self.vision.block = asyncio.Event()
        await self.runner.launch()
        await settle(lambda: len(self.vision.calls) == 1)
        before = len(self.commands("drone_get_mission"))
        async def renewed():
            while len(self.commands("drone_get_mission")) <= before:
                await asyncio.sleep(0.002)
        await asyncio.wait_for(renewed(), timeout=1)
        self.assertGreater(len(self.commands("drone_get_mission")), before)
        self.backend.fail_reads = True
        await asyncio.wait_for(self.runner._lease, timeout=1)
        self.assertEqual(self.session.phase, "aborted")
        self.assertEqual(len(self.commands("drone_stop_mission")), 1)
        self.assertTrue(self.runner._work.done())

    async def test_mock_control_response_or_mock_analysis_never_starts_live_flight(self):
        self.backend.execution_mode = "mock"
        self.assertFalse((await self.runner.launch())["ok"])
        self.assertEqual(self.commands("drone_execute_route"), [])
        self.backend.execution_mode = "live"
        self.session.data["mode"] = "mock"
        self.assertFalse((await self.runner.launch())["ok"])
        self.assertEqual(self.commands("drone_execute_route"), [])

    async def test_wrong_mission_visit_or_corrupt_pixels_abort_before_analysis(self):
        self.backend.arrived = True
        self.backend.capture_patch = {"mission_id": "another-mission"}
        await self.runner.launch()
        await settle(lambda: self.runner._work.done())
        self.assertEqual(self.vision.calls, [])
        self.assertEqual(self.session.phase, "aborted")
        self.assertEqual(len(self.commands("drone_stop_mission")), 1)

    async def test_live_capture_requires_verified_pixels_and_exact_attribution(self):
        record = dict(mission_id="live-1", visit_index=0, destination_id="tag-3", capture_id="image-1",
            arrival_confirmed=True, content_type="image/png", image_base64=base64.b64encode(png()).decode(),
            captured_at_unix_ms=1234567890000, sha256=hashlib.sha256(png()).hexdigest())
        for changes in ({"visit_index": True}, {"visit_index": 1}, {"arrival_confirmed": False},
                        {"destination_id": "tag-1"}, {"sha256": "bad"}, {"image_base64": ""},
                        {"content_type": "text/html"}, {"image_base64": "https://example.com/frame.png"}):
            with self.subTest(changes=changes), self.assertRaises(CaptureError):
                LiveCaptureCamera.from_record(record | changes, mission_id="live-1", visit_index=0,
                    destination_id="tag-3", monitor_id="monitor-3")


class MockToolGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_tool_run_requires_opt_in_capture_capability_and_nonphysical_results(self):
        for opt_in, capture_ready, physical in ((False, True, False), (True, False, False), (True, True, True)):
            session = SurveySession(mode="mock", drone_control_mode="mock")
            ready(session)
            calls = []

            async def transport(name, envelope):
                calls.append(name)
                return dict(schema_version=1, ok=True, execution_mode="mock", physical_execution=physical,
                            mock_capture_ready=capture_ready)

            async def publish(_event):
                pass

            runner = LiveMissionRunner(session, LiveCaptureCamera(), FakeVision(), publish,
                drone_client=DroneClient("mock-tool-test", token="test-only", transport=transport),
                expected_mode="mock", allow_mock_tools=opt_in)
            try:
                outcome = await runner.launch()
                self.assertFalse(outcome["ok"])
                self.assertIn("MOCK", outcome["facts"])
                self.assertNotIn("drone_execute_route", calls)
            finally:
                await runner.close()


if __name__ == "__main__":
    unittest.main()
