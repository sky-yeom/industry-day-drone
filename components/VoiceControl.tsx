"use client";

import type { ToolActivity, VoiceStatus } from "@/lib/voiceClient";
import VoiceOrb from "@/components/VoiceOrb";

const STATUS_LABEL: Record<VoiceStatus, string> = {
  idle: "세션 종료", connecting: "연결 중", listening: "듣는 중", speaking: "응답 중", error: "연결 오류",
};
const TOOL_LABELS: Record<string, string> = {
  confirm_prompt: "탐지 프롬프트 확정",
  select_stop: "방문지 선택", clear_route: "경로 초기화", confirm_route: "경로 확정",
  launch_mission: "드론 출발", retry_mission: "작업 재시도", abort_mission: "작전 중단",
  get_status: "작전 상태 확인",
};

export default function VoiceControl({ status, level, tools, onStart, onStop, startDisabled = false }: {
  status: VoiceStatus; level: number; tools: ToolActivity[];
  onStart: () => void; onStop: () => void;
  startDisabled?: boolean;
}) {
  const live = status !== "idle" && status !== "error";
  const telemetry = tools.slice(-2).map((tool) => `${TOOL_LABELS[tool.name] ?? "관제 요청"}: ${tool.facts ?? "처리 중"}`).join(" · ");
  return <section className="flex h-full min-h-0 w-full flex-col items-center justify-center gap-4 p-3">
    <div className="min-h-0 w-full max-w-40 flex-1 max-h-40">
      <VoiceOrb status={status} level={level} />
    </div>
    <span className="sr-only">{telemetry}</span>
    <p className="sr-only" role="status">{STATUS_LABEL[status]}</p>
    <div className="flex w-full shrink-0 items-center justify-center">
    {live ? <button type="button" onClick={onStop} aria-label="음성 세션 종료" title="음성 세션 종료"
      className="grid h-11 w-11 shrink-0 place-items-center rounded-full border-2 border-white bg-white/90 text-[#463668] shadow-[0_10px_24px_rgba(9,31,44,0.15)] transition hover:bg-[#eee8f7]">
      <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4"><path fill="currentColor" d="M6.4 5 5 6.4 10.6 12 5 17.6 6.4 19l5.6-5.6 5.6 5.6 1.4-1.4-5.6-5.6L19 6.4 17.6 5 12 10.6 6.4 5Z" /></svg>
    </button> : <button type="button" onClick={onStart} disabled={startDisabled} aria-label="새 음성 세션 시작" title={startDisabled ? "자동 작전 진행 중" : "새 음성 세션 시작"}
      className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-[#463668] text-white shadow-[0_10px_24px_rgba(70,54,104,0.3)] transition hover:bg-[#2a446f] disabled:cursor-not-allowed disabled:opacity-40">
      <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5"><path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.93V21h2v-2.07A7 7 0 0 0 19 12h-2Z" /></svg>
    </button>}
    </div>
  </section>;
}
