"use client";

import { useEffect, useRef, useState } from "react";
import { preload } from "react-dom";
import PixelGround from "@/components/PixelGround";
import TriageSiteCards from "@/components/TriageSiteCards";
import { ANCHOR_W, LAST_FRAME, gibbyFrameStyle } from "@/lib/gibbyMapSprite";
import { MAP_IMAGE_BY_KIND } from "@/data/monitors";
import type { TriageSite } from "@/data/scenario";
import type { BriefingBullet, MissionState } from "@/lib/types";

const FRAME_MS = 400;
const UNROLL_EXTRA_MS = 220;
const READY_DELAY_MS = 150;

export default function GibbyMapTransition({
  sites,
  state,
  briefing,
  promptConfidence,
  promptConfidenceReason,
  onWalkingStart,
  onDone,
}: {
  sites: TriageSite[];
  state: MissionState;
  briefing: BriefingBullet[];
  promptConfidence?: number | null;
  promptConfidenceReason?: string;
  onWalkingStart?: () => void;
  onDone: () => void;
}) {
  preload(MAP_IMAGE_BY_KIND[state.kind], { as: "image" });
  const [frame, setFrame] = useState(0);
  const done = useRef(onDone);
  useEffect(() => { done.current = onDone; }, [onDone]);
  const walkingStart = useRef(onWalkingStart);
  useEffect(() => { walkingStart.current = onWalkingStart; }, [onWalkingStart]);
  const fadingOut = frame >= 2;
  const walking = frame >= 1;

  // The route-intro narration (the "3 calls" briefing) must stay silent until
  // the screen has actually left the 확신도-mirrored first frame and shown the
  // 비행경로 header, not just after the map-intro step mounts.
  useEffect(() => {
    if (!walking) return;
    walkingStart.current?.();
  }, [walking]);

  useEffect(() => {
    if (frame >= LAST_FRAME) return;
    const extra = frame === 4 || frame === 5 ? UNROLL_EXTRA_MS : 0;
    const id = window.setTimeout(() => setFrame((f) => f + 1), FRAME_MS + extra);
    return () => window.clearTimeout(id);
  }, [frame]);

  useEffect(() => {
    if (frame !== LAST_FRAME) return;
    const id = window.setTimeout(() => done.current(), READY_DELAY_MS);
    return () => window.clearTimeout(id);
  }, [frame]);

  return (
    <main className="relative flex h-dvh w-full flex-col overflow-hidden">
      <div aria-hidden className="pixel-scene-sky absolute inset-0" />
      <PixelGround />

      <div className="relative z-10 flex h-full min-h-0 w-full flex-col justify-center gap-2 pl-4 pr-[calc(6%+481px*var(--ui-scale))] pb-[8dvh] pt-4 sm:pl-6 sm:pt-6">
        <div className="shrink-0">
          {walking
            ? <>
                <p className="text-sm font-bold tracking-[0.14em] text-[#091f2c] sm:text-base">실시간 경로 관제</p>
                <h2 className="mt-2 text-lg font-bold text-[#091f2c] sm:text-xl">비행경로</h2>
              </>
            : <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <p className="text-sm font-bold tracking-[0.14em] text-[#091f2c] sm:text-base">임무 브리핑</p>
                <h2 className="text-lg font-bold tracking-[0.05em] text-[#091f2c] sm:text-xl">탐지·신고 대상</h2>
              </div>}
        </div>

        {!walking && promptConfidence != null && promptConfidenceReason !== undefined && promptConfidenceReason !== "" &&
          <div className="pixel-panel flex shrink-0 max-w-[53.75rem] items-start gap-2 bg-white/95 px-3 py-2 text-xs leading-snug text-[#091f2c]">
            <span className="shrink-0 rounded-full bg-emerald-500 px-2 py-0.5 font-bold tabular-nums text-white">
              확신도 {promptConfidence}%
            </span>
            <span className="min-w-0 flex-1">{promptConfidenceReason}</span>
          </div>}

        {walking
          ? <div className={`min-h-0 max-w-[53.75rem] flex-1 space-y-3 overflow-y-auto transition-opacity duration-700 ${fadingOut ? "pointer-events-none opacity-0" : "opacity-100"}`}>
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
          : <div className="flex max-w-[53.75rem] flex-1 flex-row items-center gap-3">
              <div className="min-w-0 shrink">
                <TriageSiteCards sites={sites} people={state.people} />
              </div>
            </div>}
      </div>

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
