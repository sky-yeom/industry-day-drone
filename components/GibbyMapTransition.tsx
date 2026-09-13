"use client";

import Image from "next/image";
import { Press_Start_2P } from "next/font/google";
import { useEffect, useRef, useState } from "react";
import { preload } from "react-dom";
import PixelGround from "@/components/PixelGround";
import { ANCHOR_W, LAST_FRAME, gibbyFrameStyle } from "@/lib/gibbyMapSprite";
import type { BriefingBullet } from "@/lib/types";

const pixelFont = Press_Start_2P({ weight: "400", subsets: ["latin"] });

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
 * Route step (real map.png + info cards) — there's no separate smaller
 * map preview shown here first.
 */
export default function GibbyMapTransition({
  targetImage,
  targetAlt,
  briefing,
  onDone,
}: {
  targetImage: string;
  targetAlt: string;
  briefing: BriefingBullet[];
  onDone: () => void;
}) {
  preload("/gibby/map.png", { as: "image" });
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
        <div className={`grid min-h-0 max-w-[860px] flex-1 grid-cols-1 gap-3 transition-opacity duration-700 sm:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] sm:items-center ${fadingOut ? "pointer-events-none opacity-0" : "opacity-100"}`}>
          <div className="flex min-h-0 flex-col items-center justify-center gap-4 py-2">
            <div className="pixel-panel pixel-rendering relative mt-6 w-full max-w-48">
              <span className={`${pixelFont.className} pixel-frame-header--danger text-[10px]`}>구조 필요</span>
              <Image src={targetImage} alt={targetAlt} width={1536} height={1536} sizes="184px" className="block h-auto w-full" />
            </div>
          </div>
          <div className="pixel-panel flex h-64 shrink-0 flex-col justify-center overflow-y-auto p-5 sm:h-72">
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
            map.png + info cards) swaps in directly right after Gibby's
            last frame, at the same moment the voice cue fires (see the
            combined timeout above), instead of showing a smaller map
            here first and then the full one a moment later. */}
      </div>

      {/* Gibby: fixed at the exact same corner spot he was docked at on the
          prompt screen (see .gibby-travel--corner) — only the sprite frame
          changes here, no translate/scale, so he never appears to move. */}
      <div
        className="absolute bottom-[16%] z-20 flex items-end justify-center"
        style={{ width: ANCHOR_W, left: `min(calc(94% - 145px), calc(100% - ${ANCHOR_W}px))` }}
      >
        <div aria-hidden className="pixel-rendering shrink-0" style={gibbyFrameStyle(frame)} />
      </div>
    </main>
  );
}
