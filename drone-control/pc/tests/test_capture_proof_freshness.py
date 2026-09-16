"""Capture proof must admit telemetry that is merely one bridge poll old.

2026-09-17 field run: a capture aborted with "Capture is outside the fresh
height/battery safety envelope" while the drone sat stationary at 1.5 m with
33 % battery. Height, is_flying, are_motors_on and flight_mode all share a
single ~2.5 Hz bridge poll, so height_age_s sawtooths between 0 and ~0.40 s
(measured median 170 ms, max 405 ms over 7438 samples across three flights).
`elapsed` is an independent sawtooth reaching ~0.10 s because the status poll
runs at 10 Hz. Whenever both crested together their sum passed the old 0.5 s
bound and the mission was abandoned mid-flight; the same capture succeeded
moments earlier purely because the two sawtooths were out of phase.
"""
import threading
import time
import unittest
from types import SimpleNamespace

from test_id1_pair_integration import airborne_raw
from drone_nav.protocol import Telemetry
from drone_nav.tool_control import field

# Worst height_age_ms actually observed on the bridge, and the status poll
# period that bounds `elapsed`. Their sum is what the envelope must tolerate.
OBSERVED_PEAK_HEIGHT_AGE_S = .405
STATUS_POLL_PERIOD_S = .1


def proof(height_age_s=OBSERVED_PEAK_HEIGHT_AGE_S, elapsed=STATUS_POLL_PERIOD_S,
          height_m=1.5, battery_percent=33., **changes):
    """Run the real _capture_proof against a stationary drone at a chosen age."""
    raw = airborne_raw(height_m=height_m, battery_percent=battery_percent,
                       height_age_ms=height_age_s * 1000.,
                       # The slow poll stamps its whole group with one age.
                       is_flying_age_ms=height_age_s * 1000.,
                       are_motors_on_age_ms=height_age_s * 1000.,
                       velocity_age_ms=198.)
    raw.update(changes)
    snapshot = SimpleNamespace(key=(1, 1), received_s=time.monotonic())
    client = SimpleNamespace(
        cancel=threading.Event(), deadline=None, raw=raw,
        received=time.perf_counter() - elapsed,
        last_telemetry=Telemetry.from_ack_payload({"telemetry": raw}),
        observe_frame=lambda _: None, status=lambda _: None)
    stream = SimpleNamespace(last_detection_snapshot=snapshot)
    return field.FieldAdapter._capture_proof(client, stream, snapshot)


class CaptureProofFreshnessTests(unittest.TestCase):
    def test_peak_bridge_age_plus_poll_period_is_admitted(self):
        """The exact combination that aborted the 2026-09-17 flight now passes."""
        self.assertGreater(OBSERVED_PEAK_HEIGHT_AGE_S + STATUS_POLL_PERIOD_S, .5,
                           "the regression only exists because the sum exceeds the old bound")
        evidence = proof()
        self.assertEqual(evidence["velocity_north_mps"], 0.)
        self.assertEqual(evidence["velocity_east_mps"], 0.)

    def test_bound_covers_the_measured_bridge_cadence_with_margin(self):
        worst = OBSERVED_PEAK_HEIGHT_AGE_S + STATUS_POLL_PERIOD_S
        self.assertGreaterEqual(field.MAX_HEIGHT_AGE_S, worst * 1.5,
                                "leave room for a poll that runs late, not just the observed peak")
        # is_flying rides the identical poll, so height must not be laxer than it.
        self.assertLess(field.MAX_HEIGHT_AGE_S, field.shuttle.FLIGHT_STATE_FRESH_S)

    def test_frozen_height_is_still_refused(self):
        """Two missed bridge polls is a stale reading, not a slow one."""
        with self.assertRaises(InterruptedError) as caught:
            proof(height_age_s=field.MAX_HEIGHT_AGE_S + .05, elapsed=0.)
        self.assertIn("safety envelope", str(caught.exception))

    def test_missing_height_age_is_still_refused(self):
        with self.assertRaises(InterruptedError):
            proof(height_age_ms=None)

    def test_height_and_battery_limits_are_unchanged(self):
        for kwargs in ({"height_m": .4}, {"height_m": 1.9}, {"battery_percent": 29.}):
            with self.subTest(**kwargs), self.assertRaises(InterruptedError) as caught:
                proof(**kwargs)
            self.assertIn("safety envelope", str(caught.exception))
        proof(height_m=.5)
        proof(height_m=1.8)
        proof(battery_percent=30.)

    def test_moving_drone_is_still_refused_at_the_relaxed_height_bound(self):
        """Relaxing height freshness must not let a drifting capture through."""
        with self.assertRaises(InterruptedError) as caught:
            proof(velocity_north_mps=.2)
        self.assertIn("stationary", str(caught.exception))

    def test_rc_override_is_still_refused(self):
        with self.assertRaises(InterruptedError) as caught:
            proof(rc_override_age_ms=1000.)
        self.assertIn("RC override", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
