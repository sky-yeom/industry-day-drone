"""Home admission uses a visible tag, with no motion or point centering."""
from contextlib import redirect_stdout
import io
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import unittest

from test_standalone_tag_shuttle import FakeClient, StandaloneTestCase, config, tag, shuttle


SHAPE = (1080, 1920, 3)


class HomeVisibilityTests(StandaloneTestCase):
    def gate(self):
        return shuttle.HorizontalGate(6, "left", 949., 1.5, config().patrol, stationary_home=True)

    def test_recorded_home_positions_confirm_without_center_alignment(self):
        gate = self.gate()
        for index, (now, x) in enumerate(((0., 744.4), (.16, 765.), (.31, 783.2)), 1):
            self.assertGreater(abs(x-949.), 80.)
            right, found = gate.update([tag(6, x, 655.6)], now, .01, (1, index), SHAPE)
            self.assertEqual(right, 0.)
            if index < 3:
                self.assertIsNone(found)
        self.assertEqual(found.tag_id, 6)

    def test_corner_clipping_inside_three_percent_margin_never_confirms(self):
        for x, y in ((55., 655.6), (1900., 655.6), (765., 45.), (765., 1050.)):
            with self.subTest(center=(x, y)):
                gate = self.gate()
                for index, now in enumerate((0., .31, .62), 1):
                    self.assertEqual(gate.update([tag(6, x, y)], now, .01, (1, index), SHAPE), (0., None))

    def test_wrong_id_and_duplicate_frames_cannot_confirm_home(self):
        gate = self.gate()
        for index, now in enumerate((0., .31, .62), 1):
            self.assertEqual(gate.update([tag(1, 765., 655.6)], now, .01, (1, index), SHAPE), (0., None))
        self.assertEqual(gate.update([tag(6, 765., 655.6)], 1., .01, (1, 4), SHAPE), (0., None))
        self.assertEqual(gate.update([tag(6, 765., 655.6)], 1.4, .01, (1, 4), SHAPE), (0., None))

    def test_stale_frame_or_changed_generation_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.gate().update([tag(6, 765., 655.6)], 0., .501, (1, 1), SHAPE)
        gate = self.gate()
        gate.update([tag(6, 765., 655.6)], 0., .01, (1, 1), SHAPE)
        with self.assertRaises(InterruptedError):
            gate.update([tag(6, 765., 655.6)], .4, .01, (2, 2), SHAPE)

    def test_real_home_acquisition_sends_zero_only_at_recorded_off_center_position(self):
        client, logger, clock = FakeClient(), MagicMock(), [100.]
        client.raw.update(is_flying=True, are_motors_on=True, armed=True,
                          vs_enabled=True, vs_advanced_enabled=True, vs_authority="MSDK")
        stream = SimpleNamespace(last_detection_snapshot=None)
        def detect(_detector, _age):
            previous = stream.last_detection_snapshot
            sequence = 1 if previous is None else previous.key[1] + 1
            stream.last_detection_snapshot = SimpleNamespace(
                key=(1, sequence), received_s=clock[0], frame=SimpleNamespace(shape=SHAPE))
            return [tag(6, 783.2, 655.6)], .01
        stream.detect_latest = detect
        limiter = SimpleNamespace(wait=lambda: clock.__setitem__(0, clock[0] + .11))
        with patch.object(shuttle.time, "monotonic", lambda: clock[0]), \
                patch.object(shuttle.time, "perf_counter", lambda: clock[0]), \
                redirect_stdout(io.StringIO()):
            found = shuttle.acquire_wall_home(client, limiter, stream, None, logger, config())
        self.assertEqual(found.tag_id, 6)
        self.assertGreaterEqual(client.calls.count("zero"), 4)
        self.assertFalse(any(isinstance(call, tuple) and call[0] == "attitude" for call in client.calls))
        logger.save_confirmation_photo.assert_called_once()


if __name__ == "__main__":
    unittest.main()
