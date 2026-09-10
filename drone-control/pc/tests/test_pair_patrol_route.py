"""Full patrol orchestration with fake aircraft, frames and transport only."""
from contextlib import ExitStack, redirect_stdout
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from test_standalone_tag_shuttle import FakeClient, StandaloneTestCase, config, profile, shuttle
from test_id1_pair_integration import REFERENCE, fake_stream


class PairPatrolRouteTests(StandaloneTestCase):
    def test_user_90_percent_band_arrival_continues_with_honest_photo_metadata(self):
        edge_reference = {**REFERENCE, "arrival_center_x_fraction": [.85, .95]}
        # Whole black tag is in frame, while the extrapolated mock ROI is too
        # wide for its optional margin. User arrival now depends on tag position.
        observed = shuttle.PixelTag(1, (1689.6, 540.),
            ((1839.6, 390.), (1539.6, 390.), (1539.6, 690.), (1839.6, 690.)), 80., 0)
        with patch(__name__ + ".REFERENCE", edge_reference), \
                patch("test_id1_pair_integration.fitted_tag",
                      side_effect=lambda tag_id=1: shuttle.PixelTag(tag_id, observed.center_px,
                          observed.corners_px, observed.decision_margin, observed.hamming)):
            result, client, logger, stream, legs = self.run_patrol()
        self.assertTrue(result["route_completed"], result)
        self.assertEqual(result["visited_ids"], [6, 1, 2, 3, 2, 1, 6])
        self.assertEqual(result["pair_capture"]["arrival_policy"], "tag_right_edge_band")
        self.assertFalse(result["pair_capture"]["predicted_pair_footprint_fits"])
        self.assertFalse(result["pair_capture"]["tv_visibility_verified"])
        self.assertEqual(len(legs), 3)

    def run_patrol(self, fail_at=None):
        client, clock, logger = FakeClient(), [100.], MagicMock()
        logger.save_confirmation_photo.return_value = Path("offline-first-ID1.jpg")
        stream = fake_stream(clock)
        limiter = SimpleNamespace(wait=lambda: clock.__setitem__(0, clock[0] + .1))
        legs = []

        def traverse(c, limiter, stream, detector, log, cfg, p, departure, expected, phase):
            # Outbound ID1, ID2, ID3 were each captured with their mock before any return leg.
            self.assertEqual(logger.save_confirmation_photo.call_count, 3)
            self.assertFalse(c.pair_mode)
            self.assertIsNone(c.motion_valid_until_s)
            self.assertEqual(c.lateral_bounds, (-.6, .6))
            self.assertEqual(p["max_tilt_deg"], .6)
            self.assertEqual(cfg.patrol.angle_deg, .6)
            self.assertEqual(p["target_height_m"], 1.5)
            legs.append((departure, expected))
            if expected == fail_at:
                raise RuntimeError("offline later leg failed")

        with ExitStack() as stack:
            for name, replacement in (
                ("ShuttleClient", lambda *args: client), ("MixedDetector", MagicMock()),
                ("FreshVideoStream", lambda *args, **kwargs: stream),
                ("ShuttleDetectionLogger", lambda *args, **kwargs: logger),
                ("_wait_ground_video", MagicMock()), ("_wait_takeoff_settled", MagicMock()),
                ("_acquire_tag", MagicMock()), ("_climb", MagicMock()),
                ("acquire_wall_home", MagicMock()), ("_pause", MagicMock()),
                ("RateLimiter", lambda *args: limiter), ("traverse_horizontal", traverse),
            ):
                stack.enter_context(patch.object(shuttle, name, replacement))
            stack.enter_context(patch.object(shuttle.time, "monotonic", lambda: clock[0]))
            stack.enter_context(patch.object(shuttle.time, "perf_counter", lambda: clock[0]))
            stack.enter_context(redirect_stdout(io.StringIO()))
            result = shuttle.run(config(), profile(target_height_m=1.5),
                                 pair_reference=REFERENCE, continue_patrol=True)
        return result, client, logger, stream, legs

    def test_first_id1_photo_then_remaining_patrol_then_one_final_release(self):
        result, client, logger, stream, legs = self.run_patrol()
        self.assertEqual(result["visited_ids"], [6, 1, 2, 3, 2, 1, 6], result)
        self.assertEqual(result["active_route_ids"], result["visited_ids"])
        self.assertTrue(result["route_completed"])
        self.assertTrue(result["full_route_completed"])
        self.assertEqual(result["mission_scope"], "full_patrol_with_first_ID1_pair_capture")
        self.assertEqual(legs, [(3, 2), (2, 1), (1, 6)])
        self.assertEqual(result["pair_capture"]["tag_id"], 1)
        self.assertEqual([c["tag_id"] for c in result["pair_captures"]], [1, 2, 3])
        self.assertEqual(logger.save_confirmation_photo.call_count, 3)
        self.assertEqual(client.calls.count("takeoff"), 1)
        self.assertEqual(client.calls.count("arm"), 1)
        self.assertEqual(client.calls.count("disarm"), 1)
        self.assertNotIn("land", client.calls)
        self.assertEqual(result["state"], "awaiting_rc_landing")
        self.assertTrue(result["manual_landing_required"])
        self.assertTrue(result["control_released_to_rc"])
        stream.close.assert_called_once()

    def test_later_failure_preserves_first_photo_but_not_full_route_success(self):
        result, client, logger, stream, legs = self.run_patrol(fail_at=2)
        self.assertEqual(result["visited_ids"], [6, 1, 2, 3], result)
        self.assertFalse(result["route_completed"])
        self.assertFalse(result["full_route_completed"])
        self.assertEqual(legs, [(3, 2)])
        self.assertEqual(result["pair_capture"]["tag_id"], 1)
        self.assertEqual(logger.save_confirmation_photo.call_count, 3)
        self.assertEqual(client.calls.count("disarm"), 1)
        stream.close.assert_called_once()

    def test_plan_distinguishes_pair_only_from_continued_full_patrol(self):
        full = shuttle.plan(profile(target_height_m=1.5), REFERENCE, continue_patrol=True)
        self.assertEqual(full["active_route_ids"], [6, 1, 2, 3, 2, 1, 6])
        self.assertEqual(len(full["legs"]), 6)
        self.assertEqual(full["profile"]["max_tilt_deg"], .6)
        self.assertEqual(full["finish"], "hover_at_ID6_release_to_RC_manual_landing")
        pair = shuttle.plan(profile(), REFERENCE)
        self.assertEqual(pair["active_route_ids"], [6, 1])
        self.assertEqual(pair["finish"], "hover_at_ID1_release_to_RC_manual_landing")

    def test_continue_requires_pair_mode_before_any_client(self):
        with patch.object(shuttle, "ShuttleClient") as client:
            with self.assertRaises(ValueError):
                shuttle.run(config(), profile(), continue_patrol=True)
            client.assert_not_called()
        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as error:
            shuttle.main(["--continue-patrol"])
        self.assertEqual(error.exception.code, 2)
