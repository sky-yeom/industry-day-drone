"""Read-only AprilTag camera inspection, independent of Speech and flight control.

Default invocation prints an offline plan. Only --execute opens a video source;
even then this module never opens the command/SDK sockets or moves the gimbal.
Frame age is time since PC decoding, not age since aircraft exposure.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc"))

from drone_nav.config import load_config
from drone_nav.vision import AprilTagDetector, TcpVideoStream, VideoSnapshot


def tag_role(tag_id: int) -> str:
    return {0: "floor_home", 6: "wall_home", 1: "wall_checkpoint",
            2: "wall_checkpoint", 3: "wall_checkpoint"}.get(tag_id, "unassigned")


def finite(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class InspectionDetector:
    """Keep raw IDs/quality, including IDs the navigation map does not know.

    AprilTagDetector owns the calibrated camera and pupil backend. Its normal
    detect() intentionally filters unmapped sizes, so a no-pose pass records
    every visible tag too. Pixel coordinates use that same undistorted image
    only when calibration is declared; otherwise they use the original frame.
    No world/body pose is calculated from unconfirmed room measurements.
    """

    def __init__(self, config, detector_factory=AprilTagDetector):
        self.config = config
        self.base = detector_factory(config)

    def detect(self, frame):
        base = self.base
        calibrated = self.config.camera.calibrated
        pixels = (base._cv2.undistort(frame, base._matrix, base._distortion)
                  if calibrated else frame)
        gray = (base._cv2.cvtColor(pixels, base._cv2.COLOR_BGR2GRAY)
                if getattr(pixels, "ndim", 0) == 3 else pixels)
        raw = base._detector.detect(gray, estimate_tag_pose=False)
        poses = base.detect(frame) if calibrated else []
        result = []
        for item in raw:
            tag_id = int(item.tag_id)
            configured = tag_id in self.config.tag_map
            center = [finite(value) for value in item.center]
            candidates = [pose for pose in poses if pose.tag_id == tag_id]
            pose = min(candidates, key=lambda candidate: sum(
                (float(candidate.center_px[index]) - float(item.center[index])) ** 2
                for index in range(2)) if candidate.center_px else math.inf,
                default=None)
            transform = None
            if pose is not None:
                transform = [[finite(value) for value in row] for row in pose.T_C_T]
                if any(value is None for row in transform for value in row):
                    transform = None
            if not calibrated:
                pose_status = "camera_uncalibrated"
            elif not configured:
                pose_status = "tag_size_unknown"
            elif transform is None:
                pose_status = "unavailable"
            else:
                pose_status = "calibrated_camera_relative"
            result.append({
                "tag_id": tag_id, "role": tag_role(tag_id),
                "configured": configured,
                "center_px": center,
                "corners_px": [[finite(value) for value in corner]
                               for corner in item.corners],
                "pixel_space": "undistorted" if calibrated else "original",
                "decision_margin": finite(item.decision_margin),
                "hamming": int(item.hamming),
                "pose_status": pose_status,
                "pose_error": finite(pose.pose_error) if pose else None,
                "camera_to_tag_transform": transform,
            })
        return result


class FileVideoStream:
    """Offline replay of a local video, one decoded frame per inspection tick.

    This is frame-by-frame playback, not a real-time timing reproduction.
    Network URLs are not accepted by the CLI.
    """

    def __init__(self, path):
        import av
        self._container = av.open(str(path))
        try:
            self._frames = self._container.decode(video=0)
        except BaseException:
            self._container.close()
            raise
        self._frame_id = 0
        self._eof = False

    def snapshot(self):
        if self._eof:
            return None
        try:
            frame = next(self._frames)
        except StopIteration:
            self._eof = True
            return None
        self._frame_id += 1
        return VideoSnapshot(1, self._frame_id, time.monotonic(),
                             frame.to_ndarray(format="bgr24"))

    def diagnostics(self):
        return {"state": "EOF" if self._eof else "REPLAY",
                "decoded_frames": self._frame_id, "timing": "one_frame_per_tick"}

    def close(self):
        self._container.close()


def inspection_plan(config, *, host=None, source_file=None, duration=30.0):
    return {
        "mode": "camera_only", "flight_commands": False,
        "source": ({"type": "local_file", "path": str(source_file)} if source_file
                   else {"type": "tcp_video", "host": host or config.network.host,
                         "port": config.network.video_port,
                         "codec": config.network.video_codec}),
        "duration_s": duration,
        "camera_calibrated": config.camera.calibrated,
        "configured_tag_ids": sorted(config.tag_map),
        "roles": {str(tag_id): tag_role(tag_id) for tag_id in (0, 1, 2, 3, 6)},
        "frame_age_basis": "pc_decode_time_not_aircraft_exposure",
        "world_pose_computed": False,
    }


def run_inspection(config, output, *, host=None, source_file=None, duration=30.0,
                   max_frame_age=0.5, poll_hz=10.0,
                   detector_factory=InspectionDetector, stream_factory=None,
                   clock=time.monotonic, sleep=time.sleep, emit=print):
    """Execute video inspection with injectable sources for hardware-free tests."""
    for name, value in (("duration", duration), ("max_frame_age", max_frame_age),
                        ("poll_hz", poll_hz)):
        if finite(value) is None or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    plan = inspection_plan(config, host=host, source_file=source_file, duration=duration)
    summary = {**plan, "status": "STARTING", "frames_inspected": 0,
               "fresh_frames_inspected": 0, "stale_detection_frames": 0,
               "stale_samples": 0, "invalid_frame_timestamps": 0,
               "detections_by_id": {}, "all_detections_by_id": {},
               "detections_by_id_basis": "fresh_frames_only", "error": None}
    started = clock()
    stream = None
    last_key = None
    last_notice = None
    with (output / "observations.jsonl").open("w", encoding="utf-8") as log:
        def record(kind, **payload):
            log.write(json.dumps({"event": kind, "elapsed_s": clock() - started,
                                  **payload}, ensure_ascii=False, allow_nan=False) + "\n")
            log.flush()

        record("camera_inspection_started", **plan)
        try:
            if not source_file and config.network.video_port in {
                    config.network.port, 9997, 9998}:
                raise ValueError("video_port must not point at a command or SDK query port")
            detector = detector_factory(config)
            if stream_factory is not None:
                stream = stream_factory()
            elif source_file:
                stream = FileVideoStream(source_file)
            else:
                stream = TcpVideoStream(host or config.network.host,
                                        config.network.video_port,
                                        config.network.video_codec)
            while clock() - started < duration:
                snapshot = stream.snapshot()
                diagnostics = stream.diagnostics()
                age = None if snapshot is None else clock() - snapshot.received_s
                invalid_time = snapshot is not None and (finite(age) is None or age < 0)
                if snapshot is None or invalid_time or age > max_frame_age:
                    state = ("NO_FRAME" if snapshot is None else "INVALID_FRAME_TIME"
                             if invalid_time else "STALE_FRAME")
                    summary["stale_samples"] += int(snapshot is not None)
                    summary["invalid_frame_timestamps"] += int(invalid_time)
                    record("camera_sample", state=state, frame_age_s=finite(age),
                           video=diagnostics, detections=[])
                    if state != last_notice:
                        emit(f"Camera: {state}; {diagnostics.get('state')}")
                    last_notice = state
                elif snapshot.key != last_key:
                    detections = detector.detect(snapshot.frame)
                    age_after = clock() - snapshot.received_s
                    invalid_after = finite(age_after) is None or age_after < 0
                    detections_fresh = not invalid_after and age_after <= max_frame_age
                    state = ("INVALID_FRAME_TIME" if invalid_after else "FRESH_FRAME"
                             if detections_fresh else "STALE_AFTER_DETECTION")
                    shape = getattr(snapshot.frame, "shape", ())
                    record("camera_frame", state=state, detections_fresh=detections_fresh,
                           generation=snapshot.generation, frame_id=snapshot.frame_id,
                           frame_age_s=finite(age),
                           frame_age_after_detection_s=finite(age_after),
                           dimensions={"width": int(shape[1]), "height": int(shape[0])}
                           if len(shape) >= 2 else None,
                           video=diagnostics, detections=detections)
                    summary["frames_inspected"] += 1
                    summary["fresh_frames_inspected"] += int(detections_fresh)
                    summary["stale_detection_frames"] += int(not detections_fresh)
                    summary["invalid_frame_timestamps"] += int(invalid_after)
                    ids = set()
                    for detection in detections:
                        key = str(detection["tag_id"])
                        all_counts = summary["all_detections_by_id"]
                        all_counts[key] = all_counts.get(key, 0) + 1
                        if detections_fresh:
                            counts = summary["detections_by_id"]
                            counts[key] = counts.get(key, 0) + 1
                        ids.add(detection["tag_id"])
                    notice = (state, tuple(sorted(ids)))
                    if notice != last_notice:
                        emit(f"Camera tags [{state}]: " + (", ".join(
                            f"{tag_id} ({tag_role(tag_id)})" for tag_id in sorted(ids)) or "none"))
                    last_key, last_notice = snapshot.key, notice
                if diagnostics.get("state") == "EOF":
                    break
                sleep(1.0 / poll_hz)
            summary["status"] = ("COMPLETED" if summary["fresh_frames_inspected"]
                                 else "NO_FRESH_FRAMES" if summary["frames_inspected"]
                                 else "NO_FRAMES")
        except KeyboardInterrupt:
            summary["status"] = "INTERRUPTED"
        except Exception as exc:
            summary["status"] = "ERROR"
            summary["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception as exc:
                    summary["status"] = "ERROR"
                    summary["cleanup_error"] = f"{type(exc).__name__}: {exc}"
            summary["elapsed_s"] = clock() - started
            record("camera_inspection_finished", **summary)
            (output / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8")
    emit(f"Camera inspection {summary['status']}: {output}")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--host", help="Override only the phone video host")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--output", type=Path, help="New directory for JSONL and summary")
    parser.add_argument("--source-file", type=Path, help="Replay a local saved video")
    parser.add_argument("--max-frame-age", type=float, default=0.5)
    parser.add_argument("--poll-hz", type=float, default=10.0)
    parser.add_argument("--execute", action="store_true", help="Open video; never flight control")
    args = parser.parse_args(argv)
    for name in ("duration", "max_frame_age", "poll_hz"):
        value = getattr(args, name)
        if finite(value) is None or value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be finite and positive")
    if args.host and args.source_file:
        parser.error("--host and --source-file are mutually exclusive")
    if args.source_file and not args.source_file.is_file():
        parser.error("--source-file must be an existing local file")
    config = load_config(args.config)
    plan = inspection_plan(config, host=args.host, source_file=args.source_file,
                           duration=args.duration)
    if not args.execute:
        print(json.dumps({"execution": "OFFLINE_PLAN", **plan}, ensure_ascii=False, indent=2))
        return 0
    output = args.output or ROOT / "pc" / "logs" / "tag-camera" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    summary = run_inspection(config, output, host=args.host, source_file=args.source_file,
                             duration=args.duration, max_frame_age=args.max_frame_age,
                             poll_hz=args.poll_hz)
    return {"COMPLETED": 0, "INTERRUPTED": 130, "NO_FRAMES": 3,
            "NO_FRESH_FRAMES": 3}.get(summary["status"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
