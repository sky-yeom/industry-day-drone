"use client";

/**
 * Voice Live client.
 *
 * Talks plain WebSocket to the local relay, which holds the Entra credential and
 * forwards to Voice Live. Audio is PCM16 mono 24 kHz in both directions.
 *
 * The relay owns the route state, so this client never mutates it. It just
 * forwards `route.state` pushes to the caller.
 */

import type { DashboardState, DetectionMode } from "@/lib/types";

const SAMPLE_RATE = 24000;
const RESULTS_START_TIMEOUT_MS = 15000;
const TURN_TAKING = "after-playback-v1";
const ROUTE_BUFFER_LIMIT = 4 * 1024 * 1024;
class VoiceProtocolError extends Error {}

const RELAY_HTTP =
  process.env.NEXT_PUBLIC_RELAY_HTTP ?? "http://127.0.0.1:8080";
const RELAY_WS = process.env.NEXT_PUBLIC_RELAY_WS ?? "ws://127.0.0.1:8080/ws";

export type VoiceStatus =
  | "idle"
  | "connecting"
  | "processing"
  | "listening"
  | "speaking"
  | "error";

export interface ToolActivity {
  id: string;
  name: string;
  /** What actually happened, for the tooltip. The relay calls this `facts`. */
  facts?: string;
  ok?: boolean;
  ms?: number;
}

interface AssistantSpeech {
  parts: Map<string, string>;
  completed: boolean;
  hasAudio: boolean;
  started: boolean;
  drainRequested: boolean;
  ordinary: boolean;
  terminal: boolean;
  drained: boolean;
  receivedAt?: number;
}

interface InputWindow {
  windowId: string;
  responseIds: string[];
}

interface RouteIntro {
  introId: string;
  ready: boolean;
  responseIds: Set<string>;
  bytes: number;
}

export interface VoiceHandlers {
  /**
   * 상태 변화. `detail`은 사용자에게 보여줄 한 줄 설명이며, 상태가 바뀔 때마다
   * 갱신되므로 다음 상태 변화에서 자연스럽게 사라진다.
   */
  onStatus: (status: VoiceStatus, detail?: string) => void;
  onLevel: (peak: number) => void;
  /** Streaming transcript. `final` marks the end of that speaker's turn. */
  onTranscript: (role: "user" | "agent", text: string, final: boolean) => void;
  onRouteState: (state: DashboardState) => void;
  onTool: (activity: ToolActivity) => void;
  onTtfa: (ms: number) => void;
  /** True while a response is generating. Sending during one is rejected. */
  onBusy: (busy: boolean) => void;
  onDebrief: (text: string) => void;
  onError: (message: string) => void;
  onConnection?: (connected: boolean) => void;
  onDeparture?: () => void;
  onResultsReveal?: () => void;
  /** The caption for the response actually playing, not a queued future reply. */
  onSpeechText?: (text: string) => void;
}

export interface RelayConfig {
  model: string;
  voice: string;
  region: string;
  mode: DetectionMode;
  visionReady: boolean;
  visionError: string | null;
}

export async function fetchRelayConfig(): Promise<RelayConfig | null> {
  try {
    const res = await fetch(`${RELAY_HTTP}/api/config`);
    if (!res.ok) return null;
    return (await res.json()) as RelayConfig;
  } catch {
    return null;
  }
}

export class VoiceSession {
  private ws: WebSocket | null = null;
  private audioCtx: AudioContext | null = null;
  private captureNode: AudioWorkletNode | null = null;
  private playbackNode: AudioWorkletNode | null = null;
  private micStream: MediaStream | null = null;
  private running = false;

  private partialUser = "";
  private speech = new Map<string, AssistantSpeech>();
  private audibleResponseId: string | null = null;
  private inputReady: InputWindow | null = null;
  private inputWindowId: string | null = null;
  private inputEpoch = 0;
  private routeIntro: RouteIntro | null = null;
  private routeVisible = false;
  private diagnostics = false;
  private metricsRemaining = 256;
  private outputTimers = new Set<ReturnType<typeof setTimeout>>();
  private settledResponses = new Set<string>();
  private usedInputWindows = new Set<string>();
  private generation = 0;
  private withVoice = true;
  private debriefRunId: string | null = null;
  private finalResponseId: string | null = null;
  private finalDrainRequested = false;
  private currentRunId = "";
  private launchRunId: string | null = null;
  private launchResponseId: string | null = null;
  private launchDrainRequested = false;
  private voiceStopped = false;
  private missionEnded = false;
  private resultsRequested = false;
  private narratingResults = false;
  private resultsReadyRunId: string | null = null;
  private resultsRevealed = false;
  private resultsFallback = false;
  private resultsStartTimer: ReturnType<typeof setTimeout> | null = null;
  private resultsResponseIds = new Set<string>();
  private routeRevision = -1;
  private rejectConnect: ((reason: Error) => void) | null = null;
  private responseActive = false;
  private responseId: string | null = null;
  private interruptedResponses = new Set<string>();
  private toolCallPending = false;
  private replyResponseId: string | null = null;
  private microphoneMuted = true;
  // The mic streams from the moment the graph is wired, which can trigger VAD
  // before the greeting is requested. That collision kills the greeting with
  // "conversation already has an active response", so stay muted until the
  // greeting has finished playing.
  private greetPending = true;

  constructor(private handlers: VoiceHandlers) {}

  get isRunning() {
    return this.running;
  }

  async start({ withVoice = true }: { withVoice?: boolean } = {}): Promise<void> {
    if (this.running) return;
    const generation = ++this.generation;
    this.withVoice = withVoice;
    this.debriefRunId = null;
    this.finalResponseId = null;
    this.finalDrainRequested = false;
    this.currentRunId = "";
    this.launchRunId = null;
    this.launchResponseId = null;
    this.launchDrainRequested = false;
    this.voiceStopped = false;
    this.missionEnded = false;
    this.resultsRequested = false;
    this.narratingResults = false;
    this.resultsReadyRunId = null;
    this.resultsRevealed = false;
    this.resultsFallback = false;
    this.resultsResponseIds.clear();
    this.clearResultsStartTimer();
    this.routeRevision = -1;
    this.replyResponseId = null;
    this.responseId = null;
    this.interruptedResponses.clear();
    this.microphoneMuted = true;
    this.partialUser = "";
    this.speech.clear();
    this.inputReady = null;
    this.inputWindowId = null;
    this.routeIntro = null;
    this.routeVisible = false;
    this.settledResponses.clear();
    this.usedInputWindows.clear();
    this.diagnostics = false;
    this.metricsRemaining = 256;
    this.audibleResponseId = null;
    this.handlers.onSpeechText?.("");
    this.handlers.onStatus("connecting");

    try {
      if (withVoice) {
      // Browser-side cleanup. Server-side echo cancellation alone is not enough
      // when the machine's own speakers are in the room.
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      if (generation !== this.generation) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.micStream = stream;

      this.audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
      // The getUserMedia permission prompt consumes the click gesture, so
      // Chrome hands back a suspended context and the greeting is never heard.
      if (this.audioCtx.state === "suspended") await this.audioCtx.resume();

      await this.audioCtx.audioWorklet.addModule("/audio-worklets.js");
      if (generation !== this.generation) return;

      const source = this.audioCtx.createMediaStreamSource(this.micStream);
      this.captureNode = new AudioWorkletNode(this.audioCtx, "capture-processor");
      // Hold the mic until the greeting has been spoken.
      this.greetPending = true;
      this.setMicrophoneMuted(true);
      this.playbackNode = new AudioWorkletNode(this.audioCtx, "playback-processor", {
        outputChannelCount: [1],
      });

      this.captureNode.port.onmessage = (e) => {
        if (generation !== this.generation || this.microphoneMuted) return;
        const { pcm, peak, epoch, type, muted } = e.data as {
          pcm?: Int16Array; peak: number; epoch: number; type?: string; muted?: boolean;
        };
        if (epoch !== this.inputEpoch || !this.inputWindowId) return;
        if (type === "input-state") {
          if (!muted) this.handlers.onStatus("listening");
          return;
        }
        if (!pcm) return;
        this.handlers.onLevel(peak);
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ type: "audio", data: toBase64(pcm), windowId: this.inputWindowId }));
        }
      };
      this.playbackNode.port.onmessage = (e) => {
        if (generation !== this.generation) return;
        const msg = e.data as { type: string; playing: boolean; id?: string; contextTime?: number };
        if (msg.id && this.interruptedResponses.has(msg.id)) return;
        const speech = msg.id ? this.speech.get(msg.id) : undefined;
        if (msg.type === "state") {
          if (!this.voiceStopped || this.narratingResults) {
            if (msg.playing) this.handlers.onStatus("speaking");
            else if (this.microphoneMuted) this.handlers.onStatus("processing");
          }
        }
        if (msg.type === "started" && msg.id && speech) {
          speech.started = true;
          this.audibleResponseId = msg.id;
          this.publishSpeechCaption(msg.id);
          this.handlers.onStatus("speaking");
          this.tracePlayback("started", msg.id, speech.receivedAt === undefined ? undefined : performance.now() - speech.receivedAt);
        }
        if (msg.type === "started" && this.narratingResults && msg.id && this.resultsResponseIds.has(msg.id)) {
          this.revealResults();
        }
        if (msg.type !== "drained" || !msg.id) return;
        this.afterOutput(msg.contextTime, () => {
          if (this.launchDrainRequested && msg.id === this.launchResponseId) {
            void this.finishDeparture();
          } else if (this.finalDrainRequested && msg.id === this.finalResponseId) {
            const missingAudio = !this.resultsRevealed;
            if (missingAudio) {
              const message = "결과 음성이 재생되지 않았습니다. 화면의 최종 구조 결과를 확인해 주세요.";
              this.handlers.onStatus("error", message);
              this.handlers.onError(message);
            }
            this.fallbackResults();
            void this.closeSession(missingAudio, true);
          } else if (speech?.ordinary && speech.drainRequested && !speech.drained) {
            if (speech.started && speech.completed && this.ws?.readyState === WebSocket.OPEN) {
              this.ws.send(JSON.stringify({
                type: "voice.reply_drained", responseId: msg.id, runId: this.currentRunId,
              }));
            }
            if (msg.id === this.replyResponseId) {
              this.replyResponseId = null;
            }
            speech.drained = true;
          }
          if (msg.id) {
            this.tracePlayback("drained", msg.id);
            if (speech?.terminal) this.settleResponse(msg.id);
          }
          this.tryOpenInput();
        });
      };

      source.connect(this.captureNode);
      // Keep the capture graph alive without routing the mic to the speakers.
      this.captureNode.connect(this.audioCtx.createGain()).connect(this.audioCtx.destination);
      this.playbackNode.connect(this.audioCtx.destination);

      if (this.audioCtx.state === "suspended") await this.audioCtx.resume();
      }

      if (generation !== this.generation) return;
      await this.connect(generation);
      if (generation !== this.generation) return;
      this.running = true;
    } catch (err) {
      if (generation !== this.generation) return;
      const message = err instanceof VoiceProtocolError ? err.message : withVoice
        ? "음성 연결을 시작하지 못했습니다. 마이크 권한과 음성 서비스 설정을 확인한 뒤 연결 다시 시도를 눌러 주세요."
        : "관제 서버에 연결하지 못했습니다. 릴레이 실행 상태를 확인하세요.";
      this.handlers.onStatus("error", message);
      this.handlers.onError(message);
      await this.stop(true);
      throw err;
    }
  }

  async stop(keepStatus = false): Promise<void> {
    await this.closeSession(keepStatus);
  }

  private async closeSession(keepStatus = false, preserveResults = false): Promise<void> {
    this.clearResultsStartTimer();
    this.closeInput();
    for (const timer of this.outputTimers) clearTimeout(timer);
    this.outputTimers.clear();
    this.speech.clear();
    this.audibleResponseId = null;
    this.handlers.onSpeechText?.("");
    if (!preserveResults) {
      this.currentRunId = "";
      this.resultsReadyRunId = null;
      this.debriefRunId = null;
      this.resultsFallback = false;
      this.resultsRevealed = false;
      this.resultsResponseIds.clear();
    }
    ++this.generation;
    this.rejectConnect?.(new Error("세션이 종료되었습니다."));
    this.rejectConnect = null;
    this.running = false;
    this.handlers.onConnection?.(false);
    this.responseActive = false;
    this.toolCallPending = false;
    this.replyResponseId = null;
    this.microphoneMuted = true;
    this.handlers.onLevel(0);
    this.handlers.onBusy(false);
    if (!keepStatus) this.handlers.onStatus("idle");

    if (this.ws) {
      this.ws.onmessage = null;
      this.ws.onclose = null;
      this.ws.onerror = null;
      try {
        this.ws.close();
      } catch {
        /* already closing */
      }
      this.ws = null;
    }
    await this.releaseAudio();
  }

  private async releaseAudio() {
    const context = this.audioCtx;
    this.audioCtx = null;
    if (this.captureNode) this.captureNode.port.onmessage = null;
    if (this.playbackNode) this.playbackNode.port.onmessage = null;
    this.captureNode = null;
    this.playbackNode = null;
    this.micStream?.getTracks().forEach((track) => track.stop());
    this.micStream = null;
    if (context && context.state !== "closed") await context.close();
  }

  private async finishDeparture(announce = true) {
    if (this.voiceStopped) return;
    this.closeInput();
    this.retireRouteIntro();
    this.voiceStopped = true;
    this.greetPending = false;
    this.responseActive = false;
    this.toolCallPending = false;
    this.setMicrophoneMuted(true);
    if (this.captureNode) {
      this.captureNode.port.onmessage = null;
      this.captureNode.disconnect();
    }
    this.captureNode = null;
    this.micStream?.getTracks().forEach((track) => track.stop());
    this.micStream = null;
    this.handlers.onLevel(0);
    this.handlers.onBusy(false);
    this.handlers.onStatus("idle");
    if (announce) this.handlers.onDeparture?.();
    if (this.missionEnded) this.requestResults();
  }

  private requestResults() {
    if (this.resultsReadyRunId !== this.currentRunId || !this.currentRunId) return;
    if (this.resultsFallback) {
      this.revealResults();
      return;
    }
    if (this.resultsRequested || this.debriefRunId !== this.currentRunId || !this.voiceStopped) return;
    if (!this.withVoice || !this.playbackNode || this.ws?.readyState !== WebSocket.OPEN) {
      this.fallbackResults();
      void this.closeSession(false, true);
      return;
    }
    this.resultsRequested = true;
    this.narratingResults = true;
    this.handlers.onStatus("connecting");
    this.ws.send(JSON.stringify({ type: "results.ready", runId: this.debriefRunId }));
  }

  markResultsReady(runId: string): void {
    if (!runId || runId !== this.currentRunId || this.resultsReadyRunId === runId) return;
    this.resultsReadyRunId = runId;
    const generation = this.generation;
    // Also bound waiting for a missing debrief or a stalled departure drain.
    // A timeout is a reported failure, never permission to overlap narration.
    this.resultsStartTimer = setTimeout(() => {
      if (generation !== this.generation || runId !== this.currentRunId || this.resultsRevealed) return;
      const message = "결과 음성 재생을 시작하지 못했습니다. 화면의 최종 구조 결과를 확인해 주세요.";
      this.handlers.onStatus("error", message);
      this.handlers.onError(message);
      this.fallbackResults();
      void this.closeSession(true, true);
    }, RESULTS_START_TIMEOUT_MS);
    this.requestResults();
  }

  private clearResultsStartTimer() {
    if (this.resultsStartTimer !== null) clearTimeout(this.resultsStartTimer);
    this.resultsStartTimer = null;
  }

  private revealResults() {
    if (this.resultsRevealed || !this.currentRunId || this.resultsReadyRunId !== this.currentRunId) return;
    this.resultsRevealed = true;
    this.clearResultsStartTimer();
    this.handlers.onResultsReveal?.();
  }

  private fallbackResults() {
    this.resultsFallback = true;
    this.revealResults();
  }

  private speechFor(id: string): AssistantSpeech {
    let speech = this.speech.get(id);
    if (!speech) {
      speech = {
        parts: new Map(), completed: false, hasAudio: false, started: false,
        drainRequested: false, ordinary: !this.launchRunId && !this.debriefRunId,
        terminal: false, drained: false,
      };
      this.speech.set(id, speech);
      if (this.speech.size > 128) {
        for (const [key, old] of this.speech) {
          if (old.completed && !old.hasAudio) this.speech.delete(key);
        }
      }
    }
    return speech;
  }

  private publishSpeechCaption(id: string) {
    if (this.audibleResponseId !== id) return;
    const speech = this.speech.get(id);
    if (speech) this.handlers.onSpeechText?.([...speech.parts.values()].join(""));
  }

  private closeInput() {
    this.inputReady = null;
    this.inputWindowId = null;
    this.setMicrophoneMuted(true);
  }

  private settleResponse(id: string) {
    this.settledResponses.add(id);
    this.speech.delete(id);
    if (this.replyResponseId === id) this.replyResponseId = null;
    while (this.settledResponses.size > 128) {
      const first = this.settledResponses.values().next().value;
      if (first) this.settledResponses.delete(first);
    }
  }

  private tryOpenInput() {
    const ready = this.inputReady;
    if (!ready || !this.withVoice || this.voiceStopped || this.launchRunId || this.debriefRunId
        || (this.routeIntro && !this.routeIntro.ready)
        || this.ws?.readyState !== WebSocket.OPEN) return;
    if (ready.responseIds.some(id => !this.settledResponses.has(id))) return;
    if ([...this.speech.values()].some(speech => speech.ordinary
      && (!speech.terminal || (speech.hasAudio && !speech.drained)))) return;
    this.inputReady = null;
    this.inputWindowId = ready.windowId;
    this.usedInputWindows.add(ready.windowId);
    if (this.usedInputWindows.size > 128) {
      const first = this.usedInputWindows.values().next().value;
      if (first) this.usedInputWindows.delete(first);
    }
    this.greetPending = false;
    this.responseActive = false;
    this.handlers.onBusy(false);
    this.ws.send(JSON.stringify({ type: "voice.input.open", runId: this.currentRunId, windowId: ready.windowId }));
    this.setMicrophoneMuted(false);
  }

  private afterOutput(contextTime: number | undefined, callback: () => void) {
    const context = this.audioCtx;
    if (!context || contextTime === undefined || !Number.isFinite(contextTime)) {
      callback();
      return;
    }
    if (context.state !== "running") {
      this.handlers.onStatus("error");
      this.handlers.onError("오디오 재생이 멈췄어. 연결 다시 시도로 음성을 다시 시작해 줘.");
      return;
    }
    const output = context.getOutputTimestamp?.();
    const wait = output?.contextTime && output.performanceTime
      ? output.performanceTime + (contextTime - output.contextTime) * 1000 - performance.now()
      : (contextTime - context.currentTime + (context.baseLatency || 0) + (context.outputLatency || 0)) * 1000;
    if (!(wait > 0)) {
      callback();
      return;
    }
    const generation = this.generation;
    const timer = setTimeout(() => {
      this.outputTimers.delete(timer);
      if (generation === this.generation) this.afterOutput(contextTime, callback);
    }, Math.ceil(wait));
    this.outputTimers.add(timer);
  }

  private tracePlayback(event: string, responseId: string, ms?: number) {
    if (!this.diagnostics || this.metricsRemaining-- <= 0 || this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({
      type: "voice.playback.metrics", runId: this.currentRunId, responseId, event,
      ...(ms === undefined ? {} : { ms: Math.round(ms) }),
    }));
  }

  private retireRouteIntro() {
    if (!this.routeIntro) return;
    const ids = [...this.routeIntro.responseIds];
    this.playbackNode?.port.postMessage({ type: "discard", ids });
    for (const id of ids) {
      this.interruptedResponses.add(id);
      this.speech.delete(id);
    }
    this.routeIntro = null;
  }

  sendText(text: string): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    // Voice Live rejects a second response while one is generating with
    // "conversation already has an active response".
    if (this.responseActive || this.replyResponseId) return false;
    this.ws.send(JSON.stringify({ type: "text", text }));
    return true;
  }

  sendCommand(name: string, args: Record<string, unknown> = {}): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify({
      type: "command", name, args, requestId: crypto.randomUUID(),
    }));
    return true;
  }

  // Called after the actual route screen is visible, not just its preceding animation.
  sendRouteIntroReady(): boolean {
    this.routeVisible = true;
    if (this.ws?.readyState !== WebSocket.OPEN || !this.routeIntro) return false;
    if (this.routeIntro.ready) return true;
    this.routeIntro.ready = true;
    this.ws.send(JSON.stringify({ type: "route_intro.ready", runId: this.currentRunId, introId: this.routeIntro.introId }));
    for (const id of this.routeIntro.responseIds) {
      this.tracePlayback("route_ready", id);
      this.playbackNode?.port.postMessage({ type: "release", id });
    }
    this.routeIntro.bytes = 0;
    this.tryOpenInput();
    return true;
  }

  private connect(generation: number): Promise<void> {
    return new Promise((resolve, reject) => {
      this.rejectConnect = reject;
      const url = new URL(RELAY_WS);
      url.searchParams.set("voice", this.withVoice ? "1" : "0");
      if (this.withVoice) url.searchParams.set("turnTaking", TURN_TAKING);
      const ws = new WebSocket(url);
      this.ws = ws;

      ws.onerror = () =>
        reject(new Error("릴레이에 연결하지 못했습니다. relay/server.py 가 실행 중인지 확인하세요."));
      ws.onclose = (ev) => {
        if (generation !== this.generation) return;
        reject(new Error("관제 서버 연결이 종료되었습니다."));
        if (!this.running) return;
        // 중간에 끊기면 아무 표시가 없어서 에이전트가 그냥 대답을 멈춘 것처럼 보인다.
        const why = `릴레이 연결이 끊어졌습니다. (코드 ${ev.code})`;
        this.handlers.onStatus("error", why);
        this.handlers.onError("관제 서버 연결이 끊어졌습니다. 처음부터 다시 시작해 주세요.");
        this.fallbackResults();
        void this.closeSession(true, true);
      };
      ws.onmessage = (e) => {
        if (generation !== this.generation) return;
        const msg = JSON.parse(e.data as string);
        if (msg.type === "relay.ready") {
          if (this.withVoice && msg.turnTaking !== TURN_TAKING) {
            reject(new VoiceProtocolError("음성 서버와 화면의 버전이 달라. 서버를 업데이트하고 새로고침해 줘."));
            return;
          }
          this.diagnostics = msg.diagnostics === true;
          this.rejectConnect = null;
          this.handlers.onConnection?.(true);
          this.handlers.onStatus(this.withVoice ? "processing" : "listening");
          if (this.withVoice) ws.send(JSON.stringify({ type: "greet" }));
          resolve();
          return;
        }
        if (msg.type === "relay.error") {
          this.handlers.onStatus("error", msg.message);
          this.handlers.onError(msg.message);
          reject(new Error(msg.message));
          if (this.running) {
            this.fallbackResults();
            void this.closeSession(true, true);
          }
          return;
        }
        this.handleEvent(msg);
      };
    });
  }

  private handleEvent(msg: Record<string, unknown>) {
    const type = String(msg.type ?? "");
    if (typeof msg.response_id === "string" && this.interruptedResponses.has(msg.response_id)) return;

    switch (type) {
      case "voice.input.ready": {
        if (msg.runId !== this.currentRunId || typeof msg.windowId !== "string"
            || this.usedInputWindows.has(msg.windowId)) return;
        if (!Array.isArray(msg.responseIds) || msg.responseIds.length > 128
            || !msg.responseIds.every(id => typeof id === "string" && id)) {
          console.warn("Invalid voice input readiness response IDs.");
          return;
        }
        this.inputReady = { windowId: msg.windowId, responseIds: msg.responseIds };
        this.tryOpenInput();
        return;
      }
      case "voice.input.closed": {
        if (msg.runId === this.currentRunId
            && (msg.windowId === this.inputWindowId || msg.windowId === this.inputReady?.windowId)) {
          this.closeInput();
          this.handlers.onStatus("processing");
        }
        return;
      }
      case "voice.input.failed": {
        if (msg.runId !== this.currentRunId) return;
        this.handlers.onError(typeof msg.message === "string" && msg.message
          ? msg.message : "말을 알아듣지 못했어. 지금 말해줘 표시가 나오면 다시 말해 줘.");
        return;
      }
      case "route_intro.pending": {
        if (msg.runId !== this.currentRunId || typeof msg.introId !== "string"
            || msg.introId === this.routeIntro?.introId) return;
        this.closeInput();
        this.retireRouteIntro();
        this.routeIntro = { introId: msg.introId, ready: false, responseIds: new Set(), bytes: 0 };
        if (this.routeVisible) this.sendRouteIntroReady();
        return;
      }
      case "route_intro.response": {
        const intro = this.routeIntro;
        if (!intro || msg.runId !== this.currentRunId || msg.introId !== intro.introId
            || typeof msg.responseId !== "string") return;
        intro.responseIds.add(msg.responseId);
        if (!intro.ready) this.playbackNode?.port.postMessage({ type: "hold", id: msg.responseId });
        return;
      }
      case "route_intro.failed": {
        const intro = this.routeIntro;
        if (!intro || msg.runId !== this.currentRunId || msg.introId !== intro.introId) return;
        this.handlers.onError(String(msg.message ?? "현장 안내를 준비하지 못했어. 다시 시도할게."));
        if (typeof msg.responseId === "string") {
          this.playbackNode?.port.postMessage({ type: "discard", ids: [msg.responseId] });
          this.interruptedResponses.add(msg.responseId);
          this.settleResponse(msg.responseId);
        }
        intro.bytes = 0;
        this.tryOpenInput();
        return;
      }
      case "mission.launch": {
        if (String(msg.runId) !== this.currentRunId) return;
        this.launchRunId = this.currentRunId;
        this.greetPending = false;
        this.closeInput();
        this.retireRouteIntro();
        return;
      }
      case "mission.launch.response": {
        if (msg.runId === this.launchRunId && typeof msg.responseId === "string") {
          if (this.launchResponseId !== msg.responseId) this.launchDrainRequested = false;
          this.launchResponseId = msg.responseId;
        }
        return;
      }
      case "mission.launch.done": {
        if (msg.runId === this.launchRunId && msg.responseId === this.launchResponseId) this.drainDeparture();
        return;
      }
      case "mission.launch.failed": {
        if (msg.runId !== this.launchRunId) return;
        this.handlers.onError(String(msg.message ?? "출발 음성 안내가 중단되었습니다. 자동 작전은 계속됩니다."));
        this.playbackNode?.port.postMessage({ type: "flush" });
        void this.finishDeparture();
        return;
      }
      case "mission.debrief": {
        if (msg.runId !== this.currentRunId || this.debriefRunId === this.currentRunId) return;
        this.missionEnded = true;
        this.debriefRunId = this.currentRunId;
        this.handlers.onDebrief(String(msg.text ?? ""));
        this.closeInput();
        this.retireRouteIntro();
        if (!this.launchRunId) {
          this.playbackNode?.port.postMessage({ type: "flush" });
          void this.finishDeparture(false);
        } else if (this.voiceStopped) this.requestResults();
        return;
      }
      case "mission.debrief.failed": {
        if (msg.runId !== this.debriefRunId || !this.resultsRequested) return;
        const message = String(msg.message ?? "결과 음성 안내를 완료하지 못했습니다.");
        this.handlers.onStatus("error", message);
        this.handlers.onError(message);
        this.fallbackResults();
        void this.closeSession(true, true);
        return;
      }
      case "mission.debrief.response": {
        if (this.resultsRequested && msg.runId === this.debriefRunId && typeof msg.responseId === "string") {
          if (this.finalResponseId !== msg.responseId) this.finalDrainRequested = false;
          this.finalResponseId = String(msg.responseId ?? "");
          this.resultsResponseIds.add(this.finalResponseId);
          this.setMicrophoneMuted(true);
        }
        return;
      }
      case "mission.debrief.done": {
        if (this.finalResponseId && msg.responseId === this.finalResponseId &&
            String(msg.runId ?? this.currentRunId) === this.debriefRunId) {
          this.drainFinalResponse();
        }
        return;
      }
      case "response.created": {
        const response = msg.response as {
          id?: string;
          metadata?: { missionDebrief?: boolean | string; missionLaunch?: boolean | string; runId?: string };
        } | undefined;
        const metadata = response?.metadata;
        if (response?.id && this.launchRunId !== null &&
            (metadata?.missionLaunch === true || metadata?.missionLaunch === "true") &&
            metadata.runId === this.launchRunId) {
          if (this.launchResponseId !== response.id) this.launchDrainRequested = false;
          this.launchResponseId = response.id;
        }
        if (response?.id && this.resultsRequested && this.debriefRunId !== null &&
            (metadata?.missionDebrief === true || metadata?.missionDebrief === "true") &&
            metadata.runId === this.debriefRunId) {
          if (this.finalResponseId !== response.id) this.finalDrainRequested = false;
          this.finalResponseId = response.id;
          this.resultsResponseIds.add(response.id);
          this.setMicrophoneMuted(true);
        }
        if (response?.id && !this.launchRunId && !this.debriefRunId) {
          this.replyResponseId = response.id;
        }
        this.closeInput();
        this.handlers.onStatus("processing");
        this.responseId = response?.id ?? null;
        if (this.responseId) this.speechFor(this.responseId);
        this.responseActive = true;
        this.handlers.onBusy(true);
        return;
      }

      case "response.function_call_arguments.done": {
        // The relay answers the tool and starts another response, so the
        // conversation stays busy past this response.done.
        this.toolCallPending = true;
        return;
      }

      case "response.audio.delta":
      case "response.output_audio.delta": {
        const delta = msg.delta as string | undefined;
        const id = typeof msg.response_id === "string" ? msg.response_id
          : this.narratingResults ? this.finalResponseId : this.responseId ?? this.launchResponseId;
        if (this.voiceStopped && (!this.narratingResults || !id || id !== this.finalResponseId)) return;
        if (delta && this.playbackNode) {
          const pcm = fromBase64(delta);
          if (id && this.routeIntro?.responseIds.has(id) && !this.routeIntro.ready) {
            this.routeIntro.bytes += pcm.byteLength;
            if (this.routeIntro.bytes > ROUTE_BUFFER_LIMIT) {
              this.playbackNode.port.postMessage({ type: "discard", ids: [id] });
              this.interruptedResponses.add(id);
              this.settleResponse(id);
              this.routeIntro.bytes = 0;
              this.handlers.onError("현장 음성 안내가 너무 길어. 지도가 나타나면 다시 준비할게.");
              this.ws?.send(JSON.stringify({
                type: "route_intro.retry", runId: this.currentRunId,
                introId: this.routeIntro.introId, responseId: id, reason: "buffer_limit",
              }));
              return;
            }
          }
          if (id) {
            const speech = this.speechFor(id);
            speech.hasAudio = true;
            if (this.diagnostics && speech.receivedAt === undefined) {
              speech.receivedAt = performance.now();
              this.tracePlayback("received", id);
            }
          }
          this.playbackNode.port.postMessage({ type: "push", pcm, id });
        }
        return;
      }

      case "input_audio_buffer.speech_started": {
        if (this.microphoneMuted || this.debriefRunId !== null || this.launchRunId !== null) return;
        this.handlers.onStatus("listening");
        // Native audio can precede transcription; reserve the participant's
        // transcript entry before the next assistant reply arrives.
        this.partialUser = "";
        this.handlers.onTranscript("user", "…", false);
        return;
      }

      case "input_audio_buffer.speech_stopped": {
        if (this.microphoneMuted) return;
        this.closeInput();
        this.handlers.onStatus("processing");
        return;
      }

      case "conversation.item.input_audio_transcription.delta": {
        this.partialUser += (msg.delta as string) ?? "";
        this.handlers.onTranscript("user", this.partialUser, false);
        return;
      }
      case "conversation.item.input_audio_transcription.completed": {
        const raw = (msg.transcript as string) ?? this.partialUser;
        const final = raw.trim();
        // 잡음에서 지어낸 말은 화면에 남기지 않는다. 빈 문자열로 넘기면
        // 미리 잡아둔 자리표시자가 지워진다.
        const clean = looksHallucinated(final) ? "" : final;
        this.handlers.onTranscript("user", clean, true);
        this.partialUser = "";
        return;
      }
      case "conversation.item.input_audio_transcription.failed": {
        // 전사가 실패하면 completed가 오지 않는다. 그대로 두면 "…" 자리표시자가
        // 남고, 다음 발화가 그 낡은 말풍선에 덮어써진다.
        this.handlers.onTranscript("user", "", true);
        this.partialUser = "";
        return;
      }

      case "response.audio_transcript.delta":
      case "response.output_audio_transcript.delta":
      case "response.audio_transcript.done":
      case "response.output_audio_transcript.done": {
        const id = typeof msg.response_id === "string" ? msg.response_id : this.responseId;
        if (!id) {
          console.warn("Assistant audio transcript is missing its response ID.");
          return;
        }
        const speech = this.speechFor(id);
        const part = `${msg.item_id ?? "message"}:${msg.content_index ?? 0}`;
        const text = type.endsWith(".done")
          ? String(msg.transcript ?? speech.parts.get(part) ?? "")
          : (speech.parts.get(part) ?? "") + String(msg.delta ?? "");
        speech.parts.set(part, text);
        if (id === this.responseId) this.handlers.onTranscript("agent", [...speech.parts.values()].join(""), false);
        this.publishSpeechCaption(id);
        return;
      }
      case "response.done": {
        const response = msg.response as { id?: string; status?: string } | undefined;
        if (response?.id && this.interruptedResponses.has(response.id)) return;
        const speech = response?.id ? this.speechFor(response.id) : undefined;
        if (speech) {
          speech.terminal = true;
          speech.completed = response?.status === "completed";
          if (speech.ordinary && speech.hasAudio) this.drainReply(response?.id);
          if (response?.id) this.publishSpeechCaption(response.id);
          if (response?.id && (!speech.hasAudio || speech.drained)) this.settleResponse(response.id);
        }
        if (this.launchResponseId && response?.id === this.launchResponseId && response.status === "completed") {
          this.drainDeparture();
        }
        if (this.finalResponseId && response?.id === this.finalResponseId) {
          if (response.status === "completed") {
            this.drainFinalResponse();
          } else if (response.status) {
            this.handlers.onError("최종 음성 안내가 중단되어 다시 시도하고 있습니다. 화면의 구조 결과와 최종 설명도 확인할 수 있습니다.");
          }
        }
        if (this.responseId && response?.id !== this.responseId) return;
        const text = speech ? [...speech.parts.values()].join("") : "";
        if (text.trim()) {
          this.handlers.onTranscript("agent", text, true);
        }
        if (this.toolCallPending) {
          this.toolCallPending = false;
        } else {
          this.responseActive = false;
          this.handlers.onBusy(false);
          if (response?.id === this.replyResponseId && !this.debriefRunId && !this.launchRunId) {
            this.drainReply();
          }
        }
        this.tryOpenInput();
        return;
      }

      case "tool.started": {
        this.handlers.onTool({
          id: String(msg.id ?? msg.requestId ?? msg.call_id),
          name: String(msg.name),
        });
        return;
      }
      case "tool.finished": {
        const result = msg.result as { ok?: boolean; facts?: string; error?: string } | undefined;
        this.handlers.onTool({
          id: String(msg.id ?? msg.requestId ?? msg.call_id),
          name: String(msg.name),
          ok: result?.ok,
          facts: result?.facts,
          ms: msg.ms as number | undefined,
        });
        if (result?.ok === false) {
          this.handlers.onError(result.facts ?? result.error ?? "요청을 처리하지 못했습니다.");
        }
        return;
      }

      case "route.state": {
        const state = msg.state as DashboardState;
        if (this.currentRunId && state.runId !== this.currentRunId) return;
        if (typeof state.revision === "number") {
          if (state.revision < this.routeRevision) return;
          this.routeRevision = state.revision;
        }
        this.currentRunId = state.runId;
        this.handlers.onRouteState(state);
        return;
      }

      case "metrics.ttfa": {
        this.handlers.onTtfa(msg.ms as number);
        return;
      }

      case "error": {
        const err = msg.error as { message?: string } | undefined;
        // 응답이 실패하면 response.done이 오지 않으므로 입력창을 직접 푼다.
        this.responseActive = false;
        this.toolCallPending = false;
        if (this.responseId) {
          const speech = this.speech.get(this.responseId);
          if (speech) {
            speech.terminal = true;
            if (!speech.hasAudio) this.settleResponse(this.responseId);
          }
        }
        if (this.replyResponseId && !this.debriefRunId && !this.launchRunId) {
          this.drainReply();
        }
        this.handlers.onBusy(false);
        this.handlers.onError("음성 응답에 오류가 발생했습니다. 화면 조작은 계속 사용할 수 있습니다.");
        if (this.narratingResults) {
          this.handlers.onStatus("error", err?.message);
          this.fallbackResults();
          void this.closeSession(true, true);
          return;
        }
        // Voice Live의 error는 대부분 그 응답 하나만 실패한 것이고 세션은 살아
        // 있다. 여기서 status를 "error"로 바꾸면 마이크가 계속 열려 있는데도
        // 화면은 "세션 시작"으로 돌아가 세션이 끝난 것처럼 보인다.
        this.handlers.onStatus(
          this.microphoneMuted ? "processing" : "listening",
          err?.message ?? "알 수 없는 오류가 났습니다.",
        );
        this.tryOpenInput();
        return;
      }
    }
  }

  private setMicrophoneMuted(muted: boolean) {
    const changed = this.microphoneMuted !== muted;
    this.microphoneMuted = muted;
    if (changed) this.inputEpoch++;
    this.captureNode?.port.postMessage({ type: "mute", value: muted, epoch: this.inputEpoch });
    if (changed && this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "voice.input_state", muted }));
    }
    if (muted) this.handlers.onLevel(0);
  }

  private drainReply(id: string | null = this.replyResponseId) {
    if (!id) return;
    const speech = this.speech.get(id);
    if (!speech?.hasAudio || speech.drainRequested) return;
    speech.drainRequested = true;
    // Generation can finish seconds before the last queued sentence is audible.
    this.playbackNode?.port.postMessage({ type: "drain", id });
  }

  private drainFinalResponse() {
    if (!this.finalResponseId || this.finalDrainRequested) return;
    this.finalDrainRequested = true;
    // The worklet acknowledges this barrier after all previously queued PCM.
    this.playbackNode?.port.postMessage({ type: "drain", id: this.finalResponseId });
  }

  private drainDeparture() {
    if (!this.launchResponseId || this.launchDrainRequested || this.voiceStopped) return;
    this.launchDrainRequested = true;
    this.playbackNode?.port.postMessage({ type: "drain", id: this.launchResponseId });
  }
}

/**
 * 음성 인식이 무음이나 잡음 구간에서 지어낸 말인지 판단한다.
 *
 * "쭈쭈쭈쭈!"처럼 한 음절이 반복되는 형태가 대표적이다. 실제 발화에서 같은
 * 음절이 네 번 넘게 이어지는 경우는 거의 없으므로, 그 이상만 걸러낸다.
 * ("네네네" 같은 자연스러운 반복은 통과시킨다.)
 */
function looksHallucinated(text: string): boolean {
  const core = text.replace(/[\s!?.,~…]+/gu, "");
  if (!core) return true;
  const chars = Array.from(core);
  // 한 글자가 4번 이상 반복된 것만
  if (chars.length >= 4 && chars.every((c) => c === chars[0])) return true;
  return false;
}

function toBase64(int16: Int16Array): string {
  const bytes = new Uint8Array(int16.buffer);
  let s = "";
  const STEP = 0x8000;
  for (let i = 0; i < bytes.length; i += STEP) {
    s += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + STEP)));
  }
  return btoa(s);
}

function fromBase64(b64: string): Int16Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Int16Array(bytes.buffer);
}
