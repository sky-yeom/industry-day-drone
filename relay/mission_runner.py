"""Automatic capture/analysis and independent deadline processing."""

import asyncio
import logging

try:
    from .survey import ACTIVE, TERMINAL
except ImportError:
    from survey import ACTIVE, TERMINAL

log = logging.getLogger("relay.mission")


class MissionRunner:
    def __init__(self, session, camera, vision, publish, *, sleep=asyncio.sleep, tick_seconds=0.1):
        self.session, self.camera, self.vision = session, camera, vision
        self.publish = publish
        self.sleep, self.tick_seconds = sleep, tick_seconds
        self._work = None
        self._deadlines = None
        self._retry = asyncio.Event()
        self._terminal_task = None

    async def launch(self):
        if self._work is not None:
            return self.session.launch_mission()
        outcome = self.session.launch_mission(self.vision.readiness())
        if outcome["ok"]:
            self._work = asyncio.create_task(self._run(self.session.run_id))
            self._deadlines = asyncio.create_task(self._watch_deadlines(self.session.run_id))
        return outcome

    async def retry(self):
        outcome = self.session.retry_mission()
        if outcome["ok"]:
            self._retry.set()
        return outcome

    async def abort(self):
        outcome = self.session.abort_mission()
        await self.close()
        await self._notify()
        return outcome

    async def close(self):
        tasks = [t for t in (self._work, self._deadlines, self._terminal_task)
                 if t is not None and t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._terminal_task is not None and self._terminal_task.cancelled():
            self._terminal_task = None

    async def _publish_terminal(self):
        await self.publish({"type": "route.state", "state": self.session.snapshot()})
        await self.publish({"type": "mission.debrief", "runId": self.session.run_id,
                            "text": self.session.debrief()})

    async def _notify_terminal(self):
        if self._terminal_task is None:
            self._terminal_task = asyncio.create_task(self._publish_terminal())
        # Deadline cancellation may stop inference, but must not lose the debrief.
        await asyncio.shield(self._terminal_task)

    async def _notify(self, text=None):
        if self.session.phase in TERMINAL:
            await self._notify_terminal()
            return
        await self.publish({"type": "route.state", "state": self.session.snapshot()})
        if self.session.phase in TERMINAL:
            await self._notify_terminal()
        elif text:
            await self.publish({"type": "mission.progress", "text": text})

    async def _watch_deadlines(self, run_id):
        while run_id == self.session.run_id and self.session.phase not in TERMINAL:
            await self.sleep(self.tick_seconds)
            self.session.expire()
            if self.session.phase in TERMINAL:
                break
            await self._notify()
        if self._work is not None and not self._work.done():
            self._work.cancel()
            await asyncio.gather(self._work, return_exceptions=True)
        if run_id == self.session.run_id and self.session.phase in TERMINAL:
            await self._notify_terminal()

    async def _operation(self, operation, label, run_id):
        while run_id == self.session.run_id and self.session.phase not in TERMINAL:
            try:
                return await operation()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("mission %s failed", label)
                self._retry.clear()
                self.session.pause(f"{label} 중 오류가 발생했습니다. 연결과 설정을 확인한 뒤 재시도하거나 임무를 중단하세요.")
                await self._notify(self.session.data["error"])
                if self.session.phase in TERMINAL:
                    return None
                await self._retry.wait()
        return None

    async def _run(self, run_id):
        try:
            for monitor in self.session.state.confirmedRoute:
                self.session.expire()
                if run_id != self.session.run_id or self.session.phase in TERMINAL:
                    break
                person = self.session.person(monitor)
                if person["outcome"] is not None:
                    continue
                self.session.set_operation("flying", monitor, run_id)
                await self._notify(f"현장 {monitor[-1]}로 이동합니다.")
                await self.sleep(self.session.scenario["travelMs"] / 1000)
                self.session.expire()
                # One negative recapture is the scenario's maximum; technical retries
                # repeat the unfinished operation, not a completed negative attempt.
                for _ in range(min(2, self.session.scenario["maxDetectionAttempts"])):
                    if person["outcome"] is not None or self.session.phase in TERMINAL:
                        break
                    self.session.set_operation("capturing", monitor, run_id)
                    await self._notify()

                    async def capture_frame():
                        await self.sleep(self.session.scenario["captureMs"] / 1000)
                        frame = await self.camera.capture(monitor)
                        if not self.session.add_capture(frame, run_id):
                            raise ValueError("유효하지 않거나 오래된 촬영 결과입니다.")
                        return frame

                    capture = await self._operation(capture_frame, "이미지 촬영", run_id)
                    if capture is None or self.session.phase in TERMINAL:
                        break
                    await self._notify()
                    self.session.analyzing(capture.id, run_id)
                    await self._notify()

                    async def analyze_frame():
                        evidence = await self.vision.analyze(
                            capture, person["targetDescription"],
                            search_prompt=self.session.data["userPromptText"],
                            appearance_constraints=self.session.data["appearanceConstraints"],
                            unsupported_appearance=self.session.data["unsupportedAppearance"],
                            scene_context={"monitor_id": monitor, "label": person["label"],
                                           "report": person["clue"]})
                        if self.session.apply_detection(run_id, capture.id, evidence):
                            return evidence
                        return None

                    evidence = await self._operation(analyze_frame, "이미지 분석", run_id)
                    if evidence is None:
                        break
                    await self._notify(
                        f"현장 {monitor[-1]} 이미지에서 대상자를 확인했습니다."
                        if person["captureId"] == capture.id
                        else f"현장 {monitor[-1]}의 구조 조건이 아직 충족되지 않았습니다.")
                    if evidence["targetPresent"]:
                        break
            if self.session.phase in ACTIVE:
                self.session.set_operation("analyzing", None, run_id)
                await self._notify("방문을 마쳤습니다. 미확인 대상의 구조 시한까지 기다립니다.")
            elif self.session.phase in TERMINAL:
                await self._notify()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("mission execution failed")
            if self.session.phase not in TERMINAL:
                self.session.abort_mission()
                self.session.data["error"] = "임무 진행 중 오류가 발생하여 중단했습니다."
                self.session.touch()
            await self._notify()
