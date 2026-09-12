import { createServer } from 'node:http';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { assertRequest, assertResponse, validate, contract, tools } from './validate.mjs';
import { createCapture } from './fixtures.mjs';
import { verifyContract } from './verify.mjs';

export const DEFAULT_PORT = 18767;
export const PROFILE_ID = 'contract-mock-v1';
export const SITE_REVISION = 'v1';
export const SCENARIOS = Object.freeze([
  'nominal', 'preflight-failure', 'camera-unavailable', 'connection-loss', 'manual-landing', 'lost-ack',
]);
const RESERVED_PORTS = new Set([8766, 9997, 9998, 9999]);
const ID = /^[A-Za-z0-9_.:-]{1,128}$/;
const FILES = Object.fromEntries(['tools.json', 'contract.schema.json'].map(name =>
  [`/${name}`, readFileSync(new URL(name, import.meta.url), 'utf8')]));
const TOOL_NAMES = tools.map(tool => tool.name);
const DESTINATIONS = ['tag-1', 'tag-2', 'tag-3'];
const ROUTES = DESTINATIONS.flatMap(a => DESTINATIONS.filter(b => b !== a)
  .map(b => [a, b, DESTINATIONS.find(c => c !== a && c !== b)]));
const base = { schema_version: 1, execution_mode: 'mock', physical_execution: false };
const success = fields => ({ ...base, ok: true, status: 'ok', ...fields });
const detached = value => structuredClone(value);
const publicMission = ({ captures, ...mission }) => detached(mission);
const canonical = value => JSON.stringify(value, (key, item) => item && !Array.isArray(item) &&
  typeof item === 'object' ? Object.fromEntries(Object.keys(item).sort().map(k => [k, item[k]])) : item);
function fail(code, message, status = 400) { throw Object.assign(new Error(message), { code, status }); }
function integer(value, name, max = 30000) {
  if (!Number.isInteger(value) || value < 1 || value > max) throw new TypeError(`Invalid ${name}`);
  return value;
}
function validPort(port, ephemeral = false) {
  if (!Number.isInteger(port) || port < (ephemeral ? 0 : 1) || port > 65535 || RESERVED_PORTS.has(port)) {
    throw new TypeError('Port must be 1..65535, excluding 8766, 9997, 9998 and 9999');
  }
  return port;
}
function parseJson(text) {
  let value;
  try { value = JSON.parse(text); } catch { fail('INVALID_ARGUMENT', 'Malformed JSON'); }
  // JSON.parse accepts duplicate object keys; reject that ambiguous wire representation.
  const tokens = text.match(/"(?:\\.|[^"\\])*"|[{}\[\],:]|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g);
  let at = 0;
  function scan() {
    const start = tokens[at++];
    if (start !== '{' && start !== '[') return;
    const seen = new Set(), end = start === '{' ? '}' : ']';
    while (tokens[at] !== end) {
      if (start === '{') {
        const key = JSON.parse(tokens[at++]);
        if (seen.has(key)) fail('INVALID_ARGUMENT', 'Duplicate JSON field');
        seen.add(key); at++;
      }
      scan();
      if (tokens[at] === ',') at++;
    }
    at++;
  }
  try { scan(); } catch (error) {
    if (error.code) throw error;
    fail('INVALID_ARGUMENT', 'JSON nesting is too deep');
  }
  return value;
}
async function readBody(req) {
  if (!/^application\/json(?:\s*;\s*charset=utf-8)?$/i.test(req.headers['content-type'] ?? '')) {
    fail('INVALID_ARGUMENT', 'Content-Type must be application/json');
  }
  if (req.headers['content-encoding']) fail('INVALID_ARGUMENT', 'Encoded bodies are not supported');
  const declared = req.headers['content-length'];
  if (declared !== undefined && (!/^\d+$/.test(declared) || Number(declared) > 65536)) {
    fail('BODY_TOO_LARGE', 'JSON body exceeds 64 KiB', 413);
  }
  const bytes = await new Promise((resolve, reject) => {
    const buffers = [];
    let size = 0, overflow = false;
    req.on('data', part => {
      if (overflow) return;
      size += part.length;
      if (size > 65536) {
        overflow = true; buffers.length = 0;
        reject(Object.assign(new Error('JSON body exceeds 64 KiB'), { code: 'BODY_TOO_LARGE', status: 413 }));
      } else buffers.push(part);
    });
    req.once('end', () => { if (!overflow) resolve(Buffer.concat(buffers)); });
    req.once('error', reject);
    req.once('aborted', () => reject(Object.assign(new Error('Incomplete request body'), { code: 'INVALID_ARGUMENT', status: 400 })));
  });
  try { return parseJson(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
  catch (error) {
    if (error.status) throw error;
    fail('INVALID_ARGUMENT', 'Body must contain valid UTF-8 JSON');
  }
}

export function createMockServer(options = {}) {
  verifyContract();
  const { scenario = 'nominal', stepMs = 200, landingMs = 500, leaseMs = 10000,
    token, maxMissions = 128, maxRequests = 1024 } = options;
  if (!SCENARIOS.includes(scenario)) throw new TypeError('Unknown mock scenario');
  integer(stepMs, 'stepMs'); integer(landingMs, 'landingMs');
  integer(leaseMs, 'leaseMs', 60000);
  integer(maxMissions, 'maxMissions', 128); integer(maxRequests, 'maxRequests', 1024);
  if (token !== undefined && (typeof token !== 'string' || !/^[\x21-\x7e]{1,256}$/.test(token))) {
    throw new TypeError('Token must be 1..256 visible ASCII characters');
  }
  const missions = new Map(), admissions = new Map(), runtimes = new Map(), timers = new Set();
  let current = null, runCount = 0, droppedAck = false, closed = false;
  const active = () => missions.get(current);
  const touch = mission => { mission.updated_at_unix_ms = Date.now(); };
  function clear(timer) { clearTimeout(timer); timers.delete(timer); }
  function later(action, delay) {
    const timer = setTimeout(() => { timers.delete(timer); if (!closed) action(); }, delay);
    timers.add(timer); timer.unref();
    return timer;
  }
  function owned(mid, caller) {
    const mission = missions.get(mid);
    if (!mission) fail('NOT_FOUND', 'Mission not found', 404);
    if (mission.caller_id !== caller) fail('OWNER_MISMATCH', 'Use the original mission caller', 403);
    return mission;
  }
  function land(mission) {
    const runtime = runtimes.get(mission.mission_id);
    clear(runtime.landingTimer); clear(runtime.leaseTimer); clear(runtime.workTimer);
    runtime.airborne = false; runtime.connected = true;
    mission.ground_verified = true; mission.verification_pending = false;
    mission.state = mission.error_code ? 'failed' :
      mission.route_completed && !mission.stop_requested ? 'completed' : 'stopped';
    touch(mission);
    current = null;
  }
  function relinquish(mission, state) {
    const runtime = runtimes.get(mission.mission_id);
    clear(runtime.leaseTimer);
    runtime.relinquished = true;
    mission.state = state; mission.verification_pending = true; touch(mission);
    if (scenario !== 'manual-landing' && scenario !== 'connection-loss') {
      runtime.landingTimer = later(() => land(mission), landingMs);
    }
  }
  function stop(mission, reason) {
    const runtime = runtimes.get(mission.mission_id);
    if (current !== mission.mission_id || mission.stop_requested) return;
    clear(runtime.workTimer); clear(runtime.leaseTimer); clear(runtime.landingTimer);
    runtime.cancelled = true;
    mission.stop_requested = true; mission.stop_reason = reason; mission.state = 'stop_requested';
    touch(mission);
    runtime.workTimer = later(() => {
      if (!runtime.connected) relinquish(mission, 'outcome_unknown');
      else if (runtime.airborne) relinquish(mission, 'awaiting_rc_landing');
      else land(mission);
    }, stepMs);
  }
  function renew(mission) {
    const runtime = runtimes.get(mission.mission_id);
    if (runtime.cancelled || runtime.relinquished || current !== mission.mission_id) return;
    if (runtime.leaseDeadline && Date.now() >= runtime.leaseDeadline) {
      stop(mission, 'caller_lease_expired'); return;
    }
    clear(runtime.leaseTimer);
    runtime.leaseDeadline = Date.now() + leaseMs;
    runtime.leaseTimer = later(() => stop(mission, 'caller_lease_expired'), leaseMs);
  }
  function start(mission) {
    const runtime = runtimes.get(mission.mission_id), steps = [];
    const phase = state => { mission.state = state; };
    steps.push(() => phase('preflight'), () => {
      if (scenario === 'preflight-failure') {
        mission.error_code = 'PREFLIGHT_FAILED'; runtime.relinquished = true; land(mission);
      } else { runtime.airborne = true; phase('taking_off'); }
    }, () => phase('running'));
    for (const visit of mission.visits) {
      steps.push(() => { visit.state = 'moving'; }, () => {
        if (scenario === 'connection-loss') {
          runtime.connected = false; mission.error_code = 'CONNECTION_LOST';
          relinquish(mission, 'outcome_unknown');
        } else {
          visit.state = 'arrived'; visit.arrival_confirmed = true;
          mission.visited_ids.push(Number(visit.destination_id.slice(-1)));
        }
      });
      for (const ordinal of [1, 2]) steps.push(() => {
        if (scenario === 'camera-unavailable') {
          mission.error_code = 'CAMERA_UNAVAILABLE'; relinquish(mission, 'failed'); return;
        }
        const capture = createCapture(mission.mission_id, visit.visit_index, visit.destination_id, ordinal);
        capture.frame_generation = runCount;
        mission.captures.push(capture); visit.capture_ids.push(capture.capture_id); visit.state = 'captured';
      });
    }
    steps.push(() => phase('returning'), () => {
      mission.visited_ids.push(6);
      mission.route_completed = mission.visits.every(visit => visit.capture_ids.length === 2);
      relinquish(mission, 'awaiting_rc_landing');
    });
    let index = 0;
    function advance() {
      if (runtime.cancelled || runtime.relinquished || current !== mission.mission_id) return;
      if (Date.now() >= runtime.leaseDeadline) { stop(mission, 'caller_lease_expired'); return; }
      steps[index++](); touch(mission);
      if (index < steps.length && !runtime.relinquished && !runtime.cancelled && current === mission.mission_id) {
        runtime.workTimer = later(advance, stepMs);
      }
    }
    renew(mission);
    runtime.workTimer = later(advance, stepMs);
  }
  function status() {
    const mission = active(), runtime = mission && runtimes.get(current);
    const connected = runtime?.connected ?? true;
    return {
      connected, ground_verified: mission?.ground_verified ?? true,
      is_flying: connected ? (runtime?.airborne ?? false) : null,
      are_motors_on: connected ? (runtime?.airborne ?? false) : null,
      vs_enabled: connected ? Boolean(runtime?.airborne && !runtime.relinquished) : null,
      authority: !connected ? 'UNKNOWN' : runtime?.airborne && !runtime.relinquished ? 'MSDK' : 'RC',
      simulated: true, physical_stop_confirmed: false, state_source: 'contract_mock_simulation',
      flight_phase: mission?.state ?? 'idle', visited_ids: mission ? [...mission.visited_ids] : [],
      route_completed: mission?.route_completed ?? false,
    };
  }
  function dispatch(name, envelope) {
    const { arguments: args, caller_id: caller, request_id: request } = envelope;
    if (name === 'drone_get_capabilities') return success({
      live_ready: false, mock_capture_ready: true, expected_mode_guard: true,
      readiness_issues: [], adapter: 'contract-mock', profile_id: PROFILE_ID, site_revision: SITE_REVISION,
      home_tag_id: 6, floor_tag_id: 0, target_height_m: 1.5, tools: [...TOOL_NAMES],
      destinations: DESTINATIONS.map((id, index) => ({ destination_id: id, monitor_id: `monitor-${index + 1}`,
        physical_definition: { type: 'apriltag', marker_id: index + 1 } })),
      supported_ordered_sequences: detached(ROUTES),
    });
    if (name === 'drone_get_sensor_snapshot') return success({
      snapshot: { ...status(), ranges: null }, sensor_semantics: 'Synthetic state; ranges unavailable, no object classes',
    });
    if (name === 'drone_get_status') return success({
      ...status(), active_mission_id: current,
      active_mission: active() && active().caller_id === caller ? publicMission(active()) : null,
      active_request: active() && active().caller_id === caller ?
        { caller_id: caller, request_id: active().request_id, mission_id: current } : null,
    });
    if (name === 'drone_get_mission' || name === 'drone_get_captures') {
      const mission = owned(args.mission_id, caller);
      if (name === 'drone_get_captures') return success({ mission_id: mission.mission_id, captures: detached(mission.captures) });
      renew(mission);
      return success({ mission: publicMission(mission) });
    }
    const key = JSON.stringify([caller, request]), fingerprint = canonical([name, args]), old = admissions.get(key);
    if (old) {
      if (old.fingerprint !== fingerprint) fail('IDEMPOTENCY_CONFLICT', 'Request ID has different intent', 409);
      return detached(old.response);
    }
    if (admissions.size >= maxRequests) fail('HISTORY_LIMIT', 'Request history is full; restart the memory-only mock', 429);
    let response;
    if (name === 'drone_execute_route') {
      if (args.profile_id !== PROFILE_ID || args.site_revision !== SITE_REVISION) {
        fail('SITE_MISMATCH', 'Read current capabilities before execution');
      }
      if (!ROUTES.some(route => canonical(route) === canonical(args.destination_ids))) {
        fail('UNSUPPORTED_ROUTE', 'Visit each registered destination exactly once');
      }
      if (current) fail('MISSION_BUSY', 'Current mission has not finished simulated landing', 409);
      if (missions.size >= maxMissions) fail('HISTORY_LIMIT', 'Mission history is full; restart the memory-only mock', 429);
      const mission = {
        mission_id: randomUUID(), caller_id: caller, request_id: request, ...args,
        state: 'accepted', execution_mode: 'mock', physical_execution: false,
        simulated: true, stop_requested: false, physical_stop_confirmed: false,
        verification_pending: false, ground_verified: false, route_completed: false,
        visited_ids: [6], updated_at_unix_ms: Date.now(),
        visits: args.destination_ids.map((id, index) => ({ visit_index: index, destination_id: id,
          state: 'pending', arrival_confirmed: false, capture_ids: [] })), captures: [],
      };
      current = mission.mission_id; runCount++;
      missions.set(current, mission);
      runtimes.set(current, { airborne: false, connected: true, cancelled: false, relinquished: false });
      response = success({ mission: publicMission(mission) });
      start(mission);
    } else {
      const mission = owned(args.mission_id, caller);
      stop(mission, 'caller_requested');
      response = success({ mission: publicMission(mission), stop_requested: mission.stop_requested,
        physical_stop_confirmed: false });
    }
    admissions.set(key, { toolName: name, fingerprint, response: detached(response) });
    return response;
  }
  function header(req, name) {
    let count = 0;
    for (let i = 0; i < req.rawHeaders.length; i += 2) if (req.rawHeaders[i].toLowerCase() === name) count++;
    if (count > 1) fail('INVALID_ARGUMENT', `Duplicate ${name} header`);
    return req.headers[name];
  }
  const server = createServer({ maxHeaderSize: 16384, requestTimeout: 5000, headersTimeout: 5000 }, async (req, res) => {
    const requestedName = req.url.startsWith('/tools/') ? req.url.slice(7) : null;
    let responseName = TOOL_NAMES.includes(requestedName) ? requestedName : null;
    function send(code, body, raw = false) {
      if (!raw) {
        if (responseName) assertResponse(responseName, body);
        else validate(contract.$defs.response, body);
      }
      res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff' });
      res.end(raw ? body : JSON.stringify(body));
    }
    try {
      const origin = header(req, 'origin');
      if (origin !== undefined) {
        if (!/^http:\/\/(localhost|127\.0\.0\.1)(?::[1-9]\d{0,4})?$/.test(origin)) fail('FORBIDDEN', 'Origin is not a local HTTP frontend', 403);
        let url;
        try { url = new URL(origin); } catch { fail('FORBIDDEN', 'Invalid Origin', 403); }
        if (url.port && Number(url.port) > 65535) fail('FORBIDDEN', 'Invalid Origin port', 403);
        res.setHeader('Access-Control-Allow-Origin', origin); res.setHeader('Vary', 'Origin');
      }
      const path = req.url;
      if (!/^\/[A-Za-z0-9_./:-]*$/.test(path) || path.split('/').some(part => part === '.' || part === '..') || path.includes('//')) {
        fail('INVALID_ARGUMENT', 'Use an exact path without query, fragment or encoding');
      }
      const name = path.startsWith('/tools/') ? path.slice(7) : null;
      const lookup = /^\/requests\/([A-Za-z0-9_.:-]{1,128})\/([A-Za-z0-9_.:-]{1,128})$/.exec(path);
      const known = name ? TOOL_NAMES.includes(name) : Boolean(lookup || FILES[path] || ['/health', '/mock/status', '/mock/land'].includes(path));
      if (!known) fail('NOT_FOUND', 'Unknown endpoint', 404);
      if (req.method === 'OPTIONS') {
        if (!origin) fail('FORBIDDEN', 'Preflight requires a local Origin', 403);
        const method = header(req, 'access-control-request-method');
        const requested = (header(req, 'access-control-request-headers') ?? '').toLowerCase().split(',').map(s => s.trim()).filter(Boolean);
        if (method !== (name || path === '/mock/land' ? 'POST' : 'GET') ||
            requested.some(h => !['content-type', 'authorization', 'x-drone-expected-mode'].includes(h))) {
          fail('FORBIDDEN', 'Unsupported preflight method or headers', 403);
        }
        res.writeHead(204, { 'Access-Control-Allow-Methods': method,
          'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Drone-Expected-Mode', 'Cache-Control': 'no-store' });
        res.end(); return;
      }
      if (req.method !== (name || path === '/mock/land' ? 'POST' : 'GET')) fail('METHOD_NOT_ALLOWED', 'Unsupported method', 405);
      header(req, 'content-type'); header(req, 'content-length');
      const mode = header(req, 'x-drone-expected-mode'), auth = header(req, 'authorization');
      if (mode !== undefined && !['mock', 'live'].includes(mode)) fail('INVALID_ARGUMENT', 'Expected mode must be mock or live');
      if (mode === 'live') fail('MODE_MISMATCH', 'This server can only simulate; physical execution is impossible', 409);
      if ((name || lookup || path.startsWith('/mock/')) && token !== undefined && auth !== `Bearer ${token}`) {
        fail('UNAUTHORIZED', 'A valid mock Bearer token is required', 401);
      }
      if (req.method === 'GET' && (req.headers['transfer-encoding'] || Number(req.headers['content-length'] ?? 0) !== 0)) {
        fail('INVALID_ARGUMENT', 'GET endpoints do not accept bodies');
      }
      if (FILES[path]) { send(200, FILES[path], true); return; }
      if (path === '/health') { send(200, success({ service: 'contract-mock', simulated: true, hardware_connected: false })); return; }
      if (path === '/mock/status') {
        send(200, success({ ...status(), scenario, active_mission_id: current, run_count: runCount,
          mission_count: missions.size, request_count: admissions.size, storage: 'memory', reset_on_restart: true,
          hardware_connected: false })); return;
      }
      if (lookup) {
        const original = admissions.get(JSON.stringify([lookup[1], lookup[2]]));
        if (!original) fail('NOT_FOUND', 'No admission for this caller/request', 404);
        responseName = original.toolName;
        send(200, original.response); return;
      }
      const body = await readBody(req);
      if (closed) fail('SERVICE_SHUTTING_DOWN', 'Mock server is closing', 503);
      if (path === '/mock/land') {
        if (!body || typeof body !== 'object' || Array.isArray(body) ||
            Object.keys(body).sort().join(',') !== 'caller_id,mission_id' ||
            typeof body.caller_id !== 'string' || !ID.test(body.caller_id) ||
            typeof body.mission_id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(body.mission_id)) {
          fail('INVALID_ARGUMENT', 'Expected only mission_id and caller_id');
        }
        const mission = owned(body.mission_id, body.caller_id), runtime = runtimes.get(mission.mission_id);
        if (current !== mission.mission_id || !runtime.relinquished ||
            !['awaiting_rc_landing', 'outcome_unknown', 'failed'].includes(mission.state)) {
          fail('INVALID_STATE', 'Only the current relinquished simulated mission may land', 409);
        }
        land(mission);
        send(200, success({ mission_id: mission.mission_id, action: 'simulated_rc_landing',
          simulated: true, ground_verified: true, physical_stop_confirmed: false })); return;
      }
      try { assertRequest(name, body); } catch { fail('INVALID_ARGUMENT', 'Request does not match the fixed tool schema'); }
      const response = dispatch(name, body);
      assertResponse(name, response);
      if (scenario === 'lost-ack' && name === 'drone_execute_route' && !droppedAck) {
        droppedAck = true; req.socket.destroy(); return;
      }
      send(200, response);
    } catch (error) {
      if (!res.destroyed && !res.headersSent) send(error.status ?? 500, {
        ...base, ok: false, error: { code: error.status ? error.code : 'INTERNAL_ERROR',
          message: error.status ? error.message : 'Mock response validation or processing failed' },
      });
    }
  });
  server.on('close', () => { closed = true; for (const timer of timers) clearTimeout(timer); timers.clear(); });
  const listen = server.listen.bind(server);
  server.listen = (port = DEFAULT_PORT, host, callback) => {
    if (typeof host === 'function') { callback = host; host = undefined; }
    if (port && typeof port === 'object') {
      if (Object.keys(port).some(key => !['port', 'host'].includes(key))) throw new TypeError('Only TCP loopback listening is supported');
      host = port.host; port = port.port ?? DEFAULT_PORT;
    }
    validPort(port, true);
    if (host !== undefined && !['127.0.0.1', 'localhost'].includes(host)) throw new TypeError('Mock must listen on IPv4 loopback');
    return listen(port, '127.0.0.1', callback);
  };
  return server;
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  try {
    const options = {}, seen = new Set();
    let port = DEFAULT_PORT;
    const names = { '--port': 'port', '--scenario': 'scenario', '--step-ms': 'stepMs', '--landing-ms': 'landingMs', '--token': 'token' };
    for (let i = 2; i < process.argv.length; i += 2) {
      const flag = process.argv[i], value = process.argv[i + 1], name = names[flag];
      if (!name || seen.has(flag) || value === undefined || value.startsWith('--')) throw new TypeError('Invalid or duplicate CLI option');
      seen.add(flag);
      if (name === 'port') port = validPort(Number(value));
      else options[name] = ['stepMs', 'landingMs'].includes(name) ? Number(value) : value;
    }
    const server = createMockServer(options);
    server.on('error', error => { console.error(`Mock listen failed: ${error.code ?? 'ERROR'}`); process.exitCode = 1; });
    server.listen(port, () => console.log(`Contract mock: http://127.0.0.1:${port} (${options.scenario ?? 'nominal'}); simulation only, memory resets on restart`));
    for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => server.close());
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
