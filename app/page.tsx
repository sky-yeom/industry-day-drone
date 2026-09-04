"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import DashboardLayout from "@/components/DashboardLayout";
import FlightPathMap from "@/components/FlightPathMap";
import AnomalyGallery from "@/components/AnomalyGallery";
import ChatPanel from "@/components/ChatPanel";
import VoiceControl from "@/components/VoiceControl";
import { formatRoute, INITIAL_ROUTE_STATE } from "@/data/monitors";
import {
  fetchRelayConfig,
  VoiceSession,
  type RelayConfig,
  type ToolActivity,
  type VoiceStatus,
} from "@/lib/voiceClient";
import type { AnomalyResult, ChatMessage, RoutePlanningState } from "@/lib/types";

let messageId = 0;
function nextId() {
  messageId += 1;
  return `msg-${messageId}`;
}

export default function Home() {
  const [planningState, setPlanningState] =
    useState<RoutePlanningState>(INITIAL_ROUTE_STATE);
  const [anomalies, setAnomalies] = useState<AnomalyResult[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);

  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [statusDetail, setStatusDetail] = useState<string>();
  const [level, setLevel] = useState(0);
  const [ttfaMs, setTtfaMs] = useState<number | null>(null);
  const [tools, setTools] = useState<ToolActivity[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const [relayConfig, setRelayConfig] = useState<RelayConfig | null>(null);

  const sessionRef = useRef<VoiceSession | null>(null);
  // Ids of the bubbles currently being streamed into, one per speaker.
  const streamingRef = useRef<{ user: string | null; agent: string | null }>({
    user: null,
    agent: null,
  });

  useEffect(() => {
    void fetchRelayConfig().then(setRelayConfig);
  }, []);

  const upsertTranscript = useCallback(
    (role: "user" | "agent", text: string, final: boolean) => {
      const existingId = streamingRef.current[role];

      // An empty final transcript means speech was detected but nothing was
      // recognised, so drop the reserved placeholder instead of leaving "…".
      if (final && !text.trim()) {
        if (existingId) {
          setMessages((prev) => prev.filter((m) => m.id !== existingId));
          streamingRef.current[role] = null;
        }
        return;
      }

      if (!text.trim()) return;

      if (existingId) {
        setMessages((prev) =>
          prev.map((m) => (m.id === existingId ? { ...m, text } : m))
        );
      } else {
        const id = nextId();
        streamingRef.current[role] = id;
        setMessages((prev) => [...prev, { id, role, text, timestamp: Date.now() }]);
      }

      if (final) streamingRef.current[role] = null;
    },
    []
  );

  const getSession = useCallback(() => {
    if (sessionRef.current) return sessionRef.current;

    sessionRef.current = new VoiceSession({
      onStatus: (next, detail) => {
        setStatus(next);
        setStatusDetail(detail);
      },
      onLevel: setLevel,
      onTranscript: upsertTranscript,
      onRouteState: (state, results) => {
        setPlanningState(state);
        setAnomalies(results);
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
      onBusy: setIsThinking,
    });
    return sessionRef.current;
  }, [upsertTranscript]);

  const handleToggle = useCallback(async () => {
    const session = getSession();
    if (session.isRunning) {
      await session.stop();
      return;
    }
    // Reset the visible conversation. The relay pushes authoritative route
    // state as soon as the new session opens.
    setMessages([]);
    setTools([]);
    setTtfaMs(null);
    streamingRef.current = { user: null, agent: null };
    try {
      await session.start();
    } catch {
      /* status already reflects the failure */
    }
  }, [getSession]);

  const handleSend = useCallback((prompt: string) => {
    const session = sessionRef.current;
    if (!session?.isRunning) return;
    // Typed input is never transcribed back, so echo it locally.
    streamingRef.current.user = null;
    if (!session.sendText(prompt)) return;
    setMessages((prev) => [
      ...prev,
      { id: nextId(), role: "user", text: prompt, timestamp: Date.now() },
    ]);
  }, []);

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

  const live = status !== "idle" && status !== "error";

  return (
    <DashboardLayout
      header={
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 px-4 py-3">
          <div>
            <h1 className="text-lg font-semibold text-zinc-100">드론 조사 대시보드</h1>
            <p className="text-xs text-zinc-500">Industry Day — 실시간 음성 비행 계획 데모</p>
          </div>
          <div className="flex items-center gap-3">
            {relayConfig && (
              <span className="hidden text-[11px] text-zinc-500 sm:inline">
                {relayConfig.model} · {relayConfig.voice} · {relayConfig.region}
              </span>
            )}
            <span className="rounded-full bg-zinc-800 px-3 py-1 text-xs font-medium text-emerald-300">
              {headerStatus}
            </span>
          </div>
        </header>
      }
      pathPanel={<FlightPathMap planningState={planningState} />}
      anomalyPanel={<AnomalyGallery anomalies={anomalies} />}
      chatPanel={
        <ChatPanel
          messages={messages}
          onSend={handleSend}
          isThinking={isThinking}
          disabledReason={
            live ? undefined : "세션을 시작하면 대화할 수 있습니다"
          }
          toolbar={
            <VoiceControl
              status={status}
              detail={statusDetail}
              level={level}
              ttfaMs={ttfaMs}
              tools={tools}
              onToggle={handleToggle}
            />
          }
        />
      }
    />
  );
}
