"""Explicit, leased preview sharing the mission's single video connection."""
from __future__ import annotations

import base64
import math
from pathlib import Path
import struct
import threading
import time

from ..vision import VideoSnapshot, VisionDependencyError


MAX_EDGE = 960
MAX_BYTES = 512 * 1024
MAX_AGE_S = .5
ENCODE_INTERVAL_S = .2
MOCK_MESSAGE = "MOCK preview: public/monitors/monitor-1.png fixture; not a live camera."


class PreviewUnavailable(RuntimeError):
    pass


def encode_jpeg(frame):
    try:
        import cv2
    except ImportError as exc:
        raise PreviewUnavailable("Camera encoder unavailable") from exc
    try:
        height, width = frame.shape[:2]
        if height <= 0 or width <= 0:
            raise PreviewUnavailable("Invalid decoded frame")
        if max(height, width) > MAX_EDGE:
            scale = MAX_EDGE / max(height, width)
            frame = cv2.resize(frame, (max(1, int(width * scale)), max(1, int(height * scale))),
                               interpolation=cv2.INTER_AREA)
        # A fixed low quality bounds work per request; oversized frames are refused.
        ok, data = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
    except cv2.error as exc:
        raise PreviewUnavailable("Camera encoder unavailable") from exc
    if not ok or data.size > MAX_BYTES:
        raise PreviewUnavailable("Camera encoding exceeds preview limits")
    return "image/jpeg", data.tobytes()


class FixtureStream:
    """Lazy, local-only fixture source. No vision dependency or network access."""
    def __init__(self, path, clock):
        with Path(path).open("rb") as source:
            self.data = source.read(MAX_BYTES + 1)
        if (len(self.data) > MAX_BYTES or len(self.data) < 24
                or not self.data.startswith(b"\x89PNG\r\n\x1a\n")
                or self.data[12:16] != b"IHDR"):
            raise PreviewUnavailable("Mock fixture unavailable")
        if not all(0 < size <= MAX_EDGE for size in struct.unpack(">II", self.data[16:24])):
            raise PreviewUnavailable("Mock fixture exceeds preview limits")
        self.clock = clock
        self.latest = None

    def snapshot(self):
        now = self.clock()
        if self.latest is None or now - self.latest.received_s >= ENCODE_INTERVAL_S:
            self.latest = VideoSnapshot(1, 1 if self.latest is None else self.latest.frame_id + 1,
                                        now, self.data)
        return self.latest

    def diagnostics(self):
        return {"state": "STREAMING"}

    def close(self):
        pass


class VideoBroker:
    """Owns exactly one stream until both mission and preview leases release it.

    Only the mission receives the detector-capable stream. Preview snapshots and
    encodes immutable decoded pixels, without touching detection caches.
    """
    def __init__(self, stream_factory, *, simulated=False, encoder=encode_jpeg,
                 clock=time.monotonic, lease_seconds=5., max_viewers=4):
        self.factory, self.simulated, self.encoder = stream_factory, simulated, encoder
        self.clock, self.lease_seconds, self.max_viewers = clock, lease_seconds, max_viewers
        self.lock = threading.RLock()
        self.encoding = threading.Lock()
        self.stream = None
        self.mission = False
        self.viewers = {}
        self.closed = False
        self.open_failed = False
        self.encoding_failed = False
        self.cached = None
        self.last_encode_at = -math.inf
        self.epoch = 0
        self.stopping = threading.Event()
        self.watcher = None

    @classmethod
    def mock(cls, **kwargs):
        clock = kwargs.get("clock", time.monotonic)
        fixture = Path(__file__).resolve().parents[4] / "public" / "monitors" / "monitor-1.png"
        return cls(lambda: FixtureStream(fixture, clock), simulated=True,
                   encoder=lambda data: ("image/png", data), **kwargs)

    def _open(self):
        if self.stream is None and not self.open_failed:
            if self.factory is None:
                self.open_failed = True
                return
            try:
                self.stream = self.factory()
            except (OSError, VisionDependencyError, PreviewUnavailable):
                self.open_failed = True
                return
            self.epoch += 1

    def _release_unused(self):
        if not self.mission and not self.viewers:
            if self.stream is not None:
                self.stream.close()
                self.stream = None
            self.cached = None
            self.open_failed = self.encoding_failed = False
            self.last_encode_at = -math.inf

    def expire(self):
        with self.lock:
            now = self.clock()
            self.viewers = {caller: deadline for caller, deadline in self.viewers.items() if now < deadline}
            self._release_unused()

    def _watch(self):
        while not self.stopping.wait(.1):
            self.expire()

    def acquire_mission(self):
        with self.lock:
            self.expire()
            if self.closed or self.mission:
                raise RuntimeError("Video owner unavailable")
            self._open()
            if self.stream is None or self.stream.diagnostics()["state"] in {"FAILED", "CLOSED", "RECONNECTING"}:
                self._release_unused()
                raise RuntimeError("Video transport unavailable; no automatic reconnect")
            self.mission = True
            return self.stream

    def release_mission(self):
        with self.lock:
            self.mission = False
            self.expire()

    def _result(self, state, *, snapshot=None, content_type=None, image=None, message=None):
        age = None if snapshot is None else (self.clock() - snapshot.received_s) * 1000
        if age is not None and (not math.isfinite(age) or age < 0):
            age = None
        if state == "streaming" and (age is None or age > MAX_AGE_S * 1000):
            state, content_type, image = "waiting", None, None
        return {"state": state, "simulated": self.simulated,
                "frame_id": None if snapshot is None else f"{self.epoch}:{snapshot.generation}:{snapshot.frame_id}",
                "age_ms": age, "content_type": content_type, "image_base64": image,
                "message": (MOCK_MESSAGE + (" " + message if message else "")) if self.simulated else message}

    def _current(self):
        if self.stream is None or self.open_failed:
            return self._result("unavailable", message="Camera source unavailable.")
        if self.stream.diagnostics()["state"] in {"FAILED", "CLOSED", "RECONNECTING"}:
            return self._result("unavailable", message="Camera transport unavailable; stop before a new explicit start.")
        snapshot = self.stream.snapshot()
        if self.encoding_failed:
            return self._result("unavailable", snapshot=snapshot, message="Camera frame encoding unavailable.")
        if self.cached is not None and snapshot is not None:
            cached, content_type, image = self.cached
            age = self.clock() - cached.received_s
            if cached.generation == snapshot.generation and 0 <= age <= MAX_AGE_S:
                return self._result("streaming", snapshot=cached, content_type=content_type, image=image)
        return self._result("waiting", snapshot=snapshot, message="Waiting for a fresh decoded camera frame.")

    def camera(self, action, caller):
        with self.lock:
            self.expire()
            if action == "stop":
                self.viewers.pop(caller, None)
                self._release_unused()
                return self._result("stopped")
            if self.closed:
                return self._result("unavailable", message="Camera service is shutting down.")
            if action == "start":
                if caller not in self.viewers and len(self.viewers) >= self.max_viewers:
                    return self._result("unavailable", message="Camera viewer limit reached.")
                self.viewers[caller] = self.clock() + self.lease_seconds
                self._open()
                if self.watcher is None:
                    self.watcher = threading.Thread(target=self._watch, name="drone-preview-leases", daemon=True)
                    self.watcher.start()
            elif caller not in self.viewers:
                return self._result("stopped", message="Preview is not active; start explicitly.")
            else:
                self.viewers[caller] = self.clock() + self.lease_seconds
            stream = self.stream
            snapshot = None if stream is None else stream.snapshot()
            now = self.clock()
            if (snapshot is None or stream.diagnostics()["state"] in {"FAILED", "CLOSED", "RECONNECTING"}
                    or not 0 <= now - snapshot.received_s <= MAX_AGE_S
                    or now - self.last_encode_at < ENCODE_INTERVAL_S
                    or (self.cached is not None and self.cached[0].key == snapshot.key)
                    or not self.encoding.acquire(blocking=False)):
                return self._current()
            self.last_encode_at = now
        try:
            try:
                content_type, data = self.encoder(snapshot.frame)
                if content_type not in {"image/jpeg", "image/png"} or len(data) > MAX_BYTES:
                    raise PreviewUnavailable("Camera frame exceeds preview limits")
                image = base64.b64encode(data).decode("ascii")
            except PreviewUnavailable:
                with self.lock:
                    if self.stream is stream:
                        self.encoding_failed = True
            else:
                with self.lock:
                    if self.stream is stream:
                        self.cached = snapshot, content_type, image
                        self.encoding_failed = False
        finally:
            self.encoding.release()
        with self.lock:
            self.expire()
            if caller not in self.viewers:
                return self._result("stopped")
            return self._current()

    def close(self):
        with self.lock:
            self.closed = True
            self.stopping.set()
            self.viewers.clear()
            # A timed-out mission shutdown must keep its camera until it exits.
            self._release_unused()
        if self.watcher is not None and self.watcher is not threading.current_thread():
            self.watcher.join(timeout=1)
