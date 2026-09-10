import base64
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from urllib import request, error
from http.server import ThreadingHTTPServer

from drone_nav.tool_control.service import MissionService, MockAdapter, ToolError
from drone_nav.tool_control.server import Handler
from drone_nav.tool_control.live import ground_verified, LiveAdapter


class WaitingAdapter(MockAdapter):
    mode = "live"
    live_ready = True

    def __init__(self):
        self.started = threading.Event()
        self.calls = 0
        self.grounded = False

    def status(self):
        return {"connected": True, "ground_verified": self.grounded}

    def run(self, mission, cancel, emit):
        self.calls += 1
        self.started.set()
        emit(state="running")
        cancel.wait(3)
        return {"state": "outcome_unknown", "physical_stop_confirmed": False,
                "verification_pending": True, "ground_verified": False}


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "mission.sqlite"
        self.adapter = WaitingAdapter()
        self.svc = MissionService(self.path, self.adapter)
        self.args = {"profile_id": self.adapter.profile_id, "site_revision": self.adapter.site_revision,
                     "destination_ids": ["tag-3", "tag-1", "tag-2"]}

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def call(self, name, args=None, caller="relay-one", rid="request-one"):
        return self.svc.call(name, args or {}, caller, rid)

    def execute(self):
        return self.call("drone_execute_route", self.args)["mission"]

    def wait_worker(self):
        limit = time.perf_counter() + 1
        while self.svc.running_id is not None and time.perf_counter() < limit:
            time.sleep(.01)

    def test_duplicate_execute_is_same_durable_admission(self):
        first = self.execute()
        self.assertTrue(self.adapter.started.wait(1))
        second = self.execute()
        self.assertEqual(first, second)
        self.assertEqual(self.adapter.calls, 1)
        self.assertEqual(self.svc.lookup_request("relay-one", "request-one")["mission"], first)

    def test_lost_caller_lease_cancels_without_replay(self):
        self.svc.lease_seconds = .12
        mission = self.execute()
        self.assertTrue(self.adapter.started.wait(1))
        limit = time.perf_counter() + 1
        while not self.svc.cancel.is_set() and time.perf_counter() < limit:
            time.sleep(.02)
        self.assertTrue(self.svc.cancel.is_set())
        self.wait_worker()
        recorded = self.call("drone_get_mission", {"mission_id": mission["mission_id"]})["mission"]
        self.assertTrue(recorded["stop_requested"])
        self.assertEqual(recorded["stop_reason"], "caller_lease_expired")
        self.assertEqual(self.adapter.calls, 1)

    def test_shutdown_rejects_new_admission_and_keeps_cancel_latched(self):
        self.svc.begin_shutdown()
        with self.assertRaises(ToolError) as cm:
            self.execute()
        self.assertEqual(cm.exception.code, "SERVICE_SHUTTING_DOWN")
        self.assertTrue(self.svc.cancel.is_set())
        self.assertEqual(self.adapter.calls, 0)

    def test_queued_shutdown_never_dispatches_the_accepted_route(self):
        with self.svc.lock:
            mission = self.execute()
            self.svc.begin_shutdown()
        self.svc.worker.join(1)
        self.assertEqual(self.adapter.calls, 0)
        current = self.call("drone_get_mission", {"mission_id": mission["mission_id"]})["mission"]
        self.assertTrue(current["cancelled_before_execution"])
        self.assertFalse(current["ground_verified"])

    def test_expired_lease_read_cannot_renew_it_before_timer_tick(self):
        self.execute()
        with self.svc.lock:
            self.svc.lease_deadline = time.perf_counter() - 1
            mid = self.svc.running_id or self.svc.pending
            current = self.call("drone_get_mission", {"mission_id": mid})["mission"]
            self.assertTrue(current["stop_requested"])
            self.assertTrue(self.svc.cancel.is_set())

    def test_request_id_argument_change_is_conflict(self):
        self.execute()
        self.args["destination_ids"] = ["tag-1", "tag-2", "tag-3"]
        with self.assertRaises(ToolError) as cm:
            self.execute()
        self.assertEqual(cm.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_second_caller_cannot_start_or_stop_active_mission(self):
        mission = self.execute()
        for name, args in [("drone_execute_route", self.args), ("drone_stop_mission", {"mission_id": mission["mission_id"]})]:
            with self.assertRaises(ToolError):
                self.call(name, args, caller="relay-two")

    def test_unknown_stop_retains_slot_until_fresh_ground_proof(self):
        mission = self.execute()
        self.assertTrue(self.adapter.started.wait(1))
        ack = self.call("drone_stop_mission", {"mission_id": mission["mission_id"]}, rid="stop-one")
        self.assertTrue(ack["stop_requested"])
        self.assertFalse(ack["physical_stop_confirmed"])
        self.wait_worker()
        status = self.call("drone_get_status")
        self.assertEqual(status["active_request"], {"caller_id": "relay-one", "request_id": "request-one", "mission_id": mission["mission_id"]})
        with self.assertRaises(ToolError):
            self.call("drone_execute_route", self.args, rid="request-two")
        self.adapter.grounded = True
        self.assertIsNone(self.call("drone_get_status")["active_mission_id"])
        self.assertTrue(self.call("drone_get_mission", {"mission_id": mission["mission_id"]})["mission"]["physical_stop_confirmed"])

    def test_restart_keeps_incomplete_work_and_never_replays(self):
        mission = self.execute()
        self.assertTrue(self.adapter.started.wait(1))
        self.svc.close()
        replacement = WaitingAdapter()
        self.svc = MissionService(self.path, replacement)
        current = self.call("drone_get_mission", {"mission_id": mission["mission_id"]})["mission"]
        self.assertEqual(current["state"], "outcome_unknown")
        self.assertTrue(current["interrupted"])
        self.assertEqual(replacement.calls, 0)
        self.assertEqual(self.execute()["mission_id"], mission["mission_id"])
        self.assertEqual(replacement.calls, 0)

    def test_rejects_unsupported_route_site_and_raw_setpoint(self):
        for bad in [{**self.args, "destination_ids": ["tag-1", "tag-1", "tag-2"]},
                    {**self.args, "site_revision": "old-site"},
                    {**self.args, "velocity": 1.}]:
            with self.assertRaises(ToolError):
                self.call("drone_execute_route", bad)
        with self.assertRaises(ToolError):
            self.call("attitude", {"right": 1.})

    def test_capture_requires_arrival_and_is_bound_to_mission_visit(self):
        mission = self.execute()
        mid = mission["mission_id"]
        png = base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode()
        capture = {"capture_id": "frame-one", "arrival_confirmed": True, "image_base64": png}
        with self.assertRaises(ToolError):
            self.svc._emit(mid, visit_index=0, capture=dict(capture))
        self.svc._emit(mid, visit_index=0, arrival_confirmed=True, visit_state="arrived")
        self.svc._emit(mid, visit_index=0, capture=dict(capture), visit_state="captured")
        returned = self.call("drone_get_captures", {"mission_id": mid})["captures"][0]
        self.assertEqual(returned["destination_id"], "tag-3")
        self.assertEqual(returned["mission_id"], mid)
        self.assertEqual(len(returned["sha256"]), 64)

    def test_ground_requires_fresh_real_motor_and_flying_values(self):
        raw = {"is_flying": False, "are_motors_on": False,
               "is_flying_age_ms": 20, "are_motors_on_age_ms": 30,
               "armed": False, "vs_enabled": False, "vs_authority": "RC"}
        self.assertTrue(ground_verified(raw))
        for key, value in [("are_motors_on", None), ("are_motors_on", True),
                           ("are_motors_on_age_ms", 501), ("is_flying_age_ms", True),
                           ("vs_authority", "MSDK")]:
            self.assertFalse(ground_verified({**raw, key: value}))

    def test_unconfirmed_site_does_not_connect(self):
        site = Path(__file__).resolve().parents[2] / "integration" / "site.example.json"
        with self.assertRaises(ValueError):
            LiveAdapter(site, Path("not-read-unconfirmed-config.json"))


class HttpTest(unittest.TestCase):
    def test_loopback_bearer_validation_and_error_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = MissionService(Path(tmp) / "db")
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.service, server.api_token = svc, "x" * 32
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/tools/drone_get_capabilities"
            body = json.dumps({"arguments": {}, "caller_id": "test", "request_id": "one"}).encode()
            try:
                for auth, origin, expected in [(None, None, 401), ("Bearer " + "x" * 32, "http://malicious.invalid", 403),
                                                ("Bearer " + "x" * 32, None, 200)]:
                    headers = {"Content-Type": "application/json"}
                    if auth:
                        headers["Authorization"] = auth
                    if origin:
                        headers["Origin"] = origin
                    req = request.Request(url, data=body, headers=headers)
                    try:
                        response = request.urlopen(req, timeout=2)
                    except error.HTTPError as exc:
                        response = exc
                    with response:
                        self.assertEqual(response.status, expected)
                        result = json.loads(response.read())
                        self.assertEqual(result["schema_version"], 1)
                        self.assertEqual(result["execution_mode"], "mock")
                        self.assertFalse(result["physical_execution"])
            finally:
                server.shutdown()
                server.server_close()
                svc.close()


if __name__ == "__main__":
    unittest.main()
