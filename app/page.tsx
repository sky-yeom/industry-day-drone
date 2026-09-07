"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import DashboardLayout from "@/components/DashboardLayout";
import FlightPathMap from "@/components/FlightPathMap";
import DroneImagePanel from "@/components/DroneImagePanel";
import VoiceControl from "@/components/VoiceControl";
import { formatRoute, INITIAL_ROUTE_STATE } from "@/data/monitors";
import {
  fetchRelayConfig,
  VoiceSession,
  type RelayConfig,
  type ToolActivity,
  type VoiceStatus,
} from "@/lib/voiceClient";
import type { RoutePlanningState } from "@/lib/types";

export default function Home() {
  const [activeWorkspace, setActiveWorkspace] = useState<"route" | "images">(
    "route"
  );
  const [planningState, setPlanningState] =
    useState<RoutePlanningState>(INITIAL_ROUTE_STATE);

  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [statusDetail, setStatusDetail] = useState<string>();
  const [level, setLevel] = useState(0);
  const [ttfaMs, setTtfaMs] = useState<number | null>(null);
  const [tools, setTools] = useState<ToolActivity[]>([]);
  const [relayConfig, setRelayConfig] = useState<RelayConfig | null>(null);

  const sessionRef = useRef<VoiceSession | null>(null);

  useEffect(() => {
    void fetchRelayConfig().then(setRelayConfig);
  }, []);

  const getSession = useCallback(() => {
    if (sessionRef.current) return sessionRef.current;

    sessionRef.current = new VoiceSession({
      onStatus: (next, detail) => {
        setStatus(next);
        setStatusDetail(detail);
      },
      onLevel: setLevel,
      // Transcript text is no longer displayed anywhere; the orb is the
      // entire voice UI now.
      onTranscript: () => {},
      onRouteState: (state) => {
        setPlanningState(state);
        if (state.phase === "confirmed") {
          setActiveWorkspace("images");
          // Route is locked in and the view has handed off to the drone
          // feed; the voice agent has nothing left to do, so end the
          // session automatically instead of leaving the mic open.
          void sessionRef.current?.stop();
        }
      },
      onTool: (activity) => {
        setTools((prev) => {
          const idx = prev.findIndex((t) => t.id === activity.id);
          if (idx === -1) return [...prev, activity];
          const next = [...prev];
          next[idx] = { ...next[idx], ...activity };
          return next;
        });
      },
      onTtfa: setTtfaMs,
      onBusy: () => {},
    });
    return sessionRef.current;
  }, []);

  const handleToggle = useCallback(async () => {
    const session = getSession();
    if (session.isRunning) {
      await session.stop();
      return;
    }
    setTools([]);
    setTtfaMs(null);
    try {
      await session.start();
    } catch {
      /* status already reflects the failure */
    }
  }, [getSession]);

  useEffect(() => {
    return () => {
      void sessionRef.current?.stop();
    };
  }, []);

  const headerStatus =
    planningState.phase === "confirmed"
      ? `확정 경로: ${formatRoute(planningState.confirmedRoute)}`
      : planningState.phase === "awaiting-confirmation"
        ? `제안 경로: ${formatRoute(planningState.draftRoute)}`
        : planningState.phase === "selecting-order"
          ? "순서 정하는 중"
          : "첫 경유지 선택";

  return (
    <DashboardLayout
      header={
        <header className="flex min-h-16 flex-wrap items-center justify-between gap-4 px-1 py-2 sm:px-2">
          <div className="flex items-stretch gap-3">
            <div
              className="w-1 rounded-full bg-[linear-gradient(to_bottom,#8661c5,#0078d4,#49c5b1)]"
              aria-hidden="true"
            />
            <div>
              <div className="mb-0.5 flex items-center gap-2">
                <span className="text-[10px] font-bold uppercase tracking-[0.22em] text-[#8661c5]">
                  Microsoft Foundry
                </span>
                <span className="h-1 w-1 rounded-full bg-[#49c5b1]" />
                <span className="text-[10px] font-medium uppercase tracking-[0.16em] text-[#8c8279]">
                  Industry Day
                </span>
              </div>
              <h1 className="text-xl font-semibold tracking-[-0.03em] text-[#091f2c] sm:text-2xl">
                드론 경로 관제
              </h1>
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {relayConfig && (
              <span className="hidden rounded-full border border-[#d7d2cb] bg-white/65 px-3 py-1.5 font-mono text-[10px] text-[#5c4738] lg:inline">
                {relayConfig.model} / {relayConfig.voice} / {relayConfig.region}
              </span>
            )}
            <span className="flex items-center gap-2 rounded-full border border-[#c5b4e3]/70 bg-white/75 px-3.5 py-2 text-xs font-semibold text-[#463668] shadow-sm">
              <span className="h-2 w-2 rounded-full bg-[#8661c5] shadow-[0_0_0_4px_rgba(134,97,197,0.12)]" />
              {headerStatus}
            </span>
          </div>
        </header>
      }
      pathPanel={
        <div className="flex h-full min-h-0 flex-col">
          <div
            className="flex h-14 shrink-0 items-end gap-6 border-b border-[#ded8ea]/80 px-5"
            role="tablist"
            aria-label="드론 작업 화면"
          >
            <button
              type="button"
              role="tab"
              aria-selected={activeWorkspace === "route"}
              onClick={() => setActiveWorkspace("route")}
              className={`relative h-full border-b-2 px-0.5 pt-1 text-xs font-semibold transition-colors ${
                activeWorkspace === "route"
                  ? "border-[#8661c5] text-[#463668]"
                  : "border-transparent text-[#8c8279] hover:text-[#463668]"
              }`}
            >
              비행 경로
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeWorkspace === "images"}
              onClick={() => setActiveWorkspace("images")}
              className={`relative flex h-full items-center gap-2 border-b-2 px-0.5 pt-1 text-xs font-semibold transition-colors ${
                activeWorkspace === "images"
                  ? "border-[#8661c5] text-[#463668]"
                  : "border-transparent text-[#8c8279] hover:text-[#463668]"
              }`}
            >
              드론 이미지
              <span className="h-1.5 w-1.5 rounded-full bg-[#8661c5]" />
            </button>
          </div>
          <div className="min-h-0 flex-1">
            {activeWorkspace === "route" ? (
              <FlightPathMap planningState={planningState} />
            ) : (
              <DroneImagePanel />
            )}
          </div>
        </div>
      }
      chatPanel={
        <div className="flex h-full w-full flex-col">
          <div className="flex items-start justify-between gap-3 px-5 pb-3 pt-5 sm:px-6 sm:pt-6">
            <div>
              <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-[#8661c5]">
                Azure AI voice agent
              </p>
              <h2 className="text-lg font-semibold tracking-[-0.02em] text-[#091f2c]">
                Foundry Copilot
              </h2>
            </div>
          </div>
          <div className="relative min-h-0 flex-1">
            <VoiceControl
              status={status}
              detail={statusDetail}
              level={level}
              ttfaMs={ttfaMs}
              tools={tools}
              onToggle={handleToggle}
            />
          </div>
        </div>
      }
    />
  );
}
