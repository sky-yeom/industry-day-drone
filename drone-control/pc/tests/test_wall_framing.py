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
from drone_nav.wall_framing import (
    WallViewAction as Action, wall_view_action, tag_view_action, tv_frame_window, tv_frame_correction,
)


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
    def run_frames(self, positions, direction="left", expected=3, error=None, duplicate=False,
                   tv=False, tag_size=100):
        cfg=load_config(CONFIG)
        p=replace(cfg.patrol,leg_timeout_s=2,recovery_max_angle_deg=1.5,cruise_altitude_m=1.4,
                  tv_framing=cfg.patrol.tv_framing if tv else None)
        route=PatrolRouteTracker((2,1,3))
        for tag in ([0] if expected==2 else [0,2,1]):
            route.confirm(tag)
        if direction=="right":
            route.confirm(3)
            route.begin_return()
        client=Mock()
        client.last_telemetry=Telemetry(armed=True,vs_authority="MSDK",max_tilt_angle_deg=3,
            vs_enabled=True,vs_advanced_enabled=True,is_flying=True,is_flying_age_s=.01,
            battery_percent=80,height_m=1.4,height_age_s=.01,attitude_age_s=.01,yaw_deg=0.,
            velocity_north_mps=0.,velocity_east_mps=0.,velocity_down_mps=0.,velocity_age_s=.01)
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
            capture.last_detection_snapshot=SimpleNamespace(key=key,generation=key[0],
                received_s=clock.now,frame=SimpleNamespace(shape=(1080,1920,3)))
            # A second visible tag must not block the expected tag.
            corners = tuple((pos[0]+dx*tag_size/2, pos[1]+dy*tag_size/2)
                            for dx,dy in ((-1,-1),(1,-1),(1,1),(-1,1)))
            return [TagDetection(expected,identity(),.01,pos,corners),TagDetection(1 if expected==3 else 3,identity(),.01,(950,500))],.01
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
            if tv:
                self.assertTrue(all(call.args[4] <= .5 for call in sender.call_args_list))
                self.assertTrue(all(abs(call.args[1]) <= .5 and call.args[2] == 0
                                    for call in client.attitude.call_args_list))
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

    def test_tv_reference_never_chases_optical_centre(self):
        _, _, _, commands = self.run_frames([(1380,450)], tv=True)
        self.assertTrue(all(c.right == 0 and c.up == 0 and c.forward == 0 for c in commands))

    def test_tv_reference_approach_is_gentle_and_can_correct_both_directions(self):
        for direction,expected,positions,sign in (
            ("left",3,[(1000,450),(1200,450),(1340,450)],-1),
            ("right",1,[(1750,450),(1650,450),(1450,450)],1),
            ("left",3,[(1750,450),(1650,450),(1450,450)],1)):
            _, client, _, _ = self.run_frames(positions,direction,expected,tv=True)
            self.assertTrue(client.attitude.called)
            self.assertTrue(all(call.args[0] == 0 and call.args[1]*sign > 0
                                and call.args[2] == 0 and call.args[3] == 0
                                for call in client.attitude.call_args_list))

    def test_tv_reference_too_close_stops_without_backing_up(self):
        _, client, _, commands = self.run_frames([(1380,450)], tv=True, tag_size=400, error="cannot fit")
        self.assertEqual(commands, [])
        client.zero.assert_called()


class TVReferenceTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(CONFIG)
        self.p = self.config.patrol

    def detection(self, x=384.5, y=106., width=57., height=58., tag_id=1):
        corners = ((x-width/2,y-height/2),(x+width/2,y-height/2),
                   (x+width/2,y+height/2),(x-width/2,y+height/2))
        return TagDetection(tag_id, identity(), .01, (x,y), corners)

    def test_reference_photo_composition_is_accepted_in_both_directions_and_resolutions(self):
        # Camera area of the supplied screen reference, excluding app sidebars.
        for scale in (1., 2., 3.):
            tag = self.detection(384.5*scale,106*scale,57*scale,58*scale)
            for direction in ("left", "right"):
                self.assertEqual(tag_view_action(tag,(307*scale,550*scale,3),direction,self.p),Action.INSIDE)

    def test_tag_only_centre_is_not_the_tv_composition(self):
        tag = self.detection(x=275)
        self.assertEqual(tag_view_action(tag,(307,550,3),"left",self.p),Action.APPROACH)
        self.assertEqual(tag_view_action(tag,(307,550,3),"right",self.p),Action.PASSED)

    def test_tv_envelope_can_override_apparently_valid_tag_window(self):
        tag = self.detection(x=360,width=60)
        self.assertGreater(tag.center_px[0]/550,self.p.tv_framing.tag_x_min)
        self.assertEqual(tag_view_action(tag,(307,550,3),"left",self.p),Action.APPROACH)

    def test_too_large_reference_and_vertical_offset_cannot_trigger_height_correction(self):
        self.assertEqual(tag_view_action(self.detection(width=90),(307,550,3),"left",self.p),Action.ENVELOPE_OUTSIDE)
        for y in (70,210):
            tag=self.detection(y=y)
            self.assertEqual(tag_view_action(tag,(307,550,3),"left",self.p),Action.VERTICAL_OUTSIDE)
            with self.assertRaisesRegex(ValueError,"height correction is disabled"):
                tv_frame_correction(tv_frame_window(tag,(307,550,3),self.p),self.p)

    def test_composition_deadband_stops_correction_not_optical_centre(self):
        self.assertEqual(tv_frame_correction(tv_frame_window(self.detection(),(307,550,3),self.p),self.p),(0.,0.))
        left=tv_frame_correction(tv_frame_window(self.detection(x=275),(307,550,3),self.p),self.p)
        right=tv_frame_correction(tv_frame_window(self.detection(x=480),(307,550,3),self.p),self.p)
        self.assertLess(left[0],0)
        self.assertGreater(right[0],0)
        self.assertLessEqual(max(abs(left[0]),abs(right[0])),.5)
        self.assertEqual(left[1],0.)
        self.assertEqual(right[1],0.)

    def test_raw_frame_corners_not_undistorted_centre_drive_tv_framing(self):
        tag = replace(self.detection(),center_px=(20.,20.))
        self.assertEqual(tag_view_action(tag,(307,550,3),"left",self.p),Action.INSIDE)
        for corners in (None,((0.,0.),)*4,((float("nan"),0.),)*4):
            self.assertEqual(tag_view_action(replace(tag,frame_corners_px=corners),
                                            (307,550,3),"left",self.p),Action.INVALID)

    def test_floor_and_home_do_not_require_tv_space_or_raw_corners(self):
        for tag_id in (0,6):
            tag = replace(self.detection(x=275),tag_id=tag_id,frame_corners_px=None)
            self.assertEqual(tag_view_action(tag,(307,550,3),"left",self.p),Action.INSIDE)
        legacy = replace(self.p,tv_framing=None)
        self.assertEqual(tag_view_action(replace(self.detection(x=275),frame_corners_px=None),
                                        (307,550,3),"left",legacy),Action.INSIDE)

    def test_tv_parameters_reject_invalid_geometry_and_aggressive_correction(self):
        for change in ({"tag_x_min":.4},{"tag_x_max":.6},{"left_tag_widths":float("nan")},
                       {"right_tag_widths":.1},{"frame_margin":0},{"approach_angle_deg":4},
                       {"max_vertical_speed_mps":.08},{"max_vertical_speed_mps":-.08},
                       {"max_vertical_speed_mps":True},{"max_correction_distance_m":1.},
                       {"max_height_offset_m":.3},{"max_correction_s":20},{"correction_interval_s":.01}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                replace(self.config,patrol=replace(self.p,tv_framing=replace(self.p.tv_framing,**change))).validate()


if __name__=="__main__":
    unittest.main()
