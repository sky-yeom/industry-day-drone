"use client";

import type { ToolActivity, VoiceStatus } from "@/lib/voiceClient";
import VoiceOrb from "@/components/VoiceOrb";

interface VoiceControlProps {
  status: VoiceStatus;
  /** 상태에 딸린 한 줄 설명. 버튼/점의 title 접근성 텍스트로만 쓰인다. */
  detail?: string;
  level: number;
  ttfaMs: number | null;
  tools: ToolActivity[];
  onToggle: () => void;
}

const STATUS_LABEL: Record<VoiceStatus, string> = {
  idle: "음성 꺼짐",
  connecting: "연결 중",
  listening: "듣는 중",
  speaking: "응답 중",
  error: "오류",
};

/**
 * Icon-only voice control: the orb is purely visual (no click), a start
 * button sits centered at the bottom while idle, and an X button sits at
 * the bottom-right once a session is live, to leave it. Nothing renders as
 * visible text; labels remain as `title`/`aria-label` only.
 */
export default function VoiceControl({
  status,
  detail,
  level,
  ttfaMs,
  tools,
  onToggle,
}: VoiceControlProps) {
  const live = status !== "idle" && status !== "error";
  const a11yLabel = detail ? `${STATUS_LABEL[status]} — ${detail}` : STATUS_LABEL[status];
  // Still exposed for screen readers/tooltips even though nothing renders as
  // visible text; keeps TTFA/tool telemetry available without cluttering UI.
  const telemetryLabel = [
    ttfaMs !== null ? `TTFA ${ttfaMs}ms` : null,
    ...tools.slice(-4).map((t) => `${t.name}${t.facts ? `: ${t.facts}` : ""}`),
  ]
    .filter(Boolean)
    .join(" · ");
  const title = telemetryLabel ? `${a11yLabel} · ${telemetryLabel}` : a11yLabel;

  return (
    <div className="relative flex h-full w-full items-center justify-center">
      <div title={title}>
        <VoiceOrb status={status} level={level} />
      </div>

      {!live && (
        <button
          type="button"
          onClick={onToggle}
          aria-label="세션 시작"
          title="세션 시작"
          className="absolute bottom-6 left-1/2 grid h-12 w-12 -translate-x-1/2 place-items-center rounded-full bg-[#463668] text-white shadow-[0_10px_24px_rgba(70,54,104,0.3)] transition hover:-translate-y-0.5 hover:bg-[#2a446f] active:translate-y-0"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5">
            <path
              fill="currentColor"
              d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.93V21h2v-2.07A7 7 0 0 0 19 12h-2Z"
            />
          </svg>
        </button>
      )}

      {live && (
        <button
          type="button"
          onClick={onToggle}
          aria-label="세션 종료"
          title="세션 종료"
          className="absolute bottom-6 right-6 grid h-10 w-10 place-items-center rounded-full border-2 border-white bg-white/90 text-[#463668] shadow-[0_10px_24px_rgba(9,31,44,0.15)] backdrop-blur-sm transition hover:-translate-y-0.5 hover:bg-white active:translate-y-0"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4">
            <path
              fill="currentColor"
              d="M6.4 5 5 6.4 10.6 12 5 17.6 6.4 19l5.6-5.6 5.6 5.6 1.4-1.4-5.6-5.6L19 6.4 17.6 5 12 10.6 6.4 5Z"
            />
          </svg>
        </button>
      )}
    </div>
  );
}
