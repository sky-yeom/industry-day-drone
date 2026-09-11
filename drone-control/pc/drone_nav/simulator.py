"""Deterministic kinematic end-to-end simulator.

Unlike the original version, tag visibility is modelled honestly from the
downward camera footprint, so the run includes a genuine tag-blind stretch
in the middle that must be crossed by dead reckoning.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .config import AppConfig
from .controller import GoalVisualAligner, LineFollower, Velocity
from .estimator import PoseEstimator
from .localization import TagDetection, TagLocalizer
from .protocol import Telemetry
from .safety import SafetyFSM, State
from .transforms import from_xyz_rpy, inverse_rigid, matmul, translation, yaw

# Half-angle used for the usable downward-camera footprint. Slightly tighter
# than the physical FOV so a tag counts as visible only when fully in frame.
VISIBILITY_HALF_ANGLE_RAD = math.radians(35.0)

# Fixed (arbitrary) angle of NED north measured in the world frame, used to
# exercise the NED->world velocity conversion end to end.
SIM_NED_OFFSET_RAD = 0.7


@dataclass(frozen=True)
class SimulationResult:
    reached_goal: bool
    final_state: State
    final_xy: tuple[float, float]
    elapsed_s: float
    steps: int
    states_visited: tuple[State, ...]
    maximum_command_mps: float
    max_blind_time_s: float


def _telemetry(world_vx: float, world_vy: float, body_yaw: float) -> Telemetry:
    """True velocity re-expressed in the NED frame the aircraft reports.

    The real tag pipeline yields a z-down world whose yaw shares NED's
    handedness, so the physical relation is theta_ned = theta_w + offset.
    """
    magnitude = math.hypot(world_vx, world_vy)
    theta_ned = math.atan2(world_vy, world_vx) + SIM_NED_OFFSET_RAD
    ned_yaw_rad = body_yaw + SIM_NED_OFFSET_RAD
    return Telemetry(
        velocity_north_mps=magnitude * math.cos(theta_ned),
        velocity_east_mps=magnitude * math.sin(theta_ned),
        velocity_down_mps=0.0,
        velocity_age_s=0.05,
        yaw_deg=math.degrees(ned_yaw_rad),
        attitude_age_s=0.05,
        height_m=0.8,
        height_age_s=0.05,
        is_flying=True,
        armed=True,
    )


def run_simulation(config: AppConfig, max_steps: int = 6000) -> SimulationResult:
    dt = 1.0 / config.network.rate_hz
    start, goal = config.start_tag.world_pose, config.goal_tag.world_pose
    follower = LineFollower(
        (start.x_m, start.y_m), (goal.x_m, goal.y_m), config.controller
    )
    T_B_C = config.body_camera.matrix()
    aligner = GoalVisualAligner(
        config.controller, (config.camera.cx, config.camera.cy), T_B_C
    )
    localizer, fsm = TagLocalizer(config), SafetyFSM(config.safety)
    estimator = PoseEstimator(config.dead_reckoning)
    # Nonzero heading: a mirrored NED convention would cancel out at yaw 0
    # and pass undetected; at yaw 0.3 it visibly derails the run.
    x, y, z, body_yaw = start.x_m, start.y_m, 0.8, 0.3
    visible_radius = z * math.tan(VISIBILITY_HALF_ANGLE_RAD)
    states: list[State] = []
    maximum = now = 0.0
    max_blind = 0.0
    world_vx = world_vy = 0.0
    command = Velocity(0.0, 0.0)

    def enter(state: State) -> None:
        fsm.transition(state, now)
        if not states or states[-1] is not state:
            states.append(state)

    def result(reached: bool, step: int) -> SimulationResult:
        return SimulationResult(
            reached, fsm.state, (x, y), now, step, tuple(states), maximum, max_blind
        )

    for state in (
        State.WAIT_FOR_TAKEOFF, State.INITIALIZE, State.GIMBAL_DOWN,
        State.LOCALIZE_START, State.FOLLOW_LINE,
    ):
        enter(state)

    for step in range(1, max_steps + 1):
        now = step * dt
        T_W_B = from_xyz_rpy(x, y, z, 0.0, 0.0, body_yaw)
        detections: list[TagDetection] = []
        for tag in config.tags:
            if tag.world_pose is None:
                continue
            if (
                math.hypot(tag.world_pose.x_m - x, tag.world_pose.y_m - y)
                <= visible_radius
            ):
                T_C_T = matmul(
                    matmul(inverse_rigid(T_B_C), inverse_rigid(T_W_B)),
                    tag.world_pose.matrix(),
                )
                detections.append(TagDetection(tag.id, T_C_T, 0.01 + tag.id * 0.001))
        telemetry = _telemetry(world_vx, world_vy, body_yaw)
        if detections:
            fsm.observe_tag(now)
            if estimator.blind_time_s > 1.0:
                localizer.reset()
        estimate = localizer.update(detections) if detections else None
        fsm.observe_network(now)

        if estimate is not None:
            ex, ey, _ = translation(estimate)
            estimator.update_fix(ex, ey, yaw(estimate), telemetry)
            estimate_ok = True
        else:
            estimate_ok = estimator.update_blind(dt, telemetry, command)
            max_blind = max(max_blind, estimator.blind_time_s)

        if not estimate_ok:
            scale = fsm.tag_scale(now)
            if scale <= 0.0:
                enter(State.TAG_LOST)
                return result(False, step)
            command = command.scaled(scale)
        elif fsm.state in {State.FOLLOW_LINE, State.TAG_LOST}:
            if fsm.state is State.TAG_LOST:
                enter(State.FOLLOW_LINE)
            est = estimator.state
            command = follower.command((est.x_m, est.y_m), est.yaw_rad)
            if follower.arrived((est.x_m, est.y_m)):
                enter(State.GOAL_APPROACH)
        if fsm.state in {State.GOAL_APPROACH, State.VISUAL_ALIGN} and estimate_ok:
            enter(State.VISUAL_ALIGN)
            dx, dy = goal.x_m - x, goal.y_m - y
            camera_x = T_B_C[0][0] * dx + T_B_C[1][0] * dy
            camera_y = T_B_C[0][1] * dx + T_B_C[1][1] * dy
            center = (
                config.camera.cx + camera_x * 100.0,
                config.camera.cy + camera_y * 100.0,
            )
            command = aligner.command(center)
            if command.is_zero:
                enter(State.HOVER)
                return result(True, step)
        maximum = max(maximum, command.speed)
        c, s = math.cos(body_yaw), math.sin(body_yaw)
        world_vx = c * command.forward - s * command.right
        world_vy = s * command.forward + c * command.right
        x += world_vx * dt
        y += world_vy * dt
    enter(State.EMERGENCY_STOP)
    return result(False, max_steps)
