"""Pure observations and mock transport only; no drone/SDK connection is made."""
from io import BytesIO
import json
from unittest import TestCase
from unittest.mock import patch

from drone_nav.observation import classify_range_mm, observe_sector
from drone_nav.protocol import NDJSONClient, Protocol, Telemetry
from drone_nav.runtime import _directional_obstacle_m, _nearest_obstacle_m
from drone_nav.sdk_api import parse_sdk_response


class ObservationTests(TestCase):
    def test_old_and_new_telemetry_unknowns_and_detached_diagnostics(self):
        old = Telemetry.from_ack_payload({"telemetry": {"is_flying": False, "is_flying_age_ms": 26}})
        self.assertIs(old.is_flying, False)
        self.assertAlmostEqual(old.is_flying_age_s, .026)
        self.assertIsNone(old.are_motors_on)
        self.assertIsNone(old.fc_health)
        raw = dict(are_motors_on=False, are_motors_on_age_ms=12, telemetry_generation=7,
            telemetry_poll_failures=41, telemetry_expired_gets=3, sdk_reads_pending=2,
            bridge_health={"process_start_id": "process-a", "connection_generation": 3},
            fc_health={"generation": 7, "keys": {"KeyAreMotorsOn": {"listener_age_ms": -1, "get_success_age_ms": 12}}},
            oa_diagnostics={"generation": 1, "callback_sequence": 4, "status_fields": {"oa_enabled": {"reported": False, "value": None, "age_ms": None}}})
        new = Telemetry.from_ack_payload({"telemetry": raw})
        self.assertIs(new.are_motors_on, False)
        self.assertEqual(new.are_motors_on_age_s, .012)
        self.assertIs(type(new.telemetry_poll_failures), int)
        raw["bridge_health"]["process_start_id"] = "changed"
        self.assertEqual(new.bridge_health["process_start_id"], "process-a")
        self.assertIsNone(new.fc_health["keys"]["KeyAreMotorsOn"]["listener_age_ms"])
        self.assertIs(new.oa_diagnostics["status_fields"]["oa_enabled"]["reported"], False)

    def test_invalid_counter_age_and_range_array_are_not_coerced(self):
        t = Telemetry.from_ack_payload({"telemetry": dict(telemetry_generation=True,
            telemetry_poll_failures=3.5, sdk_submit_sequence=True, is_flying_age_ms=-1,
            are_motors_on_age_ms=float("nan"), oa_horizontal_distances_mm=[60000, True, 586])})
        for key in ("telemetry_generation", "telemetry_poll_failures", "sdk_submit_sequence", "is_flying_age_s", "are_motors_on_age_s", "oa_horizontal_distances_mm"):
            self.assertIsNone(getattr(t, key))
        self.assertTrue(t.parse_issues)
        self.assertEqual(observe_sector(t, "left").range_status, "MALFORMED_ARRAY")

    def test_range_codes_are_unknown_not_sixty_metres(self):
        cases = [(None, "MISSING"), (True, "INVALID_TYPE"), (float("nan"), "NONFINITE"),
            (float("inf"), "NONFINITE"), (0, "NONPOSITIVE"), (-1, "NONPOSITIVE"),
            (60000, "UNRESOLVED_RANGE_CODE"), (60001, "OUTSIDE_ANALYSIS_DOMAIN")]
        for value, expected in cases:
            with self.subTest(value=value):
                observed = classify_range_mm(value)
                self.assertEqual(observed.range_status, expected)
                self.assertIsNone(observed.range_m)
        self.assertEqual(classify_range_mm(586).range_m, .586)

    def test_reported_left_sector_preserves_586_and_rear_unknown(self):
        values = [60000] * 360
        for index in range(265, 276):
            values[index] = 700
        values[270] = 586
        t = Telemetry(oa_horizontal_distances_mm=tuple(values), oa_horizontal_angle_interval_deg=1,
            oa_obstacle_data_age_s=.08, oa_downward_distance_mm=1337,
            height_m=1.8, velocity_age_s=.026)
        left = observe_sector(t, "left")
        self.assertEqual((left.range_mm, left.valid_count, left.selected_count), (586, 11, 11))
        self.assertEqual(left.geometry_status, "REPORTED_INTERVAL")
        self.assertIsNone(observe_sector(t, "back").range_m)
        self.assertEqual(t.oa_downward_distance_mm, 1337)
        self.assertEqual(t.height_m, 1.8)
        self.assertEqual(_directional_obstacle_m(t, "left"), .586)

    def test_geometry_wrap_inference_and_partial_coverage(self):
        values = [60000] * 360
        values[355] = 586
        t = Telemetry(oa_horizontal_distances_mm=tuple(values), oa_obstacle_data_age_s=.1)
        front = observe_sector(t, "front")
        self.assertEqual(front.selected_count, 11)
        self.assertEqual(front.range_mm, 586)
        self.assertEqual(front.coverage_status, "PARTIAL_COVERAGE")
        self.assertEqual(front.geometry_status, "INFERRED_INTERVAL")
        four = Telemetry(oa_horizontal_distances_mm=(100, 200, 300, 400), oa_horizontal_angle_interval_deg=90)
        self.assertEqual(observe_sector(four, "right").range_mm, 200)
        bad = Telemetry(oa_horizontal_distances_mm=(100, 200, 300, 400), oa_horizontal_angle_interval_deg=1)
        self.assertEqual(observe_sector(bad, "right").geometry_status, "INVALID_GEOMETRY")
        self.assertIsNone(observe_sector(bad, "right").range_m)

    def test_callback_recency_is_not_sensor_liveness(self):
        t = Telemetry(oa_horizontal_distances_mm=(586,) * 4, oa_obstacle_data_age_s=269)
        observed = observe_sector(t, "all")
        self.assertEqual(observed.range_mm, 586)
        self.assertEqual(observed.callback_recency, "NO_RECENT_CALLBACK")
        self.assertEqual(observed.source_liveness, "UNCONFIRMED")
        self.assertIsNone(_nearest_obstacle_m(t))
        self.assertIsNone(_nearest_obstacle_m(Telemetry(oa_horizontal_distances_mm=(60000,) * 4, oa_obstacle_data_age_s=.1)))
        self.assertEqual(observe_sector(Telemetry(oa_obstacle_data_age_s=-1), "all").callback_recency, "UNKNOWN_AGE")
        self.assertEqual(observe_sector(Telemetry(oa_horizontal_distances_mm=()), "all").range_status, "EMPTY_ARRAY")

    def test_new_callback_age_precedes_legacy_alias_but_exposes_disagreement(self):
        t = Telemetry(oa_horizontal_distances_mm=(586,) * 4, oa_obstacle_data_age_s=.08,
            oa_diagnostics={"last_callback_age_ms": 269000, "generation": 3, "callback_sequence": 5})
        observed = observe_sector(t, "left")
        self.assertEqual(observed.callback_age_s, 269)
        self.assertEqual(observed.legacy_callback_age_s, .08)
        self.assertIs(observed.callback_age_consistent, False)
        self.assertEqual((observed.source_generation, observed.callback_sequence), (3, 5))
        self.assertIsNone(_directional_obstacle_m(t, "left"))


class Socket:
    def __init__(self, data=b""):
        self.reader = BytesIO(data)
        self.closed = False
    def settimeout(self, value): pass
    def sendall(self, data): pass
    def makefile(self, mode): return self.reader
    def close(self): self.closed = True


class TelemetryTransportTests(TestCase):
    def setUp(self):
        self.client = NDJSONClient("unused.invalid", 1)
        self.events = []
        self.client._log_event = lambda event, data: self.events.append((event, data))
        self.client._open_session_log = lambda: None
        self.server = Protocol()

    def ack(self, telemetry=None):
        return self.server.encode("ack", dict(ok=True, detail="status") |
            ({"telemetry": telemetry} if telemetry is not None else {}))

    def wire(self, data):
        self.client._socket = Socket(data)
        self.client._file = self.client._socket.reader

    def test_missing_telemetry_invalidates_current_but_preserves_last_observation(self):
        self.wire(self.ack({"height_m": 1.8}) + self.ack())
        self.client.send("status", {"state": "test"})
        self.assertIsNotNone(self.client.last_telemetry_received_pc_monotonic_ns)
        self.client.send("status", {"state": "test"})
        self.assertIsNone(self.client.last_telemetry)
        self.assertIsNone(self.client.last_telemetry_received_pc_monotonic_ns)
        self.assertEqual(self.client.last_known_telemetry.height_m, 1.8)

    def test_wrong_sequence_is_logged_and_cannot_leave_a_fresh_cache(self):
        bad = json.loads(self.ack({"height_m": 1.8}))
        bad["sequence"] = 99
        self.client.last_telemetry = Telemetry(height_m=9)
        self.wire((json.dumps(bad) + "\n").encode())
        with self.assertRaises(ValueError):
            self.client.send("status", {"state": "test"})
        self.assertIsNone(self.client.last_telemetry)
        self.assertTrue(any(event == "protocol_error" and data["actual_sequence"] == 99 for event, data in self.events))

    def test_transport_timeout_after_completed_route_preserves_history_only(self):
        self.wire(self.ack({"is_flying": False, "are_motors_on": False, "height_m": 0,
            "telemetry_poll_failures": 41}))
        self.client.send("status", {"state": "route_completed"})
        previous = self.client.last_telemetry
        with patch.object(self.client._socket, "sendall", side_effect=TimeoutError("offline test timeout")):
            with self.assertRaises(TimeoutError):
                self.client.send("status", {"state": "postflight_independent_verification"})
        self.assertIsNone(self.client.last_telemetry)
        self.assertIsNone(self.client.last_telemetry_received_pc_monotonic_ns)
        self.assertIs(self.client.last_known_telemetry, previous)
        self.assertEqual(previous.telemetry_poll_failures, 41)
        errors = [data for event, data in self.events if event == "transport_error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["command_type"], "status")
        self.assertNotIn("dji_error", errors[0])

    def test_reconnect_closes_prior_socket_and_changes_pc_epoch(self):
        first, second = Socket(), Socket()
        with patch("drone_nav.protocol.socket.create_connection", side_effect=[first, second]):
            self.client.connect()
            self.client.last_telemetry = Telemetry(height_m=1)
            self.client.connect()
        self.assertTrue(first.closed)
        self.assertEqual(self.client.pc_connection_epoch, 2)
        self.assertIsNone(self.client.last_telemetry)

    def test_counter_delta_is_scoped_to_process_and_generation(self):
        def record(count, process="a", generation=1):
            self.client._record_health_delta(Telemetry(bridge_health={"process_start_id": process},
                telemetry_generation=generation, telemetry_poll_failures=count))
            return self.client.last_health_delta
        record(41)
        self.assertEqual(record(41)["poll_failures_delta"], 0)
        self.assertEqual(record(43)["poll_failures_delta"], 2)
        self.assertIsNone(record(1)["poll_failures_delta"])
        self.assertEqual(record(41, process="b")["status"], "GENERATION_CHANGED")
        self.client._motion_streak_sequence = 5
        record(41, process="b", generation=2)
        self.assertIsNone(self.client._motion_streak_sequence)

    def test_query_local_error_and_prefix_mismatch_never_parse_as_sdk_values(self):
        for raw, status in [("QUERY_LOCAL_ERROR TIMEOUT", "QUERY_LOCAL_ERROR"),
            ("FlightController AreMotorsOn QUERY_LOCAL_ERROR BUSY", "QUERY_LOCAL_ERROR"),
            ("FlightController IsFlying false", "QUERY_PREFIX_MISMATCH")]:
            value = parse_sdk_response("GET", "FlightController", "AreMotorsOn", raw)
            self.assertFalse(value.ok)
            self.assertIsNone(value.value)
            self.assertIsNone(value.dji_error)
            self.assertEqual(value.status, status)
