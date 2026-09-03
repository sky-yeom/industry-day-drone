"use client";

import { useCallback, useState } from "react";
import DashboardLayout from "@/components/DashboardLayout";
import FlightPathMap from "@/components/FlightPathMap";
import AnomalyGallery from "@/components/AnomalyGallery";
import ChatPanel from "@/components/ChatPanel";
import { getAgentResponse } from "@/lib/mockAgent";
import { formatRoute, INITIAL_ROUTE_STATE } from "@/data/monitors";
import type { AnomalyResult, ChatMessage, RoutePlanningState } from "@/lib/types";

let messageId = 0;
function nextId() {
  messageId += 1;
  return `msg-${messageId}`;
}

const INITIAL_MESSAGES: ChatMessage[] = [
  {
    id: nextId(),
    role: "agent",
    text: "Hi! Which monitor should the drone visit first: Monitor 1, Monitor 2, or Monitor 3?",
    timestamp: Date.now(),
  },
];

export default function Home() {
  const [planningState, setPlanningState] =
    useState<RoutePlanningState>(INITIAL_ROUTE_STATE);
  const [anomalies, setAnomalies] = useState<AnomalyResult[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>(INITIAL_MESSAGES);
  const [isThinking, setIsThinking] = useState(false);

  const handleSend = useCallback((prompt: string) => {
    const userMessage: ChatMessage = {
      id: nextId(),
      role: "user",
      text: prompt,
      timestamp: Date.now(),
    };
    setMessages((prev) => [...prev, userMessage]);
    setIsThinking(true);

    // Simulate a short "thinking" delay so the mock agent feels responsive rather than instant.
    window.setTimeout(() => {
      const response = getAgentResponse(prompt, planningState);
      setPlanningState(response.state);
      setAnomalies(response.anomalies);
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: "agent", text: response.reply, timestamp: Date.now() },
      ]);
      setIsThinking(false);
    }, 500);
  }, [planningState]);

  const headerStatus =
    planningState.phase === "confirmed"
      ? `Confirmed route: ${formatRoute(planningState.confirmedRoute)}`
      : planningState.phase === "awaiting-confirmation"
        ? `Proposed route: ${formatRoute(planningState.draftRoute)}`
        : planningState.phase === "selecting-order"
          ? "Building route order"
          : "Choose the first monitor";

  return (
    <DashboardLayout
      header={
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 px-4 py-3">
          <div>
            <h1 className="text-lg font-semibold text-zinc-100">Drone Survey Dashboard</h1>
            <p className="text-xs text-zinc-500">Industry Day — Live Flight Planning Demo</p>
          </div>
          <span className="rounded-full bg-zinc-800 px-3 py-1 text-xs font-medium text-emerald-300">
            {headerStatus}
          </span>
        </header>
      }
      pathPanel={<FlightPathMap planningState={planningState} />}
      anomalyPanel={<AnomalyGallery anomalies={anomalies} />}
      chatPanel={
        <ChatPanel messages={messages} onSend={handleSend} isThinking={isThinking} />
      }
    />
  );
}
