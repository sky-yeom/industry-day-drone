"""Opt-in, loopback-only conversation diagnostics; never records audio or images."""
import base64
import binascii
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import struct
import time


log = logging.getLogger("relay.voice_trace")
MAX_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 2000
MAX_SECONDS = 1800


def validate_directory(directory, host):
    path = Path(directory)
    if host not in {"127.0.0.1", "localhost", "::1"} or not path.is_absolute():
        raise ValueError("Voice transcripts require a local relay and an absolute private directory.")
    path = path.resolve()
    for key in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        if os.getenv(key) and path.is_relative_to(Path(os.environ[key]).resolve()):
            raise ValueError("Voice transcripts must be stored outside OneDrive.")
    if path.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("Voice transcripts must be stored outside the repository.")
    return path


class VoiceTrace:
    def __init__(self, directory, run_id, host):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
            raise ValueError("Invalid trace session identifier.")
        folder = validate_directory(directory, host)
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"{run_id}.jsonl"
        self.file = self.path.open("x", encoding="utf-8", buffering=1)
        self.started = self.last_audio = time.monotonic()
        self.events = self.bytes = 0
        self.chunks = self.audio_bytes = self.peak = 0
        self.secrets = tuple(
            value for key, value in os.environ.items()
            if re.search(r"TOKEN|SECRET|PASSWORD|API_KEY", key) and len(value) >= 8)
        self.record("session_started", run_id=run_id, audio_recorded=False)

    def _clean(self, value, depth=0):
        if depth > 5:
            return "[depth limit]"
        if isinstance(value, dict):
            return {str(key): ("[REDACTED]" if re.search(
                r"token|secret|password|authorization|api.?key", str(key), re.I)
                else self._clean(item, depth + 1)) for key, item in list(value.items())[:40]}
        if isinstance(value, (tuple, list)):
            return [self._clean(item, depth + 1) for item in value[:40]]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[REDACTED]")
            return value if len(value) <= 6000 else value[:6000] + "[truncated]"
        if value is None or isinstance(value, (int, float, bool)):
            return value
        return type(value).__name__

    def record(self, event, **fields):
        if self.file is None:
            return
        elapsed = time.monotonic() - self.started
        limited = self.events >= MAX_EVENTS or self.bytes >= MAX_BYTES or elapsed >= MAX_SECONDS
        value = {"at": datetime.now(timezone.utc).isoformat(), "elapsed_s": round(elapsed, 3),
                 "event": "trace_limit_reached" if limited else event,
                 **({} if limited else self._clean(fields))}
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n"
        try:
            self.file.write(encoded)
            self.bytes += len(encoded.encode("utf-8"))
            self.events += 1
            if limited:
                self.close()
        except OSError:
            log.exception("Voice trace write failed; transcript recording has stopped")
            self.close()

    def audio_forwarded(self, encoded):
        if self.file is None:
            return
        try:
            if not isinstance(encoded, str) or len(encoded) > 131072:
                raise ValueError("invalid audio length")
            audio = base64.b64decode(encoded, validate=True)
            if len(audio) % 2:
                raise ValueError("invalid PCM length")
            peak = max((abs(value[0]) for value in struct.iter_unpack("<h", audio)), default=0)
        except (ValueError, binascii.Error):
            self.record("audio_metadata_invalid")
            return
        self.chunks += 1
        self.audio_bytes += len(audio)
        self.peak = max(self.peak, peak)
        if time.monotonic() - self.last_audio >= 1:
            self.record("audio_forwarded", chunks=self.chunks, bytes=self.audio_bytes,
                        peak_pcm=self.peak, non_silent=self.peak > 64)
            self.last_audio = time.monotonic()
            self.chunks = self.audio_bytes = self.peak = 0

    def browser_event(self, payload):
        kind = payload.get("type")
        if kind in {"conversation.item.input_audio_transcription.completed",
                    "conversation.item.input_audio_transcription.failed",
                    "response.audio_transcript.done", "response.output_audio_transcript.done"}:
            self.record(kind, **{key: payload.get(key) for key in
                        ("item_id", "response_id", "transcript", "content_index")})
        elif kind in {"input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped"}:
            self.record(kind, item_id=payload.get("item_id"))
        elif kind in {"relay.ready", "mission.launch", "mission.debrief",
                      "relay.error", "mission.debrief.failed"}:
            self.record(kind, **{key: payload.get(key) for key in ("runId", "text", "message", "code")})
        elif kind == "response.done":
            response = payload.get("response") or {}
            self.record(kind, response_id=response.get("id"), status=response.get("status"))
        elif kind == "response.function_call_arguments.done":
            self.record("model_tool_call", **{key: payload.get(key) for key in
                        ("response_id", "call_id", "name", "arguments")})

    def close(self):
        if self.file is not None:
            file, self.file = self.file, None
            file.close()
