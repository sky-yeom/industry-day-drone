"""Camera boundary tests: fake clocks/frames/transports, never a phone connection."""
import base64
import builtins
from email.message import Message
import io
import json
from pathlib import Path
import struct
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from drone_nav.tool_control.camera import (ENCODE_INTERVAL_S, MAX_BYTES, MAX_EDGE,
    MOCK_MESSAGE, FixtureStream, PreviewUnavailable, VideoBroker, encode_jpeg)
from drone_nav.tool_control.live import FreshVideoStream
from drone_nav.tool_control.server import Handler
from drone_nav.tool_control.service import MissionService, MockAdapter, TOOLS, ToolError
from drone_nav.vision import VideoSnapshot


CAMERA_FIELDS = {"state", "simulated", "frame_id", "age_ms", "content_type", "image_base64", "message"}


class FakeStream:
    def __init__(self, clock):
        self.clock = clock
        self.latest = VideoSnapshot(1, 1, clock(), object())
        self.state = "STREAMING"
        self.closes = 0

    def snapshot(self):
        return self.latest

    def diagnostics(self):
        return {"state": self.state, "error": "private phone endpoint and token must not escape"}

    def close(self):
        self.closes += 1

    def detect_latest(self, *args):
        raise AssertionError("Preview must never mutate mission detection state")

    def read(self):
        raise AssertionError("Preview must use snapshots, not mission reads")

    def next_frame(self):
        self.latest = VideoSnapshot(1, self.latest.frame_id + 1, self.clock(), object())


class BrokerTest(unittest.TestCase):
    def setUp(self):
        self.now = 10.
        self.streams = []
        self.encoded = []
        self.broker = VideoBroker(self.factory, encoder=self.encode, clock=lambda: self.now)
        self.addCleanup(self.broker.close)
        self.guard = patch("socket.create_connection", side_effect=AssertionError("No real network"))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def factory(self):
        stream = FakeStream(lambda: self.now)
        self.streams.append(stream)
        return stream

    def encode(self, frame):
        self.encoded.append(frame)
        return "image/jpeg", b"\xff\xd8fake-jpeg\xff\xd9"

    def test_startup_and_frame_without_start_never_open_a_stream(self):
        self.assertIsNone(self.broker.watcher)
        self.assertEqual(self.broker.camera("frame", "a")["state"], "stopped")
        self.assertEqual(self.broker.camera("stop", "a")["state"], "stopped")
        self.assertEqual(self.streams, [])

    def test_start_and_two_viewers_share_a_stream_and_encoded_frame(self):
        first = self.broker.camera("start", "a")
        self.assertEqual(set(first), CAMERA_FIELDS)
        self.assertEqual(first["state"], "streaming")
        self.assertFalse(first["simulated"])
        self.assertEqual(first["age_ms"], 0)
        second = self.broker.camera("start", "b")
        self.assertEqual(first, second)
        self.assertEqual(len(self.streams), 1)
        self.assertEqual(len(self.encoded), 1)
        self.assertEqual(self.broker.camera("stop", "a")["state"], "stopped")
        self.assertEqual(self.streams[0].closes, 0)
        self.assertEqual(self.broker.camera("frame", "a")["state"], "stopped")
        self.assertEqual(self.broker.camera("frame", "b")["state"], "streaming")
        self.broker.camera("stop", "b")
        self.assertEqual(self.streams[0].closes, 1)

    def test_mission_and_preview_share_in_either_start_order(self):
        for mission_first in (True, False):
            with self.subTest(mission_first=mission_first):
                if mission_first:
                    mission = self.broker.acquire_mission()
                    self.broker.camera("start", "a")
                else:
                    self.broker.camera("start", "a")
                    mission = self.broker.acquire_mission()
                self.assertIs(mission, self.streams[-1])
                with self.assertRaises(RuntimeError):
                    self.broker.acquire_mission()
                self.broker.camera("stop", "a")
                self.assertEqual(mission.closes, 0)
                self.broker.release_mission()
                self.assertEqual(mission.closes, 1)

    def test_mission_cleanup_does_not_close_a_viewed_stream(self):
        mission = self.broker.acquire_mission()
        self.broker.camera("start", "a")
        self.broker.release_mission()
        self.assertEqual(mission.closes, 0)
        self.assertEqual(self.broker.camera("frame", "a")["state"], "streaming")
        self.broker.camera("stop", "a")
        self.assertEqual(mission.closes, 1)

    def test_caller_lease_expiry_cannot_be_renewed_by_late_frame(self):
        self.broker.camera("start", "a")
        self.now += 4
        self.broker.camera("start", "b")
        self.now += 1
        self.assertEqual(self.broker.camera("frame", "a")["state"], "stopped")
        self.assertEqual(set(self.broker.viewers), {"b"})
        self.assertEqual(self.streams[0].closes, 0)
        self.now += 5
        self.broker.expire()
        self.assertEqual(self.streams[0].closes, 1)
        self.assertEqual(self.broker.camera("frame", "b")["state"], "stopped")
        self.assertEqual(len(self.streams), 1)

    def test_frame_renews_only_its_caller_not_the_mission_or_other_viewers(self):
        self.broker.camera("start", "a")
        self.broker.camera("start", "b")
        self.now += 4
        self.broker.camera("frame", "a")
        self.now += 1
        self.broker.expire()
        self.assertEqual(set(self.broker.viewers), {"a"})
        self.assertFalse(self.broker.mission)

    def test_abandoned_preview_expires_without_another_http_call(self):
        self.broker.camera("start", "a")
        self.now += 5
        deadline = time.perf_counter() + 1
        while self.streams[0].closes == 0 and time.perf_counter() < deadline:
            time.sleep(.01)
        self.assertEqual(self.streams[0].closes, 1)

    def test_expired_preview_cannot_close_mission_stream(self):
        mission = self.broker.acquire_mission()
        self.broker.camera("start", "a")
        self.now += 5
        self.broker.expire()
        self.assertEqual(self.broker.viewers, {})
        self.assertEqual(mission.closes, 0)
        self.broker.release_mission()
        self.assertEqual(mission.closes, 1)

    def test_four_viewers_are_bounded_and_start_is_idempotent_per_caller(self):
        for caller in ("a", "b", "c", "d"):
            self.assertEqual(self.broker.camera("start", caller)["state"], "streaming")
        self.assertEqual(self.broker.camera("start", "e")["state"], "unavailable")
        self.assertEqual(self.broker.camera("start", "a")["state"], "streaming")
        self.assertEqual(len(self.broker.viewers), 4)
        self.assertEqual(len(self.streams), 1)
        self.broker.camera("stop", "b")
        self.assertEqual(self.broker.camera("start", "e")["state"], "streaming")

    def test_max_five_encodes_per_second_share_cache_across_viewers(self):
        first = self.broker.camera("start", "a")
        for offset in (.01, .05, .1, .19):
            self.now = 10 + offset
            self.streams[0].next_frame()
            self.assertEqual(self.broker.camera("frame", "a")["frame_id"], first["frame_id"])
        self.assertEqual(len(self.encoded), 1)
        self.now = 10.201
        self.streams[0].next_frame()
        self.assertNotEqual(self.broker.camera("frame", "a")["frame_id"], first["frame_id"])
        self.assertEqual(len(self.encoded), 2)
        self.now = 10.45
        self.broker.camera("frame", "a")
        self.assertEqual(len(self.encoded), 2, "A duplicate snapshot is never re-encoded")

    def test_half_second_boundary_is_fresh_but_stale_or_future_frame_never_live(self):
        self.broker.camera("start", "a")
        self.now += .5
        self.assertEqual(self.broker.camera("frame", "a")["state"], "streaming")
        self.now += .001
        waiting = self.broker.camera("frame", "a")
        self.assertEqual(waiting["state"], "waiting")
        self.assertGreater(waiting["age_ms"], 500)
        self.assertIsNone(waiting["image_base64"])
        self.assertIsNone(waiting["content_type"])
        self.streams[0].latest = VideoSnapshot(1, 2, self.now + 1, object())
        waiting = self.broker.camera("frame", "a")
        self.assertEqual(waiting["state"], "waiting")
        self.assertIsNone(waiting["age_ms"])
        self.assertIsNone(waiting["image_base64"])

    def test_encoding_elapsed_time_is_part_of_freshness(self):
        def slow(frame):
            self.now += .501
            return self.encode(frame)
        self.broker.encoder = slow
        result = self.broker.camera("start", "a")
        self.assertEqual(result["state"], "waiting")
        self.assertIsNone(result["image_base64"])
        self.assertGreater(result["age_ms"], 500)

    def test_encode_contention_returns_without_frame_backlog(self):
        entered, release = threading.Event(), threading.Event()
        def slow(frame):
            entered.set()
            release.wait(2)
            return self.encode(frame)
        self.broker.encoder = slow
        worker = threading.Thread(target=lambda: self.broker.camera("start", "a"))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.now += .3
            self.streams[0].next_frame()
            result = self.broker.camera("start", "b")
            self.assertEqual(result["state"], "waiting")
            self.assertIsNone(result["image_base64"])
            self.assertEqual(self.encoded, [])
        finally:
            release.set()
            worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(self.encoded), 1)

    def test_stop_during_encode_does_not_publish_or_reopen(self):
        def stop_then_encode(frame):
            self.broker.camera("stop", "a")
            return self.encode(frame)
        self.broker.encoder = stop_then_encode
        result = self.broker.camera("start", "a")
        self.assertEqual(result["state"], "stopped")
        self.assertIsNone(result["image_base64"])
        self.assertEqual(self.streams[0].closes, 1)
        self.assertEqual(len(self.streams), 1)

    def test_video_loss_invalidates_cached_image_and_never_auto_reconnects(self):
        self.broker.camera("start", "a")
        self.streams[0].state = "FAILED"
        for action in ("frame", "start", "frame"):
            result = self.broker.camera(action, "a")
            self.assertEqual(result["state"], "unavailable")
            self.assertIsNone(result["image_base64"])
            self.assertNotIn("private", json.dumps(result))
        with self.assertRaises(RuntimeError):
            self.broker.acquire_mission()
        self.assertEqual(len(self.streams), 1)
        self.broker.camera("stop", "a")
        self.assertEqual(self.broker.camera("start", "a")["state"], "streaming")
        self.assertEqual(len(self.streams), 2)

    def test_open_failure_is_generic_and_not_retried_by_polling(self):
        factory = Mock(side_effect=OSError("private endpoint secret"))
        self.broker.factory = factory
        for action in ("start", "frame", "start"):
            result = self.broker.camera(action, "a")
            self.assertEqual(result["state"], "unavailable")
            self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(factory.call_count, 1)
        self.broker.camera("stop", "a")
        self.broker.camera("start", "a")
        self.assertEqual(factory.call_count, 2)

    def test_bad_encoding_is_unavailable_and_bounded(self):
        self.broker.encoder = lambda frame: ("image/jpeg", b"x" * (MAX_BYTES + 1))
        result = self.broker.camera("start", "a")
        self.assertEqual(result["state"], "unavailable")
        self.assertIsNone(result["image_base64"])
        self.broker.encoder = lambda frame: ("text/html", b"not an image")
        self.now += .3
        self.streams[0].next_frame()
        self.assertEqual(self.broker.camera("frame", "a")["state"], "unavailable")

    def test_programming_errors_are_not_silently_claimed_as_camera_success(self):
        self.broker.encoder = Mock(side_effect=RuntimeError("unexpected encoder bug"))
        with self.assertRaises(RuntimeError):
            self.broker.camera("start", "a")
        self.assertFalse(self.broker.encoding.locked())

    def test_close_does_not_steal_an_active_mission_stream(self):
        mission = self.broker.acquire_mission()
        self.broker.camera("start", "a")
        self.broker.close()
        self.assertEqual(mission.closes, 0)
        self.assertEqual(self.broker.camera("start", "b")["state"], "unavailable")
        self.broker.release_mission()
        self.assertEqual(mission.closes, 1)


class EncoderTest(unittest.TestCase):
    def fake_cv2(self, *, size=32, ok=True):
        encoded = SimpleNamespace(size=size, tobytes=lambda: b"\xff\xd8" + b"x" * max(0, size - 2))
        cv2 = SimpleNamespace(INTER_AREA=3, IMWRITE_JPEG_QUALITY=1, error=type("CvError", (Exception,), {}),
            resize=Mock(side_effect=lambda frame, dims, interpolation: SimpleNamespace(shape=(dims[1], dims[0], 3))),
            imencode=Mock(return_value=(ok, encoded)))
        return cv2

    def test_jpeg_is_scaled_with_bounded_size_and_quality(self):
        for dimensions in ((2160, 3840), (3840, 2160), (400, 640)):
            with self.subTest(dimensions=dimensions):
                cv2 = self.fake_cv2(size=MAX_BYTES)
                with patch.dict(sys.modules, {"cv2": cv2}):
                    content_type, data = encode_jpeg(SimpleNamespace(shape=(*dimensions, 3)))
                self.assertEqual(content_type, "image/jpeg")
                self.assertEqual(len(data), MAX_BYTES)
                args = cv2.imencode.call_args.args
                self.assertEqual(args[0], ".jpg")
                self.assertLessEqual(max(args[1].shape[:2]), MAX_EDGE)
                self.assertEqual(args[2], [cv2.IMWRITE_JPEG_QUALITY, 65])
                self.assertEqual(cv2.resize.call_count, int(max(dimensions) > MAX_EDGE))

    def test_oversized_or_failed_jpeg_is_rejected(self):
        for cv2 in (self.fake_cv2(size=MAX_BYTES + 1), self.fake_cv2(ok=False)):
            with patch.dict(sys.modules, {"cv2": cv2}), self.assertRaises(PreviewUnavailable):
                encode_jpeg(SimpleNamespace(shape=(400, 640, 3)))

    def test_optional_encoder_dependency_is_reported_unavailable(self):
        with patch.dict(sys.modules, {"cv2": None}), self.assertRaises(PreviewUnavailable):
            encode_jpeg(SimpleNamespace(shape=(400, 640, 3)))

    def test_fixture_checks_dimension_and_byte_limits(self):
        for data in (b"not-png", b"x" * (MAX_BYTES + 1),
                     b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 961, 1)):
            with patch.object(Path, "open", return_value=io.BytesIO(data)), self.assertRaises(PreviewUnavailable):
                FixtureStream("unused.png", lambda: 0)


class MockCameraServiceTest(unittest.TestCase):
    def test_default_service_mock_camera_is_lazy_labeled_local_and_has_no_extra_tools(self):
        original_import = builtins.__import__
        def import_without_vision(name, *args, **kwargs):
            if name.split(".")[0] in {"av", "cv2", "numpy", "pupil_apriltags"}:
                raise AssertionError("Default mock must not require optional vision dependencies")
            return original_import(name, *args, **kwargs)
        with patch("socket.create_connection", side_effect=AssertionError("Mock must not connect")), \
             patch("builtins.__import__", side_effect=import_without_vision):
            service = MissionService(":memory:")
            try:
                self.assertIsNone(service.camera_broker.stream)
                self.assertIsNone(service.camera_broker.watcher)
                self.assertIsNone(service._active())
                self.assertEqual(service.capabilities()["tools"], list(TOOLS))
                self.assertEqual(len(TOOLS), 7)
                response = service.camera("start", {}, "browser-preview", "start-1")
                self.assertEqual(response["schema_version"], 1)
                self.assertTrue(response["ok"])
                self.assertEqual(response["execution_mode"], "mock")
                self.assertFalse(response["physical_execution"])
                camera = response["camera"]
                self.assertEqual(set(camera), CAMERA_FIELDS)
                self.assertEqual(camera["state"], "streaming")
                self.assertTrue(camera["simulated"])
                self.assertEqual(camera["message"], MOCK_MESSAGE)
                self.assertEqual(camera["content_type"], "image/png")
                raw = base64.b64decode(camera["image_base64"], validate=True)
                fixture = Path(__file__).resolve().parents[3] / "public" / "monitors" / "monitor-1.png"
                self.assertEqual(raw, fixture.read_bytes())
                self.assertLessEqual(len(raw), MAX_BYTES)
                self.assertEqual(struct.unpack(">II", raw[16:24]), (640, 400))
                self.assertIsNone(service._active())
                self.assertIsNone(service.lease_deadline)
                self.assertEqual(service.camera("stop", {}, "browser-preview", "stop-1")["camera"]["state"], "stopped")
            finally:
                service.close()

    def test_shutdown_cancels_worker_before_closing_broker(self):
        events, started = [], threading.Event()
        broker = VideoBroker(lambda: FakeStream(time.monotonic), encoder=lambda frame: ("image/jpeg", b"jpeg"))
        original_close = broker.close
        def close():
            events.append("broker-close")
            original_close()
        broker.close = close
        class Adapter(MockAdapter):
            video_broker = broker
            def run(self, mission, cancel, emit):
                stream = broker.acquire_mission()
                started.set()
                cancel.wait(2)
                events.append("mission-cancelled")
                self.assert_not_closed = stream.closes == 0
                broker.release_mission()
                events.append("mission-cleanup")
                return {"state": "stopped", "ground_verified": True}
        adapter = Adapter()
        service = MissionService(":memory:", adapter)
        try:
            service.camera("start", {}, "viewer", "one")
            service.call("drone_execute_route", {"profile_id": adapter.profile_id,
                "site_revision": adapter.site_revision, "destination_ids": adapter.destination_ids}, "mission", "one")
            self.assertTrue(started.wait(1))
        finally:
            service.close()
        self.assertTrue(adapter.assert_not_closed)
        self.assertLess(events.index("mission-cleanup"), events.index("broker-close"))
        self.assertTrue(broker.closed)
        self.assertIsNone(broker.stream)


class CameraHttpTest(unittest.TestCase):
    """Exercise actual handler dispatch and auth without opening a listening port."""
    def setUp(self):
        self.service = MissionService(":memory:")
        self.addCleanup(self.service.close)
        self.body = {"arguments": {}, "caller_id": "relay-camera-test", "request_id": "preview-one"}

    def call(self, path="/camera/start", body=None, *, headers=None, method="POST"):
        body = self.body if body is None else body
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        handler = Handler.__new__(Handler)
        handler.server = SimpleNamespace(service=self.service, api_token="offline-test-token")
        handler.headers = Message()
        fields = {"Host": "127.0.0.1", "Authorization": "Bearer offline-test-token",
                  "Content-Type": "application/json", "Content-Length": str(len(data))}
        fields.update(headers or {})
        for key, value in fields.items():
            if value is not None:
                handler.headers[key] = value
        handler.connection = SimpleNamespace(settimeout=lambda value: None)
        handler.command, handler.path, handler.rfile = method, path, io.BytesIO(data)
        output = []
        handler._write = lambda code, response: output.append((code, response))
        handler._handle()
        return output[0]

    def test_auth_origin_and_host_validation_precede_preview_start(self):
        for headers, code in (({"Authorization": None}, 401), ({"Authorization": "Bearer wrong"}, 401),
                              ({"Origin": "http://example.invalid"}, 403), ({"Host": "example.invalid"}, 403)):
            with self.subTest(headers=headers):
                status, response = self.call(headers=headers)
                self.assertEqual(status, code)
                self.assertEqual(response["schema_version"], 1)
                self.assertFalse(response["ok"])
                self.assertIsNone(self.service.camera_broker.stream)

    def test_exact_camera_envelope_and_empty_arguments(self):
        for body in ([1], {}, {**self.body, "extra": True}, {"arguments": {}, "caller_id": "a"},
                     {**self.body, "arguments": {"motion": True}}, {**self.body, "arguments": []},
                     {**self.body, "caller_id": "../invalid"}, {**self.body, "request_id": True},
                     b'{"arguments":{},"caller_id":"a","caller_id":"b","request_id":"c"}',
                     b'{"arguments":{},"caller_id":"a","request_id":NaN}'):
            with self.subTest(body=body):
                code, response = self.call(body=body)
                self.assertEqual(code, 400)
                self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT")
                self.assertIsNone(self.service.camera_broker.stream)

    def test_rejects_wrong_actions_methods_queries_and_content_type(self):
        for path, headers, method, expected in (
                ("/camera/takeoff", {}, "POST", 400), ("/camera/start", {}, "GET", 404),
                ("/camera/frame?caller=x", {}, "POST", 400),
                ("/camera/start", {"Content-Type": "text/plain"}, "POST", 400),
                ("/camera/start", {"Transfer-Encoding": "chunked"}, "POST", 400),
                ("/camera/start", {"Content-Length": "65537"}, "POST", 400),
                ("/tools/camera_start", {}, "POST", 400)):
            with self.subTest(path=path, headers=headers, method=method):
                self.assertEqual(self.call(path, headers=headers, method=method)[0], expected)
                self.assertIsNone(self.service.camera_broker.stream)

    def test_start_frame_stop_and_unchanged_capabilities_contract(self):
        for action in ("start", "frame", "stop"):
            code, response = self.call("/camera/" + action)
            self.assertEqual(code, 200)
            self.assertTrue(response["ok"])
            self.assertEqual(set(response["camera"]), CAMERA_FIELDS)
            self.assertFalse(response["physical_execution"])
            self.assertEqual(response["camera"]["state"], "stopped" if action == "stop" else "streaming")
        _, capabilities = self.call("/tools/drone_get_capabilities")
        self.assertEqual(capabilities["tools"], list(TOOLS))
        self.assertEqual(capabilities["home_tag_id"], 6)
        self.assertEqual(capabilities["floor_tag_id"], 0)
        self.assertEqual(capabilities["target_height_m"], 1.4)
        self.assertEqual([d["monitor_id"] for d in capabilities["destinations"]],
                         ["monitor-1", "monitor-2", "monitor-3"])

    def test_shutdown_refuses_new_preview_and_allows_stop(self):
        self.service.begin_shutdown()
        code, response = self.call()
        self.assertEqual(code, 400)
        self.assertEqual(response["error"]["code"], "SERVICE_SHUTTING_DOWN")
        self.assertEqual(self.call("/camera/stop")[0], 200)

    def test_unexpected_exception_is_a_generic_error_without_private_details(self):
        with patch.object(self.service.camera_broker, "camera", side_effect=RuntimeError("private phone token")):
            code, response = self.call()
        self.assertEqual(code, 500)
        self.assertNotIn("private", json.dumps(response))
        self.assertFalse(response["ok"])


class SingleConnectionVideoTest(unittest.TestCase):
    def test_fresh_stream_eof_and_initial_connect_failure_never_reconnect(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                connection = Mock()
                connection.recv.return_value = b""
                with patch.dict(sys.modules, {"av": Mock()}), patch("drone_nav.vision.threading.Thread"), \
                     patch("drone_nav.vision.socket.create_connection",
                           side_effect=OSError("offline") if failure else None, return_value=connection) as connect:
                    stream = FreshVideoStream("unused.invalid", 1)
                    stream._decode()
                    self.assertEqual(connect.call_count, 1)
                    self.assertEqual(stream.diagnostics()["state"], "FAILED")
                    self.assertEqual(stream.diagnostics()["reconnects"], 0)
                    self.assertIsNone(stream.snapshot())
                    stream.close()


if __name__ == "__main__":
    unittest.main()
