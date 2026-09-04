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

import type { AnomalyResult, RoutePlanningState } from "@/lib/types";

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
  args: Record<string, unknown>;
  /** What actually happened, for the tooltip. The relay calls this `facts`. */
  facts?: string;
  ok?: boolean;
  ms?: number;
}

export interface VoiceHandlers {
  onStatus: (status: VoiceStatus, detail?: string) => void;
  onLevel: (peak: number) => void;
  /** Streaming transcript. `final` marks the end of that speaker's turn. */
  onTranscript: (role: "user" | "agent", text: string, final: boolean) => void;
  onRouteState: (state: RoutePlanningState, anomalies: AnomalyResult[]) => void;
  onTool: (activity: ToolActivity) => void;
  onTtfa: (ms: number) => void;
  /** True while a response is generating. Sending during one is rejected. */
  onBusy: (busy: boolean) => void;
  /** 세션은 살아 있지만 알려줄 만한 일이 생겼을 때 (실패한 응답 등). */
  onNotice: (message: string) => void;
}

export interface RelayConfig {
  model: string;
  voice: string;
  region: string;
  resource: string;
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
  private toolSeq = 0;
  private responseActive = false;
  private toolCallPending = false;
  // The mic streams from the moment the graph is wired, which can trigger VAD
  // before the greeting is requested. That collision kills the greeting with
  // "conversation already has an active response", so stay muted until the
  // greeting has finished playing.
  private greetPending = true;

  constructor(private handlers: VoiceHandlers) {}

  get isRunning() {
    return this.running;
  }

  async start(): Promise<void> {
    if (this.running) return;
    this.handlers.onStatus("connecting", "Requesting microphone…");

    try {
      // Browser-side cleanup. Server-side echo cancellation alone is not enough
      // when the machine's own speakers are in the room.
      this.micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      this.audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
      // The getUserMedia permission prompt consumes the click gesture, so
      // Chrome hands back a suspended context and the greeting is never heard.
      if (this.audioCtx.state === "suspended") await this.audioCtx.resume();

      await this.audioCtx.audioWorklet.addModule("/audio-worklets.js");

      const source = this.audioCtx.createMediaStreamSource(this.micStream);
      this.captureNode = new AudioWorkletNode(this.audioCtx, "capture-processor");
      // Hold the mic until the greeting has been spoken.
      this.greetPending = true;
      this.captureNode.port.postMessage({ type: "mute", value: true });
      this.playbackNode = new AudioWorkletNode(this.audioCtx, "playback-processor", {
        outputChannelCount: [1],
      });

      this.captureNode.port.onmessage = (e) => {
        const { pcm, peak } = e.data as { pcm: Int16Array; peak: number };
        this.handlers.onLevel(peak);
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ type: "audio", data: toBase64(pcm) }));
        }
      };
      this.playbackNode.port.onmessage = (e) => {
        const msg = e.data as { type: string; playing: boolean };
        if (msg.type === "state") {
          this.handlers.onStatus(msg.playing ? "speaking" : "listening");
        }
      };

      source.connect(this.captureNode);
      // Keep the capture graph alive without routing the mic to the speakers.
      this.captureNode.connect(this.audioCtx.createGain()).connect(this.audioCtx.destination);
      this.playbackNode.connect(this.audioCtx.destination);

      if (this.audioCtx.state === "suspended") await this.audioCtx.resume();

      await this.connect();
      this.running = true;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.handlers.onStatus("error", message);
      await this.stop(true);
      throw err;
    }
  }

  async stop(keepStatus = false): Promise<void> {
    this.running = false;
    this.responseActive = false;
    this.toolCallPending = false;
    this.handlers.onLevel(0);
    this.handlers.onBusy(false);
    if (!keepStatus) this.handlers.onStatus("idle");

    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        /* already closing */
      }
      this.ws = null;
    }
    this.micStream?.getTracks().forEach((t) => t.stop());
    this.micStream = null;
    if (this.audioCtx) {
      try {
        await this.audioCtx.close();
      } catch {
        /* already closed */
      }
      this.audioCtx = null;
    }
    this.captureNode = null;
    this.playbackNode = null;
  }

  sendText(text: string): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    // Voice Live rejects a second response while one is generating with
    // "conversation already has an active response".
    if (this.responseActive) return false;
    this.ws.send(JSON.stringify({ type: "text", text }));
    return true;
  }

  private connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(RELAY_WS);
      this.ws = ws;

      ws.onerror = () =>
        reject(new Error("Could not reach the relay. Is relay/server.py running?"));
      ws.onclose = (ev) => {
        if (!this.running) return;
        // A drop mid-session is silent otherwise, which looks like the agent
        // simply stopped answering.
        const why = ev.reason?.trim()
          ? ev.reason
          : `Relay connection closed (code ${ev.code}).`;
        this.handlers.onStatus("error", why);
        void this.stop(true);
      };
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data as string);
        if (msg.type === "relay.ready") {
          this.handlers.onStatus("listening");
          ws.send(JSON.stringify({ type: "greet" }));
          resolve();
          return;
        }
        if (msg.type === "relay.error") {
          this.handlers.onStatus("error", msg.message);
          reject(new Error(msg.message));
          return;
        }
        this.handleEvent(msg);
      };
    });
  }

  private handleEvent(msg: Record<string, unknown>) {
    const type = String(msg.type ?? "");

    switch (type) {
      case "response.created": {
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
        // Barge-in: drop queued audio so the agent stops mid-word.
        this.playbackNode?.port.postMessage({ type: "flush" });
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
        if (this.partialAgent.trim()) {
          this.handlers.onTranscript("agent", this.partialAgent, true);
        }
        this.partialAgent = "";
        if (this.toolCallPending) {
          this.toolCallPending = false;
        } else {
          this.responseActive = false;
          this.handlers.onBusy(false);
          if (this.greetPending) {
            // Greeting is done; it is now safe to open the mic.
            this.greetPending = false;
            this.captureNode?.port.postMessage({ type: "mute", value: false });
          }
        }
        return;
      }

      case "tool.started": {
        this.toolSeq += 1;
        this.handlers.onTool({
          id: `tool-${this.toolSeq}`,
          name: String(msg.name),
          args: (msg.args as Record<string, unknown>) ?? {},
        });
        return;
      }
      case "tool.finished": {
        const result = msg.result as { ok?: boolean; facts?: string } | undefined;
        this.handlers.onTool({
          id: `tool-${this.toolSeq}`,
          name: String(msg.name),
          args: {},
          ok: result?.ok,
          facts: result?.facts,
          ms: msg.ms as number | undefined,
        });
        return;
      }

      case "route.state": {
        this.handlers.onRouteState(
          msg.state as RoutePlanningState,
          (msg.anomalies as AnomalyResult[]) ?? []
        );
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
        if (this.greetPending) {
          this.greetPending = false;
          this.captureNode?.port.postMessage({ type: "mute", value: false });
        }
        this.handlers.onBusy(false);
        // Voice Live의 error는 대부분 그 응답 하나만 실패한 것이고 세션은 살아
        // 있다. 여기서 status를 "error"로 바꾸면 마이크가 계속 열려 있는데도
        // 화면은 "세션 시작"으로 돌아가 세션이 끝난 것처럼 보인다.
        this.handlers.onNotice(err?.message ?? "알 수 없는 오류가 났습니다.");
        this.handlers.onStatus("listening");
        return;
      }
    }
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
