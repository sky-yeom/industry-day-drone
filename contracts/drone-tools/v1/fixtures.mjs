import { createHash } from 'node:crypto';
import { deflateSync } from 'node:zlib';

export const WIDTH = 640;
export const HEIGHT = 360;
const LETTERS = [
  ['10001', '11011', '10101', '10101', '10001', '10001', '10001'],
  ['01110', '10001', '10001', '10001', '10001', '10001', '01110'],
  ['01111', '10000', '10000', '10000', '10000', '10000', '01111'],
  ['10001', '10010', '10100', '11000', '10100', '10010', '10001'],
];

function chunk(type, data) {
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  let crc = 0xffffffff;
  for (const byte of body) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  const result = Buffer.alloc(data.length + 12);
  result.writeUInt32BE(data.length);
  body.copy(result, 4);
  result.writeUInt32BE((crc ^ 0xffffffff) >>> 0, result.length - 4);
  return result;
}

// Original, generated artwork: a color field, identity stripe and large MOCK label.
export function createCapture(missionId, visitIndex, destinationId, ordinal) {
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(missionId) ||
      !Number.isInteger(visitIndex) || visitIndex < 0 || visitIndex > 2 ||
      !/^tag-[123]$/.test(destinationId) || ![1, 2].includes(ordinal)) {
    throw new TypeError('Invalid synthetic capture identity');
  }
  const captureId = `${missionId}:${visitIndex}:${ordinal}`;
  const identity = createHash('sha256').update(captureId).digest();
  const tag = Number(destinationId.slice(-1));
  const raw = Buffer.alloc((WIDTH * 3 + 1) * HEIGHT);
  for (let y = 0; y < HEIGHT; y++) {
    for (let x = 0; x < WIDTH; x++) {
      const at = y * (WIDTH * 3 + 1) + 1 + x * 3;
      let rgb = [30 + tag * 35, 35 + ordinal * 35, 70 + ((x >> 5) + (y >> 5)) * 3];
      if (y > 315) rgb = [identity[Math.floor(x / 20)], 50 + ordinal * 50, tag * 60];
      const lx = x - 88, ly = y - 112;
      const letter = Math.floor(lx / 120), col = Math.floor((lx % 120) / 20), row = Math.floor(ly / 20);
      if (lx >= 0 && ly >= 0 && letter < 4 && col < 5 && row < 7 &&
          LETTERS[letter][row][col] === '1') rgb = [255, 255, 255];
      raw.set(rgb, at);
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(WIDTH); ihdr.writeUInt32BE(HEIGHT, 4);
  ihdr[8] = 8; ihdr[9] = 2;
  const png = Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', ihdr),
    chunk('tEXt', Buffer.from(`Description\0MOCK synthetic fixture ${captureId}`)),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
  const hash = createHash('sha256').update(png).digest('hex');
  return {
    capture_id: captureId, mission_id: missionId, visit_index: visitIndex,
    destination_id: destinationId, monitor_id: `monitor-${tag}`,
    arrival_confirmed: true, image_base64: png.toString('base64'), content_type: 'image/png',
    captured_at_unix_ms: Date.now(), frame_generation: 1, frame_id: visitIndex * 2 + ordinal,
    simulated: true, capture_source: 'synthetic_fixture', fixture_sha256: hash,
    sha256: hash, image_sha256: hash, fixture_variant: `synthetic-${ordinal}`, width: WIDTH, height: HEIGHT,
    aircraft_exposure_timestamp_available: false, tv_visibility_verified: false,
    framing_mode: 'simulated_fixture', physical_stop_confirmed: false,
  };
}
