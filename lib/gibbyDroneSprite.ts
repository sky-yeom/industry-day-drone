// Sprite metadata for the Route -> drone-image handoff: the drone flies in
// and Gibby boards it (public/gibby/drone-board-1.png .. drone-board-4.png,
// cropped from UI-images/drone riding.png's "GIBBY GETTING ON DRONE"
// front-view row). There's only ever one drone/Gibby visual: once seated
// (the last boarding frame), that same mirrored sprite shrinks down and
// becomes the live "you are here" marker on the map — see MAP_MARKER_*
// below — instead of handing off to a separate static drone icon.
//
// Unlike lib/gibbyMapSprite.ts, these aren't packed into one sheet: the
// source art isn't laid out on a consistent grid (frame 1 is wide/short —
// Gibby standing beside a separate drone — while frame 3 is narrow/tall —
// Gibby airborne above the drone mid-hop), so cropping each frame to its
// own tight bounding box as an individual file is simpler and avoids the
// bleed-between-cells problems a forced grid would reintroduce. A single
// shared `BASE_SCALE` (matching idle.png/smile.png's own native-274px ->
// displayed-214px ratio, so Gibby doesn't visibly jump in size the instant
// this sequence takes over from his docked idle sprite) is applied to each
// frame's real native pixel size, so their on-screen proportions relative
// to each other stay exactly as drawn.
const IDLE_SPRITE_NATIVE_H = 274;
const IDLE_SPRITE_DISPLAY_H = 214;
export const BASE_SCALE = IDLE_SPRITE_DISPLAY_H / IDLE_SPRITE_NATIVE_H;

export interface DroneBoardFrame {
  src: string;
  width: number;
  height: number;
}

// Native pixel dimensions of each cropped frame (measured directly from the
// exported PNGs). `width`/`height` below are already the *displayed* size
// (native * BASE_SCALE).
const NATIVE_FRAMES: { src: string; nativeW: number; nativeH: number }[] = [
  { src: "/gibby/drone-board-1.png", nativeW: 311, nativeH: 216 }, // stand next to drone
  { src: "/gibby/drone-board-2.png", nativeW: 206, nativeH: 225 }, // climb on
  { src: "/gibby/drone-board-3.png", nativeW: 204, nativeH: 334 }, // mid-air hop
  { src: "/gibby/drone-board-4.png", nativeW: 230, nativeH: 284 }, // seated, ready
];

export const BOARDING_FRAMES: DroneBoardFrame[] = NATIVE_FRAMES.map(({ src, nativeW, nativeH }) => ({
  src,
  width: Math.round(nativeW * BASE_SCALE),
  height: Math.round(nativeH * BASE_SCALE),
}));

export const LAST_BOARDING_FRAME = BOARDING_FRAMES.length - 1;

// The reference art faces right (Gibby on the left, drone on his right),
// but Gibby is docked at the screen's bottom-right corner with the map to
// his left — mirroring puts the drone between him and the map instead of
// off the right edge of the viewport.
export const BOARDING_MIRRORED = true;

// Live map marker: the seated (last boarding) frame, reused small and
// still mirrored, as the single ongoing "you are here" visual — replaces
// the plain top-down drone.png icon used in the previous version of this
// feature.
export const MAP_MARKER_SRC = NATIVE_FRAMES[LAST_BOARDING_FRAME].src;
const MAP_MARKER_NATIVE = NATIVE_FRAMES[LAST_BOARDING_FRAME];
export const MAP_MARKER_HEIGHT = 44;
export const MAP_MARKER_WIDTH = Math.round(
  MAP_MARKER_HEIGHT * (MAP_MARKER_NATIVE.nativeW / MAP_MARKER_NATIVE.nativeH),
);

// Off-map entry point (percentages, same coordinate space as the map's own
// pins): just past the map's bottom-right edge, in the gap where the right
// column sits — roughly where Gibby's dock overlay visually is on screen —
// so the marker's very first frame reads as "flying in from his corner"
// before easing onto the first real pin, without needing to measure actual
// cross-container pixel positions between the page-level dock overlay and
// the map's own responsive container.
export const MAP_MARKER_ENTRY = { x: 108, y: 108 };
