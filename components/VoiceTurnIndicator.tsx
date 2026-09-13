"use client";

import type { VoiceStatus } from "@/lib/voiceClient";

const LABELS: Record<VoiceStatus, string> = {
  idle: "음성 연결 대기",
  connecting: "음성을 연결하고 있어",
  processing: "답변을 준비하고 있어",
  speaking: "기비가 말하는 중",
  listening: "지금 말해줘",
  error: "음성 연결을 확인해 줘",
};

export default function VoiceTurnIndicator({ status }: { status: VoiceStatus }) {
  return <span role="status" aria-live="polite" aria-atomic="true" data-voice-status={status}
    className={`inline-flex items-center gap-2 rounded border-2 border-[#091f2c] px-2 py-1 text-xs font-semibold text-[#091f2c] ${status === "listening" ? "bg-[#d6f4cf]" : "bg-white"}`}>
    <span aria-hidden className={`h-2 w-2 rounded-full ${status === "listening" ? "bg-[#24773a]" : "bg-[#697780]"}`} />
    {LABELS[status]}
  </span>;
}
