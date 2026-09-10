"""Camera-only runner tests use fake images/decoders and never contact a phone."""
from dataclasses import replace
import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from drone_nav.config import load_config
from drone_nav.localization import TagDetection
from drone_nav.transforms import identity
from drone_nav.vision import VideoSnapshot

TRIAL = Path(__file__).parents[2] / "trials" / "tag_camera_check.py"
SPEC = importlib.util.spec_from_file_location("standalone_camera_check_tests", TRIAL)
camera = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(camera)
CONFIG = Path(__file__).parents[1] / "config.sample.json"


class FakeClock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeStream:
    def __init__(self, snapshot, state="STREAMING"):
        self.frame = snapshot
        self.state = state
        self.closed = False

    def snapshot(self):
        return self.frame

    def diagnostics(self):
        return {"state": self.state, "decoded_frames": int(self.frame is not None)}

    def close(self):
        self.closed = True


class CameraInspectionTests(TestCase):
    def setUp(self):
        self.config = load_config(CONFIG)
        self.clock = FakeClock()
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "run"
        self.frame = SimpleNamespace(shape=(720, 1280, 3))

    def run_fake(self, stream, detector):
        return camera.run_inspection(
            self.config, self.output, duration=0.31,
            detector_factory=lambda config: detector,
            stream_factory=lambda: stream,
            clock=self.clock, sleep=self.clock.sleep, emit=lambda value: None)

    def events(self):
        return [json.loads(line) for line in
                (self.output / "observations.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_default_cli_is_offline_and_redacts_token(self):
        with patch.object(camera, "run_inspection") as execute, \
                patch.object(camera, "TcpVideoStream") as tcp, \
                patch("sys.stdout", new_callable=io.StringIO) as printed:
            self.assertEqual(camera.main(["--config", str(CONFIG), "--host", "192.0.2.8"]), 0)
        execute.assert_not_called()
        tcp.assert_not_called()
        plan = json.loads(printed.getvalue())
        self.assertEqual(plan["execution"], "OFFLINE_PLAN")
        self.assertEqual(plan["source"]["host"], "192.0.2.8")
        self.assertFalse(plan["flight_commands"])
        self.assertNotIn("confirmation_token", printed.getvalue())

    def test_fresh_snapshot_logged_once_and_decoder_closed(self):
        stream = FakeStream(VideoSnapshot(4, 7, 10.0, self.frame))
        detector = Mock()
        detector.detect.return_value = [{"tag_id": 0, "role": "floor_home"},
                                       {"tag_id": 6, "role": "wall_home"},
                                       {"tag_id": 99, "role": "unassigned"}]
        summary = self.run_fake(stream, detector)
        self.assertEqual(summary["status"], "COMPLETED")
        self.assertEqual(summary["frames_inspected"], 1)
        self.assertEqual(summary["detections_by_id"], {"0": 1, "6": 1, "99": 1})
        self.assertTrue(stream.closed)
        detector.detect.assert_called_once_with(self.frame)
        frames = [event for event in self.events() if event["event"] == "camera_frame"]
        self.assertEqual(frames[0]["dimensions"], {"width": 1280, "height": 720})
        self.assertEqual(frames[0]["generation"], 4)
        self.assertEqual(frames[0]["frame_age_s"], 0)

    def test_stale_frame_is_recorded_without_running_detection(self):
        stream = FakeStream(VideoSnapshot(1, 1, 8.0, self.frame))
        detector = Mock()
        summary = self.run_fake(stream, detector)
        detector.detect.assert_not_called()
        self.assertEqual(summary["status"], "NO_FRAMES")
        self.assertGreater(summary["stale_samples"], 0)
        self.assertTrue(stream.closed)
        sample = self.events()[1]
        self.assertEqual(sample["state"], "STALE_FRAME")
        self.assertEqual(sample["detections"], [])
        self.assertGreaterEqual(sample["frame_age_s"], 2.0)

    def test_no_frame_is_not_reported_as_success_or_infinite_age(self):
        stream = FakeStream(None, "RECONNECTING")
        summary = self.run_fake(stream, Mock())
        self.assertEqual(summary["status"], "NO_FRAMES")
        self.assertIsNone(self.events()[1]["frame_age_s"])
        self.assertTrue(stream.closed)

    def test_future_timestamp_is_invalid_without_running_detection(self):
        stream = FakeStream(VideoSnapshot(1, 1, 12.0, self.frame))
        detector = Mock()
        summary = self.run_fake(stream, detector)
        detector.detect.assert_not_called()
        self.assertEqual(summary["status"], "NO_FRAMES")
        self.assertGreater(summary["invalid_frame_timestamps"], 0)
        self.assertEqual(self.events()[1]["state"], "INVALID_FRAME_TIME")
        self.assertEqual(self.events()[1]["frame_age_s"], -2.0)

    def test_slow_detection_keeps_raw_data_but_not_fresh_counts(self):
        stream = FakeStream(VideoSnapshot(1, 1, 10.0, self.frame))
        detector = Mock()

        def detect(frame):
            self.clock.sleep(.7)
            return [{"tag_id": 6, "role": "wall_home"}]

        detector.detect.side_effect = detect
        summary = self.run_fake(stream, detector)
        self.assertEqual(summary["status"], "NO_FRESH_FRAMES")
        self.assertEqual(summary["frames_inspected"], 1)
        self.assertEqual(summary["fresh_frames_inspected"], 0)
        self.assertEqual(summary["stale_detection_frames"], 1)
        self.assertEqual(summary["detections_by_id"], {})
        self.assertEqual(summary["all_detections_by_id"], {"6": 1})
        event = self.events()[1]
        self.assertEqual(event["state"], "STALE_AFTER_DETECTION")
        self.assertFalse(event["detections_fresh"])
        self.assertEqual(event["detections"][0]["tag_id"], 6)
        self.assertGreater(event["frame_age_after_detection_s"], .5)

    def test_detection_error_closes_stream_and_preserves_error_log(self):
        stream = FakeStream(VideoSnapshot(1, 1, 10.0, self.frame))
        detector = Mock()
        detector.detect.side_effect = RuntimeError("bad decoded frame")
        summary = self.run_fake(stream, detector)
        self.assertEqual(summary["status"], "ERROR")
        self.assertIn("bad decoded frame", summary["error"])
        self.assertTrue(stream.closed)
        self.assertEqual(self.events()[-1]["event"], "camera_inspection_finished")

    def test_detector_startup_error_never_opens_video(self):
        factory = Mock(side_effect=ImportError("missing vision library"))
        stream_factory = Mock()
        summary = camera.run_inspection(
            self.config, self.output, duration=.2, detector_factory=factory,
            stream_factory=stream_factory, emit=lambda value: None)
        stream_factory.assert_not_called()
        self.assertEqual(summary["status"], "ERROR")
        self.assertIn("ImportError", summary["error"])

    def test_source_startup_error_is_recorded(self):
        stream_factory = Mock(side_effect=OSError("file decoder unavailable"))
        summary = camera.run_inspection(
            self.config, self.output, duration=.2, detector_factory=lambda _: Mock(),
            stream_factory=stream_factory, emit=lambda value: None)
        self.assertEqual(summary["status"], "ERROR")
        self.assertEqual(summary["frames_inspected"], 0)

    def test_misconfigured_video_cannot_open_the_control_port(self):
        self.config = replace(self.config, network=replace(
            self.config.network, video_port=self.config.network.port))
        with patch.object(camera, "TcpVideoStream") as tcp, \
                patch.object(camera, "InspectionDetector") as detector:
            summary = camera.run_inspection(
                self.config, self.output, duration=.2,
                detector_factory=detector, emit=lambda value: None)
        tcp.assert_not_called()
        detector.assert_not_called()
        self.assertEqual(summary["status"], "ERROR")
        self.assertIn("command or SDK query port", summary["error"])

    def test_interruption_closes_video(self):
        stream = FakeStream(VideoSnapshot(1, 1, 10.0, self.frame))
        detector = Mock()
        detector.detect.side_effect = KeyboardInterrupt()
        self.assertEqual(self.run_fake(stream, detector)["status"], "INTERRUPTED")
        self.assertTrue(stream.closed)

    def test_existing_log_directory_is_not_overwritten(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.run_fake(FakeStream(None), Mock())


class InspectionDetectorTests(TestCase):
    @staticmethod
    def raw(tag_id):
        return SimpleNamespace(tag_id=tag_id, center=(20.0, 30.0),
                               corners=((10., 20.), (30., 20.), (30., 40.), (10., 40.)),
                               decision_margin=78.5, hamming=0)

    def make_detector(self, calibrated):
        config = load_config(CONFIG)
        config = replace(config, camera=replace(config.camera, calibrated=calibrated))
        base = Mock()
        base._detector.detect.return_value = [self.raw(0), self.raw(6), self.raw(88)]
        base.detect.return_value = [TagDetection(0, identity(), 0.02, (20., 30.))]
        return camera.InspectionDetector(config, detector_factory=lambda _: base), base

    def test_unknown_ids_keep_quality_and_roles_without_fabricated_pose(self):
        detector, base = self.make_detector(True)
        observed = detector.detect(SimpleNamespace(ndim=2))
        self.assertEqual([tag["tag_id"] for tag in observed], [0, 6, 88])
        floor, home, unknown = observed
        self.assertEqual(floor["role"], "floor_home")
        self.assertEqual(floor["pose_status"], "calibrated_camera_relative")
        self.assertEqual(floor["pose_error"], .02)
        self.assertEqual(home["role"], "wall_home")
        self.assertEqual(home["pose_status"], "tag_size_unknown")
        self.assertIsNone(home["camera_to_tag_transform"])
        self.assertEqual(home["decision_margin"], 78.5)
        self.assertEqual(len(home["corners_px"]), 4)
        self.assertEqual(unknown["role"], "unassigned")
        self.assertFalse(unknown["configured"])
        self.assertFalse(base._detector.detect.call_args.kwargs["estimate_tag_pose"])

    def test_uncalibrated_camera_only_reports_raw_geometry(self):
        detector, base = self.make_detector(False)
        observed = detector.detect(SimpleNamespace(ndim=2))
        base._cv2.undistort.assert_not_called()
        base.detect.assert_not_called()
        self.assertTrue(all(tag["pose_status"] == "camera_uncalibrated" for tag in observed))
        self.assertTrue(all(tag["camera_to_tag_transform"] is None for tag in observed))
        self.assertTrue(all(tag["pixel_space"] == "original" for tag in observed))

    def test_invalid_pose_matrix_is_not_logged_as_a_real_pose(self):
        detector, base = self.make_detector(True)
        bad = tuple((math_row if index else (float("nan"), 0., 0., 0.))
                    for index, math_row in enumerate(identity()))
        base.detect.return_value = [TagDetection(0, bad, float("nan"), (20., 30.))]
        floor = detector.detect(SimpleNamespace(ndim=2))[0]
        self.assertEqual(floor["pose_status"], "unavailable")
        self.assertIsNone(floor["camera_to_tag_transform"])
        self.assertIsNone(floor["pose_error"])
