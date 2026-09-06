"use client";

import type { ToolActivity, VoiceStatus } from "@/lib/voiceClient";

interface VoiceControlProps {
  status: VoiceStatus;
  /** 상태에 딸린 한 줄 설명. 오류면 빨강, 그 외에는 노랑으로 보여준다. */
  detail?: string;
  level: number;
  ttfaMs: number | null;
  tools: ToolActivity[];
  onToggle: () => void;
}

const STATUS_TEXT: Record<VoiceStatus, string> = {
  idle: "음성 꺼짐",
  connecting: "연결 중…",
  listening: "듣는 중",
  speaking: "응답 중",
  error: "오류",
};

const STATUS_STYLE: Record<VoiceStatus, string> = {
  idle: "bg-[#f4f3f5] text-[#8c8279]",
  connecting: "bg-[#ffe399] text-[#7f5a1a]",
  listening: "bg-[#d4ec8e] text-[#07641d]",
  speaking: "bg-[#d9edf8] text-[#2a446f]",
  error: "bg-[#ffb3bb] text-[#73262f]",
};

export default function VoiceControl({
  status,
  detail,
  level,
  ttfaMs,
  tools,
  onToggle,
}: VoiceControlProps) {
  const live = status !== "idle" && status !== "error";

  return (
    <div className="flex flex-col gap-3 border-y border-[#e8e6df] bg-[#f8f7f8]/70 px-5 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onToggle}
          className={`flex h-10 items-center gap-2 rounded-lg px-3.5 text-sm font-semibold shadow-sm transition hover:-translate-y-0.5 active:translate-y-0 ${
            live
              ? "bg-[#73262f] text-white hover:bg-[#5c1e26]"
              : "bg-[#463668] text-white hover:bg-[#2a446f]"
          }`}
        >
          <span
            aria-hidden="true"
            className={`h-2.5 w-2.5 ${live ? "rounded-sm bg-[#ffb3bb]" : "rounded-full bg-[#d4ec8e]"}`}
          />
          {live ? "세션 종료" : "세션 시작"}
        </button>

        <span
          className={`rounded-full px-2.5 py-1.5 text-[11px] font-semibold ${STATUS_STYLE[status]}`}
        >
          {STATUS_TEXT[status]}
        </span>

        {ttfaMs !== null && (
          <span
            className={`ml-auto font-mono text-[10px] font-semibold ${
              ttfaMs < 1000 ? "text-[#07641d]" : "text-[#7f5a1a]"
            }`}
            title="말을 마친 뒤 첫 음성이 나오기까지 걸린 시간"
          >
            TTFA {ttfaMs} ms
          </span>
        )}
      </div>

      {/* Mic level meter, so it is obvious the mic is actually picking up. */}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-white">
        <div
          className="h-full rounded-full bg-[linear-gradient(90deg,#8661c5,#0078d4,#49c5b1)] transition-[width] duration-75"
          style={{ width: `${Math.min(100, level * 180)}%` }}
        />
      </div>

      {detail && (
        <p
          className={`text-[11px] leading-snug ${
            status === "error" ? "text-[#73262f]" : "text-[#7f5a1a]"
          }`}
        >
          {detail}
        </p>
      )}

      {tools.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {tools.slice(-4).map((tool) => (
            <span
              key={`${tool.id}-${tool.name}`}
              title={tool.facts ?? "실행 중…"}
              className={`rounded-md border px-1.5 py-0.5 font-mono text-[10px] ${
                tool.ok === undefined
                  ? "border-[#ffe399] bg-[#fff8f3] text-[#7f5a1a]"
                  : tool.ok
                    ? "border-[#b9dcd2] bg-[#eef8f5] text-[#07641d]"
                    : "border-[#ffb3bb] bg-[#fff1f2] text-[#73262f]"
              }`}
            >
              {tool.name}
              {tool.ms !== undefined && ` ${tool.ms}ms`}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
