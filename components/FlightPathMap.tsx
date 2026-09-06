"use client";

import Image from "next/image";
import { formatRoute, MONITOR_MAP, MONITORS } from "@/data/monitors";
import type { MonitorId, RoutePlanningState } from "@/lib/types";

interface FlightPathMapProps {
  planningState: RoutePlanningState;
}

const PHASE_LABELS: Record<RoutePlanningState["phase"], string> = {
  "selecting-destinations": "첫 경유지 선택",
  "selecting-order": "순서 정하는 중",
  "awaiting-confirmation": "확정 대기",
  confirmed: "경로 확정됨",
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
  const lineColor = isConfirmed ? "#34d399" : "#facc15";

  return (
    <div className="flex h-full w-full flex-col bg-slate-950">
      <div className="flex items-center justify-between border-b border-emerald-900/60 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-emerald-300">
            비행 경로
          </h2>
          <p className="text-xs text-emerald-400/80">
            {visibleRoute.length > 0 ? formatRoute(visibleRoute) : "선택된 경로 없음"}
          </p>
        </div>
        <span
          className={`rounded-full px-3 py-1 text-xs font-medium ${
            isConfirmed
              ? "bg-emerald-800/70 text-emerald-100"
              : "bg-amber-900/60 text-amber-200"
          }`}
        >
          {PHASE_LABELS[planningState.phase]}
        </span>
      </div>

      <div className="relative min-h-0 flex-1 p-4">
        <div className="relative h-full min-h-[360px] overflow-hidden rounded-xl border border-slate-700 bg-[radial-gradient(circle_at_center,rgba(16,185,129,0.12),transparent_65%)]">
          <svg
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            className="pointer-events-none absolute inset-0 z-10 h-full w-full"
            aria-hidden="true"
          >
            <defs>
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
              />
            ))}
          </svg>

          {MONITORS.map((monitor) => {
            const routeIndex = visibleRoute.indexOf(monitor.id);
            return (
              <div
                key={monitor.id}
                className={`absolute z-20 w-[38%] max-w-48 -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-xl border-2 bg-slate-900 shadow-xl transition-colors ${
                  routeIndex >= 0
                    ? isConfirmed
                      ? "border-emerald-400"
                      : "border-amber-300"
                    : "border-slate-700"
                }`}
                style={{ left: `${monitor.x}%`, top: `${monitor.y}%` }}
              >
                <div className="relative aspect-[16/9] bg-slate-800">
                  <Image
                    src={monitor.image}
                    alt={`${monitor.label} camera placeholder`}
                    fill
                    className="object-cover"
                    sizes="(max-width: 768px) 38vw, 19vw"
                  />
                  {routeIndex >= 0 && (
                    <span
                      className={`absolute left-2 top-2 flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold text-slate-950 ${
                        isConfirmed ? "bg-emerald-300" : "bg-amber-300"
                      }`}
                    >
                      {routeIndex + 1}
                    </span>
                  )}
                </div>
                <div className="flex items-center justify-between gap-2 px-3 py-2">
                  <span className="text-sm font-semibold text-slate-100">
                    {monitor.label}
                  </span>
                  <span className="text-[10px] uppercase tracking-wide text-slate-400">
                    {routeIndex >= 0 ? `${routeIndex + 1}번째` : "대기"}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="border-t border-emerald-900/60 px-4 py-2 text-xs text-emerald-400/70">
        {planningState.phase === "selecting-destinations" &&
          "어느 모니터부터 갈지 말씀해 주세요."}
        {planningState.phase === "selecting-order" &&
          "두 번째 경유지를 정하면 남은 한 곳은 자동으로 추가됩니다."}
        {planningState.phase === "awaiting-confirmation" &&
          "제안된 경로를 확인하고 음성으로 확정해 주세요."}
        {planningState.phase === "confirmed" &&
          "초록색 실선 경로가 확정되어 비행 준비가 끝났습니다."}
      </div>
    </div>
  );
}
