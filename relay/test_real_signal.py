"""Record the Real outbound HTTP shape on a fake receiver; never contact a drone."""
import asyncio
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import patch
from urllib.request import ProxyHandler, Request, build_opener

from relay import config
from relay.drone_client import DroneClient
from relay.live_mission import LiveMissionRunner
from relay.survey import SurveySession
from relay.test_mission_runner import FakeVision
from relay.camera import LiveCaptureCamera


class RealSignalTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_selection_serializes_exact_route_once_to_isolated_http_receiver(self):
        calls, mission = [], None
        token = "offline-wire-fixture-not-an-aircraft-credential"

        class Receiver(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                nonlocal mission
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, dict(self.headers), payload))
                response = {"schema_version": 1, "ok": True, "status": "ok",
                            "execution_mode": "live", "physical_execution": True}
                name = self.path.rsplit("/", 1)[-1]
                if name == "drone_get_capabilities":
                    response.update(live_ready=True, profile_id="wire-fixture", site_revision="v1",
                        destinations=[{"destination_id": f"tag-{n}", "monitor_id": f"monitor-{n}"} for n in (1,2,3)],
                        supported_ordered_sequences=[["tag-2","tag-3","tag-1"]])
                elif name == "drone_get_status":
                    response.update(connected=True, ground_verified=True, active_mission_id=None)
                elif name == "drone_execute_route":
                    mission = {
                        "mission_id": "recorded-only", "state": "accepted",
                        "destination_ids": list(payload["arguments"]["destination_ids"]),
                        "stop_requested": False, "physical_stop_confirmed": False,
                        "visits": [{"visit_index": i, "destination_id": tag, "state": "pending",
                                    "arrival_confirmed": False, "capture_ids": []}
                                   for i, tag in enumerate(payload["arguments"]["destination_ids"])],
                    }
                    response["mission"] = deepcopy(mission)
                elif name == "drone_get_mission":
                    response["mission"] = deepcopy(mission)
                elif name == "drone_stop_mission":
                    mission.update(state="stopped", stop_requested=True)
                    response["mission"] = deepcopy(mission)
                else:
                    self.send_error(400)
                    return
                data = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        http = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        thread = threading.Thread(target=http.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        thread.start()
        original_opener = build_opener(ProxyHandler({}))
        test_case = self

        class WireRecorder:
            def open(self, req, timeout):
                test_case.assertTrue(req.full_url.startswith("http://127.0.0.1:8766/tools/"))
                name = req.full_url.rsplit("/", 1)[-1]
                test_case.assertIn(name, {"drone_get_capabilities","drone_get_status","drone_execute_route",
                                         "drone_get_mission","drone_stop_mission"})
                # Preserve serialized method/body/headers, but route exclusively to this test's socket.
                recorded = Request(f"http://127.0.0.1:{http.server_port}/tools/{name}",
                                   data=req.data, headers=dict(req.header_items()), method=req.get_method())
                return original_opener.open(recorded, timeout=timeout)

        session = SurveySession(mode="azure", drone_control_mode="live")
        session.confirm_prompt("wire fixture only")
        session.select_stop("monitor-2")
        session.select_stop("monitor-3")
        session.confirm_route()
        events = []
        async def publish(event):
            events.append(event)
        runner = None
        try:
            with patch.multiple(config, DRONE_RUN_MODE="real", DRONE_CONTROL_MODE="live",
                                DRONE_CONTROL_API_URL="http://127.0.0.1:8766",
                                DRONE_CONTROL_API_TOKEN=token, DRONE_CONTROL_TRANSPORT="local"), \
                    patch("relay.drone_client.request.build_opener", return_value=WireRecorder()):
                client = DroneClient("real-wire-test")
                runner = LiveMissionRunner(session, LiveCaptureCamera(), FakeVision(), publish,
                                           drone_client=client, stop_verify_seconds=0)
                result = await runner.launch()
                self.assertTrue(result["ok"])
                duplicate = await runner.launch()
                self.assertTrue(duplicate["ok"])
                await runner.close()
            outgoing = [call for call in calls if call[0] == "/tools/drone_execute_route"]
            self.assertEqual(len(outgoing), 1)
            _, headers, envelope = outgoing[0]
            normalized = {key.lower(): value for key, value in headers.items()}
            self.assertEqual(normalized["x-drone-expected-mode"], "live")
            self.assertEqual(normalized["authorization"], "Bearer " + token)
            self.assertEqual(envelope["arguments"], {
                "profile_id": "wire-fixture", "site_revision": "v1",
                "destination_ids": ["tag-2","tag-3","tag-1"],
            })
            self.assertEqual(envelope["caller_id"], "real-wire-test")
            self.assertTrue(envelope["request_id"])
            self.assertEqual(session.data["droneMissionId"], "recorded-only")
        finally:
            http.shutdown()
            await asyncio.to_thread(thread.join, 5)
            http.server_close()
