import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

from relay.drone_client import DroneClient, DroneError


def response(**fields):
    return dict(schema_version=1, ok=True, execution_mode="live", physical_execution=False, **fields)


class DroneClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        blocker = patch("socket.socket.connect", side_effect=AssertionError("offline test"))
        blocker.start()
        self.addCleanup(blocker.stop)
        self.calls = []

        async def transport(name, envelope):
            self.calls.append((name, deepcopy(envelope)))
            await asyncio.sleep(0)
            return response(mission={"mission_id": "m-1"})
        self.client = DroneClient("relay-test", token="backend-only-test-token", transport=transport)
        self.args = dict(profile_id="site-v1", site_revision="rev-1", destination_ids=["tag-3", "tag-1", "tag-2"])

    async def test_concurrent_write_has_one_business_request_and_copied_results(self):
        first, second = await asyncio.gather(*(
            self.client.call("drone_execute_route", self.args, request_id="intent-1") for _ in range(2)))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1], dict(arguments=self.args, caller_id="relay-test", request_id="intent-1"))
        self.assertNotIn("token", str(self.calls))
        first["mission"]["mission_id"] = "corrupted"
        self.assertEqual(second["mission"]["mission_id"], "m-1")
        self.assertEqual((await self.client.call("drone_execute_route", self.args, request_id="intent-1"))["mission"]["mission_id"], "m-1")

    async def test_same_key_different_body_conflicts_without_send(self):
        await self.client.call("drone_execute_route", self.args, request_id="intent-1")
        with self.assertRaises(DroneError) as caught:
            await self.client.call("drone_execute_route", dict(self.args, destination_ids=["tag-1"]), request_id="intent-1")
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(len(self.calls), 1)

    async def test_write_timeout_latches_unknown_and_never_replays(self):
        async def timeout(name, envelope):
            self.calls.append((name, envelope))
            raise TimeoutError()
        self.client._transport = timeout
        for _ in range(2):
            with self.assertRaises(DroneError) as caught:
                await self.client.call("drone_execute_route", self.args, request_id="intent-1")
            self.assertEqual(caught.exception.code, "OUTCOME_UNKNOWN")
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(DroneError) as caught:
            await self.client.call("drone_get_status", {})
        self.assertEqual(caught.exception.code, "TRANSPORT_ERROR")

    async def test_caller_cancellation_preserves_inflight_admission_result(self):
        gate = asyncio.Event()
        async def delayed(name, envelope):
            self.calls.append((name, envelope))
            await gate.wait()
            return response(mission={"mission_id": "admitted"})
        self.client._transport = delayed
        task = asyncio.create_task(self.client.call("drone_execute_route", self.args, request_id="intent-1"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        gate.set()
        value = await self.client.call("drone_execute_route", self.args, request_id="intent-1")
        self.assertEqual(value["mission"]["mission_id"], "admitted")
        self.assertEqual(len(self.calls), 1)

    async def test_unknown_fields_bad_types_and_untrusted_hosts_never_send(self):
        for field in ("host", "token", "rawkey", "caller_id", "request_id", "velocity"):
            with self.subTest(field=field), self.assertRaises(DroneError):
                await self.client.call("drone_execute_route", dict(self.args, **{field: "x"}), request_id="x")
        for value in (True, None, [], [True], ["x"] * 33):
            with self.subTest(value=value), self.assertRaises(DroneError):
                await self.client.call("drone_execute_route", dict(self.args, destination_ids=value), request_id="x")
        for url in ("http://example.com:8766", "http://127.0.0.1:9998", "http://127.0.0.1:8766/path", "http://u:p@127.0.0.1:8766"):
            with self.subTest(url=url), self.assertRaises(DroneError):
                DroneClient("relay-test", base_url=url, token="x")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
