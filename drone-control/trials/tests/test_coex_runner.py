"""Runner lifecycle/CLI checks with fake telemetry and a forbidden real network."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from coex_io import ActionOutcomeUnknown, MotorsSnapshot
from coex_mission import Setpoint, load_profile
import coex_tagless_left_return as runner_module


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0, seconds)


class FakeIO:
    """ACKs follow actions, while separate overrides model contradictory evidence."""
    log_path = None

    def __init__(self, clock):
        self.clock = clock
        self.events = []
        self.airborne = self.armed = self.released = self.closed = False
        self.sampler_running = self.transport_failed = False
        self.initial_query = {}
        self.ground_override = {}
        self.arm_override = {}
        self.release_override = {}
        self.unknown_action = None
        self.stop_error = None
        self.landing_values = {"IsFlying": False, "AreMotorsOn": False}

    def connect(self):
        self.events.append(("connect",))

    def command(self, kind, payload):
        if self.transport_failed:
            raise ConnectionError("latched transport; no network write")
        self.events.append(("command", kind, dict(payload)))
        if kind == self.unknown_action:
            self.transport_failed = True
            raise ActionOutcomeUnknown("fake ambiguous action ACK")
        if kind == "takeoff":
            self.airborne = True
        if kind == "arm":
            self.armed = True
        if kind == "disarm":
            self.armed = False
            self.released = True
        raw = {"bridge_build_id": runner_module.REQUIRED_BUILD,
               "_received_monotonic_s": self.clock(), "_connection_epoch": 1,
               "telemetry_generation": 1, "is_flying": self.airborne,
               "is_flying_age_ms": 0, "height_m": 1.2 if self.airborne else .1,
               "height_age_ms": 0, "yaw_deg": 0, "attitude_age_ms": 0,
               "velocity_north_mps": 0, "velocity_east_mps": 0,
               "velocity_down_mps": 0, "velocity_age_ms": 0,
               "flight_mode": "GPS_NORMAL", "flight_mode_age_ms": 0,
               "battery_percent": 80, "armed": self.armed,
               "vs_enabled": self.armed, "vs_advanced_enabled": self.armed,
               "vs_authority": "MSDK" if self.armed else "RC",
               "stick_mode": "OFFICIAL_ADVANCED", "sdk_roll_pitch_mode": "VELOCITY",
               "disconnect_release_enabled": True, "time_watchdog_enabled": False}
        if not self.airborne:
            raw.update(self.ground_override)
        if self.armed:
            raw.update(self.arm_override)
        if self.released:
            raw.update(self.release_override)
        return raw

    def query_bool(self, key):
        if self.sampler_running:
            raise AssertionError("synchronous query raced the motor sampler")
        self.events.append(("query", key))
        if self.released:
            return self.landing_values[key]
        if not self.airborne:
            return self.initial_query.get(key, False)
        return True

    def start_motors(self):
        self.events.append(("start_motors",))
        self.sampler_running = True

    def stop_motors(self):
        self.events.append(("stop_motors",))
        self.sampler_running = False
        if self.stop_error:
            raise self.stop_error

    def motors_snapshot(self):
        return MotorsSnapshot(True, self.clock(), 1, None)

    def log_event(self, event, data):
        self.events.append(("log", event, dict(data)))

    def close(self):
        self.events.append(("close",))
        self.closed = True

    @property
    def commands(self):
        return [event[1] for event in self.events if event[0] == "command"]


class CompletedController:
    x_right_m = 0.0

    def __init__(self):
        self.calls = 0

    def step(self, *_):
        self.calls += 1
        return Setpoint(0, 0, 0, 0, "DONE", True)

    def summary(self):
        return {"phase": "DONE", "fault": None, "estimated_right_m": 0.0}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("socket.socket", side_effect=AssertionError("real network forbidden")))
        self.enterContext(patch("socket.create_connection", side_effect=AssertionError("real network forbidden")))
        self.reset_fake()

    def reset_fake(self):
        self.clock = Clock()
        self.io = FakeIO(self.clock)
        self.runner = runner_module.Runner(self.io, load_profile(), "offline-test-token",
            clock=self.clock, sleep=self.clock.sleep, emit=lambda _: None, landing_timeout_s=1.0)
        self.controller = CompletedController()
        self.runner.controller = self.controller

    def test_check_only_uses_queries_and_status_without_flight_actions(self):
        result = self.runner.run(check_only=True)
        self.assertEqual(result["status"], "GROUND_CHECK_PASSED")
        self.assertEqual(self.io.commands, ["status"])
        self.assertEqual(self.io.events[:3], [("query", "IsFlying"), ("query", "AreMotorsOn"), ("connect",)])
        self.assertTrue(self.io.closed)

    def test_connectivity_build_requires_watchdog_and_fresh_motor_telemetry(self):
        for override, passed in [({"time_watchdog_enabled": True, "are_motors_on": False, "are_motors_on_age_ms": 0}, True),
                                  ({"time_watchdog_enabled": False, "are_motors_on": False, "are_motors_on_age_ms": 0}, False),
                                  ({"time_watchdog_enabled": True, "are_motors_on": False, "are_motors_on_age_ms": 600}, False),
                                  ({"time_watchdog_enabled": True}, False)]:
            with self.subTest(override=override):
                self.reset_fake()
                self.io.ground_override = {"bridge_build_id": runner_module.CONNECTIVITY_BUILD, **override}
                result = self.runner.run(check_only=True)
                self.assertEqual(result["status"] == "GROUND_CHECK_PASSED", passed)
                self.assertEqual(self.io.commands, ["status"])

    def test_unknown_ground_proof_never_opens_control_connection(self):
        for key, value in (("IsFlying", True), ("IsFlying", None), ("AreMotorsOn", True), ("AreMotorsOn", None)):
            with self.subTest(key=key, value=value):
                self.reset_fake()
                self.io.initial_query[key] = value
                result = self.runner.run()
                self.assertEqual(result["status"], "ABORTED")
                self.assertNotIn(("connect",), self.io.events)
                self.assertEqual(self.io.commands, [])
                self.assertTrue(self.io.closed)

    def test_ground_ack_guards_block_takeoff(self):
        for override in ({"armed": True}, {"vs_enabled": None}, {"bridge_build_id": "unexpected"},
                         {"battery_percent": 20}, {"disconnect_release_enabled": False},
                         {"is_flying_age_ms": 600}, {"rc_stick_right_horizontal": 150}):
            with self.subTest(override=override):
                self.reset_fake()
                self.io.ground_override = override
                self.assertEqual(self.runner.run()["status"], "ABORTED")
                self.assertEqual(self.io.commands, ["status"])

    def test_one_takeoff_then_settle_then_arm_and_release_before_waiting_for_sampler(self):
        result = self.runner.run()
        self.assertTrue(result["route_completed_estimated"])
        self.assertTrue(result["rc_handover_confirmed"])
        self.assertTrue(result["landing_confirmed"])
        self.assertFalse(result["automatic_landing_commanded"])
        self.assertEqual(self.io.commands.count("takeoff"), 1)
        self.assertEqual(self.io.commands.count("arm"), 1)
        self.assertEqual(self.io.commands.count("disarm"), 1)
        self.assertNotIn("land", self.io.commands)
        self.assertEqual(self.controller.calls, 1)
        takeoff = next(i for i, e in enumerate(self.io.events) if e[:2] == ("command", "takeoff"))
        arm = next(i for i, e in enumerate(self.io.events) if e[:2] == ("command", "arm"))
        self.assertGreaterEqual(sum(e[:2] == ("command", "status") for e in self.io.events[takeoff:arm]), 20)
        stopped = self.io.events.index(("stop_motors",))
        disarm = next(i for i, e in enumerate(self.io.events) if e[:2] == ("command", "disarm"))
        self.assertLess(disarm, stopped)
        landing_queries = [i for i, event in enumerate(self.io.events)
                           if i > disarm and event[0] == "query"]
        self.assertTrue(landing_queries)
        self.assertTrue(all(i > stopped for i in landing_queries))
        self.assertEqual(self.io.events[-1], ("close",))

    def test_unknown_takeoff_or_arm_is_never_replayed_or_reconnected(self):
        for action in ("takeoff", "arm"):
            with self.subTest(action=action):
                self.reset_fake()
                self.io.unknown_action = action
                result = self.runner.run()
                self.assertEqual(result["status"], "ABORTED")
                self.assertEqual(self.io.commands.count(action), 1)
                self.assertEqual(self.io.events.count(("connect",)), 1)
                self.assertNotIn("velocity", self.io.commands)
                self.assertEqual(self.controller.calls, 0)
                self.assertTrue(self.io.closed)

    def test_arm_ack_without_actual_authority_never_enters_flight(self):
        self.io.arm_override = {"vs_authority": "UNKNOWN"}
        result = self.runner.run()
        self.assertEqual(result["status"], "ABORTED")
        self.assertEqual(result["fault"], "AUTHORITY_LOST")
        self.assertFalse(result["rc_handover_confirmed"])
        self.assertEqual(self.controller.calls, 0)
        self.assertEqual(self.io.commands.count("arm"), 1)
        self.assertNotIn("velocity", self.io.commands)

    def test_unknown_inflight_velocity_aborts_without_replay_or_reconnect(self):
        self.io.unknown_action = "velocity"
        with patch.object(self.controller, "step", return_value=Setpoint(0, -.1, 0, 0, "LEFT")):
            result = self.runner.run()
        self.assertEqual(result["status"], "ABORTED")
        self.assertEqual(result["fault"], "ActionOutcomeUnknown")
        self.assertEqual(self.io.commands.count("velocity"), 1)
        self.assertEqual(self.io.events.count(("connect",)), 1)
        self.assertEqual(self.io.commands[-1], "velocity")
        self.assertFalse(result["rc_handover_confirmed"])
        self.assertTrue(self.io.closed)

    def test_rc_override_after_arm_does_not_rearm_or_send_release_motion(self):
        self.io.arm_override = {"rc_override_age_ms": 0, "armed": False,
                                "vs_enabled": False, "vs_authority": "RC"}
        result = self.runner.run()
        self.assertEqual(result["status"], "ABORTED")
        self.assertEqual(result["fault"], "RC_OVERRIDE")
        self.assertTrue(result["rc_handover_confirmed"])
        after_arm = self.io.commands[self.io.commands.index("arm") + 1:]
        self.assertTrue(all(command == "status" for command in after_arm))
        self.assertEqual(self.controller.calls, 0)

    def test_disarm_ack_is_not_rc_handover_proof(self):
        self.io.release_override = {"vs_enabled": False, "vs_authority": "UNKNOWN"}
        result = self.runner.run()
        self.assertTrue(result["route_completed_estimated"])
        self.assertFalse(result["rc_handover_confirmed"])
        self.assertFalse(result["landing_confirmed"])

    def test_landing_needs_both_not_flying_and_motors_off(self):
        for flying, motors in ((False, True), (True, False), (False, None), (None, False)):
            with self.subTest(flying=flying, motors=motors):
                self.reset_fake()
                self.io.landing_values = {"IsFlying": flying, "AreMotorsOn": motors}
                result = self.runner.run()
                self.assertTrue(result["rc_handover_confirmed"])
                self.assertFalse(result["landing_confirmed"])
                self.assertNotIn("land", self.io.commands)

    def test_motor_sampler_stop_error_still_attempts_release_and_closes(self):
        self.io.stop_error = RuntimeError("fake sampler failed to join")
        result = self.runner.run()
        self.assertIn("disarm", self.io.commands)
        self.assertTrue(result["rc_handover_confirmed"])
        self.assertFalse(result["landing_confirmed"])
        self.assertIn("fake sampler failed to join", result["sampler_stop_error"])
        self.assertTrue(self.io.closed)

    def test_preflight_fault_survives_controller_empty_summary(self):
        self.io.ground_override = {"battery_percent": 20}
        self.assertEqual(self.runner.run()["fault"], "BATTERY_LOW")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("socket.socket", side_effect=AssertionError("real network forbidden")))
        self.enterContext(patch("socket.create_connection", side_effect=AssertionError("real network forbidden")))
        self.output = self.enterContext(redirect_stdout(StringIO()))
        self.enterContext(redirect_stderr(StringIO()))
        self.credentials = self.enterContext(patch.object(runner_module, "read_credentials", return_value=("fake-token", 9998)))
        self.factory = self.enterContext(patch("coex_io.ControlIO"))

    def test_default_and_site_ready_without_execute_remain_offline_plan(self):
        for args in ([], ["--site-ready"]):
            with self.subTest(args=args):
                self.assertEqual(runner_module.main(args), 0)
        self.credentials.assert_not_called()
        self.factory.assert_not_called()
        self.assertIn('"physical_execution": false', self.output.getvalue())

    def test_execute_requires_both_host_and_explicit_site_ready(self):
        for args in (["--execute"], ["--execute", "--host", "127.0.0.1"],
                     ["--execute", "--site-ready"]):
            with self.subTest(args=args), self.assertRaises(SystemExit) as caught:
                runner_module.main(args)
            self.assertEqual(caught.exception.code, 2)
        self.credentials.assert_not_called()
        self.factory.assert_not_called()

    def test_check_and_execute_modes_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            runner_module.main(["--check", "--execute"])
        self.factory.assert_not_called()

    def test_check_routes_to_ground_only_runner(self):
        self.factory.return_value.log_path = None
        with patch.object(runner_module.Runner, "run", return_value={"status": "GROUND_CHECK_PASSED"}) as run:
            self.assertEqual(runner_module.main(["--check", "--host", "127.0.0.1"]), 0)
        run.assert_called_once_with(check_only=True)

    def test_explicit_execute_routes_once_with_video_disabled_in_test(self):
        self.factory.return_value.log_path = None
        result = {"status": "ROUTE_COMPLETE_ESTIMATED", "route_completed_estimated": True,
                  "rc_handover_confirmed": True, "landing_confirmed": True}
        profile = load_profile()
        profile["live_execution_enabled"] = True
        with patch.object(runner_module, "load_profile", return_value=profile), \
                patch.object(runner_module.Runner, "run", return_value=result) as run:
            self.assertEqual(runner_module.main(["--execute", "--site-ready", "--host", "127.0.0.1", "--no-video"]), 0)
        run.assert_called_once_with(check_only=False)
        self.factory.assert_called_once()

    def test_disabled_profile_blocks_execute_before_credentials_or_io(self):
        profile = load_profile()
        profile["live_execution_enabled"] = False
        with patch.object(runner_module, "load_profile", return_value=profile), self.assertRaises(SystemExit):
            runner_module.main(["--execute", "--site-ready", "--host", "127.0.0.1", "--no-video"])
        self.credentials.assert_not_called()
        self.factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
