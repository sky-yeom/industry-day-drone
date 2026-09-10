"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import DashboardLayout from "@/components/DashboardLayout";
import FlightPathMap, { MISSION_LABELS, MissionCountdownSummary } from "@/components/FlightPathMap";
import DroneImagePanel from "@/components/DroneImagePanel";
import OpeningScreen from "@/components/OpeningScreen";
import PromptWorkspace from "@/components/PromptWorkspace";
import ResultsPanel from "@/components/ResultsPanel";
import VoiceControl from "@/components/VoiceControl";
import { INITIAL_ROUTE_STATE } from "@/data/monitors";
import { INITIAL_MISSION_STATE, SCENARIO_TITLE } from "@/data/scenario";
import { fetchRelayConfig, VoiceSession, type RelayConfig, type ToolActivity, type VoiceStatus } from "@/lib/voiceClient";
import type { ChatMessage, DashboardState } from "@/lib/types";

const INITIAL_STATE: DashboardState = { ...INITIAL_ROUTE_STATE, ...INITIAL_MISSION_STATE };
const WORKSPACES = [
  { id: "prompt", label: "프롬프트" },
  { id: "route", label: "비행 경로" },
  { id: "images", label: "드론 이미지" },
  { id: "results", label: "결과" },
] as const;
type Workspace = typeof WORKSPACES[number]["id"];

export default function Home() {
  const [started, setStarted] = useState(false);
  const [snapshot, setSnapshot] = useState({ state: INITIAL_STATE, receivedAt: 0 });
  const [now, setNow] = useState(0);
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [connected, setConnected] = useState(false);
  const [level, setLevel] = useState(0);
  const [tools, setTools] = useState<ToolActivity[]>([]);
  const [transcript, setTranscript] = useState<ChatMessage[]>([]);
  const [config, setConfig] = useState<RelayConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [debrief, setDebrief] = useState("");
  const [activeWorkspace, setActiveWorkspace] = useState<Workspace>("prompt");
  const sessionRef = useRef<VoiceSession | null>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const generationRef = useRef(0);
  const advancedToCapturesRef = useRef(false);
  const advancedToRouteRef = useRef(false);
  const advancedToResultsRef = useRef(false);
  const streamingRef = useRef<{ user: string | null; agent: string | null }>({ user: null, agent: null });
  const state = snapshot.state;
  const missionLaunched = state.clockRunning || state.elapsedMs > 0 || ["paused", "complete"].includes(state.missionPhase);

  useEffect(() => {
    transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight, behavior: "smooth" });
  }, [transcript]);

  useEffect(() => {
    let active = true;
    void fetchRelayConfig().then((value) => { if (active) setConfig(value); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!state.clockRunning || !connected) return;
    const timer = window.setInterval(() => setNow(performance.now()), 100);
    return () => window.clearInterval(timer);
  }, [state.clockRunning, connected]);

  useEffect(() => () => {
    ++generationRef.current;
    void sessionRef.current?.stop();
  }, []);

  const reset = useCallback(() => {
    ++generationRef.current;
    const previous = sessionRef.current;
    sessionRef.current = null;
    previous?.sendCommand("abort_mission");
    void previous?.stop();
    streamingRef.current = { user: null, agent: null };
    advancedToCapturesRef.current = false;
    advancedToRouteRef.current = false;
    advancedToResultsRef.current = false;
    setStarted(false);
    setSnapshot({ state: INITIAL_STATE, receivedAt: 0 });
    setNow(0);
    setStatus("idle");
    setConnected(false);
    setLevel(0);
    setTools([]);
    setTranscript([]);
    setError(null);
    setDebrief("");
    setActiveWorkspace("prompt");
  }, []);

  const start = useCallback(() => {
    if (sessionRef.current) return;
    const generation = ++generationRef.current;
    const current = () => generationRef.current === generation;
    setStarted(true);
    setStatus("connecting");
    void fetchRelayConfig().then((value) => { if (current()) setConfig(value); });
    const session = new VoiceSession({
      onStatus: (next, detail) => {
        if (!current()) return;
        setStatus(next);
        if (detail) setError(detail);
      },
      onError: (message) => { if (current()) setError(message); },
      onConnection: (value) => { if (current()) setConnected(value); },
      onDeparture: () => {
        if (!current() || advancedToResultsRef.current) return;
        advancedToCapturesRef.current = true;
        setActiveWorkspace("images");
      },
      onLevel: (peak) => { if (current()) setLevel(peak); },
      onDebrief: (text) => { if (current()) setDebrief(text); },
      onRouteState: (next) => {
        if (!current()) return;
        if (!advancedToRouteRef.current && next.promptPhase === "confirmed") {
          advancedToRouteRef.current = true;
          setActiveWorkspace("route");
        }
        if (!advancedToResultsRef.current &&
            (next.missionPhase === "complete" || next.missionPhase === "aborted")) {
          advancedToResultsRef.current = true;
          setActiveWorkspace("results");
        }
        const receivedAt = performance.now();
        setSnapshot((previous) => {
          if (previous.state.runId === next.runId && next.revision < previous.state.revision) return previous;
          return { state: next, receivedAt };
        });
        setNow(receivedAt);
      },
      onTranscript: (role, text, final) => {
        if (!current()) return;
        let id = streamingRef.current[role];
        if (final && !text.trim()) {
          setTranscript((previous) => previous.filter((message) => message.id !== id));
          streamingRef.current[role] = null;
          return;
        }
        if (!text.trim()) return;
        if (!id) {
          id = crypto.randomUUID();
          streamingRef.current[role] = id;
          const message = { id, role, text, timestamp: Date.now() };
          setTranscript((previous) => [...previous, message]);
        } else {
          setTranscript((previous) => previous.map((message) => message.id === id ? { ...message, text } : message));
        }
        if (final) streamingRef.current[role] = null;
      },
      onTool: (activity) => {
        if (!current()) return;
        setTools((previous) => {
          const index = previous.findIndex((tool) => tool.id === activity.id);
          if (index < 0) return [...previous, activity];
          return previous.map((tool, i) => i === index ? { ...tool, ...activity } : tool);
        });
      },
      onTtfa: () => {},
      onBusy: () => {},
    });
    sessionRef.current = session;
    void session.start().catch(() => {});
  }, []);

  const retryConnection = useCallback(() => {
    reset();
    start();
  }, [reset, start]);

  const stopVoice = useCallback(() => {
    ++generationRef.current;
    const session = sessionRef.current;
    sessionRef.current = null;
    session?.sendCommand("abort_mission");
    void session?.stop();
    setStatus("idle");
    setConnected(false);
    setLevel(0);
  }, []);

  if (!started) return <OpeningScreen onStart={start} />;

  // Display interpolation only: expiration, rescue and scoring remain relay-owned.
  const elapsedMs = state.elapsedMs + (state.clockRunning ? Math.max(0, now - snapshot.receivedAt) : 0);
  const visionReady = config?.visionReady ?? false;

  return <DashboardLayout
    header={<header className="flex min-h-16 flex-wrap items-center justify-between gap-4 px-1 py-2 sm:px-2">
      <div>
        <div className="mb-0.5 flex items-center gap-2">
          <span className="text-[10px] font-bold uppercase tracking-[0.22em] text-[#8661c5]">Microsoft Foundry</span>
          <span className="h-1 w-1 rounded-full bg-[#49c5b1]" />
          <span className="text-[10px] font-medium uppercase tracking-[0.16em] text-[#8c8279]">Industry Day</span>
        </div>
        <h1 className="text-xl font-semibold tracking-[-0.03em] text-[#091f2c] sm:text-2xl">{SCENARIO_TITLE}</h1>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex items-center gap-2 rounded-full border border-[#c5b4e3]/70 bg-white/75 px-3.5 py-2 text-xs font-semibold text-[#463668] shadow-sm">
          <span className="h-2 w-2 rounded-full bg-[#8661c5] shadow-[0_0_0_4px_rgba(134,97,197,0.12)]" />{state.promptPhase === "confirmed" ? MISSION_LABELS[state.missionPhase] : "탐지 프롬프트 작성"}
        </span>
      </div>
    </header>}
    pathPanel={<div className="flex h-full min-h-0 flex-col">
      <div className="flex h-11 shrink-0 items-end gap-4 border-b border-[#ded8ea]/80 px-4" role="tablist" aria-label="드론 작업 화면">
        {WORKSPACES.map((workspace, index) => <button
          key={workspace.id} id={`tab-${workspace.id}`} type="button" role="tab"
          aria-selected={activeWorkspace === workspace.id} aria-controls={`panel-${workspace.id}`}
          tabIndex={activeWorkspace === workspace.id ? 0 : -1}
          onClick={() => setActiveWorkspace(workspace.id)}
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
            event.preventDefault();
            const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? WORKSPACES.length - 1
              : (index + (event.key === "ArrowRight" ? 1 : -1) + WORKSPACES.length) % WORKSPACES.length;
            const next = WORKSPACES[nextIndex];
            setActiveWorkspace(next.id);
            document.getElementById(`tab-${next.id}`)?.focus();
          }}
          className={`relative flex h-full items-center border-b-2 px-0.5 pt-1 text-xs font-semibold transition-colors ${activeWorkspace === workspace.id ? "border-[#8661c5] text-[#463668]" : "border-transparent text-[#8c8279] hover:text-[#463668]"}`}
        >{workspace.label}</button>)}
      </div>
      <div className="flex min-h-0 flex-1 flex-col">
      {(!config || !visionReady) && <div role="alert" className="mx-4 mt-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm leading-6 text-amber-950">
        {config?.visionError || (config ? "이미지 분석 서비스가 준비되지 않아 출발할 수 없습니다." : "관제 서버 설정을 확인할 수 없습니다. 릴레이 실행 상태를 확인해 주세요.")}
      </div>}
      {state.error && <div role="alert" className="mx-4 mt-3 rounded-xl border border-red-200 bg-red-50 p-4 text-sm leading-6 text-red-900">
        <strong>{state.missionPhase === "paused" ? "시계가 일시 정지되었습니다. " : "작전 오류: "}</strong>{state.error}
        {state.missionPhase === "paused" && (status === "idle" && connected ? <div className="mt-2 flex gap-3">
          <button type="button" onClick={() => sessionRef.current?.sendCommand("retry_mission")} className="rounded-lg border border-red-300 px-3 py-1">다시 시도</button>
          <button type="button" onClick={() => sessionRef.current?.sendCommand("abort_mission")} className="rounded-lg border border-red-300 px-3 py-1">작전 중단</button>
        </div> : <p className="mt-2">계속하려면 “다시 시도해 줘”, 중단하려면 “작전을 중단해 줘”라고 말해주세요.</p>)}
      </div>}
      <div className="min-h-0 flex-1" id={`panel-${activeWorkspace}`} role="tabpanel" aria-labelledby={`tab-${activeWorkspace}`}>
        {activeWorkspace === "prompt" && <PromptWorkspace
          confirmed={state.promptPhase === "confirmed"} userPromptText={state.userPromptText} />}
        {activeWorkspace === "route" && <FlightPathMap state={state} />}
        {activeWorkspace === "images" && <div className="flex h-full min-h-0 flex-col">
          {missionLaunched && <div className="mx-3 shrink-0 pt-2">
            <MissionCountdownSummary state={state} elapsedMs={elapsedMs} connected={connected} />
          </div>}
          <div className="min-h-0 flex-1"><DroneImagePanel captures={state.captures} /></div>
        </div>}
        {activeWorkspace === "results" && <ResultsPanel state={state} debrief={debrief} onReset={reset} />}
      </div>
      </div>
    </div>}
    chatPanel={<aside className="voice-panel flex h-full min-h-0 w-full flex-col">
      <div className="voice-panel-heading flex shrink-0 flex-wrap items-start justify-between gap-2 px-4 pb-2 pt-3">
        <div>
          <p className="text-[10px] font-bold tracking-[0.2em] text-[#8661c5]">Azure AI 음성 에이전트</p>
          <h2 className="text-base font-semibold tracking-[-0.02em] text-[#091f2c]">Foundry Copilot</h2>
        </div>
        <span className="flex shrink-0 items-center gap-1.5 rounded-full border border-[#c5b4e3]/70 bg-white/75 px-3 py-1.5 text-[11px] font-semibold text-[#463668] shadow-sm">
          <span className={`h-1.5 w-1.5 rounded-full ${status === "error" ? "bg-[#e5484d]" : status === "idle" ? "bg-[#8c8279]" : "bg-[#49c5b1]"}`} />
          {status === "error" ? "세션 오류" : status === "connecting" ? "연결 중" : status === "idle" ? "세션 종료" : "세션 진행 중"}
        </span>
      </div>
      {error && <div role="alert" className="mx-5 mt-3 rounded-xl bg-red-50 p-3 text-sm leading-6 text-red-900">
        {error}<button onClick={() => setError(null)} className="ml-2 underline">알림 닫기</button>
      </div>}
      {status === "error" && <div className="mx-5 mt-3 space-y-2">
        <button onClick={retryConnection} className="rounded-xl bg-[#463668] px-4 py-3 text-sm font-semibold text-white">연결 다시 시도 · 새 작전</button>
        <p className="text-xs leading-5 text-[#6e6575]">연결이 끊긴 작전은 이어서 진행할 수 없습니다. 음성 관제 세션에 새로 연결합니다.</p>
      </div>}
      <div className="voice-body grid min-h-0 flex-1">
      <div className="voice-visualization min-h-0">
        <VoiceControl status={status} level={level} tools={tools} onStart={retryConnection} onStop={stopVoice}
          startDisabled={connected && missionLaunched && !["complete", "aborted"].includes(state.missionPhase)} />
      </div>
      <div ref={transcriptRef} className="soft-scrollbar min-h-0 space-y-3 overflow-y-auto border-t border-[#ded8ea] px-4 py-4 sm:px-5" aria-label="실시간 음성 대화">
        <h3 className="text-xs font-semibold text-[#8661c5]">실시간 대화</h3>
        {!transcript.length && <p className="text-xs leading-5 text-[#8c8279]">말씀하신 내용과 에이전트의 답변이 여기에 표시됩니다.</p>}
        {transcript.map((message) => <div key={message.id} className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}>
          <div className={`max-w-[94%] px-3.5 py-2.5 text-sm leading-relaxed shadow-sm ${message.role === "user" ? "rounded-[18px_18px_4px_18px] bg-[#463668] text-white" : "rounded-[18px_18px_18px_4px] border border-[#ded8ea] bg-white text-[#091f2c]"}`}>
            <p className={`mb-1 text-[10px] font-semibold ${message.role === "user" ? "text-white/70" : "text-[#8661c5]"}`}>{message.role === "user" ? "참가자" : "관제 에이전트"}</p>
            {message.text}
          </div>
        </div>)}
      </div>
      </div>
    </aside>}
  />;
}
