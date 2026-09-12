# Drone control API and diagnostic contract

This document describes protocol version 1 and result schema version 1.  The
goal is to let a human operator, a deterministic controller, or a Realtime
agent distinguish four different facts:

1. the PC sent a command;
2. Android parsed and accepted it;
3. MSDK received/submitted the Advanced parameter;
4. aircraft telemetry confirmed physical motion.

An HTTP-like number such as `201` or `402` must never be the only tool result.
Every ACK includes a stable status token, a Korean explanation, raw detail,
and a telemetry snapshot.

## Control path

- Production: `advanced` / `OFFICIAL_ADVANCED`
  - `enableVirtualStick()`
  - `setVirtualStickAdvancedModeEnabled(true)`
  - `sendVirtualStickAdvancedParam(param)` continuously at 20 Hz
- Diagnostic only: `basic`
- Diagnostic only: `advanced_direct`
  - Sends the same parameter through the low-level action key solely to expose
    the DJI completion callback. It is not the production path.
- Diagnostic for indoor `GPS_ATTI`: `advanced_angle` /
  `OFFICIAL_ADVANCED_ANGLE`
  - Uses the same official continuous Advanced sender at 20 Hz.
  - Horizontal mode is BODY `ANGLE`; vertical remains `VELOCITY` and yaw
    remains `ANGULAR_VELOCITY`.
  - Tilt is hard-limited to +/-3 degrees and is never inferred to be m/s.

Advanced parameters are fixed to BODY coordinates, horizontal VELOCITY,
vertical VELOCITY, and yaw ANGULAR_VELOCITY. The semantic mapping is:

| Meaning | MSDK field |
| --- | --- |
| forward/back | Roll |
| right/left | Pitch |
| up/down | VerticalThrottle |
| clockwise/counter-clockwise yaw | Yaw |

ANGLE uses a separate `attitude` request because DJI's BODY-angle signs and
axes differ from BODY-velocity: semantic forward becomes negative SDK Pitch,
and semantic right becomes positive SDK Roll.

| Semantic ANGLE meaning | MSDK field |
| --- | --- |
| forward/back | Pitch negative/positive |
| right/left | Roll positive/negative |
| up/down | VerticalThrottle velocity |
| clockwise/counter-clockwise yaw | Yaw angular velocity |

## Request envelope

Exactly five top-level keys are allowed. Each request is one newline-delimited
JSON object.

```json
{
  "version": 1,
  "sequence": 42,
  "timestamp_ns": 1788400000000000000,
  "type": "velocity",
  "payload": {
    "forward_mps": 0.0,
    "right_mps": -0.3,
    "up_mps": 0.0,
    "yaw_rate_rps": 0.0
  }
}
```

Explicit Advanced ANGLE example (left tilt):

```json
{
  "version": 1,
  "sequence": 43,
  "timestamp_ns": 1788400000100000000,
  "type": "attitude",
  "payload": {
    "forward_tilt_deg": 0.0,
    "right_tilt_deg": -2.0,
    "up_mps": 0.0,
    "yaw_rate_rps": 0.0
  }
}
```

`sequence` and `timestamp_ns` must increase within one TCP connection. They
reset when a new connection is accepted. Supported request types are `status`,
`stick_mode`, `arm`, `takeoff`, `land`, `velocity`, `attitude`, `zero`, `heartbeat`,
`disarm`, `emergency_stop`, `gimbal`, and `obstacle_avoidance`.

## ACK envelope

```json
{
  "version": 1,
  "type": "ack",
  "sequence": 42,
  "timestamp_ns": 123456789,
  "payload": {
    "ok": true,
    "detail": "velocity_accepted",
    "result_schema_version": 1,
    "status": "PHONE_ACCEPTED_WAITING_FOR_MOTION",
    "message_ko": "속도 명령이 휴대폰 제어기에 저장되었습니다. 실제 기체 이동은 아직 확인되지 않았습니다.",
    "telemetry": {}
  }
}
```

The ACK for `velocity` or `attitude` proves Android acceptance, not aircraft motion. The
official Advanced manager call has no per-frame completion callback. A later
ACK's telemetry is correlated with `active_command_sequence`, and the PC emits
one of these motion assessments:

| Status | Meaning |
| --- | --- |
| `WAITING_FOR_MOTION` | Setpoint is younger than 0.6 s |
| `MOTION_CONFIRMED` | Fresh NED velocity projects onto the commanded body direction |
| `SDK_SUBMITTED_NO_MOTION` | Submission is recorded but no commanded-direction motion is measured |
| `TELEMETRY_STALE` | Velocity or attitude is too old to decide |
| `SDK_COMMAND_FAILED` | Diagnostic direct-send callback returned a DJI error |
| `SETPOINT_ZERO` | Current setpoint is zero |
| `NO_ACTIVE_COMMAND` | No correlated velocity/zero command exists |

## Correlation and telemetry

Every ACK snapshot contains, when available:

- aircraft response: NED velocity and age, yaw/pitch/roll and age, ultrasonic
  height and age, flying state, battery;
- control state: Virtual Stick enabled, Advanced enabled, authority owner,
  authority change reason, FlightMode and age, plus explicit
  `time_watchdog_enabled` and `disconnect_release_enabled` flags;
- selected implementation: stick mode, control path, speed level;
- command trace: requested values, clamped setpoints,
  `active_command_sequence`, command age, and the final SDK Roll/Pitch values,
  units, and control mode;
- SDK trace: submit sequence/frame/age/result and, on the diagnostic direct
  path, completion result/error;
- obstacle avoidance state and working sensor directions.

The PC writes an append-only JSONL session under `pc/logs/` (or the directory
in `DRONE_NAV_LOG_DIR`). Events include `pc_request`, `android_ack`, transport
errors, and `motion_assessment`; the arm confirmation token is redacted.

For the current diagnostic build, the former 1.5 s heartbeat Zero and 2 s
Virtual Stick release are disabled. Physical RC override, explicit
zero/disarm, control-path mismatch, sender failure, and an actual control TCP
disconnect still release control.

## Direct DJI SDK query/call console

Port `9997` exposes DJI KeyManager diagnostics independently of the flight
controller protocol. The PC wrapper turns the SDK's raw strings into the same
kind of semantic JSON needed by an operator or Realtime agent:

```powershell
# Read one current value (no token required)
python pc\sdk_api.py get FlightController FCFlightMode

# Watch value changes
python pc\sdk_api.py listen FlightController AircraftVelocity

# Invoke a DJI action (operator token required)
python pc\sdk_api.py call FlightController StartTakeoff --arm-token <token>
```

The result contains `ok`, `status`, `message_ko`, parsed `value`, parsed
`dji_error`, and the complete `raw_dji_response`. `GET`, `HELP`, and `LISTEN`
are read-only. `SET` and `CALL/ACTION` are rejected by Android unless the arm
token is supplied. Flight operations should normally use port `9998`, because
that path adds control-state gates, RC override, disconnect release, and
telemetry.

## Field-test gate

Before any non-zero command, verify in this order:

1. `status` round trip works and telemetry is fresh;
2. `stick_mode=advanced` selects `OFFICIAL_ADVANCED`, or the explicit indoor
   diagnostic selects `advanced_angle` / `OFFICIAL_ADVANCED_ANGLE`;
3. ground-only arm/zero check reports Virtual Stick enabled, Advanced enabled,
   and authority `MSDK`; log both the bridge FlightMode and a direct
   `FCFlightMode` GET because this Mini 4 Pro currently reports `GPS_ATTI`
   through the latter indoors even while Virtual Stick authority is granted;
4. RC-N2 deflection releases Virtual Stick;
5. airborne axis test runs one direction at a time and records the full JSONL
   timeline;
6. only after the axis mapping is observed does AprilTag navigation run.
