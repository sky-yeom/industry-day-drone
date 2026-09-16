"""Explicit HTTP adapter for the latest standalone field shuttle helpers.

Construction validates local facts only. Wall coordinates remain image-only;
the independently confirmed private floor size is the sole metric tag input.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
from dataclasses import replace
from pathlib import Path
import sys
import threading
import time
import uuid

from .camera import VideoBroker
from .live import BUILD_ID, FreshVideoStream, LiveAdapter
from .service import identifier

TRIALS = Path(__file__).resolve().parents[3] / "trials"
if str(TRIALS) not in sys.path:
    sys.path.insert(0, str(TRIALS))
import standalone_tag_shuttle as shuttle
from id1_pair_framing import PairFramingGate

MAX_CAPTURE_BYTES = 4 * 1024 * 1024
SITE_FIELDS = {
    "schema_version", "profile_id", "site_revision", "wall_ids_left_to_right",
    "floor_tag_id", "home_tag_id", "expected_bridge_build_id",
    "layout_confirmed", "field_setup_confirmed",
}


class FieldVideoStream(FreshVideoStream):
    """Expose exactly the undistorted pixels used by MixedDetector's wall pass.

    Preview still reads the broker's original immutable decoded snapshot. Only
    the mission detection snapshot changes; frame identity/time are preserved.
    """
    def detect_latest(self, detector, max_age_s):
        if getattr(self, "_field_detector", None) is not detector:
            self._detection_key = None
            self._field_detector = detector
        tags, age = super().detect_latest(detector, max_age_s)
        snapshot = self.last_detection_snapshot
        if snapshot is not None and 0 <= age <= max_age_s:
            if getattr(detector, "last_input_frame", None) is not snapshot.frame:
                raise RuntimeError("Wall detection pixels do not match the decoded snapshot")
            pixels = getattr(detector, "last_detection_frame", None)
            if pixels is None or pixels.shape != snapshot.frame.shape:
                raise RuntimeError("Full undistorted detection frame is required")
            self.last_detection_snapshot = replace(snapshot, frame=pixels)
        return tags, age


class FieldAdapter(LiveAdapter):
    # Only LiveAdapter's read-only telemetry cache is reused, never its runner,
    # capture routine, metric wall detector or legacy framing correction.
    adapter_name = "field"
    mode = "live"
    destination_ids = ["tag-1", "tag-2", "tag-3"]
    home_tag_id, floor_tag_id = 6, 0
    # The ascent target is a control tunable, so it lives in the flight profile
    # beside the tilt, hold and timeout values it has to agree with, and nowhere
    # else. The site states which profile it expects, not how high that profile
    # flies, because a number written down twice is a number that drifts. The
    # value must still land inside the band the bounded climb law was validated
    # for, so editing the profile cannot quietly fly a height the climb
    # controller will refuse mid-ascent.
    target_height_band_m = (1.4, 1.6)

    def __init__(self, site_path, config_path, profile_path, reference_path):
        site = json.loads(Path(site_path).read_text(encoding="utf-8-sig"))
        if (not isinstance(site, dict) or set(site) != SITE_FIELDS
                or type(site["schema_version"]) is not int or site["schema_version"] != 2
                or site["layout_confirmed"] is not True or site["field_setup_confirmed"] is not True):
            raise ValueError("Private field site requires explicit layout and this-PC setup confirmation")
        low, high = self.target_height_band_m
        if (site["wall_ids_left_to_right"] != [3, 2, 1, 6]
                or any(type(tag) is not int for tag in site["wall_ids_left_to_right"])
                or type(site["floor_tag_id"]) is not int or site["floor_tag_id"] != 0
                or type(site["home_tag_id"]) is not int or site["home_tag_id"] != 6
                or site["expected_bridge_build_id"] != BUILD_ID):
            raise ValueError(f"Field site requires {BUILD_ID}, floor0, Home6 "
                             f"and left-to-right [3,2,1,6]")
        self.profile_id = identifier(site["profile_id"])
        self.site_revision = identifier(site["site_revision"])
        self.profile = shuttle.load_profile(profile_path)
        if self.profile["profile_id"] != self.profile_id:
            raise ValueError(f"Site expects profile {self.profile_id} but {profile_path} "
                             f"is {self.profile['profile_id']}")
        self.target_height_m = self.profile["target_height_m"]
        if not low <= self.target_height_m <= high:
            raise ValueError(f"Profile climbs to {self.target_height_m:g}m, outside the "
                             f"{low:g}-{high:g}m band the climb controller was validated for")
        self.reference = json.loads(Path(reference_path).read_text(encoding="utf-8-sig"))
        band = (self.reference.get("arrival_center_x_fraction")
                if isinstance(self.reference, dict) else None)
        # The band is a site tunable, so the adapter checks its shape and lets the
        # gate validate the fractions; a reference without it is a pre-edge-band file.
        if not isinstance(band, (list, tuple)) or len(band) != 2:
            raise ValueError("Field HTTP adapter requires arrival_center_x_fraction, an ordered "
                             "edge arrival band such as [0.85, 0.95]")
        self.arrival_band = list(band)
        PairFramingGate(self.reference, arrival_band=self.arrival_band,
                        layout=self.profile["wall_ids_left_to_right"])
        self.config = shuttle.configure_execution(
            shuttle.load_config(config_path, image_only_walls=True), self.profile)
        address = ipaddress.ip_address(self.config.network.host)
        if (not address.is_private or address.is_loopback or address.is_unspecified
                or address.is_multicast or not self.config.actual_measurements_confirmed):
            raise ValueError("Confirmed private floor/calibration measurements and actual private phone IP required")
        self.site, self.live_ready = site, True
        self.lock = threading.RLock()
        self.busy = False
        self.cache, self.cache_at = {"connected": False, "ground_verified": False}, 0.
        self.video_broker = VideoBroker(lambda: FieldVideoStream(
            self.config.network.host, self.config.network.video_port,
            self.config.network.video_codec, initial_keyframe_timeout_s=15.))

    def status(self):
        # Prevent an independent status connection racing mission admission.
        # Snapshot callbacks use the same reentrant lock on the owning thread.
        with self.lock:
            return super().status()

    @staticmethod
    def _capture_proof(client, stream, snapshot):
        if client.cancel.is_set():
            raise InterruptedError("Cancelled before capture publication; no resume")
        if client.deadline is not None and time.perf_counter() >= client.deadline:
            raise InterruptedError("Mission deadline expired before capture publication")
        shuttle._require_flight(client)
        client.observe_frame(snapshot)
        if stream.last_detection_snapshot is not snapshot:
            raise InterruptedError("Framing snapshot changed before capture publication")
        telemetry = client.last_telemetry
        elapsed = time.perf_counter() - client.received
        velocity = (() if telemetry is None else
                    (telemetry.velocity_north_mps, telemetry.velocity_east_mps, telemetry.velocity_down_mps))
        age = None if telemetry is None or telemetry.velocity_age_s is None else telemetry.velocity_age_s + elapsed
        if (not shuttle._number(age, 0., .5) or len(velocity) != 3
                or not all(shuttle._number(v, -.08, .08) for v in velocity)
                or not shuttle._number((velocity[0] ** 2 + velocity[1] ** 2) ** .5, 0., .08)):
            raise InterruptedError("Fresh finite stationary velocity required for capture")
        if (not shuttle._number(telemetry.height_m, .5, 1.8)
                or telemetry.height_age_s is None
                or not shuttle._number(telemetry.height_age_s + elapsed, 0., .5)
                or not shuttle._number(telemetry.battery_percent, 30., 100.)):
            raise InterruptedError("Capture is outside the fresh height/battery safety envelope")
        if telemetry.rc_override_age_s is not None and 0 <= telemetry.rc_override_age_s < 5:
            raise InterruptedError("RC override before capture; no resume")
        return {"velocity_north_mps": velocity[0], "velocity_east_mps": velocity[1],
                "velocity_down_mps": velocity[2], "velocity_age_s": age,
                "frame_age_s": time.monotonic() - snapshot.received_s}

    def run(self, mission, cancel, emit):
        destinations = mission["destination_ids"]
        if (not isinstance(destinations, list) or len(destinations) != 3
                or set(destinations) != set(self.destination_ids)):
            raise ValueError("Select each registered destination exactly once")
        route = shuttle.validate_external_route([6, *(int(d[-1]) for d in destinations), 6])
        if mission.get("profile_id") != self.profile_id or mission.get("site_revision") != self.site_revision:
            raise ValueError("Field profile/site revision mismatch")
        if [(v["visit_index"], v["destination_id"]) for v in mission["visits"]] != list(enumerate(destinations)):
            raise ValueError("Mission visits must preserve the selected destination order")
        with self.lock:
            if self.busy:
                raise RuntimeError("Field control owner already active")
            self.busy = True
        captured = {i: [] for i in range(3)}

        def capture(index, client, stream, snapshot, tag, diagnostic):
            if (tag.tag_id != route[index + 1]
                    or diagnostic.get("arrival_policy") != "tag_right_edge_band"
                    or list(diagnostic.get("arrival_band_fraction") or []) != self.arrival_band
                    or diagnostic.get("arrival_ready") is not True
                    or diagnostic.get("capture_ready") is not True):
                raise RuntimeError("Latest field gate did not confirm this destination")
            self._capture_proof(client, stream, snapshot)
            import cv2
            ok, data = cv2.imencode(".png", snapshot.frame)
            if not ok or data.size > MAX_CAPTURE_BYTES:
                raise RuntimeError("Full-frame PNG encoding failed or exceeds 4MiB")
            raw = data.tobytes()
            image = base64.b64encode(raw).decode("ascii")
            digest = hashlib.sha256(raw).hexdigest()
            proof = self._capture_proof(client, stream, snapshot)
            prior = captured[index]
            if len(prior) >= 2:
                raise RuntimeError("Only two distinct captures per visit are allowed")
            if prior:
                key, previous_digest = prior[0]
                if snapshot.key[0] != key[0] or snapshot.key[1] <= key[1]:
                    raise InterruptedError("Capture frame identity did not advance")
                if digest == previous_digest:
                    return False  # Another decoded frame is needed, not a duplicate image.
            payload = {
                "capture_id": uuid.uuid4().hex, "mission_id": mission["mission_id"],
                "visit_index": index, "destination_id": destinations[index],
                "monitor_id": "monitor-" + destinations[index][-1], "arrival_confirmed": True,
                "image_base64": image, "content_type": "image/png", "sha256": digest,
                "captured_at_unix_ms": int(time.time() * 1000), "simulated": False,
                "capture_source": "pc_undistorted_camera_frame", "frame_generation": snapshot.key[0],
                "frame_id": snapshot.key[1], "frame_decoded_pc_monotonic_s": snapshot.received_s,
                "aircraft_exposure_timestamp_available": False, "tv_visibility_verified": False,
                "framing_mode": "field_tag_right_edge_band", "arrival_policy": "tag_right_edge_band",
                "arrival_band_fraction": list(self.arrival_band), "predicted_footprint_policy": "diagnostic_only",
                "framing_diagnostic": diagnostic, "capture_evidence": proof,
            }
            if not prior:
                emit(visit_index=index, visit_state="arrived", arrival_confirmed=True)
            # Emitting arrival may persist to disk; re-age before publishing bytes.
            payload["capture_evidence"] = self._capture_proof(client, stream, snapshot)
            emit(visit_index=index, capture=payload, visit_state="captured")
            prior.append((snapshot.key, digest))
            return True

        try:
            result = shuttle.run(self.config, self.profile, cancel,
                pair_reference=self.reference, external_route=route, video_broker=self.video_broker,
                on_snapshot=self._snapshot, on_event=emit, on_capture=capture, capture_count=2)
            if result["route_completed"] and any(len(items) != 2 for items in captured.values()):
                result.update(route_completed=False, state="outcome_unknown",
                              error="Field runner returned without two distinct captures per visit")
            result["verification_pending"] = not result.get("ground_verified", False)
            return result
        finally:
            with self.lock:
                self.busy = False
