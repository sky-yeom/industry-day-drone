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
import { INITIAL_ROUTE_STATE, MONITORS_BY_KIND } from "@/data/monitors";
import { INITIAL_MISSION_STATE, TARGET_APPEARANCE, TRIAGE_TARGET_SITE } from "@/data/scenario";
import { SECURITY_SUSPECT_SITE, SECURITY_TARGET_APPEARANCE } from "@/data/security-scenario";
import { CONSTRUCTION_TARGET_APPEARANCE, CONSTRUCTION_TARGET_SITE } from "@/data/construction-scenario";
import { SCENARIOS, type ScenarioId } from "@/data/scenarios";
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
  const [halted, setHalted] = useState<string | null>(null);
  const [scenarioId, setScenarioId] = useState<ScenarioId>("saving-people");
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
    // Show the result text boxes as soon as the return animation finishes -
    // don't make the participant stare at a blank screen while a brand new
    // voice connection spins up just to narrate the debrief out loud.
    setResultsVisible(true);
    sessionRef.current?.markResultsReady(scene.runId);
  }, []);

  // A failed run parks on the map instead of the debrief. The operator asked for
  // it, so leave the way through open rather than deciding for them.
  const showResults = useCallback(() => {
    if (advancedToResultsRef.current) return;
    advancedToResultsRef.current = true;
    beginReturn(latestStateRef.current);
  }, [beginReturn]);

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
    setHalted(null);
    setScenarioId("saving-people");
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
          // Give the confidence/reasoning banner (just set on this same
          // state update) a beat to actually render and be read/heard on
          // the prompt screen before we swap it out for the map transition.
          // Without this delay the two updates land in the same React
          // commit and the banner is never visible at all.
          window.setTimeout(() => {
            if (current()) setStep("map-intro");
          }, 4000);
        }
        if (!advancedToResultsRef.current &&
            terminal) {
          if (next.missionPhase === "aborted" && next.error) {
            // A single failure used to jump straight to the debrief, which reads
            // as "the run is over" when the aircraft is merely stopped. Hold the
            // map and let the operator choose the next move.
            setHalted(next.error);
          } else {
            advancedToResultsRef.current = true;
            if (next.missionPhase === "complete" && !boardedRef.current) {
              pendingReturnRef.current = next;
              if (!advancedToRouteRef.current) setStep("route");
            } else {
              beginReturn(next);
            }
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
    void session.start({ scenarioKind: SCENARIOS[scenarioId].kind }).catch(() => {});
  }, [beginReturn, scenarioId]);

  const retryConnection = useCallback(() => {
    reset();
    start();
  }, [reset, start]);

  // Manual operator override for a mic/venue-audio failure: drives the
  // exact same relay tools a real voice confirmation would, through the
  // model-independent browser "command" channel (see run_tool in
  // relay/server.py), so mission state stays consistent either way.
  const forceConfirmPrompt = useCallback(() => {
    const session = sessionRef.current;
    if (!session || state.promptPhase === "confirmed") return;
    const kind = SCENARIOS[scenarioId].kind;
    const description = kind === "security" ? SECURITY_TARGET_APPEARANCE.description
      : kind === "construction" ? CONSTRUCTION_TARGET_APPEARANCE.description
      : TARGET_APPEARANCE.description;
    session.sendCommand("confirm_prompt", {
      prompt_text: description, appearance_constraints: [], unsupported_appearance: [],
    });
  }, [scenarioId]);

  const forceConfirmRoute = useCallback(() => {
    const session = sessionRef.current;
    if (!session) return;
    // Force the first two stops in scenario map order rather than a
    // hardcoded "monitor-1"/"monitor-2" - keeps this in sync automatically
    // if a scenario's stop ids or count ever change.
    const [first, second] = MONITORS_BY_KIND[SCENARIOS[scenarioId].kind];
    if (first) session.sendCommand("select_stop", { monitor: first.id });
    if (second) session.sendCommand("select_stop", { monitor: second.id });
    session.sendCommand("confirm_route");
    session.sendCommand("launch_mission");
  }, [scenarioId]);

  // Display interpolation only: expiration, reporting and scoring remain relay-owned.
  const elapsedMs = state.elapsedMs + (state.clockRunning ? Math.max(0, now - snapshot.receivedAt) : 0);
  const visionReady = config?.visionReady ?? false;
  const agentText = speechText ?? [...transcript].reverse().find((message) => message.role === "agent")?.text ?? "";

  if (step === "opening") {
    return <GibbyIntroSequence
      onReady={start}
      onScenarioChosen={setScenarioId}
      agentText={agentText}
      voiceStatus={status}
      error={error}
      onRetry={retryConnection}
      state={state}
      promptConfidence={state.promptConfidence}
      promptConfidenceReason={state.promptConfidenceReason}
      onForceNext={forceConfirmPrompt}
    />;
  }

  if (step === "map-intro") {
    const scenario = SCENARIOS[scenarioId];
    return <GibbyMapTransition
      sites={
        scenario.kind === "security" ? [SECURITY_SUSPECT_SITE]
          : scenario.kind === "construction" ? [CONSTRUCTION_TARGET_SITE]
          : [TRIAGE_TARGET_SITE]
      }
      state={state}
      briefing={scenario.briefing}
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
      {halted && <div className="mt-2">
        <p className="text-xs leading-5">실제 비행 오류 뒤에는 자동으로 이어서 날지 않습니다. 기체가 멈춘 것을 확인한 뒤 새 작전을 시작해 주세요.</p>
        <div className="mt-2 flex gap-3">
          <button type="button" onClick={reset} className="pixel-button bg-white px-3 py-1.5">새 작전 시작</button>
          <button type="button" onClick={showResults} className="pixel-button bg-white px-3 py-1.5">결과 보기</button>
        </div>
      </div>}
    </div>}
    {error && status !== "error" && <p role="alert" className="pixel-panel bg-white p-2 text-sm text-[#091f2c]">{error}</p>}
    {status === "error" && <div className="pixel-panel bg-white p-3">
      <button onClick={retryConnection} className="pixel-button bg-[#ffd23f] px-4 py-2 text-sm font-semibold text-[#091f2c]">연결 다시 시도 · 새 작전</button>
      {error && <p className="mt-2 text-xs leading-5 text-[#6e6575]">{error}</p>}
    </div>}
  </>;

  return <div className="results-scene relative h-dvh w-full overflow-hidden">
    <PixelShell banners={banners} groundHidden={boarded && !returnScene} groundReturning={Boolean(returnScene?.origin)}>
      {(step === "route" || step === "results-transition") && <div className="absolute inset-0">
        <FlightPathMap state={state} boarded={boarded} elapsedMs={elapsedMs} connected={connected}
          markerRef={markerRef} departing={Boolean(returnScene)}
          voiceStatus={!missionLaunched ? status : undefined}
          onMapReady={() => sessionRef.current?.sendRouteIntroReady()} onMapError={setSceneError}
          onForceNext={step === "route" && !missionLaunched ? forceConfirmRoute : undefined} />
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
