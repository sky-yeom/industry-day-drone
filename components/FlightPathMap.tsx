"use client";

import Image from "next/image";
import { formatRoute, MONITOR_MAP, MONITORS } from "@/data/monitors";
import type { MonitorId, RoutePlanningState } from "@/lib/types";

interface FlightPathMapProps {
  planningState: RoutePlanningState;
}

const PHASE_LABELS: Record<RoutePlanningState["phase"], string> = {
  "selecting-destinations": "경로 대기",
  "selecting-order": "경로 구성 중",
  "awaiting-confirmation": "승인 대기",
  confirmed: "비행 준비 완료",
};

function routeSegments(route: MonitorId[]) {
  return route.slice(1).map((destination, index) => ({
    from: MONITOR_MAP[route[index]],
    to: MONITOR_MAP[destination],
  }));
}

export default function FlightPathMap({ planningState }: FlightPathMapProps) {
  const visibleRoute =
    planningState.phase === "confirmed"
      ? planningState.confirmedRoute
      : planningState.draftRoute;
  const isConfirmed = planningState.phase === "confirmed";
  const lineColor = isConfirmed ? "#0078d4" : "#8661c5";

  return (
    <section className="flex h-full w-full flex-col">
      <div className="flex items-center justify-between gap-4 px-5 pb-3 pt-5 sm:px-6 sm:pt-6">
        <div>
          <p className="mb-1 text-[10px] font-bold uppercase tracking-[0.2em] text-[#8661c5]">
            Live route orchestration
          </p>
          <h2 className="text-lg font-semibold tracking-[-0.02em] text-[#091f2c]">
            비행 경로
          </h2>
          <p className="mt-1 text-xs text-[#8c8279]">
            {visibleRoute.length > 0 ? formatRoute(visibleRoute) : "선택된 경로 없음"}
          </p>
        </div>
        <span
          className={`flex items-center gap-2 rounded-full px-3 py-2 text-xs font-semibold ${
            isConfirmed
              ? "bg-[#0078d4] text-white"
              : "bg-[#eee8f7] text-[#463668]"
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              isConfirmed ? "bg-[#d4ec8e]" : "bg-[#8661c5]"
            }`}
          />
          {PHASE_LABELS[planningState.phase]}
        </span>
      </div>

      <div className="relative min-h-0 flex-1 px-3 pb-3 sm:px-4 sm:pb-4">
        <div className="relative h-full min-h-[390px] overflow-hidden rounded-2xl border border-[#ded8ea] bg-[linear-gradient(145deg,#ffffff_0%,#f3effb_56%,#e7f4fc_100%)]">
          <div className="dot-field absolute inset-0 opacity-55 [mask-image:linear-gradient(to_bottom,black,transparent_88%)]" />
          <div className="absolute -left-20 bottom-[-8rem] h-72 w-72 rounded-full bg-[#c5b4e3]/70 blur-3xl" />
          <div className="absolute -right-20 top-[-6rem] h-64 w-64 rounded-full bg-[#8dc8e8]/55 blur-3xl" />

          <div className="absolute left-5 top-5 z-20 flex items-center gap-2 rounded-full border border-white/80 bg-white/70 px-3 py-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-[#2a446f] shadow-sm backdrop-blur">
            <span className="h-2 w-2 rounded-full bg-[#49c5b1]" />
            Digital twin workspace
          </div>

          <svg
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            className="pointer-events-none absolute inset-0 z-10 h-full w-full"
            aria-hidden="true"
          >
            <defs>
              <filter id="route-glow" x="-40%" y="-40%" width="180%" height="180%">
                <feGaussianBlur stdDeviation="0.55" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
              <marker
                id="route-arrow"
                markerWidth="5"
                markerHeight="5"
                refX="4"
                refY="2.5"
                orient="auto"
              >
                <path d="M0,0 L5,2.5 L0,5 Z" fill={lineColor} />
              </marker>
            </defs>
            {routeSegments(visibleRoute).map(({ from, to }, index) => (
              <line
                key={`${from.id}-${to.id}-${index}`}
                x1={from.x}
                y1={from.y}
                x2={to.x}
                y2={to.y}
                stroke={lineColor}
                strokeWidth="1.1"
                strokeDasharray={isConfirmed ? undefined : "3 2"}
                strokeLinecap="round"
                vectorEffect="non-scaling-stroke"
                markerEnd="url(#route-arrow)"
                filter="url(#route-glow)"
              />
            ))}
          </svg>

          {MONITORS.map((monitor) => {
            const routeIndex = visibleRoute.indexOf(monitor.id);
            return (
              <div
                key={monitor.id}
                className={`absolute z-20 w-[36%] max-w-52 -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-xl border bg-white/95 shadow-[0_12px_28px_rgba(42,68,111,0.13)] backdrop-blur transition-[transform,box-shadow,border-color] duration-200 hover:-translate-y-[52%] hover:shadow-[0_18px_36px_rgba(42,68,111,0.18)] ${
                  routeIndex >= 0
                    ? isConfirmed
                      ? "border-[#0078d4] ring-4 ring-[#0078d4]/10"
                      : "border-[#8661c5] ring-4 ring-[#8661c5]/10"
                    : "border-white/90"
                }`}
                style={{ left: `${monitor.x}%`, top: `${monitor.y}%` }}
              >
                <div className="relative aspect-[16/8] bg-[#eee8f7]">
                  <Image
                    src={monitor.image}
                    alt={`${monitor.label} camera placeholder`}
                    fill
                    className="object-cover"
                    sizes="(max-width: 768px) 38vw, 19vw"
                  />
                  {routeIndex >= 0 && (
                    <span
                      className={`absolute left-2.5 top-2.5 flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold text-white shadow-lg ${
                        isConfirmed ? "bg-[#0078d4]" : "bg-[#8661c5]"
                      }`}
                    >
                      {routeIndex + 1}
                    </span>
                  )}
                </div>
                <div className="flex items-center justify-between gap-2 px-3 py-2.5">
                  <span className="text-sm font-semibold text-[#091f2c]">
                    {monitor.label}
                  </span>
                  <span className="rounded-full bg-[#f4f3f5] px-2 py-1 text-[9px] font-bold uppercase tracking-[0.12em] text-[#8c8279]">
                    {routeIndex >= 0 ? `${routeIndex + 1}번째` : "대기"}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="flex items-center gap-2 px-5 pb-5 text-xs text-[#5c4738] sm:px-6">
        <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[#ffe399] text-[10px] font-bold text-[#7f5a1a]">
          i
        </span>
        {planningState.phase === "selecting-destinations" &&
          "어느 모니터부터 갈지 말씀해 주세요."}
        {planningState.phase === "selecting-order" &&
          "두 번째 경유지를 정하면 남은 한 곳은 자동으로 추가됩니다."}
        {planningState.phase === "awaiting-confirmation" &&
          "제안된 경로를 확인하고 음성으로 확정해 주세요."}
        {planningState.phase === "confirmed" &&
          "파란색 실선 경로가 확정되어 비행 준비가 끝났습니다."}
      </div>
    </section>
  );
}
