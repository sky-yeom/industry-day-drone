"""Video-only first-frame deadlines tested with fake codec/socket/clock."""
from types import SimpleNamespace
import threading
import time
from unittest import TestCase
from unittest.mock import Mock, patch

from drone_nav.vision import TcpVideoStream


class VideoInitialKeyframeTests(TestCase):
    def decode_scenario(self, *, first_frame_at=None, initial_timeout=None,
                        stop_after_first_frame=False):
        clock = SimpleNamespace(now=0.0)
        pixels = SimpleNamespace(flags=SimpleNamespace(writeable=True))
        frame = Mock()
        frame.to_ndarray.return_value = pixels
        codec = Mock()
        codec.parse.side_effect = lambda data: [data] if data == b"IDR" else []
        codec.decode.return_value = [frame]
        av = Mock()
        av.codec.context.CodecContext.create.return_value = codec
        with patch.dict("sys.modules", {"av": av}), patch("drone_nav.vision.threading.Thread"):
            kwargs = {} if initial_timeout is None else {"initial_keyframe_timeout_s": initial_timeout}
            stream = TcpVideoStream("unused.invalid", 9999, **kwargs)
        connection = Mock()
        saved_snapshots = []

        def recv(_):
            if stop_after_first_frame and stream.snapshot() is not None:
                saved_snapshots.append(stream.snapshot())
                stream._closed.set()
                raise TimeoutError()
            clock.now += 1.0
            return b"IDR" if clock.now == first_frame_at else b"P_FRAME"

        connection.recv.side_effect = recv
        # End this deterministic run after the reset closes its old socket.
        connection.close.side_effect = stream._closed.set
        with patch("drone_nav.vision.socket.create_connection", return_value=connection) as connect, \
                patch("drone_nav.vision.time.monotonic", side_effect=lambda: clock.now):
            stream._decode()
        return stream, clock, saved_snapshots, connection, connect

    def test_explicit_fifteen_seconds_accepts_first_idr_after_seven(self):
        stream, clock, snapshots, connection, connect = self.decode_scenario(
            first_frame_at=7.0, initial_timeout=15.0, stop_after_first_frame=True)
        self.assertEqual(clock.now, 7.0)
        self.assertEqual(stream.diagnostics()["decoded_frames"], 1)
        self.assertEqual(stream.diagnostics()["reconnects"], 0)
        self.assertEqual(snapshots[0].received_s, 7.0)
        self.assertEqual(snapshots[0].generation, 1)
        self.assertFalse(snapshots[0].frame.flags.writeable)
        connect.assert_called_once()
        connection.close.assert_called_once()

    def test_unchanged_default_rejects_first_idr_after_five(self):
        stream, clock, snapshots, _, _ = self.decode_scenario(first_frame_at=7.0)
        self.assertEqual(clock.now, 6.0)
        self.assertEqual(stream.diagnostics()["decoded_frames"], 0)
        self.assertEqual(stream.diagnostics()["reconnects"], 1)
        self.assertIn("initial decoded video frame for 5 seconds", stream.diagnostics()["error"])
        self.assertEqual(snapshots, [])
        self.assertIsNone(stream.snapshot())

    def test_later_stall_resets_after_five_even_with_extended_initial_wait(self):
        stream, clock, _, _, _ = self.decode_scenario(first_frame_at=7.0, initial_timeout=15.0)
        self.assertEqual(clock.now, 13.0)
        self.assertEqual(stream.diagnostics()["decoded_frames"], 1)
        self.assertEqual(stream.diagnostics()["reconnects"], 1)
        self.assertIn("no decoded video frame for 5 seconds", stream.diagnostics()["error"])
        self.assertIsNone(stream.snapshot())

    def test_extended_first_frame_timeout_still_has_a_bound(self):
        stream, clock, _, _, _ = self.decode_scenario(initial_timeout=15.0)
        self.assertEqual(clock.now, 16.0)
        self.assertEqual(stream.diagnostics()["decoded_frames"], 0)
        self.assertEqual(stream.diagnostics()["reconnects"], 1)
        self.assertIn("initial decoded video frame for 15 seconds", stream.diagnostics()["error"])

    def test_cancel_interrupts_initial_wait_without_waiting_fifteen_seconds(self):
        entered, released = threading.Event(), threading.Event()
        connection = Mock()

        def recv(_):
            entered.set()
            released.wait(2.0)
            raise OSError("socket shut down")

        connection.recv.side_effect = recv
        connection.shutdown.side_effect = lambda _: released.set()
        connection.close.side_effect = released.set
        av = Mock()
        with patch.dict("sys.modules", {"av": av}), \
                patch("drone_nav.vision.socket.create_connection", return_value=connection):
            stream = TcpVideoStream("unused.invalid", 9999, initial_keyframe_timeout_s=15.0)
            try:
                self.assertTrue(entered.wait(1.0))
                started = time.perf_counter()
                stream.close()
                self.assertLess(time.perf_counter() - started, .5)
                self.assertFalse(stream._thread.is_alive())
                self.assertEqual(stream.diagnostics()["state"], "CLOSED")
            finally:
                released.set()
                stream.close()

    def test_invalid_timeout_never_starts_a_thread(self):
        for value in (0, -1, float("nan"), float("inf"), True, "15"):
            with self.subTest(value=value), patch("drone_nav.vision.threading.Thread") as thread:
                with self.assertRaises(ValueError):
                    TcpVideoStream("unused.invalid", 9999, initial_keyframe_timeout_s=value)
                thread.assert_not_called()
