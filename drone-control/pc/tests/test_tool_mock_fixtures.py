"""Explicit capture mock must work without installing PC vision dependencies."""
import base64
import builtins
import hashlib
import io
from pathlib import Path
import threading
import struct
import zlib
import unittest
from unittest.mock import patch

from drone_nav.tool_control.service import CaptureMockAdapter
from drone_nav.tool_control.fixtures import SIGNATURE, _chunk, _decode, _row, fixture_frames, fixture_preview


class CaptureFixtureTests(unittest.TestCase):
    def test_rgb_and_rgba_variants_preserve_pixels_below_banner_and_resize_preview(self):
        for channels, color in ((3, 2), (4, 6)):
            with self.subTest(channels=channels):
                width, height = 96, 64
                pixel = bytes((30, 120, 70, 255))[:channels]
                raw = (b"\0" + pixel * width) * height
                original = (SIGNATURE + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0))
                            + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b""))
                with patch.object(Path, "open", side_effect=lambda *_a, **_k: io.BytesIO(original)):
                    first, second = fixture_frames("fixture.png")
                    preview = fixture_preview("fixture.png", max_edge=48, max_bytes=512 * 1024)
                self.assertEqual(first, original)
                self.assertNotEqual(first, second)
                _, _, _, _, changed = _decode(second)
                stride = width * channels
                previous = bytearray(stride)
                for y in range(height):
                    row = _row(changed, y, width, channels, previous)
                    previous = row
                    if y >= 32:
                        self.assertEqual(row, pixel * width)
                _, w, h, c, data = _decode(preview)
                self.assertEqual((w, h, c), (48, 32, channels))
                self.assertEqual(data, (b"\0" + pixel * 48) * 32)

    def test_mock_capture_has_no_vision_dependency_and_retains_canonical_identity(self):
        real_import = builtins.__import__
        def without_vision(name, *args, **kwargs):
            if name.split(".")[0] in {"cv2", "numpy", "av", "pupil_apriltags"}:
                raise AssertionError("Mock must not import PC vision dependencies")
            return real_import(name, *args, **kwargs)
        mission = {"mission_id": "mock-transport-test", "visits": [
            {"visit_index": index, "destination_id": f"tag-{tag}"}
            for index, tag in enumerate((3, 1, 2))]}
        events = []
        with patch("builtins.__import__", side_effect=without_vision), \
                patch("socket.create_connection", side_effect=AssertionError("Mock cannot connect")):
            result = CaptureMockAdapter().run(mission, threading.Event(), lambda **event: events.append(event))
        self.assertTrue(result["simulated"])
        self.assertEqual(result["state"], "completed")
        captures = [event["capture"] for event in events if "capture" in event]
        self.assertEqual(len(captures), 6)
        root = Path(__file__).resolve().parents[3]
        for index, tag in enumerate((3, 1, 2)):
            first, second = captures[2 * index:2 * index + 2]
            original = (root / "public" / "monitors" / f"monitor-{tag}.png").read_bytes()
            self.assertEqual(base64.b64decode(first["image_base64"]), original)
            self.assertNotEqual(first["image_base64"], second["image_base64"])
            self.assertNotEqual(first["capture_id"], second["capture_id"])
            for capture in (first, second):
                self.assertEqual(capture["fixture_sha256"], hashlib.sha256(original).hexdigest())
                self.assertTrue(capture["simulated"])
                self.assertEqual(capture["capture_source"], "synthetic_fixture")
                self.assertEqual(capture["visit_index"], index)

    def test_fixture_crc_failure_is_not_a_capture(self):
        import io
        root = Path(__file__).resolve().parents[3]
        data = bytearray((root / "public" / "monitors" / "monitor-1.png").read_bytes())
        data[20] ^= 1
        with patch.object(Path, "open", return_value=io.BytesIO(data)), self.assertRaises(RuntimeError):
            fixture_frames("not-opened.png")


if __name__ == "__main__":
    unittest.main()
