"use client";

import Image from "next/image";
import { useEffect, useRef, useState, type Ref } from "react";
import DroneImagePanel from "@/components/DroneImagePanel";
import VoiceTurnIndicator from "@/components/VoiceTurnIndicator";
import type { VoiceStatus } from "@/lib/voiceClient";
import { MONITOR_MAP, MONITORS } from "@/data/monitors";
import { BOARDING_MIRRORED, MAP_MARKER_ENTRY, MAP_MARKER_HEIGHT, MAP_MARKER_SRC, MAP_MARKER_WIDTH } from "@/lib/gibbyDroneSprite";
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
      <strong>{MISSION_LABELS[state.missionPhase]}{state.activeMonitorId ? ` · ${MONITOR_MAP[state.activeMonitorId].label}` : ""}</strong>
      <span className="tabular-nums">
        경과 {(elapsedMs / 1000).toFixed(1)}초 · {state.clockRunning ? connected ? "진행 중" : "연결 끊김 · 마지막 수신 상태" : "정지"}
      </span>
    </div>
    <div className="grid grid-cols-3 gap-2">
      {state.people.map((person) => {
        const remaining = Math.max(0, person.deadlineMs - (person.resolvedAtMs ?? elapsedMs));
        return <div key={person.id} className={`pixel-panel px-2 py-1 ${state.activeMonitorId === person.monitorId ? "bg-[#f0ebf7]" : "bg-white"}`}>
          <p className="text-xs font-semibold">{MONITOR_MAP[person.monitorId].label}{person.attempts >= 2 ? " · 2회 시도" : ""}</p>
          <p className="text-xs font-semibold tabular-nums">{person.outcome ? OUTCOME_LABELS[person.outcome] : `남은 ${(remaining / 1000).toFixed(1)}초`}</p>
          {!person.outcome && remaining === 0 && <p className="mt-1 text-xs">서버 판정 대기 중</p>}
        </div>;
      })}
    </div>
  </section>;
}

/**
 * Route step: a real map (public/gibby/map.png, matching the island art
 * Gibby unrolls during the map-finding transition) instead of the old
 * abstract dot-field/blob background. Each scenario (splash/rubble/fire)
 * gets a location-pin.png pin positioned exactly over its spot on the map
 * art (data/emergency-triage.json monitor x/y were remapped to match this
 * art); pins stay hidden until the user picks that stop into the route,
 * then pop in, and a dashed path connects picked pins in the order chosen.
 *
 * This screen now also persists through the whole mission (no separate
 * full-screen "images" step anymore, see app/page.tsx): once Gibby boards
 * the drone (`boarded`), a small live drone marker eases between pins on
 * the map tracking `state.activeMonitorId` in real time, and the right
 * column swaps from the clue cards below to the drone-image panel + the
 * 3 rescue timers.
 */
export default function FlightPathMap({ state, boarded = false, elapsedMs, connected, departing = false, markerRef,
  voiceStatus, onMapReady, onMapError }: {
  state: DashboardState;
  boarded?: boolean;
  elapsedMs: number;
  connected: boolean;
  departing?: boolean;
  markerRef?: Ref<HTMLDivElement>;
  voiceStatus?: VoiceStatus;
  onMapReady?: () => void;
  onMapError?: (message: string) => void;
}) {
  const [mapReady, setMapReady] = useState(false);
  const mapCallbacks = useRef({ onMapReady, onMapError });
  useEffect(() => { mapCallbacks.current = { onMapReady, onMapError }; }, [onMapReady, onMapError]);
  useEffect(() => {
    if (mapReady) return;
    const timer = window.setTimeout(() => {
      mapCallbacks.current.onMapError?.("지도를 불러오지 못했어. 현장 설명을 보고 진행해 줘.");
      setMapReady(true);
    }, 8000);
    return () => window.clearTimeout(timer);
  }, [mapReady]);
  useEffect(() => {
    if (!mapReady || departing || boarded) return;
    let paint: number | undefined;
    const frame = requestAnimationFrame(() => {
      paint = requestAnimationFrame(() => mapCallbacks.current.onMapReady?.());
    });
    return () => {
      cancelAnimationFrame(frame);
      if (paint !== undefined) cancelAnimationFrame(paint);
    };
  }, [mapReady, departing, boarded, state.runId]);
  const route = state.confirmedRoute.length ? state.confirmedRoute : state.draftRoute;
  const isConfirmed = state.phase === "confirmed";
  const lineColor = isConfirmed ? "#0078d4" : "#8661c5";
  const orderedPicked = MONITORS
    .map((monitor) => ({ monitor, order: route.indexOf(monitor.id) }))
    .filter((entry) => entry.order >= 0)
    .sort((a, b) => a.order - b.order);
  // Live drone marker target: the site the backend is actually working
  // (state.activeMonitorId) once boarding has finished, falling back to the
  // first confirmed stop before the backend has reported an active site yet
  // (e.g. right after launch, still climbing out).
  const activeMonitorId = state.activeMonitorId ?? route[0] ?? null;
  const droneMarkerMonitor = boarded && activeMonitorId ? MONITOR_MAP[activeMonitorId] : null;
  // Entrance: mount at the off-map corner (MAP_MARKER_ENTRY, roughly where
  // Gibby's dock overlay visually sits) then flip to the real target
  // position one frame later, so the very first move is an actual CSS
  // transition (flying in from the corner) rather than appearing already
  // on the pin.
  const [markerArrived, setMarkerArrived] = useState(false);
  useEffect(() => {
    if (!boarded) {
      const id = window.setTimeout(() => setMarkerArrived(false), 0);
      return () => window.clearTimeout(id);
    }
    const id = requestAnimationFrame(() => setMarkerArrived(true));
    return () => cancelAnimationFrame(id);
  }, [boarded]);
  const markerPos = markerArrived && droneMarkerMonitor ? droneMarkerMonitor : MAP_MARKER_ENTRY;

  return <section className={`mission-workspace flex h-full min-h-0 w-full flex-col gap-3 p-3 sm:p-4 ${departing ? "mission-workspace--exiting" : ""}`}
    inert={departing} aria-hidden={departing || undefined}>
    <div className="mission-workspace-heading flex shrink-0 items-center justify-between gap-3 px-1">
      <div>
        <div className="flex flex-wrap items-baseline gap-2">
          <p className="text-[10px] font-bold tracking-[0.2em] text-[#091f2c]">실시간 경로 관제</p>
          <h2 className="text-lg font-semibold text-[#091f2c]">비행경로</h2>
          {voiceStatus && <VoiceTurnIndicator status={voiceStatus} />}
        </div>
      </div>
      <div className="shrink-0 text-right">
        <span className={`pixel-panel px-3 py-1.5 text-xs font-semibold text-[#091f2c] ${isConfirmed ? "bg-[#0078d4]" : "bg-white"}`}>
          {isConfirmed ? "경로 확정" : route.length === 3 ? "확정 대기" : "경로 구성 중"}
        </span>
      </div>
    </div>

    <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,0.8fr)]">
      <div className="mission-map pixel-frame pixel-rendering relative mx-auto aspect-[3/2] w-full max-w-[820px] overflow-hidden">
        <Image src="/gibby/map.png" alt="탐색 지역 지도" fill unoptimized loading="eager"
          className="object-contain" sizes="(max-width: 1024px) 90vw, 820px"
          onLoad={() => setMapReady(true)} onError={() => {
            mapCallbacks.current.onMapError?.("지도를 불러오지 못했어. 현장 설명을 보고 진행해 줘.");
            setMapReady(true);
          }} />
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="pointer-events-none absolute inset-0 z-10 h-full w-full" aria-hidden="true">
          <defs>
            {/* userSpaceOnUse (not the default objectBoundingBox) — a
                perfectly horizontal or vertical route line has a zero-height
                or zero-width bounding box, and objectBoundingBox percentages
                degenerate to a zero-area filter region for those, which
                makes the browser silently clip the whole line. Fixed
                viewBox-space bounds avoid that regardless of a line's angle. */}
            <filter id="route-glow" filterUnits="userSpaceOnUse" x="-10" y="-10" width="120" height="120">
              <feGaussianBlur stdDeviation="0.55" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
            <marker id="route-arrow" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
              <path d="M0,0 L5,2.5 L0,5 Z" fill={lineColor} />
            </marker>
          </defs>
          {orderedPicked.slice(1).map((entry, index) => {
            const from = orderedPicked[index].monitor;
            const to = entry.monitor;
            return <line key={`${from.id}-${to.id}`} x1={from.x} y1={from.y} x2={to.x} y2={to.y}
              stroke={lineColor} strokeWidth="1.1" strokeDasharray="3 2"
              strokeLinecap="round" vectorEffect="non-scaling-stroke" markerEnd="url(#route-arrow)" filter="url(#route-glow)" />;
          })}
        </svg>
        {MONITORS.map((monitor) => {
          const order = route.indexOf(monitor.id);
          const picked = order >= 0;
          return <div key={monitor.id}
            className={`absolute z-20 -translate-x-1/2 -translate-y-full transition-all duration-500 ease-out ${picked ? "scale-100 opacity-100" : "scale-0 opacity-0"}`}
            style={{ left: `${monitor.x}%`, top: `${monitor.y}%` }}>
            <div className="relative">
              <Image src="/gibby/location-pin.png" alt={`${monitor.label} 위치`} width={40} height={40} unoptimized className="pixel-rendering h-10 w-10 drop-shadow-[2px_2px_0_#091f2c]" />
              {picked && <span className={`absolute -right-1 -top-1 flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold text-white ${isConfirmed ? "bg-[#0078d4]" : "bg-[#8661c5]"}`}>{order + 1}</span>}
            </div>
          </div>;
        })}
        {boarded && !departing && (
          <div
            ref={markerRef}
            aria-label="드론 현재 위치" role="img"
            className="absolute z-30 -translate-x-1/2 -translate-y-1/2 transition-[left,top] duration-[2800ms] ease-in-out"
            style={{ left: `${markerPos.x}%`, top: `${markerPos.y}%` }}
          >
            <div
              className="pixel-rendering drop-shadow-[2px_2px_0_#091f2c]"
              style={{ width: MAP_MARKER_WIDTH, height: MAP_MARKER_HEIGHT, transform: BOARDING_MIRRORED ? "scaleX(-1)" : undefined }}
            >
              <Image src={MAP_MARKER_SRC} alt="" width={MAP_MARKER_WIDTH} height={MAP_MARKER_HEIGHT} unoptimized className="pixel-rendering h-full w-full object-contain" />
            </div>
          </div>
        )}
      </div>

      <div className={`mission-images flex min-h-0 flex-col gap-2 ${boarded ? "" : "overflow-y-auto"}`}>
        {boarded ? (
          <>
            <div className="pixel-panel shrink-0 bg-white p-3">
              <MissionCountdownSummary state={state} elapsedMs={elapsedMs} connected={connected} />
            </div>
            <div className="min-h-0 flex-1"><DroneImagePanel captures={state.captures} /></div>
          </>
        ) : MONITORS.map((monitor) => {
          const person = state.people.find((entry) => entry.monitorId === monitor.id);
          const order = route.indexOf(monitor.id);
          return <article key={monitor.id}
            className={`pixel-panel shrink-0 p-3 ${order >= 0 ? isConfirmed ? "bg-[#eaf3fb]" : "bg-[#f0ebf7]" : "bg-white"}`}>
            <h3 className="text-xs font-semibold text-[#091f2c]">{monitor.label} <span className="font-normal text-[#091f2c]">{order >= 0 ? `· ${order + 1}번째 방문` : ""}</span></h3>
            <p className="mt-1 text-[11px] leading-snug text-[#091f2c]">{person?.clue}</p>
          </article>;
        })}
      </div>
    </div>

    {state.promptPhase === "confirmed" && state.missionPhase === "briefing" && <p className="mission-workspace-heading shrink-0 text-[11px] leading-4 text-[#091f2c] [text-shadow:1px_1px_0_#fff]">첫 두 방문지를 음성으로 선택하세요. 출발에 동의하면 자동 비행을 시작합니다.</p>}
  </section>;
}
