"""A confirmed visit must still produce a photo when the detection frame ages.

The detection snapshot is taken when the tag is found, then keeps ageing while
the arrival is confirmed and proved stationary.  Field flight 20260916T175217
lost both ID2 photos to `camera frame is stale (0.672s)` even though the
aircraft was holding position and the stream was healthy.
"""

from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

import numpy as np

from drone_nav.patrol import _DetectionLogger, PatrolPhase
from drone_nav.runtime import FRESH_FRAME_S
from drone_nav.vision import VideoSnapshot


class ConfirmationPhotoFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / "_photo_freshness_tmp"
        for stale in self.root.glob("*.jpg"):
            stale.unlink()
        self.tag = SimpleNamespace(tag_id=2, center_px=(1614, 662))

    def tearDown(self):
        for leftover in self.root.glob("*.jpg"):
            leftover.unlink()
        if self.root.is_dir():
            self.root.rmdir()

    def _capture(self, *, detection_age_s, newest_age_s, newest_frame=True):
        now = time.monotonic()
        detected = VideoSnapshot(1, 10, now - detection_age_s, np.full((16, 16, 3), 40, np.uint8))
        newest = None
        if newest_age_s is not None:
            newest = VideoSnapshot(
                1, 11, now - newest_age_s,
                np.full((16, 16, 3), 90, np.uint8) if newest_frame else None,
            )
        return SimpleNamespace(last_detection_snapshot=detected, snapshot=lambda: newest), detected, newest

    def _events(self, client):
        return [call.args[0] for call in client.log_event.call_args_list]

    def _payload(self, client, name):
        for call in client.log_event.call_args_list:
            if call.args[0] == name:
                return call.args[1]
        raise AssertionError(f"{name} never logged: {self._events(client)}")

    def test_stale_detection_frame_falls_back_to_the_newest_decoded_frame(self):
        capture, _, newest = self._capture(detection_age_s=FRESH_FRAME_S + .18, newest_age_s=.05)
        client = MagicMock()
        logger = _DetectionLogger(client, photo_root=self.root)

        path = logger.save_confirmation_photo(capture, self.tag, phase=PatrolPhase.OUTBOUND)

        self.assertIsNotNone(path, "a hovering aircraft must not lose its evidence photo")
        self.assertTrue(path.is_file(), path)
        self.assertNotIn("tag_photo_failed", self._events(client))
        payload = self._payload(client, "tag_photo_saved")
        self.assertEqual(payload["frame_source"], "latest_decoded")
        self.assertEqual(tuple(payload["frame_key"]), tuple(newest.key))
        self.assertLessEqual(payload["frame_age_s"], FRESH_FRAME_S)

    def test_a_fresh_detection_frame_is_still_the_one_that_gets_saved(self):
        capture, detected, _ = self._capture(detection_age_s=.04, newest_age_s=.01)
        client = MagicMock()
        logger = _DetectionLogger(client, photo_root=self.root)

        self.assertIsNotNone(logger.save_confirmation_photo(capture, self.tag, phase=PatrolPhase.OUTBOUND))

        payload = self._payload(client, "tag_photo_saved")
        self.assertEqual(payload["frame_source"], "detection")
        self.assertEqual(tuple(payload["frame_key"]), tuple(detected.key))

    def test_a_stalled_stream_still_refuses_to_invent_a_photo(self):
        capture, _, _ = self._capture(detection_age_s=FRESH_FRAME_S + .3, newest_age_s=FRESH_FRAME_S + .29)
        client = MagicMock()
        logger = _DetectionLogger(client, photo_root=self.root)

        self.assertIsNone(logger.save_confirmation_photo(capture, self.tag, phase=PatrolPhase.OUTBOUND))

        self.assertIn("tag_photo_failed", self._events(client))
        self.assertIn("stale", self._payload(client, "tag_photo_failed")["error"])

    def test_a_stream_with_no_decoded_frame_is_not_a_fallback(self):
        capture, _, _ = self._capture(detection_age_s=FRESH_FRAME_S + .3, newest_age_s=None)
        client = MagicMock()
        logger = _DetectionLogger(client, photo_root=self.root)

        self.assertIsNone(logger.save_confirmation_photo(capture, self.tag, phase=PatrolPhase.OUTBOUND))
        self.assertIn("tag_photo_failed", self._events(client))

    def test_a_frameless_newest_snapshot_is_not_a_fallback(self):
        capture, _, _ = self._capture(
            detection_age_s=FRESH_FRAME_S + .3, newest_age_s=.02, newest_frame=False
        )
        client = MagicMock()
        logger = _DetectionLogger(client, photo_root=self.root)

        self.assertIsNone(logger.save_confirmation_photo(capture, self.tag, phase=PatrolPhase.OUTBOUND))
        self.assertIn("tag_photo_failed", self._events(client))

    def test_a_capture_without_a_snapshot_method_keeps_the_old_behaviour(self):
        now = time.monotonic()
        capture = SimpleNamespace(
            last_detection_snapshot=VideoSnapshot(
                1, 10, now - (FRESH_FRAME_S + .2), np.full((16, 16, 3), 40, np.uint8)
            )
        )
        client = MagicMock()
        logger = _DetectionLogger(client, photo_root=self.root)

        self.assertIsNone(logger.save_confirmation_photo(capture, self.tag, phase=PatrolPhase.OUTBOUND))
        self.assertIn("tag_photo_failed", self._events(client))


if __name__ == "__main__":
    unittest.main()
