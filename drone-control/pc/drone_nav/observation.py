"""Read-only, bounded 1 Hz observation sink; never owns a control/video socket.

Uses the decoder's immutable latest snapshot. Slow disk/image work drops
intermediate frames instead of blocking flight control or building a backlog.
Context and image have separate timestamps/keys: temporal proximity is not
proof that an SDK ACK caused the motion visible in an image.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
import threading
import time


@dataclass(frozen=True)
class RangeObservation:
    raw: object
    range_status: str
    range_mm: float | None

    @property
    def range_m(self):
        return None if self.range_mm is None else self.range_mm / 1000.0


def classify_range_mm(raw):
    """Analysis-domain classification, never proof that unknown space is clear."""
    if raw is None:
        status = "MISSING"
    elif type(raw) not in (int, float):
        status = "INVALID_TYPE"
    elif not math.isfinite(raw):
        status = "NONFINITE"
    elif raw <= 0:
        status = "NONPOSITIVE"
    elif raw == 60000:
        status = "UNRESOLVED_RANGE_CODE"
    elif raw > 60000:
        status = "OUTSIDE_ANALYSIS_DOMAIN"
    else:
        return RangeObservation(raw, "FINITE_RANGE", float(raw))
    return RangeObservation(raw, status, None)


@dataclass(frozen=True)
class SectorObservation:
    direction: str
    range_mm: float | None
    range_status: str
    valid_count: int
    selected_count: int
    unknown_count: int
    callback_age_s: float | None
    callback_recency: str
    source_liveness: str
    source_generation: int | None
    callback_sequence: int | None
    geometry_status: str
    coverage_status: str
    raw_values: tuple
    direction_mapping: str = "body_front0_right90_assumed_not_physically_calibrated"
    legacy_callback_age_s: float | None = None
    callback_age_consistent: bool | None = None

    @property
    def range_m(self):
        return None if self.range_mm is None else self.range_mm / 1000.0


def observe_sector(telemetry, direction, half_width_deg=5):
    """Describe OA callback evidence without applying any flight-stop policy."""
    centers = {"front": 0, "forward": 0, "right": 90, "back": 180, "rear": 180, "left": 270, "all": None}
    if direction not in centers or type(half_width_deg) not in (int, float) or not math.isfinite(half_width_deg) or not 0 <= half_width_deg <= 180:
        raise ValueError("valid direction and finite sector width required")
    diagnostics = getattr(telemetry, "oa_diagnostics", None) or {}
    age = getattr(telemetry, "oa_obstacle_data_age_s", None)
    legacy_age = age if type(age) in (int, float) and math.isfinite(age) and age >= 0 else None
    if "last_callback_age_ms" in diagnostics:
        ms = diagnostics["last_callback_age_ms"]
        age = ms / 1000 if type(ms) in (int, float) and math.isfinite(ms) and ms >= 0 else None
    if type(age) not in (int, float) or not math.isfinite(age) or age < 0:
        age = None
    recency = "UNKNOWN_AGE" if age is None else "RECENT_CHANGE" if age <= 1 else "NO_RECENT_CALLBACK"
    consistent = (math.isclose(age, legacy_age, abs_tol=.001)
        if "last_callback_age_ms" in diagnostics and age is not None and legacy_age is not None else None)
    generation = diagnostics.get("generation")
    sequence = diagnostics.get("callback_sequence")
    generation = generation if type(generation) is int and generation >= 0 else None
    sequence = sequence if type(sequence) is int and sequence >= 0 else None
    values = getattr(telemetry, "oa_horizontal_distances_mm", None)
    geometry, status, selected = "UNKNOWN_GEOMETRY", "MISSING_ARRAY", ()
    if values is None and any("oa_horizontal_distances_mm" in issue for issue in getattr(telemetry, "parse_issues", ())):
        status = "MALFORMED_ARRAY"
    if values is not None:
        if not values:
            status = "EMPTY_ARRAY"
        else:
            interval = getattr(telemetry, "oa_horizontal_angle_interval_deg", None)
            geometry = "INFERRED_INTERVAL" if interval is None else "REPORTED_INTERVAL"
            interval = 360.0 / len(values) if interval is None else interval
            if (type(interval) not in (int, float) or not math.isfinite(interval) or interval <= 0
                    or not math.isclose(interval * len(values), 360, rel_tol=1e-6)):
                geometry, status = "INVALID_GEOMETRY", "INVALID_GEOMETRY"
            else:
                center = centers[direction]
                selected = tuple(value for index, value in enumerate(values) if center is None or
                    abs((index * interval - center + 180) % 360 - 180) <= half_width_deg + 1e-8)
                status = "NO_SELECTED_SAMPLES" if not selected else "UNKNOWN_RANGE"
    classified = [classify_range_mm(value) for value in selected]
    finite = [value.range_mm for value in classified if value.range_mm is not None]
    unknown = len(selected) - len(finite)
    coverage = "PARTIAL_COVERAGE" if finite and unknown else "COMPLETE_COVERAGE" if finite else "NO_VALID_RANGE"
    return SectorObservation(direction, min(finite) if finite else None, "FINITE_RANGE" if finite else status,
        len(finite), len(selected), unknown, age, recency, "UNCONFIRMED", generation, sequence,
        geometry, coverage, selected, legacy_callback_age_s=legacy_age, callback_age_consistent=consistent)


def write_image(path, frame):
    """Encode unmodified pixels, then use Unicode-safe Python filesystem I/O."""
    import cv2
    path = Path(path)
    ok, encoded = cv2.imencode(path.suffix.lower(), frame)
    if not ok or encoded is None or not encoded.size:
        raise OSError("image encoder rejected frame")
    data = encoded.tobytes()
    if path.write_bytes(data) != len(data):
        raise OSError("image file write was incomplete")
    return True


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
                        writer = write_image
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
