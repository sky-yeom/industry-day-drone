# Dashboard / Speech / drone integration — 2026-09-10

## Backend-only integration with current main

Integration baseline: **main `cc84076`**, with drone sources from
**feat/drone `2cd49d1`** plus the local Home6, shared-camera and horizontal-only
TV-framing changes. The integration does not replace the main team's frontend,
scenario assets, sign-in gate, infrastructure, web container or deployment script.
Only `relay/`, `drone-control/` and the owned Windows backend launcher are included.

The existing browser contract remains: `/api/config`, `/ws`, voice tool names and
arguments, `route.state`, `tool.started`/`tool.finished`, departure/result events,
and full-frame `captures[].imageUrl`. Drone status and capture attribution are
additive fields. Camera preview `/ws/camera` and `/api/drone/status` are optional
backend capabilities; main's unchanged frontend does **not** render those new
panels or the detailed physical stop/landing states automatically.

Main now bakes the **cloud relay URL** into its web build. That relay cannot reach
the operator PC's `127.0.0.1:8766`: loopback in Azure means the Azure container, not
the PC. This merge preserves that deployment and keeps flight **mock by default**.
It does not implement or enable a cloud-to-PC control channel. A separately reviewed,
authenticated, device-bound outbound PC channel is required before using that cloud
relay for real flight; do not expose the raw local API or phone ports as a shortcut.
Command identity, ordering, lease expiry, uncertain-write handling, stop verification
and single-video ownership must survive such a channel, not just JSON field names.

`relay/Dockerfile` includes only the shared seven-tool JSON schema from the drone
subtree. The cloud relay does not import or install DJI/PC vision code; the default
mock scenario and existing Azure voice/vision deployment remain usable without a PC.
Its Dockerfile-specific ignore list limits backend build context to relay source,
scenario assets and that schema, excluding local environments, secrets and configs.
Use a clean source checkout for shared image builds; the operator's runtime folder
is not a deployment context.
Frontend contract readiness is not physical-flight or camera-stream readiness.
Before enabling live missions with the current frontend, agree the UI contract for
RC takeover, unknown stop outcomes and manual landing; scenario completion is not
aircraft landing.

The newly fetched standalone checker/shuttle remains a **separate entry point**:
its default left-to-right `[1,2,3,6]` differs from the dashboard profile
`[3,1,2,6]`. Neither profile is interchangeable, and a sample's flags do not certify
the current physical venue. Do not run the standalone controller concurrently with
the dashboard's mission owner.

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

The current dashboard profile separates forward-facing **Home ID6** from the three
scenario destinations: ID1 = sea, ID2 = rubble, ID3 = fire. **Floor ID0** remains the
takeoff/manual-landing reference. The displayed downward target height remains
**1.4m**, BODY ANGLE at most **1.5 degrees**, and vertical correction at most **0.18m/s**.
These are flight settings, not a surveyed mounting height for Home ID6.

The planned displayed left-to-right order is **3, 1, 2, 6**, with floor ID0 under
the aircraft facing Home ID6. Accordingly the example's aircraft-left traversal
order is **[6, 2, 1, 3]**. This is a planned layout, not a measured calibration:
`layout_confirmed` remains **false**. Verify the complete physical layout, current
camera/body calibration and actual measurements before issuing a new private site
revision. Home must occur exactly once, preserving destination-left order **2, 1, 3**.

The adapter accepts all six permutations visiting registered tags 1/2/3 exactly
once. ID6 is not a fourth destination and does not count as a scenario visit or
capture. It first acquires ID6 after ascent, then returns to ID6 after the selected
route, deriving both outbound and return directions from the measured order. It
releases Virtual Stick and waits for **manual RC landing at floor ID0**; this change
does not add automatic floor alignment or landing.

Existing measured Home-ID2 profiles remain supported. The legacy `drone-nav` patrol
and historical trial scripts still use their original ID2 anchor; the HTTP adapter
is the dashboard integration path. The tagless COEX 1.8m/1m-left-return runner remains
separate and cannot stand in for three monitor destinations.

Raw obstacle samples remain logged. PC obstacle-distance stop conditions are
disabled for this profile; aircraft avoidance settings are not changed. Values
such as 60000 are retained as unresolved range codes rather than measured 60m.

## Seven backend tools

All calls use `POST http://127.0.0.1:8766/tools/{name}`, bearer authentication, and
`{arguments, caller_id, request_id}`. The browser and language model never receive
the phone arm token or this bearer token. Arguments are validated again by the service.

| Tool | Result |
|---|---|
| `drone_get_capabilities` | Mode, profile/site revision, Home/floor tag IDs, target height, registered destinations, exact supported sequences |
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

## Explicit camera preview (not an LLM tool)

The authenticated backend-only `POST /camera/{action}` endpoint accepts `start`,
`frame`, or `stop` with exactly
`{"arguments":{},"caller_id":"relay-camera-unique-id","request_id":"unique-id"}`.
The same bearer, loopback Host and browser-Origin rejection as the seven tools
apply; unknown fields, duplicate JSON fields and nonempty arguments are refused.
No camera action arms, takes off, changes gimbal position or commands motion.
Service startup and `frame` without a preceding `start` never open video.

Successful responses retain `schema_version:1`, `ok:true`, `status:"ok"`,
`execution_mode` and `physical_execution`, adding this exact `camera` object:

```json
{
  "state": "streaming",
  "simulated": false,
  "frame_id": "1:1:42",
  "age_ms": 23,
  "content_type": "image/jpeg",
  "image_base64": "<base64>",
  "message": null
}
```

States are `streaming`, `waiting`, `stopped`, or `unavailable`. Frame ID, age,
content type, image and message are nullable. Image/content type are present only
for a fresh streaming frame (PC decode age **<=500ms**, not aircraft exposure
time). Live JPEGs have longest edge **<=960px**, decoded byte size **<=512KiB**,
and one shared encode cache at **at most 5fps**, with no queued frame backlog.
An unavailable/stale source is never relabeled as live. Expected source/encoding
failures yield generic unavailable messages; malformed/auth requests use the
existing error envelope without private endpoint or token details.

Each `start` grants one of **four** caller-specific **5-second** preview leases.
Only that caller's `start`/`frame` renews it; `stop` releases only that caller.
A background expiry check releases abandoned viewers within approximately 5.1s.
After expiration, `frame` reports stopped until a new explicit start. Preview
requests are ephemeral and do not create durable mission admissions.

Phone video9999 admits one client, replacing the previous one. The live adapter
and preview therefore share **one broker-owned FreshVideoStream**. Preview reads
immutable decoded snapshots; only the mission invokes detection. Stopping or
expiring preview cannot close the mission's stream, and mission cleanup preserves
a still-viewed preview. Service shutdown cancels/waits for the mission before
closing the broker. Transport loss remains observable and is not auto-reconnected;
release all leases before a new explicit operation can open a new video transport.
The independent legacy patrol stream retains its prior reconnect policy.

Default mock preview uses only the canonical local
`public/monitors/monitor-1.png` (640x400, within the bounds), explicitly labeled
**MOCK fixture; not a live camera**, `simulated:true`, with PNG content type.
Mock service/preview does not require PyAV, OpenCV, NumPy or AprilTag dependencies
and never opens a phone connection. A missing fixture reports unavailable, never
substitutes another image. The seven tool names and destination mapping are unchanged.

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
`pc/config.sample.json` includes a 150mm ID6 with `world_pose=null` for visual
recognition; it does not invent surveyed coordinates. Its legacy ID0/ID2 poses and
patrol defaults are unchanged, and its readiness flags must not be treated as proof
of the new site. The HTTP adapter overrides the patrol cruise target to 1.4m.

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

### TV-left / tag-right capture framing

The sample navigation config now opts into `patrol.tv_framing` for **scenario
IDs 1/2/3 only**. Floor ID0 and forward Home ID6 retain their separate acquisition
and return behavior. This is a reference composition for the operator's 32-inch
TV mock: a TV on the left and its tag on the right, not a tag centred in the image.

The tag centre must fall within **65–82% of decoded-frame width** and **25–60%
of height**. The reference also reserves space from the tag centre: **6 tag
widths left, 0.8 right, 1.5 tag heights above, 2.2 below**, with a **2% frame
margin**. These adjustable image-space ratios come from the reference composition,
not surveyed screen size, wall distance or tag-to-TV spacing. They include room
for the shown mock but do **not detect a television** or prove that an arbitrary
real TV fits. A different mount, gap, perspective or TV aspect ratio requires
ground-level framing review before flight; no new measurement flag is asserted.

Framing uses the tag corners mapped back to the **raw decoded frame**, matching
the full, uncropped image saved and sent to the VLM. This avoids treating
undistorted detector coordinates as raw-image pixels. The same condition gates
arrival, stationary photo dwell and the final two API PNG captures.

For these destination legs, tilt is capped at **0.5 degrees**, including stall
recovery. When the expected tag is visible but the composition is outside the
window, the controller **stops, observes, and applies small left/right-only
corrections toward the TV-plus-tag composition**, even if the small lateral
correction is opposite the route direction. It does not target the optical centre.
Corrections aim slightly inside the nearest window edge, then stop anywhere
inside the allowed window, avoiding continuous point chasing.

Correction pulses are separated by at least **0.3s**; the next control iteration
sends zero. Framing always commands **zero vertical velocity**; the configuration
requires `tv_framing.max_vertical_speed_mps=0`. A **0.15m** height tolerance around
the current profile's **1.4m** target remains a safety gate, not permission to
change height. Ordinary takeoff/ascent and altitude-hold behavior are unchanged.
Corrections stop after
**8s** or **0.4m of travel estimated from measured velocity**, whichever comes
first. These are software command/evidence budgets, not a guarantee of physical
stopping distance. Fresh tag frames, finite recent height/velocity/heading,
airborne MSDK authority and battery >=30% are required. Motion above 0.15m/s is
held before another pulse; missing/stale evidence, a camera-generation change,
RC takeover or an exceeded budget prevents further correction.

Once framed, the controller sends zero and waits for fresh, low-speed frames
before photographing. The same bounded correction can reacquire the composition
if it drifts horizontally during dwell or before the final PNG captures.
A vertical mismatch or insufficient room for the entire reference stops/withholds
capture: it does **not** rise/fall, fly forward/backward, rotate yaw or infer a new
wall distance automatically. Align the TV/tag mounting and camera view on the
ground when their vertical framing is unsuitable.
Home ID6 and floor ID0 acquisition/return settings are unchanged. The capture
metadata deliberately retains `tv_visibility_verified=false`.

Existing private configs do not automatically gain the sample setting. Copy the
`tv_framing` object into their `patrol` object to opt in; omitting it preserves the
legacy broad-view policy. Neither this configuration change nor the screenshot
is evidence of calibrated camera intrinsics/extrinsics or a safe measured site.

Build/test results are recorded in `IMPLEMENTATION_VALIDATION_20260910.md` after
the final integration checks. Pure tests and APK compilation do not prove that
DJI's internal FC error has disappeared or that a phone's video is fixed in hardware.
No live flight or paid Azure request is made by offline tests. The scenario's
current 18/28/45-second deadlines include actual travel and analysis; whether all
destinations are reachable in those windows must be measured, not replaced with
the old seven-second simulated travel time.
