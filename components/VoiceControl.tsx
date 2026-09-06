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
  idle: "bg-zinc-800 text-zinc-300",
  connecting: "bg-amber-900/70 text-amber-200",
  listening: "bg-emerald-900/70 text-emerald-200",
  speaking: "bg-sky-900/70 text-sky-200",
  error: "bg-red-900/70 text-red-200",
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
    <div className="flex flex-col gap-2 border-b border-zinc-800 px-3 py-2.5">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onToggle}
          className={`flex h-9 items-center gap-2 rounded-lg px-3 text-sm font-medium transition-colors ${
            live
              ? "bg-red-600 text-white hover:bg-red-500"
              : "bg-emerald-600 text-white hover:bg-emerald-500"
          }`}
        >
          <span aria-hidden="true">{live ? "■" : "●"}</span>
          {live ? "세션 종료" : "세션 시작"}
        </button>

        <span
          className={`rounded-full px-2.5 py-1 text-xs font-medium ${STATUS_STYLE[status]}`}
        >
          {STATUS_TEXT[status]}
        </span>

        {ttfaMs !== null && (
          <span
            className={`ml-auto text-[11px] font-medium ${
              ttfaMs < 1000 ? "text-emerald-400" : "text-amber-400"
            }`}
            title="말을 마친 뒤 첫 음성이 나오기까지 걸린 시간"
          >
            TTFA {ttfaMs} ms
          </span>
        )}
      </div>

      {/* Mic level meter, so it is obvious the mic is actually picking up. */}
      <div className="h-1 w-full overflow-hidden rounded-full bg-zinc-800">
        <div
          className="h-full rounded-full bg-emerald-500 transition-[width] duration-75"
          style={{ width: `${Math.min(100, level * 180)}%` }}
        />
      </div>

      {detail && (
        <p
          className={`text-[11px] leading-snug ${
            status === "error" ? "text-red-400" : "text-amber-400/90"
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
                  ? "border-amber-700/60 bg-amber-900/40 text-amber-200"
                  : tool.ok
                    ? "border-emerald-700/60 bg-emerald-900/40 text-emerald-200"
                    : "border-red-700/60 bg-red-900/40 text-red-200"
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
