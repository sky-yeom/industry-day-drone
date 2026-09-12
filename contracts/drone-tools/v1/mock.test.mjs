import test from 'node:test';
import assert from 'node:assert/strict';
import { request as httpRequest } from 'node:http';
import { once } from 'node:events';
import { createHash, randomUUID } from 'node:crypto';
import { inflateSync } from 'node:zlib';
import { spawnSync } from 'node:child_process';
import { appendFileSync, cpSync, mkdirSync, rmSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { createMockServer, DEFAULT_PORT, PROFILE_ID, SITE_REVISION, SCENARIOS } from './mock.mjs';
import { assertResponse, validate, contract, tools } from './validate.mjs';

const folder = fileURLToPath(new URL('.', import.meta.url));
const CALLER = 'frontend.contract:1';
const envelope = (args = {}, request = randomUUID(), caller = CALLER) =>
  ({ arguments: args, caller_id: caller, request_id: request });
const routeArgs = (ids = ['tag-1', 'tag-2', 'tag-3']) =>
  ({ profile_id: PROFILE_ID, site_revision: SITE_REVISION, destination_ids: ids });
const validStates = new Set(['accepted', 'preflight', 'taking_off', 'running', 'returning',
  'awaiting_rc_landing', 'completed', 'stop_requested', 'stopped', 'failed', 'outcome_unknown']);

async function fixture(t, options = {}) {
  const server = createMockServer({ stepMs: 8, landingMs: 35, ...options });
  server.listen(0);
  await once(server, 'listening');
  t.after(() => new Promise(resolve => server.close(resolve)));
  const url = `http://127.0.0.1:${server.address().port}`;
  async function send(path, body, headers = {}, method = body === undefined ? 'GET' : 'POST') {
    const response = await fetch(url + path, { method, headers: {
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}), ...headers,
    }, body: body === undefined ? undefined : JSON.stringify(body) });
    const result = { code: response.status, headers: response.headers,
      body: response.status === 204 ? null : await response.json() };
    if (result.body && !['/tools.json', '/contract.schema.json'].includes(path)) validate(contract.$defs.response, result.body);
    return result;
  }
  async function call(name, args = {}, request, caller = CALLER, headers) {
    const result = await send(`/tools/${name}`, envelope(args, request, caller), headers);
    assertResponse(name, result.body);
    assert.equal(result.body.execution_mode, 'mock');
    assert.equal(result.body.physical_execution, false);
    if (result.body.mission) {
      assert.ok(validStates.has(result.body.mission.state));
      assert.equal(result.body.mission.physical_stop_confirmed, false);
      assert.ok(result.body.mission.visits.every(visit => ['pending', 'moving', 'arrived', 'captured'].includes(visit.state)));
    }
    return result;
  }
  async function poll(mid, predicate, timeout = 4000) {
    const deadline = Date.now() + timeout, seen = new Set();
    while (Date.now() < deadline) {
      const result = await call('drone_get_mission', { mission_id: mid });
      assert.equal(result.code, 200);
      const mission = result.body.mission;
      seen.add(mission.state);
      if (predicate(mission)) return { mission, seen };
      await sleep(3);
    }
    assert.fail(`Mission did not reach condition; observed: ${[...seen].join(', ')}`);
  }
  async function execute(ids, request) {
    const caps = await call('drone_get_capabilities');
    const args = { profile_id: caps.body.profile_id, site_revision: caps.body.site_revision,
      destination_ids: ids ?? caps.body.supported_ordered_sequences[0] };
    const result = await call('drone_execute_route', args, request);
    assert.equal(result.code, 200);
    assert.equal(result.body.mission.state, 'accepted');
    return result.body.mission;
  }
  return { server, url, send, call, poll, execute };
}

function verifyPng(capture) {
  const bytes = Buffer.from(capture.image_base64, 'base64');
  assert.equal(bytes.toString('base64'), capture.image_base64);
  assert.deepEqual([...bytes.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
  assert.equal(bytes.readUInt32BE(16), 640); assert.equal(bytes.readUInt32BE(20), 360);
  assert.equal(bytes[24], 8); assert.equal(bytes[25], 2);
  const idat = [];
  for (let at = 8; at < bytes.length;) {
    const length = bytes.readUInt32BE(at), data = bytes.subarray(at + 4, at + 8 + length);
    let crc = 0xffffffff;
    for (const byte of data) {
      crc ^= byte;
      for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
    assert.equal(bytes.readUInt32BE(at + 8 + length), (crc ^ 0xffffffff) >>> 0);
    if (data.subarray(0, 4).toString() === 'IDAT') idat.push(data.subarray(4));
    at += 12 + length;
  }
  const pixels = inflateSync(Buffer.concat(idat));
  assert.equal(pixels.length, (640 * 3 + 1) * 360);
  const visibleM = 112 * (640 * 3 + 1) + 1 + 88 * 3;
  assert.deepEqual([...pixels.subarray(visibleM, visibleM + 3)], [255, 255, 255]);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), capture.sha256);
  assert.equal(capture.fixture_sha256, capture.sha256);
  assert.equal(capture.simulated, true); assert.equal(capture.capture_source, 'synthetic_fixture');
  assert.equal(capture.aircraft_exposure_timestamp_available, false);
  assert.equal(capture.physical_stop_confirmed, false);
  assert.equal(capture.fixture_path, undefined);
}

test('seven frozen tools, static documents and explicitly nonphysical capabilities', async t => {
  const api = await fixture(t);
  assert.equal(DEFAULT_PORT, 18767);
  assert.equal(SCENARIOS.length, 6);
  for (const name of ['tools.json', 'contract.schema.json']) {
    const response = await api.send(`/${name}`);
    assert.equal(response.code, 200);
    assert.deepEqual(response.body, JSON.parse(readFileSync(join(folder, name), 'utf8')));
  }
  const caps = (await api.call('drone_get_capabilities')).body;
  assert.equal(caps.live_ready, false); assert.equal(caps.mock_capture_ready, true);
  assert.equal(caps.expected_mode_guard, true); assert.equal(caps.adapter, 'contract-mock');
  assert.equal(caps.profile_id, PROFILE_ID); assert.equal(caps.site_revision, SITE_REVISION);
  assert.equal(caps.home_tag_id, 6); assert.equal(caps.floor_tag_id, 0); assert.equal(caps.target_height_m, 1.5);
  assert.deepEqual(caps.tools, tools.map(tool => tool.name));
  assert.deepEqual(caps.destinations.map(d => [d.destination_id, d.monitor_id, d.physical_definition]),
    [1, 2, 3].map(n => [`tag-${n}`, `monitor-${n}`, { type: 'apriltag', marker_id: n }]));
  const status = (await api.call('drone_get_status')).body;
  assert.equal(status.active_mission_id, null);
  assert.equal(status.physical_stop_confirmed, false); assert.equal(status.simulated, true);
  const sensor = (await api.call('drone_get_sensor_snapshot')).body;
  assert.equal(sensor.snapshot.ranges, null); assert.equal(sensor.snapshot.simulated, true);
  const health = (await api.send('/health')).body;
  assert.equal(health.hardware_connected, false); assert.equal(health.physical_execution, false);
});

test('all six routes return home with two distinct attributed, decodable PNGs per visit', async t => {
  const api = await fixture(t);
  const routes = (await api.call('drone_get_capabilities')).body.supported_ordered_sequences;
  const allHashes = new Set(), allIds = new Set();
  for (const ids of routes) {
    const admission = await api.execute(ids);
    const { mission } = await api.poll(admission.mission_id, m => m.state === 'completed');
    assert.deepEqual(mission.visited_ids, [6, ...ids.map(id => Number(id.slice(-1))), 6]);
    assert.equal(mission.route_completed, true); assert.equal(mission.ground_verified, true);
    assert.equal(mission.verification_pending, false);
    const captures = (await api.call('drone_get_captures', { mission_id: mission.mission_id })).body.captures;
    assert.equal(captures.length, 6);
    for (const [index, visit] of mission.visits.entries()) {
      assert.equal(visit.state, 'captured'); assert.equal(visit.arrival_confirmed, true);
      assert.equal(visit.capture_ids.length, 2);
      const frames = captures.filter(capture => capture.visit_index === index);
      assert.deepEqual(frames.map(capture => capture.capture_id), visit.capture_ids);
      for (const capture of frames) {
        assert.equal(capture.mission_id, mission.mission_id); assert.equal(capture.destination_id, ids[index]);
        assert.equal(capture.monitor_id, `monitor-${ids[index].slice(-1)}`);
        verifyPng(capture);
        assert.ok(!allHashes.has(capture.sha256)); allHashes.add(capture.sha256);
        assert.ok(!allIds.has(capture.capture_id)); allIds.add(capture.capture_id);
      }
    }
  }
  assert.equal(allHashes.size, 36);
  assert.equal((await api.send('/mock/status')).body.run_count, 6);
});

test('observable finite stages, original admission lookup and write idempotency never replay', async t => {
  // Leave enough time for HTTP polling on loaded Windows development machines.
  const api = await fixture(t, { stepMs: 150, landingMs: 250 });
  const request = 'original.admission:1';
  const admitted = await api.call('drone_execute_route', routeArgs(), request);
  const mid = admitted.body.mission.mission_id;
  const progress = api.poll(mid, m => m.state === 'completed', 8000);
  const reordered = { destination_ids: routeArgs().destination_ids, site_revision: SITE_REVISION, profile_id: PROFILE_ID };
  assert.deepEqual((await api.call('drone_execute_route', reordered, request)).body, admitted.body);
  assert.equal((await api.call('drone_execute_route', routeArgs(['tag-3', 'tag-2', 'tag-1']), request)).code, 409);
  assert.equal((await api.call('drone_stop_mission', { mission_id: mid }, request)).body.error.code, 'IDEMPOTENCY_CONFLICT');
  assert.equal((await api.call('drone_execute_route', routeArgs())).body.error.code, 'MISSION_BUSY');
  assert.equal((await api.send(`/requests/other/${request}`)).code, 404);
  const { seen } = await progress;
  for (const state of ['preflight', 'taking_off', 'running', 'returning', 'awaiting_rc_landing', 'completed']) {
    assert.ok(seen.has(state), `Stage ${state} must be observable`);
  }
  assert.deepEqual((await api.send(`/requests/${CALLER}/${request}`)).body, admitted.body);
  assert.deepEqual((await api.call('drone_execute_route', routeArgs(), request)).body, admitted.body);
  assert.equal((await api.send('/mock/status')).body.run_count, 1);
});

test('manual landing holds occupancy and rejects early, wrong-owner and old-mission landing', async t => {
  const api = await fixture(t, { scenario: 'manual-landing' });
  const m = await api.execute();
  const land = caller_id => api.send('/mock/land', { mission_id: m.mission_id, caller_id });
  assert.equal((await land(CALLER)).code, 409);
  await api.poll(m.mission_id, mission => mission.state === 'awaiting_rc_landing');
  await sleep(80);
  assert.equal((await api.call('drone_execute_route', routeArgs())).body.error.code, 'MISSION_BUSY');
  assert.equal((await land('another-caller')).code, 403);
  const result = await land(CALLER);
  assert.equal(result.body.action, 'simulated_rc_landing'); assert.equal(result.body.physical_stop_confirmed, false);
  await api.poll(m.mission_id, mission => mission.state === 'completed');
  assert.equal((await land(CALLER)).code, 409);
  const next = await api.execute();
  assert.notEqual(next.mission_id, m.mission_id);
  assert.equal((await land(CALLER)).code, 409);
});

test('preflight failure never takes off or captures; camera failure preserves valid arrived visit', async t => {
  const preflight = await fixture(t, { scenario: 'preflight-failure' });
  const first = await preflight.execute();
  const failed = (await preflight.poll(first.mission_id, m => m.state === 'failed')).mission;
  assert.equal(failed.error_code, 'PREFLIGHT_FAILED'); assert.equal(failed.ground_verified, true);
  assert.deepEqual(failed.visited_ids, [6]); assert.equal(failed.route_completed, false);
  assert.ok(failed.visits.every(v => v.state === 'pending'));
  assert.deepEqual((await preflight.call('drone_get_captures', { mission_id: first.mission_id })).body.captures, []);
  const camera = await fixture(t, { scenario: 'camera-unavailable', landingMs: 120 });
  const second = await camera.execute();
  const cameraFailed = (await camera.poll(second.mission_id, m => m.state === 'failed')).mission;
  assert.equal(cameraFailed.error_code, 'CAMERA_UNAVAILABLE'); assert.equal(cameraFailed.route_completed, false);
  assert.equal(cameraFailed.visits[0].state, 'arrived'); assert.equal(cameraFailed.verification_pending, true);
  assert.deepEqual((await camera.call('drone_get_captures', { mission_id: second.mission_id })).body.captures, []);
  assert.equal((await camera.call('drone_execute_route', routeArgs())).code, 409);
  await camera.poll(second.mission_id, m => m.ground_verified);
  assert.equal((await camera.call('drone_get_status')).body.active_mission_id, null);
});

test('connection loss remains unknown with no automatic replay until explicit simulated reset', async t => {
  const api = await fixture(t, { scenario: 'connection-loss' });
  const m = await api.execute();
  const unknown = (await api.poll(m.mission_id, mission => mission.state === 'outcome_unknown')).mission;
  assert.equal(unknown.error_code, 'CONNECTION_LOST'); assert.equal(unknown.route_completed, false);
  const status = (await api.call('drone_get_status')).body;
  assert.equal(status.connected, false); assert.equal(status.is_flying, null); assert.equal(status.are_motors_on, null);
  await sleep(120);
  assert.deepEqual((await api.call('drone_get_mission', { mission_id: m.mission_id })).body.mission, unknown);
  assert.equal((await api.call('drone_execute_route', routeArgs())).code, 409);
  assert.equal((await api.send('/mock/status')).body.run_count, 1);
  await api.send('/mock/land', { mission_id: m.mission_id, caller_id: CALLER });
  assert.equal((await api.call('drone_get_status')).body.active_mission_id, null);
});

test('lost acknowledgement preserves admission in memory and recovery never starts a second mission', async t => {
  const api = await fixture(t, { scenario: 'lost-ack' });
  const request = 'lost-ack:1';
  await assert.rejects(api.call('drone_execute_route', routeArgs(), request));
  const lookup = await api.send(`/requests/${CALLER}/${request}`);
  assert.equal(lookup.code, 200); assert.equal(lookup.body.mission.state, 'accepted');
  assert.deepEqual((await api.call('drone_execute_route', routeArgs(), request)).body, lookup.body);
  const mid = lookup.body.mission.mission_id;
  await api.poll(mid, m => m.state === 'completed');
  assert.deepEqual((await api.send(`/requests/${CALLER}/${request}`)).body, lookup.body);
  assert.deepEqual((await api.call('drone_execute_route', routeArgs(), request)).body, lookup.body);
  assert.equal((await api.send('/mock/status')).body.run_count, 1);
});

test('stop is caller-owned, idempotent, nonphysical and prevents any later route progress', async t => {
  const api = await fixture(t, { scenario: 'manual-landing', stepMs: 20 });
  const m = await api.execute();
  await api.poll(m.mission_id, mission => mission.visits[0].arrival_confirmed);
  for (const name of ['drone_get_mission', 'drone_get_captures', 'drone_stop_mission']) {
    assert.equal((await api.call(name, { mission_id: m.mission_id }, undefined, 'other')).code, 403);
  }
  const hidden = (await api.call('drone_get_status', {}, undefined, 'other')).body;
  assert.equal(hidden.active_mission, null); assert.equal(hidden.active_request, null);
  const request = 'stop-original';
  const stopped = await api.call('drone_stop_mission', { mission_id: m.mission_id }, request);
  assert.equal(stopped.body.stop_requested, true); assert.equal(stopped.body.physical_stop_confirmed, false);
  assert.equal(stopped.body.mission.state, 'stop_requested');
  const stoppedIds = stopped.body.mission.visited_ids, captureIds = stopped.body.mission.visits.flatMap(v => v.capture_ids);
  await api.poll(m.mission_id, mission => mission.state === 'awaiting_rc_landing');
  await sleep(100);
  const current = (await api.call('drone_get_mission', { mission_id: m.mission_id })).body.mission;
  assert.deepEqual(current.visited_ids, stoppedIds);
  assert.deepEqual(current.visits.flatMap(v => v.capture_ids), captureIds); assert.equal(current.route_completed, false);
  assert.deepEqual((await api.call('drone_stop_mission', { mission_id: m.mission_id }, request)).body, stopped.body);
  await api.send('/mock/land', { mission_id: m.mission_id, caller_id: CALLER });
  assert.equal((await api.call('drone_get_mission', { mission_id: m.mission_id })).body.mission.state, 'stopped');
});

test('only owner get_mission renews lease; expiration cancels without replay', async t => {
  const api = await fixture(t, { scenario: 'manual-landing', stepMs: 10, leaseMs: 90 });
  const m = await api.execute();
  for (let i = 0; i < 5; i++) {
    await api.call('drone_get_mission', { mission_id: m.mission_id }, undefined, 'other');
    await api.call('drone_get_status');
    await sleep(25);
  }
  const expired = (await api.call('drone_get_mission', { mission_id: m.mission_id })).body.mission;
  assert.equal(expired.stop_requested, true); assert.equal(expired.stop_reason, 'caller_lease_expired');
  assert.equal(expired.route_completed, false); assert.equal(expired.state, 'awaiting_rc_landing');
  await sleep(100);
  const later = (await api.call('drone_get_mission', { mission_id: m.mission_id })).body.mission;
  assert.deepEqual(later.visited_ids, expired.visited_ids); assert.deepEqual(later.visits, expired.visits);
  assert.equal((await api.send('/mock/status')).body.run_count, 1);
  const renewed = await fixture(t, { stepMs: 10, leaseMs: 90 });
  const running = await renewed.execute();
  const complete = (await renewed.poll(running.mission_id, mission => mission.state === 'completed')).mission;
  assert.equal(complete.stop_requested, false);
});

test('fixed envelope/argument schemas, malformed JSON, body bounds and paths fail explicitly', async t => {
  const api = await fixture(t);
  const badBodies = [null, [], {}, { ...envelope(), extra: true }, envelope(null), envelope([]),
    envelope({ extra: true }), { ...envelope(), caller_id: '' }, { ...envelope(), request_id: 'x'.repeat(129) },
    { ...envelope(), caller_id: 1 }, { ...envelope(), request_id: 'not/allowed' }];
  for (const body of badBodies) assert.equal((await api.send('/tools/drone_get_status', body)).code, 400);
  for (const args of [{ ...routeArgs(), destination_ids: 'tag-1' }, { ...routeArgs(), scenario: 'nominal' },
    { ...routeArgs(), profile_id: 1 }, { ...routeArgs(), destination_ids: ['tag-1', 'tag-1', 'tag-3'] },
    { ...routeArgs(), destination_ids: ['tag-1', 'tag-2'] }, { ...routeArgs(), profile_id: 'stale' },
    { ...routeArgs(), site_revision: 'stale' }]) {
    assert.equal((await api.call('drone_execute_route', args)).code, 400);
  }
  for (const text of ['{', '', '{"arguments":{},"arguments":{},"caller_id":"c","request_id":"r"}',
    '{"arguments":{"x":1,"x":2},"caller_id":"c","request_id":"r"}',
    '{"arguments":{},"caller_id":"c","request_id":"r","request\\u005fid":"r"}']) {
    const response = await fetch(`${api.url}/tools/drone_get_status`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: text,
    });
    assert.equal(response.status, 400);
  }
  assert.equal((await api.send('/tools/drone_get_status', envelope(), { 'Content-Type': 'text/plain' })).code, 400);
  const oversized = await fetch(`${api.url}/tools/drone_get_status`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: ' '.repeat(65537),
  });
  assert.equal(oversized.status, 413);
  for (const path of ['/tools/unknown', '/tools.json/extra', '/mock/unknown']) assert.equal((await api.send(path)).code, 404);
  for (const path of ['/tools.json?x=1', '/requests/bad%2Fcaller/r']) assert.equal((await api.send(path)).code, 400);
  assert.equal((await api.send('/tools/drone_get_status')).code, 405);
  assert.equal((await api.send('/health', {})).code, 405);
  assert.equal((await api.call('drone_get_mission', { mission_id: 'missing' })).code, 404);
  assert.equal((await raw(api, '/tools/drone_get_status', [], ' '.repeat(65537), 'POST', true)).code, 413);
  assert.equal((await raw(api, '/tools/drone_get_status', [], Buffer.from([0xff, 0xfe]))).code, 400);
  assert.equal((await raw(api, '/anything/../tools.json', [], '', 'GET')).code, 400);
  const dots = await api.call('drone_execute_route', routeArgs(), 'request..1', 'caller..1');
  assert.equal(dots.code, 200);
  assert.deepEqual((await api.send('/requests/caller..1/request..1')).body, dots.body);
});

function raw(api, path, headers, body = '{}', method = 'POST', chunked = false) {
  return new Promise((resolve, reject) => {
    const req = httpRequest(api.url, { path, method, headers: [
      'Host', new URL(api.url).host, 'Connection', 'close', 'Content-Type', 'application/json',
      ...(chunked ? ['Transfer-Encoding', 'chunked'] : ['Content-Length', String(Buffer.byteLength(body))]), ...headers,
    ] }, res => {
      let text = '';
      res.setEncoding('utf8'); res.on('data', chunk => { text += chunk; });
      res.on('end', () => resolve({ code: res.statusCode, body: JSON.parse(text), headers: res.headers }));
    });
    req.on('error', reject); req.end(body);
  });
}

test('mode guard precedes admission; duplicate and invalid headers cannot bypass mock-only dispatch', async t => {
  const api = await fixture(t);
  const result = await api.call('drone_execute_route', routeArgs(), undefined, CALLER, { 'X-Drone-Expected-Mode': 'live' });
  assert.equal(result.code, 409); assert.equal(result.body.error.code, 'MODE_MISMATCH');
  for (const value of ['Live', 'mock, live', '', 'physical']) {
    assert.equal((await api.call('drone_execute_route', routeArgs(), undefined, CALLER,
      { 'X-Drone-Expected-Mode': value })).code, 400);
  }
  for (const headers of [
    ['X-Drone-Expected-Mode', 'mock', 'X-Drone-Expected-Mode', 'mock'],
    ['X-Drone-Expected-Mode', 'mock', 'X-Drone-Expected-Mode', 'live'],
    ['Authorization', 'Bearer a', 'Authorization', 'Bearer b'],
    ['Origin', 'http://localhost:5173', 'Origin', 'http://localhost:5173'],
  ]) assert.equal((await raw(api, '/tools/drone_execute_route', headers, JSON.stringify(envelope(routeArgs())))).code, 400);
  assert.equal((await api.send('/mock/status')).body.run_count, 0);
});

test('optional mock bearer auth and local-only CORS support independent browser fetch without token leakage', async t => {
  const api = await fixture(t, { token: 'mock-test-only-value' });
  assert.equal((await api.call('drone_get_status', {}, undefined, CALLER, { Authorization: '' })).code, 401);
  assert.equal((await api.send(`/requests/${CALLER}/missing`, undefined, { Authorization: 'Bearer wrong' })).code, 401);
  const authorized = await api.call('drone_get_capabilities', {}, undefined, CALLER, { Origin: 'http://localhost:5173' });
  assert.equal(authorized.code, 200); assert.equal(authorized.headers.get('access-control-allow-origin'), 'http://localhost:5173');
  assert.equal(authorized.headers.get('access-control-allow-credentials'), null);
  assert.ok(!JSON.stringify(authorized.body).includes('mock-test-only-value'));
  const preflight = await api.send('/tools/drone_get_status', undefined, {
    Origin: 'http://127.0.0.1:5173', Authorization: '', 'Access-Control-Request-Method': 'POST',
    'Access-Control-Request-Headers': 'content-type,authorization,x-drone-expected-mode',
  }, 'OPTIONS');
  assert.equal(preflight.code, 204);
  assert.equal(preflight.headers.get('access-control-allow-origin'), 'http://127.0.0.1:5173');
  for (const origin of ['https://localhost:5173', 'http://evil.example', 'null', 'http://localhost.evil:5173',
    'http://localhost:99999', 'http://user@localhost:5173', 'http://localhost:5173/path', '']) {
    const rejected = await api.call('drone_get_status', {}, undefined, CALLER, { Origin: origin });
    assert.equal(rejected.code, 403); assert.equal(rejected.headers.get('access-control-allow-origin'), null);
  }
  const disallowed = await api.send('/tools/drone_get_status', undefined, {
    Origin: 'http://localhost:5173', 'Access-Control-Request-Method': 'POST',
    'Access-Control-Request-Headers': 'x-unrecognized',
  }, 'OPTIONS');
  assert.equal(disallowed.code, 403);
  assert.equal((await api.send('/mock/land', { mission_id: 'missing', caller_id: CALLER }, { Authorization: '' })).code, 401);
});

test('bounded memory refuses new admissions explicitly without evicting originals', async t => {
  const api = await fixture(t, { maxMissions: 1 });
  const request = 'bounded';
  const m = await api.execute(undefined, request);
  await api.poll(m.mission_id, mission => mission.state === 'completed');
  assert.equal((await api.call('drone_execute_route', routeArgs())).body.error.code, 'HISTORY_LIMIT');
  assert.equal((await api.send(`/requests/${CALLER}/${request}`)).body.mission.mission_id, m.mission_id);
  const requests = await fixture(t, { maxRequests: 1 });
  const first = await requests.execute(undefined, request);
  await requests.poll(first.mission_id, mission => mission.state === 'completed');
  assert.equal((await requests.call('drone_execute_route', routeArgs())).code, 429);
  assert.equal((await requests.call('drone_execute_route', routeArgs(), request)).code, 200);
});

test('API and CLI reject reserved/non-loopback ports, bad scenario and unbounded timing', () => {
  for (const options of [{ stepMs: 0 }, { stepMs: NaN }, { landingMs: Infinity }, { leaseMs: 0 },
    { scenario: 'live' }, { maxMissions: 129 }, { maxRequests: 1025 }, { token: '' }]) {
    assert.throws(() => createMockServer(options), TypeError);
  }
  const server = createMockServer();
  for (const port of [8766, 9997, 9998, 9999, -1, 65536, '18767']) assert.throws(() => server.listen(port), TypeError);
  assert.throws(() => server.listen(0, '0.0.0.0'), TypeError);
  assert.throws(() => server.listen({ path: 'socket' }), TypeError);
  for (const args of [['--port', '8766'], ['--port', '9997'], ['--port', '9998'], ['--port', '9999'],
    ['--port', '0'], ['--port', '-1'], ['--port', '65536'], ['--port', 'abc'], ['--port', '1.5'],
    ['--scenario', 'live'], ['--step-ms', '0'], ['--landing-ms', 'Infinity'], ['--token', ''],
    ['--port', '18767', '--port', '18768'], ['--unknown', 'x'], ['--scenario']]) {
    const child = spawnSync(process.execPath, [join(folder, 'mock.mjs'), ...args], { encoding: 'utf8', timeout: 5000 });
    assert.equal(child.status, 1, `${args.join(' ')}: ${child.stderr}`);
  }
});

test('close clears mission timers and copied folder needs only Node and its local contract files', () => {
  const script = `
    import { createMockServer } from './mock.mjs';
    const server = createMockServer({stepMs:30000, landingMs:30000});
    await new Promise(resolve => server.listen(0, resolve));
    const url = 'http://127.0.0.1:' + server.address().port;
    const response = await fetch(url + '/tools/drone_execute_route', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
        arguments:{profile_id:'contract-mock-v1',site_revision:'v1',destination_ids:['tag-1','tag-2','tag-3']},
        caller_id:'copy-test',request_id:'copy-test'
      })
    });
    const body = await response.json();
    if (response.status !== 200 || body.physical_execution !== false) throw new Error('Bad copy response');
    await new Promise(resolve => server.close(resolve));
    console.log('copy verified');
  `;
  const copy = join(folder, `.mock-isolation-${randomUUID()}`);
  mkdirSync(copy);
  try {
    for (const name of ['mock.mjs', 'fixtures.mjs', 'validate.mjs', 'verify.mjs', 'contract.lock.json',
      'tools.json', 'contract.schema.json', 'types.d.ts']) {
      cpSync(join(folder, name), join(copy, name));
    }
    const child = spawnSync(process.execPath, ['--input-type=module', '--eval', script], {
      cwd: copy, encoding: 'utf8', timeout: 5000, env: { ...process.env, NODE_PATH: '' },
    });
    assert.equal(child.status, 0, child.stderr);
    assert.match(child.stdout, /copy verified/);
    appendFileSync(join(copy, 'types.d.ts'), '\n// Deliberate isolated-copy contract mutation.\n');
    const altered = spawnSync(process.execPath, ['--input-type=module', '--eval', script], {
      cwd: copy, encoding: 'utf8', timeout: 5000, env: { ...process.env, NODE_PATH: '' },
    });
    assert.equal(altered.status, 1);
    assert.match(altered.stderr, /Frozen v1\.0\.0 artifact changed: types\.d\.ts/);
  } finally { rmSync(copy, { recursive: true, force: true }); }
});
