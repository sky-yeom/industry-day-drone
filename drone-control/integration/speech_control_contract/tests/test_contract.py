import asyncio
import copy
import unittest
from unittest.mock import patch
from contract import Gateway, TOOLS
from mock_service import MockService, PROFILE, SITE, SEQUENCE


class ContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        blocker = patch("socket.create_connection", side_effect=AssertionError("network forbidden"))
        blocker.start()
        self.addCleanup(blocker.stop)
        self.service = MockService()
        self.gateway = Gateway(self.service)
        self.args = dict(profile_id=PROFILE, site_revision=SITE, destination_ids=SEQUENCE.copy())

    async def execute(self, key="execute-1", args=None):
        return await self.gateway.call("drone_execute_route", self.args if args is None else args,
            caller_id="speech-app", request_id=key)

    def code(self, result, expected):
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["error"]["code"], expected)
        self.assertEqual(result["execution_mode"], "mock")
        self.assertIs(result["physical_execution"], False)

    async def test_concurrent_idempotency(self):
        results = await asyncio.gather(*(self.execute() for _ in range(30)))
        self.assertTrue(all(item["ok"] for item in results))
        self.assertEqual({r["mission"]["mission_id"] for r in results}, {"mock-mission-1"})
        self.assertEqual(len(self.service._missions), 1)

    async def test_racing_distinct_requests_one_active(self):
        results = await asyncio.gather(*(self.execute(str(i)) for i in range(10)))
        self.assertEqual(sum(item["ok"] for item in results), 1)
        self.assertEqual(len(self.service._missions), 1)
        for item in results:
            if not item["ok"]:
                self.code(item, "DRONE_BUSY")

    async def test_key_collision(self):
        await self.execute()
        altered = dict(self.args, destination_ids=["tag-1"])
        self.code(await self.execute(args=altered), "IDEMPOTENCY_CONFLICT")

    async def test_idempotency_scope_is_caller_and_business_request(self):
        first = await self.execute("same-intent")
        mid = first["mission"]["mission_id"]
        self.code(await self.gateway.call("drone_stop_mission", {"mission_id": mid},
            caller_id="speech-app", request_id="same-intent"), "IDEMPOTENCY_CONFLICT")
        await self.service.test_only_set_terminal(mid, "completed")
        second = await self.gateway.call("drone_execute_route", self.args,
            caller_id="other-trusted-caller", request_id="same-intent")
        self.assertNotEqual(first["mission"]["mission_id"], second["mission"]["mission_id"])
        self.assertEqual(await self.execute("same-intent"), first)

    async def test_wrong_sequence_and_stale_site(self):
        self.code(await self.execute("wrong", dict(self.args, destination_ids=["tag-1"])), "ROUTE_UNSUPPORTED")
        self.code(await self.execute("stale", dict(self.args, site_revision="old")), "STALE_SITE_REVISION")
        accepted = await self.execute()
        self.assertEqual(accepted["mission"]["destination_ids"], SEQUENCE)

    async def test_extra_parameters_rejected_at_both_boundaries(self):
        for field in ("rawkey", "host", "token", "velocity", "altitude", "request_id", "caller_id"):
            bad = dict(self.args, **{field: "danger"})
            self.code(await self.execute(field, bad), "INVALID_ARGUMENTS")
            self.code(await self.service.request("POST", "/v1/drone/missions", bad, request_id=field), "INVALID_ARGUMENTS")
        self.assertEqual(len(self.service._missions), 0)

    async def test_types_lengths_and_context(self):
        for value in (True, 1, None, [], "", "a" * 65, "../raw"):
            self.code(await self.execute(args=dict(self.args, profile_id=value)), "INVALID_ARGUMENTS")
        for value in (True, "tag-1", [], [True], ["tag-1"] * 33):
            self.code(await self.execute(args=dict(self.args, destination_ids=value)), "INVALID_ARGUMENTS")
        for key in (None, True, "", "a" * 129):
            self.code(await self.execute(key), "INVALID_REQUEST_CONTEXT")
        self.code(await self.gateway.call("drone_execute_route", self.args,
            caller_id=True, request_id="id"), "INVALID_REQUEST_CONTEXT")
        self.code(await self.gateway.call([], {}), "INVALID_ARGUMENTS")

    async def test_stop_not_physical_or_terminal(self):
        result = await self.execute()
        self.assertEqual(result["status"], "accepted")
        mid = result["mission"]["mission_id"]
        stopped = await self.gateway.call("drone_stop_mission", {"mission_id": mid},
            caller_id="speech-app", request_id="stop-1")
        self.assertEqual(stopped["mission"]["state"], "stop_requested")
        self.assertEqual(stopped["status"], "stop_requested")
        self.assertIs(stopped["physical_stop_confirmed"], False)
        self.code(await self.execute("next"), "DRONE_BUSY")
        status = await self.gateway.call("drone_get_status", {})
        self.assertEqual(status["active_mission_id"], mid)
        self.assertEqual(status["flight_state"], "unknown")
        await self.service.test_only_set_terminal(mid, "cancelled")
        self.assertTrue((await self.execute("after-terminal"))["ok"])

    async def test_unknown_data_and_missing_mission(self):
        status = await self.gateway.call("drone_get_status", {})
        self.assertEqual(status["flight_state"], "unknown")
        for field in ("armed", "battery_percent", "observed_at", "is_flying",
                      "are_motors_on", "vs_enabled", "control_authority", "snapshot_age_ms"):
            self.assertIsNone(status[field])
        snapshot = await self.gateway.call("drone_get_sensor_snapshot", {})
        self.assertEqual(set(snapshot["readings"]), {"oa_horizontal_distances_mm",
            "oa_horizontal_angle_interval_deg", "oa_upward_distance_mm",
            "oa_downward_distance_mm", "oa_obstacle_data_age_ms"})
        self.assertTrue(all(value is None for value in snapshot["readings"].values()))
        self.assertIsNone(snapshot["observed_at"])
        self.code(await self.gateway.call("drone_get_mission", {"mission_id": "missing"}), "MISSION_NOT_FOUND")

    async def test_write_timeout_ambiguous_no_retry(self):
        service = self.service
        class CommitThenTimeout:
            calls = 0
            async def request(inner, *args, **kwargs):
                inner.calls += 1
                await service.request(*args, **kwargs)
                raise TimeoutError()
        transport = CommitThenTimeout()
        gateway = Gateway(transport)
        self.code(await gateway.call("drone_execute_route", self.args,
            caller_id="speech-app", request_id="one"), "OUTCOME_UNKNOWN")
        self.assertEqual(transport.calls, 1)
        self.assertEqual(len(service._missions), 1)
        self.code(await gateway.call("drone_get_status", {}), "TRANSPORT_ERROR")
        self.assertEqual(transport.calls, 2)

    async def test_mutation_isolation_and_capabilities(self):
        first = await self.execute()
        saved = copy.deepcopy(first)
        first["mission"]["destination_ids"].clear()
        self.args["destination_ids"].clear()
        self.assertEqual(await self.execute(args=dict(self.args, destination_ids=SEQUENCE.copy())), saved)
        caps = await self.gateway.call("drone_get_capabilities", {})
        self.assertIs(caps["live_ready"], False)
        caps["supported_ordered_sequences"][0].clear()
        fresh = await self.gateway.call("drone_get_capabilities", {})
        self.assertEqual(fresh["supported_ordered_sequences"], [SEQUENCE])
        captures = await self.gateway.call("drone_get_captures", {"mission_id": saved["mission"]["mission_id"]})
        self.assertEqual(captures["captures"], [])
        for tool in TOOLS:
            self.assertTrue(tool["strict"])
            self.assertFalse(tool["parameters"]["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
