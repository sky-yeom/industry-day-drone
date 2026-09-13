"use client";

import Image from "next/image";
import { Press_Start_2P } from "next/font/google";
import type { BriefingBullet } from "@/lib/types";
import type { VoiceStatus } from "@/lib/voiceClient";
import VoiceTurnIndicator from "@/components/VoiceTurnIndicator";

const pixelFont = Press_Start_2P({ weight: "400", subsets: ["latin"] });

/**
 * Reskinned "prompt" step: a pixel photo frame (with a red 구조 필요 ribbon) for
 * the reference person, and a pixel-panel briefing box. The confirmed
 * detection prompt is no longer shown as its own box here — Gibby's speech
 * bubble is the only place the confirmed prompt surfaces now. Gibby himself
 * and his speech bubble are rendered by the parent
 * (components/GibbyIntroSequence.tsx) as a persistent, fixed-position
 * overlay — he doesn't live inside this component so he never resizes,
 * fades, or moves when this content mounts/slides in. Mounted for the whole
 * lifetime of the opening->prompt transition so it can slide in without
 * remounting once the walk finishes.
 */
export default function PixelPromptScreen({
  targetImage,
  targetAlt,
  briefing,
  voiceStatus,
  error,
  onRetry,
}: {
  targetImage: string;
  targetAlt: string;
  briefing: BriefingBullet[];
  voiceStatus?: VoiceStatus;
  error?: string | null;
  onRetry?: () => void;
}) {
  return (
    <div className="relative z-10 flex h-full min-h-0 w-full flex-col justify-center gap-3 px-4 pb-[8dvh] pt-4 sm:px-6 sm:pt-6">
      <div className="flex shrink-0 flex-wrap items-baseline gap-x-3 gap-y-1">
        <p className="text-sm font-bold tracking-[0.14em] text-[#091f2c] sm:text-base">임무 브리핑</p>
        <h2 className="text-lg font-bold text-[#091f2c] sm:text-xl">프롬프트</h2>
        {voiceStatus && <VoiceTurnIndicator status={voiceStatus} />}
      </div>
      {error && <div role="alert" className="shrink-0 text-sm text-[#091f2c]">
        {error}
        {voiceStatus === "error" && onRetry && <button type="button" onClick={onRetry}
          className="pixel-button ml-3 bg-white px-2 py-1 text-xs">연결 다시 시도</button>}
      </div>}

      <div className="grid min-h-0 max-w-[860px] flex-1 grid-cols-1 gap-3 sm:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] sm:items-center">
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
    </div>
  );
}
