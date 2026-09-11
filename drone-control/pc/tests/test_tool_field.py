"""Field HTTP contracts with actual pair gates, fake aircraft/frames, no sockets."""
import base64
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
from email.message import Message
import io
import itertools
import json
from pathlib import Path
import shutil
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import uuid

import cv2
import numpy as np

from drone_nav.tool_control.camera import VideoBroker
from drone_nav.tool_control.field import FieldAdapter, FieldVideoStream, PairFramingGate, shuttle
from drone_nav.tool_control.live import FreshVideoStream, LiveAdapter
from drone_nav.tool_control.server import Handler, adapter_from_environment
from drone_nav.tool_control.service import CaptureMockAdapter, MissionService, MockAdapter, ToolError
from drone_nav.observation import FrameRecorder, write_image
from drone_nav.patrol import _DetectionLogger, PatrolPhase
from drone_nav.protocol import Telemetry
from drone_nav.vision import VideoSnapshot
from test_standalone_tag_shuttle import FakeClient, config, profile

ROOT = Path(__file__).resolve().parents[2]
REAL_CLIENT = shuttle.ShuttleClient
REAL_LOGGER = shuttle.ShuttleDetectionLogger
REFERENCE = json.loads(shuttle.DEFAULT_PAIR_REFERENCE.read_text(encoding="utf-8"))
SITE = {
    "schema_version": 1, "profile_id": "field-ordered-v1", "site_revision": "offline-20260910",
    "wall_ids_left_to_right": [3, 2, 1, 6], "floor_tag_id": 0, "home_tag_id": 6,
    "target_height_m": 1.5, "expected_bridge_build_id": shuttle.BUILD_ID,
    "layout_confirmed": True, "field_setup_confirmed": True,
}


class Aircraft(FakeClient):
    def __init__(self, cancel, callback):
        super().__init__()
        self.cancel, self.on_snapshot = cancel, callback
        self.video_generation, self.deadline = None, None
        self._pair_dispatch_proof = None
        self.pair_mode = False
        self.last_motion_assessment = None
        self.last_telemetry.height_m = 1.5
        self.expected, self.direction, self.leg_frame = 6, "left", 0

    def status(self, state):
        if self.cancel.is_set() and not self.cleaning:
            raise InterruptedError("Fake aircraft observes cancellation")
        super().status(state)
        self.on_snapshot({"connected": True, "ground_verified": shuttle.ground_verified(self.raw),
                          "raw_telemetry": self.raw})

    def log_event(self, event, data):
        super().log_event(event, data)
        if event == "standalone_leg":
            self.expected, self.direction, self.leg_frame = data["to"], data["direction"], 0

    def observe_frame(self, snapshot):
        REAL_CLIENT.observe_frame(self, snapshot)

    def attitude(self, *axes):
        if self.cancel.is_set():
            raise InterruptedError("Cancelled before motion")
        REAL_CLIENT._guard_dispatch(self, "attitude", dict(zip(
            ("forward_tilt_deg", "right_tilt_deg", "up_mps", "yaw_rate_rps"), axes)))
        super().attitude(*axes)


class Frames:
    def __init__(self, clock, aircraft):
        self.clock, self.aircraft = clock, aircraft
        self.last_detection_snapshot = None
        self.closed, self.sequence = False, 0
        self.frames = {}
        self.repeated_pixels = False
        self.changed_generation = False
        self.missing = False

    def detect_latest(self, detector, max_age):
        self.sequence += 1
        client = self.aircraft()
        client.leg_frame += 1
        image = np.full((360, 640, 3), 7 if self.repeated_pixels else self.sequence % 255, dtype=np.uint8)
        snapshot = VideoSnapshot(2 if self.changed_generation and self.sequence > 3 else 1,
                                 self.sequence, self.clock[0], image)
        self.frames[snapshot.key] = image
        self.last_detection_snapshot = snapshot
        x = 320. if client.expected == 6 else 576.
        tag = shuttle.PixelTag(client.expected, (x, 180.),
            ((x - 12, 168.), (x + 12, 168.), (x + 12, 192.), (x - 12, 192.)), 80., 0)
        return ([] if self.missing or client.leg_frame == 1 else [tag]), 0.

    def snapshot(self):
        return self.last_detection_snapshot

    def diagnostics(self):
        return {"state": "CLOSED" if self.closed else "STREAMING"}

    def close(self):
        self.closed = True


class FieldTests(unittest.TestCase):
    def setUp(self):
        self.workspace = ROOT / "pc" / "tests" / (".field-test-" + uuid.uuid4().hex)
        self.workspace.mkdir()
        self.addCleanup(lambda: shutil.rmtree(self.workspace))
        self.guard = patch("socket.create_connection", side_effect=AssertionError("Real sockets forbidden"))
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.site_path = self.workspace / "site.json"
        self.profile_path = self.workspace / "profile.json"
        self.reference_path = self.workspace / "reference.json"
        self.config_path = self.workspace / "config.json"
        self.site_path.write_text(json.dumps(SITE), encoding="utf-8")
        self.profile_path.write_text(json.dumps(profile(target_height_m=1.5, visit_pause_s=0.)), encoding="utf-8")
        self.reference_path.write_text(json.dumps(REFERENCE), encoding="utf-8")
        private = json.loads((ROOT / "pc" / "config.sample.json").read_text(encoding="utf-8"))
        private["network"].update(host="192.168.137.2", confirmation_token="offline-not-a-real-secret")
        private["actual_measurements_confirmed"] = True
        private["tags"] = [tag for tag in private["tags"] if tag["id"] == 0]
        private["tags"][0]["world_pose"] = None
        self.config_path.write_text(json.dumps(private), encoding="utf-8")

    def adapter(self):
        return FieldAdapter(self.site_path, self.config_path, self.profile_path, self.reference_path)

    def mission(self, order=(1, 2, 3)):
        destinations = [f"tag-{tag}" for tag in order]
        return {"mission_id": "offline-mission", "profile_id": SITE["profile_id"],
                "site_revision": SITE["site_revision"], "destination_ids": destinations,
                "visits": [{"visit_index": i, "destination_id": d} for i, d in enumerate(destinations)]}

    def harness(self, stack, adapter, cancel, *, mutate=None):
        clock, clients = [100.], []
        def factory(cfg, token, callback):
            client = Aircraft(token, callback)
            if mutate:
                mutate(client)
            clients.append(client)
            return client
        stream = Frames(clock, lambda: clients[0])
        broker = VideoBroker(lambda: stream, clock=lambda: clock[0])
        adapter.video_broker = broker
        logger = MagicMock()
        logger.save_confirmation_photo.return_value = Path("diagnostic-only.jpg")
        steps = []
        for name in ("_wait_ground_video", "_wait_takeoff_settled", "_acquire_tag", "_climb",
                     "acquire_wall_home", "_pause"):
            stack.enter_context(patch.object(shuttle, name,
                side_effect=lambda *args, _name=name, **kwargs: steps.append(_name)))
        for name, value in (
            ("ShuttleClient", factory), ("MixedDetector", MagicMock()),
            ("ShuttleDetectionLogger", lambda *args, **kwargs: logger),
            ("RateLimiter", lambda *args: SimpleNamespace(wait=lambda: clock.__setitem__(0, clock[0] + .1))),
        ):
            stack.enter_context(patch.object(shuttle, name, value))
        stack.enter_context(patch.object(shuttle.time, "monotonic", lambda: clock[0]))
        stack.enter_context(patch.object(shuttle.time, "perf_counter", lambda: clock[0]))
        stack.enter_context(patch.object(shuttle, "write_image", return_value=True))
        stack.enter_context(patch.object(LiveAdapter, "run", side_effect=AssertionError("Legacy runner forbidden")))
        stack.enter_context(patch("drone_nav.patrol._align_tv_composition",
                                 side_effect=AssertionError("Legacy PULSE framing forbidden")))
        stack.enter_context(redirect_stdout(io.StringIO()))
        return clock, clients, stream, steps

    @staticmethod
    def http(service, name, arguments=None, request_id=None, *, expected_mode=None,
             path=None, method="POST"):
        body = json.dumps({"arguments": arguments or {}, "caller_id": "field-test",
                          "request_id": request_id or uuid.uuid4().hex}).encode()
        handler = object.__new__(Handler)
        handler.connection = MagicMock()
        handler.server = SimpleNamespace(api_token="test-secret", service=service)
        handler.command, handler.path = method, path or "/tools/" + name
        handler.headers = Message()
        for key, value in (("Host", "127.0.0.1"), ("Authorization", "Bearer test-secret"),
                           ("Content-Type", "application/json"), ("Content-Length", str(len(body)))):
            handler.headers[key] = value
        if expected_mode is not None:
            for value in expected_mode if isinstance(expected_mode, list) else [expected_mode]:
                handler.headers["X-Drone-Expected-Mode"] = value
        handler.rfile = io.BytesIO(body)
        responses = []
        handler._write = lambda code, response: responses.append((code, response))
        handler._handle()
        return responses[0]

    def test_expected_mode_header_guards_tools_camera_and_lookup_before_dispatch(self):
        for mode in ("mock", "live"):
            service = SimpleNamespace(mode=mode, adapter=SimpleNamespace(mode=mode),
                call=MagicMock(return_value={"ok": True}), camera=MagicMock(return_value={"ok": True}),
                lookup_request=MagicMock(return_value={"ok": True}))
            for path, method in (("/tools/drone_execute_route", "POST"),
                                 ("/camera/start", "POST"), ("/requests/caller/request", "GET")):
                with self.subTest(mode=mode, path=path):
                    other = "live" if mode == "mock" else "mock"
                    code, response = self.http(service, "", expected_mode=other, path=path, method=method)
                    self.assertEqual(code, 409)
                    self.assertEqual(response["error"]["code"], "MODE_MISMATCH")
                    service.call.assert_not_called()
                    service.camera.assert_not_called()
                    service.lookup_request.assert_not_called()
                    for invalid in ("unknown", "", ["mock", "live"], ["mock", "mock"]):
                        code, response = self.http(service, "", expected_mode=invalid, path=path, method=method)
                        self.assertEqual(code, 400)
                        self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT")
                    for expected in (None, mode):
                        code, _ = self.http(service, "", expected_mode=expected, path=path, method=method)
                        self.assertEqual(code, 200)
                    service.call.reset_mock()
                    service.camera.reset_mock()
                    service.lookup_request.reset_mock()
            service.adapter.mode = "live" if mode == "mock" else "mock"
            code, response = self.http(service, "drone_execute_route", expected_mode=mode)
            self.assertEqual(code, 409)
            self.assertEqual(response["error"]["code"], "MODE_MISMATCH")
            service.call.assert_not_called()

    def test_constructor_and_environment_do_not_connect_or_instantiate_detector(self):
        with patch.object(shuttle, "ShuttleClient") as client, patch.object(shuttle, "MixedDetector") as detector, \
                patch("drone_nav.tool_control.field.FieldVideoStream") as stream:
            adapter = self.adapter()
            self.assertTrue(adapter.live_ready)
            self.assertEqual(set(adapter.config.tag_map), {0})
            self.assertIsNone(adapter.config.tag_map[0].world_pose)
            self.assertIsNone(adapter.video_broker.stream)
            client.assert_not_called()
            detector.assert_not_called()
            stream.assert_not_called()
        self.assertIs(type(adapter_from_environment({})), MockAdapter)
        self.assertIsInstance(adapter_from_environment({"DRONE_CONTROL_MOCK_CAPTURES": "1"}), CaptureMockAdapter)
        env = {"DRONE_CONTROL_MODE": "live", "DRONE_CONTROL_ENABLE_LIVE": "1",
               "DRONE_CONTROL_ADAPTER": "field", "DRONE_CONTROL_SITE_CONFIG": str(self.site_path),
               "DRONE_CONTROL_CONFIG_PATH": str(self.config_path),
               "DRONE_CONTROL_FIELD_PROFILE": str(self.profile_path),
               "DRONE_CONTROL_FIELD_REFERENCE": str(self.reference_path)}
        self.assertIsInstance(adapter_from_environment(env), FieldAdapter)
        with patch("drone_nav.tool_control.live.LiveAdapter") as legacy:
            adapter_from_environment({**env, "DRONE_CONTROL_ADAPTER": "legacy"})
            legacy.assert_called_once()
        for changes in ({"DRONE_CONTROL_ADAPTER": "unknown"}, {"DRONE_CONTROL_ENABLE_LIVE": "0"},
                        {"DRONE_CONTROL_MOCK_CAPTURES": "1"}):
            with self.assertRaises(SystemExit):
                adapter_from_environment({**env, **changes})

    def test_private_confirmation_and_current_reference_are_mandatory(self):
        for key, value in (("field_setup_confirmed", False), ("layout_confirmed", False),
                           ("target_height_m", 1.4), ("wall_ids_left_to_right", [3, 1, 2, 6]),
                           ("expected_bridge_build_id", "older-build")):
            self.site_path.write_text(json.dumps({**SITE, key: value}), encoding="utf-8")
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.adapter()
        self.site_path.write_text(json.dumps(SITE), encoding="utf-8")
        self.reference_path.write_text(json.dumps({**REFERENCE, "arrival_center_x_fraction": [.84, .94]}))
        with self.assertRaises(ValueError):
            self.adapter()
        self.reference_path.write_text(json.dumps(REFERENCE))
        private = json.loads(self.config_path.read_text())
        for change in ({"actual_measurements_confirmed": False},
                       {"camera": {**private["camera"], "calibrated": False}},
                       {"network": {**private["network"], "host": "127.0.0.1"}},
                       {"network": {**private["network"], "confirmation_token": "REPLACE_TOKEN"}},
                       {"tags": []}):
            self.config_path.write_text(json.dumps({**private, **change}))
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.adapter()

    def test_all_six_http_orders_use_current_pair_gate_two_frames_then_home(self):
        for order in itertools.permutations((1, 2, 3)):
            with self.subTest(order=order), ExitStack() as stack:
                adapter, cancel = self.adapter(), threading.Event()
                clock, clients, stream, steps = self.harness(stack, adapter, cancel)
                service = MissionService(self.workspace / ("route-" + "".join(map(str, order)) + ".sqlite"), adapter)
                try:
                    code, caps = self.http(service, "drone_get_capabilities")
                    self.assertEqual(code, 200)
                    self.assertEqual(caps["adapter"], "field")
                    self.assertTrue(caps["expected_mode_guard"])
                    self.assertEqual(caps["target_height_m"], 1.5)
                    self.assertEqual(len(caps["supported_ordered_sequences"]), 6)
                    args = {key: self.mission(order)[key] for key in
                            ("profile_id", "site_revision", "destination_ids")}
                    code, response = self.http(service, "drone_execute_route", args, "same-admission")
                    self.assertEqual(code, 200)
                    mid = response["mission"]["mission_id"]
                    for _ in range(2000):
                        with service.lock:
                            done = service.running_id is None and service.pending is None
                        if done:
                            break
                        time.sleep(.002)
                    self.assertTrue(done, "Fake worker did not complete")
                    _, current = self.http(service, "drone_get_mission", {"mission_id": mid})
                    mission = current["mission"]
                    self.assertEqual(mission["state"], "awaiting_rc_landing", mission)
                    self.assertEqual(mission["visited_ids"], [6, *order, 6])
                    self.assertEqual([v["destination_id"] for v in mission["visits"]], args["destination_ids"])
                    self.assertTrue(all(v["arrival_confirmed"] and len(v["capture_ids"]) == 2 for v in mission["visits"]))
                    _, response = self.http(service, "drone_get_captures", {"mission_id": mid})
                    captures = response["captures"]
                    self.assertEqual([c["destination_id"] for c in captures],
                                     [f"tag-{tag}" for tag in order for _ in range(2)])
                    self.assertEqual(len({c["sha256"] for c in captures}), 6)
                    for capture in captures:
                        self.assertEqual(capture["mission_id"], mid)
                        self.assertFalse(capture["tv_visibility_verified"])
                        self.assertFalse(capture["simulated"])
                        self.assertEqual(capture["arrival_band_fraction"], [.85, .95])
                        self.assertEqual(capture["framing_diagnostic"]["motion_valid_until_s"], None)
                        raw = base64.b64decode(capture["image_base64"])
                        actual = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                        key = capture["frame_generation"], capture["frame_id"]
                        self.assertTrue(np.array_equal(actual, stream.frames[key]))
                        self.assertLessEqual(capture["capture_evidence"]["frame_age_s"], .5)
                    client = clients[0]
                    legs = [data for name, data in client.events if name == "standalone_leg"]
                    expected_legs = list(zip([6, *order], [*order, 6]))
                    self.assertEqual([(leg["from"], leg["to"]) for leg in legs], expected_legs)
                    self.assertEqual([leg["direction"] for leg in legs],
                        [shuttle.external_direction([6, *order, 6], a, b) for a, b in expected_legs])
                    self.assertEqual(client.calls.count("takeoff"), 1)
                    self.assertEqual(client.calls.count("arm"), 1)
                    self.assertEqual(client.calls.count("disarm"), 1)
                    self.assertNotIn("land", client.calls)
                    for call in client.calls:
                        if isinstance(call, tuple) and call[0] == "attitude":
                            forward, right, up, yaw = call[1]
                            self.assertEqual((forward, up, yaw), (0., 0., 0.))
                            self.assertLessEqual(abs(right), .6)
                    self.assertLess(steps.index("_wait_takeoff_settled"), steps.index("_climb"))
                    self.assertTrue(stream.closed)
                    _, duplicate = self.http(service, "drone_execute_route", args, "same-admission")
                    self.assertEqual(duplicate["mission"]["mission_id"], mid)
                    self.assertEqual(len(clients), 1)
                    self.assertEqual(self.http(service, "drone_get_status")[0], 200)
                    self.assertEqual(self.http(service, "drone_get_sensor_snapshot")[0], 200)
                    self.assertEqual(self.http(service, "drone_stop_mission", {"mission_id": mid})[0], 200)
                finally:
                    service.close()

    def test_external_route_extension_does_not_relax_standalone_defaults(self):
        with self.assertRaises(ValueError):
            shuttle.planned_direction(6, 3)
        self.assertEqual(shuttle.external_direction([6, 3, 1, 2, 6], 3, 1), "right")
        for route in ([6, 1, 1, 3, 6], [6, 1, 2, 3], [0, 1, 2, 3, 6], [6, True, 2, 3, 6]):
            with self.assertRaises(ValueError):
                shuttle.validate_external_route(route)
        with self.assertRaises(ValueError):
            shuttle.external_direction([6, 1, 2, 3, 6], 3, 1)
        with self.assertRaises(ValueError):
            shuttle.run(config(), profile(), external_route=[6, 3, 2, 1, 6])
        with self.assertRaises(ValueError):
            shuttle.load_config(self.config_path)

    def test_rightward_field_gate_uses_continuous_reverse_correction_not_pulse(self):
        gate = PairFramingGate(REFERENCE, "right", [.85, .95], tag_id=2)
        observed = shuttle.PixelTag(2, (450., 180.),
            ((438., 168.), (462., 168.), (462., 192.), (438., 192.)), 80., 0)
        self.assertEqual(gate.update([observed], 100., 0., (1, 1), (360, 640), 0., 0.)[0], 0.)
        for ordinal, now in enumerate((100.7, 100.9, 101.3), 2):
            right, confirmed = gate.update([observed], now, 0., (1, ordinal), (360, 640), 0., 0.)
            self.assertGreaterEqual(right, -.6)
            self.assertLessEqual(right, -.25)
            self.assertIsNone(confirmed)
            self.assertIsNone(gate.diagnostic["motion_valid_until_s"])
            self.assertEqual(gate.diagnostic["state"], "CONTINUOUS_CORRECTION")

    def test_preflight_rejects_cancel_ground_motor_build_or_low_battery_before_takeoff(self):
        for fault in ("cancel", "motors", "stale_ground", "build", "battery"):
            with self.subTest(fault=fault), ExitStack() as stack:
                adapter, cancel = self.adapter(), threading.Event()
                if fault == "cancel":
                    cancel.set()
                def mutate(client):
                    if fault == "motors":
                        client.raw["are_motors_on"] = True
                    elif fault == "stale_ground":
                        client.raw["is_flying_age_ms"] = 501
                    elif fault == "build":
                        client.raw["bridge_build_id"] = "old"
                    elif fault == "battery":
                        client.last_telemetry.battery_percent = 29.
                _, clients, stream, _ = self.harness(stack, adapter, cancel, mutate=mutate)
                result = adapter.run(self.mission(), cancel, lambda **event: None)
                self.assertFalse(result["route_completed"])
                self.assertNotIn("takeoff", clients[0].calls)
                self.assertNotIn("arm", clients[0].calls)
                self.assertIsNone(adapter.video_broker.stream)
                if fault == "cancel":
                    self.assertNotIn("connect", clients[0].calls)

    def test_stop_after_first_capture_never_moves_to_next_visit_or_replays(self):
        with ExitStack() as stack:
            adapter, cancel, events = self.adapter(), threading.Event(), []
            _, clients, stream, _ = self.harness(stack, adapter, cancel)
            def emit(**event):
                events.append(event)
                if "capture" in event:
                    cancel.set()
            result = adapter.run(self.mission(), cancel, emit)
            self.assertFalse(result["route_completed"])
            self.assertEqual(len([e for e in events if "capture" in e]), 1)
            self.assertEqual([e["visit_index"] for e in events if e.get("visit_state") == "moving"], [0])
            self.assertEqual(clients[0].calls.count("takeoff"), 1)
            self.assertEqual(clients[0].calls.count("disarm"), 1)
            self.assertTrue(stream.closed)
            self.assertTrue(result["manual_landing_required"])

    def test_generation_stale_encoding_velocity_or_missing_capture_stops_without_retry(self):
        for fault in ("generation", "encoding_age", "encoding_size", "nan_velocity", "repeated_pixels", "missing"):
            with self.subTest(fault=fault), ExitStack() as stack:
                adapter, cancel, events = self.adapter(), threading.Event(), []
                clock, clients, stream, _ = self.harness(stack, adapter, cancel)
                stream.changed_generation = fault == "generation"
                stream.repeated_pixels = fault == "repeated_pixels"
                stream.missing = fault == "missing"
                if fault in {"encoding_age", "encoding_size", "nan_velocity"}:
                    encode = cv2.imencode
                    def stale_encoder(*args):
                        data = encode(*args)
                        if fault == "encoding_age":
                            clock[0] += .501
                        elif fault == "encoding_size":
                            return True, np.zeros(4 * 1024 * 1024 + 1, np.uint8)
                        else:
                            clients[0].last_telemetry.velocity_down_mps = float("nan")
                        return data
                    stack.enter_context(patch("cv2.imencode", side_effect=stale_encoder))
                result = adapter.run(self.mission(), cancel, lambda **event: events.append(event))
                self.assertFalse(result["route_completed"], result)
                self.assertEqual(len([e for e in events if "capture" in e]), 1 if fault == "repeated_pixels" else 0)
                self.assertEqual(clients[0].calls.count("takeoff"), 1)
                self.assertEqual(clients[0].calls.count("arm"), 1)
                self.assertTrue(stream.closed)

    def test_shared_preview_survives_mission_and_preview_stop_cannot_close_owned_stream(self):
        with ExitStack() as stack:
            adapter, cancel = self.adapter(), threading.Event()
            clock, clients, stream, _ = self.harness(stack, adapter, cancel)
            broker = adapter.video_broker
            broker.lease_seconds = 100
            broker.camera("start", "viewer-one")
            self.assertIs(broker.stream, stream)
            def emit(**event):
                if event.get("visit_state") == "moving":
                    broker.camera("stop", "viewer-one")
                    self.assertFalse(stream.closed)
                    broker.camera("start", "viewer-two")
            result = adapter.run(self.mission(), cancel, emit)
            self.assertTrue(result["route_completed"], result)
            self.assertFalse(stream.closed)
            self.assertFalse(broker.mission)
            self.assertIs(broker.stream, stream)
            broker.camera("stop", "viewer-two")
            self.assertTrue(stream.closed)
            broker.close()

    def test_detection_snapshot_uses_exact_undistorted_pixels_without_altering_preview(self):
        raw, undistorted = np.zeros((20, 30, 3), np.uint8), np.ones((20, 30, 3), np.uint8)
        snapshot = VideoSnapshot(1, 2, time.monotonic(), raw)
        stream = object.__new__(FieldVideoStream)
        detector = SimpleNamespace(last_input_frame=raw, last_detection_frame=undistorted)
        def detected(*args):
            stream.last_detection_snapshot = snapshot
            return ["wall"], 0.
        with patch.object(FreshVideoStream, "detect_latest", side_effect=detected):
            self.assertEqual(stream.detect_latest(detector, .5)[0], ["wall"])
            self.assertIs(stream.last_detection_snapshot.frame, undistorted)
            self.assertIs(snapshot.frame, raw)
            self.assertEqual(stream.last_detection_snapshot.key, snapshot.key)
            detector.last_input_frame = undistorted
            with self.assertRaises(RuntimeError):
                stream.detect_latest(detector, .5)

    def test_unicode_png_writer_preserves_pixels_and_propagates_failures(self):
        root = self.workspace / "문서 촬영 증거"
        root.mkdir()
        frame = np.arange(32 * 32 * 3, dtype=np.uint8).reshape(32, 32, 3)
        path = root / "카메라 프레임.png"
        with patch("cv2.imwrite", side_effect=AssertionError("ANSI pathname API forbidden")):
            self.assertTrue(write_image(path, frame))
        self.assertTrue(path.is_file())
        decoded = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        self.assertTrue(np.array_equal(decoded, frame))
        with patch("cv2.imencode", return_value=(False, None)), self.assertRaises(OSError):
            write_image(root / "인코딩 실패.png", frame)
        self.assertFalse((root / "인코딩 실패.png").exists())
        with patch.object(Path, "write_bytes", side_effect=OSError("disk unavailable")), self.assertRaises(OSError):
            write_image(root / "저장 실패.png", frame)
        with patch.object(Path, "write_bytes", return_value=1), self.assertRaises(OSError):
            write_image(root / "불완전 저장.png", frame)

    def test_actual_unicode_field_diagnostic_and_confirmation_jpegs_are_written(self):
        with ExitStack() as stack:
            adapter, cancel, events = self.adapter(), threading.Event(), []
            _, clients, stream, _ = self.harness(stack, adapter, cancel,
                mutate=lambda client: setattr(client, "last_telemetry", Telemetry(**vars(client.last_telemetry))))
            root = self.workspace / "문서 실제 촬영"
            stack.enter_context(patch.object(shuttle, "write_image", write_image))
            stack.enter_context(patch("drone_nav.patrol.FrameRecorder", return_value=MagicMock()))
            def logger_factory(client, **kwargs):
                return REAL_LOGGER(client, photo_root=root)
            stack.enter_context(patch.object(shuttle, "ShuttleDetectionLogger", logger_factory))
            stack.enter_context(patch("cv2.imwrite", side_effect=AssertionError("ANSI pathname API forbidden")))
            result = adapter.run(self.mission(), cancel, lambda **event: events.append(event))
            self.assertTrue(result["route_completed"], result)
            self.assertEqual(len([event for event in events if "capture" in event]), 6)
            saved = [(name, data) for name, data in clients[0].events
                     if name in {"id1_first_detection_image", "tag_photo_saved"}]
            self.assertEqual(len([name for name, _ in saved if name == "id1_first_detection_image"]), 3)
            self.assertGreaterEqual(len([name for name, _ in saved if name == "tag_photo_saved"]), 6)
            for name, data in saved:
                path = Path(data["photo_path" if name == "id1_first_detection_image" else "path"])
                self.assertTrue(path.is_file(), path)
                frame = stream.frames[tuple(data["frame_key"])]
                ok, expected = cv2.imencode(".jpg", frame)
                self.assertTrue(ok)
                self.assertEqual(path.read_bytes(), expected.tobytes())
            self.assertFalse(any(name in {"id1_diagnostic_image_error", "tag_photo_failed"}
                                 for name, _ in clients[0].events))

    def test_unicode_confirmation_failure_and_frame_recorder_report_real_results(self):
        root = self.workspace / "문서 기록"
        root.mkdir()
        frame = np.full((32, 32, 3), 87, np.uint8)
        snapshot = VideoSnapshot(1, 2, time.monotonic(), frame)
        capture = SimpleNamespace(last_detection_snapshot=snapshot, snapshot=lambda: snapshot,
                                  diagnostics=lambda: {"state": "STREAMING"})
        client = MagicMock()
        tag = SimpleNamespace(tag_id=1, center_px=(16, 16))
        logger = _DetectionLogger(client, photo_root=root)
        with patch.object(Path, "write_bytes", side_effect=OSError("disk unavailable")), redirect_stdout(io.StringIO()):
            self.assertIsNone(logger.save_confirmation_photo(capture, tag, phase=PatrolPhase.OUTBOUND))
        self.assertIn("tag_photo_failed", [call.args[0] for call in client.log_event.call_args_list])
        with patch("cv2.imwrite", side_effect=AssertionError("ANSI pathname API forbidden")):
            recorder = FrameRecorder(root / "관찰", capture, interval_s=.01, max_frames=1)
            recorder._thread.join(timeout=2)
            recorder.close()
        self.assertEqual(recorder.errors, 0, recorder.last_error)
        self.assertEqual(recorder.saved_frames, 1)
        self.assertTrue((root / "관찰" / "g0001_f00000002.jpg").is_file())

    def test_explicit_mock_captures_are_canonical_then_distinct_synthetic_pixels(self):
        for enabled in (False, True):
            adapter = CaptureMockAdapter() if enabled else MockAdapter()
            service = MissionService(self.workspace / f"mock-{enabled}.sqlite", adapter)
            try:
                _, caps = self.http(service, "drone_get_capabilities")
                self.assertEqual(caps.get("mock_capture_ready", False), enabled)
                self.assertTrue(caps["expected_mode_guard"])
                args = {"profile_id": caps["profile_id"], "site_revision": caps["site_revision"],
                        "destination_ids": ["tag-3", "tag-1", "tag-2"]}
                _, response = self.http(service, "drone_execute_route", args)
                mid = response["mission"]["mission_id"]
                for _ in range(1000):
                    _, response = self.http(service, "drone_get_mission", {"mission_id": mid})
                    if response["mission"]["state"] == "completed":
                        break
                    time.sleep(.002)
                self.assertEqual(response["mission"]["state"], "completed")
                self.assertFalse(response["physical_execution"])
                _, response = self.http(service, "drone_get_captures", {"mission_id": mid})
                captures = response["captures"]
                self.assertEqual(len(captures), 6 if enabled else 0)
                if enabled:
                    for index, tag in enumerate((3, 1, 2)):
                        first, second = captures[index * 2:index * 2 + 2]
                        fixture = ROOT.parent / "public" / "monitors" / f"monitor-{tag}.png"
                        self.assertEqual(base64.b64decode(first["image_base64"]), fixture.read_bytes())
                        self.assertNotEqual(first["sha256"], second["sha256"])
                        pixels = [cv2.imdecode(np.frombuffer(base64.b64decode(item["image_base64"]),
                                  np.uint8), cv2.IMREAD_UNCHANGED) for item in (first, second)]
                        self.assertFalse(np.array_equal(*pixels))
                        self.assertTrue(np.array_equal(pixels[0][32:], pixels[1][32:]))
                        for capture in (first, second):
                            self.assertTrue(capture["simulated"])
                            self.assertEqual(capture["capture_source"], "synthetic_fixture")
                            self.assertEqual(capture["visit_index"], index)
                            self.assertEqual(capture["destination_id"], f"tag-{tag}")
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
