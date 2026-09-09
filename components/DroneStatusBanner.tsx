"use client";

import type { DashboardState, DroneStopState } from "@/lib/types";

const STOP_LABELS: Record<DroneStopState, string> = {
  not_requested: "실제 비행 상태 확인 중",
  requesting: "중지 요청 전송 중 · 실제 정지 미확인",
  stop_requested: "중지 요청 접수 · 실제 정지 미확인, RC로 확인하세요",
  confirmed: "PC 서비스가 물리 정지를 확인했습니다",
  unknown: "중지 결과 미확인 · 즉시 RC로 제어·착륙 상태를 확인하세요",
  awaiting_manual: "자동 이동 종료 · RC 수동 착륙 및 모터 정지 확인 대기",
};
const FLIGHT_LABELS: Record<string, string> = {
  idle: "출발 전", requesting: "출발 요청 중", accepted: "임무 접수", preflight: "비행 준비 확인",
  taking_off: "이륙 중", running: "경로 비행 중", returning: "복귀 중",
  awaiting_rc_landing: "수동 착륙 대기", completed: "비행 절차 완료",
  stop_requested: "중지 요청됨", stopped: "중지 절차 종료", failed: "비행 오류", outcome_unknown: "비행 결과 미확인",
};

export default function DroneStatusBanner({ state, connected, onAbort }: {
  state: DashboardState; connected: boolean; onAbort: () => void;
}) {
  const live = state.droneControlMode === "live";
  const launched = !!state.droneMissionId || state.droneState === "requesting";
  const disconnected = live && launched && !connected && state.droneStopState !== "confirmed";
  return <div role={disconnected || state.droneStopState === "unknown" ? "alert" : "status"}
    className="mx-4 mt-3 flex shrink-0 flex-wrap items-center justify-between gap-2 rounded-xl border border-[#ded8ea] bg-white px-3 py-2 text-xs leading-5 text-[#463668]">
    <div>
      <strong>{live ? "실제 드론" : "모의 비행"}</strong>
      <span> · {state.mode === "azure" ? "Azure 이미지 분석" : "모의 이미지 분석"}</span>
      {live && <p>{disconnected ? "관제 연결 끊김 · 중지 결과 미확인. RC로 제어·착륙 상태를 확인하세요."
        : launched ? `${FLIGHT_LABELS[state.droneState] ?? "상태 확인 중"} · ${STOP_LABELS[state.droneStopState]}`
        : "확정 경로를 요청한 뒤 실제 도착·촬영 근거를 기다립니다."}</p>}
      {live && launched && <p className="text-[10px] text-[#6e6575]">훈련 결과 완료와 실제 정지·착륙 확인은 별도로 표시됩니다.</p>}
    </div>
    {live && launched && connected && state.droneStopState === "not_requested" &&
      <button type="button" onClick={onAbort} className="rounded-lg border border-red-300 px-3 py-1 font-semibold text-red-900">드론 중지 요청</button>}
  </div>;
}
