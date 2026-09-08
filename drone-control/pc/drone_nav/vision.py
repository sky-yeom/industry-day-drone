"""Optional OpenCV/pupil_apriltags detector adapter."""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

from .config import AppConfig
from .localization import TagDetection


class VisionDependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoSnapshot:
    generation: int
    frame_id: int
    received_s: float
    frame: Any

    @property
    def key(self):
        return self.generation, self.frame_id


class TcpVideoStream:
    """One video socket owner, atomic snapshots, decoder reset on reconnect.

    Received time is PC decode time, not aircraft exposure time. No control
    reconnect, arming, flight command or sensor mutation occurs here.
    """

    def __init__(self, host: str, port: int, codec: str = "h264") -> None:
        try:
            import av
        except ImportError as exc:
            raise VisionDependencyError(
                "PyAV is required for DJI TCP video; install with "
                "`pip install -e .[vision]`"
            ) from exc
        self._av = av
        self._address = (host, port)
        self._codec_name = codec
        self._lock = threading.Lock()
        self._socket = None
        self._snapshot = None
        self._generation = 0
        self._frame_id = 0
        self._bytes = 0
        self._decoded = 0
        self._reconnects = 0
        self._error = None
        self._state = "CONNECTING"
        self._detection_key = None
        self._detections = []
        self.last_detection_snapshot = None
        self._closed = threading.Event()
        self._thread = threading.Thread(
            target=self._decode, name="dji-video-decoder", daemon=True
        )
        self._thread.start()

    def _decode(self) -> None:
        while not self._closed.is_set():
            connection = None
            try:
                connection = socket.create_connection(self._address, timeout=2.0)
                connection.settimeout(0.5)
                codec = self._av.codec.context.CodecContext.create(self._codec_name, "r")
                with self._lock:
                    self._socket = connection
                    self._generation += 1
                    generation = self._generation
                    self._snapshot = None
                    self._state = "WAIT_KEYFRAME"
                last_frame_s = time.monotonic()
                while not self._closed.is_set():
                    if time.monotonic() - last_frame_s > 5.0:
                        raise TimeoutError("no decoded video frame for 5 seconds")
                    try:
                        data = connection.recv(1 << 20)
                    except TimeoutError:
                        continue
                    if not data:
                        raise ConnectionError("Android video stream closed")
                    with self._lock:
                        self._bytes += len(data)
                    for packet in codec.parse(data):
                        for frame in codec.decode(packet):
                            pixels = frame.to_ndarray(format="bgr24")
                            pixels.flags.writeable = False
                            last_frame_s = time.monotonic()
                            with self._lock:
                                self._frame_id += 1
                                self._decoded += 1
                                self._snapshot = VideoSnapshot(generation, self._frame_id, last_frame_s, pixels)
                                self._state = "STREAMING"
                                self._error = None
            except Exception as exc:
                with self._lock:
                    self._error = repr(exc)
                    self._state = "RECONNECTING"
                    self._snapshot = None
                    self._reconnects += 1
            finally:
                if connection is not None:
                    connection.close()
                with self._lock:
                    self._socket = None
            self._closed.wait(0.5)
        with self._lock:
            self._state = "CLOSED"

    def snapshot(self) -> VideoSnapshot | None:
        with self._lock:
            return self._snapshot

    def diagnostics(self) -> dict:
        with self._lock:
            return dict(state=self._state, generation=self._generation,
                        decoded_frames=self._decoded, received_bytes=self._bytes,
                        reconnects=self._reconnects, error=self._error)

    def detect_latest(self, detector, max_age_s):
        snapshot = self.snapshot()
        self.last_detection_snapshot = snapshot
        age = float("inf") if snapshot is None else time.monotonic() - snapshot.received_s
        if snapshot is None or age > max_age_s:
            self._detection_key = None
            self._detections = []
            return [], age
        if snapshot.key != self._detection_key:
            self._detections = detector.detect(snapshot.frame)
            self._detection_key = snapshot.key
        return self._detections, age

    def read(self) -> tuple[bool, Any | None, float]:
        """Latest decoded frame plus its age in seconds.

        A connected-but-stalled stream keeps returning the same frame; the
        age lets callers refuse to localize from stale imagery.
        """
        snapshot = self.snapshot()
        if snapshot is None:
            return False, None, float("inf")
        return True, snapshot.frame, time.monotonic() - snapshot.received_s

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            connection = self._socket
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self._thread.join(timeout=3.0)


class AprilTagDetector:
    def __init__(self, config: AppConfig) -> None:
        try:
            import cv2
            import numpy as np
            from pupil_apriltags import Detector
        except ImportError as exc:
            raise VisionDependencyError(
                "vision dependencies unavailable; install with "
                "`pip install -e .[vision]`"
            ) from exc
        self._cv2, self._np = cv2, np
        self._detector = Detector(families="tag36h11")
        self._camera = config.camera
        self._matrix = np.array(
            [
                [self._camera.fx, 0.0, self._camera.cx],
                [0.0, self._camera.fy, self._camera.cy],
                [0.0, 0.0, 1.0],
            ]
        )
        self._distortion = np.array(self._camera.distortion)
        self._sizes = {tag.id: tag.size_m for tag in config.tags}

    def detect(self, frame: Any) -> list[TagDetection]:
        frame = self._cv2.undistort(frame, self._matrix, self._distortion)
        gray = (
            self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2GRAY)
            if getattr(frame, "ndim", 0) == 3
            else frame
        )
        output: list[TagDetection] = []
        camera_params = (
            self._camera.fx,
            self._camera.fy,
            self._camera.cx,
            self._camera.cy,
        )
        for tag_size in sorted(set(self._sizes.values())):
            raw = self._detector.detect(
                gray,
                estimate_tag_pose=True,
                camera_params=camera_params,
                tag_size=tag_size,
            )
            for item in raw:
                if self._sizes.get(item.tag_id) != tag_size:
                    continue
                rotation = item.pose_R
                offset = item.pose_t
                transform = tuple(
                    tuple(float(rotation[i][j]) for j in range(3))
                    + (float(offset[i][0]),)
                    for i in range(3)
                ) + ((0.0, 0.0, 0.0, 1.0),)
                output.append(
                    TagDetection(
                        int(item.tag_id),
                        transform,
                        float(item.pose_err),
                        (float(item.center[0]), float(item.center[1])),
                    )
                )
        return output
