"use client";

import Image from "next/image";
import { Press_Start_2P } from "next/font/google";
import { useEffect, useState } from "react";
import { ANCHOR_W, LAST_FRAME, gibbyFrameStyle } from "@/lib/gibbyMapSprite";
import { useTypewriter } from "@/lib/useTypewriter";
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
 * reaches the last frame, `onIntroReady` fires (releases the backend-held
 * voice line, see relay/server.py's route_intro_pending gate) and shortly
 * after, `onDone` fires so the parent can swap straight into the live
 * Route step (real map.png + info cards) — there's no separate smaller
 * map preview shown here first.
 */
export default function GibbyMapTransition({
  targetImage,
  targetAlt,
  briefing,
  agentText,
  onIntroReady,
  onDone,
}: {
  targetImage: string;
  targetAlt: string;
  briefing: BriefingBullet[];
  agentText: string;
  onIntroReady: () => void;
  onDone: () => void;
}) {
  const [frame, setFrame] = useState(0);
  const typed = useTypewriter(agentText);
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
    // Voice release and the swap into the real Route screen now fire
    // together off one timer — previously the route-screen swap
    // (`onDone`) waited an extra beat after the voice was already released
    // (`onIntroReady`), which made the agent start talking while the
    // screen was still sitting on the empty transition frame. There
    // should be no perceptible gap between "voice starts" and "route
    // screen appears".
    const id = window.setTimeout(() => {
      onIntroReady();
      onDone();
    }, READY_DELAY_MS);
    return () => window.clearTimeout(id);
  }, [frame, onIntroReady, onDone]);

  return (
    <main className="relative flex h-dvh w-full flex-col overflow-hidden">
      <div aria-hidden className="pixel-scene-sky absolute inset-0" />
      <div aria-hidden className="pixel-scene-dirt absolute inset-x-0 bottom-0 h-[16%]" />
      <div aria-hidden className="pixel-scene-ground pixel-rendering absolute inset-x-0 bottom-[12%] h-6" />

      <div className="relative z-10 flex h-full min-h-0 w-full flex-col gap-3 px-4 pb-[8dvh] pt-4 sm:px-6 sm:pt-6">
        <div className="shrink-0">
          <p className={`${pixelFont.className} pixel-title text-sm tracking-[0.14em] text-[#091f2c] sm:text-base`}>실시간 경로 관제</p>
          <h2 className={`${pixelFont.className} pixel-title mt-2 text-lg text-[#091f2c] sm:text-xl`}>비행경로</h2>
        </div>

        {/* Old prompt content fades away as soon as the transition starts. */}
        <div className={`grid min-h-0 max-w-[860px] flex-1 grid-cols-1 gap-3 transition-opacity duration-700 sm:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] sm:items-center ${fadingOut ? "pointer-events-none opacity-0" : "opacity-100"}`}>
          <div className="flex min-h-0 flex-col items-center justify-center gap-4 py-2">
            <div className="pixel-frame pixel-rendering relative mt-6 w-full max-w-48">
              <span className={`${pixelFont.className} pixel-frame-header--danger text-[10px]`}>위험!</span>
              <div className="relative aspect-[3/4] w-full overflow-hidden bg-[#eee8f7]">
                <Image src={targetImage} alt={targetAlt} fill sizes="(max-width: 768px) 60vw, 220px" className="object-contain" />
              </div>
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
        className="absolute bottom-[16%] left-[calc(94%-145px)] z-20 flex items-end justify-center"
        style={{ width: ANCHOR_W }}
      >
        <div aria-hidden className="pixel-rendering shrink-0" style={gibbyFrameStyle(frame)} />
      </div>

      {typed && (
        <div className="absolute bottom-[calc(16%+146px)] z-20 right-[calc(6%+161px)]">
          <div className="pixel-bubble pixel-bubble--right relative max-w-[260px] px-4 py-3 text-sm leading-6 text-[#091f2c] sm:max-w-xs">
            {typed}
          </div>
        </div>
      )}
    </main>
  );
}
