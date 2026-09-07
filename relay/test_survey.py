"""Deterministic tests for monitor id validation and route flow."""

import unittest

from relay.survey import GROUND_TRUTH_ANOMALIES, SurveySession, resolve_monitor, score_prompt


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


class ScorePromptTests(unittest.TestCase):
    def test_matches_keywords_present_in_prompt(self):
        score = score_prompt("침수 구역이랑 균열 위주로 볼게요.")

        matched_ids = {a["id"] for a in score["matched"]}
        self.assertIn("standing-water", matched_ids)
        self.assertIn("structural-crack", matched_ids)
        self.assertEqual(score["totalGroundTruth"], len(GROUND_TRUTH_ANOMALIES))
        self.assertEqual(score["matchedCount"] + score["missedCount"], score["totalGroundTruth"])

    def test_no_keywords_scores_zero(self):
        score = score_prompt("그냥 전체적으로 한번 둘러봐 주세요.")

        self.assertEqual(score["matchedCount"], 0)
        self.assertEqual(score["accuracyPercent"], 0)


class MissionFlowTests(unittest.TestCase):
    def _confirmed_route_session(self) -> SurveySession:
        session = SurveySession()
        session.select_stop("monitor-1")
        session.select_stop("monitor-2")
        session.confirm_route()
        return session

    def test_confirm_prompt_requires_nonempty_text(self):
        session = self._confirmed_route_session()

        result = session.confirm_prompt("")

        self.assertFalse(result["ok"])
        self.assertEqual(session.mission.promptPhase, "briefing")

    def test_report_detection_requires_confirmed_prompt(self):
        session = self._confirmed_route_session()

        result = session.report_detection()

        self.assertFalse(result["ok"])
        self.assertEqual(session.mission.detectionPhase, "idle")

    def test_full_mission_flow(self):
        session = self._confirmed_route_session()

        confirmed = session.confirm_prompt("침수랑 균열 위주로 볼게요")
        detected = session.report_detection()

        self.assertTrue(confirmed["ok"])
        self.assertEqual(session.mission.promptPhase, "confirmed")
        self.assertTrue(detected["ok"])
        self.assertEqual(session.mission.detectionPhase, "complete")
        self.assertEqual(
            len(session.mission.detectedAnomalies), len(GROUND_TRUTH_ANOMALIES)
        )
        self.assertIsNotNone(session.mission.score)
        self.assertEqual(session.mission.score["totalGroundTruth"], len(GROUND_TRUTH_ANOMALIES))


if __name__ == "__main__":
    unittest.main()
