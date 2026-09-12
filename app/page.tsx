"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import FlightPathMap from "@/components/FlightPathMap";
import GibbyDroneBoarding from "@/components/GibbyDroneBoarding";
import GibbyIntroSequence from "@/components/GibbyIntroSequence";
import GibbyMapTransition from "@/components/GibbyMapTransition";
import GibbyRouteDock from "@/components/GibbyRouteDock";
import GibbyResultsTransition from "@/components/GibbyResultsTransition";
import PixelShell from "@/components/PixelShell";
import ResultsPanel from "@/components/ResultsPanel";
import { INITIAL_ROUTE_STATE } from "@/data/monitors";
import { INITIAL_MISSION_STATE, SCENARIO_BRIEFING, TARGET_APPEARANCE } from "@/data/scenario";
import { fetchRelayConfig, VoiceSession, type RelayConfig, type VoiceStatus } from "@/lib/voiceClient";
import type { ChatMessage, DashboardState } from "@/lib/types";
import type { MarkerRect } from "@/lib/gibbyResultsSprite";

const INITIAL_STATE: DashboardState = { ...INITIAL_ROUTE_STATE, ...INITIAL_MISSION_STATE };
type Step = "opening" | "map-intro" | "route" | "results-transition" | "results";

interface ReturnScene {
  runId: string;
  generation: number;
  origin: MarkerRect | null;
  celebrate: boolean;
}

export default function Home() {
  const [step, setStep] = useState<Step>("opening");
  const [snapshot, setSnapshot] = useState({ state: INITIAL_STATE, receivedAt: 0 });
  const [now, setNow] = useState(0);
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [connected, setConnected] = useState(false);
  const [transcript, setTranscript] = useState<ChatMessage[]>([]);
  const [speechText, setSpeechText] = useState<string | null>(null);
  const [config, setConfig] = useState<RelayConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [debrief, setDebrief] = useState("");
  const [boarded, setBoarded] = useState(false);
  const [returnScene, setReturnScene] = useState<ReturnScene | null>(null);
  const [resultsVisible, setResultsVisible] = useState(false);
  const [sceneError, setSceneError] = useState<string | null>(null);
  const sessionRef = useRef<VoiceSession | null>(null);
  const markerRef = useRef<HTMLDivElement>(null);
  const boardedRef = useRef(false);
  const latestStateRef = useRef(INITIAL_STATE);
  const pendingReturnRef = useRef<DashboardState | null>(null);
  const returnSceneRef = useRef<ReturnScene | null>(null);
  const resultsReadyRef = useRef(false);
  const generationRef = useRef(0);
  const advancedToCapturesRef = useRef(false);
  const advancedToRouteRef = useRef(false);
  const advancedToResultsRef = useRef(false);
  const streamingRef = useRef<{ user: string | null; agent: string | null }>({ user: null, agent: null });
  const state = snapshot.state;
  const missionLaunched = state.clockRunning || state.elapsedMs > 0 || ["paused", "complete"].includes(state.missionPhase);

  const beginReturn = useCallback((terminal: DashboardState) => {
    if (returnSceneRef.current) return;
    const rect = boardedRef.current ? markerRef.current?.getBoundingClientRect() : null;
    const origin = rect && rect.width > 0 && rect.height > 0
      ? { left: rect.left, top: rect.top, width: rect.width, height: rect.height } : null;
    if (boardedRef.current && !origin) {
      setSceneError("드론 표시 위치를 확인하지 못해 귀환 애니메이션 없이 결과를 표시합니다.");
    }
    const scene = {
      runId: terminal.runId, generation: generationRef.current,
      origin, celebrate: terminal.missionPhase === "complete",
    };
    pendingReturnRef.current = null;
    returnSceneRef.current = scene;
    setReturnScene(scene);
    setStep(origin ? "results-transition" : "results");
  }, []);

  useEffect(() => {
    boardedRef.current = boarded;
    if (!boarded || !pendingReturnRef.current) return;
    const id = requestAnimationFrame(() => {
      if (pendingReturnRef.current) beginReturn(pendingReturnRef.current);
    });
    return () => cancelAnimationFrame(id);
  }, [boarded, beginReturn]);

  const finishReturn = useCallback((scene: ReturnScene) => {
    if (scene.generation !== generationRef.current || returnSceneRef.current !== scene || resultsReadyRef.current) return;
    resultsReadyRef.current = true;
    setStep("results");
    sessionRef.current?.markResultsReady(scene.runId);
  }, []);

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
    setSpeechText(null);
    setError(null);
    setDebrief("");
    setBoarded(false);
    boardedRef.current = false;
    latestStateRef.current = INITIAL_STATE;
    pendingReturnRef.current = null;
    returnSceneRef.current = null;
    resultsReadyRef.current = false;
    setReturnScene(null);
    setResultsVisible(false);
    setSceneError(null);
  }, []);

  const start = useCallback(() => {
    if (sessionRef.current) return;
    const generation = ++generationRef.current;
    const current = () => generationRef.current === generation;
    setStatus("connecting");
    setSpeechText("");
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
      },
      onLevel: () => {},
      onDebrief: (text) => { if (current()) setDebrief(text); },
      onResultsReveal: () => { if (current()) setResultsVisible(true); },
      onSpeechText: (text) => { if (current()) setSpeechText(text); },
      onRouteState: (next) => {
        if (!current()) return;
        const previous = latestStateRef.current;
        if (previous.runId && (previous.runId !== next.runId || next.revision < previous.revision)) return;
        latestStateRef.current = next;
        const terminal = next.missionPhase === "complete" || next.missionPhase === "aborted";
        if (!advancedToRouteRef.current && !terminal && next.promptPhase === "confirmed") {
          advancedToRouteRef.current = true;
          setStep("map-intro");
        }
        if (!advancedToResultsRef.current &&
            terminal) {
          advancedToResultsRef.current = true;
          if (next.missionPhase === "complete" && !boardedRef.current) {
            pendingReturnRef.current = next;
            if (!advancedToRouteRef.current) setStep("route");
          } else {
            beginReturn(next);
          }
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
  }, [beginReturn]);

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
  const agentText = speechText ?? [...transcript].reverse().find((message) => message.role === "agent")?.text ?? "";

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
      onDone={() => {
        if (!returnSceneRef.current && latestStateRef.current.runId === state.runId) setStep("route");
      }}
    />;
  }

  const banners = <>
    {sceneError && <div role="alert" className="pixel-panel bg-[#fff3d6] p-3 text-sm text-[#091f2c]">{sceneError}</div>}
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

  return <div className="results-scene relative h-dvh w-full overflow-hidden">
    <PixelShell banners={banners} groundHidden={boarded && !returnScene} groundReturning={Boolean(returnScene?.origin)}>
      {(step === "route" || step === "results-transition") && <div className="absolute inset-0">
        <FlightPathMap state={state} boarded={boarded} elapsedMs={elapsedMs} connected={connected}
          markerRef={markerRef} departing={Boolean(returnScene)} />
      </div>}
      {step === "results" && <div className="results-content h-full min-h-0">
        <ResultsPanel state={state} debrief={debrief} onReset={reset} visible={resultsVisible} />
      </div>}
    </PixelShell>
    {step === "route" && missionLaunched && !boarded && <GibbyDroneBoarding onBoarded={() => {
      if (!returnSceneRef.current && latestStateRef.current.runId === state.runId) setBoarded(true);
    }} />}
    {step === "route" && !missionLaunched && <GibbyRouteDock agentText={agentText} />}
    {step === "results-transition" && <button type="button" onClick={reset}
      className="pixel-button absolute right-6 top-6 z-[70] bg-[#ffd23f] px-4 py-2 text-sm font-semibold text-[#091f2c]">처음으로</button>}
    {returnScene && <GibbyResultsTransition key={returnScene.runId}
      origin={returnScene.origin} celebrate={returnScene.celebrate}
      onReady={() => finishReturn(returnScene)} onError={setSceneError} />}
  </div>;
}
