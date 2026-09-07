"""Deterministic tests for monitor id validation and route flow."""

import unittest

from relay.survey import SurveySession, resolve_monitor


class ResolveMonitorTests(unittest.TestCase):
    def test_accepts_exact_canonical_ids(self):
        for monitor_id in ("monitor-1", "monitor-2", "monitor-3"):
            with self.subTest(monitor_id=monitor_id):
                self.assertEqual(resolve_monitor(monitor_id), monitor_id)

    def test_rejects_anything_else(self):
        # The real path only ever passes a canonical id (the tool schema's
        # enum guarantees it). Natural-language phrases, partial ids, and
        # empty/None input must all be rejected rather than guessed at.
        for spoken in (
            "", None, "아무거나", "1", "monitor-4", "Monitor-1",
            "첫번째", "모니터 1", "monitor-1 ", " monitor-1",
        ):
            with self.subTest(spoken=spoken):
                self.assertIsNone(resolve_monitor(spoken))


class SurveySessionTests(unittest.TestCase):
    def test_natural_route_flow(self):
        session = SurveySession()

        first = session.select_stop("monitor-1")
        second = session.select_stop("monitor-2")
        confirmed = session.confirm_route()

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(confirmed["ok"])
        self.assertEqual(
            session.state.confirmedRoute,
            ["monitor-1", "monitor-2", "monitor-3"],
        )

    def test_ambiguous_reference_does_not_change_route(self):
        session = SurveySession()

        result = session.select_stop("아무거나")

        self.assertFalse(result["ok"])
        self.assertEqual(session.state.draftRoute, [])


if __name__ == "__main__":
    unittest.main()
