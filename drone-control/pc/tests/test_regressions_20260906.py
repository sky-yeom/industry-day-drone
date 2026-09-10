from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from drone_nav.observation import FrameRecorder
from drone_nav.patrol import OrderedTagGate, TagGateAction, ExpectedTagTracker
from drone_nav.protocol import NDJSONClient, Telemetry, assess_motion
from drone_nav.vision import TcpVideoStream, VideoSnapshot
from test_patrol import detection


class CommandEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.client = NDJSONClient("unused.invalid", 1)
        self.telemetry = Telemetry(armed=True, vs_authority="MSDK",
            sdk_roll_pitch_mode="ANGLE", active_command_sequence=1,
            active_command_age_s=.01, setpoint_right_tilt_deg=-1.5,
            velocity_north_mps=0, velocity_east_mps=0, velocity_down_mps=0,
            velocity_age_s=.1, yaw_deg=0, attitude_age_s=.1)

    def tick(self, t, telemetry=None):
        with patch("drone_nav.protocol.time.monotonic_ns", return_value=int(t*1e9)):
            return self.client._update_motion_streak(telemetry or self.telemetry)

    def test_angle_refresh_sequences_accumulate_age(self):
        for i in range(20):
            age, sequence = self.tick(i*.1, replace(self.telemetry, active_command_sequence=i+1))
        self.assertAlmostEqual(age, 1.9)
        self.assertEqual(sequence, 1)
        result = assess_motion(self.telemetry, command_age_s=age)
        self.assertEqual(result.status, "SDK_SUBMITTED_NO_MOTION")
        self.assertIsNone(result.evidence["setpoint_right_mps"])

    def test_gap_zero_mode_reversal_and_authority_reset(self):
        self.tick(0)
        self.tick(.1)
        self.assertEqual(self.tick(1)[0], 0)
        self.assertEqual(self.tick(1.1, replace(self.telemetry, setpoint_right_tilt_deg=1.5))[0], 0)
        self.tick(1.2, replace(self.telemetry, setpoint_right_tilt_deg=0))
        self.assertEqual(self.tick(1.3)[0], 0)
        self.tick(1.4, replace(self.telemetry, vs_authority="RC"))
        self.assertEqual(self.tick(1.5)[0], 0)
        self.assertEqual(self.tick(1.6, replace(self.telemetry, sdk_roll_pitch_mode="VELOCITY", setpoint_right_mps=-.3))[0], 0)

    def test_old_error_cannot_be_attributed_to_new_command(self):
        telemetry = replace(self.telemetry, sdk_result="FAILED", sdk_result_sequence=0, sdk_result_age_s=.1)
        self.assertNotEqual(assess_motion(telemetry).status, "SDK_COMMAND_FAILED")
        telemetry = replace(telemetry, sdk_result_sequence=1)
        self.assertEqual(assess_motion(telemetry).status, "SDK_COMMAND_FAILED")

    def test_angle_vertical_success_is_not_horizontal_success(self):
        telemetry = replace(self.telemetry, setpoint_up_mps=.3, velocity_down_mps=-.3)
        self.assertEqual(assess_motion(telemetry, command_age_s=1).status, "PARTIAL_MOTION")


class FrameGateTests(unittest.TestCase):
    def test_both_ids_allowed_but_duplicate_frame_cannot_confirm(self):
        gate = OrderedTagGate(1, 3, .3)
        self.assertEqual(gate.update({1,3}, 0, frame_key=(1,1)), TagGateAction.HOLD_EXPECTED_ONLY)
        self.assertEqual(gate.update({1,3}, 1, frame_key=(1,1)), TagGateAction.HOLD_EXPECTED_ONLY)
        self.assertEqual(gate.update({1,3}, 1.1, frame_key=(1,2)), TagGateAction.CENTER_EXPECTED)

    def test_reconnect_resets_hold(self):
        gate = OrderedTagGate(3, 1, .3)
        gate.update({1,3}, 0, frame_key=(1,1))
        self.assertEqual(gate.update({1,3}, 1, frame_key=(2,2)), TagGateAction.HOLD_EXPECTED_ONLY)
        self.assertEqual(gate.update({1,3}, 1.31, frame_key=(2,3)), TagGateAction.CENTER_EXPECTED)

    def test_floor_tracker_requires_distinct_frames(self):
        tracker = ExpectedTagTracker(0, .3, None, None)
        tags = [detection(0, 900)]
        self.assertIsNone(tracker.update(tags, 0, frame_key=(1,1)))
        self.assertIsNone(tracker.update(tags, 1, frame_key=(1,1)))
        self.assertIsNone(tracker.update(tags, 1.1, frame_key=(2,2)))
        self.assertIsNotNone(tracker.update(tags, 1.5, frame_key=(2,3)))


class VideoRecoveryTests(unittest.TestCase):
    def test_detection_cached_per_frame_and_invalidated_on_stale_or_generation(self):
        with patch.dict("sys.modules", {"av": Mock()}), patch("drone_nav.vision.threading.Thread"):
            stream = TcpVideoStream("unused.invalid", 1)
        detector = Mock()
        detector.detect.return_value = [detection(0, 900)]
        stream._snapshot = VideoSnapshot(1, 1, time.monotonic(), object())
        self.assertEqual(len(stream.detect_latest(detector, 1)[0]), 1)
        stream.detect_latest(detector, 1)
        self.assertEqual(detector.detect.call_count, 1)
        stream._snapshot = VideoSnapshot(2, 1, time.monotonic(), object())
        stream.detect_latest(detector, 1)
        self.assertEqual(detector.detect.call_count, 2)
        stream._snapshot = replace(stream._snapshot, received_s=time.monotonic()-2)
        self.assertEqual(stream.detect_latest(detector, 1)[0], [])

    def test_eof_reconnects_with_new_decoder_and_frame(self):
        held = threading.Event()
        first = Mock()
        first.recv.return_value = b""
        second = Mock()
        chunks = iter([b"encoded"])
        def recv(_):
            try:
                return next(chunks)
            except StopIteration:
                held.wait(.02)
                raise TimeoutError()
        second.recv.side_effect = recv
        pixels = SimpleNamespace(flags=SimpleNamespace(writeable=True))
        frame = Mock()
        frame.to_ndarray.return_value = pixels
        codec = Mock()
        codec.parse.return_value = [b"packet"]
        codec.decode.return_value = [frame]
        av = Mock()
        av.codec.context.CodecContext.create.return_value = codec
        with patch.dict("sys.modules", {"av": av}), patch("drone_nav.vision.socket.create_connection", side_effect=[first,second]):
            stream = TcpVideoStream("unused.invalid", 1)
            try:
                deadline = time.monotonic()+3
                while stream.snapshot() is None and time.monotonic() < deadline:
                    held.wait(.01)
                snapshot = stream.snapshot()
                self.assertIsNotNone(snapshot)
                self.assertEqual(snapshot.generation, 2)
                self.assertFalse(pixels.flags.writeable)
                self.assertEqual(av.codec.context.CodecContext.create.call_count, 2)
                self.assertGreaterEqual(stream.diagnostics()["reconnects"], 1)
            finally:
                stream.close()
            self.assertFalse(stream._thread.is_alive())

    def test_observer_shares_snapshot_and_slow_writer_does_not_block_producer(self):
        entered = threading.Event()
        release = threading.Event()
        capture = Mock()
        capture.snapshot.return_value = VideoSnapshot(1, 1, time.monotonic(), object())
        capture.diagnostics.return_value = {"state": "STREAMING"}
        def write(*_):
            entered.set()
            release.wait(2)
            return True
        with tempfile.TemporaryDirectory() as directory:
            recorder = FrameRecorder(Path(directory), capture, interval_s=.01, max_frames=2, writer=write)
            try:
                self.assertTrue(entered.wait(1))
                before = time.monotonic()
                recorder.update_context({"phase":"outbound"})
                self.assertLess(time.monotonic()-before, .2)
                release.set()
            finally:
                release.set()
                recorder.close()
            row = json.loads((Path(directory)/"observations.jsonl").read_text().splitlines()[0])
            self.assertEqual(row["status"], "FRAME_SAVED")
            self.assertEqual(row["frame_key"], [1,1])
            self.assertEqual(recorder.saved_frames, 1)
