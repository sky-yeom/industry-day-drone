"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { formatRoute, MONITOR_MAP, MONITORS } from "@/data/monitors";
import { OUTCOME_LABELS, type DashboardState } from "@/lib/types";

export const MISSION_LABELS: Record<DashboardState["missionPhase"], string> = {
  briefing: "방문 순서 선택", ready: "출발 준비 완료", flying: "자동 비행 중",
  capturing: "현장 이미지 촬영 중", analyzing: "대상자 탐지 중",
  paused: "기술 오류 · 시계 일시 정지", complete: "구조 작전 종료", aborted: "작전 중단",
};

export function MissionCountdownSummary({ state, elapsedMs, connected }: {
  state: DashboardState; elapsedMs: number; connected: boolean;
}) {
  return <section aria-label="세 사람의 구조 시한과 현재 작전 상태" className="space-y-1">
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
      <strong>{MISSION_LABELS[state.missionPhase]}{state.activeMonitorId ? ` · 현장 ${state.activeMonitorId.slice(-1)}` : ""}</strong>
      <span className="tabular-nums">
        경과 {(elapsedMs / 1000).toFixed(1)}초 · {state.clockRunning ? connected ? "진행 중" : "연결 끊김 · 마지막 수신 상태" : "정지"}
      </span>
    </div>
    <div className="grid grid-cols-3 gap-2">
      {state.people.map((person) => {
        const remaining = Math.max(0, person.deadlineMs - (person.resolvedAtMs ?? elapsedMs));
        return <div key={person.id} className={`rounded-xl border px-2 py-1 ${state.activeMonitorId === person.monitorId ? "border-[#8661c5] bg-[#f0ebf7]" : "border-[#e2dce9] bg-white"}`}>
          <p className="text-xs font-semibold">현장 {person.monitorId.slice(-1)}{person.attempts >= 2 ? " · 2회 시도" : ""}</p>
          <p className="text-xs font-semibold tabular-nums">{person.outcome ? OUTCOME_LABELS[person.outcome] : `남은 ${(remaining / 1000).toFixed(1)}초`}</p>
          {!person.outcome && remaining === 0 && <p className="mt-1 text-xs">서버 판정 대기 중</p>}
        </div>;
      })}
    </div>
  </section>;
}

export default function FlightPathMap({ state }: {
  state: DashboardState;
}) {
  const mapRef = useRef<HTMLDivElement>(null);
  const [positions, setPositions] = useState(MONITORS.map((monitor) => monitor.y));
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const cards = Array.from(map.querySelectorAll<HTMLElement>(".monitor-card"));
    const observer = new ResizeObserver(() => {
      if (!map.clientHeight) return;
      const next = MONITORS.map((monitor, index) => {
        const inset = Math.min(50, ((cards[index].offsetHeight / 2 + 8) / map.clientHeight) * 100);
        return Math.max(inset, Math.min(100 - inset, monitor.y));
      });
      setPositions((previous) => next.every((y, index) => Math.abs(y - previous[index]) < 0.01) ? previous : next);
    });
    observer.observe(map);
    cards.forEach((card) => observer.observe(card));
    return () => observer.disconnect();
  }, []);
  const monitors = MONITORS.map((monitor, index) => ({
    ...monitor, y: positions[index],
  }));
  const route = state.confirmedRoute.length ? state.confirmedRoute : state.draftRoute;
  const isConfirmed = state.phase === "confirmed";
  const lineColor = isConfirmed ? "#0078d4" : "#8661c5";
  return <section className="flex h-full min-h-0 w-full flex-col gap-2 p-3">
    <div className="flex shrink-0 items-center justify-between gap-3 px-1">
      <div>
        <p className="mb-1 text-[10px] font-bold tracking-[0.2em] text-[#8661c5]">실시간 경로 관제</p>
        <h2 className="text-lg font-semibold text-[#091f2c]">비행 경로</h2>
        <p className="text-xs text-[#5c4738]">{route.length ? formatRoute(route) : state.promptPhase === "confirmed" ? "첫 번째로 갈 곳을 말해주세요" : "프롬프트 확인 후 경로를 정합니다"}</p>
      </div>
      <div className="shrink-0 text-right">
      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isConfirmed ? "bg-[#0078d4] text-white" : "bg-[#eee8f7] text-[#463668]"}`}>
        {state.promptPhase !== "confirmed" ? "프롬프트 대기" : isConfirmed ? "경로 확정" : route.length === 3 ? "확정 대기" : "경로 구성 중"}
      </span>
      </div>
    </div>
    <div ref={mapRef} className="route-map relative min-h-0 flex-1 overflow-hidden rounded-2xl border border-[#ded8ea] bg-[linear-gradient(145deg,#ffffff_0%,#f3effb_56%,#e7f4fc_100%)]">
      <div className="dot-field absolute inset-0 opacity-55 [mask-image:linear-gradient(to_bottom,black,transparent_88%)]" />
      <div className="absolute -left-20 bottom-[-8rem] h-72 w-72 rounded-full bg-[#c5b4e3]/70 blur-3xl" />
      <div className="absolute -right-20 top-[-6rem] h-64 w-64 rounded-full bg-[#8dc8e8]/55 blur-3xl" />
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="pointer-events-none absolute inset-0 z-10 h-full w-full" aria-hidden="true">
        <defs>
          <filter id="route-glow" x="-40%" y="-40%" width="180%" height="180%">
            <feGaussianBlur stdDeviation="0.55" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <marker id="route-arrow" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
            <path d="M0,0 L5,2.5 L0,5 Z" fill={lineColor} />
          </marker>
        </defs>
        {route.slice(1).map((id, index) => {
          const from = monitors.find((monitor) => monitor.id === route[index]) ?? MONITOR_MAP[route[index]];
          const to = monitors.find((monitor) => monitor.id === id) ?? MONITOR_MAP[id];
          return <line key={`${from.id}-${to.id}`} x1={from.x} y1={from.y} x2={to.x} y2={to.y}
            stroke={lineColor} strokeWidth="1.1" strokeDasharray={isConfirmed ? undefined : "3 2"}
            strokeLinecap="round" vectorEffect="non-scaling-stroke" markerEnd="url(#route-arrow)" filter="url(#route-glow)" />;
        })}
      </svg>
      {monitors.map((monitor) => {
        const person = state.people.find((entry) => entry.monitorId === monitor.id);
        const order = route.indexOf(monitor.id);
        return <article key={monitor.id}
          className={`monitor-card absolute z-20 w-[28%] max-w-64 -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-xl border bg-white/95 shadow-[0_12px_28px_rgba(42,68,111,0.13)] backdrop-blur ${order >= 0 ? isConfirmed ? "border-[#0078d4] ring-4 ring-[#0078d4]/10" : "border-[#8661c5] ring-4 ring-[#8661c5]/10" : "border-white/90"}`}
          style={{ left: `${monitor.x}%`, top: `${monitor.y}%` }}>
          <div className="monitor-preview relative bg-[#eee8f7]">
            <Image src={monitor.image} alt={`${monitor.label}의 가상 구조 현장 미리보기 · 분석 전 이미지`} fill unoptimized className="object-contain" sizes="(max-width: 768px) 30vw, 256px" />
            {order >= 0 && <span className={`absolute left-2 top-2 flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold text-white ${isConfirmed ? "bg-[#0078d4]" : "bg-[#8661c5]"}`}>{order + 1}</span>}
          </div>
          <div className="space-y-1 p-2">
            <h3 className="text-xs font-semibold text-[#091f2c]">{monitor.label} <span className="font-normal text-[#8661c5]">{order >= 0 ? `· ${order + 1}번째 방문` : ""}</span></h3>
            <p className="text-[11px] leading-snug text-[#5c4738]">{person?.clue}</p>
          </div>
        </article>;
      })}
    </div>
    <p className="shrink-0 text-[11px] leading-4 text-[#6e6575]">{state.promptPhase !== "confirmed" ? "탐지 프롬프트를 먼저 음성으로 설명하고 확인해주세요." : state.missionPhase === "briefing" ? "첫 두 방문지를 음성으로 선택하세요. 출발에 동의하면 자동 비행을 시작합니다." : "방문 순서와 이미지 분석 완료 시점에 따라 구조 결과가 달라집니다."}</p>
  </section>;
}
