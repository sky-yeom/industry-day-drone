"""Closed-loop composition and refusal cases; no sockets or real aircraft."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from drone_nav.config import load_config
from drone_nav.localization import TagDetection
from drone_nav.patrol import _align_tv_composition
from drone_nav.protocol import Telemetry
from drone_nav.transforms import identity
from drone_nav.vision import VideoSnapshot


class TVCorrectionTests(unittest.TestCase):
    def exercise(self, *, x=950., y=450., telemetry=None, lost=False, duplicate=False,
                 generation_change=False, latency=0., responds=True, speed=0., width=100.):
        config = load_config(Path(__file__).parents[1] / "config.sample.json")
        patrol = replace(config.patrol, cruise_altitude_m=1.4)
        clock = SimpleNamespace(now=0., frames=0, x=x, y=y)
        actions = []
        normal = Telemetry(
            armed=True,is_flying=True,is_flying_age_s=.01,vs_enabled=True,vs_advanced_enabled=True,
            vs_authority="MSDK",battery_percent=80,height_m=1.4,height_age_s=.01,
            yaw_deg=0.,attitude_age_s=.01,velocity_north_mps=speed,velocity_east_mps=0.,
            velocity_down_mps=0.,velocity_age_s=.01,max_tilt_angle_deg=3.)
        normal = replace(normal, **(telemetry or {}))

        class Client:
            last_telemetry = normal

            def zero(self):
                actions.append(("zero", clock.now))
                self.last_telemetry = normal

            def log_event(self, *args):
                pass

            def attitude(self, forward, right, up, yaw):
                actions.append(("motion", clock.now, forward, right, up, yaw))
                if responds:
                    clock.x -= right * 230
                    clock.y += up * 700

        class Stream:
            last_detection_snapshot = None

            def detect_latest(self, *args):
                clock.frames += 1
                received = clock.now
                clock.now += latency
                generation = 2 if generation_change and clock.frames > 1 else 1
                self.last_detection_snapshot = VideoSnapshot(
                    generation,1 if duplicate else clock.frames,received,
                    SimpleNamespace(shape=(1080,1920,3)))
                x,y = clock.x,clock.y
                corners = ((x-width/2,y-50),(x+width/2,y-50),(x+width/2,y+50),(x-width/2,y+50))
                tag = TagDetection(1,identity(),.01,(x,y),corners)
                return [] if lost and clock.frames > 1 else [tag], latency

        def wait():
            clock.now += .1

        error = None
        with patch("socket.create_connection",side_effect=AssertionError("No hardware")), \
             patch("drone_nav.patrol.time.monotonic",lambda:clock.now):
            try:
                _align_tv_composition(Client(),SimpleNamespace(wait=wait),Stream(),None,1,patrol,deadline=20.)
            except (RuntimeError,InterruptedError) as exc:
                error = str(exc)
        return actions,error,clock

    def test_horizontal_corrections_converge_without_changing_height(self):
        for x,y in ((950.,450.),(1750.,450.),(950.,600.),(1750.,300.)):
            with self.subTest(x=x,y=y):
                actions,error,clock=self.exercise(x=x,y=y)
                self.assertIsNone(error)
                motion=[a for a in actions if a[0]=="motion"]
                self.assertTrue(motion)
                self.assertTrue(all(a[2]==0 and a[4]==0 and a[5]==0 and abs(a[3])<=.5 for a in motion))
                self.assertTrue(all(b[1]-a[1]>=.3-1e-9 for a,b in zip(motion,motion[1:])))
                self.assertEqual(actions[-1][0],"zero")
                self.assertTrue(.65*1920<=clock.x<=.82*1920)
                self.assertTrue(.25*1080<=clock.y<=.60*1080)
                self.assertEqual(clock.y,y)

    def test_vertical_or_combined_error_stops_without_any_motion(self):
        for x,y in ((1400.,190.),(1400.,730.),(950.,730.),(1750.,190.)):
            with self.subTest(x=x,y=y):
                actions,error,clock=self.exercise(x=x,y=y)
                self.assertIn("height correction is disabled",error)
                self.assertFalse(any(a[0]=="motion" for a in actions))
                self.assertEqual(actions[-1][0],"zero")
                self.assertLess(clock.now,1.)

    def test_already_inside_has_no_centre_chasing(self):
        actions,error,_=self.exercise(x=1380,y=450)
        self.assertIsNone(error)
        self.assertFalse(any(a[0]=="motion" for a in actions))

    def test_no_response_duplicate_frames_and_expired_budget_cannot_keep_pulsing(self):
        for kwargs in ({"responds":False},{"duplicate":True},{"speed":.1,"responds":False}):
            with self.subTest(kwargs=kwargs):
                actions,error,clock=self.exercise(**kwargs)
                self.assertIsNotNone(error)
                self.assertLessEqual(clock.now,8.2)
                self.assertEqual(actions[-1][0],"zero")
                if kwargs.get("duplicate"):
                    self.assertEqual(sum(a[0]=="motion" for a in actions),1)
                if kwargs.get("speed"):
                    self.assertIn("travel budget",error)

    def test_missing_frame_generation_change_and_stale_detection_abort_correction(self):
        for kwargs in ({"lost":True},{"generation_change":True},{"latency":.6}):
            with self.subTest(kwargs=kwargs):
                actions,error,_=self.exercise(**kwargs)
                self.assertIsNotNone(error)
                self.assertEqual(actions[-1][0],"zero")

    def test_authority_velocity_height_and_battery_gate_each_correction(self):
        for fields in ({"vs_authority":"RC"},{"rc_override_age_s":.1},{"height_age_s":.6},
                       {"is_flying_age_s":None},{"velocity_north_mps":float("nan")},
                       {"battery_percent":29},{"height_m":1.6}):
            with self.subTest(fields=fields):
                actions,error,_=self.exercise(telemetry=fields)
                self.assertIsNotNone(error)
                self.assertFalse(any(a[0]=="motion" for a in actions))

    def test_insufficient_distance_does_not_command_forward_or_backward(self):
        actions,error,_=self.exercise(width=400)
        self.assertIn("cannot fit",error)
        self.assertFalse(any(a[0]=="motion" for a in actions))

    def test_moving_aircraft_is_held_before_any_reverse_correction(self):
        actions,error,_=self.exercise(x=1750,speed=.25)
        self.assertIsNotNone(error)
        self.assertFalse(any(a[0]=="motion" for a in actions))


if __name__ == "__main__":
    unittest.main()
