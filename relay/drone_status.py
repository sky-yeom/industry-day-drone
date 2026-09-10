"""Read-only integration status without exposing tokens or phone telemetry."""

import math
from uuid import uuid4

try:
    from .drone_client import DroneClient, DroneError
except ImportError:
    from drone_client import DroneClient, DroneError


async def read_drone_status():
    result = {
        "apiConnected": False, "executionMode": None, "physicalConnected": None,
        "liveReady": None, "homeTagId": None, "floorTagId": None, "targetHeightM": None,
        "destinations": [], "activeMissionId": None, "error": None,
    }
    try:
        client = DroneClient("relay-diagnostics-" + str(uuid4()))
        caps = await client.call("drone_get_capabilities", {})
        status = await client.call("drone_get_status", {})
        mode = caps["execution_mode"]
        destinations = caps.get("destinations")
        height = caps.get("target_height_m")
        if (status["execution_mode"] != mode or type(caps.get("live_ready")) is not bool
                or type(destinations) is not list or len(destinations) != 3
                or type(caps.get("home_tag_id")) is not int or type(caps.get("floor_tag_id")) is not int
                or type(height) not in (int, float) or not math.isfinite(height) or height <= 0
                or type(status.get("connected")) is not bool):
            raise DroneError("INVALID_RESPONSE")
        cleaned = []
        for item in destinations:
            if type(item) is not dict:
                raise DroneError("INVALID_RESPONSE")
            tag = item.get("physical_definition")
            if (type(tag) is not dict or type(tag.get("marker_id")) is not int
                    or tag.get("type") != "apriltag" or tag["marker_id"] not in (1, 2, 3)
                    or item.get("destination_id") != f"tag-{tag['marker_id']}"
                    or item.get("monitor_id") != f"monitor-{tag['marker_id']}"):
                raise DroneError("INVALID_RESPONSE")
            cleaned.append({"destination_id": item["destination_id"], "monitor_id": item["monitor_id"],
                            "physical_definition": {"type": "apriltag", "marker_id": tag["marker_id"]}})
        if len({item["destination_id"] for item in cleaned}) != 3:
            raise DroneError("INVALID_RESPONSE")
        mission = status.get("active_mission_id")
        if mission is not None and (type(mission) is not str or not 1 <= len(mission) <= 128):
            raise DroneError("INVALID_RESPONSE")
        result.update(apiConnected=True, executionMode=mode,
            physicalConnected=status["connected"] if mode == "live" else None,
            liveReady=caps["live_ready"], homeTagId=caps["home_tag_id"],
            floorTagId=caps["floor_tag_id"], targetHeightM=height,
            destinations=cleaned, activeMissionId=mission)
    except DroneError as exc:
        result["error"] = ("로컬 드론 API 연결을 확인하지 못했습니다 "
                           f"({exc.code}). PC 제어 서비스와 공유 토큰 설정을 확인하세요.")
    return result
