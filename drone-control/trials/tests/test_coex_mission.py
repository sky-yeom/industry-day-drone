"""Offline controller contract tests; the plant below is a synthetic test fixture.

Command duration integration exists only in the virtual plant, never as evidence of
real aircraft displacement. These tests do not validate real flight safety/accuracy.
"""
from collections import deque
from copy import deepcopy
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coex_mission import Controller, MissionFault, load_profile


def telemetry(now, **changes):
    raw = dict(_received_monotonic_s=now, _connection_epoch=1,
        telemetry_generation=1, height_m=1.8, height_age_ms=0,
        is_flying=True, is_flying_age_ms=0, armed=True, vs_enabled=True,
        vs_advanced_enabled=True, vs_authority="MSDK", stick_mode="OFFICIAL_ADVANCED",
        sdk_roll_pitch_mode="VELOCITY", battery_percent=80,
        velocity_north_mps=0.0, velocity_east_mps=0.0, velocity_down_mps=0.0,
        velocity_age_ms=0, yaw_deg=0.0, attitude_age_ms=0, rc_override_age_ms=-1)
    raw.update(changes)
    return raw


def motors(now, **changes):
    values = dict(value=True, received_monotonic_s=now, connection_epoch=1, error=None)
    values.update(changes)
    return SimpleNamespace(**values)


class FakePlant:
    """NED kinematics with 150 ms command delay and first-order velocity inertia.

    The 10 ms physics clock is independent of the controller's 100 ms sample clock.
    Actual simulated velocities, including lag after a zero command, are measured.
    """
    def __init__(self, yaw_deg=0.0, height_m=1.8):
        self.now = 100.0
        self.yaw = math.radians(yaw_deg)
        self.origin_yaw = self.yaw
        self.height = height_m
        self.north = self.east = 0.0
        self.vn = self.ve = self.vu = 0.0
        self.command = (0.0, 0.0, 0.0, 0.0)
        self.pending = deque()

    def submit(self, setpoint):
        self.pending.append((self.now + 0.15, (setpoint.forward_mps,
            setpoint.right_mps, setpoint.up_mps, setpoint.yaw_rate_rps)))

    def advance(self, dt=0.1):
        end = self.now + dt
        while self.now < end - 1e-9:
            while self.pending and self.pending[0][0] <= self.now + 1e-9:
                _, self.command = self.pending.popleft()
            h = min(0.01, end - self.now)
            f, r, u, yaw_rate = self.command
            c, s = math.cos(self.yaw), math.sin(self.yaw)
            targets = (f * c - r * s, f * s + r * c, u)
            before = (self.vn, self.ve, self.vu)
            alpha = 1.0 - math.exp(-h / 0.25)
            self.vn, self.ve, self.vu = [v + alpha * (target - v)
                for v, target in zip(before, targets)]
            self.north += (before[0] + self.vn) * h / 2
            self.east += (before[1] + self.ve) * h / 2
            self.height += (before[2] + self.vu) * h / 2
            self.yaw += yaw_rate * h
            self.now += h

    @property
    def right(self):
        return -self.north * math.sin(self.origin_yaw) + self.east * math.cos(self.origin_yaw)

    @property
    def forward(self):
        return self.north * math.cos(self.origin_yaw) + self.east * math.sin(self.origin_yaw)

    def observation(self, **changes):
        return telemetry(self.now, height_m=self.height, velocity_north_mps=self.vn,
            velocity_east_mps=self.ve, velocity_down_mps=-self.vu,
            yaw_deg=(math.degrees(self.yaw) + 180) % 360 - 180, **changes)


class Simulation:
    def __init__(self, profile=None, yaw_deg=0.0, height_m=1.8):
        self.controller = Controller(profile or load_profile())
        self.plant = FakePlant(yaw_deg, height_m)
        self.trace = []

    def tick(self, **changes):
        p = self.plant
        output = self.controller.step(p.observation(**changes), motors(p.now), p.now)
        self.trace.append((p.now, self.controller.phase, p.right, p.forward, output))
        p.submit(output)
        p.advance()
        return output

    def until(self, phase):
        for _ in range(1000):
            if self.controller.phase == phase:
                return
            self.tick()
        raise AssertionError(f"phase {phase} not reached; current={self.controller.phase}")


class CoexMissionTests(unittest.TestCase):
    def setUp(self):
        # Catch accidental network use without creating a socket or contacting hardware.
        for target in ("socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex"):
            blocker = patch(target, side_effect=AssertionError("offline tests forbid network I/O"))
            blocker.start()
            self.addCleanup(blocker.stop)
        self.profile = load_profile()

    def fault(self, code, controller, raw, motor_state=None, now=None):
        now = raw["_received_monotonic_s"] if now is None else now
        with self.assertRaises(MissionFault) as caught:
            controller.step(raw, motors(now) if motor_state is None else motor_state, now)
        self.assertEqual(caught.exception.code, code)

    def left_controller(self):
        sim = Simulation()
        sim.until("LEFT")
        return sim

    def test_height_settle_requires_two_seconds_before_lateral_motion(self):
        controller = Controller(self.profile)
        for index in range(20):
            now = 100 + index / 10
            command = controller.step(telemetry(now), motors(now), now)
            self.assertEqual(controller.phase, "CLIMB")
            self.assertEqual(command.right_mps, 0.0)
        for index in range(20, 23):
            now = 100 + index / 10
            controller.step(telemetry(now), motors(now), now)
        self.assertEqual(controller.phase, "LEFT")

    def test_height_display_alone_does_not_prove_vertical_settle(self):
        controller = Controller(self.profile)
        # Synthetic sensor inconsistency: fixed displayed height while measured NED
        # velocity still says ascending. Height alone must not unlock lateral motion.
        for index in range(30):
            now = 100 + index / 10
            command = controller.step(telemetry(now, velocity_down_mps=-0.12), motors(now), now)
            self.assertEqual(controller.phase, "CLIMB")
            self.assertEqual((command.forward_mps, command.right_mps), (0, 0))
        for index in range(30, 53):
            now = 100 + index / 10
            controller.step(telemetry(now, velocity_down_mps=0), motors(now), now)
            if controller.phase == "LEFT":
                break
        self.assertEqual(controller.phase, "LEFT")

    def test_normal_roundtrip_under_command_delay_and_inertia(self):
        for yaw in (0.0, 90.0, 180.0):
            with self.subTest(yaw=yaw):
                sim = Simulation(yaw_deg=yaw, height_m=1.4)
                sim.until("DONE")
                phases = list(dict.fromkeys(row[1] for row in sim.trace))
                self.assertEqual(phases, ["CLIMB", "LEFT", "BRAKE_LEFT", "RIGHT", "FINAL_HOVER", "DONE"])
                self.assertTrue(sim.trace[-1][4].done)
                self.assertLess(min(row[2] for row in sim.trace), -0.85)
                self.assertLess(abs(sim.plant.right), 0.18)
                self.assertLess(abs(sim.plant.forward), 0.04)
                self.assertAlmostEqual(sim.controller.x_right_m, sim.plant.right, delta=0.04)
                self.assertAlmostEqual(sim.controller.x_forward_m, sim.plant.forward, delta=0.04)
                for _, _, _, _, command in sim.trace:
                    self.assertLessEqual(abs(command.right_mps), 0.2 + 1e-9)
                    self.assertLessEqual(abs(command.yaw_rate_rps), math.radians(10) + 1e-9)
                final = sim.trace[-1][4]
                self.assertEqual((final.forward_mps, final.right_mps, final.up_mps, final.yaw_rate_rps), (0, 0, 0, 0))

    def test_fakeplant_has_real_delay_and_residual_velocity(self):
        plant = FakePlant()
        plant.submit(SimpleNamespace(forward_mps=0, right_mps=-0.2, up_mps=0, yaw_rate_rps=0))
        plant.advance(0.1)
        self.assertEqual(plant.east, 0.0)
        plant.advance(0.2)
        self.assertLess(plant.ve, 0.0)
        self.assertGreater(plant.ve, -0.2)
        plant.submit(SimpleNamespace(forward_mps=0, right_mps=0, up_mps=0, yaw_rate_rps=0))
        before = plant.east
        plant.advance(0.3)
        self.assertLess(plant.east, before)
        self.assertLess(plant.ve, 0.0)

    def test_yaw_wrap_uses_small_angular_difference(self):
        sim = Simulation(yaw_deg=179.0)
        sim.until("LEFT")
        sim.plant.yaw = math.radians(-179.0)
        command = sim.tick()
        self.assertLess(command.yaw_rate_rps, 0)
        self.assertLess(abs(command.yaw_rate_rps), math.radians(3))

    def test_missing_and_nonfinite_telemetry_fail_closed(self):
        required = ("height_m", "height_age_ms", "is_flying", "is_flying_age_ms",
            "velocity_north_mps", "velocity_east_mps", "velocity_down_mps", "velocity_age_ms",
            "yaw_deg", "attitude_age_ms", "battery_percent", "telemetry_generation")
        for field in required:
            with self.subTest(missing=field):
                raw = telemetry(100)
                del raw[field]
                self.fault("INVALID_TELEMETRY", Controller(self.profile), raw)
        for field in ("height_m", "velocity_north_mps", "velocity_east_mps", "velocity_down_mps", "yaw_deg", "battery_percent"):
            for value in (float("nan"), float("inf"), True):
                with self.subTest(field=field, value=value):
                    self.fault("INVALID_TELEMETRY", Controller(self.profile), telemetry(100, **{field: value}))

    def test_stale_samples_fail_closed(self):
        for field in ("height_age_ms", "velocity_age_ms", "attitude_age_ms", "is_flying_age_ms"):
            with self.subTest(field=field):
                self.fault("STALE_TELEMETRY", Controller(self.profile), telemetry(100, **{field: 501}))
        self.fault("STALE_TELEMETRY", Controller(self.profile), telemetry(100), now=101)

    def test_rc_override_aborts_each_active_phase(self):
        for phase in ("CLIMB", "LEFT", "BRAKE_LEFT", "RIGHT", "FINAL_HOVER"):
            with self.subTest(phase=phase):
                sim = Simulation()
                sim.until(phase)
                self.fault("RC_OVERRIDE", sim.controller, sim.plant.observation(rc_override_age_ms=0))

    def test_generation_change_aborts_without_reinitializing_origin(self):
        sim = self.left_controller()
        self.fault("GENERATION_CHANGED", sim.controller, sim.plant.observation(telemetry_generation=2))

    def test_old_false_or_failed_motor_evidence_is_rejected(self):
        for changes in ({"received_monotonic_s": 98.9}, {"value": False},
                        {"value": None}, {"error": "query_timeout"}, {"connection_epoch": 2}):
            with self.subTest(changes=changes):
                self.fault("MOTORS_UNCONFIRMED", Controller(self.profile), telemetry(100), motors(100, **changes))

    def test_authority_loss_aborts(self):
        for changes in ({"vs_enabled": False}, {"vs_advanced_enabled": False},
                        {"vs_authority": "RC"}, {"stick_mode": "legacy"}, {"sdk_roll_pitch_mode": "ANGLE"}):
            with self.subTest(changes=changes):
                sim = self.left_controller()
                self.fault("AUTHORITY_LOST", sim.controller, sim.plant.observation(**changes))

    def test_gap_and_duplicate_observation_are_not_integrated(self):
        sim = self.left_controller()
        now = sim.plant.now + 0.3
        self.fault("INTEGRATION_GAP", sim.controller, telemetry(now))
        controller = Controller(self.profile)
        raw = telemetry(100)
        controller.step(raw, motors(100), 100)
        self.fault("DUPLICATE_OBSERVATION", controller, raw, now=100.1)

    def test_wrong_direction_uses_measured_signed_velocity(self):
        sim = self.left_controller()
        for index in range(10):
            now = sim.plant.now + index / 10
            raw = telemetry(now, velocity_east_mps=0.15)
            try:
                sim.controller.step(raw, motors(now), now)
            except MissionFault as exc:
                self.assertEqual(exc.code, "WRONG_DIRECTION")
                return
        self.fail("opposite measured velocity did not abort")

    def test_stalled_vehicle_never_completes_from_command_duration(self):
        sim = self.left_controller()
        for index in range(40):
            now = sim.plant.now + index / 10
            try:
                sim.controller.step(telemetry(now), motors(now), now)
            except MissionFault as exc:
                self.assertEqual(exc.code, "MOTION_UNCONFIRMED")
                self.assertAlmostEqual(sim.controller.x_right_m, 0.0)
                return
        self.fail("unmoving telemetry did not trigger signed progress watchdog")

    def test_climb_leg_and_total_timeouts(self):
        # Use allowed profile limits and simulated time, never sleeping or bypassing validation.
        for code in ("CLIMB_TIMEOUT", "LEG_TIMEOUT", "MISSION_TIMEOUT"):
            with self.subTest(code=code):
                profile = deepcopy(self.profile)
                if code == "MISSION_TIMEOUT":
                    profile["height"]["climb_timeout_s"] = 20
                    profile["horizontal"]["each_leg_timeout_s"] = 25
                    profile["execution_conditions"]["mission_timeout_s"] = 60
                controller = Controller(profile)
                with self.assertRaises(MissionFault) as caught:
                    for index in range(1000):
                        now = 100 + index / 10
                        climbing = code == "CLIMB_TIMEOUT" or (code == "MISSION_TIMEOUT" and index < 170)
                        speed = 0.039 if code == "MISSION_TIMEOUT" else 0.03
                        east = -speed if controller.phase == "LEFT" else speed if controller.phase == "RIGHT" else 0
                        controller.step(telemetry(now, height_m=1.0 if climbing else 1.8,
                            velocity_east_mps=east), motors(now), now)
                self.assertEqual(caught.exception.code, code)

    def test_oa_60000_is_record_only_and_never_height_fallback(self):
        extra = dict(oa_horizontal_distances_mm=[60000] * 8,
            oa_upward_distance_mm=60000, oa_downward_distance_mm=60000, oa_obstacle_data_age_ms=0)
        clean, recorded = Simulation(), Simulation()
        for _ in range(500):
            baseline = clean.tick()
            observed = recorded.tick(**extra)
            self.assertEqual(baseline, observed)
            if baseline.done:
                break
        self.assertTrue(baseline.done)
        raw = telemetry(100, **extra)
        del raw["height_m"]
        self.fault("INVALID_TELEMETRY", Controller(self.profile), raw)


if __name__ == "__main__":
    unittest.main()
