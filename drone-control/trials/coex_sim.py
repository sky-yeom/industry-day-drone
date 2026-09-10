"""Offline whole-run simulator; its synthetic sensor data is never live data."""
from collections import deque
from dataclasses import dataclass
import copy
import math


@dataclass(frozen=True)
class Motors:
    value: bool | None
    received_monotonic_s: float | None
    connection_epoch: int = 1
    error: str | None = None


class SimIO:
    """Delayed first-order velocity response, takeoff, and simulated RC landing."""
    log_path = None
    session_id = "offline-simulation"

    def __init__(self):
        self.now = 1.0
        self.height = self.north = self.east = self.yaw = 0.0
        self.vn = self.ve = self.up = 0.0
        self.armed = self.flying = self.motors = self.connected = False
        self.start_takeoff = self.release_time = None
        self.target = (0.0, 0.0, 0.0, 0.0)
        self.queue = deque()
        self.commands = []
        self.events = []
        self.motor_sampling = False
        self.mode = "OFFICIAL_ADVANCED"

    def clock(self):
        return self.now

    def sleep(self, duration):
        end = self.now + duration
        while self.now < end - 1e-10:
            dt = min(.01, end - self.now)
            self.now += dt
            while self.queue and self.queue[0][0] <= self.now:
                _, self.target = self.queue.popleft()
            forward, right, up, yaw_rate = self.target
            if self.start_takeoff is not None and not self.armed and self.release_time is None:
                self.motors = self.flying = True
                up = .3 if self.height < .55 else 0.0
            if self.release_time is not None:
                # The simulated operator, not a land command, brings it down.
                up = -.5 if self.now - self.release_time > 1 else 0.0
                forward = right = yaw_rate = 0.0
            a = math.radians(self.yaw)
            desired_n = forward * math.cos(a) - right * math.sin(a)
            desired_e = forward * math.sin(a) + right * math.cos(a)
            blend = 1 - math.exp(-dt / .25)
            self.vn += (desired_n - self.vn) * blend
            self.ve += (desired_e - self.ve) * blend
            self.up += (up - self.up) * blend
            self.north += self.vn * dt
            self.east += self.ve * dt
            self.height += self.up * dt
            self.yaw = (self.yaw + math.degrees(yaw_rate) * dt + 180) % 360 - 180
            if self.height <= 0:
                self.height = 0.0
                self.up = 0.0
                if self.release_time is not None:
                    self.flying = self.motors = False

    def connect(self):
        self.connected = True

    def snapshot(self):
        return {"bridge_build_id": "5.18-telemetry-age.20260906.4",
                "_received_monotonic_s": self.now, "_connection_epoch": 1,
                "telemetry_generation": 1, "height_m": round(self.height, 1),
                "height_age_ms": 0, "velocity_north_mps": self.vn, "velocity_east_mps": self.ve,
                "velocity_down_mps": -self.up, "velocity_age_ms": 0,
                "yaw_deg": self.yaw, "attitude_age_ms": 0,
                "is_flying": self.flying, "is_flying_age_ms": 0,
                "flight_mode": "VIRTUAL_STICK" if self.armed else (
                    "AUTO_TAKE_OFF" if self.start_takeoff is not None and self.height < .55 else "GPS_NORMAL"),
                "flight_mode_age_ms": 0, "armed": self.armed,
                "vs_enabled": self.armed, "vs_advanced_enabled": self.armed,
                "vs_authority": "MSDK" if self.armed else "RC",
                "stick_mode": self.mode, "sdk_roll_pitch_mode": "VELOCITY",
                "battery_percent": 80, "rc_override_age_ms": -1,
                "disconnect_release_enabled": True, "time_watchdog_enabled": False,
                "oa_horizontal_distances_mm": [60000] * 360,
                "oa_downward_distance_mm": self.height * 1000,
                "oa_upward_distance_mm": 60000, "oa_obstacle_data_age_ms": 100}

    def command(self, kind, payload, timeout_s=None):
        if not self.connected:
            raise ConnectionError("simulated socket closed")
        self.sleep(.01)  # A finite receive time, separate from previous ACK.
        self.commands.append((kind, {k: ("<redacted>" if k == "confirmation_token" else v)
                                     for k, v in payload.items()}))
        if kind == "takeoff":
            self.start_takeoff = self.now
        elif kind == "arm":
            self.armed = True
        elif kind == "velocity":
            self.queue.append((self.now + .15, tuple(payload[k] for k in
                               ("forward_mps", "right_mps", "up_mps", "yaw_rate_rps"))))
        elif kind == "zero":
            self.queue.append((self.now + .15, (0, 0, 0, 0)))
        elif kind == "disarm":
            self.armed = False
            self.queue.clear()
            self.target = (0, 0, 0, 0)
            self.release_time = self.now
        elif kind not in ("status", "stick_mode"):
            raise AssertionError("unexpected command " + kind)
        return self.snapshot()

    def query_bool(self, key):
        return self.flying if key == "IsFlying" else self.motors

    def start_motors(self):
        self.motor_sampling = True

    def stop_motors(self):
        self.motor_sampling = False

    def motors_snapshot(self):
        return Motors(self.motors, self.now)

    def log_event(self, event, data):
        self.events.append((event, copy.deepcopy(data)))

    def close(self):
        self.connected = False


def simulate(profile):
    from coex_tagless_left_return import Runner
    io = SimIO()
    result = Runner(io, profile, "SIMULATION_TOKEN", clock=io.clock, sleep=io.sleep,
                    emit=lambda message: None).run()
    result.update(physical_execution=False, execution_mode="simulation",
                  simulated_elapsed_s=round(io.now - 1, 3),
                  simulated_final_north_m=round(io.north, 4),
                  simulated_final_east_m=round(io.east, 4),
                  commands_by_type={kind: sum(k == kind for k, _ in io.commands)
                                    for kind in sorted({k for k, _ in io.commands})})
    assert not any(kind in {"land", "obstacle_avoidance", "attitude"} for kind, _ in io.commands)
    return result
