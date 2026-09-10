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
        def status(state):
            if state == "standalone_climb_after_video":
                clock[0] += refresh_rtt
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
        with self.assertRaisesRegex(InterruptedError, "floor frame expired"):
            self.exercise(refresh_rtt=.3)
        self.assertEqual(self.zero_times, [])
        self.assertEqual(self.confirmation, [])

    def test_post_zero_height_and_velocity_must_stay_at_target_for_hold(self):
        def zero_sample(telemetry, count):
            telemetry.height_m = 1.5 if count <= 2 else 1.6
            telemetry.velocity_down_mps = -.15 if count == 3 else 0.
        self.exercise(post_zero=zero_sample)
        self.assertGreaterEqual(len(self.zero_times), 6)
        self.assertGreaterEqual(self.confirmation[0][0] - self.zero_times[3], .5)
        self.assertEqual(self.confirmation[0][1]["velocity_down_mps"], 0.)


if __name__ == "__main__":
    unittest.main()
