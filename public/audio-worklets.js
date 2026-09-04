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
    this.port.onmessage = (e) => {
      if (e.data && e.data.type === 'mute') this._muted = !!e.data.value;
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
          this.port.postMessage({ pcm, peak }, [pcm.buffer]);
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
    this.port.onmessage = (e) => {
      const msg = e.data;
      if (msg.type === 'push') {
        this._queue.push(msg.pcm);
      } else if (msg.type === 'flush') {
        // Barge-in: drop everything still queued so the agent stops mid-word.
        this._queue.length = 0;
        this._cur = null;
        this._pos = 0;
      }
    };
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;

    let written = 0;
    while (written < out.length) {
      if (this._cur === null) {
        if (this._queue.length === 0) break;
        this._cur = this._queue.shift();
        this._pos = 0;
      }
      const remaining = this._cur.length - this._pos;
      const take = Math.min(remaining, out.length - written);
      for (let i = 0; i < take; i++) {
        out[written + i] = this._cur[this._pos + i] / 0x8000;
      }
      written += take;
      this._pos += take;
      if (this._pos >= this._cur.length) this._cur = null;
    }
    for (let i = written; i < out.length; i++) out[i] = 0;

    const busy = this._cur !== null || this._queue.length > 0;
    if (busy !== this._playing) {
      this._playing = busy;
      this.port.postMessage({ type: 'state', playing: busy });
    }
    return true;
  }
}

registerProcessor('capture-processor', CaptureProcessor);
registerProcessor('playback-processor', PlaybackProcessor);
