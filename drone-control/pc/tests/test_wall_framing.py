from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from drone_nav.config import load_config
from drone_nav.localization import TagDetection
from drone_nav.patrol import _traverse_to_expected, PatrolRouteTracker, PatrolPhase
from drone_nav.protocol import Telemetry
from drone_nav.transforms import identity
from drone_nav.wall_framing import WallViewAction as Action, wall_view_action


CONFIG = Path(__file__).parents[1] / "config.sample.json"


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(CONFIG)
        self.p = self.config.patrol

    def test_right_inside_not_centre_is_accepted(self):
        self.assertEqual(wall_view_action((1450,750),(1080,1920,3),"left",self.p),Action.INSIDE)

    def test_left_inside_on_return(self):
        self.assertEqual(wall_view_action((350,300),(1080,1920,3),"right",self.p),Action.INSIDE)

    def test_resolution_independent(self):
        self.assertEqual(wall_view_action((725,375),(540,960,3),"left",self.p),Action.INSIDE)

    def test_same_direction_approach_only(self):
        self.assertEqual(wall_view_action((100,540),(1080,1920,3),"left",self.p),Action.APPROACH)
        self.assertEqual(wall_view_action((1800,540),(1080,1920,3),"right",self.p),Action.APPROACH)
        self.assertEqual(wall_view_action((1800,540),(1080,1920,3),"left",self.p),Action.PASSED)
        self.assertEqual(wall_view_action((100,540),(1080,1920,3),"right",self.p),Action.PASSED)

    def test_no_height_chase(self):
        self.assertEqual(wall_view_action((960,50),(1080,1920,3),"left",self.p),Action.VERTICAL_OUTSIDE)

    def test_invalid_geometry(self):
        for point,shape in [((float("nan"),500),(1080,1920)),((300,500),(0,1920)),
                            ((2000,500),(1080,1920)),(None,(1080,1920))]:
            self.assertEqual(wall_view_action(point,shape,"left",self.p),Action.INVALID)

    def test_bounds_validate_and_heading_is_not_implicitly_enabled(self):
        self.assertFalse(self.p.align_cruise_yaw)
        for change in ({"wall_view_x_min":.9},{"wall_view_y_max":1.1},
                       {"wall_view_x_min":float("nan")},{"align_cruise_yaw":1}):
            with self.assertRaises(ValueError):
                replace(self.config,patrol=replace(self.p,**change)).validate()


class TraversalTests(unittest.TestCase):
    def run_frames(self, positions, direction="left", expected=3, error=None, duplicate=False):
        cfg=load_config(CONFIG)
        p=replace(cfg.patrol,leg_timeout_s=2,recovery_max_angle_deg=1.5)
        route=PatrolRouteTracker((2,1,3))
        for tag in ([0] if expected==2 else [0,2,1]):
            route.confirm(tag)
        if direction=="right":
            route.confirm(3)
            route.begin_return()
        client=Mock()
        client.last_telemetry=Telemetry(armed=True,vs_authority="MSDK",max_tilt_angle_deg=3)
        logger=Mock()
        logger.payload.return_value={}
        clock=SimpleNamespace(now=0.,index=-1)
        def wait():
            clock.now+=.11
        limiter=Mock()
        limiter.wait.side_effect=wait
        capture=Mock()
        def detect(*_):
            clock.index+=1
            pos=positions[min(clock.index,len(positions)-1)]
            key=(1,1 if duplicate else clock.index+1)
            capture.last_detection_snapshot=SimpleNamespace(key=key,received_s=clock.now,frame=SimpleNamespace(shape=(1080,1920,3)))
            # A second visible tag must not block the expected tag.
            return [TagDetection(expected,identity(),.01,pos),TagDetection(1 if expected==3 else 3,identity(),.01,(950,500))],.01
        with patch("drone_nav.patrol.time.monotonic",side_effect=lambda:clock.now), \
             patch("drone_nav.patrol._fresh_detections",side_effect=detect), \
             patch("drone_nav.patrol._pause_for_tag_photo",side_effect=lambda *a: TagDetection(expected,identity(),.01,positions[-1])) as dwell, \
             patch("drone_nav.patrol._send_test_setpoint") as sender:
            def invoke():
                return _traverse_to_expected(client,limiter,capture,Mock(),logger,route,p,
                    direction=direction,target_x=960,target_y=540,departure_tag_id=1 if expected==3 else 3)
            if error:
                with self.assertRaisesRegex(RuntimeError,error):
                    invoke()
            else:
                self.assertEqual(invoke().tag_id,expected)
            commands=[call.args[2] for call in sender.call_args_list]
            if not error:
                dwell.assert_called_once()
            else:
                dwell.assert_not_called()
        return route,client,logger,commands

    def test_overlap_off_centre_reaches_endpoint(self):
        route,client,logger,commands=self.run_frames([(1450,750)])
        self.assertEqual(route.phase,PatrolPhase.TURNAROUND)
        self.assertTrue(all(c.right==0 and c.up==0 for c in commands))

    def test_recorded_id2_positions_no_longer_chase_centre(self):
        # Actual image positions from 20260906T120754: old point controller
        # crossed the optical centre and reversed repeatedly. These first
        # positions already provide broad-view acceptance. Offline replay is
        # a controller regression, not a claim of new flight success.
        _,_,logger,commands=self.run_frames([
            (283.57,696.81),(591.79,697.32),(810.88,648.12),
            (1102.82,573.84),(1328.78,567.76)],expected=2)
        self.assertTrue(all(c.right<=0 and c.up==0 for c in commands))

    def test_return_acceptance_and_other_tag_ignored(self):
        route,_,_,commands=self.run_frames([(350,300)],"right",1)
        self.assertEqual(route.expected_id,2)
        self.assertTrue(all(c.right==0 and c.up==0 for c in commands))

    def test_approach_never_reverses_or_changes_height(self):
        _,_,_,commands=self.run_frames([(100,600),(200,600),(350,600),(600,600),(900,600),(1200,600)])
        self.assertTrue(any(c.right<0 for c in commands))
        self.assertTrue(all(c.right<=0 and c.up==0 and c.forward==0 for c in commands))

    def test_passed_window_does_not_command_reverse(self):
        _,client,_,commands=self.run_frames([(1800,500)],error="passed_view_no_reverse")
        self.assertEqual(commands,[])
        client.zero.assert_called()

    def test_vertical_outside_does_not_command_climb(self):
        _,client,_,commands=self.run_frames([(960,50)],error="vertical_outside")
        self.assertEqual(commands,[])
        client.zero.assert_called()

    def test_duplicate_frames_cannot_finish_visit(self):
        route,_,logger,_=self.run_frames([(1400,500)],error="timed out",duplicate=True)
        self.assertEqual(route.phase,PatrolPhase.OUTBOUND)
        logger.save_confirmation_photo.assert_not_called()


if __name__=="__main__":
    unittest.main()
