"use client";

import Image from "next/image";
import type { TriageSite } from "@/data/scenario";
import type { SecurityZone } from "@/data/security-scenario";
import type { ConstructionZone } from "@/data/construction-scenario";
import type { MonitorId, PersonState } from "@/lib/types";

/**
 * The 3 site cards at the heart of the merged triage board: one card per
 * 119 call site (fire / sea / rubble), each showing that site's own
 * reference photo and — once Gibby has confirmed it by voice — that
 * site's own confirmed appearance description + confidence. Before
 * confirmation a card shows the site's clue as a placeholder so the
 * participant can see what's still pending. The card matching
 * `activeMonitorId` is highlighted so it's obvious which site Gibby is
 * currently asking about. Reused by both the live triage board
 * (PixelPromptScreen) and the prompt->route transition (GibbyMapTransition).
 */
export default function TriageSiteCards({
  sites,
  people,
  activeMonitorId,
  compact = false,
}: {
  sites: (TriageSite | SecurityZone | ConstructionZone)[];
  people: PersonState[];
  activeMonitorId?: MonitorId | null;
  compact?: boolean;
}) {
  const isSingle = sites.length === 1;
  return (
    <div className={`grid grid-cols-1 gap-3 ${sites.length > 1 ? "sm:grid-cols-3" : "max-w-md"}`}>
      {sites.map((site) => {
        const person = people.find((candidate) => candidate.monitorId === site.monitorId);
        const confirmed = person?.promptConfirmed ?? false;
        const active = activeMonitorId === site.monitorId;
        const statusBadge = (
          <span
            className={`self-start rounded-full px-2 py-0.5 text-[0.625rem] font-bold ${
              confirmed ? "bg-emerald-500 text-white" : active ? "bg-[#ffd23f] text-[#091f2c]" : "bg-[#e6e1ee] text-[#6e6575]"
            }`}
          >
            {confirmed ? "설명 확인 완료" : active ? "설명 확인 중" : "대기 중"}
          </span>
        );
        const confidenceBadge = confirmed && person?.promptConfidence !== null && person?.promptConfidence !== undefined && (
          <span className="text-[0.625rem] font-bold tabular-nums text-[#091f2c]">
            확신도 {person.promptConfidence}%
          </span>
        );
        const textBody = (
          <div className={`overflow-y-auto pr-1 text-xs leading-snug text-[#091f2c] ${isSingle ? "max-h-32" : "max-h-16"}`}>
            {confirmed ? (
              <p>{person?.promptText}</p>
            ) : (
              <p className="italic text-[#6e6575]">{site.clue}</p>
            )}
          </div>
        );
        const image = (
          <Image
            src={site.referenceImage}
            alt={site.referenceAlt}
            width={1536}
            height={1536}
            sizes={isSingle ? "224px" : "184px"}
            className={isSingle ? "block h-auto w-32 shrink-0 sm:w-40" : "block h-auto w-full"}
          />
        );
        return (
          <div
            key={site.monitorId}
            className={`pixel-panel pixel-rendering relative flex flex-col gap-2 bg-white/95 p-3 transition-colors ${active ? "ring-2 ring-[#ffd23f]" : ""}`}
          >
            <span className="pixel-frame-header--danger text-[0.5625rem] font-bold tracking-[0.03em]">
              {site.label}
            </span>
            {isSingle ? (
              <div className="flex flex-row items-center gap-3">
                {image}
                <div className="flex min-w-0 flex-1 flex-col items-start gap-2">
                  {!compact && textBody}
                  {statusBadge}
                  {confidenceBadge}
                </div>
              </div>
            ) : (
              <>
                {image}
                {!compact && textBody}
                {statusBadge}
                {confidenceBadge}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
