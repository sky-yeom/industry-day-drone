import type { CSSProperties } from "react";

// Sheet layout: public/gibby/map-finding.png is a 4x2 grid, 384x512 per
// cell. The art does NOT respect the 512px cell grid:
// - Frames 0-3 (idle -> reach into pocket -> pull out scroll -> hold it up)
//   draw the character large, feet at row ~405-410, with genuinely empty
//   space below (rows ~410-450) before the *next row's* frames' artwork
//   begins.
// - Frames 4-7 (unrolling -> reading -> final "aha") are drawn small
//   enough, and high enough in their row, that their heads actually start
//   *above* their own cell's top edge — bleeding ~50-60px up into row 0's
//   band (rows ~450-511, previously misread as "stray reference art" left
//   behind in frames 0-3's cells; it's really frames 4-7's own heads).
//   Cropping each frame strictly at its nominal `row * FRAME_H` (as an
//   earlier version of this file did, to avoid bleed-through when frames
//   0-3 render) chops the top of the head off for frames 4-7. The fix is
//   a per-frame `top` offset (negative for frames 4-7) marking exactly
//   where that frame's real artwork starts, measured past the genuine
//   gap below frames 0-3's feet so it can never bleed into their actual
//   character.
// Measured precisely via a per-frame alpha-channel bounding-box scan
// (restricted to rows above each frame's `cropBottom`, to exclude the
// stray art below): every frame's *tight* head-to-feet pixel span
// (`spanPx`), i.e. the actual drawn silhouette ignoring the empty margin
// above the head. This is the single source of truth `gibbyFrameStyle`
// derives everything else from, which fixes two problems that plagued
// earlier hand-tuned scale/margin constants:
//   1) Uniform on-screen character size: `scale` is solved per frame so
//      `spanPx * scale` is exactly the same for all 8 frames — not just
//      approximately matched by eye, so there's no perceptible size pulse
//      as he moves between poses.
//   2) Uniform headroom: every frame is padded up to the same total box
//      height (`TARGET_TOTAL_H`) via a real spacer, so the margin above
//      the head is identical everywhere and the head is never flush
//      against the box's top edge for any frame.
// The crop is always taken from row 0 of the cell downward and the
// display box is anchored via a fixed `bottom` CSS position (not `top`),
// so however tall a given frame's crop/zoom combo ends up, his feet always
// land on the same screen row.
export const FRAME_W = 384;
export const FRAME_H = 512;
export const COLS = 4;
export const FRAME_COUNT = 8;
export const LAST_FRAME = FRAME_COUNT - 1;

// top: native-pixel row (relative to this frame's nominal `row * FRAME_H`)
// where its real artwork actually starts. 0 for frames 0-3 (their heads
// start at/near the cell's own top); negative for frames 4-7, whose heads
// bleed upward into row 0's band (see note above).
// cropBottom: last source row (relative to the same nominal row origin)
// to include, chosen to land just past that frame's feet.
// spanPx: tight head-to-feet pixel span (`cropBottom - top`) — the actual
// character silhouette height, re-measured from each frame's *true* top.
// headWidthPx: tight pixel width of the head/goggles region (measured
// across the topmost ~40px of the true silhouette) — a second,
// independent size cue, kept as a small blend factor alongside spanPx
// since the two aren't measured with perfect pixel-for-pixel consistency
// frame to frame; now that both use each frame's true top they land much
// closer together (~0.46-0.59 head-width-to-span ratio across all 8
// frames, vs. a spurious ~0.8+ for 4-7 before the true-top fix) so this
// blend barely nudges scale anymore, but is kept for parity.
const FRAME_META: { top: number; cropBottom: number; spanPx: number; headWidthPx: number }[] = [
  { top: 0, cropBottom: 405, spanPx: 368, headWidthPx: 178 },   // 0: idle
  { top: 0, cropBottom: 405, spanPx: 367, headWidthPx: 168 },   // 1: reach into pocket
  { top: 0, cropBottom: 405, spanPx: 367, headWidthPx: 181 },   // 2: pull out scroll
  { top: 0, cropBottom: 406, spanPx: 369, headWidthPx: 174 },   // 3: hold it up
  { top: -61, cropBottom: 295, spanPx: 356, headWidthPx: 166 }, // 4: unrolling
  { top: -61, cropBottom: 291, spanPx: 352, headWidthPx: 173 }, // 5: unrolling
  { top: -62, cropBottom: 291, spanPx: 353, headWidthPx: 171 }, // 6: reading
  { top: -59, cropBottom: 292, spanPx: 351, headWidthPx: 164 }, // 7: final "aha"
];

// Display scale: `TARGET_SPAN_PX` below is calibrated to match Gibby's
// on-screen character height as he's docked on the prompt screen just
// before this transition starts (public/gibby/idle.png, rendered there at
// a fixed 214px image height — its own tight head-to-feet span is 273 of
// its 274px native height, i.e. ~213px displayed). Frame 0 here is his
// idle pose picking up right where that leaves off, so it (and every
// other frame, solved to match) targets that same displayed span, instead
// of an arbitrary fraction of the sprite sheet's cell height — otherwise
// he visibly shrinks the instant this transition takes over from the
// prompt screen's idle sprite.
const IDLE_SPRITE_NATIVE_H = 274;
const IDLE_SPRITE_DISPLAY_H = 214;
const IDLE_SPRITE_SPAN_NATIVE = 273;
const TARGET_SPAN_PX = IDLE_SPRITE_SPAN_NATIVE * (IDLE_SPRITE_DISPLAY_H / IDLE_SPRITE_NATIVE_H);
const BASE_SCALE = TARGET_SPAN_PX / FRAME_META[0].spanPx;
// Target head width every frame's blended scale aims for, anchored to
// frame 0 (idle) at BASE_SCALE — see FRAME_META's headWidthPx comment.
const TARGET_HEAD_W_PX = FRAME_META[0].headWidthPx * BASE_SCALE;

// Per-frame scale: geometric mean of "match head-to-feet span" and "match
// head width", each against frame 0's displayed size. For frames 0-3
// (whose width:height proportions already match frame 0 closely) both
// candidates roughly agree and this reduces to ~BASE_SCALE either way.
// For frames 4-7 (chubbier/wider art) this settles on a middle ground
// instead of forcing his height to match exactly and leaving him looking
// noticeably wider than every other frame.
function scaleForFrame(frame: number): number {
  const meta = FRAME_META[frame];
  const scaleBySpan = TARGET_SPAN_PX / meta.spanPx;
  const scaleByHeadWidth = TARGET_HEAD_W_PX / meta.headWidthPx;
  return Math.sqrt(scaleBySpan * scaleByHeadWidth);
}

// Total box height (crop + headroom padding) every frame is normalized to,
// so the box itself never visibly changes size either (previously frames
// 4-7's box was shorter than 0-3's despite the character being the same
// size, which read as a shrink/jump at the pose change). Frame 0's natural
// box height (cropBottom * BASE_SCALE) plus extra breathing room above the
// head — bumped from an initial 10px to 30px after the head still read as
// slightly clipped for frames 4-7 at 10px.
const TARGET_TOTAL_H = FRAME_META[0].cropBottom * BASE_SCALE + 30;

// Fixed anchor box every frame is centered/bottom-aligned within, sized to
// frame 0's on-screen width so the transition starts exactly where the
// prompt screen's docked Gibby ends up (see .gibby-travel--corner).
export const ANCHOR_W = FRAME_W * BASE_SCALE;

// `gibbyFrameLayout` keeps the blank headroom ("topPad") as a *real*,
// separate spacer element rather than folding it into the sprite div's
// own height via a background-position offset — that would make the
// div's height (and thus its background-position "peephole") taller than
// one frame's own crop, risking bleed into neighboring artwork above or
// below it in the sheet. Each frame's `spriteHeight` is exactly
// `(cropBottom - top) * scale` (its own true crop, no more), and the
// headroom is instead just empty layout space above it via `marginTop`.
export function gibbyFrameLayout(frame: number) {
  const col = frame % COLS;
  const row = Math.floor(frame / COLS);
  const meta = FRAME_META[frame];
  const scale = scaleForFrame(frame);
  const sheetW = FRAME_W * COLS * scale;
  const sheetH = FRAME_H * 2 * scale;
  // Peephole spans from this frame's *true* top (nominal row start + the
  // possibly-negative `top` offset) down to its feet — never more, so it
  // can never bleed into neighboring artwork above or below it.
  const spriteHeight = (meta.cropBottom - meta.top) * scale;
  const topPad = Math.max(0, TARGET_TOTAL_H - spriteHeight);
  const bgX = -(col * FRAME_W * scale);
  const bgY = -((row * FRAME_H + meta.top) * scale);
  const spriteStyle: CSSProperties = {
    width: FRAME_W * scale,
    height: spriteHeight,
    backgroundImage: "url(/gibby/map-finding.png)",
    backgroundRepeat: "no-repeat",
    backgroundSize: `${sheetW}px ${sheetH}px`,
    backgroundPosition: `${bgX}px ${bgY}px`,
  };
  return { spriteStyle, topPad, width: FRAME_W * scale };
}

// Back-compat single-style helper (used anywhere only the final combined
// box is needed, e.g. nowhere currently critical — prefer
// `gibbyFrameLayout` for actual rendering since it avoids the bleed bug
// above). Kept only as a thin convenience wrapper.
export function gibbyFrameStyle(frame: number): CSSProperties {
  const { spriteStyle, topPad } = gibbyFrameLayout(frame);
  return { ...spriteStyle, marginTop: topPad };
}

