"""Raw-frame TV geometry agrees with the image used for capture, not a UI screenshot."""
from dataclasses import replace
from pathlib import Path
import unittest

from drone_nav.config import load_config
from drone_nav.vision import AprilTagDetector
from drone_nav.wall_framing import WallViewAction, tag_view_action


class DetectorFrameTests(unittest.TestCase):
    def setUp(self):
        try:
            import cv2
            import numpy as np
            import pupil_apriltags
        except ImportError:
            self.skipTest("Optional vision dependencies are required for camera geometry tests")
        self.cv2, self.np = cv2, np
        self.config = load_config(Path(__file__).parents[1] / "config.sample.json")

    def test_distorted_frame_corners_map_back_to_the_original_detection(self):
        detector = AprilTagDetector(self.config)
        undistorted = self.np.array([[1200.,400.],[1300.,400.],[1300.,500.],[1200.,500.]])
        raw = self.np.array(detector._frame_corners(undistorted))
        recovered = self.cv2.undistortPoints(raw.reshape(-1,1,2),detector._matrix,
                                           detector._distortion,P=detector._matrix).reshape(-1,2)
        self.np.testing.assert_allclose(recovered,undistorted,atol=.05)
        self.assertGreater(float(self.np.max(self.np.abs(raw-undistorted))),.1)

    def test_synthetic_id1_is_detected_and_framed_without_changing_saved_pixels(self):
        camera = replace(self.config.camera,fx=800.,fy=800.,cx=480.,cy=270.,
                         distortion=[0.,0.,0.,0.,0.])
        detector = AprilTagDetector(replace(self.config,camera=camera))
        image = self.np.full((540,960,3),255,dtype=self.np.uint8)
        dictionary = self.cv2.aruco.getPredefinedDictionary(self.cv2.aruco.DICT_APRILTAG_36h11)
        marker = self.cv2.aruco.generateImageMarker(dictionary,1,90)
        image[155:245,627:717] = marker[:,:,None]
        before = image.copy()
        detections = detector.detect(image)
        self.assertEqual([tag.tag_id for tag in detections],[1])
        self.assertEqual(len(detections[0].frame_corners_px),4)
        self.assertEqual(tag_view_action(detections[0],image.shape,"left",self.config.patrol),
                         WallViewAction.INSIDE)
        self.np.testing.assert_array_equal(image,before)


if __name__ == "__main__":
    unittest.main()
