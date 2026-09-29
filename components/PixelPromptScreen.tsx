"use client";

import ForceNextButton from "@/components/ForceNextButton";
import TriageSiteCards from "@/components/TriageSiteCards";
import { TRIAGE_TARGET_SITE } from "@/data/scenario";
import type { BriefingBullet, MissionState } from "@/lib/types";
import type { VoiceStatus } from "@/lib/voiceClient";
import VoiceTurnIndicator from "@/components/VoiceTurnIndicator";

export default function PixelPromptScreen({
  briefing,
  state,
  voiceStatus,
  error,
  onRetry,
  promptConfidence,
  promptConfidenceReason,
  onForceNext,
}: {
  briefing: BriefingBullet[];
  state: MissionState;
  voiceStatus?: VoiceStatus;
  error?: string | null;
  onRetry?: () => void;
  promptConfidence?: number | null;
  promptConfidenceReason?: string;
  onForceNext?: () => void;
}) {
  const confirmed = state.promptPhase === "confirmed";
  return (
    <div className="relative z-10 flex h-full min-h-0 w-full flex-col overflow-y-auto pl-4 pr-[calc(6%+481px*var(--ui-scale))] pb-[8dvh] pt-4 sm:pl-6 sm:pt-6">
      {!confirmed && onForceNext && <ForceNextButton onClick={onForceNext} />}
      <div className="flex min-h-0 flex-1 flex-col justify-center gap-2" style={{ zoom: 1.0 }}>
        <div className="flex shrink-0 flex-wrap items-baseline gap-x-3 gap-y-1">
          <p className="text-sm font-bold tracking-[0.14em] text-[#091f2c] sm:text-base">임무 브리핑</p>
          <h2 className="text-lg font-bold tracking-[0.05em] text-[#091f2c] sm:text-xl">탐지·신고 대상</h2>
          {voiceStatus && <VoiceTurnIndicator status={voiceStatus} />}
        </div>
        {error && <div role="alert" className="shrink-0 text-sm text-[#091f2c]">
          {error}
          {voiceStatus === "error" && onRetry && <button type="button" onClick={onRetry}
            className="pixel-button ml-3 bg-white px-2 py-1 text-xs">연결 다시 시도</button>}
        </div>}

        {promptConfidence !== null && promptConfidenceReason !== undefined && promptConfidenceReason !== "" &&
          <div className="pixel-panel flex shrink-0 max-w-[53.75rem] items-start gap-2 bg-white/95 px-3 py-2 text-xs leading-snug text-[#091f2c]">
            <span className="shrink-0 rounded-full bg-emerald-500 px-2 py-0.5 font-bold tabular-nums text-white">
              확신도 {promptConfidence}%
            </span>
            <span className="min-w-0 flex-1">{promptConfidenceReason}</span>
          </div>}

        <div className="flex max-w-[53.75rem] flex-1 flex-row items-center gap-3">
          <div className="min-w-0 shrink">
            <TriageSiteCards
              sites={[TRIAGE_TARGET_SITE]}
              people={state.people}
              activeMonitorId={state.activePromptMonitorId}
            />
          </div>

          {!confirmed && (
            <div className="pixel-panel shrink max-h-full min-w-0 max-w-[500px] overflow-y-auto bg-white/90 px-3 py-[37px]">
              <ol aria-label="임무 브리핑" className="space-y-2 text-sm leading-relaxed text-[#091f2c]">
                {briefing.map((bullet, index) => (
                  <li key={bullet.id} className="flex gap-2">
                    <span className="shrink-0 font-bold text-[#d63447]">{index + 1}.</span>
                    <span>{bullet.text}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
