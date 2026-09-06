"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import DashboardLayout from "@/components/DashboardLayout";
import FlightPathMap from "@/components/FlightPathMap";
import DroneImagePanel from "@/components/DroneImagePanel";
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
import type { ChatMessage, RoutePlanningState } from "@/lib/types";

let messageId = 0;
function nextId() {
  messageId += 1;
  return `msg-${messageId}`;
}

export default function Home() {
  const [activeWorkspace, setActiveWorkspace] = useState<"route" | "images">(
    "route"
  );
  const [planningState, setPlanningState] =
    useState<RoutePlanningState>(INITIAL_ROUTE_STATE);
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
      onRouteState: (state) => {
        setPlanningState(state);
        if (state.phase === "confirmed") {
          setActiveWorkspace("images");
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
    // 보내는 데 실패하면(응답 생성 중) 아무것도 건드리지 않는다. 여기서 먼저
    // streamingRef를 지우면 진행 중이던 "…" 말풍선이 미아가 된다.
    if (!session.sendText(prompt)) return;
    // 타이핑한 입력은 전사되어 돌아오지 않으므로 화면에 직접 그린다.
    streamingRef.current.user = null;
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
