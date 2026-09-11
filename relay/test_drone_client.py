import asyncio
from copy import deepcopy
import io
import json
import unittest
from unittest.mock import Mock, patch

from relay.drone_client import DroneClient, DroneError
from relay.tool_target import REAL_API_URL, TEST_API_URL, resolve_tool_target


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


class DroneClientTargetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        blocker = patch("socket.socket.connect", side_effect=AssertionError("offline test"))
        blocker.start()
        self.addCleanup(blocker.stop)
        self.requests = []
        self.reply = dict(schema_version=1, ok=True, execution_mode="mock", physical_execution=False)

        def open_response(req, timeout):
            self.requests.append(req)
            if isinstance(self.reply, Exception):
                raise self.reply
            return io.BytesIO(json.dumps(self.reply).encode())

        self.opener = Mock()
        self.opener.open.side_effect = open_response
        opener_patch = patch("relay.drone_client.request.build_opener", return_value=self.opener)
        self.build_opener = opener_patch.start()
        self.addCleanup(opener_patch.stop)
        self.args = dict(profile_id="site-v1", site_revision="rev-1", destination_ids=["tag-1"])

    def client(self, mode, *, env=None, **kwargs):
        values = dict(DRONE_CONTROL_API_TOKEN="real-secret")
        if mode is not None:
            values["DRONE_RUN_MODE"] = mode
        values.update(env or {})
        target = resolve_tool_target(values)
        with patch.multiple("relay.drone_client.config", DRONE_RUN_MODE=target.run_mode,
            DRONE_CONTROL_MODE=target.control_mode, DRONE_CONTROL_API_URL=target.api_url,
            DRONE_CONTROL_API_TOKEN=target.api_token, DRONE_CONTROL_TRANSPORT=target.transport):
            return DroneClient("target-test", **kwargs)

    def headers(self, index=-1):
        return {name.lower(): value for name, value in self.requests[index].header_items()}

    async def test_explicit_test_is_ready_and_omits_empty_authorization(self):
        client = self.client("test")
        self.assertIsNone(client.readiness())
        result = await client.call("drone_get_capabilities", {})
        self.assertEqual(result["execution_mode"], "mock")
        self.assertEqual(self.requests[0].full_url, TEST_API_URL + "/tools/drone_get_capabilities")
        self.assertNotIn("authorization", self.headers())
        self.assertEqual(self.headers()["x-drone-expected-mode"], "mock")
        self.assertNotIn("real-secret", self.requests[0].data.decode())
        proxy, redirect = self.build_opener.call_args.args
        self.assertEqual(proxy.proxies, {})
        self.assertIsNone(redirect.redirect_request(None, None, 302, "", {}, REAL_API_URL))

    async def test_test_auth_uses_only_opt_in_token_and_selected_origin(self):
        client = self.client("test", env=dict(DRONE_TEST_API_URL="http://127.0.0.1:18768/",
            DRONE_TEST_API_TOKEN="test-secret"))
        await client.call("drone_get_status", {})
        self.assertEqual(self.requests[0].full_url, "http://127.0.0.1:18768/tools/drone_get_status")
        self.assertEqual(self.headers()["authorization"], "Bearer test-secret")
        self.assertEqual(self.headers()["x-drone-expected-mode"], "mock")
        self.assertNotIn("real-secret", repr(self.requests[0].header_items()))

    async def test_test_lookup_camera_and_write_keep_mock_header_without_auth(self):
        client = self.client("test")
        await client.lookup_request("intent-1")
        await client.camera("frame")
        await client.call("drone_execute_route", self.args, request_id="intent-1")
        self.assertEqual([req.full_url for req in self.requests], [
            TEST_API_URL + "/requests/target-test/intent-1",
            TEST_API_URL + "/camera/frame",
            TEST_API_URL + "/tools/drone_execute_route",
        ])
        self.assertEqual([req.method for req in self.requests], ["GET", "POST", "POST"])
        for index in range(3):
            self.assertEqual(self.headers(index)["x-drone-expected-mode"], "mock")
            self.assertNotIn("authorization", self.headers(index))

    async def test_real_http_is_fixed_to_original_port_token_and_live_header(self):
        client = self.client("real", env=dict(DRONE_CONTROL_API_URL=TEST_API_URL,
            DRONE_TEST_API_TOKEN="test-secret", DRONE_CONTROL_MODE="mock"))
        self.reply["execution_mode"] = "live"
        await client.call("drone_execute_route", self.args, request_id="intent-1")
        self.assertEqual(self.requests[0].full_url, REAL_API_URL + "/tools/drone_execute_route")
        self.assertEqual(self.headers()["authorization"], "Bearer real-secret")
        self.assertEqual(self.headers()["x-drone-expected-mode"], "live")

    async def test_no_test_or_real_client_can_override_its_resolved_origin(self):
        for mode, urls in (
            ("test", [REAL_API_URL, "http://127.0.0.1:18768", "http://localhost:18767",
                "http://127.0.0.1:9998", TEST_API_URL + "?secret", TEST_API_URL + "/path"]),
            ("real", [TEST_API_URL, "http://127.0.0.1:18768", "http://127.0.0.1:9999",
                "http://localhost:8766", "http://@127.0.0.1:8766", "http://127.0.0.1:bad"]),
            (None, [TEST_API_URL, "http://127.0.0.1:18768", "http://127.0.0.1:9997"]),
        ):
            for url in urls:
                with self.subTest(mode=mode, url=url), self.assertRaises(DroneError) as caught:
                    self.client(mode, base_url=url)
                self.assertEqual(caught.exception.code, "INVALID_CONFIGURATION")
                self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(self.requests, [])

    async def test_live_expected_mode_under_test_config_cannot_reach_test_origin(self):
        for url in (TEST_API_URL, "http://127.0.0.1:18768"):
            with self.subTest(url=url), self.assertRaises(DroneError) as caught:
                self.client("test", expected_mode="live", base_url=url)
            self.assertEqual(caught.exception.code, "INVALID_CONFIGURATION")
        client = self.client("test", expected_mode="live", base_url=REAL_API_URL)
        self.assertIsNotNone(client.readiness())
        self.assertEqual(self.requests, [])

    async def test_legacy_and_real_empty_token_block_reads_writes_camera_and_lookup(self):
        for mode in (None, "real"):
            for expected in ("mock", "live"):
                client = self.client(mode, token="", expected_mode=expected)
                self.assertIsNotNone(client.readiness())
                for name in ("read", "write", "camera", "lookup"):
                    with self.subTest(mode=mode, expected=expected, name=name), self.assertRaises(DroneError) as caught:
                        if name == "read":
                            await client.call("drone_get_status", {})
                        elif name == "write":
                            await client.call("drone_execute_route", self.args, request_id="intent-1")
                        elif name == "camera":
                            await client.camera("frame")
                        else:
                            await client.lookup_request("intent-1")
                    self.assertEqual(caught.exception.code, "INVALID_CONFIGURATION")
        self.assertEqual(self.requests, [])

    async def test_http_rejects_mode_or_physical_claims_without_learning_new_mode(self):
        client = self.client("test")
        for mode, physical in (("live", False), ("mock", True), ("mock", None)):
            self.reply.update(execution_mode=mode, physical_execution=physical)
            with self.subTest(mode=mode, physical=physical), self.assertRaises(DroneError) as caught:
                await client.call("drone_get_capabilities", {})
            self.assertEqual(caught.exception.code, "MODE_MISMATCH")
            self.assertEqual(self.headers()["x-drone-expected-mode"], "mock")
        self.reply.update(execution_mode="mock", physical_execution=False)
        with patch("relay.drone_client.config.DRONE_CONTROL_MODE", "live"):
            await client.call("drone_execute_route", self.args, request_id="intent-1")
        self.assertEqual(self.headers()["x-drone-expected-mode"], "mock")

    async def test_real_http_refuses_mock_response(self):
        client = self.client("real")
        with self.assertRaises(DroneError) as caught:
            await client.call("drone_get_capabilities", {})
        self.assertEqual(caught.exception.code, "MODE_MISMATCH")
        self.assertEqual(self.headers()["x-drone-expected-mode"], "live")

    async def test_ambiguous_http_writes_never_replay_even_after_response_recovers(self):
        for name, args in (("drone_execute_route", self.args), ("drone_stop_mission", {"mission_id": "m-1"})):
            for failure in (dict(schema_version=1, ok=True, execution_mode="live", physical_execution=False), TimeoutError()):
                client = self.client("test")
                before = len(self.requests)
                self.reply = failure
                for attempt in range(2):
                    with self.subTest(name=name, attempt=attempt), self.assertRaises(DroneError) as caught:
                        await client.call(name, args, request_id="intent-1")
                    self.assertEqual(caught.exception.code, "OUTCOME_UNKNOWN")
                    self.reply = dict(schema_version=1, ok=True, execution_mode="mock", physical_execution=False)
                self.assertEqual(len(self.requests), before + 1)
                self.assertEqual(self.headers()["x-drone-expected-mode"], "mock")

    async def test_injected_transport_retains_existing_response_compatibility(self):
        calls = []
        async def transport(name, envelope):
            calls.append(name)
            return response()
        client = self.client(None, transport=transport, expected_mode="mock")
        self.assertEqual((await client.call("drone_get_status", {}))["execution_mode"], "live")
        self.assertEqual(calls, ["drone_get_status"])
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
