"""Real mission evidence adapter; scenario selection, deadlines and scoring stay in SurveySession."""
from __future__ import annotations

import asyncio
from uuid import uuid4

try:
    from .camera import LiveCaptureCamera
    from .drone_client import DroneError
    from .mission_runner import MissionRunner
    from .survey import ACTIVE, TERMINAL, result
except ImportError:
    from camera import LiveCaptureCamera
    from drone_client import DroneError
    from mission_runner import MissionRunner
    from survey import ACTIVE, TERMINAL, result

STATES = {"accepted", "preflight", "taking_off", "running", "returning",
    "awaiting_rc_landing", "completed", "stop_requested", "stopped", "failed", "outcome_unknown"}
TERMINAL_FLIGHT = {"completed", "stopped", "failed", "outcome_unknown"}


class LiveMissionRunner(MissionRunner):
    def __init__(self, session, camera, vision, publish, *, drone_client, stop_verify_seconds=2.0,
                 lease_seconds=2.0, expected_mode="live", allow_mock_tools=False, **kwargs):
        super().__init__(session, camera, vision, publish, **kwargs)
        if expected_mode not in {"mock", "live"}:
            raise DroneError("MODE_MISMATCH")
        self.expected_mode, self.allow_mock_tools = expected_mode, allow_mock_tools
        self.label = "MOCK 도구" if expected_mode == "mock" else "실제 드론"
        self.session.data["droneToolExecution"] = expected_mode
        self.drone = drone_client
        self._launch_lock = asyncio.Lock()
        self._execute_id, self._stop_id = str(uuid4()), str(uuid4())
        self._execute_task = self._stop_task = None
        self._attempted = self._closing = False
        self._route = []
        self._monitor_by_destination = {}
        self._mission_id = None
        self._last_mission = None
        self._seen_captures = set()
        self._last_pushed_revision = -1
        self.stop_verify_seconds = stop_verify_seconds
        self.lease_seconds = lease_seconds
        self._lease = None

    def _live_response(self, response):
        if (response.get("execution_mode") != self.expected_mode
                or (self.expected_mode == "mock" and response.get("physical_execution") is not False)):
            raise DroneError("MODE_MISMATCH", f"{self.label} 실행 모드가 일치하지 않아 중단합니다.")
        return response

    def _mission(self, response):
        mission = self._live_response(response).get("mission")
        if (type(mission) is not dict or type(mission.get("mission_id")) is not str
                or not mission["mission_id"] or mission.get("state") not in STATES
                or mission.get("destination_ids") != self._route
                or type(mission.get("stop_requested")) is not bool
                or type(mission.get("physical_stop_confirmed")) is not bool
                or (self.expected_mode == "mock" and mission["physical_stop_confirmed"])):
            raise DroneError("INVALID_MISSION_EVIDENCE")
        if self._mission_id is not None and mission["mission_id"] != self._mission_id:
            raise DroneError("MISSION_MISMATCH")
        visits = mission.get("visits")
        if type(visits) is not list or len(visits) != len(self._route):
            raise DroneError("INVALID_VISIT_EVIDENCE")
        for index, (visit, destination) in enumerate(zip(visits, self._route)):
            if (type(visit) is not dict or type(visit.get("visit_index")) is not int
                    or visit["visit_index"] != index or visit.get("destination_id") != destination
                    or visit.get("state") not in {"pending", "moving", "arrived", "captured"}
                    or type(visit.get("arrival_confirmed")) is not bool
                    or type(visit.get("capture_ids")) is not list
                    or len(visit["capture_ids"]) > 2
                    or any(type(cid) is not str or not cid for cid in visit["capture_ids"])
                    or len(set(visit["capture_ids"])) != len(visit["capture_ids"])
                    or (visit["state"] in {"arrived", "captured"} and not visit["arrival_confirmed"])):
                raise DroneError("INVALID_VISIT_EVIDENCE")
        self._mission_id = mission["mission_id"]
        self._last_mission = mission
        before = (self.session.data["droneMissionId"], self.session.data["droneState"], self.session.data["droneStopState"])
        self.session.data.update(droneMissionId=self._mission_id, droneState=mission["state"])
        if mission["state"] == "awaiting_rc_landing":
            self.session.data["droneStopState"] = "awaiting_manual"
        elif mission["physical_stop_confirmed"]:
            self.session.data["droneStopState"] = "confirmed"
        after = (self.session.data["droneMissionId"], self.session.data["droneState"], self.session.data["droneStopState"])
        if before != after:
            self.session.touch()
        return mission

    async def launch(self):
        async with self._launch_lock:
            if self._attempted:
                return result(self._work is not None and self.session.phase not in {"aborted"},
                    f"같은 {self.label} 요청은 다시 전송하지 않습니다. 화면의 상태를 확인하세요.")
            if self.session.phase != "ready":
                return result(False, "경로와 탐색 프롬프트를 먼저 확인해야 합니다.")
            if self.session.data["droneControlMode"] != self.expected_mode:
                return result(False, f"{self.label} 세션의 실행 모드가 일치하지 않습니다.")
            if self.expected_mode == "mock" and not self.allow_mock_tools:
                return result(False, "MOCK 도구 실행은 DRONE_CONTROL_USE_TOOLS=1로 명시적으로 활성화해야 합니다.")
            if self.expected_mode == "live" and self.session.data["mode"] != "azure":
                return result(False, "실제 드론 촬영 분석은 TRIAGE_MODE=azure가 필요합니다. 모의 분석으로 대체하지 않습니다.")
            readiness = self.drone.readiness() or self.vision.readiness()
            if readiness:
                return result(False, readiness)
            try:
                caps = self._live_response(await self.drone.call("drone_get_capabilities", {}))
                if self.expected_mode == "live" and caps.get("live_ready") is not True:
                    raise DroneError("PROFILE_UNAVAILABLE", "현장 프로파일과 실제 비행 준비 상태를 먼저 확인해야 합니다.")
                if self.expected_mode == "mock" and (caps.get("mock_capture_ready") is not True
                        or caps.get("physical_execution") is not False):
                    raise DroneError("MOCK_CAPTURE_UNAVAILABLE", "MOCK 도구용 PC 캡처를 명시적으로 활성화하세요.")
                mapping = {}
                destinations = caps.get("destinations")
                if type(destinations) is not list:
                    raise DroneError("INVALID_CAPABILITIES")
                for item in destinations:
                    if (type(item) is not dict or type(item.get("destination_id")) is not str
                            or item.get("monitor_id") not in self.session.state.confirmedRoute
                            or item["monitor_id"] in mapping or item["destination_id"] in mapping.values()):
                        raise DroneError("INVALID_CAPABILITIES")
                    mapping[item["monitor_id"]] = item["destination_id"]
                if set(mapping) != set(self.session.state.confirmedRoute):
                    raise DroneError("INVALID_CAPABILITIES")
                self._route = [mapping[mid] for mid in self.session.state.confirmedRoute]
                self._monitor_by_destination = {destination: monitor for monitor, destination in mapping.items()}
                if self._route not in caps.get("supported_ordered_sequences", []):
                    raise DroneError("ROUTE_UNSUPPORTED", "확인한 방문 순서는 현장 드론 프로파일에서 지원하지 않습니다.")
                status = self._live_response(await self.drone.call("drone_get_status", {}))
                if status.get("active_mission_id") is not None:
                    raise DroneError("DRONE_BUSY", "기존 드론 임무가 종료되었는지 먼저 확인해야 합니다.")
                if self._closing:
                    return result(False, "세션이 종료되어 비행을 시작하지 않습니다.")
                self._attempted = True
                self.session.data.update(droneState="requesting", droneErrorCode=None)
                self.session.touch()
                await self._push()
                self._execute_task = asyncio.create_task(self.drone.call("drone_execute_route", {
                    "profile_id": caps.get("profile_id"), "site_revision": caps.get("site_revision"),
                    "destination_ids": self._route}, request_id=self._execute_id))
                self._mission(await asyncio.shield(self._execute_task))
                if self._closing:
                    await self._stop_hardware()
                    return result(False, "세션 종료로 중지 요청을 전송했습니다.")
                outcome = self.session.launch_mission()
                if not outcome["ok"]:
                    await self._stop_hardware()
                    return outcome
                self._work = asyncio.create_task(self._run_live(self.session.run_id))
                self._deadlines = asyncio.create_task(self._watch_deadlines(self.session.run_id))
                self._lease = asyncio.create_task(self._renew_lease())
                return result(True, "MOCK 도구 실행: 확정 경로와 PC 모의 캡처를 요청했습니다. 실제 비행은 없습니다."
                    if self.expected_mode == "mock" else
                    "확정한 전체 경로를 드론에 요청했습니다. 실제 도착과 촬영 근거를 기다립니다.")
            except asyncio.CancelledError:
                await self._stop_hardware()
                raise
            except Exception as exc:
                await self._fail(exc)
                return result(False, self.session.data["error"])

    async def retry(self):
        if self.expected_mode == "mock":
            return result(False, "MOCK 도구 오류 후 자동 재개하지 않습니다. 새 모의 작전을 명시적으로 시작하세요.")
        return result(False, "실제 비행 오류 후에는 자동 재개하지 않습니다. 정지·착륙 확인 후 새 작전을 명시적으로 시작하세요.")

    async def abort(self):
        self.session.abort_mission()
        await self.close()
        await self._notify()
        return result(True, "MOCK 도구 중지를 요청했습니다. 실제 비행은 없습니다." if self.expected_mode == "mock"
            else "드론 중지 요청을 처리했습니다. 화면의 실제 정지 확인 여부를 확인하세요.")

    async def _push(self):
        if self._last_pushed_revision == self.session.data["revision"]:
            return
        self._last_pushed_revision = self.session.data["revision"]
        await self.publish({"type": "route.state", "state": self.session.snapshot()})

    async def _notify(self, text=None):
        if self.session.phase in TERMINAL:
            await self._notify_terminal()
        else:
            await self._push()
            if text:
                await self.publish({"type": "mission.progress", "text": text})

    async def _fail(self, exc):
        code = getattr(exc, "code", "MOCK_TOOL_FAILED" if self.expected_mode == "mock" else "LIVE_OPERATION_FAILED")
        if self._attempted:
            self.session.abort_mission()
        self.session.data.update(droneErrorCode=code, error=(
            f"MOCK 도구 실행을 중단했습니다 ({code}). 실제 비행은 없으며 자동 재개하지 않습니다."
            if self.expected_mode == "mock" else
            f"실제 드론 작업을 중단했습니다 ({code}). 자동 재개하지 않습니다. 정지 상태를 확인하고 필요하면 RC로 제어·착륙하세요."))
        self.session.touch()
        await self._stop_hardware()
        await self._notify()

    async def _read_mission(self):
        mission = self._mission(await self.drone.call("drone_get_mission", {"mission_id": self._mission_id}))
        if mission["state"] in {"failed", "outcome_unknown", "stopped", "stop_requested"}:
            raise DroneError("MISSION_" + mission["state"].upper())
        return mission

    async def _renew_lease(self):
        # Analysis can take longer than the service lease. Keep this independent
        # of vision, browser narration and the ordered capture/analysis loop.
        try:
            while not self._closing and self._stop_task is None:
                await asyncio.sleep(self.lease_seconds)
                if self._closing or self._stop_task is not None:
                    return
                response = await self.drone.call("drone_get_mission", {"mission_id": self._mission_id})
                if self._stop_task is not None:
                    return
                mission = self._mission(response)
                await self._push()
                if mission["state"] == "completed":
                    return
                if mission["state"] in TERMINAL_FLIGHT or mission["stop_requested"]:
                    raise DroneError("MISSION_" + mission["state"].upper())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._fail(exc)
            if self._work and not self._work.done():
                self._work.cancel()
                await asyncio.gather(self._work, return_exceptions=True)

    async def _watch_deadlines(self, run_id):
        expired_terminal = False
        while run_id == self.session.run_id and self.session.phase not in TERMINAL:
            await self.sleep(self.tick_seconds)
            changed = self.session.expire()
            if self.session.phase in TERMINAL:
                expired_terminal = changed
                break
            await self._notify()
        if expired_terminal or self.session.phase == "aborted":
            await self._stop_hardware()
            if self._work and not self._work.done():
                self._work.cancel()
                await asyncio.gather(self._work, return_exceptions=True)
        if run_id == self.session.run_id and self.session.phase in TERMINAL:
            await self._notify_terminal()

    async def _run_live(self, run_id):
        try:
            for index, destination in enumerate(self._route):
                if self.session.phase in TERMINAL:
                    break
                monitor = self._monitor_by_destination[destination]
                person = self.session.person(monitor)
                self.session.data["activeVisitIndex"] = index
                self.session.set_operation("flying", monitor, run_id)
                await self._notify(f"모니터 {monitor[-1]}의 {self.label} 도착 근거를 기다립니다.")
                while True:
                    mission = await self._read_mission()
                    visit = mission["visits"][index]
                    await self._push()
                    if visit["arrival_confirmed"] and visit["state"] in {"arrived", "captured"}:
                        break
                    if mission["state"] in TERMINAL_FLIGHT:
                        raise DroneError("ARRIVAL_UNCONFIRMED")
                    await self.sleep(self.tick_seconds)
                # Consume only distinct PC frames attributed to this exact ordered visit.
                while person["attempts"] < min(2, self.session.scenario["maxDetectionAttempts"]):
                    if person["outcome"] is not None or self.session.phase in TERMINAL:
                        break
                    self.session.set_operation("capturing", monitor, run_id)
                    mission = await self._read_mission()
                    visit = mission["visits"][index]
                    response = self._live_response(await self.drone.call("drone_get_captures", {"mission_id": self._mission_id}))
                    records = response.get("captures")
                    if (response.get("mission_id") != self._mission_id or type(records) is not list
                            or len(records) > 6):
                        raise DroneError("CAPTURE_MISMATCH")
                    frame = None
                    ids = [record.get("capture_id") for record in records if type(record) is dict]
                    if any(type(cid) is not str for cid in ids) or len(set(ids)) != len(records):
                        raise DroneError("CAPTURE_MISMATCH")
                    for record in records:
                        if (type(record) is not dict or record.get("mission_id") != self._mission_id
                                or type(record.get("visit_index")) is not int
                                or not 0 <= record["visit_index"] < len(self._route)
                                or record.get("destination_id") != self._route[record["visit_index"]]):
                            raise DroneError("CAPTURE_MISMATCH")
                        if record["visit_index"] != index or record.get("capture_id") in self._seen_captures:
                            continue
                        if record.get("capture_id") not in visit["capture_ids"]:
                            # Capture publication may race the previous mission snapshot; reread it.
                            continue
                        frame = LiveCaptureCamera.from_record(record, mission_id=self._mission_id,
                            visit_index=index, destination_id=destination, monitor_id=monitor)
                        self._seen_captures.add(frame.id)
                        break
                    if frame is None:
                        # The service publishes each frame separately. A captured
                        # visit with one ID can still be acquiring its second frame.
                        if mission["state"] in TERMINAL_FLIGHT:
                            raise DroneError("CAPTURE_UNAVAILABLE")
                        await self._push()
                        await self.sleep(self.tick_seconds)
                        continue
                    if not self.session.add_capture(frame, run_id):
                        raise DroneError("CAPTURE_REJECTED")
                    self.session.analyzing(frame.id, run_id)
                    await self._notify()
                    evidence = await self.vision.analyze(frame, person["targetDescription"],
                        search_prompt=self.session.data["userPromptText"],
                        appearance_constraints=self.session.data["appearanceConstraints"],
                        unsupported_appearance=self.session.data["unsupportedAppearance"])
                    if not self.session.apply_detection(run_id, frame.id, evidence):
                        break
                    await self._notify()
                    if evidence["targetPresent"]:
                        break
            # Scoring can finish before the aircraft returns or lands. Publish both states.
            while not self._closing and self.session.phase != "aborted":
                mission = await self._read_mission()
                await self._push()
                if mission["state"] == "completed":
                    return
                await self.sleep(self.tick_seconds)
        except asyncio.CancelledError:
            await self._stop_hardware()
            raise
        except Exception as exc:
            await self._fail(exc)

    async def _stop_hardware(self):
        if not self._attempted:
            return
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._stop_once())
        await asyncio.shield(self._stop_task)

    async def _stop_once(self):
        self.session.data["droneStopState"] = "requesting"
        self.session.touch()
        try:
            if self._mission_id is None and self._execute_task:
                try:
                    self._mission(await asyncio.shield(self._execute_task))
                except Exception:
                    try:
                        self._mission(await self.drone.lookup_request(self._execute_id))
                    except Exception:
                        status = self._live_response(await self.drone.call("drone_get_status", {}))
                        active = status.get("active_request")
                        if (type(active) is not dict or active.get("caller_id") != self.drone.caller_id
                                or active.get("request_id") != self._execute_id or not active.get("mission_id")):
                            raise DroneError("STOP_OUTCOME_UNKNOWN")
                        self._mission_id = active["mission_id"]
            if not self._mission_id:
                raise DroneError("STOP_OUTCOME_UNKNOWN")
            response = await self.drone.call("drone_stop_mission", {"mission_id": self._mission_id}, request_id=self._stop_id)
            mission = self._mission(response)
            deadline = asyncio.get_running_loop().time() + self.stop_verify_seconds
            while (not mission["physical_stop_confirmed"] and mission["state"] != "awaiting_rc_landing"
                    and not (self.expected_mode == "mock" and mission["state"] in TERMINAL_FLIGHT)
                    and asyncio.get_running_loop().time() < deadline):
                await asyncio.sleep(0.2)
                mission = self._mission(await self.drone.call("drone_get_mission", {"mission_id": self._mission_id}))
            self.session.data["droneStopState"] = ("awaiting_manual" if mission["state"] == "awaiting_rc_landing"
                else "confirmed" if mission["physical_stop_confirmed"]
                else "awaiting_manual" if mission.get("verification_pending") is True
                else "stop_requested")
        except Exception:
            self.session.data["droneStopState"] = "unknown"
            self.session.data["droneErrorCode"] = self.session.data["droneErrorCode"] or "STOP_OUTCOME_UNKNOWN"
        finally:
            self.session.touch()
            await self._push()

    async def close(self):
        self._closing = True
        await self._stop_hardware()
        if self._lease and self._lease is not asyncio.current_task():
            self._lease.cancel()
            await asyncio.gather(self._lease, return_exceptions=True)
        await super().close()


# Same evidence-driven implementation, explicitly opted in for nonphysical tool E2E.
ToolMissionRunner = LiveMissionRunner
