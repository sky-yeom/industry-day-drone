# Dashboard / Speech / drone integration — 2026-09-10

## Backend-only integration with current main

Integration baseline: **main `e129277`**, prior backend `958cd49`, and field source
`6a4e233`, with an explicit field HTTP adapter. The integration does not replace the main team's frontend,
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
An explicitly configured, authenticated, device-bound outbound PC channel is required before using that cloud
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

The standalone CLI remains a separate entry point, with fixed route
`[6,1,2,3,2,1,6]`. The **field HTTP adapter reuses those actual helpers**, including
settled takeoff, bounded climb, `MixedDetector`, and the latest `PairFramingGate`.
It has a separately validated external route extension, not a rewritten motion
loop or the old PULSE controller. Do not run the standalone controller concurrently
with the HTTP mission owner. A sample's flags do not certify the current PC/site.

Baseline: `sky/feat/dashboard` at `f416ae545f703542f30e4b69c2dbcd896635a5f7`.
Branch: `feat/drone`, commit identity `WhoAmI125`.

## Flow and ownership

The dashboard still collects the participant's search description and visit order.
Voice Live calls the existing `launch_mission` tool only after route confirmation.
`SurveySession` retains the scenario, real-time deadlines, clue interpretation,
image analysis and scoring. This integration does not choose a different route.

`DRONE_CONTROL_MODE=mock` preserves the dashboard's existing timed fixture flow.
Only explicit `DRONE_CONTROL_MOCK_CAPTURES=1` enables capture-capable mock tools:
capabilities then include `mock_capture_ready=true`. Each selected monitor returns
the canonical `public/monitors/monitor-N.png` bytes and a second, visibly marked
synthetic fixture variant with different pixels. Both are `simulated=true`,
`capture_source=synthetic_fixture`; neither asserts camera freshness or physical
arrival. `fixture_sha256` preserves the canonical asset identity for both variants;
the actual capture `sha256` still describes its own transmitted bytes. The fixture
variant encoder uses only Python's standard library, without PC vision dependencies.
Default mock continues returning no tool captures. See relay settings for
its explicit tools-transport mock mode; enabling mock captures alone does not change
the relay's default scenario runner.
`DRONE_CONTROL_MODE=live` creates a `LiveMissionRunner`: it reads capabilities,
maps each `monitor-N` to the registered `tag-N`, submits the whole selected route
once, then waits for actual arrival and attributed PNG frames. It never falls back
to simulated travel or fixture images. `TRIAGE_MODE=azure` is required for live
captures; the two mode flags have different responsibilities.

The field profile separates forward-facing **Home ID6** from the three
scenario destinations: ID1 = sea, ID2 = rubble, ID3 = fire. **Floor ID0** remains the
takeoff/manual-landing reference. The displayed downward target height remains
**1.5m**, lateral BODY ANGLE at most **0.6 degrees**, and ascent at most **0.18m/s**.
These are flight settings, not a surveyed mounting height for Home ID6.

Field left-to-right order is **[3,2,1,6]**, with floor ID0 under the aircraft
facing Home6. Direction for every selected leg, including non-adjacent legs and
rightward visits, comes from this exact order. Only the expected ID confirms a
visit. Target centre must be at **85–95%** of image width, with the entire black
tag inside the image. Continuous proportional lateral correction/reacquisition
uses the latest pair gate (correction **0.25–0.6 degrees**); reversals settle first.
No vertical/depth/yaw corrections are permitted during lateral travel. The
reference-plane footprint is diagnostic-only, and `tv_visibility_verified=false`.
After the selected visits, Home6 uses the broad, non-pair return gate.

The adapter accepts all six permutations visiting registered tags 1/2/3 exactly
once. ID6 is not a fourth destination and does not count as a scenario visit or
capture. It first acquires ID6 after ascent, then returns to ID6 after the selected
route, deriving both outbound and return directions from the measured order. It
releases Virtual Stick and waits for **manual RC landing at floor ID0**; this change
does not add automatic floor alignment or landing.

The explicit/default `DRONE_CONTROL_ADAPTER=legacy` preserves the older HTTP
adapter, including 1.4m and the example's `[6,2,1,3]` aircraft-left ordering.
`integration/site.example.json` is **legacy only**, not a field setup certificate.
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
Connectors additionally send `X-Drone-Expected-Mode: mock` or `live` on every
request. The service compares this with its actual mode **before** dispatching
tools, camera operations or admission lookups; mismatch returns HTTP 409
`MODE_MISMATCH` without execution. Invalid/duplicate mode headers are rejected.
This prevents a mock connector from controlling a replacement live service
between its capability preflight and execute POST. Existing local clients may
omit the header; authenticated connectors must always set their fixed expected mode.
Capabilities advertise `expected_mode_guard=true`; connectors require this before
opening their cloud channel so an older unguarded PC service fails closed.

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
Field capabilities add `adapter=field`. Its full-frame PNG pixels are exactly
the undistorted pixels used by `MixedDetector` for the same framing decision
(`capture_source=pc_undistorted_camera_frame`), without crops or annotations.
Frame identity, generation, finite stationary N/E/down velocity, current RC/MSDK
ownership, and freshness <=500ms are rechecked after PNG/base64 encoding.
Both decoded frame ID and PNG contents must differ for the second capture.
Mission/visit/destination bindings, `capture_evidence`, `framing_diagnostic`,
`arrival_band_fraction=[0.85,0.95]` and `simulated=false` accompany the bytes.
Each ordered visit emits moving, visually confirmed arrival, then two captures;
preflight/taking_off/running/returning remain mission states. Home6 is not a visit.
If either capture is unavailable the mission stops/releases, without replaying
movement or passing a stale JPEG off as fresh camera evidence.
Local confirmation JPEGs, first-detection diagnostics, and the background frame
recorder use `cv2.imencode` plus Python binary file writes, not OpenCV pathname
writes. This supports Korean/Unicode checkout and capture directories; encoding,
filesystem and incomplete-write failures remain failures, never saved-photo claims.

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

Starting these processes does not start a mission. Default mode is mock, and the
adapter selector defaults to legacy. Field execution requires all of:

```text
DRONE_CONTROL_MODE=live
DRONE_CONTROL_ENABLE_LIVE=1
DRONE_CONTROL_ADAPTER=field
DRONE_CONTROL_SITE_CONFIG=C:\private\field-site.local.json
DRONE_CONTROL_CONFIG_PATH=C:\private\config.local.json
DRONE_CONTROL_FIELD_PROFILE=C:\checkout\drone-control\trials\profiles\standalone_tag_6321236.json
DRONE_CONTROL_FIELD_REFERENCE=C:\checkout\drone-control\trials\profiles\id1_tv_pair_reference.json
```

Use the relay's real-image analysis configuration separately. All paths must
identify the intended local files; missing/invalid field settings fail, never fall
back to legacy or mock. The private field site accepts exactly this schema:

```json
{
  "schema_version": 1,
  "profile_id": "field-ordered-v1",
  "site_revision": "replace-with-independent-private-site-revision",
  "wall_ids_left_to_right": [3, 2, 1, 6],
  "floor_tag_id": 0,
  "home_tag_id": 6,
  "target_height_m": 1.5,
  "expected_bridge_build_id": "5.18-connectivity.20260910.6",
  "layout_confirmed": false,
  "field_setup_confirmed": false
}
```

Both confirmation booleans must independently be true for a prepared PC/site.
Upstream profile `layout_confirmed=true` is not this confirmation. Private nav
JSON must have calibrated camera intrinsics, `actual_measurements_confirmed=true`,
the actual positive floor0 black-square size, current private non-loopback phone
IP, and private non-placeholder confirmation token. Field's explicitly selected
image-only-wall loader accepts only floor0 in `tags`, with `world_pose=null`;
wall sizes/poses are neither required nor invented. Existing extra tag facts are
preserved but never used for metric wall navigation. Legacy config validation
and standalone CLI defaults are unchanged. Profile target must be 1.5m; reference
band must be `[0.85,0.95]`. Do not treat sample dimensions/calibration as measurements.

The service is loopback-only, rejects browser Origin requests and keeps tokens out
of tools, browser bundles and logs. Raw flight JSONL, SQLite/captures, configs and
APK files are local and excluded from Git. Do not run another flight controller
against the same phone while this service owns a mission.

## Android connectivity changes

Required field source build: **5.18-connectivity.20260910.6**, versionCode **20260910**.
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

### Legacy-only TV-left / tag-right capture framing

This section describes `DRONE_CONTROL_ADAPTER=legacy`, **not field**. Field uses
the continuous 85–95% pair gate above and never invokes this older PULSE path.

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
