export const RESULTS_SPRITE_WIDTH = 520;
export const RESULTS_SPRITE_HEIGHT = 360;
export const RIDING_FRAMES = Array.from({ length: 9 }, (_, i) => `/gibby/drone-ride-${i + 1}.png`);
export const RETURN_BOARDING_FRAMES = Array.from({ length: 4 }, (_, i) => `/gibby/drone-return-${i + 1}.png`);
export const CELEBRATION_FRAMES = Array.from({ length: 6 }, (_, i) => `/gibby/celebrate-${i + 1}.png`);
export const RESULTS_IDLE = "/gibby/results-idle.png";
export const PARKED_DRONE = "/gibby/drone-parked.png";
export const RESULTS_ASSETS = [...RIDING_FRAMES, ...RETURN_BOARDING_FRAMES, ...CELEBRATION_FRAMES, RESULTS_IDLE, PARKED_DRONE];

export interface MarkerRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export const RETURN_FLIGHT_MS = 1600;
const HANDOFF_MS = 80;
const DISMOUNT_FRAME_MS = 300;
const IDLE_HOLD_MS = 250;
const JUMP_FRAME_MS = 160;

export function returnFrame(elapsed: number, celebrate: boolean) {
  if (elapsed < HANDOFF_MS) return { phase: "handoff", src: RETURN_BOARDING_FRAMES[3], parked: false };
  elapsed -= HANDOFF_MS;
  if (elapsed < RETURN_FLIGHT_MS) {
    return { phase: "flying", src: RIDING_FRAMES[Math.floor(elapsed / 120) % RIDING_FRAMES.length], parked: false };
  }
  elapsed -= RETURN_FLIGHT_MS;
  if (elapsed < DISMOUNT_FRAME_MS * 4) {
    return { phase: "dismounting", src: RETURN_BOARDING_FRAMES[3 - Math.floor(elapsed / DISMOUNT_FRAME_MS)], parked: false };
  }
  elapsed -= DISMOUNT_FRAME_MS * 4;
  if (elapsed < IDLE_HOLD_MS) return { phase: "idle", src: RESULTS_IDLE, parked: true };
  elapsed -= IDLE_HOLD_MS;
  if (celebrate && elapsed < CELEBRATION_FRAMES.length * JUMP_FRAME_MS) {
    return { phase: "celebrating", src: CELEBRATION_FRAMES[Math.floor(elapsed / JUMP_FRAME_MS)], parked: true };
  }
  return { phase: "resting", src: RESULTS_IDLE, parked: true };
}

export function markerCanvas(rect: MarkerRect) {
  // The registered seated frame preserves the existing marker's mirrored art.
  const scale = rect.width / (230 * 0.96);
  return {
    left: rect.left,
    top: rect.top - 95 * scale,
    width: RESULTS_SPRITE_WIDTH * scale,
    height: RESULTS_SPRITE_HEIGHT * scale,
  };
}
