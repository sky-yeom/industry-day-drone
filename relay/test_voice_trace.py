import base64
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from relay import config, server
from relay.survey import SurveySession
from relay.test_mission_runner import FakeCamera, FakeVision
from relay.test_server import Browser, participant_turn
from relay.voice_trace import VoiceTrace, validate_directory


class TraceTests(unittest.TestCase):
    def test_private_trace_redacts_known_credentials_and_does_not_store_audio(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"TRACE_TEST_TOKEN": "private-test-secret"}):
            trace = VoiceTrace(directory, "trace-test", "127.0.0.1")
            trace.record("text", transcript="불난 집이요 private-test-secret",
                         arguments={"token": "not-for-log"})
            trace.last_audio -= 2
            audio = base64.b64encode(struct.pack("<hh", 1200, -2400)).decode()
            trace.audio_forwarded(audio)
            trace.close()
            text = trace.path.read_text(encoding="utf-8")
            records = [json.loads(line) for line in text.splitlines()]
            self.assertNotIn("private-test-secret", text)
            self.assertNotIn("not-for-log", text)
            self.assertNotIn(audio, text)
            self.assertEqual(records[-1]["peak_pcm"], 2400)
            self.assertEqual(records[1]["transcript"], "불난 집이요 [REDACTED]")

    def test_trace_bounds_and_invalid_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            for host in ("0.0.0.0", "example.com"):
                with self.assertRaises(ValueError):
                    validate_directory(directory, host)
            with patch.dict("os.environ", {"OneDrive": directory}):
                with self.assertRaises(ValueError):
                    validate_directory(str(Path(directory) / "sync"), "127.0.0.1")
            trace = VoiceTrace(directory, "bounded", "127.0.0.1")
            trace.events = 2000
            trace.record("ignored", transcript="not-written")
            self.assertIsNone(trace.file)
            self.assertIn("trace_limit_reached", trace.path.read_text("utf-8"))
            self.assertNotIn("not-written", trace.path.read_text("utf-8"))


class BridgeTraceTests(unittest.IsolatedAsyncioTestCase):
    async def test_trace_captures_actual_transcript_and_rejected_tool_without_changing_decision(self):
        with tempfile.TemporaryDirectory() as directory, patch.multiple(
                config, VOICE_TRACE_DIRECTORY=directory, HOST="127.0.0.1",
                DRONE_CONTROL_MODE="mock", DRONE_CONTROL_USE_TOOLS=False):
            session = SurveySession()
            session.confirm_prompt("초록색 티셔츠를 입은 사람")
            bridge = server.Bridge(Browser(), session, (FakeCamera(), FakeVision()))
            try:
                turn = participant_turn(bridge, "불난 집 말고")
                await bridge.send_browser({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": turn.item_id, "transcript": turn.text})
                await bridge.send_browser({
                    "type": "response.audio_transcript.done", "response_id": "response-1",
                    "transcript": "어디부터 갈까?"})
                result = await bridge.run_tool(
                    "select_stop", {"monitor": "monitor-3"}, "call-1", from_voice=True, turn=turn)
                self.assertFalse(result["ok"])
                self.assertEqual(session.state.draftRoute, [])
            finally:
                await bridge.close()
            records = [json.loads(line) for line in bridge.voice_trace.path.read_text("utf-8").splitlines()]
            tool = next(record for record in records if record["event"] == "tool_result")
            self.assertEqual(tool["participant_text"], "불난 집 말고")
            self.assertEqual(tool["arguments"], {"monitor": "monitor-3"})
            self.assertEqual(tool["rejection_code"], "stop_mismatch")
            self.assertEqual(tool["participant_item_id"], turn.item_id)
            self.assertTrue(any(record.get("transcript") == "어디부터 갈까?" for record in records))
