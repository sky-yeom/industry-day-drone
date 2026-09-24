"use client";

import { useEffect, useRef, useState } from "react";
import { preload } from "react-dom";
import PixelGround from "@/components/PixelGround";
import TriageSiteCards from "@/components/TriageSiteCards";
import { ANCHOR_W, LAST_FRAME, gibbyFrameStyle } from "@/lib/gibbyMapSprite";
import { MAP_IMAGE_BY_KIND } from "@/data/monitors";
import type { TriageSite } from "@/data/scenario";
import type { SecurityZone } from "@/data/security-scenario";
import type { ConstructionZone } from "@/data/construction-scenario";
import type { BriefingBullet, MissionState } from "@/lib/types";

const FRAME_MS = 400;
// Linger a bit longer while he's unrolling (frames 4-5) so it doesn't flash by.
const UNROLL_EXTRA_MS = 220;
// Brief buffer after his last frame before both the voice cue and the
// swap into the real Route screen fire together (see the single combined
// timeout below) — just long enough that his final pose reads for a beat,
// not a separate "reveal" animation. There's no in-transition map preview
// anymore (removed per feedback — it read as "the map shows twice": once
// small here, once full-size on the real Route screen).
const READY_DELAY_MS = 150;

/**
 * Prompt -> Route handoff: Gibby (fixed position/size — he never
 * translates or rescales here, per the "don't move him" requirement) digs
 * in his pocket and unrolls a map while the old prompt content (photo +
 * briefing) fades out and the title swaps to "비행경로". The moment he
 * reaches the last frame, `onDone` fires so the parent can swap into the live
 * Route step (real per-scenario map art + info cards) — there's no separate smaller
 * map preview shown here first.
 */
export default function GibbyMapTransition({
  sites,
  state,
  briefing,
  onDone,
}: {
  sites: (TriageSite | SecurityZone | ConstructionZone)[];
  state: MissionState;
  briefing: BriefingBullet[];
  onDone: () => void;
}) {
  preload(MAP_IMAGE_BY_KIND[state.kind], { as: "image" });
  const [frame, setFrame] = useState(0);
  const done = useRef(onDone);
  useEffect(() => { done.current = onDone; }, [onDone]);
  // Old photo/briefing content starts fading out as soon as Gibby actually
  // pulls the scroll out of his pocket (frame 2), instead of waiting for
  // the whole animation to finish — so the "content leaves" beat is synced
  // with the "map comes out" beat rather than happening all at once at the
  // very end.
  const fadingOut = frame >= 2;

  useEffect(() => {
    if (frame >= LAST_FRAME) return;
    const extra = frame === 4 || frame === 5 ? UNROLL_EXTRA_MS : 0;
    const id = window.setTimeout(() => setFrame((f) => f + 1), FRAME_MS + extra);
    return () => window.clearTimeout(id);
  }, [frame]);

  useEffect(() => {
    if (frame !== LAST_FRAME) return;
    // The mounted map releases prefetched audio after its first visible paint.
    const id = window.setTimeout(() => done.current(), READY_DELAY_MS);
    return () => window.clearTimeout(id);
  }, [frame]);

  return (
    <main className="relative flex h-dvh w-full flex-col overflow-hidden">
      <div aria-hidden className="pixel-scene-sky absolute inset-0" />
      <PixelGround />

      <div className="relative z-10 flex h-full min-h-0 w-full flex-col gap-3 px-4 pb-[8dvh] pt-4 sm:px-6 sm:pt-6">
        <div className="shrink-0">
          <p className="text-sm font-bold tracking-[0.14em] text-[#091f2c] sm:text-base">실시간 경로 관제</p>
          <h2 className="mt-2 text-lg font-bold text-[#091f2c] sm:text-xl">비행경로</h2>
        </div>

        {/* Old prompt content fades away as soon as the transition starts. */}
        <div className={`min-h-0 max-w-[53.75rem] flex-1 space-y-3 overflow-y-auto transition-opacity duration-700 ${fadingOut ? "pointer-events-none opacity-0" : "opacity-100"}`}>
          <TriageSiteCards sites={sites} people={state.people} compact />
          <div className="pixel-panel shrink-0 p-5">
            <ol aria-label="임무 브리핑" className="space-y-4 text-sm leading-relaxed text-[#091f2c]">
              {briefing.map((bullet, index) => (
                <li key={bullet.id} className="flex gap-3">
                  <span className="shrink-0 font-bold text-[#d63447]">{index + 1}.</span>
                  <span>{bullet.text}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>

        {/* No map preview here anymore — the real Route screen (full
            per-scenario map art + info cards) swaps in directly right after Gibby's
            last frame, at the same moment the voice cue fires (see the
            combined timeout above), instead of showing a smaller map
            here first and then the full one a moment later. */}
      </div>

      {/* Gibby: fixed at the exact same corner spot he was docked at on the
          prompt screen (see .gibby-travel--corner) — only the sprite frame
          changes here, no translate/scale, so he never appears to move. */}
      <div
        className="absolute bottom-[16%] z-20 flex items-end justify-center"
        style={{
          width: ANCHOR_W,
          left: `min(calc(94% - (145px * var(--ui-scale))), calc(100% - ${ANCHOR_W}px))`,
        }}
      >
        <div
          aria-hidden
          className="pixel-rendering shrink-0"
          style={{ ...gibbyFrameStyle(frame), transform: "scale(var(--ui-scale))", transformOrigin: "bottom center" }}
        />
      </div>
    </main>
  );
}
