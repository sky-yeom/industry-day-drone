"""Official VS handover transitions over an in-memory protocol connection."""
from contextlib import ExitStack
import json
from types import SimpleNamespace
import threading
from unittest.mock import patch
import unittest

from test_standalone_tag_shuttle import StandaloneTestCase, config, profile, shuttle
from test_id1_pair_integration import AckSocket, airborne_raw
from drone_nav.tool_control.live import DeadlineTransport


class ArmTransitionTests(StandaloneTestCase):
    def setup_client(self, clock, status_samples):
        client = shuttle.ShuttleClient(shuttle.configure_execution(config(), profile()),
                                       threading.Event(), lambda snapshot: None)
        client.owner, client.phase = threading.get_ident(), "takeoff"
        client.stream = SimpleNamespace(last_detection_snapshot=SimpleNamespace(key=(1, 1), received_s=clock[0]),
                                        read=lambda: (True, None, .01))
        history = []
        test = self
        class TransitionWire(AckSocket):
            def __init__(self):
                super().__init__(clock, airborne_raw())
                self.index = 0

            def sendall(self, data):
                request = json.loads(data)
                if request["type"] == "arm":
                    self.raw = airborne_raw(vs_enabled=False, vs_advanced_enabled=False, vs_authority="RC")
                elif request["type"] == "status":
                    self.raw = airborne_raw(**status_samples[min(self.index, len(status_samples)-1)])
                    self.index += 1
                    history.append((clock[0], client.phase, client._armed))
                    if client.phase == "arming":
                        client.stream.last_detection_snapshot.received_s = clock[0]
                        with test.assertRaises(PermissionError):
                            client._guard_dispatch("attitude", {"forward_tilt_deg": 0., "right_tilt_deg": 0.,
                                                                "up_mps": .1, "yaw_rate_rps": 0.})
                elif request["type"] == "disarm":
                    self.raw = airborne_raw(armed=False, vs_enabled=False, vs_advanced_enabled=False,
                                            vs_authority="RC")
                super().sendall(data)
        wire = TransitionWire()
        client._socket = client._file = DeadlineTransport(wire)
        return client, wire, history

    def timing(self, clock):
        stack = ExitStack()
        stack.enter_context(patch.object(shuttle.time, "monotonic", lambda: clock[0]))
        stack.enter_context(patch.object(shuttle.time, "perf_counter", lambda: clock[0]))
        stack.enter_context(patch.object(shuttle.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0]+seconds)))
        return stack

    def test_delayed_vs_and_authority_are_polled_after_exactly_one_arm(self):
        clock = [100.]
        with self.timing(clock):
            client, wire, history = self.setup_client(clock, [
                {"vs_enabled": True, "vs_advanced_enabled": True, "vs_authority": "RC"},
                {"vs_enabled": True, "vs_advanced_enabled": True, "vs_authority": "MSDK"}])
            client.arm("offline-fixture-token")
        kinds = [request["type"] for request in wire.writes]
        self.assertEqual(kinds, ["arm", "status", "status"])
        self.assertTrue(client._armed)
        self.assertEqual(client.phase, "takeoff")
        self.assertEqual(client.raw["vs_authority"], "MSDK")
        self.assertTrue(all(phase == "arming" and armed is False for _, phase, armed in history))
        self.assertFalse(any(kind in {"attitude", "zero", "takeoff"} for kind in kinds))

    def test_authority_timeout_does_not_retry_arm_and_allows_cleanup(self):
        clock = [100.]
        with self.timing(clock):
            client, wire, history = self.setup_client(clock, [
                {"vs_enabled": True, "vs_advanced_enabled": True, "vs_authority": "RC"}])
            with self.assertRaises((TimeoutError, RuntimeError)):
                client.arm("offline-fixture-token")
            self.assertFalse(client._armed)
            self.assertFalse(client.failed)
            self.assertGreaterEqual(clock[0], 102.)
            self.assertLess(clock[0], 102.5)
            client.cleaning = True
            client.zero()
            client.disarm()
        kinds = [request["type"] for request in wire.writes]
        self.assertEqual(kinds.count("arm"), 1)
        self.assertNotIn("attitude", kinds)
        self.assertEqual(kinds[-2:], ["zero", "disarm"])
        self.assertTrue(all(phase == "arming" and armed is False for _, phase, armed in history))

    def test_rc_input_cancels_transition_even_when_authority_becomes_msdk(self):
        clock = [100.]
        with self.timing(clock):
            client, wire, _ = self.setup_client(clock, [{"rc_override_age_ms": 100.}])
            with self.assertRaises(InterruptedError):
                client.arm("offline-fixture-token")
        self.assertFalse(client._armed)
        self.assertEqual([request["type"] for request in wire.writes], ["arm", "status"])

    def test_stale_or_false_airborne_evidence_never_confirms_arm(self):
        for changed in ({"is_flying_age_ms": 1501.}, {"is_flying": False}):
            clock = [100.]
            with self.subTest(changed=changed), self.timing(clock):
                client, wire, _ = self.setup_client(clock, [changed])
                with self.assertRaises((InterruptedError, RuntimeError)):
                    client.arm("offline-fixture-token")
                self.assertFalse(client._armed)
                self.assertEqual([request["type"] for request in wire.writes], ["arm", "status"])


if __name__ == "__main__":
    unittest.main()
