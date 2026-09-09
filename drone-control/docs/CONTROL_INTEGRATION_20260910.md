# Dashboard / Speech / drone integration — 2026-09-10

Baseline: `sky/feat/dashboard` at `f416ae545f703542f30e4b69c2dbcd896635a5f7`.
Branch: `feat/drone`, commit identity `WhoAmI125`.

## Flow and ownership

The dashboard still collects the participant's search description and visit order.
Voice Live calls the existing `launch_mission` tool only after route confirmation.
`SurveySession` retains the scenario, real-time deadlines, clue interpretation,
image analysis and scoring. This integration does not choose a different route.

`DRONE_CONTROL_MODE=mock` preserves the dashboard's existing timed fixture flow.
`DRONE_CONTROL_MODE=live` creates a `LiveMissionRunner`: it reads capabilities,
maps each `monitor-N` to the registered `tag-N`, submits the whole selected route
once, then waits for actual arrival and attributed PNG frames. It never falls back
to simulated travel or fixture images. `TRIAGE_MODE=azure` is required for live
captures; the two mode flags have different responsibilities.

The current physical adapter supports the previously used corridor: aircraft-left
order **2, 1, 3**, floor **0** under home **2**, displayed downward height **1.4m**,
BODY ANGLE at most **1.5 degrees**, at most **0.18m/s** vertical correction.
It accepts all six permutations visiting registered tags 1/2/3 exactly once.
Intermediate non-target tags do not become visits. It returns to tag 2, releases
Virtual Stick and waits for RC landing. The tagless COEX 1.8m/1m-left-return runner
remains a separate entry point; it cannot stand in for three monitor destinations.

Raw obstacle samples remain logged. PC obstacle-distance stop conditions are
disabled for this profile; aircraft avoidance settings are not changed. Values
such as 60000 are retained as unresolved range codes rather than measured 60m.

## Seven backend tools

All calls use `POST http://127.0.0.1:8766/tools/{name}`, bearer authentication, and
`{arguments, caller_id, request_id}`. The browser and language model never receive
the phone arm token or this bearer token. Arguments are validated again by the service.

| Tool | Result |
|---|---|
| `drone_get_capabilities` | Mode, profile/site revision, registered destinations, exact supported sequences |
| `drone_get_status` | Current status evidence, active mission and originating caller/request |
| `drone_execute_route` | Durable admission of one external ordered route; not proof of takeoff or completion |
| `drone_get_mission` | Visit/capture/state evidence; renews the active caller's 10-second lease |
| `drone_stop_mission` | Cancellation request; physical stop and ground verification remain separate |
| `drone_get_sensor_snapshot` | Raw telemetry and diagnostic snapshot; no object classes or identities |
| `drone_get_captures` | PNG bytes bound to mission, visit index, destination, capture ID and SHA-256 |

Responses contain `schema_version`, `ok`, `execution_mode`, `physical_execution`
and tool-specific fields. Execute/get/stop return a `mission` object. Captures are
at most two fresh PC-decoded camera frames per confirmed visit, 4MiB each; their
timestamps describe PC decode/capture time, not an aircraft exposure timestamp.

SQLite stores admissions, request fingerprints, visits and captures before replying.
The same caller/request returns the original admission; changed arguments conflict.
`GET /requests/{caller_id}/{request_id}` retrieves the durable result after an
ambiguous reply. An uncertain write is never automatically replayed. A restart
marks unfinished work `outcome_unknown` and does not launch it again. An occupied
mission is released only by fresh independent ground evidence, or cancellation
before any execution was dispatched. The single physical worker owns its socket.

The relay polls the mission lease independently of image analysis. If the relay
dies or loses the API for 10 seconds, the service requests cancellation. Closing
voice/WebSocket, deadline expiration, abort and live-operation errors also request
stop. ACK timeouts, RC takeover and app generation changes end automatic execution.
New missions remain blocked while physical state is unresolved. The dashboard
shows scenario completion separately from return, RC landing and stop confirmation.

## Windows setup and launch

Use Node >=20.9, Python >=3.11 and the existing Azure credentials in backend-only
environment variables. Install the root JavaScript lockfile dependencies with
`npm ci`. Create isolated Python environments from the repository root:

```powershell
python -m venv relay/.venv
relay/.venv/Scripts/python -m pip install -r relay/requirements.lock.txt
python -m venv drone-control/.venv
drone-control/.venv/Scripts/python -m pip install -e './drone-control[vision]'
```

Copy `drone-control/integration/control.env.example` to `.env.drone.local` and
`relay/.env.example` settings into the same private file as needed. Set a random
`DRONE_CONTROL_API_TOKEN` of at least 24 characters shared by the two backends.
The script reads literal `KEY=value` lines; it does not evaluate shell expressions.
Run each component in its own terminal:

```powershell
./scripts/start-drone.ps1 -Component control -EnvFile .env.drone.local
./scripts/start-drone.ps1 -Component relay -EnvFile .env.drone.local
./scripts/start-drone.ps1 -Component dashboard
```

Starting these processes does not start a mission. Default mode is mock. To
configure the physical adapter, set `DRONE_CONTROL_MODE=live`,
`DRONE_CONTROL_ENABLE_LIVE=1`, `DRONE_CONTROL_SITE_CONFIG` and
`DRONE_CONTROL_CONFIG_PATH`, plus `TRIAGE_MODE=azure` and the relay's image/voice
credentials. A copy of `integration/site.example.json` requires an actual measured
site revision and `layout_confirmed=true`; the shipped example deliberately does
not assert that a new venue has the old tag layout. The private nav config must
contain actual calibration, tags, current phone IP and phone arm token.

The service is loopback-only, rejects browser Origin requests and keeps tokens out
of tools, browser bundles and logs. Raw flight JSONL, SQLite/captures, configs and
APK files are local and excluded from Git. Do not run another flight controller
against the same phone while this service owns a mission.

## Android connectivity changes

New source build: **5.18-connectivity.20260910.5**, versionCode **20260910**.
The overlay implements bounded shared physical SDK reads, FC key health and
actual motor telemetry, process/connection generations, transactional perception
listeners, connection-owned query callbacks, camera-binding generations and
Surface ownership. Automatic recovery is **off by default** and requires fresh
ground/motors-off proof when enabled. There is no automatic re-arm or flight replay.
Nonzero command freshness has a 300ms zero and 1000ms disable watchdog; STATUS and
heartbeat do not refresh an old movement command. Video source/queue/socket/phone
display evidence is kept separate so a black screen is not automatically called
a network disconnection.

The new APK must be installed on the phone to use these fixes. Source overlay
details and the upstream sample baseline are in [Android README](../android/README.md).
Existing historical counted trial scripts keep their original build requirement.
The separate COEX runner accepts .4 and .5, requiring watchdog and fresh actual
motor telemetry for .5. New-build execution still needs ground/hardware validation.

## Verification and practical limits

Build/test results are recorded in `IMPLEMENTATION_VALIDATION_20260910.md` after
the final integration checks. Pure tests and APK compilation do not prove that
DJI's internal FC error has disappeared or that a phone's video is fixed in hardware.
No live flight or paid Azure request is made by offline tests. The scenario's
current 18/28/45-second deadlines include actual travel and analysis; whether all
destinations are reachable in those windows must be measured, not replaced with
the old seven-second simulated travel time.
