"""Read-only, bounded 1 Hz observation sink; never owns a control/video socket.

Uses the decoder's immutable latest snapshot. Slow disk/image work drops
intermediate frames instead of blocking flight control or building a backlog.
Context and image have separate timestamps/keys: temporal proximity is not
proof that an SDK ACK caused the motion visible in an image.
"""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time


class FrameRecorder:
    def __init__(self, directory: Path, capture, *, interval_s=1.0,
                 max_frames=1200, writer=None):
        if interval_s <= 0 or max_frames < 1:
            raise ValueError("positive recording interval and capacity required")
        self.directory = Path(directory)
        self.capture = capture
        self.interval_s = interval_s
        self.max_frames = max_frames
        self._writer = writer
        self._lock = threading.Lock()
        self._context = {}
        self._stop = threading.Event()
        self.saved_frames = 0
        self.errors = 0
        self.last_error = None
        self._last_key = None
        self._thread = threading.Thread(target=self._run, name="frame-observer", daemon=True)
        self._thread.start()

    def update_context(self, context):
        # Caller gives a detached dataclass/dict snapshot, not mutable SDK state.
        with self._lock:
            self._context = dict(context)

    def _record_once(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        snapshot = self.capture.snapshot()
        now = time.monotonic()
        with self._lock:
            context = self._context
        event = {"schema_version": 1, "observed_monotonic_s": now,
                 "observed_unix_s": time.time(), "context": context,
                 "video": self.capture.diagnostics(), "image_path": None,
                 "status": "NO_FRESH_FRAME"}
        if snapshot is not None:
            age = now - snapshot.received_s
            event.update(frame_key=snapshot.key, frame_age_s=age,
                         decoded_monotonic_s=snapshot.received_s)
            if 0 <= age <= 1.0:
                event["status"] = "DUPLICATE_FRAME"
                if snapshot.key != self._last_key:
                    path = self.directory / f"g{snapshot.generation:04d}_f{snapshot.frame_id:08d}.jpg"
                    writer = self._writer
                    if writer is None:
                        import cv2
                        writer = cv2.imwrite
                    if not writer(str(path), snapshot.frame):
                        raise OSError("image writer rejected frame")
                    self._last_key = snapshot.key
                    self.saved_frames += 1
                    event.update(status="FRAME_SAVED", image_path=str(path))
        with (self.directory / "observations.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")

    def _run(self):
        # Total samples, not only JPEGs, are bounded even with a dead stream.
        for _ in range(self.max_frames):
            if self._stop.is_set():
                break
            started = time.monotonic()
            try:
                self._record_once()
            except Exception as error:
                self.errors += 1
                self.last_error = repr(error)
            delay = max(0.01, self.interval_s - (time.monotonic() - started))
            if self._stop.wait(delay):
                break

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2.0)
