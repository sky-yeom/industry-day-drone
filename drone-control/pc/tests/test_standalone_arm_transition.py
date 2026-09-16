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
            self.assertGreaterEqual(clock[0], 105.)
            self.assertLess(clock[0], 105.5)
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


class AuthorityReacquireTests(StandaloneTestCase):
    """A watchdog handback is recoverable; a pilot's is not, ever."""
    timing = ArmTransitionTests.timing

    def build(self, clock, handbacks, **handback_fields):
        client = shuttle.ShuttleClient(shuttle.configure_execution(config(), profile()),
                                       threading.Event(), lambda snapshot: None)
        client.owner, client.phase = threading.get_ident(), "takeoff"
        client.stream = SimpleNamespace(last_detection_snapshot=SimpleNamespace(key=(1, 1), received_s=clock[0]),
                                        read=lambda: (True, None, .01))
        ready = airborne_raw()
        # armed=False is what the bridge reports once the aircraft disables
        # Virtual Stick, and it is also what takes the arm handoff grace out of
        # the picture so the post-check is genuinely exercised.
        handback = airborne_raw(armed=False, vs_enabled=False, vs_advanced_enabled=False,
                                vs_authority="RC", **handback_fields)
        remaining, events = [handbacks], []
        original_log = client.log_event
        client.log_event = lambda event, data: (events.append((event, data)), original_log(event, data))[1]
        class Wire(AckSocket):
            def __init__(self):
                super().__init__(clock, ready)

            def sendall(self, data):
                # Only a status outside the arm transition hands control back,
                # so every re-arm sees a healthy aircraft as it would in flight.
                if (json.loads(data)["type"] == "status"
                        and client.phase != "arming" and remaining[0]):
                    remaining[0] -= 1
                    self.raw = handback
                else:
                    self.raw = ready
                super().sendall(data)
        wire = Wire()
        client._socket = client._file = DeadlineTransport(wire)
        return client, wire, events

    @staticmethod
    def arms(wire):
        return [request["type"] for request in wire.writes].count("arm")

    def test_sdk_initiated_handback_is_undone_and_the_mission_continues(self):
        clock = [100.]
        with self.timing(clock):
            client, wire, events = self.build(clock, 1, vs_change_reason="MSDK_REQUEST")
            client.arm("offline-fixture-token")
            client.status("standalone_bounded_sonar_climb")
        self.assertTrue(client._armed)
        self.assertEqual(client.raw["vs_authority"], "MSDK")
        self.assertEqual(client.reacquisitions, 1)
        self.assertEqual(self.arms(wire), 2)
        self.assertIn("standalone_authority_reacquired", [event for event, _ in events])
        self.assertEqual(client.phase, "takeoff")

    def test_rc_override_during_handback_is_never_contested(self):
        clock = [100.]
        with self.timing(clock):
            client, wire, events = self.build(clock, 1, vs_change_reason="MSDK_REQUEST",
                                              rc_override_age_ms=100.)
            client.arm("offline-fixture-token")
            with self.assertRaises(InterruptedError):
                client.status("standalone_bounded_sonar_climb")
        self.assertEqual(client.reacquisitions, 0)
        self.assertEqual(self.arms(wire), 1)
        self.assertEqual(dict(events)["standalone_authority_handback"]["refusal"], "rc_override_active")

    def test_handback_without_an_sdk_reason_still_ends_the_flight(self):
        for reason in ({}, {"vs_change_reason": "RC_REQUEST"}):
            clock = [100.]
            with self.subTest(reason=reason), self.timing(clock):
                client, wire, events = self.build(clock, 1, **reason)
                client.arm("offline-fixture-token")
                with self.assertRaises(InterruptedError):
                    client.status("standalone_bounded_sonar_climb")
                self.assertEqual(client.reacquisitions, 0)
                self.assertEqual(self.arms(wire), 1)
                self.assertEqual(dict(events)["standalone_authority_handback"]["refusal"],
                                 "reason_not_sdk_initiated")

    def test_repeated_handbacks_stop_at_the_reacquire_limit(self):
        clock = [100.]
        with self.timing(clock):
            client, wire, events = self.build(clock, 9, vs_change_reason="MSDK_REQUEST")
            client.arm("offline-fixture-token")
            with self.assertRaises(InterruptedError):
                client.status("standalone_bounded_sonar_climb")
        self.assertEqual(client.reacquisitions, shuttle.AUTHORITY_REACQUIRE_LIMIT)
        self.assertEqual(self.arms(wire), 1 + shuttle.AUTHORITY_REACQUIRE_LIMIT)
        self.assertEqual(dict(events)["standalone_authority_handback"]["refusal"],
                         "reacquire_limit_reached")

    def test_cleanup_never_re_arms(self):
        clock = [100.]
        with self.timing(clock):
            client, wire, _ = self.build(clock, 1, vs_change_reason="MSDK_REQUEST")
            client.arm("offline-fixture-token")
            client.cleaning = True
            client.status("standalone_release_verification")
        self.assertEqual(client.reacquisitions, 0)
        self.assertEqual(self.arms(wire), 1)


class ArmSettleTests(StandaloneTestCase):
    """The controller keeps the vertical axis for a moment after the handover.

    Field logs split six flights on this with no overlap: every climb commanded
    within 0.82s of the authority callback was discarded while the aircraft sat
    at takeoff height, and both that waited longer moved on the first command.
    """
    timing = ArmTransitionTests.timing
    setup_client = ArmTransitionTests.setup_client

    def armed(self, clock):
        client, wire, _ = self.setup_client(clock, [
            {"vs_enabled": True, "vs_advanced_enabled": True, "vs_authority": "MSDK"}])
        client.arm("offline-fixture-token")
        return client, wire

    def test_the_climb_waits_out_the_handover_while_commanding_a_hold(self):
        clock = [100.]
        with self.timing(clock):
            client, wire = self.armed(clock)
            armed_at = client._armed_at
            self.assertIsNotNone(armed_at)
            before = len(wire.writes)
            shuttle._settle_after_arm(client)
        self.assertGreaterEqual(clock[0] - armed_at, shuttle.ARM_SETTLE_S)
        held = [request["type"] for request in wire.writes[before:]]
        # Virtual Stick lapses at about a second of silence, so the wait is
        # spent commanding a hold rather than spent quiet.
        self.assertIn("zero", held)
        self.assertNotIn("attitude", held)

    def test_a_controller_that_already_settled_is_not_made_to_wait(self):
        clock = [100.]
        with self.timing(clock):
            client, wire = self.armed(clock)
            clock[0] = client._armed_at + shuttle.ARM_SETTLE_S
            settled_at, before = clock[0], len(wire.writes)
            shuttle._settle_after_arm(client)
        self.assertEqual(clock[0], settled_at)
        self.assertEqual(wire.writes[before:], [])

    def test_re_arming_restarts_the_settle_clock(self):
        clock = [100.]
        with self.timing(clock):
            client, _ = self.armed(clock)
            first = client._armed_at
            clock[0] += 60.
            # The handover runs again, so the aircraft discards a vertical
            # setpoint again; a resumed climb has to wait it out again.
            client._armed = False
            client.arm("offline-fixture-token")
        self.assertGreater(client._armed_at, first)

    def test_a_failed_arm_leaves_no_settle_clock(self):
        clock = [100.]
        with self.timing(clock):
            client, _, _ = self.setup_client(clock, [
                {"vs_enabled": True, "vs_advanced_enabled": True, "vs_authority": "RC"}])
            with self.assertRaises((TimeoutError, RuntimeError)):
                client.arm("offline-fixture-token")
        self.assertIsNone(client._armed_at)


if __name__ == "__main__":
    unittest.main()
