"use client";

import ForceNextButton from "@/components/ForceNextButton";
import TriageSiteCards from "@/components/TriageSiteCards";
import { MONITOR_MAP_BY_KIND } from "@/data/monitors";
import { TRIAGE_TARGET_SITE } from "@/data/scenario";
import { SECURITY_SUSPECT_SITE } from "@/data/security-scenario";
import { CONSTRUCTION_TARGET_SITE } from "@/data/construction-scenario";
import type { BriefingBullet, MissionState, ScenarioKind } from "@/lib/types";
import type { VoiceStatus } from "@/lib/voiceClient";
import VoiceTurnIndicator from "@/components/VoiceTurnIndicator";

/**
 * The prompt/confirm board: all 3 scenario kinds (119 rescue, security
 * breach, construction safety) now share the same "single real
 * target + 2 false alarms" shape, so this shows one compact site card
 * (see TriageSiteCards) confirmed by voice, plus the general briefing
 * list. Once confirmed, this also renders the two relay-proposed visit
 * orders (raw "sounds urgent" order vs. the order adjusted for which
 * clue is actually more likely real) so the "AI proposes, human
 * decides" choice has a visual home, not just a spoken one — the
 * participant still picks their own order by voice (select_stop), this
 * is read-only reference. Gibby himself and his speech bubble are
 * rendered by the parent (GibbyIntroSequence.tsx).
 */
export default function PixelPromptScreen({
  briefing,
  state,
  scenarioKind,
  voiceStatus,
  error,
  onRetry,
  promptConfidence,
  promptConfidenceReason,
  onForceNext,
}: {
  briefing: BriefingBullet[];
  state: MissionState;
  // Known synchronously as soon as the user picks a scenario, unlike
  // state.kind — which only reflects the picked scenario once the relay's
  // WS connection has actually been established and pushed a snapshot.
  // The prompt screen becomes visible (mid-"sliding" transition) well
  // before that round trip completes, so falling back to state.kind here
  // would flash the wrong (default triage) layout for a moment.
  scenarioKind: ScenarioKind;
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
      <div className="flex min-h-0 flex-1 flex-col justify-center gap-2" style={{ zoom: 0.85 }}>
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

      <div
        className="flex max-w-[53.75rem] flex-1 flex-row items-center gap-3"
      >
        <div className="shrink-0">
          <TriageSiteCards
            sites={
              scenarioKind === "security" ? [SECURITY_SUSPECT_SITE]
                : scenarioKind === "construction" ? [CONSTRUCTION_TARGET_SITE]
                : [TRIAGE_TARGET_SITE]
            }
            people={state.people}
            activeMonitorId={state.activePromptMonitorId}
          />
        </div>

        {!confirmed && (
          <div
            className="pixel-panel shrink-0 max-h-full min-w-0 max-w-[500px] overflow-y-auto bg-white/90 p-3"
          >
            <ol aria-label="임무 브리핑" className="space-y-2 text-xs leading-snug text-[#091f2c]">
              {briefing.map((bullet, index) => (
                <li key={bullet.id} className="flex gap-2">
                  <span className="shrink-0 font-bold text-[#d63447]">{index + 1}.</span>
                  <span>{bullet.text}</span>
                </li>
              ))}
            </ol>
          </div>
        )}

        {confirmed && (
          <div className="pixel-panel shrink-0 min-w-0 max-w-[500px] overflow-y-auto bg-white/90 p-3">
            <p className="text-sm font-bold text-[#091f2c]">
              {scenarioKind === "security"
                ? "기비의 제안 순서 · 너는 어떤 순서로 확인하고 싶어?"
                : scenarioKind === "construction"
                ? "기비의 제안 순서 · 너는 어떤 순서로 점검하고 싶어?"
                : "기비의 제안 순서 · 너는 어떤 순서로 신고하고 싶어?"}
            </p>
            <div className="mt-2 grid grid-cols-1 gap-2">
              <div>
                <p className="text-xs font-bold tracking-[0.08em] text-[#6e6575]">
                  {scenarioKind === "security" ? "다급해 보이는 순서"
                    : scenarioKind === "construction" ? "눈에 띄는 정도"
                    : "다급하게 들리는 순서"}
                </p>
                <p className="mt-1 text-sm leading-relaxed text-[#091f2c]">
                  {state.dangerOrder.map((id) => MONITOR_MAP_BY_KIND[scenarioKind][id]?.label ?? id).join(" → ")}
                </p>
              </div>
              <div>
                <p className="text-xs font-bold tracking-[0.08em] text-[#6e6575]">
                  {scenarioKind === "security" ? "단서 분석 순서"
                    : scenarioKind === "construction" ? "실제 위험도"
                    : "신고 내용 분석 순서"}
                </p>
                <p className="mt-1 text-sm leading-relaxed text-[#091f2c]">
                  {state.vulnerableAdjustedOrder.map((id) => MONITOR_MAP_BY_KIND[scenarioKind][id]?.label ?? id).join(" → ")}
                </p>
              </div>
            </div>
          </div>
        )}
      </div>
      </div>
    </div>
  );
}
