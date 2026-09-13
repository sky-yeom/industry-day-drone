// Audio worklets for the drone survey dashboard.
//
// Voice Live speaks PCM16 mono at 24 kHz in both directions. The AudioContext is
// created at 24 kHz so the browser resamples the mic for us and no manual rate
// conversion is needed here.

const CHUNK_SAMPLES = 480; // 20 ms at 24 kHz

class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = new Float32Array(CHUNK_SAMPLES);
    this._n = 0;
    this._muted = false;
    this._epoch = 0;
    this.port.onmessage = (e) => {
      if (e.data && e.data.type === 'mute') {
        this._muted = !!e.data.value;
        this._n = 0;
        if (Number.isInteger(e.data.epoch)) {
          this._epoch = e.data.epoch;
          this.port.postMessage({ type: 'input-state', muted: this._muted, epoch: this._epoch });
        }
      }
    };
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const ch = input[0];

    for (let i = 0; i < ch.length; i++) {
      this._buf[this._n++] = ch[i];
      if (this._n === CHUNK_SAMPLES) {
        if (!this._muted) {
          const pcm = new Int16Array(CHUNK_SAMPLES);
          let peak = 0;
          for (let j = 0; j < CHUNK_SAMPLES; j++) {
            const s = Math.max(-1, Math.min(1, this._buf[j]));
            pcm[j] = s < 0 ? s * 0x8000 : s * 0x7fff;
            const a = Math.abs(s);
            if (a > peak) peak = a;
          }
          this.port.postMessage({ pcm, peak, epoch: this._epoch }, [pcm.buffer]);
        }
        this._n = 0;
      }
    }
    return true;
  }
}

class PlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._queue = [];
    this._cur = null;
    this._pos = 0;
    this._playing = false;
    this._drainIds = [];
    this._startedIds = new Set();
    this._responseId = null;
    this._paused = false;
    this._heldIds = new Set();
    this._outputEnd = undefined;
    this.port.onmessage = (e) => {
      const msg = e.data;
      if (msg.type === 'push') {
        this._queue.push({ pcm: msg.pcm, id: msg.id });
      } else if (msg.type === 'hold') {
        this._heldIds.add(msg.id);
      } else if (msg.type === 'release') {
        this._heldIds.delete(msg.id);
      } else if (msg.type === 'flush') {
        // Used when the entire audio session is being retired.
        this._queue.length = 0;
        this._cur = null;
        this._pos = 0;
        this._drainIds.length = 0;
        this._startedIds.clear();
        this._responseId = null;
        this._paused = false;
        this._heldIds.clear();
        this._outputEnd = undefined;
      } else if (msg.type === 'pause') {
        this._paused = !!msg.value;
      } else if (msg.type === 'discard') {
        const ids = new Set(msg.ids);
        this._queue = this._queue.filter(chunk => !ids.has(chunk.id));
        this._drainIds = this._drainIds.filter(id => !ids.has(id));
        for (const id of ids) {
          this._startedIds.delete(id);
          this._heldIds.delete(id);
        }
        if (ids.has(this._responseId)) {
          this._cur = null;
          this._responseId = null;
          this._pos = 0;
        }
      } else if (msg.type === 'drain') {
        this._drainIds.push(msg.id);
      }
    };
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;
    if (this._paused) {
      out.fill(0);
      if (this._playing) {
        this._playing = false;
        this.port.postMessage({ type: 'state', playing: false });
      }
      return true;
    }

    let written = 0;
    let held = false;
    while (written < out.length) {
      if (this._cur === null) {
        if (this._queue.length === 0) break;
        if (this._heldIds.has(this._queue[0].id)) {
          held = true;
          break;
        }
        const chunk = this._queue.shift();
        this._cur = chunk.pcm;
        this._responseId = chunk.id;
        this._pos = 0;
      }
      if (this._heldIds.has(this._responseId)) {
        held = true;
        break;
      }
      const remaining = this._cur.length - this._pos;
      const take = Math.min(remaining, out.length - written);
      for (let i = 0; i < take; i++) {
        out[written + i] = this._cur[this._pos + i] / 0x8000;
      }
      if (take > 0 && this._responseId && !this._startedIds.has(this._responseId)) {
        this._startedIds.add(this._responseId);
        this.port.postMessage({ type: 'started', id: this._responseId });
      }
      written += take;
      if (take > 0 && typeof currentFrame === 'number' && typeof sampleRate === 'number') {
        this._outputEnd = (currentFrame + written) / sampleRate;
      }
      this._pos += take;
      if (this._pos >= this._cur.length) this._cur = null;
    }
    for (let i = written; i < out.length; i++) out[i] = 0;

    const busy = this._cur !== null || this._queue.length > 0;
    const playing = busy && !held;
    if (playing !== this._playing) {
      this._playing = playing;
      this.port.postMessage({ type: 'state', playing });
    }
    if (!busy && this._drainIds.length) {
      for (const id of this._drainIds) this.port.postMessage({ type: 'drained', id, contextTime: this._outputEnd });
      this._drainIds.length = 0;
    }
    return true;
  }
}

registerProcessor('capture-processor', CaptureProcessor);
registerProcessor('playback-processor', PlaybackProcessor);
