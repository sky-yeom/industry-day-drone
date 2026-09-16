"""Climb timing regressions with fake aircraft, processed frames and ACKs."""
from contextlib import redirect_stdout
import io
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import unittest

from test_standalone_tag_shuttle import FakeClient, StandaloneTestCase, config, shuttle


class ClimbFreshnessTests(StandaloneTestCase):
    def exercise(self, *, refreshed_age=.01, refresh_rtt=.04, post_zero=None):
        clock, client, logger = [100.], FakeClient(), MagicMock()
        client.raw.update(is_flying=True, are_motors_on=True, armed=True,
                          vs_enabled=True, vs_advanced_enabled=True, vs_authority="MSDK")
        stream = SimpleNamespace(last_detection_snapshot=None)
        limiter = SimpleNamespace(wait=lambda: clock.__setitem__(0, clock[0] + .1))
        zero_times, confirmation = [], []
        original_status, original_zero, original_log = client.status, client.zero, client.log_event
        passes = [0]
        def status(state):
            if state == "standalone_climb_after_video":
                passes[0] += 1
                clock[0] += refresh_rtt(passes[0]) if callable(refresh_rtt) else refresh_rtt
            original_status(state)
            client.last_telemetry.height_m = 1.6
            client.last_telemetry.height_age_s = refreshed_age if state == "standalone_climb_after_video" else .318
        def detect(detector, max_age):
            old = stream.last_detection_snapshot
            stream.last_detection_snapshot = SimpleNamespace(
                key=(1, 1 if old is None else old.key[1]+1), received_s=clock[0])
            clock[0] += .25  # Pose computation ages the old 318 ms height past 500 ms.
            return [SimpleNamespace(tag_id=0)], .25
        def zero():
            original_zero()
            zero_times.append(clock[0])
            client.last_telemetry = SimpleNamespace(**vars(client.last_telemetry))
            client.last_telemetry.height_age_s = .01
            client.last_telemetry.velocity_age_s = .01
            if post_zero is not None:
                post_zero(client.last_telemetry, len(zero_times))
        def log(event, data):
            original_log(event, data)
            if event == "standalone_target_height_confirmed":
                confirmation.append((clock[0], dict(data)))
        stream.detect_latest = detect
        client.status, client.zero, client.log_event = status, zero, log
        self.client, self.zero_times, self.confirmation = client, zero_times, confirmation
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]), \
                patch.object(shuttle, "_visual_floor_height_m", return_value=1.6), \
                redirect_stdout(io.StringIO()):
            shuttle._climb(client, limiter, stream, None, logger, config(), 1.6)

    def test_detection_delay_refreshes_height_and_confirms_target_1_6(self):
        self.exercise()
        self.assertTrue(self.confirmation)
        self.assertIn(("status", "standalone_climb_after_video"), self.client.calls)
        confirmed = self.confirmation[0][1]
        self.assertEqual(confirmed["target_height_m"], 1.6)
        self.assertEqual(confirmed["height_m"], 1.6)
        self.assertEqual(confirmed["confirmation_source"], "fresh_zero_ack")
        self.assertLessEqual(confirmed["height_age_s"], .5)
        self.assertGreaterEqual(self.confirmation[0][0] - self.zero_times[0], .5)
        self.assertFalse(any(isinstance(call, tuple) and call[0] == "attitude" for call in self.client.calls))

    def test_stale_height_returned_by_refresh_is_rejected_before_actuation(self):
        with self.assertRaisesRegex(RuntimeError, "fresh height"):
            self.exercise(refreshed_age=.501)
        self.assertEqual(self.zero_times, [])
        self.assertEqual(self.confirmation, [])

    def test_exact_frame_aged_by_refresh_rtt_is_rejected_before_actuation(self):
        with self.assertRaisesRegex(RuntimeError, "not confirmed within"):
            self.exercise(refresh_rtt=.3)
        self.assertEqual(self.confirmation, [])
        self.assertFalse(any(isinstance(call, tuple) and call[0] == "attitude" for call in self.client.calls))
        self.assertTrue(self.zero_times, "the setpoint must keep arriving while a new frame decodes")
        self.assertTrue(any(event == "standalone_climb_frame_deferred" for event, _ in self.client.events))

    def test_one_slow_round_trip_re_observes_instead_of_ending_the_climb(self):
        self.exercise(refresh_rtt=lambda n: .3 if n == 1 else .04)
        self.assertTrue(self.confirmation)
        self.assertEqual(self.confirmation[0][1]["target_height_m"], 1.6)
        self.assertEqual(sum(event == "standalone_climb_frame_deferred"
                             for event, _ in self.client.events), 1)

    def test_post_zero_height_and_velocity_must_stay_at_target_for_hold(self):
        def zero_sample(telemetry, count):
            telemetry.height_m = 1.5 if count <= 2 else 1.6
            telemetry.velocity_down_mps = -.15 if count == 3 else 0.
        self.exercise(post_zero=zero_sample)
        self.assertGreaterEqual(len(self.zero_times), 6)
        self.assertGreaterEqual(self.confirmation[0][0] - self.zero_times[3], .5)
        self.assertEqual(self.confirmation[0][1]["velocity_down_mps"], 0.)


class ClimbSetpointContinuityTests(StandaloneTestCase):
    """The aircraft disables Virtual Stick when no setpoint arrives for ~1 s.

    A 1.045 s setpoint gap handed authority back to the RC mid-climb while
    status polling kept flowing, so every path that re-observes instead of
    commanding has to refresh the setpoint on its way round the loop.
    """

    def drive(self, *, floor_seen=True, defer_attitude=False, height_m=1.1):
        clock, client, logger = [100.], FakeClient(), MagicMock()
        client.raw.update(is_flying=True, are_motors_on=True, armed=True,
                          vs_enabled=True, vs_advanced_enabled=True, vs_authority="MSDK")
        client.last_telemetry.height_m = height_m
        stream = SimpleNamespace(last_detection_snapshot=None)
        limiter = SimpleNamespace(wait=lambda: clock.__setitem__(0, clock[0] + .1))
        # Every call records the clock so a setpoint gap is measurable.
        setpoints, original_zero, original_attitude = [], client.zero, client.attitude
        def zero():
            original_zero()
            setpoints.append(clock[0])
            client.last_telemetry.height_age_s = .01
            client.last_telemetry.velocity_age_s = .01
        def attitude(*axes):
            if defer_attitude:
                raise shuttle.FramingCorrectionDeferred(
                    "Detected camera frame expired before command dispatch")
            original_attitude(*axes)
            setpoints.append(clock[0])
        def detect(detector, max_age):
            old = stream.last_detection_snapshot
            stream.last_detection_snapshot = SimpleNamespace(
                key=(1, 1 if old is None else old.key[1]+1), received_s=clock[0])
            clock[0] += .25
            return ([SimpleNamespace(tag_id=0)] if floor_seen else []), .25
        stream.detect_latest = detect
        client.zero, client.attitude = zero, attitude
        self.client, self.setpoints = client, setpoints
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]), \
                patch.object(shuttle, "_visual_floor_height_m", return_value=height_m), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                shuttle._climb(client, limiter, stream, None, logger, config(), 1.6,
                               {"timeouts": {"climb_s": 5.}})

    def longest_gap(self):
        return max(b - a for a, b in zip(self.setpoints, self.setpoints[1:]))

    def test_deferred_correction_still_refreshes_the_setpoint(self):
        self.drive(defer_attitude=True)
        self.assertTrue(any(event == "standalone_climb_deferred"
                            for event, _ in self.client.events))
        self.assertIn("zero", self.client.calls)
        self.assertGreaterEqual(len(self.setpoints), 2)
        self.assertLess(self.longest_gap(), 1.,
                        "a deferred climb correction left Virtual Stick uncommanded")

    def test_floor_tag_miss_grace_still_refreshes_the_setpoint(self):
        self.drive(floor_seen=False)
        self.assertTrue(any(event == "standalone_climb_tag_miss"
                            for event, _ in self.client.events))
        self.assertIn("zero", self.client.calls)
        self.assertGreaterEqual(len(self.setpoints), 2)
        self.assertLess(self.longest_gap(), shuttle.CLIMB_TAG_MISS_GRACE_S,
                        "the tag-miss grace outlasted the Virtual Stick watchdog")


if __name__ == "__main__":
    unittest.main()
