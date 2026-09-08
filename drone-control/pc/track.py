"""Live flight tracker.

Subscribes to the aircraft's own telemetry through the query server and shows
what the drone is ACTUALLY doing, independently of whatever the control
program believes. Integrates the reported NED velocity into a displacement so
"did it move sideways?" stops being a question answered by eye.

Run it in a second terminal while a flight test is running:

    python pc\\track.py                 # live view
    python pc\\track.py --log run.csv   # also write a CSV

Read-only: it never sends a control command.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import socket
import sys
import time

QUERY_PORT = 9997
# Keys pushed by the aircraft. LISTEN delivers a line per value change.
LISTEN_KEYS = (
    "FlightController AircraftVelocity",
    "FlightController AircraftAttitude",
    "FlightController UltrasonicHeight",
    "FlightController IsFlying",
)


def _parse_line(line: str):
    """'FlightController AircraftVelocity {"x":0,"y":0,"z":0}' -> (key, value)."""
    parts = line.strip().split(" ", 2)
    if len(parts) < 3:
        return None, None
    key, payload = parts[1], parts[2]
    if payload.startswith("{"):
        try:
            return key, json.loads(payload)
        except json.JSONDecodeError:
            return key, None
    if payload in ("true", "false"):
        return key, payload == "true"
    try:
        return key, float(payload)
    except ValueError:
        return key, payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live DJI flight tracker")
    parser.add_argument("--host", default="172.16.251.9")
    parser.add_argument("--log", help="write a CSV of every sample here")
    parser.add_argument(
        "--zero-after-s",
        type=float,
        default=0.0,
        help="restart the displacement integral this many seconds in, so the "
        "takeoff drift is not counted against the leg you care about",
    )
    args = parser.parse_args(argv)

    try:
        sock = socket.create_connection((args.host, QUERY_PORT), timeout=5.0)
    except OSError as error:
        print(f"cannot reach the query server at {args.host}:{QUERY_PORT}: {error}")
        return 1
    sock.settimeout(1.0)
    for key in LISTEN_KEYS:
        sock.sendall((f"LISTEN {key}\n").encode())

    writer = None
    handle = None
    if args.log:
        handle = open(args.log, "w", newline="", encoding="utf-8")
        writer = csv.writer(handle)
        writer.writerow(
            ["t_s", "vN", "vE", "vD", "dN", "dE", "dD", "yaw_deg", "height_m", "flying"]
        )

    north = east = down = 0.0
    dn = de = dd = 0.0
    yaw = float("nan")
    height = float("nan")
    flying = None
    started = time.monotonic()
    last = started
    zeroed = args.zero_after_s <= 0.0
    buffer = ""

    print("tracking - Ctrl+C to stop.  displacement is integrated aircraft velocity")
    print("  NED: N/E are horizontal, D is down-positive\n")
    try:
        while True:
            now = time.monotonic()
            dt = now - last
            last = now
            # Integrate first: the last reported velocity held over this slice.
            dn += north * dt
            de += east * dt
            dd += down * dt
            if not zeroed and now - started >= args.zero_after_s:
                dn = de = dd = 0.0
                zeroed = True
                print("\n-- displacement reset --")

            try:
                chunk = sock.recv(65536).decode(errors="replace")
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line.strip():
                        continue
                    key, value = _parse_line(line)
                    if key == "AircraftVelocity" and isinstance(value, dict):
                        north = float(value.get("x", 0.0))
                        east = float(value.get("y", 0.0))
                        down = float(value.get("z", 0.0))
                    elif key == "AircraftAttitude" and isinstance(value, dict):
                        yaw = float(value.get("yaw", float("nan")))
                    elif key == "UltrasonicHeight" and isinstance(value, (int, float)):
                        height = float(value) / 10.0
                    elif key == "IsFlying":
                        flying = value
            except socket.timeout:
                pass

            elapsed = now - started
            speed = math.hypot(north, east)
            sys.stdout.write(
                f"\rt={elapsed:6.1f}s  v N={north:+.2f} E={east:+.2f} D={down:+.2f} "
                f"|v|={speed:4.2f}  disp N={dn:+.2f} E={de:+.2f} D={dd:+.2f} m  "
                f"yaw={yaw:6.1f}  h={height:4.2f}m  flying={flying}   "
            )
            sys.stdout.flush()
            if writer:
                writer.writerow(
                    [f"{elapsed:.2f}", north, east, down, f"{dn:.3f}",
                     f"{de:.3f}", f"{dd:.3f}", yaw, height, flying]
                )
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n\nstopped")
        print(f"total displacement:  N={dn:+.2f} m  E={de:+.2f} m  D={dd:+.2f} m")
        horizontal = math.hypot(dn, de)
        print(f"horizontal distance: {horizontal:.2f} m")
    finally:
        try:
            sock.close()
        except OSError:
            pass
        if handle:
            handle.close()
            print(f"CSV written to {args.log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
