# Shared tool schemas and historical mock starter

The `tools.json` schemas are now also consumed by the production relay client.
The Python starter in this directory remains a separate mock-only example.
The current durable HTTP service is `pc/drone_nav/tool_control`; the relay uses
`relay/drone_client.py` and `relay/live_mission.py`. See
[current integration instructions](../../docs/CONTROL_INTEGRATION_20260910.md).
The rest of this document describes the historical mock starter, not the live service.

This stdlib-only starter exercises our drone boundary. It cannot connect to a drone,
move hardware, or establish flight safety. Dashboard, scenarios, destination choice,
route planning, ordering and scoring belong to the other team.

Run `python example.py` and `python -m unittest discover -s tests -v` from this directory.
`tools.json` contains flattened function definitions and strict JSON Schemas. The
Speech team maps them to its Voice Live SDK envelope and verifies provider support
for schema keywords; local validation remains mandatory. `Gateway` takes an injected asynchronous transport;
the only implementation supplied is `MockService`. REST paths are proposed contract
paths in `contract.ROUTES`, not deployed endpoints. No host, credentials or raw flight
controls are model arguments.

Read capabilities first. MOCK profile `trial-23132-v1`, revision `mock-site-r1`,
supports only `[tag-2, tag-3, tag-1, tag-3, tag-2]`. IDs identify mock physical
fiducial markers, with no screen/scenario mapping or asserted real-world coordinates.
The service preserves the requested sequence, including non-adjacent repeated stops.
Other sequences return `ROUTE_UNSUPPORTED`; stale revisions are rejected.

For writes, trusted application code supplies `caller_id` and an opaque `request_id`
separately from tool arguments. Their pair identifies the business execution intent;
preserve it across retries/reconnects. Provider/Azure `call_id` is logging metadata,
not the stable idempotency key. Repeated keys with identical requests return a copied
original result; different requests conflict. Keys span write operations per caller. An async lock
protects the mock's idempotency store and single active mission. State and keys are
in-memory and disappear on restart; durable storage and real driver integration are
outside this starter. Service input is revalidated independently of the gateway.

Acceptance means a mock mission exists. Stop means `stop_requested`, never confirmed
physical stopping or landing, and keeps the mission busy. Only the explicit test
fixture method can mark a terminal state and release it; that method is not a tool.
Flight status remains unknown, absent sensor readings stay empty/null, and captures
are empty. Every result says `execution_mode: mock` and `physical_execution: false`;
capabilities also say `live_ready: false`.

Status keeps `is_flying`, `are_motors_on`, `armed`, `vs_enabled`,
`control_authority`, and `snapshot_age_ms` separate; all are unknown in the mock.
Sensor readings define nullable `oa_horizontal_distances_mm`,
`oa_horizontal_angle_interval_deg`, `oa_upward_distance_mm`,
`oa_downward_distance_mm`, and `oa_obstacle_data_age_ms`. The last field is callback
age, not a guaranteed sensor heartbeat. No missing distance is converted to zero.

Every reply includes `schema_version: 1`, `ok`, `status`, `execution_mode`, and
`physical_execution`. Operation-specific fields such as `mission`, `readings`, or
`captures` are top-level; failures contain `error.code`. `accepted` is not completed.
Do not substitute a live transport into this mock-only starter: error responses
also describe a mock boundary. The production adapter, durable journal and live
response contract must pass the implementation plan's separate acceptance checks.

Authoritative implementation plan:
[DRONE_TOOL_CONTROL_IMPLEMENTATION_PLAN_20260908.md](../../docs/fix_ready_20260907/DRONE_TOOL_CONTROL_IMPLEMENTATION_PLAN_20260908.md).

A write timeout returns `OUTCOME_UNKNOWN`; a read timeout returns `TRANSPORT_ERROR`.
The gateway never retries or replays automatically. A caller must preserve the
original caller/request ID pair and reconcile an ambiguous write under an independently agreed
policy; blindly creating a new ID could duplicate a real operation in a future system.
This starter has no production deployment or live backend.
