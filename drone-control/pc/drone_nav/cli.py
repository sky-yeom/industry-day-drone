"""Command-line entry point."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .config import load_config
from .patrol import run_tag_patrol
from .runtime import run_flight_test, run_hardware, run_pose_check
from .simulator import run_simulation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AprilTag-guided Mini 4 Pro indoor PC controller"
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parents[1] / "config.sample.json"),
    )
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate configuration and run deterministic simulation without networking",
    )
    parser.add_argument(
        "--pose-check",
        action="store_true",
        help="print live world pose from the video stream; sends no commands",
    )
    parser.add_argument("--arm-token")
    parser.add_argument(
        "--takeoff",
        action="store_true",
        help="command DJI auto-takeoff before navigating (token-gated)",
    )
    parser.add_argument(
        "--land-after-hover",
        action="store_true",
        help="auto-land once the final floor ID0 alignment is confirmed "
        "(default: keep hovering)",
    )
    parser.add_argument(
        "--tag-patrol",
        action="store_true",
        help="run the configured GPS-free wall-tag patrol: floor ID0, "
        "outbound wall route, reverse route, then floor ID0 alignment",
    )
    parser.add_argument(
        "--resume-wall-id",
        type=int,
        choices=(1, 2, 3),
        help="resume an already-airborne outbound patrol at the currently visible wall ID",
    )
    parser.add_argument("--manual-takeoff-confirmed", action="store_true")
    parser.add_argument(
        "--test",
        choices=("vscheck", "hover", "forward"),
        help="staged pre-flight test without camera or tags: 'vscheck' stays "
        "on the ground and reports whether DJI grants Virtual Stick control; "
        "'hover' holds altitude until the RC pilot takes over; 'forward' "
        "additionally creeps forward --distance metres and hovers",
    )
    parser.add_argument(
        "--stick-mode",
        choices=("advanced", "advanced_angle", "basic", "advanced_direct"),
        help="select the official DJI ADVANCED path (default), or an explicit "
        "BODY+ANGLE, BASIC, or direct-action diagnostic path",
    )
    parser.add_argument(
        "--distance",
        default="1.0",
        help="forward distance in metres. Comma-separate for several legs "
        "with a pause between them, e.g. 0.7,0.3 (default: 1.0)",
    )
    parser.add_argument(
        "--direction",
        choices=("forward", "back", "left", "right"),
        default="forward",
        help="body axis the legs move along; left/right fly sideways",
    )
    parser.add_argument(
        "--vision",
        action="store_true",
        help="use the camera during the test: AprilTag altitude and position, and report when the goal tag is seen",
    )
    parser.add_argument(
        "--gimbal",
        type=float,
        help="gimbal pitch in degrees after arming: -90 down, 0 forward, -45 sees floor 0.7-3.3 m out",
    )
    parser.add_argument(
        "--search-gimbal",
        type=float,
        help="after the legs finish, re-aim the gimbal to this pitch and keep looking for the goal tag, e.g. 0 for a wall tag",
    )
    parser.add_argument(
        "--until-tag",
        action="store_true",
        help="keep flying the leg until the goal tag is detected, the operator takes over, or the room budget is spent (ignores --distance)",
    )
    parser.add_argument(
        "--until-rc",
        action="store_true",
        help="keep sending the selected direction until a physical RC stick "
        "takes control or the operator stops the PC process",
    )
    parser.add_argument(
        "--reverse-on-stall",
        action="store_true",
        help="for a left/right --until-rc test, detect a sustained measured "
        "stop after motion, zero briefly, then command the opposite direction",
    )
    parser.add_argument(
        "--climb-to",
        type=float,
        help="absolute altitude in metres before the legs, e.g. 1.8",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=0.3,
        help="forward test speed in m/s, capped by controller.max_speed_mps "
        "(default: 0.3)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        help="diagnostic leg duration in seconds (maximum 5); when set, stop "
        "and zero by time instead of inferred distance",
    )
    parser.add_argument(
        "--angle-deg",
        type=float,
        default=2.0,
        help="maximum body tilt for --stick-mode advanced_angle, within "
        "0.5..5.0 degrees (default: 2.0)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config(args.config)
    if args.simulate or args.dry_run:
        result = run_simulation(config)
        document = asdict(result)
        document["final_state"] = result.final_state.value
        document["states_visited"] = [state.value for state in result.states_visited]
        print(json.dumps(document, sort_keys=True))
        return 0 if result.reached_goal else 2
    if args.pose_check:
        run_pose_check(config)
        return 0
    if not args.arm_token:
        raise SystemExit("--arm-token is required before hardware can issue nonzero commands")
    if args.tag_patrol:
        run_tag_patrol(
            config,
            args.arm_token,
            auto_takeoff=args.takeoff,
            manual_takeoff_confirmed=args.manual_takeoff_confirmed,
            land_after_hover=args.land_after_hover,
            resume_wall_id=args.resume_wall_id,
        )
        return 0
    if args.test:
        run_flight_test(
            config,
            args.arm_token,
            args.test,
            distance_m=[float(d) for d in str(args.distance).split(",") if d.strip()],
            speed_mps=args.speed,
            auto_takeoff=args.takeoff,
            manual_takeoff_confirmed=args.manual_takeoff_confirmed,
            stick_mode=args.stick_mode,
            direction=args.direction,
            climb_to_m=args.climb_to,
            use_vision=args.vision,
            gimbal_deg=args.gimbal,
            search_gimbal_deg=args.search_gimbal,
            until_stopped=args.until_tag,
            until_rc=args.until_rc,
            reverse_on_stall=args.reverse_on_stall,
            duration_s=args.duration,
            angle_deg=args.angle_deg,
        )
        return 0
    run_hardware(
        config,
        args.arm_token,
        args.manual_takeoff_confirmed,
        auto_takeoff=args.takeoff,
        land_after_hover=args.land_after_hover,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
