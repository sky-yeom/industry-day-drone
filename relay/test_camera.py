"""Fixture capture validation, using the same committed pixels as the browser."""

import base64
from pathlib import Path
import unittest
from unittest.mock import patch

from relay import config
from relay.camera import CaptureError, FixtureCamera, PUBLIC_ROOT, SCENARIO, validate_image


class CameraTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_actual_pixels_data_url_and_unique_id(self):
        camera = FixtureCamera()
        self.assertIsNone(camera.readiness())
        for person in SCENARIO["people"]:
            with self.subTest(monitor=person["monitorId"]):
                capture = await camera.capture(person["monitorId"])
                expected = (PUBLIC_ROOT / person["image"].lstrip("/")).read_bytes()
                self.assertEqual(capture.image_bytes, expected)
                self.assertEqual(capture.monitor_id, person["monitorId"])
                self.assertEqual(capture.content_type, "image/png")
                self.assertTrue(capture.image_url.startswith("data:image/png;base64,"))
                self.assertEqual(base64.b64decode(capture.image_url.split(",", 1)[1]), expected)
                next_capture = await camera.capture(person["monitorId"])
                self.assertNotEqual(capture.id, next_capture.id)

    async def test_unknown_paths_and_remote_urls_not_loaded(self):
        for monitor in ("monitor-4", "../monitor-1", "https://example.com/image.png", "/monitors/monitor-1.png"):
            with self.subTest(monitor=monitor), patch.object(Path, "open") as opened:
                with self.assertRaisesRegex(CaptureError, "등록되지 않은"):
                    await FixtureCamera().capture(monitor)
                opened.assert_not_called()

    async def test_missing_image_is_useful_camera_error(self):
        with patch.object(Path, "open", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(CaptureError, "파일을 읽을 수 없습니다"):
                await FixtureCamera().capture("monitor-1")
            self.assertIn("파일을 읽을 수 없습니다", FixtureCamera().readiness())

    async def test_scenario_cannot_redirect_capture_to_arbitrary_file(self):
        scenario = {"people": [{"monitorId": "monitor-1", "image": "https://example.com/frame.png"}]}
        with patch("relay.camera.SCENARIO", scenario), patch.object(Path, "open") as opened:
            with self.assertRaisesRegex(CaptureError, "허용된"):
                await FixtureCamera().capture("monitor-1")
            opened.assert_not_called()

    async def test_symlink_outside_fixture_folder_rejected(self):
        with patch.object(Path, "resolve", side_effect=[
            PUBLIC_ROOT / "elsewhere.png", PUBLIC_ROOT,
        ]):
            with self.assertRaisesRegex(CaptureError, "지정된 로컬"):
                await FixtureCamera().capture("monitor-1")
        with patch.object(Path, "resolve", side_effect=OSError("symlink loop")):
            with self.assertRaisesRegex(CaptureError, "로컬 경로"):
                await FixtureCamera().capture("monitor-1")

    async def test_corrupt_or_oversize_file_rejected(self):
        with patch.object(Path, "open") as opened:
            opened.return_value.__enter__.return_value.read.return_value = b"not png"
            with self.assertRaises(CaptureError):
                await FixtureCamera().capture("monitor-1")
            opened.return_value.__enter__.return_value.read.assert_called_once_with(
                config.VISION_MAX_IMAGE_BYTES + 1
            )

    def test_invalid_image_format_size_and_structure(self):
        image = (PUBLIC_ROOT / "monitors" / "monitor-1.png").read_bytes()
        for data, content_type in (
            (b"", "image/png"),
            (image, "image/jpeg"),
            (b"<svg/>", "image/png"),
            (image[:-12], "image/png"),
            (image + b"trailing", "image/png"),
            (image[:30] + bytes([image[30] ^ 1]) + image[31:], "image/png"),
        ):
            with self.subTest(length=len(data), content_type=content_type):
                with self.assertRaises(CaptureError):
                    validate_image(data, content_type)
        with patch("relay.config.VISION_MAX_IMAGE_BYTES", len(image) - 1):
            with self.assertRaisesRegex(CaptureError, "허용 크기"):
                validate_image(image, "image/png")


if __name__ == "__main__":
    unittest.main()
