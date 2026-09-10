"""Bounded display-only frames; no voice, image analysis, or flight commands."""

import asyncio
import base64
import contextlib
import logging
import math
from uuid import uuid4

from fastapi import WebSocketDisconnect

try:
    from .drone_client import DroneClient, DroneError
    from . import operator_access
except ImportError:
    from drone_client import DroneClient, DroneError
    import operator_access


log = logging.getLogger("relay.camera")
MAX_PREVIEW_BYTES = 512 * 1024


def camera_event(response):
    camera = response.get("camera")
    mode = response.get("execution_mode")
    if (mode not in ("mock", "live") or type(camera) is not dict
            or camera.get("state") not in ("streaming", "waiting", "stopped", "unavailable")
            or type(camera.get("simulated")) is not bool
            or camera["simulated"] != (mode == "mock")):
        raise DroneError("INVALID_CAMERA_RESPONSE")
    state = camera["state"]
    frame = None
    if state == "streaming":
        age = camera.get("age_ms")
        frame_id = camera.get("frame_id")
        kind, encoded = camera.get("content_type"), camera.get("image_base64")
        if (type(age) not in (int, float) or not math.isfinite(age) or age < 0
                or type(frame_id) is not str or not 1 <= len(frame_id) <= 128
                or kind not in ("image/png", "image/jpeg")
                or type(encoded) is not str or len(encoded) > 4 * ((MAX_PREVIEW_BYTES + 2) // 3)):
            raise DroneError("INVALID_CAMERA_RESPONSE")
        try:
            image = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise DroneError("INVALID_CAMERA_RESPONSE") from exc
        if (not 0 < len(image) <= MAX_PREVIEW_BYTES
                or (kind == "image/png" and not image.startswith(b"\x89PNG\r\n\x1a\n"))
                or (kind == "image/jpeg" and not (image.startswith(b"\xff\xd8") and image.endswith(b"\xff\xd9")))):
            raise DroneError("INVALID_CAMERA_RESPONSE")
        if age > 500:
            state = "waiting"
        else:
            frame = {"imageUrl": f"data:{kind};base64,{encoded}", "frameId": frame_id, "ageMs": age}
    messages = {
        "streaming": "모의 카메라 · 훈련 이미지" if mode == "mock" else "실제 카메라 미리보기 · 조종용 FPV 아님",
        "waiting": "새 카메라 프레임을 기다립니다. 이전 영상으로 위치를 판단하지 마세요.",
        "stopped": "카메라 미리보기가 종료되었습니다. 비행 중지 명령은 아닙니다.",
        "unavailable": "카메라 영상이 없습니다. PC 제어 서비스와 휴대폰 연결을 확인하세요.",
    }
    return {"type": "camera.state", "state": state, "mode": mode,
            "message": messages[state], "frame": frame}


async def serve_camera(browser):
    protocol = operator_access.selected_protocol(browser)
    if protocol:
        await browser.accept(subprotocol=protocol)
    else:
        await browser.accept()
    client = None
    mode = None
    tasks = []
    attempted = False
    try:
        client = DroneClient("relay-camera-" + str(uuid4()))
        await browser.send_json({"type": "camera.state", "state": "connecting", "mode": None,
                                 "message": "PC 카메라 연결 중 · 이륙하지 않습니다.", "frame": None})

        async def wait_for_disconnect():
            while True:
                message = await browser.receive()
                if message["type"] == "websocket.disconnect":
                    return

        async def publish_frames():
            nonlocal mode, attempted
            attempted = True
            response = await client.camera("start")
            while True:
                event = camera_event(response)
                mode = event["mode"]
                await browser.send_json(event)
                if event["state"] in ("stopped", "unavailable"):
                    return
                await asyncio.sleep(.2)
                response = await client.camera("frame")

        tasks = [asyncio.create_task(wait_for_disconnect()), asyncio.create_task(publish_frames())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
    except WebSocketDisconnect:
        pass
    except DroneError as exc:
        log.warning("Camera preview failed: %s", exc.code)
        try:
            await browser.send_json({"type": "camera.state", "state": "unavailable", "mode": mode,
                "message": "PC 카메라 연결에 실패했습니다. 로컬 제어 서비스와 설정을 확인하세요.", "frame": None})
        except WebSocketDisconnect:
            pass
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if attempted and client is not None:
            try:
                await client.camera("stop")
            except DroneError as exc:
                log.warning("Camera stop was not acknowledged (%s); viewer lease will expire", exc.code)
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await browser.close()
