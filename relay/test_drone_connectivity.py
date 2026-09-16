import copy
import unittest
from unittest.mock import patch

from relay.drone_status import live_readiness_issue, read_drone_status


def phone_status(product_connected, *, sdk_registered=True, ground_verified=False):
    return {
        "execution_mode": "live",
        "connected": True,
        "ground_verified": ground_verified,
        "active_mission_id": None,
        "raw_telemetry": {
            "bridge_health": {
                "sdk_registered": sdk_registered,
                "product_connected": product_connected,
                "private_diagnostic": "not-for-browser",
            },
            "fc_health": {"state": "WARMING"},
        },
    }


def fc_key(source="GET", error=None, failures=0):
    return {"source": source, "last_error_code": error, "consecutive_failures": failures}


def faulted_status(keys):
    observation = phone_status(True, ground_verified=True)
    observation["raw_telemetry"]["fc_health"] = {"state": "HANDLER_FAULT", "keys": keys}
    return observation


def asleep_keys(failures=3, witness_failures=None, witness_source="GET"):
    """Every polled key dead with the handler-missing error the idle aircraft produces."""
    keys = {
        name: fc_key(error="REQUEST_HANDLER_NOT_FOUND", failures=failures)
        for name in ("IsFlying", "AreMotorsOn", "FlightMode", "AircraftAttitude")
    }
    keys["Connection"] = fc_key(source="LISTEN", error="REQUEST_HANDLER_NOT_FOUND", failures=1)
    keys["BatteryPowerPercent"] = fc_key(
        source=witness_source,
        error="REQUEST_HANDLER_NOT_FOUND",
        failures=failures if witness_failures is None else witness_failures,
    )
    return keys


class DroneConnectivityTests(unittest.IsolatedAsyncioTestCase):
    async def observed_status(self, observation):
        capabilities = {
            "execution_mode": "live",
            "live_ready": True,
            "home_tag_id": 6,
            "floor_tag_id": 0,
            "target_height_m": 1.5,
            "destinations": [
                {
                    "destination_id": f"tag-{tag}",
                    "monitor_id": f"monitor-{tag}",
                    "physical_definition": {"type": "apriltag", "marker_id": tag},
                }
                for tag in (1, 2, 3)
            ],
        }
        calls = []

        class Client:
            async def call(self, name, arguments):
                calls.append(name)
                return copy.deepcopy(
                    capabilities if name == "drone_get_capabilities" else observation
                )

        with patch("relay.drone_status.DroneClient", return_value=Client()):
            result = await read_drone_status()
        self.assertEqual(calls, ["drone_get_capabilities", "drone_get_status"])
        self.assertNotIn("raw_telemetry", result)
        self.assertNotIn("not-for-browser", str(result))
        return result

    async def test_phone_socket_is_not_an_aircraft_connection(self):
        for connected, code in (
            (None, "AIRCRAFT_CONNECTION_UNCONFIRMED"),
            (False, "AIRCRAFT_DISCONNECTED"),
        ):
            with self.subTest(product_connected=connected):
                result = await self.observed_status(phone_status(connected))
                self.assertTrue(result["apiConnected"])
                self.assertTrue(result["bridgeConnected"])
                self.assertIs(result["physicalConnected"], connected)
                self.assertEqual(result["readinessErrorCode"], code)

    async def test_missing_product_evidence_is_unknown_not_connected(self):
        observation = phone_status(True, ground_verified=True)
        observation.pop("raw_telemetry")
        result = await self.observed_status(observation)
        self.assertIsNone(result["physicalConnected"])

    async def test_connected_aircraft_still_requires_ground_proof(self):
        result = await self.observed_status(phone_status(True))
        self.assertTrue(result["physicalConnected"])
        self.assertEqual(result["readinessErrorCode"], "GROUND_UNVERIFIED")
        result = await self.observed_status(phone_status(True, ground_verified=True))
        self.assertTrue(result["physicalConnected"])
        self.assertIsNone(result["readinessErrorCode"])

    def test_registration_and_product_failures_precede_ground_error(self):
        for observation, code in (
            (phone_status(None, sdk_registered=False), "DJI_SDK_NOT_REGISTERED"),
            (phone_status(None), "AIRCRAFT_CONNECTION_UNCONFIRMED"),
            (phone_status(False, ground_verified=True), "AIRCRAFT_DISCONNECTED"),
        ):
            with self.subTest(code=code):
                issue = live_readiness_issue(observation)
                self.assertEqual(issue[0], code)
                self.assertNotIn("not-for-browser", issue[1])

    def test_a_silent_aircraft_asks_for_a_stick_nudge_not_an_app_repair(self):
        issue = live_readiness_issue(faulted_status(asleep_keys()))
        self.assertEqual(issue[0], "AIRCRAFT_LINK_ASLEEP")
        self.assertIn("스틱", issue[1])
        self.assertNotIn("not-for-browser", issue[1])

    def test_a_readable_battery_keeps_the_fault_on_the_flight_controller(self):
        keys = asleep_keys()
        keys["BatteryPowerPercent"] = fc_key(failures=0)
        self.assertEqual(live_readiness_issue(faulted_status(keys))[0],
                         "FLIGHT_CONTROLLER_UNAVAILABLE")

    def test_one_blink_is_not_an_outage(self):
        for failures in (1, 2):
            with self.subTest(consecutive_failures=failures):
                self.assertEqual(live_readiness_issue(faulted_status(asleep_keys(failures)))[0],
                                 "FLIGHT_CONTROLLER_UNAVAILABLE")

    def test_the_witness_must_be_polled_and_failing_like_the_rest(self):
        for keys in (asleep_keys(witness_source="LISTEN"),
                     asleep_keys(witness_failures=0),
                     {"IsFlying": fc_key(error="REQUEST_HANDLER_NOT_FOUND", failures=9)},
                     {}):
            with self.subTest(keys=sorted(keys)):
                self.assertEqual(live_readiness_issue(faulted_status(keys))[0],
                                 "FLIGHT_CONTROLLER_UNAVAILABLE")

    def test_a_different_error_on_one_key_is_not_a_silent_aircraft(self):
        keys = asleep_keys()
        keys["FlightMode"] = fc_key(error="TIMEOUT", failures=7)
        self.assertEqual(live_readiness_issue(faulted_status(keys))[0],
                         "FLIGHT_CONTROLLER_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
