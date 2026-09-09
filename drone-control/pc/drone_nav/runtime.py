"""10 Hz hardware loop: optional auto takeoff, altitude hold, dead reckoning.

Safety model:
- The RC-N2 pilot always wins: any physical stick input makes the Android
  bridge release Virtual Stick immediately, after which every command from
  this loop is rejected and the loop exits cleanly.
- Losing both tags starts a budgeted dead-reckoning bridge; exhausting the
  budget decelerates to a hover (TAG_LOST).
- This diagnostic build has no time-based Android heartbeat zero/release.
  Physical RC override and an actual TCP disconnect still release control.
"""

from __future__ import annotations

import math
import time

from .config import AppConfig
from .controller import GoalVisualAligner, LineFollower, Velocity
from .estimator import PoseEstimator
from .localization import TagLocalizer
from .protocol import NDJSONClient, RateLimiter, Telemetry
from .observation import observe_sector
from .safety import SafetyFSM, State
from .transforms import translation, yaw
from .vision import AprilTagDetector, TcpVideoStream

# Reject camera frames older than this: a stalled stream must not keep
# refreshing tag observations.
FRESH_FRAME_S = 0.5
# Ultrasonic height older than this stops altitude corrections.
#
# Height telemetry is CHANGE-triggered and quantised to decimetres, so a
# steady hover simply stops producing updates: here "stale" means "unchanged",
# not "unknown". A 1 s window deadlocked the altitude loop - it needs a fresh
# height to command a climb, and a climb to produce a fresh height, so it sat
# at 0.5 m commanding +0.00 m/s forever. Trusting an unchanged reading for a
# few seconds is acceptable here because a real socket disconnect releases
# Virtual Stick and the physical RC override remains available.
FRESH_HEIGHT_S = 6.0
# The takeoff gate tolerates a staler height than the control loop does.
# Telemetry is change-triggered and the height is quantised to decimetres, so
# a drone that has settled into its hover stops reporting entirely. Staleness
# is safe in this direction: while climbing, an old ">= 0.9 m" reading means
# the aircraft is now higher still, never lower.
TAKEOFF_HEIGHT_STALE_S = 4.0
# DJI refuses auto-takeoff on a low battery with an opaque error; stop earlier
# with a reason the operator can act on. Well above DJI's own cutoff.
MIN_TAKEOFF_BATTERY_PERCENT = 10.0
# Measured on a Mini 4 Pro indoors: DJI auto-takeoff settles at ~0.5 m, not
# the ~1.2 m the docs suggest. This is only a "clearly off the ground" gate -
# the altitude loop flies to flight.target_altitude_m afterwards - so keep it
# below the real hover height or the takeoff wait can never succeed.
TAKEOFF_HOVER_MIN_M = 0.3
# Give up waiting for the start tag after this long.
LOCALIZE_TIMEOUT_S = 30.0
# Stage-1 altitude sweep: prove vertical control in both directions.
SWEEP_DELTA_M = 0.15
SWEEP_TOLERANCE_M = 0.06
SWEEP_STEP_TIMEOUT_S = 25.0
# Settle time between forward legs.
LEG_PAUSE_S = 4.0
# Reverse only after movement was observed and then remained below this speed.
STALL_SPEED_MPS = 0.04
STALL_CONFIRM_S = 2.0
REVERSE_PAUSE_S = 0.8
# Stop a continuous left/right diagnostic only for an obstacle in the commanded
# lateral sector.  DJI's official HSI uses 0=front, 90=right, 180=back,
# 270=left for the 360-degree horizontal range array.  A narrow sector avoids
# stopping on the front/back/diagonal clearance the operator has already
# checked for this test.
LATERAL_OBSTACLE_STOP_M = 0.70
LATERAL_OBSTACLE_HALF_WIDTH_DEG = 5.0


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def run_hardware(
    config: AppConfig,
    arm_token: str,
    manual_takeoff_confirmed: bool,
    auto_takeoff: bool = False,
    land_after_hover: bool = False,
) -> None:
    config.require_hardware_calibration()
    if not auto_takeoff and not manual_takeoff_confirmed:
        raise RuntimeError(
            "either --takeoff (app-commanded) or --manual-takeoff-confirmed is required"
        )
    if arm_token != config.network.confirmation_token:
        raise PermissionError("CLI arm confirmation token does not match configuration")

    detector = AprilTagDetector(config)
    capture = TcpVideoStream(
        config.network.host,
        config.network.video_port,
        config.network.video_codec,
    )
    client = NDJSONClient(
        config.network.host, config.network.port, config.network.protocol_version
    )
    localizer = TagLocalizer(config)
    estimator = PoseEstimator(config.dead_reckoning)
    fsm = SafetyFSM(config.safety)
    start, goal = config.start_tag.world_pose, config.goal_tag.world_pose
    start_tag_id, goal_tag_id = config.start_tag.id, config.goal_tag.id
    follower = LineFollower(
        (start.x_m, start.y_m), (goal.x_m, goal.y_m), config.controller
    )
    aligner = GoalVisualAligner(
        config.controller,
        (config.camera.cx, config.camera.cy),
        config.body_camera.matrix(),
    )
    limiter = RateLimiter(config.network.rate_hz)
    dt = 1.0 / config.network.rate_hz
    flight = config.flight

    last_command = Velocity(0.0, 0.0)
    last_goal_center: tuple[float, float] | None = None
    last_goal_s: float | None = None
    localize_started_s: float | None = None
    estimate_lost_s: float | None = None
    phase = State.LOCALIZE_START
    client.connect()
    print(f"Control log: {client.log_path}")
    try:
        now = time.monotonic()
        fsm.transition(State.WAIT_FOR_TAKEOFF, now)
        client.stick_mode("advanced")
        print("Stick interface: official advanced")
        client.zero()

        if auto_takeoff:
            print("Takeoff: commanding DJI auto-takeoff (hovers at ~1.2 m)")
            client.takeoff(arm_token)
            _wait_for_takeoff(client, limiter, flight.takeoff_timeout_s)
            print("Takeoff: airborne")

        # Gimbal before arm keeps the first motion sequence easy to diagnose.
        now = time.monotonic()
        fsm.transition(State.GIMBAL_DOWN, now)
        client.gimbal_down()
        fsm.transition(State.INITIALIZE, now)
        _arm_when_ready(client, arm_token, limiter)
        fsm.transition(State.LOCALIZE_START, now)
        localize_started_s = time.monotonic()
        previous_loop_s: float | None = None

        while True:
            limiter.wait()
            now = time.monotonic()
            # Blocking sends can stretch a tick well past the nominal period;
            # dead reckoning must integrate the time that actually passed.
            loop_dt = (
                dt if previous_loop_s is None
                else min(0.5, max(0.02, now - previous_loop_s))
            )
            previous_loop_s = now
            client.heartbeat()
            fsm.observe_network(now)
            telemetry = client.last_telemetry

            if (
                telemetry is not None
                and telemetry.rc_override_age_s is not None
                and telemetry.rc_override_age_s < 5.0
            ):
                print("RC stick override detected: control stays with the pilot")
                return

            ok, frame, frame_age = capture.read()
            detections = (
                detector.detect(frame)
                if ok and frame_age <= FRESH_FRAME_S
                else []
            )
            if detections:
                fsm.observe_tag(now)
            goal_centers = [
                d.center_px for d in detections
                if d.tag_id == goal_tag_id and d.center_px
            ]
            if goal_centers:
                last_goal_center = goal_centers[0]
                last_goal_s = now

            # After a long blind stretch the EMA history is stale; restart the
            # filter from the fresh detection.
            if detections and estimator.blind_time_s > 1.0:
                localizer.reset()
            pose = localizer.update(detections) if detections else None
            if pose is not None:
                px, py, _ = translation(pose)
                estimator.update_fix(px, py, yaw(pose), telemetry)
                estimate_ok = True
            else:
                estimate_ok = estimator.update_blind(loop_dt, telemetry, last_command)

            # Hold whatever altitude the mission asked for.
            up_mps = _altitude_command(telemetry, flight)

            if phase is State.LOCALIZE_START:
                if pose is None or not any(
                    d.tag_id == start_tag_id for d in detections
                ):
                    if now - localize_started_s > LOCALIZE_TIMEOUT_S:
                        print("Start tag not found in time: hovering, aborting run")
                        return
                    client.velocity(Velocity(0.0, 0.0, up_mps))
                    client.status(State.LOCALIZE_START.value)
                    continue
                phase = State.FOLLOW_LINE
                fsm.transition(phase, now)
                print("Start tag locked: following the line to the goal tag")

            if phase is State.FOLLOW_LINE:
                if not estimate_ok:
                    # Dead-reckoning budget exhausted: stage the deceleration
                    # from the moment the budget expired (the last tag sighting
                    # is already far in the past, so tag_scale would be 0).
                    if estimate_lost_s is None:
                        estimate_lost_s = now
                    lost_age = now - estimate_lost_s
                    if lost_age >= config.safety.tag_decelerate_s:
                        fsm.transition(State.TAG_LOST, now)
                        client.velocity(Velocity(0.0, 0.0, up_mps))
                        client.status(State.TAG_LOST.value)
                        continue
                    if lost_age < config.safety.tag_hold_s:
                        scale = 1.0
                    else:
                        scale = (config.safety.tag_decelerate_s - lost_age) / (
                            config.safety.tag_decelerate_s
                            - config.safety.tag_hold_s
                        )
                    command = last_command.scaled(scale)
                else:
                    estimate_lost_s = None
                    if fsm.state is State.TAG_LOST:
                        fsm.transition(phase, now)
                    est = estimator.state
                    command = follower.command((est.x_m, est.y_m), est.yaw_rad)
                    last_command = command
                    if follower.arrived((est.x_m, est.y_m)):
                        phase = State.GOAL_APPROACH
                        fsm.transition(phase, now)

            if phase in {State.GOAL_APPROACH, State.VISUAL_ALIGN}:
                phase = State.VISUAL_ALIGN
                fsm.transition(phase, now)
                goal_age = float("inf") if last_goal_s is None else now - last_goal_s
                if goal_age >= config.safety.tag_decelerate_s:
                    fsm.transition(State.TAG_LOST, now)
                    client.velocity(Velocity(0.0, 0.0, up_mps))
                    client.status(State.TAG_LOST.value)
                    continue
                if last_goal_center is None:
                    command = Velocity(0.0, 0.0)
                else:
                    command = aligner.command(last_goal_center)
                    if goal_age >= config.safety.tag_hold_s:
                        command = command.scaled(
                            (config.safety.tag_decelerate_s - goal_age)
                            / (
                                config.safety.tag_decelerate_s
                                - config.safety.tag_hold_s
                            )
                        )
                last_command = command
                if command.is_zero and goal_centers:
                    phase = State.HOVER
                    fsm.transition(State.HOVER, now)
                    print("Goal tag centered: hovering")
                    if land_after_hover:
                        client.zero()
                        client.status(State.HOVER.value)
                        print("Landing (auto)")
                        client.land(arm_token)
                        return
                    continue

            if phase is State.HOVER:
                # Keep position and altitude until the pilot takes over
                # (RC stick) or the operator stops the program (Ctrl+C).
                client.velocity(Velocity(0.0, 0.0, up_mps))
                client.status(State.HOVER.value)
                continue

            client.velocity(
                Velocity(command.forward, command.right, up_mps, command.yaw_rate)
            )
            client.status(fsm.state.value)
    except PermissionError as error:
        # The bridge rejected a command: RC override, emergency stop, or a
        # disarmed Virtual Stick. Control already belongs to the RC pilot.
        # Must precede OSError - PermissionError is a subclass of it.
        print(f"Bridge rejected command ({error}); control returned to the RC")
    except (OSError, ConnectionError) as error:
        # Wi-Fi or video link died mid-flight: closing the control socket
        # makes Android release Virtual Stick.
        fsm.transition(State.NETWORK_LOST, time.monotonic())
        print(f"Link lost ({error}): closing the socket returns control to the RC")
    except KeyboardInterrupt:
        print("Operator stop: zeroing and returning control to the RC")
    finally:
        _shutdown(client, capture)


def run_flight_test(
    config: AppConfig,
    arm_token: str,
    mode: str,
    distance_m: float | list[float] = 1.0,
    speed_mps: float = 0.3,
    auto_takeoff: bool = True,
    manual_takeoff_confirmed: bool = False,
    stick_mode: str | None = None,
    direction: str = "forward",
    climb_to_m: float | None = None,
    use_vision: bool = False,
    gimbal_deg: float | None = None,
    search_gimbal_deg: float | None = None,
    until_stopped: bool = False,
    duration_s: float | None = None,
    angle_deg: float = 2.0,
    until_rc: bool = False,
    reverse_on_stall: bool = False,
) -> None:
    """Staged pre-flight tests that use no camera and no tags.

    ``hover``   takeoff, hold altitude, and stay put until the RC pilot takes
                over or the operator stops the program.
    ``forward`` the same, then creep forward a bounded distance and hover.

    Camera calibration is not required because neither mode reads the video
    stream; position never depends on vision here. The arm token, physical RC
    override, and socket-disconnect release all still apply.
    """
    if mode not in {"hover", "forward", "vscheck"}:
        raise ValueError(f"unknown flight test mode {mode!r}")
    if mode == "vscheck":
        _run_vs_check(config, arm_token, stick_mode)
        return
    if not auto_takeoff and not manual_takeoff_confirmed:
        raise RuntimeError(
            "either --takeoff (app-commanded) or --manual-takeoff-confirmed is required"
        )
    if arm_token != config.network.confirmation_token:
        raise PermissionError("CLI arm confirmation token does not match configuration")
    leg_distances = (
        [float(distance_m)] if isinstance(distance_m, (int, float))
        else [float(d) for d in distance_m]
    )
    if not leg_distances:
        raise ValueError("at least one forward leg is required")
    # The aircraft starts at one end of the travel axis, so half the room
    # was never the real limit - stage 3 legitimately crosses 2.46 m of a
    # 3.30 m axis. Keep a margin off the far wall instead.
    travel_budget_m = max(0.5, config.room.length_m - 0.6)
    if sum(leg_distances) > travel_budget_m:
        raise ValueError(
            f"total travel {sum(leg_distances):.2f} m exceeds the safe "
            f"{travel_budget_m:.1f} m budget"
        )
    distance_m = leg_distances[0]
    if distance_m <= 0.0 or distance_m > travel_budget_m:
        raise ValueError(
            f"test distance must be within (0, {config.room.length_m / 2.0:.1f}] m"
        )
    if direction not in {"forward", "back", "left", "right"}:
        raise ValueError(f"unknown direction {direction!r}")
    if duration_s is not None and not (0.0 < duration_s <= 5.0):
        raise ValueError("diagnostic duration must be within (0, 5] seconds")
    if duration_s is not None and until_stopped:
        raise ValueError("--duration and --until-tag cannot be combined")
    # Android defaults to DJI's official Advanced path. Basic and direct
    # action modes must be requested explicitly for diagnostics.
    selected_stick_mode = stick_mode or "advanced"
    expected_advanced = selected_stick_mode != "basic"
    speed_mps = min(abs(speed_mps), config.controller.max_speed_mps)
    if speed_mps <= 0.0:
        raise ValueError("test speed must be positive")
    if selected_stick_mode == "advanced_angle" and not (0.5 <= angle_deg <= 3.0):
        raise ValueError("advanced-angle tilt must be within [0.5, 3.0] degrees")
    if duration_s is not None and until_rc:
        raise ValueError("--duration and --until-rc cannot be combined")
    if until_stopped and until_rc:
        raise ValueError("--until-tag and --until-rc cannot be combined")
    if reverse_on_stall and (mode != "forward" or direction not in {"left", "right"}):
        raise ValueError("--reverse-on-stall requires a left or right forward test")
    if reverse_on_stall and not until_rc:
        raise ValueError("--reverse-on-stall requires --until-rc")

    client = NDJSONClient(
        config.network.host, config.network.port, config.network.protocol_version
    )
    limiter = RateLimiter(config.network.rate_hz)
    flight = config.flight

    # Vision gives the only trustworthy horizontal position indoors, and a
    # far better altitude than the ultrasonic sensor, which read 1.30 m when
    # the aircraft was actually at 1.50 m.
    detector = None
    capture = None
    localizer = None
    if use_vision:
        detector = AprilTagDetector(config)
        capture = TcpVideoStream(
            config.network.host,
            config.network.video_port,
            config.network.video_codec,
        )
        localizer = TagLocalizer(config)

    # Emergency backstop only: distance is tracked strictly from measured
    # speed. A command ACK proves only that the phone received the request; it
    # does not prove that the aircraft moved.
    # Ramp-in and ramp-out make the leg slower than distance/speed, so the
    # cap must leave room or it stops the leg short of the target.
    travel_timeout_s = distance_m / speed_mps * 2.0 + 4.0
    # Long enough for the aircraft to finish its own post-takeoff descent.
    settle_s = 6.0

    print(f"Flight test '{mode}': altitude {flight.target_altitude_m:.2f} m", end="")
    if mode == "forward":
        legs = " + ".join(f"{d:.2f}" for d in leg_distances)
        if until_rc:
            if reverse_on_stall:
                print(
                    f", {direction} until measured stall, then "
                    f"{_opposite_direction(direction)} until physical RC override"
                )
            else:
                print(f", {direction} continuously until physical RC override")
        elif duration_s is None:
            print(f", {direction} {legs} m at {speed_mps:.2f} m/s")
        else:
            if selected_stick_mode == "advanced_angle":
                print(f", {direction} at {angle_deg:.1f} deg for {duration_s:.1f} s")
            else:
                print(f", {direction} at {speed_mps:.2f} m/s for {duration_s:.1f} s")
    else:
        print()
    print("Move any RC stick at any time to take control back.")

    client.connect()
    print(f"Control log: {client.log_path}")
    travelled_m = 0.0
    started_s: float | None = None
    previous_loop_s: float | None = None
    goal_seen = False
    vision_xy = None
    vision_age_s = None
    sonar_offset_m = None
    goal_tag_id = config.goal_tag.id
    leg_index = 0
    leg_pause_started_s = 0.0
    reverse_pause_started_s = 0.0
    active_direction = direction
    reversed_once = False
    directional_motion_seen = False
    stall_started_s: float | None = None
    sweep_index = 0
    sweep_started_s = 0.0
    base = flight.target_altitude_m
    # Stage 1: prove OUR command lifts the aircraft, then hand back.
    # Only upward - the operator brings it down with the RC.
    sweep_targets = [base + SWEEP_DELTA_M]
    phase = "settle"
    try:
        client.zero()
        try:
            client.obstacle_avoidance_off()
            print("Obstacle avoidance: set to CLOSE")
        except (OSError, ValueError, PermissionError) as error:
            print(f"Obstacle avoidance could NOT be disabled: {error}")
        client.stick_mode(selected_stick_mode)
        print(f"Stick interface: {selected_stick_mode}")
        if auto_takeoff:
            _require_takeoff_battery(client, limiter)
            print("Takeoff: commanding DJI auto-takeoff (settles near 0.5 m)")
            client.takeoff(arm_token)
            height = _wait_for_takeoff(client, limiter, flight.takeoff_timeout_s)
            print(f"Takeoff: airborne at {height:.2f} m")
        _arm_when_ready(client, arm_token, limiter)
        if gimbal_deg is not None:
            client.gimbal(gimbal_deg)
            print(f"Gimbal set to {gimbal_deg:+.0f} deg")
        # An "armed" ACK only means the SDK call succeeded. Confirm the flight
        # controller actually handed Virtual Stick the authority, otherwise
        # every later command is silently ignored and the drone just hovers.
        vs = client.last_telemetry
        # enableVirtualStick's completion callback can arrive before DJI's
        # VirtualStickStateListener publishes enabled/authority. The previous
        # code treated that brief stale RC snapshot as a hard rejection and
        # disarmed immediately after a successful arm. Give the authoritative
        # state callback up to two seconds to settle before deciding.
        for _ in range(20):
            if (
                vs is not None
                and vs.vs_enabled is True
                and vs.vs_advanced_enabled == expected_advanced
                and vs.vs_authority == "MSDK"
            ):
                break
            limiter.wait()
            client.status(State.INITIALIZE.value)
            vs = client.last_telemetry
        if vs is not None and (
            vs.vs_enabled is not None or vs.vs_advanced_enabled is not None
        ):
            print(
                f"Virtual Stick: enabled={vs.vs_enabled} "
                f"advanced={vs.vs_advanced_enabled} authority={vs.vs_authority}"
                + (f" flight_mode={vs.flight_mode}" if vs.flight_mode else "")
                + (f" reason={vs.vs_change_reason}" if vs.vs_change_reason else "")
            )
            if vs.vs_enabled is False:
                raise RuntimeError(
                    "flight controller did not grant Virtual Stick control "
                    f"(enabled={vs.vs_enabled}, advanced={vs.vs_advanced_enabled}, "
                    f"authority={vs.vs_authority}, reason={vs.vs_change_reason}); "
                    "refusing to continue - land with the RC"
                )
            if (
                vs.vs_advanced_enabled is not None
                and vs.vs_advanced_enabled != expected_advanced
            ):
                raise RuntimeError(
                    "Virtual Stick command-path mismatch: "
                    f"requested {'ADVANCED' if expected_advanced else 'BASIC'}, "
                    f"DJI reports advanced={vs.vs_advanced_enabled}; "
                    "Android is returning control to the RC"
                )
            if vs.vs_authority is not None and vs.vs_authority != "MSDK":
                # The authority field is change-triggered too, so it can lag a
                # successful arm; give it a moment before believing it.
                for _ in range(10):
                    limiter.wait()
                    # Must send something: telemetry only refreshes on an
                    # ACK, so a read-only wait sees the stale pre-arm
                    # authority forever.
                    client.status(State.INITIALIZE.value)
                    vs = client.last_telemetry
                    if vs is not None and vs.vs_authority == "MSDK":
                        break
                print(f"Virtual Stick: authority settled at {vs.vs_authority}")
                if vs is not None and vs.vs_authority not in (None, "MSDK"):
                    # Without flight-control authority every command is ignored
                    # while the bridge still ACKs it, which looks exactly like a
                    # working flight that refuses to move. Do not take off.
                    raise RuntimeError(
                        f"flight control authority stayed with {vs.vs_authority}, "
                        "not MSDK; refusing to fly - land with the RC"
                    )
        print(f"Armed: descending to {flight.target_altitude_m:.2f} m and settling")
        settle_started_s = time.monotonic()

        while True:
            limiter.wait()
            now = time.monotonic()
            loop_dt = (
                1.0 / config.network.rate_hz if previous_loop_s is None
                else min(0.5, max(0.02, now - previous_loop_s))
            )
            previous_loop_s = now
            # One command per tick refreshes telemetry without adding a
            # separate heartbeat round trip.
            telemetry = client.last_telemetry

            if (
                telemetry is not None
                and telemetry.rc_override_age_s is not None
                and telemetry.rc_override_age_s < 5.0
            ):
                print("RC stick override detected: control stays with the pilot")
                return

            if (
                telemetry is not None
                and telemetry.vs_advanced_enabled is not None
                and telemetry.vs_advanced_enabled != expected_advanced
            ):
                raise RuntimeError(
                    "Virtual Stick command path changed during flight; "
                    "Android is returning control to the RC"
                )

            # Hold the altitude the mission asked for, not the
            # configured takeoff altitude.
            up_mps = _altitude_command(telemetry, flight, target_m=climb_to_m)
            height = None if telemetry is None else telemetry.height_m

            # Vision overrides the sonar: the tag gives a directly measured
            # height and the horizontal position the aircraft cannot supply
            # indoors (its NED velocity is GPS-derived and reads zero here).
            if capture is not None:
                ok, frame, frame_age = capture.read()
                detections = (
                    detector.detect(frame)
                    if ok and frame_age <= FRESH_FRAME_S
                    else []
                )
                seen = sorted({d.tag_id for d in detections})
                if goal_tag_id in seen and not goal_seen:
                    goal_seen = True
                    print(f"\n*** ID {goal_tag_id} DETECTED ***")
                pose = localizer.update(detections) if detections else None
                if pose is not None:
                    px, py, pz = translation(pose)
                    # Tag frame is z-down, so the drone sits at negative z.
                    height = abs(pz)
                    vision_xy = (px, py)
                    vision_age_s = now
                    # Learn how far the sonar under-reads while both are
                    # available, so height survives losing the tag.
                    if telemetry is not None and telemetry.height_m is not None:
                        sonar_offset_m = height - telemetry.height_m
                elif (
                    sonar_offset_m is not None
                    and telemetry is not None
                    and telemetry.height_m is not None
                ):
                    # Tag out of frame - it leaves view after ~0.5 m of drift.
                    # Keep flying on the corrected sonar instead of freezing
                    # at the last tag reading and believing the climb stalled.
                    height = telemetry.height_m + sonar_offset_m
                    if vision_age_s is not None and now - vision_age_s > 1.0:
                        vision_xy = None

            if phase == "settle":
                # No vertical command while settling: the aircraft is
                # still descending from its own takeoff overshoot, and
                # correcting into that makes the later climb test
                # impossible to read.
                _send_test_setpoint(
                    client, selected_stick_mode, Velocity(0.0, 0.0, 0.0),
                    speed_mps, angle_deg,
                )
                if now - settle_started_s >= settle_s:
                    if mode == "hover" or climb_to_m is not None:
                        # Reach the mission altitude before translating,
                        # so a leg never starts mid-climb.
                        phase = "sweep"
                        sweep_index = 0
                        sweep_started_s = now
                        # Step up from where the aircraft actually is:
                        # it settles wherever it settles, and a fixed
                        # target can already be below it.
                        if climb_to_m is not None:
                            # Absolute goal (stage 3 needs 1.80 m at
                            # the wall), not a relative step.
                            sweep_targets = [climb_to_m]
                        elif height is not None:
                            sweep_targets = [height + SWEEP_DELTA_M]
                        print(
                            f"Altitude sweep: {' -> '.join(f'{t:.2f}' for t in sweep_targets)} m"
                        )
                    else:
                        phase = "forward"
                        started_s = now
                        print("Moving forward now")
                continue

            if phase == "sweep":
                # Step the altitude up and down so vertical control is proven
                # in BOTH directions, not just "it happens to sit still".
                goal = sweep_targets[sweep_index]
                up_mps = _altitude_command(telemetry, flight, target_m=goal)
                _send_test_setpoint(
                    client, selected_stick_mode, Velocity(0.0, 0.0, up_mps),
                    speed_mps, angle_deg,
                )
                reached = (
                    height is not None
                    and abs(height - goal) <= SWEEP_TOLERANCE_M
                )
                elapsed = now - sweep_started_s
                if reached or elapsed >= SWEEP_STEP_TIMEOUT_S:
                    print(
                        f"  sweep {sweep_index + 1}/{len(sweep_targets)} "
                        f"target {goal:.2f} m -> height "
                        f"{height if height is not None else float('nan'):.2f} m "
                        f"({'reached' if reached else 'TIMED OUT'} in {elapsed:.1f}s)"
                    )
                    if not reached and climb_to_m is not None and mode == "forward":
                        # The requested sequence is climb first, translate
                        # second.  Proceeding after a timeout previously ran
                        # the lateral leg at 1.2 m despite a 1.8 m request.
                        client.zero()
                        raise RuntimeError(
                            f"required climb to {goal:.2f} m was not reached "
                            f"(measured {height if height is not None else float('nan'):.2f} m); "
                            "horizontal leg cancelled and control returned to the RC"
                        )
                    sweep_index += 1
                    sweep_started_s = now
                    if sweep_index >= len(sweep_targets):
                        if mode == "forward":
                            # Climb finished: start translating.
                            phase = "forward"
                            started_s = now
                            if search_gimbal_deg is not None:
                                # Climb used the floor tag for a true
                                # height; the traverse needs to see the
                                # wall tag instead.
                                try:
                                    client.gimbal(search_gimbal_deg)
                                    print(
                                        f"Gimbal -> {search_gimbal_deg:+.0f} deg "
                                        f"to look for ID {goal_tag_id}"
                                    )
                                except (OSError, ValueError, PermissionError) as e:
                                    print(f"gimbal re-aim failed: {e}")
                            print(
                                f"Climb done. Leg 1/{len(leg_distances)}: "
                                f"moving {active_direction} {distance_m:.2f} m"
                            )
                            continue
                        phase = "hover"
                        print(
                            "Sweep done. Hovering - take control with the RC, "
                            "or press Ctrl+C to return control and stop."
                        )
                else:
                    print(
                        f"  sweep {sweep_index + 1}/{len(sweep_targets)} "
                        f"height {height if height is not None else float('nan'):4.2f} m"
                        f"  target {goal:.2f} m  cmd {up_mps:+.2f} m/s",
                        end="\r",
                    )
                continue

            if phase == "forward":
                lateral_obstacle_m = _directional_obstacle_m(
                    telemetry, active_direction
                )
                if (
                    until_rc
                    and active_direction in {"left", "right"}
                    and lateral_obstacle_m is not None
                    and lateral_obstacle_m <= LATERAL_OBSTACLE_STOP_M
                ):
                    client.zero()
                    print(
                        f"\n{active_direction} obstacle {lateral_obstacle_m:.2f} m "
                        f"<= {LATERAL_OBSTACLE_STOP_M:.2f} m: zeroing and "
                        "returning control to the RC."
                    )
                    return
                # Never claim that the aircraft moved merely because a
                # command was accepted. Indoors the horizontal velocity key
                # is frequently stale/zero; integrating the requested speed
                # made a stationary aircraft appear to traverse the room and
                # stopped the left command after 6-8 seconds.
                speed = _horizontal_speed(telemetry)
                measured = speed is not None
                if measured:
                    travelled_m += speed * loop_dt
                elapsed = now - started_s
                directional_speed = _directional_speed(telemetry, active_direction)
                if directional_speed is not None and directional_speed >= STALL_SPEED_MPS:
                    directional_motion_seen = True
                    stall_started_s = None
                elif (
                    reverse_on_stall
                    and not reversed_once
                    and directional_motion_seen
                    and directional_speed is not None
                ):
                    if stall_started_s is None:
                        stall_started_s = now
                    elif now - stall_started_s >= STALL_CONFIRM_S:
                        _send_test_setpoint(
                            client,
                            selected_stick_mode,
                            Velocity(0.0, 0.0, up_mps),
                            speed_mps,
                            angle_deg,
                        )
                        phase = "reverse_pause"
                        reverse_pause_started_s = now
                        nearest = _nearest_obstacle_m(telemetry)
                        nearest_text = (
                            "unavailable" if nearest is None else f"{nearest:.2f} m"
                        )
                        print(
                            f"\nMeasured {active_direction} stall after ~{travelled_m:.2f} m; "
                            f"nearest raw obstacle={nearest_text}. Zeroing before reversal."
                        )
                        continue
                done = _diagnostic_leg_done(
                    until_stopped=until_stopped,
                    until_rc=until_rc,
                    goal_seen=goal_seen,
                    duration_s=duration_s,
                    elapsed_s=elapsed,
                    travelled_m=travelled_m,
                    distance_m=distance_m,
                    timeout_s=travel_timeout_s,
                )
                if done:
                    if until_stopped:
                        reason = "goal tag seen"
                    elif duration_s is not None:
                        reason = "diagnostic duration reached"
                    else:
                        reason = ("distance reached"
                                  if travelled_m >= distance_m
                                  else "time limit reached")
                    print(
                        f"Leg {leg_index + 1}/{len(leg_distances)} complete "
                        f"({reason}, ~{travelled_m:.2f} m)."
                    )
                    _send_test_setpoint(
                        client, selected_stick_mode, Velocity(0.0, 0.0, up_mps),
                        speed_mps, angle_deg,
                    )
                    leg_index += 1
                    if leg_index < len(leg_distances):
                        # Settle between legs so each starts from rest and its
                        # distance is measured independently.
                        phase = "leg_pause"
                        leg_pause_started_s = now
                        print(f"Pausing {LEG_PAUSE_S:.0f}s before the next leg")
                    else:
                        if duration_s is not None:
                            # A bounded diagnostic is complete. Do not keep a
                            # hidden control loop armed afterward; zero and let
                            # finally return authority to the RC immediately.
                            client.zero()
                            print("Timed diagnostic complete; returning control to the RC.")
                            return
                        phase = "hover"
                        print("All legs done. Hovering; take control with the RC.")
                    continue
                # Ease in and out so the aircraft never steps to full speed.
                if until_stopped or until_rc:
                    ramp = min(1.0, elapsed / 1.5)
                elif duration_s is not None:
                    # Timed axis diagnostics must not depend on indoor NED
                    # velocity. Ease in/out by time and stop exactly on time.
                    ramp = min(
                        1.0,
                        elapsed / 0.5,
                        max(0.0, duration_s - elapsed) / 0.3,
                    )
                else:
                    ramp = min(
                        1.0, elapsed / 1.5,
                        max(0.0, distance_m - travelled_m) / 0.3,
                    )
                command = speed_mps * max(0.15, ramp)
                # forward drives the nose axis; left/right drive the
                # lateral axis, with the aircraft still facing the wall.
                # A continuous lateral diagnostic is deliberately two-axis:
                # do not let noisy/quantised downward-range telemetry inject
                # climb or descent commands while the operator is testing
                # only left/right response.  The aircraft's own vertical
                # hold remains active with a zero vertical setpoint.
                lateral_only = until_rc and active_direction in {"left", "right"}
                horizontal_up_mps = 0.0 if lateral_only else up_mps
                step = _diagnostic_velocity(
                    active_direction, command, horizontal_up_mps
                )
                _send_test_setpoint(
                    client, selected_stick_mode, step, speed_mps, angle_deg
                )
                oa = ""
                if telemetry is not None and telemetry.oa_type is not None:
                    oa = f"  OA={telemetry.oa_type}"
                    if telemetry.oa_sensors_working:
                        oa += f" sensors={telemetry.oa_sensors_working}"
                    nearest = _nearest_obstacle_m(telemetry)
                    if nearest is not None:
                        oa += f" nearest={nearest:.2f}m"
                    if lateral_obstacle_m is not None:
                        oa += f" {active_direction}={lateral_obstacle_m:.2f}m"
                print(
                    f"  {active_direction} {travelled_m:4.2f}/{distance_m:.2f} m  "
                    f"height {height if height is not None else float('nan'):4.2f} m  "
                    + (
                        f"cmd {angle_deg * command / speed_mps:3.1f} deg  "
                        if selected_stick_mode == "advanced_angle"
                        else f"cmd {command:4.2f} m/s  "
                    )
                    + f"[{'measured' if measured else 'unmeasured'}]{oa}"
                    + f"{_format_direct_frames(telemetry)}",
                    end="\r",
                )
                continue

            if phase == "reverse_pause":
                _send_test_setpoint(
                    client,
                    selected_stick_mode,
                    Velocity(0.0, 0.0, up_mps),
                    speed_mps,
                    angle_deg,
                )
                if now - reverse_pause_started_s >= REVERSE_PAUSE_S:
                    active_direction = _opposite_direction(active_direction)
                    reversed_once = True
                    directional_motion_seen = False
                    stall_started_s = None
                    travelled_m = 0.0
                    started_s = now
                    phase = "forward"
                    print(
                        f"Reversing: moving {active_direction} until physical RC override"
                    )
                continue

            if phase == "leg_pause":
                _send_test_setpoint(
                    client, selected_stick_mode, Velocity(0.0, 0.0, up_mps),
                    speed_mps, angle_deg,
                )
                if now - leg_pause_started_s >= LEG_PAUSE_S:
                    distance_m = leg_distances[leg_index]
                    travel_timeout_s = distance_m / speed_mps * 2.0 + 4.0
                    travelled_m = 0.0
                    started_s = now
                    phase = "forward"
                    print(
                        f"Leg {leg_index + 1}/{len(leg_distances)}: "
                        f"moving forward {distance_m:.2f} m"
                    )
                continue

            _send_test_setpoint(
                client, selected_stick_mode, Velocity(0.0, 0.0, up_mps),
                speed_mps, angle_deg,
            )
            hover_target_m = (
                climb_to_m if climb_to_m is not None else flight.target_altitude_m
            )
            print(
                f"  hover  height {height if height is not None else float('nan'):4.2f} m"
                f"  target {hover_target_m:.2f} m  cmd {up_mps:+.2f} m/s"
                f"  age {telemetry.height_age_s if telemetry else None}s"
                f"  authority={telemetry.vs_authority if telemetry else '?'}",
                f"{_format_direct_frames(telemetry)}",
                end="\r",
            )
    # PermissionError subclasses OSError, so it must be caught first or a
    # rejected command gets misreported as a lost link.
    except PermissionError as error:
        print(f"Bridge rejected command ({error}); control returned to the RC")
    except (OSError, ConnectionError) as error:
        print(f"Link lost ({error}): socket disconnect returns control to the RC")
    except KeyboardInterrupt:
        print("\nOperator stop: zeroing and returning control to the RC")
    finally:
        _test_shutdown(client, capture)


def _arm_when_ready(
    client: NDJSONClient,
    arm_token: str,
    limiter: RateLimiter,
    timeout_s: float = 25.0,
) -> None:
    """Arm once DJI will actually hand over control.

    Auto-takeoff keeps flight authority until its climb finishes and rejects
    enableVirtualStick with CONTROL_AUTH_TAKING_OFF until then. There is no
    reliable "takeoff finished" signal - the height telemetry is
    change-triggered - so ask the aircraft directly and retry while it is
    still busy.
    """
    deadline = time.monotonic() + timeout_s
    attempt = 0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        attempt += 1
        try:
            client.arm(arm_token)
            if attempt > 1:
                print(f"Armed after {attempt} attempts (takeoff had to finish)")
            return
        except PermissionError as error:
            if "CONTROL_AUTH_TAKING_OFF" not in str(error):
                raise
            last_error = error
            if attempt == 1:
                print("Takeoff still in progress; waiting to take control...")
            # Keep requesting fresh telemetry while takeoff owns authority.
            for _ in range(10):
                limiter.wait()
    raise RuntimeError(
        f"aircraft never released control after takeoff: {last_error}"
    )


def _disable_obstacle_avoidance(host: str, query_port: int = 9997) -> None:
    """Turn off DJI vision avoidance before flying.

    Obstacle avoidance was what actually blocked Virtual Stick motion in this
    room: with it on, the aircraft accepted every command and refused to move.
    The operator flies with the RC in hand and takes over at will, so the
    avoidance is traded away deliberately - it is re-enabled by DJI Fly or by
    setting these keys back to true.
    """
    import socket as _socket
    try:
        sock = _socket.create_connection((host, query_port), timeout=4.0)
    except OSError as error:
        print(f"Could not reach the query server to disable avoidance: {error}")
        return
    try:
        sock.settimeout(4.0)
        for key in ("VisionAvoidEnable", "UserAvoidEnable"):
            sock.sendall((f"SET FlightController {key} false" + chr(10)).encode())
            time.sleep(1.0)
        sock.settimeout(2.0)
        reply = b""
        try:
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                reply += chunk
        except OSError:
            pass
        print("Obstacle avoidance: requested off ->",
              " | ".join(l.strip()[:60] for l in
                         reply.decode(errors="replace").splitlines() if l.strip()))
    finally:
        sock.close()


def _require_takeoff_battery(client: NDJSONClient, limiter: RateLimiter) -> None:
    """Refuse takeoff on a low battery before DJI does, with a clear reason.

    DJI rejects auto-takeoff below its own safety threshold and reports only
    an opaque ``StartTakeoff:-7``, which is impossible to act on.
    """
    telemetry = None
    for _ in range(5):
        limiter.wait()
        # Telemetry only refreshes on an ACK, so the wait has to
        # send something or it reads stale values forever.
        client.status(State.WAIT_FOR_TAKEOFF.value)
        telemetry = client.last_telemetry
        if telemetry is not None and telemetry.battery_percent is not None:
            break
    if telemetry is None or telemetry.battery_percent is None:
        print("Battery level unknown; continuing (DJI still enforces its own limit)")
        return
    print(f"Battery: {telemetry.battery_percent:.0f}%")
    if telemetry.battery_percent < MIN_TAKEOFF_BATTERY_PERCENT:
        raise RuntimeError(
            f"battery is {telemetry.battery_percent:.0f}%, below the "
            f"{MIN_TAKEOFF_BATTERY_PERCENT}% needed for takeoff; charge or swap "
            "the battery"
        )


def _run_vs_check(
    config: AppConfig, arm_token: str, stick_mode: str | None = None
) -> None:
    """Ground-only check: does the flight controller actually hand Virtual
    Stick the control authority? Arms, reads DJI's own state, disarms.

    Never takes off and never sends a nonzero velocity, so it is safe to run
    with the drone sitting on the floor.
    """
    if arm_token != config.network.confirmation_token:
        raise PermissionError("CLI arm confirmation token does not match configuration")
    client = NDJSONClient(
        config.network.host, config.network.port, config.network.protocol_version
    )
    limiter = RateLimiter(config.network.rate_hz)
    selected_stick_mode = stick_mode or "advanced"
    expected_advanced = selected_stick_mode != "basic"
    print("Virtual Stick ground check - the drone must stay on the floor.")
    client.connect()
    print(f"  control log: {client.log_path}")
    try:
        client.stick_mode(selected_stick_mode)
        print(f"  stick interface: {selected_stick_mode}")
        before = _read_vs_state(client, limiter, 5)
        print(f"  before arm: {_format_vs(before)}")
        if before is not None and before.is_flying:
            raise RuntimeError("drone reports flying; land it before this check")
        print("  arming (no motion commands are sent)...")
        client.arm(arm_token)
        after = _read_vs_state(client, limiter, 15)
        print(f"  after arm : {_format_vs(after)}")
        print()
        if after is None or (
            after.vs_enabled is None and after.vs_advanced_enabled is None
        ):
            print("RESULT: the app got no Virtual Stick state from DJI at all.")
        elif (
            after.vs_enabled
            and after.vs_advanced_enabled is not None
            and after.vs_advanced_enabled == expected_advanced
        ):
            if selected_stick_mode == "advanced_direct":
                selected = "ADVANCED direct-action diagnostic"
            elif expected_advanced:
                selected = "official ADVANCED"
            else:
                selected = "BASIC diagnostic"
            print(f"RESULT: Virtual Stick is active on the requested {selected} path.")
            print("        Authority:", after.vs_authority)
            print("        Direct frames:", _format_direct_frames(after).strip() or "n/a")
        elif after.vs_enabled:
            print("RESULT: Virtual Stick is on, but the selected path does not match.")
            print("        requested advanced:", expected_advanced)
            print("        reported advanced :", after.vs_advanced_enabled)
        else:
            print("RESULT: the flight controller did NOT grant Virtual Stick.")
            print("        authority:", after.vs_authority)
            print("        reason   :", after.vs_change_reason)
    finally:
        try:
            client.disarm()
        except (OSError, ConnectionError, PermissionError, ValueError):
            pass
        try:
            client.close()
        except OSError:
            pass
        print("\nDisarmed; control belongs to the RC.")


def _read_vs_state(client: NDJSONClient, limiter: RateLimiter, ticks: int):
    """Poll status so ACK telemetry stays current."""
    telemetry = None
    for _ in range(ticks):
        limiter.wait()
        # Telemetry only refreshes on an ACK, so the wait has to
        # send something or it reads stale values forever.
        client.status(State.WAIT_FOR_TAKEOFF.value)
        telemetry = client.last_telemetry
    return telemetry


def _format_vs(telemetry) -> str:
    if telemetry is None:
        return "no telemetry"
    return (
        f"vs_enabled={telemetry.vs_enabled} "
        f"advanced={telemetry.vs_advanced_enabled} "
        f"authority={telemetry.vs_authority} "
        f"mode={telemetry.stick_mode} flight_mode={telemetry.flight_mode} "
        f"speed_level={telemetry.speed_level} "
        f"reason={telemetry.vs_change_reason} "
        f"armed={telemetry.armed} is_flying={telemetry.is_flying}"
        f"{_format_direct_frames(telemetry)}"
    )


def _format_direct_frames(telemetry) -> str:
    if telemetry is None:
        return ""
    if telemetry.stick_mode in {"OFFICIAL_ADVANCED", "OFFICIAL_ADVANCED_ANGLE"}:
        sent = int(telemetry.official_advanced_frames_sent or 0)
        age = telemetry.official_advanced_frame_age_s
        age_text = "?" if age is None else f"{age:.3f}s"
        return f" official_advanced={sent} last={age_text}"
    if telemetry.stick_mode != "ADVANCED_DIRECT":
        return ""
    sent = int(telemetry.direct_frames_sent or 0)
    succeeded = int(telemetry.direct_frames_succeeded or 0)
    failed = int(telemetry.direct_frames_failed or 0)
    consecutive = int(telemetry.direct_consecutive_failures or 0)
    suffix = (
        f" direct={sent}/{succeeded}/{failed}"
        f" consecutive_fail={consecutive}"
    )
    if telemetry.direct_last_error:
        suffix += f" error={telemetry.direct_last_error}"
    return suffix


def _diagnostic_leg_done(
    *,
    until_stopped: bool,
    until_rc: bool,
    goal_seen: bool,
    duration_s: float | None,
    elapsed_s: float,
    travelled_m: float,
    distance_m: float,
    timeout_s: float,
) -> bool:
    """Choose exactly one bounded stopping rule for a diagnostic leg."""
    if until_rc:
        return False
    if until_stopped:
        return goal_seen
    if duration_s is not None:
        return elapsed_s >= duration_s
    return travelled_m >= distance_m or elapsed_s >= timeout_s


def _opposite_direction(direction: str) -> str:
    opposites = {"left": "right", "right": "left", "forward": "back", "back": "forward"}
    try:
        return opposites[direction]
    except KeyError as error:
        raise ValueError(f"unknown diagnostic direction {direction!r}") from error


def _directional_speed(
    telemetry: Telemetry | None, direction: str
) -> float | None:
    """Fresh signed velocity along one body direction."""
    if (
        telemetry is None
        or telemetry.velocity_north_mps is None
        or telemetry.velocity_east_mps is None
        or telemetry.velocity_age_s is None
        or telemetry.velocity_age_s > 0.5
        or telemetry.yaw_deg is None
        or telemetry.attitude_age_s is None
        or telemetry.attitude_age_s > 0.5
    ):
        return None
    yaw_rad = math.radians(telemetry.yaw_deg)
    north = telemetry.velocity_north_mps
    east = telemetry.velocity_east_mps
    body_forward = north * math.cos(yaw_rad) + east * math.sin(yaw_rad)
    body_right = -north * math.sin(yaw_rad) + east * math.cos(yaw_rad)
    if direction == "forward":
        return body_forward
    if direction == "back":
        return -body_forward
    if direction == "right":
        return body_right
    if direction == "left":
        return -body_right
    raise ValueError(f"unknown diagnostic direction {direction!r}")


def _nearest_obstacle_m(telemetry: Telemetry | None) -> float | None:
    """Compatibility policy: finite analysis-domain range, callback age <= 1 s."""
    observation = observe_sector(telemetry, "all")
    return observation.range_m if observation.callback_recency == "RECENT_CHANGE" else None


def _directional_obstacle_m(
    telemetry: Telemetry | None, direction: str
) -> float | None:
    """Compatibility wrapper retaining the existing one-second callback-age policy."""
    if direction not in {"left", "right"}:
        return None
    observation = observe_sector(telemetry, direction, LATERAL_OBSTACLE_HALF_WIDTH_DEG)
    return observation.range_m if observation.callback_recency == "RECENT_CHANGE" else None


def _diagnostic_velocity(direction: str, command_mps: float, up_mps: float) -> Velocity:
    """Map a named body direction to the semantic PC velocity payload."""
    if direction == "forward":
        return Velocity(command_mps, 0.0, up_mps)
    if direction == "back":
        return Velocity(-command_mps, 0.0, up_mps)
    if direction == "right":
        return Velocity(0.0, command_mps, up_mps)
    if direction == "left":
        return Velocity(0.0, -command_mps, up_mps)
    raise ValueError(f"unknown diagnostic direction {direction!r}")


def _send_test_setpoint(
    client: NDJSONClient,
    stick_mode: str,
    velocity: Velocity,
    full_speed_mps: float,
    angle_deg: float,
) -> None:
    """Send one test setpoint without conflating ANGLE with VELOCITY.

    The existing test ramp is expressed as a fraction of ``full_speed_mps``.
    In ANGLE mode that fraction scales the explicitly logged body tilt; the
    vertical and yaw axes remain velocity/angular-velocity as DJI documents.
    """
    if stick_mode != "advanced_angle":
        client.velocity(velocity)
        return
    scale = angle_deg / full_speed_mps
    client.attitude(
        forward_tilt_deg=velocity.forward * scale,
        right_tilt_deg=velocity.right * scale,
        up_mps=velocity.up,
        yaw_rate_rps=velocity.yaw_rate,
    )


def _horizontal_speed(telemetry) -> float | None:
    """Ground speed magnitude; frame-convention independent."""
    if (
        telemetry is None
        or telemetry.velocity_north_mps is None
        or telemetry.velocity_east_mps is None
        or telemetry.velocity_age_s is None
        or telemetry.velocity_age_s > 0.5
    ):
        return None
    return math.hypot(telemetry.velocity_north_mps, telemetry.velocity_east_mps)


def _test_shutdown(client: NDJSONClient, capture=None) -> None:
    """Best-effort release; each step runs even if the previous one failed."""
    suppressed = (OSError, ConnectionError, PermissionError, ValueError)
    interrupted = False
    steps = [client.zero, client.disarm, client.close]
    if capture is not None:
        steps.append(capture.close)
    for step in steps:
        try:
            step()
        except suppressed:
            pass
        except KeyboardInterrupt:
            interrupted = True
    print("Control released. The drone holds position under RC control.")
    if interrupted:
        raise KeyboardInterrupt


def _wait_for_takeoff(
    client: NDJSONClient, limiter: RateLimiter, timeout_s: float
) -> float:
    """Wait for is_flying + a clearly-airborne height. Returns that height."""
    deadline = time.monotonic() + timeout_s
    last_seen = "no telemetry"
    while time.monotonic() < deadline:
        limiter.wait()
        # Something must be sent each tick or no ACK comes back and the
        # telemetry never refreshes: the wait then times out on a stale
        # is_flying while the aircraft is happily airborne.
        client.status(State.WAIT_FOR_TAKEOFF.value)
        telemetry = client.last_telemetry
        if telemetry is not None:
            last_seen = (
                f"is_flying={telemetry.is_flying} height={telemetry.height_m} m "
                f"(age {telemetry.height_age_s} s)"
            )
        if (
            telemetry is not None
            and telemetry.is_flying is True
            and telemetry.height_m is not None
            and telemetry.height_age_s is not None
            and telemetry.height_age_s <= TAKEOFF_HEIGHT_STALE_S
            and telemetry.height_m >= TAKEOFF_HOVER_MIN_M
        ):
            return telemetry.height_m
    # Report what the aircraft actually said; the previous message gave no clue
    # that the real hover height was simply below the gate.
    raise RuntimeError(
        "takeoff did not reach a stable hover in time "
        f"(needed is_flying and height >= {TAKEOFF_HOVER_MIN_M} m; "
        f"last telemetry: {last_seen}); land with the RC"
    )


def _altitude_command(telemetry, flight, target_m: float | None = None) -> float:
    """Proportional climb/descend toward the target altitude.

    Deliberately does NOT reject an old height. Height telemetry is
    change-triggered and decimetre-quantised, so a drone holding altitude
    stops reporting entirely and the reading goes "stale" precisely when it
    is most accurate - the aircraft has not moved. Gating on age deadlocked
    the loop: it needed a fresh height to command a climb and a climb to
    produce a fresh height, so it sat at 0.5 m sending +0.00 m/s forever.

    Telemetry only arrives on a live ACK, a dead link raises on the next
    request, closing the socket releases Virtual Stick, and the physical RC
    override remains active. The commanded rate is also clamped, and as soon
    as the aircraft moves the height starts updating again.
    """
    if telemetry is None or telemetry.height_m is None:
        return 0.0
    goal = flight.target_altitude_m if target_m is None else target_m
    return _clamp(
        flight.altitude_gain * (goal - telemetry.height_m),
        flight.max_vertical_speed_mps,
    )


def _shutdown(client: NDJSONClient, capture: TcpVideoStream) -> None:
    """Best-effort release: each step runs even if the previous one failed.

    A second Ctrl+C while a step blocks must not skip disarm; the interrupt
    is deferred until every step has been attempted.
    """
    suppressed = (OSError, ConnectionError, PermissionError, ValueError)
    interrupted = False
    for step in (client.zero, client.disarm, client.close, capture.close):
        try:
            step()
        except suppressed:
            pass
        except KeyboardInterrupt:
            interrupted = True
    if interrupted:
        raise KeyboardInterrupt


def run_pose_check(config: AppConfig) -> None:
    """Ground-truth helper: print live tag detections, the derived world pose,
    and the NED-telemetry velocity mapped into the world frame while the drone
    is moved by hand. Sends only status keepalives, never motion commands."""
    detector = AprilTagDetector(config)
    capture = TcpVideoStream(
        config.network.host,
        config.network.video_port,
        config.network.video_codec,
    )
    localizer = TagLocalizer(config)
    estimator = PoseEstimator(config.dead_reckoning)
    client: NDJSONClient | None = NDJSONClient(
        config.network.host, config.network.port, config.network.protocol_version
    )
    try:
        client.connect()
        print(f"Control log: {client.log_path}")
        print("Control channel connected: telemetry check enabled")
    except (OSError, ConnectionError):
        client = None
        print("Control channel unavailable: pose-only check")
    print("Pose check: move the drone by hand over a tag (Ctrl+C to stop)")
    print("Carry it toward ID2 (+X): world vx must print POSITIVE.")
    printed_size = False
    try:
        while True:
            time.sleep(0.5)
            telemetry = None
            if client is not None:
                try:
                    client.status("IDLE")
                    telemetry = client.last_telemetry
                except (OSError, ConnectionError, PermissionError, ValueError):
                    client = None
                    print("control channel lost: continuing pose-only")
            ok, frame, frame_age = capture.read()
            if not ok or frame_age > FRESH_FRAME_S:
                print("no fresh frame")
                continue
            if not printed_size:
                printed_size = True
                height, width = frame.shape[:2]
                print(
                    f"stream {width}x{height}: set camera.cx={width / 2:.1f} "
                    f"cy={height / 2:.1f} in the config if they differ"
                )
            detections = detector.detect(frame)
            if not detections:
                print("no tags visible")
                continue
            pose = localizer.update(detections)
            ids = sorted(d.tag_id for d in detections)
            if pose is None:
                print(f"tags {ids}: no pose")
                continue
            x, y, z = translation(pose)
            line = (
                f"tags {ids}: world x={x:+.3f} m  y={y:+.3f} m  z={z:+.3f} m  "
                f"yaw={yaw(pose):+.3f} rad"
            )
            estimator.update_fix(x, y, yaw(pose), telemetry)
            world_velocity = estimator.world_velocity_from_telemetry(telemetry)
            if world_velocity is not None:
                line += (
                    f"  |  NED->world vx={world_velocity[0]:+.2f} "
                    f"vy={world_velocity[1]:+.2f} m/s"
                )
            print(line)
    except KeyboardInterrupt:
        pass
    finally:
        if client is not None:
            try:
                client.close()
            except OSError:
                pass
        capture.close()
