"""Supervised COEX flight. Default: offline plan. --check is ground-only.

Only --execute --site-ready may send takeoff/motion. No Android installation,
AprilTag, OA setting changes, automatic landing, or automatic retries.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import ipaddress
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc"))
from coex_mission import Controller, MissionFault, fresh, load_profile, number, rc_override

REQUIRED_BUILD = "5.18-telemetry-age.20260906.4"
CONNECTIVITY_BUILD = "5.18-connectivity.20260910.6"
SUPPORTED_BUILDS = {REQUIRED_BUILD, CONNECTIVITY_BUILD}
DEFAULT_LOCAL_CONFIG = Path("C:/dev/13_DRONE/pc/config.local.json")


class Runner:
    def __init__(self, io, profile, token, *, clock=time.perf_counter, sleep=time.sleep,
                 emit=print, landing_timeout_s=60.0, recorder=None):
        self.io, self.p, self.token = io, profile, token
        self.clock, self.sleep, self.emit = clock, sleep, emit
        self.landing_timeout_s, self.recorder = landing_timeout_s, recorder
        self.last = None
        self.controller = Controller(profile)
        self.takeoff_requested = self.armed_by_us = False
        self.started = None
        self.outcome = {"status": "NOT_STARTED", "route_completed_estimated": False,
                        "rc_handover_confirmed": False, "landing_confirmed": False,
                        "automatic_landing_commanded": False, "counted_trial": False}

    def command(self, kind, payload=None):
        self.last = self.io.command(kind, payload or {})
        return self.last

    def status(self, phase):
        return self.command("status", {"state": "coex_" + phase.lower()})

    def ground_check(self):
        # Only GETs before opening the control connection: closing an existing
        # armed control session could otherwise disarm somebody else's flight.
        if self.io.query_bool("IsFlying") is not False or self.io.query_bool("AreMotorsOn") is not False:
            raise MissionFault("GROUND_UNCONFIRMED", "no automatic resume")
        self.io.connect()
        self.status("PREFLIGHT")
        t, now = self.last, self.clock()
        if t.get("bridge_build_id") not in SUPPORTED_BUILDS:
            raise MissionFault("BUILD_MISMATCH", str(t.get("bridge_build_id")))
        if t.get("bridge_build_id") == CONNECTIVITY_BUILD:
            if t.get("time_watchdog_enabled") is not True:
                raise MissionFault("WATCHDOG_UNCONFIRMED")
            if fresh(t, "are_motors_on", "are_motors_on_age_ms", .5, now) is not False:
                raise MissionFault("GROUND_UNCONFIRMED", "new bridge motor telemetry")
        if fresh(t, "is_flying", "is_flying_age_ms", .5, now) is not False or t.get("armed") is not False:
            raise MissionFault("GROUND_UNCONFIRMED", "ACK flight/arm state")
        if t.get("vs_enabled") is not False:
            raise MissionFault("GROUND_UNCONFIRMED", "Virtual Stick already enabled/unknown")
        if rc_override(t):
            raise MissionFault("RC_OVERRIDE", "center sticks before a new run")
        if not self.p["execution_conditions"]["min_start_battery_percent"] <= number(t.get("battery_percent"), "battery") <= 100:
            raise MissionFault("BATTERY_LOW", "start requires at least 30 percent")
        for key, age in (("height_m", "height_age_ms"), ("yaw_deg", "attitude_age_ms"),
                         ("velocity_north_mps", "velocity_age_ms"), ("velocity_east_mps", "velocity_age_ms"),
                         ("velocity_down_mps", "velocity_age_ms")):
            number(fresh(t, key, age, .5, now), key)
        if not 0 <= t["height_m"] <= 2.1:
            raise MissionFault("HEIGHT_OUT_OF_BOUNDS", "ground height")
        for key in ("telemetry_generation", "_connection_epoch"):
            if isinstance(t.get(key), bool) or not isinstance(t.get(key), int) or t[key] < 0:
                raise MissionFault("INVALID_TELEMETRY", key)
        if t.get("disconnect_release_enabled") is not True:
            raise MissionFault("DISCONNECT_RELEASE_UNCONFIRMED")
        self.io.log_event("coex_ground_check", {"passed": True, "time_watchdog_enabled": t.get("time_watchdog_enabled"),
                           "warning": "RC takeover required if Wi-Fi blackhole prevents TCP close delivery"})
        self.emit(f"지상·모터 정지·센서 갱신 확인 완료. PC 오류 시 RC 인계; 앱 시간 watchdog={t.get('time_watchdog_enabled')}.")

    def _takeoff_and_arm(self):
        self.command("stick_mode", {"mode": "advanced"})
        self.command("zero")
        # Repeat the actual ground proofs immediately before the one takeoff.
        if self.io.query_bool("IsFlying") is not False or self.io.query_bool("AreMotorsOn") is not False:
            raise MissionFault("GROUND_UNCONFIRMED")
        self.status("BEFORE_TAKEOFF")
        if rc_override(self.last):
            raise MissionFault("RC_OVERRIDE")
        self.takeoff_requested = True
        self.emit("이륙 명령을 한 번 전송합니다. 이상 시 RC 스틱으로 제어하세요.")
        self.command("takeoff", {"confirmation_token": self.token})
        deadline, stable_since = self.clock() + 20.0, None
        while self.clock() < deadline:
            self.sleep(.1)
            t = self.status("TAKEOFF")
            now = self.clock()
            if rc_override(t):
                raise MissionFault("RC_OVERRIDE")
            height = number(fresh(t, "height_m", "height_age_ms", .5, now), "height")
            flying = fresh(t, "is_flying", "is_flying_age_ms", .5, now)
            mode = fresh(t, "flight_mode", "flight_mode_age_ms", .5, now)
            vd = number(fresh(t, "velocity_down_mps", "velocity_age_ms", .5, now), "vertical speed")
            if height > self.p["height"]["climb_height_upper_m"]:
                raise MissionFault("HEIGHT_OUT_OF_BOUNDS")
            # GPS_NORMAL was the observed idle/position mode on this build;
            # its name does not assert an outdoor GNSS fix. Never arm while
            # AUTO_TAKE_OFF or another automatic task owns the aircraft.
            ready = flying is True and .35 <= height <= 2.1 and abs(vd) <= .05 and mode == "GPS_NORMAL"
            if ready:
                stable_since = now if stable_since is None else stable_since
                if now - stable_since >= 2.0 - 1e-8:
                    break
            else:
                stable_since = None
        else:
            raise MissionFault("TAKEOFF_NOT_SETTLED", "RC landing required; no arm retry")
        if self.io.query_bool("AreMotorsOn") is not True:
            raise MissionFault("MOTORS_UNCONFIRMED")
        self.command("arm", {"confirmation_token": self.token})
        self.armed_by_us = True
        self.io.start_motors()
        deadline = self.clock() + 2.5
        while self.clock() < deadline:
            self.sleep(.1)
            t = self.status("ARM_VERIFY")
            if rc_override(t):
                raise MissionFault("RC_OVERRIDE")
            motors = self.io.motors_snapshot()
            if motors.error:
                raise MissionFault("MOTORS_UNCONFIRMED", motors.error)
            if (t.get("armed") is True and t.get("vs_enabled") is True and
                    t.get("vs_advanced_enabled") is True and t.get("vs_authority") == "MSDK" and
                    motors.value is True and motors.received_monotonic_s is not None):
                return
        raise MissionFault("AUTHORITY_LOST", "arm ACK was not followed by usable authority")

    def _flight(self):
        period = 1.0 / self.p["execution_conditions"]["nominal_pc_command_rate_hz"]
        next_tick, previous_phase, last_print = self.clock(), None, -float("inf")
        # ARM_VERIFY's current ACK is the first observation. Each subsequent
        # command's ACK is the next observation, with one command per tick.
        while True:
            self.sleep(max(0.0, next_tick - self.clock()))
            now = self.clock()
            if now - self.started > self.p["execution_conditions"]["mission_timeout_s"]:
                raise MissionFault("MISSION_TIMEOUT")
            sp = self.controller.step(self.last, self.io.motors_snapshot(), now)
            summary = self.controller.summary()
            self.io.log_event("coex_control_step", {**summary, "monotonic_s": now,
                                                   "height_m": self.last.get("height_m"),
                                                   "setpoint": asdict(sp)})
            if self.recorder:
                self.recorder.update_context(summary)
            if sp.phase != previous_phase or now - last_print >= 1:
                self.emit(f"{sp.phase}: 높이 {self.last.get('height_m'):.2f}m / 좌우 추정 {self.controller.x_right_m:+.2f}m")
                previous_phase, last_print = sp.phase, now
            if sp.done:
                self.command("zero")
                self.outcome["route_completed_estimated"] = True
                return
            payload = sp.payload()
            if all(v == 0 for v in payload.values()):
                self.command("zero")
            else:
                self.command("velocity", payload)
            next_tick = max(next_tick + period, self.clock())

    def _release(self):
        if not self.takeoff_requested:
            return
        # A failed transport must not be reused or reconnected for cleanup.
        # Fresh RC ownership must not be overridden by another command.
        try:
            t = self.status("RELEASE_CHECK")
            if t.get("vs_enabled") is False and t.get("vs_authority") == "RC":
                self.outcome["rc_handover_confirmed"] = True
                return
            if rc_override(t) or t.get("vs_authority") != "MSDK":
                return
            self.command("zero")
            self.command("disarm")
            deadline = self.clock() + 2.0
            while self.clock() < deadline:
                self.sleep(.1)
                t = self.status("RELEASE_VERIFY")
                if t.get("vs_enabled") is False and t.get("vs_authority") == "RC":
                    self.outcome["rc_handover_confirmed"] = True
                    break
        except Exception as error:
            self.outcome["release_error"] = str(error)

    def _wait_landing(self):
        self.emit("RC 제어권 반환 확인. RC로 직접 착륙해 주세요. 착륙 명령은 보내지 않습니다.")
        deadline = self.clock() + self.landing_timeout_s
        while self.clock() < deadline:
            try:
                flying = self.io.query_bool("IsFlying")
                motors = self.io.query_bool("AreMotorsOn")
            except Exception as error:
                self.outcome["landing_query_error"] = str(error)
                return
            if flying is False and motors is False:
                self.outcome["landing_confirmed"] = True
                self.emit("착륙·모터 정지 확인. 로그 저장 완료.")
                return
            self.sleep(.5)

    def run(self, *, check_only=False):
        try:
            self.ground_check()
            self.io.log_event("coex_profile", self.p)
            self.io.log_event("coex_profile_hash", {"sha256": hashlib.sha256(
                json.dumps(self.p, sort_keys=True).encode("utf-8")).hexdigest()})
            if check_only:
                self.outcome["status"] = "GROUND_CHECK_PASSED"
                return self.outcome
            self.started = self.clock()
            self._takeoff_and_arm()
            self._flight()
            self.outcome["status"] = "ROUTE_COMPLETE_ESTIMATED"
        except (Exception, KeyboardInterrupt) as error:
            self.outcome["status"] = "ABORTED"
            self.outcome["fault"] = getattr(error, "code", type(error).__name__)
            self.outcome["detail"] = str(error)
            self.emit(f"중단: {self.outcome['fault']}. 자동 재시도하지 않습니다.")
        finally:
            sampler_stopped = False
            try:
                self._release()
            except (Exception, KeyboardInterrupt) as error:
                self.outcome["release_error"] = str(error)
            try:
                self.io.stop_motors()
                sampler_stopped = True
            except (Exception, KeyboardInterrupt) as error:
                self.outcome["sampler_stop_error"] = str(error)
            try:
                if self.outcome["rc_handover_confirmed"] and sampler_stopped:
                    self._wait_landing()
                elif self.outcome["rc_handover_confirmed"]:
                    self.emit("RC 제어권 반환 확인. RC로 착륙하세요. 착륙 상태 조회는 확인하지 못했습니다.")
                elif self.takeoff_requested:
                    self.emit("RC 인계 상태를 확인하지 못했습니다. 기체가 떠 있다면 즉시 RC로 제어·착륙하세요.")
            except (Exception, KeyboardInterrupt) as error:
                self.outcome["cleanup_error"] = str(error)
            finally:
                summary = self.controller.summary()
                if summary["fault"] is None:
                    summary.pop("fault")
                self.outcome.update(summary)
                self.outcome["log_path"] = str(getattr(self.io, "log_path", None))
                try:
                    self.io.log_event("coex_result", self.outcome)
                finally:
                    try:
                        self.io.close()
                    except (Exception, KeyboardInterrupt) as error:
                        self.outcome["close_error"] = str(error)
        return self.outcome


def read_credentials(path, host):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    network = config["network"]
    token = network.get("confirmation_token")
    if not isinstance(token, str) or not token or token.lower() in {"change-me", "changeme", "example", "replace-me"}:
        raise ValueError("existing private configuration must contain the app confirmation token")
    ipaddress.ip_address(host)
    return token, int(network.get("port", 9998))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="ground-only read checks; no takeoff/arm")
    mode.add_argument("--execute", action="store_true", help="perform one supervised flight")
    mode.add_argument("--simulate", action="store_true", help="offline whole-mission simulation")
    parser.add_argument("--host", help="current phone hotspot IPv4/IPv6 address")
    parser.add_argument("--config", type=Path, default=DEFAULT_LOCAL_CONFIG)
    parser.add_argument("--site-ready", action="store_true", help="operator confirms clear route, sensors uncovered and RC ready")
    parser.add_argument("--no-video", action="store_true", help="skip optional 1 Hz camera recording")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "pc" / "logs" / "coex")
    args = parser.parse_args(argv)
    profile = load_profile()
    if not (args.check or args.execute or args.simulate):
        print(json.dumps({"mode": "plan_only", "physical_execution": False, "profile": profile}, ensure_ascii=False, indent=2))
        return 0
    if args.simulate:
        from coex_sim import simulate
        result = simulate(profile)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["route_completed_estimated"] and result["landing_confirmed"] else 2
    if not args.host:
        parser.error("--host with the current phone IP is required")
    if args.execute and not args.site_ready:
        parser.error("--execute requires --site-ready (clear route, uncovered sensors, RC pilot ready)")
    if args.execute and profile.get("live_execution_enabled") is not True:
        parser.error("this profile has not been enabled for the prepared supervised runner")
    try:
        token, port = read_credentials(args.config, args.host)
    except (ValueError, KeyError, OSError) as error:
        parser.error(f"local configuration unavailable: {type(error).__name__}; check --config and --host")
    from coex_io import ControlIO
    io = ControlIO(args.host, port, query_port=9997, timeout_s=.4, log_dir=args.log_dir)
    runner = Runner(io, profile, token)
    capture = recorder = None
    try:
        # This optional observer never supplies navigation or blocks a flight
        # waiting for an AprilTag or a camera frame.
        if args.execute and not args.no_video:
            try:
                from drone_nav.vision import TcpVideoStream
                from drone_nav.observation import FrameRecorder
                capture = TcpVideoStream(args.host, 9999, "h264")
                recording_dir = args.log_dir / (time.strftime("%Y%m%dT%H%M%S") + "-camera")
                recorder = FrameRecorder(recording_dir, capture, max_frames=180)
                runner.recorder = recorder
            except Exception as error:
                print(f"영상 기록 준비 실패: {type(error).__name__}; 센서 로그와 제어 검사는 계속합니다.")
        result = runner.run(check_only=args.check)
    finally:
        if recorder:
            recorder.close()
        if capture:
            capture.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if io.log_path is not None:
        report = Path(io.log_path).with_suffix(".summary.json")
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result["status"] == "GROUND_CHECK_PASSED" or (
        result["route_completed_estimated"] and result["rc_handover_confirmed"] and result["landing_confirmed"]
    ) else 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    raise SystemExit(main())
