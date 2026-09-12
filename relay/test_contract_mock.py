import base64
from copy import deepcopy
import hashlib
import itertools
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from relay.contract_mock import ContractMockTransport, PROFILE_ID, SITE_REVISION
from relay.camera import LiveCaptureCamera
from relay.vision import ContractMockVision

ROOT = Path(__file__).resolve().parents[1]


class ContractMockTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 100.0
        self.mock = ContractMockTransport("test-session", clock=lambda: self.now)
        self.counter = 0
        self.records = []
        self.args = dict(profile_id=PROFILE_ID, site_revision=SITE_REVISION,
                         destination_ids=["tag-2", "tag-3", "tag-1"])

    async def call(self, name, args=None, *, rid=None, caller="test-session", mock=None):
        self.counter += 1
        envelope = dict(arguments=args or {}, caller_id=caller, request_id=rid or f"r-{self.counter}")
        value = await (mock or self.mock)(name, envelope)
        self.records.append(dict(name=name, value=value))
        self.assertIs(value["physical_execution"], False)
        self.assertEqual(value["execution_mode"], "mock")
        return value

    async def finish(self, mid):
        for _ in range(40):
            self.now += .2
            value = await self.call("drone_get_mission", dict(mission_id=mid))
            self.assertTrue(value["ok"], value)
            if value["mission"]["state"] == "completed":
                return value["mission"]
        self.fail("Mock did not complete")

    async def test_same_seven_tools_route_captures_and_frozen_schema(self):
        caps = await self.call("drone_get_capabilities")
        self.assertEqual(len(caps["tools"]), 7)
        self.assertEqual(len(caps["supported_ordered_sequences"]), 6)
        self.assertIsNone((await self.call("drone_get_status"))["active_mission_id"])
        await self.call("drone_get_sensor_snapshot")
        with patch("socket.socket", side_effect=AssertionError("No mock socket")), \
                patch("subprocess.Popen", side_effect=AssertionError("No mock child")):
            admitted = await self.call("drone_execute_route", self.args, rid="departure")
            mid = admitted["mission"]["mission_id"]
            completed = await self.finish(mid)
            captures = await self.call("drone_get_captures", dict(mission_id=mid))
        self.assertEqual(completed["visited_ids"], [6, 2, 3, 1, 6])
        self.assertTrue(completed["route_completed"])
        self.assertTrue(completed["ground_verified"])
        self.assertFalse(completed["physical_stop_confirmed"])
        self.assertEqual(len(captures["captures"]), 6)
        self.assertEqual(len({c["sha256"] for c in captures["captures"]}), 6)
        for capture in captures["captures"]:
            image = base64.b64decode(capture["image_base64"])
            self.assertEqual(hashlib.sha256(image).hexdigest(), capture["sha256"])
            frame = LiveCaptureCamera.from_record(capture, mission_id=mid,
                visit_index=capture["visit_index"], destination_id=capture["destination_id"],
                monitor_id=capture["monitor_id"])
            self.assertTrue((await ContractMockVision().analyze(frame, "test person"))["targetPresent"])
        stopped = await self.call("drone_stop_mission", dict(mission_id=mid))
        self.assertFalse(stopped["physical_stop_confirmed"])
        self.assertEqual(stopped["mission"]["state"], "completed")
        self.assertEqual(await self.call("_lookup_request", rid="departure"), admitted)
        if not shutil.which("node"):
            self.fail("Node is required for this development-time frozen-schema check")
        validator = (ROOT / "contracts/drone-tools/v1/validate.mjs").as_uri()
        script = (
            f"import {{assertResponse,tools}} from {json.dumps(validator)};"
            "import {readFileSync} from 'node:fs';"
            "const input=JSON.parse(readFileSync(0,'utf8'));"
            "for(const row of input.records) if(row.name!=='_lookup_request') assertResponse(row.name,row.value);"
            "if(JSON.stringify(tools)!==JSON.stringify(input.tools)) throw Error('Tools schema drift');"
        )
        schemas = json.loads((ROOT / "drone-control/integration/speech_control_contract/tools.json").read_text("utf8"))
        result = subprocess.run(["node", "--input-type=module", "-e", script],
            input=json.dumps(dict(records=self.records, tools=schemas)), capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_idempotence_owner_validation_and_session_isolation(self):
        admitted = await self.call("drone_execute_route", self.args, rid="same")
        changed = deepcopy(admitted)
        changed["mission"]["destination_ids"].clear()
        self.assertEqual(await self.call("drone_execute_route", self.args, rid="same"), admitted)
        self.assertEqual((await self.call("drone_execute_route", dict(self.args,
            destination_ids=["tag-1", "tag-2", "tag-3"]), rid="same"))["error"]["code"], "IDEMPOTENCY_CONFLICT")
        self.assertEqual((await self.call("drone_execute_route", self.args))["error"]["code"], "MISSION_BUSY")
        other = ContractMockTransport("other", clock=lambda: self.now)
        second = await self.call("drone_execute_route", self.args, caller="other", mock=other)
        self.assertTrue(second["ok"])
        self.assertNotEqual(second["mission"]["mission_id"], admitted["mission"]["mission_id"])
        self.assertEqual((await self.call("drone_get_status", caller="other"))["error"]["code"], "OWNER_MISMATCH")
        invalid = await self.mock("drone_get_status", {"arguments": {}, "caller_id": "test-session", "request_id": ".bad"})
        self.assertEqual(invalid["error"]["code"], "INVALID_REQUEST_CONTEXT")
        self.assertFalse((await self.call("drone_get_status", {"unexpected": True}))["ok"])

    async def test_time_not_poll_count_drives_progress_and_stop_retains_mock_evidence(self):
        admitted = await self.call("drone_execute_route", self.args)
        mid = admitted["mission"]["mission_id"]
        for _ in range(10):
            self.assertEqual((await self.call("drone_get_mission", dict(mission_id=mid)))["mission"]["state"], "accepted")
        self.now += 1
        progress = await self.call("drone_get_mission", dict(mission_id=mid))
        self.assertEqual(progress["mission"]["state"], "running")
        stopped = await self.call("drone_stop_mission", dict(mission_id=mid), rid="stop")
        self.assertEqual(stopped["mission"]["state"], "stop_requested")
        self.assertFalse(stopped["physical_stop_confirmed"])
        self.now += 1
        final = await self.call("drone_get_mission", dict(mission_id=mid))
        self.assertEqual(final["mission"]["state"], "stopped")
        self.assertFalse(final["mission"]["route_completed"])

    async def test_expired_lease_never_resumes_even_when_polled_late(self):
        self.mock = ContractMockTransport("test-session", clock=lambda: self.now, lease_ms=700)
        admitted = await self.call("drone_execute_route", self.args)
        self.now += 20
        final = await self.call("drone_get_mission", dict(mission_id=admitted["mission"]["mission_id"]))
        self.assertEqual(final["mission"]["state"], "stopped")
        self.assertEqual(final["mission"]["stop_reason"], "caller_lease_expired")
        self.assertFalse(final["mission"]["route_completed"])

    async def test_all_six_orders_preserve_order_and_two_captures_per_visit(self):
        for order in itertools.permutations(("tag-1", "tag-2", "tag-3")):
            admitted = await self.call("drone_execute_route", dict(self.args, destination_ids=list(order)))
            completed = await self.finish(admitted["mission"]["mission_id"])
            self.assertEqual(completed["visited_ids"], [6, *(int(dest[-1]) for dest in order), 6])
            self.assertTrue(all(len(visit["capture_ids"]) == 2 for visit in completed["visits"]))

    async def test_faults_and_explicit_nonphysical_recovery(self):
        for scenario, expected in (("preflight-failure", "failed"), ("camera-unavailable", "failed"),
                                   ("connection-loss", "outcome_unknown"), ("manual-landing", "awaiting_rc_landing")):
            self.mock = ContractMockTransport("test-session", scenario=scenario, clock=lambda: self.now)
            admitted = await self.call("drone_execute_route", self.args)
            mid = admitted["mission"]["mission_id"]
            self.now += 5
            value = await self.call("drone_get_mission", dict(mission_id=mid))
            self.assertEqual(value["mission"]["state"], expected)
            if scenario in {"connection-loss", "manual-landing"}:
                self.assertEqual((await self.call("drone_execute_route", self.args))["error"]["code"], "MISSION_BUSY")
                landed = await self.mock.simulate_landing(mid, caller_id="test-session")
                self.assertTrue(landed["ok"])
                self.assertFalse(landed["physical_stop_confirmed"])
        self.mock = ContractMockTransport("test-session", scenario="lost-ack", clock=lambda: self.now)
        with self.assertRaises(TimeoutError):
            await self.call("drone_execute_route", self.args, rid="lost")
        original = await self.call("_lookup_request", rid="lost")
        self.assertEqual(await self.call("drone_execute_route", self.args, rid="lost"), original)

    async def test_histories_are_bounded_and_closed_sessions_reject_work(self):
        self.mock = ContractMockTransport("test-session", max_missions=1, max_requests=1, clock=lambda: self.now)
        admitted = await self.call("drone_execute_route", self.args)
        await self.finish(admitted["mission"]["mission_id"])
        self.assertEqual((await self.call("drone_execute_route", self.args))["error"]["code"], "HISTORY_LIMIT")
        self.mock.close()
        self.assertEqual((await self.call("drone_get_status"))["error"]["code"], "SERVICE_SHUTTING_DOWN")


if __name__ == "__main__":
    unittest.main()
