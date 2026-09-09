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

const RELAY_HTTP =
  process.env.NEXT_PUBLIC_RELAY_HTTP ?? "http://127.0.0.1:8080";
const RELAY_WS = process.env.NEXT_PUBLIC_RELAY_WS ?? "ws://127.0.0.1:8080/ws";

export type VoiceStatus =
  | "idle"
  | "connecting"
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
  private partialAgent = "";
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
  private rejectConnect: ((reason: Error) => void) | null = null;
  private responseActive = false;
  private toolCallPending = false;
  private replyResponseId: string | null = null;
  private replyDrainRequested = false;
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
    this.replyResponseId = null;
    this.replyDrainRequested = false;
    this.microphoneMuted = true;
    this.partialUser = "";
    this.partialAgent = "";
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
        const { pcm, peak } = e.data as { pcm: Int16Array; peak: number };
        this.handlers.onLevel(peak);
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ type: "audio", data: toBase64(pcm) }));
        }
      };
      this.playbackNode.port.onmessage = (e) => {
        if (generation !== this.generation) return;
        const msg = e.data as { type: string; playing: boolean; id?: string };
        if (msg.type === "state") {
          if (!this.voiceStopped || this.narratingResults) {
            this.handlers.onStatus(msg.playing || this.replyResponseId || this.narratingResults ? "speaking" : "listening");
          }
        }
        if (msg.type === "drained" && this.launchDrainRequested && msg.id === this.launchResponseId) {
          void this.finishDeparture();
        } else if (msg.type === "drained" && msg.id === this.finalResponseId) {
          void this.stop();
        } else if (msg.type === "drained" && this.replyDrainRequested && msg.id === this.replyResponseId) {
          this.replyResponseId = null;
          this.replyDrainRequested = false;
          if (!this.responseActive && !this.launchRunId && !this.debriefRunId) {
            this.greetPending = false;
            this.ws?.send(JSON.stringify({ type: "input_audio_buffer.clear" }));
            this.setMicrophoneMuted(false);
            this.handlers.onStatus("listening");
          }
        }
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
      const message = withVoice
        ? "음성 연결을 시작하지 못했습니다. 마이크 권한과 음성 서비스 설정을 확인한 뒤 연결 다시 시도를 눌러 주세요."
        : "관제 서버에 연결하지 못했습니다. 릴레이 실행 상태를 확인하세요.";
      this.handlers.onStatus("error", message);
      this.handlers.onError(message);
      await this.stop(true);
      throw err;
    }
  }

  async stop(keepStatus = false): Promise<void> {
    ++this.generation;
    this.rejectConnect?.(new Error("세션이 종료되었습니다."));
    this.rejectConnect = null;
    this.running = false;
    this.handlers.onConnection?.(false);
    this.responseActive = false;
    this.toolCallPending = false;
    this.replyResponseId = null;
    this.replyDrainRequested = false;
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

  private async finishDeparture() {
    if (this.voiceStopped) return;
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
    this.handlers.onDeparture?.();
    if (this.missionEnded) this.requestResults();
  }

  private requestResults() {
    if (this.resultsRequested || !this.debriefRunId || this.ws?.readyState !== WebSocket.OPEN) return;
    this.resultsRequested = true;
    this.narratingResults = true;
    this.handlers.onStatus("connecting");
    this.ws.send(JSON.stringify({ type: "results.ready", runId: this.debriefRunId }));
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

  private connect(generation: number): Promise<void> {
    return new Promise((resolve, reject) => {
      this.rejectConnect = reject;
      const url = new URL(RELAY_WS);
      url.searchParams.set("voice", this.withVoice ? "1" : "0");
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
        void this.stop(true);
      };
      ws.onmessage = (e) => {
        if (generation !== this.generation) return;
        const msg = JSON.parse(e.data as string);
        if (msg.type === "relay.ready") {
          this.rejectConnect = null;
          this.handlers.onConnection?.(true);
          this.handlers.onStatus("listening");
          if (this.withVoice) ws.send(JSON.stringify({ type: "greet" }));
          resolve();
          return;
        }
        if (msg.type === "relay.error") {
          this.handlers.onStatus("error", msg.message);
          this.handlers.onError(msg.message);
          reject(new Error(msg.message));
          if (this.running) void this.stop(true);
          return;
        }
        this.handleEvent(msg);
      };
    });
  }

  private handleEvent(msg: Record<string, unknown>) {
    const type = String(msg.type ?? "");

    switch (type) {
      case "mission.launch": {
        if (String(msg.runId) !== this.currentRunId) return;
        this.launchRunId = this.currentRunId;
        this.greetPending = false;
        this.setMicrophoneMuted(true);
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
        void this.finishDeparture();
        return;
      }
      case "mission.debrief": {
        this.missionEnded = true;
        this.debriefRunId = String(msg.runId ?? this.currentRunId);
        this.handlers.onDebrief(String(msg.text ?? ""));
        this.setMicrophoneMuted(true);
        if (!this.withVoice) void this.stop();
        else if (this.voiceStopped) this.requestResults();
        return;
      }
      case "mission.debrief.failed": {
        if (msg.runId !== this.debriefRunId) return;
        this.handlers.onError(String(msg.message ?? "결과 음성 안내를 완료하지 못했습니다."));
        void this.stop();
        return;
      }
      case "mission.debrief.response": {
        if (String(msg.runId ?? this.currentRunId) === this.debriefRunId) {
          this.finalResponseId = String(msg.responseId ?? "");
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
        if (response?.id && this.debriefRunId !== null &&
            (metadata?.missionDebrief === true || metadata?.missionDebrief === "true") &&
            metadata.runId === this.debriefRunId) {
          this.finalResponseId = response.id;
          this.setMicrophoneMuted(true);
        }
        if (response?.id && !this.launchRunId && !this.debriefRunId) {
          this.replyResponseId = response.id;
          this.replyDrainRequested = false;
        }
        this.setMicrophoneMuted(true);
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
        if (delta && this.playbackNode) {
          this.playbackNode.port.postMessage({ type: "push", pcm: fromBase64(delta) });
        }
        return;
      }

      case "input_audio_buffer.speech_started": {
        if (this.microphoneMuted || this.debriefRunId !== null || this.launchRunId !== null) return;
        this.handlers.onStatus("listening");
        // Whisper's transcript lands about a second later, but the agent starts
        // answering in ~460ms. Reserve the user's bubble now or the reply is
        // rendered above the question that prompted it.
        this.partialUser = "";
        this.handlers.onTranscript("user", "…", false);
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
      case "response.output_audio_transcript.delta": {
        this.partialAgent += (msg.delta as string) ?? "";
        this.handlers.onTranscript("agent", this.partialAgent, false);
        return;
      }
      case "response.done": {
        const response = msg.response as { id?: string; status?: string } | undefined;
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
        if (this.partialAgent.trim()) {
          this.handlers.onTranscript("agent", this.partialAgent, true);
        }
        this.partialAgent = "";
        if (this.toolCallPending) {
          this.toolCallPending = false;
        } else {
          this.responseActive = false;
          this.handlers.onBusy(false);
          if (response?.id === this.replyResponseId && !this.debriefRunId && !this.launchRunId) {
            this.drainReply();
          }
        }
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
        if (this.replyResponseId && !this.debriefRunId && !this.launchRunId) {
          this.drainReply();
        } else if (this.greetPending && this.debriefRunId === null && this.launchRunId === null) {
          this.greetPending = false;
          this.setMicrophoneMuted(false);
        }
        this.handlers.onBusy(false);
        this.handlers.onError("음성 응답에 오류가 발생했습니다. 화면 조작은 계속 사용할 수 있습니다.");
        // Voice Live의 error는 대부분 그 응답 하나만 실패한 것이고 세션은 살아
        // 있다. 여기서 status를 "error"로 바꾸면 마이크가 계속 열려 있는데도
        // 화면은 "세션 시작"으로 돌아가 세션이 끝난 것처럼 보인다.
        this.handlers.onStatus(
          this.replyResponseId ? "speaking" : "listening",
          err?.message ?? "알 수 없는 오류가 났습니다.",
        );
        return;
      }
    }
  }

  private setMicrophoneMuted(muted: boolean) {
    this.microphoneMuted = muted;
    this.captureNode?.port.postMessage({ type: "mute", value: muted });
    if (muted) this.handlers.onLevel(0);
  }

  private drainReply() {
    if (!this.replyResponseId || this.replyDrainRequested) return;
    this.replyDrainRequested = true;
    // Generation can finish seconds before the last queued sentence is audible.
    this.playbackNode?.port.postMessage({ type: "drain", id: this.replyResponseId });
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
