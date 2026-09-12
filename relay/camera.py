"""Local fixture camera boundary; captures contain pixels, never remote URLs."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
import uuid
import zlib

try:
    from . import config
except ImportError:
    import config


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = ROOT / "public"
SCENARIO = json.loads((ROOT / "data" / "emergency-triage.json").read_text("utf-8"))
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class CaptureError(Exception):
    """A camera failure requiring an explicit mission pause/retry."""


@dataclass(frozen=True)
class Capture:
    id: str
    monitor_id: str
    image_bytes: bytes
    content_type: str
    mission_id: str | None = None
    visit_index: int | None = None
    destination_id: str | None = None
    captured_at_unix_ms: int | None = None

    @property
    def image_url(self) -> str:
        encoded = base64.b64encode(self.image_bytes).decode("ascii")
        return f"data:{self.content_type};base64,{encoded}"


def validate_image(image: bytes, content_type: str) -> None:
    """Bound uploads and check PNG structure without a new imaging dependency."""
    if content_type != "image/png":
        raise CaptureError("촬영 이미지는 PNG 형식이어야 합니다.")
    if not isinstance(image, bytes) or not 0 < len(image) <= config.VISION_MAX_IMAGE_BYTES:
        raise CaptureError("촬영 이미지가 비어 있거나 허용 크기 4MB를 초과했습니다.")
    if not image.startswith(PNG_SIGNATURE):
        raise CaptureError("촬영 이미지가 올바른 PNG 파일이 아닙니다.")
    offset = 8
    saw_header = saw_pixels = False
    while offset + 12 <= len(image):
        size = struct.unpack_from(">I", image, offset)[0]
        kind = image[offset + 4:offset + 8]
        end = offset + size + 12
        if end > len(image):
            break
        payload = image[offset + 8:end - 4]
        checksum = struct.unpack_from(">I", image, end - 4)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != checksum:
            break
        if not saw_header:
            if kind != b"IHDR" or size != 13:
                break
            width, height = struct.unpack_from(">II", payload)
            if not (0 < width <= 4096 and 0 < height <= 4096):
                raise CaptureError("촬영 이미지 해상도는 가로·세로 4096픽셀 이하여야 합니다.")
            saw_header = True
        elif kind == b"IHDR":
            break
        if kind == b"IDAT" and payload:
            saw_pixels = True
        if kind == b"IEND":
            if size == 0 and saw_pixels and end == len(image):
                return
            break
        offset = end
    raise CaptureError("촬영 PNG 이미지가 손상되었거나 픽셀 데이터가 없습니다.")


class FixtureCamera:
    """Only the canonical scenario's three local monitor images can be captured."""

    def _path(self, monitor_id: str) -> Path:
        person = next(
            (person for person in SCENARIO["people"] if person["monitorId"] == monitor_id),
            None,
        )
        if person is None or monitor_id not in ("monitor-1", "monitor-2", "monitor-3"):
            raise CaptureError("등록되지 않은 현장입니다. 구조 경로를 확인해 주세요.")
        expected = f"/monitors/{monitor_id}.png"
        if person["image"] != expected:
            raise CaptureError("촬영 파일 설정이 허용된 현장 PNG 경로와 다릅니다.")
        path = PUBLIC_ROOT / "monitors" / f"{monitor_id}.png"
        try:
            resolved = path.resolve()
            allowed = PUBLIC_ROOT.resolve() / "monitors" / f"{monitor_id}.png"
        except (OSError, RuntimeError) as exc:
            raise CaptureError("촬영 파일의 로컬 경로를 확인할 수 없습니다.") from exc
        if resolved != allowed:
            raise CaptureError("촬영 파일은 지정된 로컬 현장 폴더 안에 있어야 합니다.")
        return path

    def read_image(self, monitor_id: str) -> bytes:
        path = self._path(monitor_id)
        try:
            with path.open("rb") as source:
                image = source.read(config.VISION_MAX_IMAGE_BYTES + 1)
        except OSError as exc:
            raise CaptureError("현장 촬영 파일을 읽을 수 없습니다. 로컬 PNG 파일을 확인해 주세요.") from exc
        validate_image(image, "image/png")
        return image

    def readiness(self) -> str | None:
        try:
            for person in SCENARIO["people"]:
                self.read_image(person["monitorId"])
        except CaptureError as exc:
            return str(exc)
        return None

    async def capture(self, monitor_id: str) -> Capture:
        return Capture(
            id=str(uuid.uuid4()),
            monitor_id=monitor_id,
            image_bytes=self.read_image(monitor_id),
            content_type="image/png",
        )


class LiveCaptureCamera:
    """Accept only bounded, attributed PC capture bytes; never load fixture images."""

    @staticmethod
    def from_record(record, *, mission_id, visit_index, destination_id, monitor_id):
        if (type(record) is not dict or record.get("mission_id") != mission_id
                or type(record.get("visit_index")) is not int or record["visit_index"] != visit_index
                or record.get("destination_id") != destination_id or record.get("arrival_confirmed") is not True
                or type(record.get("capture_id")) is not str or not 1 <= len(record["capture_id"]) <= 128
                or record.get("content_type") != "image/png"
                or type(record.get("captured_at_unix_ms")) is not int or record["captured_at_unix_ms"] <= 0):
            raise CaptureError("촬영 결과의 임무·방문·도착 근거를 확인하지 못했습니다.")
        encoded = record.get("image_base64")
        if type(encoded) is not str or len(encoded) > 4 * ((config.VISION_MAX_IMAGE_BYTES + 2) // 3):
            raise CaptureError("실제 촬영 이미지가 없거나 허용 크기를 초과했습니다.")
        try:
            image = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise CaptureError("실제 촬영 이미지의 인코딩을 확인하지 못했습니다.") from exc
        validate_image(image, "image/png")
        if record.get("sha256") != hashlib.sha256(image).hexdigest():
            raise CaptureError("촬영 이미지 무결성 확인에 실패했습니다.")
        return Capture(record["capture_id"], monitor_id, image, "image/png",
            mission_id, visit_index, destination_id, record["captured_at_unix_ms"])

    async def capture(self, monitor_id):
        raise CaptureError("실제 비행은 해당 방문의 PC 촬영 증거를 기다려야 합니다.")
