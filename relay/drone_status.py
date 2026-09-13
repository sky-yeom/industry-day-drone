"""Read-only integration status without exposing tokens or phone telemetry."""

import math
from uuid import uuid4

try:
    from .drone_client import DroneClient, DroneError
except ImportError:
    from drone_client import DroneClient, DroneError


def _bridge_health(status):
    raw = status.get("raw_telemetry")
    health = raw.get("bridge_health") if isinstance(raw, dict) else None
    return health if isinstance(health, dict) else None


def _aircraft_connected(status):
    if status.get("connected") is not True:
        return False
    health = _bridge_health(status)
    if health is None:
        return None
    if health.get("sdk_registered") is False:
        return False
    connected = health.get("product_connected")
    return connected if type(connected) is bool else None


def live_readiness_issue(status):
    """Use the real controller's observation, never a configured profile or TCP link alone."""
    if status.get("connected") is not True:
        return ("DRONE_DISCONNECTED", "폰의 드론 제어 연결을 확인하지 못했습니다.")
    bridge = _bridge_health(status)
    if bridge is not None:
        if bridge.get("sdk_registered") is False:
            return ("DJI_SDK_NOT_REGISTERED",
                    "PC와 폰은 연결됐지만 DJI SDK 등록이 완료되지 않았습니다. 폰 앱의 등록 상태를 확인하세요.")
        if bridge.get("product_connected") is False:
            return ("AIRCRAFT_DISCONNECTED",
                    "PC와 폰은 연결됐지만 DJI 기체 연결이 끊겼습니다. RC·기체 전원과 RC↔폰 USB 연결을 확인하세요.")
        if bridge.get("product_connected") is not True:
            return ("AIRCRAFT_CONNECTION_UNCONFIRMED",
                    "PC와 폰은 연결됐지만 DJI SDK가 기체 연결을 아직 확인하지 못했습니다. RC↔폰 USB 연결과 폰 앱의 기체 상태를 확인하세요.")
    raw = status.get("raw_telemetry")
    health = raw.get("fc_health") if isinstance(raw, dict) else None
    if isinstance(health, dict) and health.get("state") == "HANDLER_FAULT":
        return ("FLIGHT_CONTROLLER_UNAVAILABLE",
                "폰 앱의 DJI 비행제어 조회가 실패하고 있습니다. 앱·RC·기체 연결을 복구한 뒤 상태를 다시 확인하세요.")
    if status.get("ground_verified") is not True:
        return ("GROUND_UNVERIFIED",
                "현재 모터 정지·지상 상태·RC 제어권을 확인하지 못했습니다.")
    return None


async def read_drone_status():
    result = {
        "apiConnected": False, "executionMode": None, "physicalConnected": None,
        "bridgeConnected": None,
        "liveReady": None, "homeTagId": None, "floorTagId": None, "targetHeightM": None,
        "destinations": [], "activeMissionId": None, "error": None,
        "readinessIssues": [],
        "groundVerified": None,
        "readinessErrorCode": None, "readinessError": None,
    }
    try:
        client = DroneClient("relay-diagnostics-" + str(uuid4()))
        caps = await client.call("drone_get_capabilities", {})
        status = await client.call("drone_get_status", {})
        mode = caps.get("execution_mode")
        destinations = caps.get("destinations")
        height = caps.get("target_height_m")
        issues = caps.get("readiness_issues", [])
        ground = status.get("ground_verified")
        if (mode not in ("live", "mock") or status.get("execution_mode") != mode
                or type(caps.get("live_ready")) is not bool
                or type(destinations) is not list or len(destinations) != 3
                or type(caps.get("home_tag_id")) is not int or type(caps.get("floor_tag_id")) is not int
                or type(height) not in (int, float) or not math.isfinite(height) or height <= 0
                or type(status.get("connected")) is not bool
                or ground is not None and type(ground) is not bool
                or type(issues) is not list or len(issues) > 4
                or any(type(issue) is not str or issue not in {
                    "READ_ONLY_CONNECTION", "LAYOUT_NOT_CONFIRMED",
                    "FIELD_SETUP_NOT_CONFIRMED", "MEASUREMENTS_NOT_CONFIRMED"} for issue in issues)):
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
            bridgeConnected=status["connected"] if mode == "live" else None,
            physicalConnected=_aircraft_connected(status) if mode == "live" else None,
            liveReady=caps["live_ready"], homeTagId=caps["home_tag_id"],
            floorTagId=caps["floor_tag_id"], targetHeightM=height,
            readinessIssues=issues, groundVerified=ground if mode == "live" else None,
            destinations=cleaned, activeMissionId=mission)
        if mode == "live" and (issue := live_readiness_issue(status)):
            result.update(readinessErrorCode=issue[0], readinessError=issue[1])
    except DroneError as exc:
        result["error"] = ("로컬 드론 API 연결을 확인하지 못했습니다 "
                           f"({exc.code}). PC 제어 서비스와 공유 토큰 설정을 확인하세요.")
    return result
