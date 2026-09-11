"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import FlightPathMap, { MissionCountdownSummary } from "@/components/FlightPathMap";
import DroneImagePanel from "@/components/DroneImagePanel";
import GibbyIntroSequence from "@/components/GibbyIntroSequence";
import GibbyMapTransition from "@/components/GibbyMapTransition";
import GibbyRouteDock from "@/components/GibbyRouteDock";
import PixelShell from "@/components/PixelShell";
import ResultsPanel from "@/components/ResultsPanel";
import { INITIAL_ROUTE_STATE } from "@/data/monitors";
import { INITIAL_MISSION_STATE, SCENARIO_BRIEFING, TARGET_APPEARANCE } from "@/data/scenario";
import { fetchRelayConfig, VoiceSession, type RelayConfig, type VoiceStatus } from "@/lib/voiceClient";
import type { ChatMessage, DashboardState } from "@/lib/types";

const INITIAL_STATE: DashboardState = { ...INITIAL_ROUTE_STATE, ...INITIAL_MISSION_STATE };
// No tabs anymore: the mission plays out as sequential full-screen pixel
// steps. "opening" covers the Gibby intro + prompt screen together (see
// components/GibbyIntroSequence.tsx); "map-intro" is the pocket/map-finding
// handoff (components/GibbyMapTransition.tsx) that plays once before the
// route step; route/images/results are separate steps swapped in
// automatically by the same relay-driven signals that used to just switch
// the active tab.
type Step = "opening" | "map-intro" | "route" | "images" | "results";

export default function Home() {
  const [step, setStep] = useState<Step>("opening");
  const [snapshot, setSnapshot] = useState({ state: INITIAL_STATE, receivedAt: 0 });
  const [now, setNow] = useState(0);
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [connected, setConnected] = useState(false);
  const [transcript, setTranscript] = useState<ChatMessage[]>([]);
  const [config, setConfig] = useState<RelayConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [debrief, setDebrief] = useState("");
  const sessionRef = useRef<VoiceSession | null>(null);
  const generationRef = useRef(0);
  const advancedToCapturesRef = useRef(false);
  const advancedToRouteRef = useRef(false);
  const advancedToResultsRef = useRef(false);
  const streamingRef = useRef<{ user: string | null; agent: string | null }>({ user: null, agent: null });
  const state = snapshot.state;
  const missionLaunched = state.clockRunning || state.elapsedMs > 0 || ["paused", "complete"].includes(state.missionPhase);

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
    setStep("opening");
    setSnapshot({ state: INITIAL_STATE, receivedAt: 0 });
    setNow(0);
    setStatus("idle");
    setConnected(false);
    setTranscript([]);
    setError(null);
    setDebrief("");
  }, []);

  const start = useCallback(() => {
    if (sessionRef.current) return;
    const generation = ++generationRef.current;
    const current = () => generationRef.current === generation;
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
        setStep("images");
      },
      onLevel: () => {},
      onDebrief: (text) => { if (current()) setDebrief(text); },
      onRouteState: (next) => {
        if (!current()) return;
        if (!advancedToRouteRef.current && next.promptPhase === "confirmed") {
          advancedToRouteRef.current = true;
          setStep("map-intro");
        }
        if (!advancedToResultsRef.current &&
            (next.missionPhase === "complete" || next.missionPhase === "aborted")) {
          advancedToResultsRef.current = true;
          setStep("results");
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
      onTool: () => {},
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

  // Mic/voice controls are fully hidden across the pixel UI: the session
  // still runs in the background the whole time (started the moment the
  // player presses "Let's Go!"), only Gibby's own transcript lines surface,
  // via his speech bubble on the prompt screen.

  // Display interpolation only: expiration, rescue and scoring remain relay-owned.
  const elapsedMs = state.elapsedMs + (state.clockRunning ? Math.max(0, now - snapshot.receivedAt) : 0);
  const visionReady = config?.visionReady ?? false;
  const agentText = [...transcript].reverse().find((message) => message.role === "agent")?.text ?? "";

  if (step === "opening") {
    return <GibbyIntroSequence
      onReady={start}
      targetImage={TARGET_APPEARANCE.referenceImage}
      targetAlt={TARGET_APPEARANCE.referenceAlt}
      briefing={SCENARIO_BRIEFING}
      agentText={agentText}
    />;
  }

  if (step === "map-intro") {
    return <GibbyMapTransition
      targetImage={TARGET_APPEARANCE.referenceImage}
      targetAlt={TARGET_APPEARANCE.referenceAlt}
      briefing={SCENARIO_BRIEFING}
      onIntroReady={() => sessionRef.current?.sendRouteIntroReady()}
      onDone={() => setStep("route")}
    />;
  }

  const banners = <>
    {(!config || !visionReady) && <div role="alert" className="pixel-panel bg-[#fff3d6] p-3 text-sm leading-6 text-[#7f5a1a]">
      {config?.visionError || (config ? "이미지 분석 서비스가 준비되지 않아 출발할 수 없습니다." : "관제 서버 설정을 확인할 수 없습니다. 릴레이 실행 상태를 확인해 주세요.")}
    </div>}
    {state.error && <div role="alert" className="pixel-panel bg-[#fde4e4] p-3 text-sm leading-6 text-[#7a1f1f]">
      <strong>{state.missionPhase === "paused" ? "시계가 일시 정지되었습니다. " : "작전 오류: "}</strong>{state.error}
      {state.missionPhase === "paused" && (status === "idle" && connected ? <div className="mt-2 flex gap-3">
        <button type="button" onClick={() => sessionRef.current?.sendCommand("retry_mission")} className="pixel-button bg-white px-3 py-1.5">다시 시도</button>
        <button type="button" onClick={() => sessionRef.current?.sendCommand("abort_mission")} className="pixel-button bg-white px-3 py-1.5">작전 중단</button>
      </div> : <p className="mt-2">계속하려면 “다시 시도해 줘”, 중단하려면 “작전을 중단해 줘”라고 말해주세요.</p>)}
    </div>}
    {status === "error" && <div className="pixel-panel bg-white p-3">
      <button onClick={retryConnection} className="pixel-button bg-[#ffd23f] px-4 py-2 text-sm font-semibold text-[#091f2c]">연결 다시 시도 · 새 작전</button>
      {error && <p className="mt-2 text-xs leading-5 text-[#6e6575]">{error}</p>}
    </div>}
  </>;

  if (step === "route") return <div className="relative h-dvh w-full">
    <PixelShell banners={banners}><FlightPathMap state={state} /></PixelShell>
    <GibbyRouteDock agentText={agentText} />
  </div>;

  if (step === "images") return <PixelShell banners={banners}>
    <div className="flex h-full min-h-0 flex-col gap-2">
      {missionLaunched && <div className="pixel-panel shrink-0 bg-white p-3">
        <MissionCountdownSummary state={state} elapsedMs={elapsedMs} connected={connected} />
      </div>}
      <div className="min-h-0 flex-1"><DroneImagePanel captures={state.captures} /></div>
    </div>
  </PixelShell>;

  return <PixelShell banners={banners}><ResultsPanel state={state} debrief={debrief} onReset={reset} /></PixelShell>;
}
