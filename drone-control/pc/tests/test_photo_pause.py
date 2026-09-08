from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from drone_nav.config import load_config
from drone_nav.localization import TagDetection
from drone_nav.patrol import _pause_for_tag_photo, PatrolPhase
from drone_nav.protocol import Telemetry
from drone_nav.transforms import identity


class PhotoPauseTests(unittest.TestCase):
    def exercise(self, *, vx=0., age=.1, visible=True, override=False, photo_ok=True, duplicate=False):
        p=load_config(Path(__file__).parents[1]/"config.sample.json").patrol
        clock=NS(now=0., index=0)
        client=Mock()
        client.last_telemetry=Telemetry(armed=not override,vs_authority="RC" if override else "MSDK",
            vs_enabled=True,vs_advanced_enabled=True,is_flying=True,battery_percent=65,
            velocity_north_mps=vx,velocity_east_mps=0.,velocity_down_mps=0.,velocity_age_s=age)
        capture=Mock()
        logger=Mock()
        photo_times=[]
        def photograph(*args,**kwargs):
            photo_times.append(clock.now)
            return Path("OFFLINE_ONLY.jpg") if photo_ok else None
        logger.save_confirmation_photo.side_effect=photograph
        tag=TagDetection(3,identity(),.01,(1350,600))
        def detect(*args):
            clock.index+=1
            capture.last_detection_snapshot=NS(key=(1,1 if duplicate else clock.index),
                received_s=clock.now,frame=NS(shape=(1080,1920,3)))
            return ([tag] if visible else []),.01
        limiter=Mock()
        def wait(): clock.now+=.1
        limiter.wait.side_effect=wait
        problem=None
        with patch("drone_nav.patrol.time.monotonic",side_effect=lambda:clock.now), \
             patch("drone_nav.patrol._fresh_detections",side_effect=detect):
            try:
                result=_pause_for_tag_photo(client,limiter,capture,Mock(),logger,
                    PatrolPhase.OUTBOUND,3,"left",p)
                self.assertEqual(result.tag_id,3)
            except (RuntimeError,InterruptedError) as e:
                problem=e
        client.attitude.assert_not_called()
        client.arm.assert_not_called()
        client.zero.assert_called()
        return problem,photo_times,clock.now

    def test_photo_only_after_three_second_pause(self):
        error,photos,_=self.exercise()
        self.assertIsNone(error)
        self.assertEqual(len(photos),1)
        self.assertGreaterEqual(photos[0],3.)

    def test_motion_does_not_count_as_successful_pause(self):
        error,photos,elapsed=self.exercise(vx=.4)
        self.assertIsInstance(error,RuntimeError)
        self.assertEqual(photos,[])
        self.assertLess(elapsed,7.2)

    def test_stale_speed_is_not_zero_speed(self):
        error,photos,_=self.exercise(age=3.)
        self.assertIsNotNone(error)
        self.assertEqual(photos,[])

    def test_missing_tag_never_captures_wrong_target(self):
        error,photos,_=self.exercise(visible=False)
        self.assertIsNotNone(error)
        self.assertEqual(photos,[])

    def test_duplicate_frames_cannot_confirm_pause(self):
        error,photos,_=self.exercise(duplicate=True)
        self.assertIsNotNone(error)
        self.assertEqual(photos,[])

    def test_rc_override_stops_without_rearm(self):
        error,photos,elapsed=self.exercise(override=True)
        self.assertIsInstance(error,InterruptedError)
        self.assertEqual(photos,[])
        self.assertLess(elapsed,1.)

    def test_photo_failure_is_not_visit_success(self):
        error,photos,_=self.exercise(photo_ok=False)
        self.assertIsNotNone(error)
        self.assertEqual(len(photos),1)

    def test_duration_bounds(self):
        c=load_config(Path(__file__).parents[1]/"config.sample.json")
        for value in (-1,0,11,float("nan")):
            with self.assertRaises(ValueError):
                replace(c,patrol=replace(c.patrol,visit_pause_s=value)).validate()


if __name__=="__main__": unittest.main()
