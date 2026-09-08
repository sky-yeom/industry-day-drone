"""Unchanged sonar helper extracted from the supervised trial; no CLI."""
import json
import math
import time
from drone_nav.patrol import _visual_floor_height_m

CLIMB_TIMEOUT_S = 8.0

def climb_command(height, age, target=1.5):
    """Sonar-display target, not a claim of centimetre physical accuracy."""
    if height is None or age is None or not math.isfinite(height) or not math.isfinite(age) or not 0 <= age <= .5:
        raise RuntimeError("no fresh height for climb")
    if not .5 <= height <= 1.8 or not .5 <= target <= 1.5:
        raise RuntimeError("height/target outside bounded ascent")
    error = target - height
    if error < -.051:
        raise RuntimeError("already above ascent target; no automatic descent")
    if abs(error) <= .051:
        return 0.0
    return min(.18, max(.10, error*.8))

def climb_to_sonar_target(client, limiter, stream, detector, recorder, config, target):
    deadline = time.monotonic()+CLIMB_TIMEOUT_S
    reached_since = None
    last_frame_key = None
    last_print = 0
    print(f"CLIMB: sonar-display target={target:.2f}m, up<=0.18m/s, <={CLIMB_TIMEOUT_S:g}s", flush=True)
    while time.monotonic() < deadline:
        limiter.wait()
        client.status("bounded_target_height")
        t = client.last_telemetry
        if t is None or t.armed is not True or t.vs_authority != "MSDK" or t.is_flying is not True:
            raise InterruptedError("climb authority/airborne state lost")
        if t.rc_override_age_s is not None and t.rc_override_age_s < 5:
            raise InterruptedError("RC override during climb")
        tags, age = stream.detect_latest(detector, .5)
        if not math.isfinite(age) or age > .5:
            raise RuntimeError("climb image unavailable")
        floor = next((x for x in tags if x.tag_id == 0), None)
        if floor is None:
            raise RuntimeError("ID0 lost during height cross-check; lateral cancelled")
        visual_height = _visual_floor_height_m(config, floor)
        if not math.isfinite(visual_height) or visual_height > min(2.1, config.room.height_m-.3):
            raise RuntimeError("visual height exceeds short-test ceiling margin")
        up = climb_command(t.height_m, t.height_age_s, target)
        now = time.monotonic()
        # Waiting for a tick, status or image can cross the climb deadline.
        if now >= deadline:
            break
        row = {"phase":"target_height", "source":"ultrasonic_display", "target_m":target,
               "height_m":t.height_m,"id0_estimated_height_m":visual_height,"up_mps":up}
        client.log_event("bounded_height_sample", row)
        recorder.update_context(row)
        if now-last_print > .4:
            print(json.dumps(row), flush=True)
            last_print = now
        if up == 0:
            client.zero()
            frame_key = stream.last_detection_snapshot.key
            if last_frame_key != frame_key:
                reached_since = now if reached_since is None else reached_since
                if now-reached_since >= .5:
                    client.log_event("bounded_target_height_reached", row)
                    print(f"HEIGHT CONFIRMED: sonar={t.height_m:.2f}m, ID0 estimate={visual_height:.2f}m", flush=True)
                    return
            last_frame_key = frame_key
        else:
            reached_since = None
            last_frame_key = None
            client.attitude(0,0,up,0)
    client.zero()
    raise RuntimeError(f"{target:.2f}m sonar target not confirmed within{CLIMB_TIMEOUT_S:g}s; no lateral command")
