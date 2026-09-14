# main(다른 PC) 디버깅 기록 검토와 feat/drone 대조 — 2026-09-14

대상: `origin/main 6a268d4` (다른 PC, `FIELD_DEBUG_20260914.md` 포함) vs `feat/drone 973ec13` (이 PC, 09-10·09-11 7/7 성공).
목적: 다른 PC의 세 증상의 원인을 코드 대조로 특정하고, 패치 수준의 해결책을 준다.

| 증상 (사용자 보고) | 판정 | 핵심 원인 |
|---|---|---|
| ① LLM 경로도 우리처럼 7/7이 안 됨 | 코드+현장 복합 | 비인접 경로(6→3 첫 구간이 태그 1·2를 맹목 통과), 태그 2·3 미검출(현장), 폰·네트워크 불안정 |
| ② LLM→Voice 전환 시 짐벌만 내려가고 이륙 없음 / 이륙 후 추가 상승 없음 | **코드 결함 잔존** | (a) 영상·지상증명 게이트(수정됨) (b) `ShuttleClient.arm`의 **2 s 권한 대기** vs 폰 권한 확정 **1.6–3.3 s** — main의 4 s 유예는 이 경로를 덮지 못함 |
| ③ UI에서 오류 1회면 바로 종료 | **relay 정책** | `LiveMissionRunner`가 읽기 1회 실패·VLM 실패·브라우저 WS 끊김에 즉시 `abort + drone_stop_mission`; page.tsx는 6a268d4에서 일부만 수정 |
| ④ 그 밖에 Voice로 바꿀 때 생기는 문제 | 목록 §10 | 브라우저·Azure 인증·음성 턴 관문·relay 승인·비행 중 전용 위험 46항목. 특히 **시나리오 시계(18/28/45 s)가 45 s에 실비행을 정지시킴**(§10.5 F1) |
| ⑤ 이 PC가 연결 끊김으로 시도한 것 | 이력 §11 | 08-30~09-13 시도·결과·미해결 항목 |

먼저 오해 하나를 정리한다. **LLM과 Voice는 드론 쪽 경로가 같다.** 둘 다
`relay/tools.py → :8766 → FieldAdapter.run → shuttle.run(external_route=…)`이다. 다른 PC의 문서 §4.3도 같은 결론이다.
"우리 LLM 버전"이 7/7인 이유는 LLM이라서가 아니라 **단독 셔틀 CLI**(연결 1개, relay 감시자 없음, 인접 경로 6→1→2→1→6, 1.2 m, APK `.20260910.6`)이기 때문이다.
아래 §2에 그 차이를 층별로 나열한다.

---

## 1. 코드 계보와 차이

| 항목 | feat/drone 973ec13 (이 PC) | main 6a268d4 (다른 PC) | 영향 |
|---|---|---|---|
| 셔틀 기반 | 09-10 셔틀 + 09-11 프로필 기반 경로 | 09-10 셔틀을 그대로 가져감(`validate_profile`이 `[3,2,1,6]`·`[6,1,2,3,2,1,6]` 고정) | main은 3-태그 벽(2-1-6)·`216` 프로필을 못 돌림 |
| `id1_pair_framing.py` | 연속 보정 게이트 | **바이트 동일** | 두 PC의 횡이동 논리는 같다 |
| `bounded_sonar_climb.py` | 목표보다 0.30 m 위까지 허용(자동이륙이 1.1–1.3 m에 안착) | 목표+0.051 m 초과 시 `RuntimeError` | main에서 1.2 m 목표는 즉시 중단됨(1.5/1.6은 무관) |
| 프로필 고도 범위 | 1.0–1.6 m | 1.4–1.6 m | 위와 동일 |
| `BUILD_ID` | `5.18-connectivity.20260910.6` | `5.18-connectivity.20260913.3` | **폰 APK가 어느 PC 코드를 날릴지 결정** (§9) |
| 폰 APK(.6 → .3) | — | `MODE_STATE_GRACE_MS` 1000→3000, `FrameBuffer.trimToNewestKeyFrame()`(open·overflow), `FieldDiagnostics`(비동기 writer) | 권한 회수·영상 전달률 문제 완화 |
| 지상 증명 | 단발 STATUS | `_await_ground_proof` 30 s 폴링 | main이 낫다 (이식 권장) |
| 지상 영상 | `_wait_ground_video` 20 s | + `_prepare_ground_video` 1회 재연결 | main이 낫다 |
| 낡은 프레임 | `InterruptedError` → 임무 종료 | `FramingCorrectionDeferred` → 제자리 유지·재관측 | **우리도 이식 권장** (우리 PC가 빨라서 안 터졌을 뿐) |
| 상승 중 ID0 미검출 | 즉시 종료 | 1 s 유예 | 이식 권장 |
| 검출 비용 | `cv2.undistort` 2회, `nthreads=1` | 캐시 remap 1회, `nthreads=4` (280→40 ms) | 이식 권장 |
| 중단 로그 | 메시지만 | traceback 40줄 | 이식 권장 |
| HTTP 훅 | 없음 | `external_route`/`on_capture`/`capture_count`/`video_broker` | Voice 경로의 진입점 |
| 도구 서비스 | `tool_control/{server,service,live}.py` | + `field.py`, `camera.py`(VideoBroker), `fixtures.py` | Voice 경로 전용 |
| relay | 09-10 통합본 | +10 k줄(voice_turns, live_mission, drone_status, operator_*, voice_trace …) | §5 |

검증한 부수 사실(다시 조사하지 말 것):
- 폰 `connectionGeneration`은 **USB 제품 이벤트에서만** 증가한다(`PcBridge.enqueueProduct`). PC의 STATUS용 TCP 접속이 늘어도 텔레메트리·제어권이 리셋되지 않는다.
- `FieldDiagnostics`는 전용 writer 스레드+큐다. SDK 콜백 스레드에서 fsync하지 않는다. 권한 확정 지연의 원인은 아니다.
- 웹은 `/api/config`를 마운트·시작 시 2회만 부른다. 상시 폴링은 없다.

---

## 2. 실행 경로와 관문 (어느 층에서 멈추는지 읽는 법)

```
[단독 CLI]  standalone_tag_shuttle.py --execute ─────────────────────────┐
[LLM/Voice] browser → relay/server.py → tools.dispatch → LiveMissionRunner.launch()
            → HTTP :8766 drone_execute_route → service worker thread → FieldAdapter.run()
            → shuttle.run(external_route, on_capture, capture_count=2, video_broker) ◄──┘
            → ShuttleClient → 폰 :9999 → DJI
```

### 2.1 relay `launch()` 관문 (이륙 요청 전, 실패 시 "실제 출발 요청을 보내지 않았습니다")
`session.phase == ready` → `droneControlMode == live` → `TRIAGE_MODE == azure` → `drone.readiness()`/`vision.readiness()` →
`drone_get_capabilities.live_ready` → 경로가 `supported_ordered_sequences`에 있음 → `drone_get_status` →
`live_readiness_issue`: `DRONE_DISCONNECTED` / `DJI_SDK_NOT_REGISTERED` / `AIRCRAFT_DISCONNECTED` /
`AIRCRAFT_CONNECTION_UNCONFIRMED`(`bridge_health.product_connected`가 `True`가 아님) / `FLIGHT_CONTROLLER_UNAVAILABLE`(`fc_health.state == HANDLER_FAULT`) /
`GROUND_UNVERIFIED`(캐시 0.5 s 초과 또는 ages > 500 ms) → `active_mission_id is None`.

### 2.2 서비스·어댑터 관문
`service.execute`: 경로 ∈ `itertools.permutations(["tag-1","tag-2","tag-3"])` (**6가지 모두 허용**).
`FieldAdapter.run`: 목적지 정확히 3개·각 1회, `profile_id`/`site_revision` 일치, `visits` 순서 일치.
`shuttle.run`: `external_route`는 `pair_reference`+`on_capture`+`capture_count=2` 필수.

### 2.3 비행 시퀀스와 각 단계의 중단 문구 (main 기준)
| 순서 | 단계 | 시간 상한 | 중단 시 `error` |
|---|---|---|---|
| 1 | `_await_ground_proof("standalone_preflight")` | 30 s | `Fresh motors-off grounded RC state and bridge … required` |
| 2 | `bridge_build_id == BUILD_ID`, 프로세스 식별자, 배터리 ≥30 % | — | `… bridge 5.18-connectivity.20260913.3 required` / `At least 30% battery required` |
| 3 | `gimbal_down()` | — | **여기까지가 "카메라만 아래로"** |
| 4 | `_prepare_ground_video` (fresh 프레임 0.3 s 연속) | 20 s + 재연결 1회 | `GroundVideoUnavailable: Fresh video not confirmed before takeoff` → relay `PREFLIGHT_VIDEO_UNAVAILABLE` |
| 5 | `stick_mode("advanced_angle")` | — | — |
| 6 | `_await_ground_proof("…immediate_takeoff…")` | 30 s | `Ground/motor state changed before takeoff` |
| 7 | RC 스틱 5 s 내 입력 없음 | — | `Recent RC stick input; no takeoff` |
| 8 | `takeoff(token)` | — | `invalid_confirmation_token`(09-13 사례) |
| 9 | `_wait_takeoff_settled` (is_flying fresh, h ≥ 0.5, 2 s 안정) | 20 s | `Takeoff not confirmed…` |
| 10 | **`ShuttleClient.arm` → 권한 확정 대기** | **2 s** | **`Virtual Stick authority did not become ready within 2s; no re-arm`** |
| 11 | `_acquire_tag(ID0)` | 12 s | acquire timeout |
| 12 | `_climb` (ID0 매 틱, 1 s 유예; 높이 fresh; `climb_command`) | 8 s | `Fresh floor ID0 unavailable during climb` / `already above ascent target` / `…not confirmed within 8s` |
| 13 | `gimbal(0)` → 1 s → `acquire_wall_home(6)` | 12 s | `Wall home ID6 was not fully visible…` |
| 14 | 구간 반복 (OUTBOUND=pair gate, RETURN=`traverse_horizontal`) | `leg_timeout_s` 45 s | `ID{n} framing not confirmed within 45s…` |

**"이륙 후 추가 상승 없음"은 10–12 사이에서 끝난 것이다.** 자동이륙이 1.1–1.3 m까지 올려놓은 뒤 PC가 arm·상승을 못 이어간 상태다.

---

## 3. 증상 ②: Voice 전환 시 상승 실패

### 3.1 (a) 짐벌만 내려가고 이륙하지 않음 — 4·6·7·8 단계
다른 PC가 실제로 겪은 것:
- **영상 전달률 0.03 %** (§3.2 기록): 폰 `FrameBuffer.openReader()`가 큐를 비우고 다음 I-프레임(~2.7 s)을 기다리는데, `_prepare_ground_video`의 재연결이 그보다 빨리 와서 영영 락온 못 함. **APK `.3`에서 수정됨**(`trimToNewestKeyFrame`).
  우리 PC가 안 겪은 이유: 단독 셔틀은 `FreshVideoStream` 1개를 열고 `initial_keyframe_timeout_s=15`로 기다린다. 재연결 자체가 없다.
- **`fc=HANDLER_FAULT`로 지상 증명 4 s 만에 포기** (§3.6): 30 s로 늘림. 우리도 09-11에 같은 `REQUEST_HANDLER_NOT_FOUND`를 겪었고 USB 재연결·앱 재시작으로 회복했다. 자동 복구 없음(`automatic_fc_recovery_enabled:false`)은 양쪽 동일.
- **토큰 불일치**(09-13): APK에 구운 `OPERATOR_ARM_TOKEN` ≠ PC `network.confirmation_token` → `takeoff`에서 `invalid_confirmation_token`. `OTHER_PC_LIFT_DIAGNOSIS_20260913.md` 참조. `android-private-fixed.properties`에 낡은 47자 토큰이 남아 있다는 기록(§4.5)이 있으니 빌드 시 어느 properties가 쓰였는지 확인.

남은 점검: 로그에서 `standalone_ground_proof_failed`(ages·`telemetry_poll_failures`), `ground_video_preflight`(`decoded_frames`, `android_written_bytes`), `ground_video_reconnect`를 본다. 셋 다 없고 `no_flight_action_dispatched:true`면 relay 관문(§2.1)에서 막힌 것이므로 relay 로그 `Tool mission failed (CODE…)`를 본다.

### 3.2 (b) 이륙은 되는데 추가 상승이 없음 — 10 단계가 진짜 구멍
측정값:

| 출처 | arm → `vs_advanced_enabled && vs_authority==MSDK` 확정까지 |
|---|---|
| 이 PC, APK `.6`, `20260910T152608` / `20260910T153439` / `20260911T160145` (`standalone_arm_transition`) | **0 ms / 125 ms / 125 ms** |
| 다른 PC, APK `.20260913.x` (main 주석: `StickControlManager.java:79`, `live.py:31`) | **1.6 s 이상, 최대 ~3.3 s** |

main이 넣은 세 가지 완화와 각각의 적용 범위:

| 완화 | 위치 | Voice 경로(FieldAdapter→shuttle)에 적용? |
|---|---|---|
| 폰 `MODE_STATE_GRACE_MS` 1→3 s | `StickControlManager.java:84,862-877` | 예. 브리지가 listener 지연 때문에 스스로 RC로 회수하던 것은 막힘 |
| PC `AUTHORITY_HANDOFF_GRACE_S = 4.0` | `live.py MissionClient.status` | **아니오.** 이 검사는 `self._armed`가 `True`일 때만 돈다. `ShuttleClient.arm`은 대기 루프 동안 `self._armed = False`로 둔다(`standalone_tag_shuttle.py:316`) |
| `_confirm_advanced_authority(timeout 5 s)` | `live.py LiveAdapter.run`(legacy 어댑터) | **아니오.** `FieldAdapter.run`은 `shuttle.run`을 호출하며 이 함수를 부르지 않는다 |

즉 Voice가 실제로 타는 경로에서는 여전히 **`standalone_tag_shuttle.py:317` `deadline = time.monotonic() + 2.`** 가 살아 있다.
폰이 2 s 안에 `vs_advanced_enabled`를 올리지 못하면 `TimeoutError("Virtual Stick authority did not become ready within 2s")` → `run()`의 `except` → `release_to_rc` → 기체는 자동이륙 고도에서 호버링. 보고된 증상 그대로다.
이 PC에서 안 터진 이유는 코드가 달라서가 아니라 **폰이 125 ms 안에 확정해 줬기 때문**이다.

같은 폰(`10.244.155.20`)이라면 차이는 APK뿐이다: `.6`은 125 ms, `.20260913.x`는 1.6–3.3 s.
recorder는 비동기라 I/O 탓은 아니다. `2a1445d`의 Android 변경(`TelemetryProvider` 폴 틱마다 lock 안에서 `diagnosticSnapshot` 생성, `MSDKManagerVM.kt`, `FcHealthTracker`)이 후보다. 확인 방법: 두 APK로 각각 `standalone_arm_transition`의 `ready` 도달 시각을 재서 비교한다(§8).

**수정 (P0-1)** — 요구 조건은 그대로 두고 대기 시간만 늘린다. main 문서의 원칙("only allow more time")과 같다.
```python
# standalone_tag_shuttle.py (main) — ShuttleClient.arm
# 폰의 vsAdvancedEnabled listener 확정이 현장에서 1.6–3.3 s 걸렸다(APK .20260913.x).
# 2 s 상한은 그 지연보다 짧아 정상 arm을 TimeoutError로 끝냈다. 권한 없이는 여전히
# 어떤 자세 명령도 나가지 않는다; 묻는 시간만 늘린다. (_acquire_tag 12 s보다 짧게 유지)
ARM_AUTHORITY_TIMEOUT_S = 5.0
...
            deadline = time.monotonic() + ARM_AUTHORITY_TIMEOUT_S
...
            raise TimeoutError(f"Virtual Stick authority did not become ready within {ARM_AUTHORITY_TIMEOUT_S:g}s; no re-arm")
```
`pc/tests/test_standalone_arm_transition.py`의 2 s 기대치를 같이 고친다.

### 3.3 (b)의 나머지 후보 — 11·12 단계
- **ID0가 시야에 없음**: 자동이륙 중 표류하면 하방 카메라(짐벌 -90°)에서 바닥 태그가 빠진다. `_acquire_tag` 12 s 타임아웃 또는 `Fresh floor ID0 unavailable during climb`. 로그 `visible_ids`에 0이 없으면 이것이다. 이륙 지점을 ID0 바로 위로.
- **`climb_command` 상한**: main은 `height > target + 0.051`이면 `already above ascent target` 예외. 1.5/1.6 m 목표에선 자동이륙(1.1–1.3 m)으로는 안 걸리지만, 초음파가 바닥 물체를 읽다가(§4.2) 갑자기 정상값으로 돌아오면 순간 "목표 초과"가 될 수 있다. 우리 쪽처럼 0.30 m 여유를 두는 편이 안전하다(하강 명령은 어차피 없다).
- **8 s 상승 시한**: 1.2→1.6 m를 0.10–0.18 m/s로 2.5–4 s. `FramingCorrectionDeferred`가 반복되면(낡은 프레임) 8 s를 넘길 수 있다. `standalone_climb_deferred` 횟수를 본다.

---

## 4. 증상 ①: LLM 경로가 7/7이 안 됨

### 4.1 경로 순서 — 검증된 적 없는 비인접 구간 (코드)
서비스는 목적지 순열 6가지를 모두 허용하고(`service.py:191-192`), Voice/LLM은 대화에서 정한 모니터 순서를 그대로 태그 순서로 보낸다.
다른 PC의 21:00 비행은 **6→3→1→2→6**이었다. 벽이 좌→우 `[3,2,1,6]`이므로:

| 구간 | 방향 | 지나치는 중간 태그 | 거리(태그 간격 단위) |
|---|---|---|---|
| 6→3 | 좌 | **1, 2** | 3 |
| 3→1 | 우 | **2** | 2 |
| 1→2 | 좌 | — | 1 |
| 2→6 | 우 | **1** | 2 |

우리 7/7 경로(6→1→2→3→2→1→6, 6→1→2→1→6)는 **모든 구간이 인접(1)** 이다. `PairFramingGate`는 목표 태그가 처음 보일 때까지 `APPROACH_PAIR_IN_ROUTE_DIRECTION`으로 ≤0.6° 기울여 전진할 뿐이고, 중간 태그는 무시한다(문서 §7). 전후축은 한 번도 지령되지 않으므로(`forward_tilt_deg` 항상 0) 3칸을 맹목 주행하는 동안 벽과의 거리가 표류한다. 21:00 비행의 "태그1 이후 9.3 s / 2.8 m 실명"은 이 구간에서 나왔다.
또한 `capture_id1_pair`의 역방향 재획득 시크(`_recover_missing_tag`)는 "목표를 봤다가 놓친" 상황용이라, 아직 못 본 목표에는 도움이 안 된다.

**수정 (P0-3)** — 물리 순서는 인접으로 고정하고, 대화의 순서는 서술에만 쓴다.
```python
# field.py FieldAdapter.__init__ (main)
self.supported_ordered_sequences = [list(self.destination_ids)]   # 예: ["tag-1","tag-2","tag-3"]
```
`service.capabilities`는 `getattr(self.adapter, "supported_ordered_sequences", permutations)`를 이미 쓰므로 이 한 줄로 `launch()`의 `ROUTE_UNSUPPORTED` 관문이 다른 순서를 거른다. (feat/voice-drone-live 워크트리의 `field.py:93`이 이 방식이다.) 사용자가 다른 순서를 말하면 relay가 "현장 경로는 1→2→3 순서로 비행합니다"로 안내하도록 `tools.py` 문구를 맞춘다.
사용자 순서를 꼭 지켜야 한다면 `validate_external_route`에 인접성 검사(`abs(wall.index(a)-wall.index(b)) == 1`)를 넣어 비인접 요청을 거부한다.

### 4.2 고도 1.6 m와 프로필 (코드+현장)
main은 `standalone_tag_6321236.json`을 1.5→1.6 m로 올렸다. 우리는 1.2 m에서 7/7이었다.
1.6 m는 (i) ID0 의존 상승 구간이 3–4 s 더 길고, (ii) 초음파가 바닥 물체를 읽는 착시(§4.2)에 그만큼 더 노출되며, (iii) `climb_command`의 `target ≤ 1.6` 상한 끝값이다. 벽 태그 높이가 바뀌지 않았다면 고도를 올릴 이유가 없다. 1.4–1.5 m로 되돌리거나, 우리 프로필 기반 코드를 이식해 1.2 m를 쓰는 것을 권한다.

### 4.3 태그 2·3을 한 번도 못 봄 (현장, 코드 차이 아님)
검출기는 두 PC가 같다(main이 더 빠름). 따라서 코드 대조로는 답이 안 나온다. 확인 순서:
1. **정적 검출 시험**: 스택을 띄운 채 `/camera/start`(또는 CLI `--check`)로 기체를 손에 들고 태그 2·3 앞 1–2 m에서 `visible_ids`에 2·3이 찍히는지 본다. 안 찍히면 인쇄 크기·tag36h11 여부·유리 반사·조명 문제다.
2. 찍히면 비행 중 `diagnostic_blind_*.jpg`(이미 코드 반영)로 카메라가 벽을 보고 있는지 확인. 벽이 안 보이면 표류(4.1), 벽은 보이는데 태그가 없으면 배치.
3. ID6 캡처 사진처럼 기체가 유리 파티션에 매우 가깝다면 시작 거리를 우리 현장과 같게(태그가 프레임 폭의 ~10–15 %) 잡는다. 전후 제어가 없으므로 **시작 거리가 곧 구간 내내의 거리**다.

### 4.4 환경 (다른 PC에만 있는 것)
- **PC가 폰 핫스팟에서 회사 Wi-Fi(`MSFTCONNECT`)로 이탈** — 우리 PC엔 없는 요인. 아래 중 하나:
  ```powershell
  netsh wlan set profileparameter name="MSFTCONNECT" connectionmode=manual
  ```
  또는 폰 **USB 테더링**으로 바꾸면 SSID 로밍 자체가 사라진다(IP만 바꿔 `config.tag-shuttle.local.json`·site 갱신).
- adb `offline`, `HANDLER_FAULT` — 양쪽 공통. `fly3.py`의 프리플라이트 게이트(앱 재시작→90 s)는 좋은 대응이며 저장소에 넣을 가치가 있다.
- 검출 280 ms(GIL 점유로 디코더 스레드 굶김) — main에서 수정. 우리 PC는 CPU가 빨라 같은 코드로도 안 터졌을 뿐이므로 **우리도 이식**한다.

### 4.5 우리 쪽에도 있는 잠재 결함 (main이 먼저 고침)
`observe_frame`의 낡은 프레임 → `InterruptedError`(임무 종료)는 우리 973ec13에도 그대로다. 왕복 지연 p99 63 ms에서도 0.2 %는 150 ms를 넘고, 그 한 번이 59 s 비행을 끝냈다는 기록(§3.3)은 우리에게도 유효하다. 이식 대상: `FramingCorrectionDeferred`로 바꾸고 `_climb`/`traverse_horizontal`에서 제자리 유지·재관측.

---

## 5. 증상 ③: UI에서 오류 1회면 바로 종료

세 층이 겹쳐 있고, 6a268d4는 맨 위 층만 고쳤다.

### 5.1 웹 `app/page.tsx` (일부 수정됨)
- 수정됨: `missionPhase === "aborted" && error` → `halted` 배너(새 작전 시작/결과 보기). 이전엔 `beginReturn`으로 결과 화면 직행.
- 남음: `status === "error"`(relay.error·WS 종료)면 "연결 다시 시도 · 새 작전" 패널만 남고, 누르면 `reset()`. 비행 중이면 아래 5.3 때문에 이미 기체가 정지 요청을 받은 뒤다.

### 5.2 웹 `lib/voiceClient.ts`
`relay.error` 수신 → `onStatus("error")` + 실행 중이면 `closeSession(true,true)`; `ws.onclose` → 같은 처리 + `fallbackResults()`. 즉 **relay가 오류 메시지를 한 번 보내거나 WS가 끊기면 클라이언트가 세션을 닫는다.**

### 5.3 relay `server.py` — 브라우저 WS 종료 = 기체 정지
`ws_endpoint`의 `finally` → `bridge.close()`(`:1385`) → `session.abort_mission()` + `runner.close()`(`:1283-1285`) → `LiveMissionRunner.close()` → `_stop_hardware()` → `drone_stop_mission` → 서비스 `cancel.set()` → 셔틀 `InterruptedError("Mission cancelled…")` → `release_to_rc` → 호버링.
**탭 새로고침·노트북 절전·WS 끊김 한 번이 비행을 끝낸다.** "브라우저가 임무의 소유자"라는 설계 의도지만, 현장에서 이걸 모르면 "연결 오류 한 번에 드론이 멈췄다"로 보인다.

### 5.4 relay `live_mission.py` — 감시자가 단일 오류에 정지 요청 (핵심)
```python
async def _renew_lease(self):            # 2 s마다 drone_get_mission
    try:
        while ...:
            response = await self.drone.call("drone_get_mission", ...)   # 타임아웃 5 s
            ...
    except Exception as exc:
        await self._fail(exc)            # → abort_mission + _stop_hardware
```
`_run_live`도 같다: `_read_mission`, `drone_get_captures`, **`vision.analyze`(Azure VLM, 30 s)** 중 하나라도 예외면 `_fail` → **비행 중단 + 정지 요청**. 재시도는 없다(`retry()`는 항상 거부).
결과적으로 relay↔PC 로컬 HTTP의 5 s 타임아웃 1회, `INVALID_MISSION_EVIDENCE` 1회, VLM 호출 실패 1회가 모두 "기체 정지"로 귀결된다. 단독 셔틀에는 이 층이 없다. 우리가 7/7이고 다른 PC가 아닌 차이의 상당 부분이 여기다.

**수정 (P0-2)**
1. 읽기 실패는 리스(10 s) 예산 안에서 재시도한다.
```python
READ_RETRY_BUDGET_S = 8.0   # 서비스 caller lease 10 s 보다 짧게

async def _read_mission_retrying(self, label):
    deadline = asyncio.get_running_loop().time() + READ_RETRY_BUDGET_S
    while True:
        try:
            return self._mission(await self.drone.call("drone_get_mission", {"mission_id": self._mission_id}))
        except DroneError as exc:
            if exc.code not in {"TRANSPORT_ERROR", "OUTCOME_UNKNOWN", "INVALID_RESPONSE"} \
                    or asyncio.get_running_loop().time() >= deadline:
                raise
            log.warning("%s: transient %s; retrying", label, exc.code)
            await asyncio.sleep(0.3)
```
`_renew_lease`와 `_read_mission`에서 이 함수를 쓴다. `MISSION_FAILED` 같은 실제 실패 상태는 그대로 즉시 `_fail`.
2. VLM 실패는 비행과 분리한다: `_run_live`의 `vision.analyze` 호출을 `try/except (VisionError, asyncio.TimeoutError)`로 감싸고, 실패 시 해당 캡처를 `evidence:null`로 두고 다음 방문지로 진행한다(비행은 계속, 채점만 "분석 실패"). `PromptRevisionRequired`만 예외로 중단 유지.
3. 브라우저 WS 종료 시 정책을 명시적으로 고른다. 최소한 `bridge.close()`에서 **비행 중(`droneState in {"preflight","taking_off","running","returning"}`)이면 `abort_mission()`을 부르지 않고** 임무를 서비스에 맡긴 채 relay만 정리하거나, 환경변수(`RELAY_STOP_DRONE_ON_DISCONNECT=0`)로 끌 수 있게 한다. 리스는 `_renew_lease`가 유지하므로 `runner`는 살려 둬야 한다.

### 5.5 Voice에만 있는 조용한 거부 (출발 전 단계)
`voice_turns.authorize()` 거부는 `{"ok": False, "silent": True}`로 화면에 아무것도 안 보인다(`server.py:1029-1035`). 출발 후 음성 도구 호출은 통째로 무시(`:963`). 비행에는 영향 없지만 "드론 제어가 사라진다"는 인상의 정체다. `silent`를 없애거나 `tool.finished`에 `rejectionCode`를 실어 UI 배너로 노출한다.

---

## 6. Voice/HTTP 경로에만 있는 다음 지뢰 (아직 안 터졌지만 순서상 다음)

1. **캡처 훅의 신선도 증명 3회** (`field.py FieldAdapter.run.capture`): `_capture_proof`(프레임 나이 ≤0.5 s, 정지 ≤0.08 m/s, 높이·배터리·RC) → 1080p PNG 인코드(100–200 ms) → 다시 `_capture_proof` → `emit(arrived)`(sqlite 저장) → 또 `_capture_proof` → `emit(captured)`(4 MB base64 저장). 검출 40 ms 기준으로도 빠듯하고, 280 ms 시절엔 거의 항상 실패했을 구조다.
   `_capture_proof` 안의 `client.observe_frame(snapshot)`이 던지는 `FramingCorrectionDeferred`는 `capture_id1_pair`의 `on_capture` 호출부에서 잡히지 않는다 → `run()`의 `except` → **첫 사진에서 임무 종료**.
   수정 (P1-1): 훅 호출을 감싼다.
   ```python
   # standalone_tag_shuttle.py capture_id1_pair
   if on_capture is not None:
       try:
           accepted = on_capture(client, stream, snapshot, confirmed, diagnostic)
       except FramingCorrectionDeferred as exc:
           client.log_event("id1_pair_capture_deferred", {"reason": str(exc), "stage": "capture_hook"})
           continue
       if not accepted:
           continue
   ```
   그리고 `field.py`에서 증명은 인코드 전 1회 + 발행 직전 1회로 줄인다.
2. `drone_get_captures`는 매 틱 누적 캡처 전체(≤6 × ≤4 MiB base64)를 돌려준다. 5 s 타임아웃 안이지만 5.4의 감시자와 결합하면 위험하다. `since_capture_id` 인자로 증분 조회하게 바꾸는 것이 안전하다.
3. `FieldAdapter`는 목적지 3개 고정이라 3-태그 벽(2-1-6, 목적지 2개)을 못 돌린다. feat/voice-drone-live 워크트리는 프로필에서 목적지 수를 도출한다.
4. `site.json`의 `target_height_m`는 **float**이어야 한다(`type(...) is not float` 검사). `1`이나 `"1.5"`는 거부된다.
5. `NEW_PC_SETUP.md`는 아직 APK `.20260913.1`이라고 적혀 있다. 코드는 `.3`을 요구한다.

---

## 7. 권장 수정 목록 (우선순위)

| 우선 | 항목 | 파일 | 근거 |
|---|---|---|---|
| P0-1 | `ShuttleClient.arm` 권한 대기 2→5 s | `trials/standalone_tag_shuttle.py`, `pc/tests/test_standalone_arm_transition.py` | §3.2 |
| P0-2 | relay 읽기 재시도·VLM 분리·WS 종료 정책 | `relay/live_mission.py`, `relay/server.py` | §5.4, §5.3 |
| P0-3 | 물리 경로를 인접 순서로 고정 | `pc/drone_nav/tool_control/field.py` (+`relay/tools.py` 안내 문구) | §4.1 |
| P0-4 | 시나리오 시계가 실비행을 끊지 않게: `RELAY_SCENARIO_FILE`로 마감 150/180 s 시나리오, 또는 live에서 `too_late` 종료 시 `_stop_hardware` 제외 | `relay/survey.py`, `relay/live_mission.py _watch_deadlines`, `data/emergency-triage*.json` | §10.5 F1 |
| P0-5 | 운영자 인증·쿠키 만료로 비행 중 WS가 닫히지 않게: `RELAY_LOCAL_DIRECT=1`(localhost 전용) 또는 비행 중 watchdog 유예 | `relay/operator_access.py` | §10.1 B4·B5 |
| P1-1 | 캡처 훅의 `FramingCorrectionDeferred` 처리, 증명 횟수 축소 | `trials/standalone_tag_shuttle.py`, `field.py` | §6.1 |
| P1-2 | 두 브랜치 통합 (§9) | — | 같은 폰·같은 벽에서 두 코드가 갈라져 있음 |
| P1-3 | 고도 1.6→1.4–1.5(또는 우리 1.2 이식), `climb_command` +0.30 여유 | `profiles/*.json`, `bounded_sonar_climb.py` | §4.2, §3.3 |
| P1-4 | Wi-Fi 로밍 차단 또는 USB 테더링 | 운영 | §4.4 |
| P2-1 | `status==="error"` 패널이 비행 중 `reset`을 유도하지 않게, 거부 코드 배너 | `app/page.tsx`, `lib/voiceClient.ts` | §5.1, §5.5 |
| P2-2 | `fly3.py` 프리플라이트 게이트를 저장소 스크립트로 | `scripts/` | §4.4 |
| P2-3 | 문서 APK 버전 `.1`→`.3` | `docs/NEW_PC_SETUP.md` | §6.5 |

---

## 8. 검증 절차 (다음 비행에서 로그로 확인할 것)

로그: `%LOCALAPPDATA%\IndustryDayDrone\logs\navigation\*.jsonl` (main) / `pc/logs/*.jsonl` (feat/drone). 타임스탬프 `pc_monotonic_ns`, 페이로드 `data`.

| 이벤트 | 기대 | 어긋나면 |
|---|---|---|
| `standalone_arm_transition` | 첫 샘플부터 `ready:true`까지 ≤ 5 s, 이 PC 기준 ≤ 125 ms | 폰/APK 문제 (§3.2 A/B) |
| `standalone_ground_proof_settled` / `_failed` | `attempts` 소수, `failed` 없음 | `HANDLER_FAULT`·ages 확인 |
| `ground_video_preflight` | `decoded_frames` 증가, `pc_state STREAMING` | APK `.3` 설치 여부 |
| `standalone_climb_tag_miss` / `standalone_climb_deferred` | 각 ≤ 2회 | ID0 시야·프레임 지연 |
| `id1_pair_framing_sample.visible_ids` | 구간마다 목표 태그가 2.5 s 이상 보임 | §4.3 |
| `id1_pair_blind_image` | 없거나 소수 | 벽 방향·거리 |
| `standalone_interrupted.traceback` | 없음 | 마지막 프레임이 호출 지점 |
| relay 로그 `Tool mission failed (CODE, ExcType)` | 없음 | CODE로 §5.4 분기 |

수용 기준: 인접 경로(6→1→2→3→2→1→6)로 **연속 5회** `visited_ids`가 전체 경로와 같고 `route_completed:true`. 그 뒤에 Voice로 같은 경로를 3회.

두 APK A/B (§3.2): 같은 폰에 `.6`·`.3`을 번갈아 설치하고 각 PC 코드의 `BUILD_ID`에 맞춰 3회씩 arm 확정 시간을 기록한다. `.3`만 느리면 `2a1445d`의 Android 변경을 반으로 나눠 빌드해 이분한다.

---

## 9. 브랜치·APK·토큰 정합성 (두 PC가 같은 폰을 쓸 때)

- 폰에는 APK가 하나만 있다. `.20260910.6`이면 feat/drone만, `.20260913.3`이면 main만 난다. **PC 코드를 바꾸면 APK도 바꿔야 한다.** `bridge_build_id` 불일치는 `… bridge … required`로 이륙 전에 거부된다.
- APK에 구운 `OPERATOR_ARM_TOKEN`은 그 PC의 `config.tag-shuttle.local.json` `network.confirmation_token`과 같아야 한다. `~/.gradle/gradle.properties` 또는 `android-private-build.properties`에서 무엇이 주입됐는지 빌드 로그로 확인.
- 통합 제안: main을 기준으로 다음을 이식해 한 브랜치로 만든다.
  - feat/drone → main: 프로필 기반 `wall_ids`/`route_ids`/`home`/`outbound_leg_count`, `validate_profile` 1.0–1.6 m, `climb_command` +0.30 여유, `standalone_tag_216.json`, `test_profile_216.py`, `START_TAG_SHUTTLE.ps1` 기본값.
  - main → feat/drone: `_await_ground_proof`, `_prepare_ground_video`, `FramingCorrectionDeferred` 정책, `CLIMB_TAG_MISS_GRACE_S`, 검출 최적화(`vision.py undistort/detect_undistorted`, `nthreads=4`), traceback 로깅, blind-strip 진단.
  - 이 PC의 `feat/voice-drone-live` 워크트리(origin/main f29a94a 기반, 미커밋)는 위 두 방향을 이미 부분 통합했고 `FieldAdapter`를 프로필 기반·목적지 N개로 일반화했다. main이 f29a94a 이후 셔틀·`field.py`·`live.py`를 고쳤으므로 그 위에 재적용(rebase)해야 하며, 충돌 지점은 `standalone_tag_shuttle.py`의 `run()`·`validate_profile`·`capture_id1_pair`와 `field.py`다.
- 비밀 파일(`config.tag-shuttle.local.json`, `control.env`, `relay/.env`, `site.json`)은 계속 Git 밖. 대화에 노출된 토큰은 교체.

---

## 10. Voice로 바꿀 때 생길 수 있는 문제 전체 목록 (층별)

단독 셔틀 CLI에는 없고 Voice(relay) 경로에만 있는 실패 지점을 **입력에서 기체까지 순서대로** 나열한다. 세 증상(§3–5)에 안 들어간 것도 포함한다.
"상태" 열: ✅ main에 해결됨 / ⚠️ 설계상 의도지만 현장에서 알아야 함 / ❌ 미해결.

### 10.1 브라우저·대시보드 (`lib/voiceClient.ts`, `app/page.tsx`, relay 접근 제어)

| # | 문제 | 어디서 | 보이는 것 | 상태 / 해결 |
|---|---|---|---|---|
| B1 | 마이크 권한·AudioWorklet 실패 | `VoiceSession.start()` | "음성 연결을 시작하지 못했습니다. 마이크 권한과 음성 서비스 설정을…" | ⚠️ 브라우저 권한, HTTPS/localhost 필요 |
| B2 | 웹과 relay의 입력 창 프로토콜 불일치 | `relay.ready.turnTaking != "after-playback-v1"` → `VOICE_TURN_TAKING_UNSUPPORTED` | "음성 서버와 화면의 버전이 달라. 서버를 업데이트하고 새로고침해 줘." | ⚠️ 웹·relay를 **같은 커밋으로** 빌드(`next build` 재실행) |
| B3 | Origin 거부 | `BrowserAccessMiddleware` (`RELAY_WEB_ORIGINS`/`RELAY_WEB_ORIGIN_REGEX`) | WS가 1008로 즉시 닫힘, HTTP 403 "Dashboard origin is not allowed" | ⚠️ LAN IP·다른 포트로 열면 막힘. `http://127.0.0.1:3000`로 열거나 regex에 추가 |
| B4 | 운영자 인증 요구 | `operator_access.required()`: `DRONE_CONTROL_MODE=live`이고 `local_direct()`가 아니면 | `OPERATOR_AUTH_REQUIRED` "운영자 인증이 필요합니다…" 후 1008 | ⚠️ `RELAY_LOCAL_DIRECT=1` 또는 origins를 localhost 전용으로 두면 면제. 아니면 `RELAY_OPERATOR_TOKEN`(32자+) 또는 `/operator` 쿠키 로그인 |
| B5 | 쿠키 세션 만료 시 WS 강제 종료 | `operator_access.session_watchdog` → `browser.close(1008, "Operator session expired")` | 비행 중 화면이 끊기고 §5.3에 따라 **기체 정지 요청** | ❌ 비행 중엔 만료를 막거나 5.3 정책을 바꿔야 함 |
| B6 | `/api/config`는 마운트·시작 시 2회만 조회 | `page.tsx:103,155` | 기체가 나중에 연결돼도 새로고침 전까지 "실제 출발은 준비되지 않았습니다" 배너 | ⚠️ 새로고침 |
| B7 | `relay.error` 1건·WS 종료 1건이면 세션 종료 | `voiceClient.ts:620-655` | "릴레이 연결이 끊어졌습니다 (코드 N)" → 결과 화면 fallback | ⚠️ §5.2 |
| B8 | 결과 음성 미재생 / 디브리핑 2회 실패 | `mission.debrief.failed`, `OSError("Result narration failed after retry")` | error 상태로 종료 | ⚠️ 결과 화면은 남음 |
| B9 | 출발 안내 음성 2회 실패 | `mission.launch.failed` | "출발 음성 안내를 완료하지 못했습니다. 자동 작전은 계속…" | ✅ 비행은 계속됨 |
| B10 | 오류 중단 시 결과 화면 직행 | `page.tsx` | — | ✅ 6a268d4에서 `halted` 배너로 수정 |

### 10.2 relay ↔ Azure Voice Live (`relay/server.py`, `relay/config.py`)

| # | 문제 | 어디서 | 보이는 것 | 상태 / 해결 |
|---|---|---|---|---|
| V1 | 인증이 `DefaultAzureCredential`뿐 (API 키 경로 없음) | `server.py:128-131, 604, 1326` | "Azure 음성 인증에 실패했습니다. PC에서 Azure 로그인을 완료한 뒤…" | ⚠️ relay PC에 회사 계정 `az login` 필수. 이 노트북(Windows Home·governance policy)이 막힌 이유. 키를 쓰려면 `{"api-key": …}` 헤더 지원을 추가해야 함 |
| V2 | Voice Live WS 연결·설정 실패 | `websockets.connect(open_timeout=30)`, `configure_voice` → `VOICE_SETUP_FAILED` | "Azure 음성 세션 설정을 완료하지 못했습니다. 리소스와 연결을 확인하세요." | ⚠️ `VOICE_LIVE_RESOURCE/REGION/API_VERSION(2026-07-15)/MODEL/VOICE` 확인 |
| V3 | VAD가 발화 끝을 못 잡음 | `build_session()` `turn_detection`(`VOICE_LIVE_VAD_TYPE/THRESHOLD/SILENCE_MS…`) | 말했는데 응답·도구 호출이 없음 | ⚠️ 현장 소음에 맞춰 `VOICE_LIVE_SILENCE_MS`/threshold 조정, `voice-trace`로 `speech_stopped` 확인 |
| V4 | 입력 창 프로토콜 위반 | `VOICE_INPUT_PROTOCOL_INVALID` | relay.error | ⚠️ B2와 같이 버전 일치 |
| V5 | 입력 6 s 타임아웃·전사 실패 | `INPUT_TIMEOUT_SECONDS=6`, `recover_input`, `report_input_failure` | "못 들었어요" 류 재질문 | ✅ 재질문 흐름 있음 |
| V6 | 응답 실패·경로 안내 정체 | `wait_for_failed_response`, `ROUTE_INTRO_RESPONSE_STALLED` | 경로 안내 재시도/실패 문구 | ✅ 재시도 있음 |
| V7 | 출발 뒤 음성 세션 종료 | `stop_departure_voice()` → upstream close | 이륙 후에는 말해도 반응 없음 | ⚠️ 설계. 비행 중 음성 명령(중단 등)은 없음 |

### 10.3 음성 턴 관문 (`relay/voice_turns.py`) — 전부 **화면에 표시되지 않는 거부**

| 거부 코드 | 언제 | 해결 |
|---|---|---|
| `stale_or_missing_turn` | 마지막 참가자 발화가 없거나 이미 소비됨(모델이 혼자 이어 말한 뒤 도구 호출) | 질문 뒤 참가자가 실제로 답해야 함 |
| `transcript_timeout` | 발화 전사가 5 s 안에 안 옴 | 전사 모델(`VOICE_LIVE_TRANSCRIPTION_MODEL`)·네트워크 |
| `superseded_turn` / `empty_transcript` | 그 사이 새 발화 / 전사 비어 있음 | 다시 답하기 |
| `wrong_stage` | 발화 당시 단계(prompt·route·phase)가 지금과 다름 | 현재 질문에 새로 답하기 |
| `description_missing` | 탐색 설명 대신 "네"만 말함 | 설명을 말해야 함 |
| `not_consent` / `no_pending_readback` | 되읽기 뒤 긍정이 아님 / 되읽기가 없었음 | "네/맞아요/출발해" 등 `AFFIRMATIVES`로 답 |
| `stop_mismatch` | `select_stop` 인자와 발화의 별칭(`STOP_ALIASES`: 바다·잔해·불…)이 안 맞음 | 목적지를 별칭으로 말하기 |
| `departure_not_confirmed` | 경로 되읽기(`route_readback_done`) 뒤 새 긍정이 없음 | 되읽기 듣고 "출발해" |

단계별 허용 도구(`tools.voice_context`): 설명 미확정 → `prepare_prompt`/`confirm_prompt`; briefing → `select_stop`/`clear_route`/`confirm_route`; ready → +`launch_mission`; 출발 후 → `retry_mission`/`abort_mission`/`get_state`만, 그리고 **`retry_mission`은 live에서 항상 거부**. 거부는 `silent:true`라 relay 로그(`Blocked voice action …`)와 `RELAY_VOICE_TRACE_DIRECTORY`의 `rejected` 레코드로만 보인다. 해결은 §5.5.

### 10.4 relay `launch()`·서비스 승인 (§2.1·§2.2에 더해)

| # | 문제 | 어디서 | 상태 / 해결 |
|---|---|---|---|
| L1 | `TRIAGE_MODE=azure` 아니면 live 출발 거부 | `live_mission.launch` | ⚠️ Azure Vision env(`AZURE_VISION_ENDPOINT/DEPLOYMENT/API_VERSION/API_KEY` 또는 자격증명) 필요 |
| L2 | `live_readiness_issue` 6가지 | `drone_status.py` | ⚠️ `GROUND_UNVERIFIED`는 캐시 0.5 s·ages 500 ms 기준이라 폰 폴링 0.54 s와 겹쳐 간헐 거부 가능 → 다시 "출발해" |
| L3 | `DRONE_BUSY` | 서비스 sqlite에 이전 임무가 active로 남음(비정상 종료 뒤) | ⚠️ 이전 임무 `drone_stop_mission` 또는 DB 파일 교체. `NEW_PC_SETUP`도 "이전 임무 DB 재개 금지" |
| L4 | 경로 순열 6가지 허용 | `service.py:191-192` | ❌ §4.1 |
| L5 | 목적지 3개 고정, `[3,2,1,6]`·home 6·floor 0·float 고도·build `.3` | `field.py` | ❌ 3-태그 벽 불가(feat/voice-drone-live에서 일반화) |
| L6 | 토큰·URL·모드 헤더 | `drone_client.py` | ⚠️ `DRONE_CONTROL_API_TOKEN` 24자+ 양쪽 동일, `127.0.0.1:8766`, `X-Drone-Expected-Mode`(`MODE_MISMATCH`) |
| L7 | HTTP 5 s 타임아웃, `OUTCOME_UNKNOWN`(쓰기)·`TRANSPORT_ERROR`(읽기)·`INVALID_RESPONSE`·`IDEMPOTENCY_CONFLICT` | `drone_client._request` | ❌ 1회 실패가 §5.4로 이어짐 |
| L8 | 호출자 리스 10 s | `service.py` `_watch_lease` → `caller_lease_expired` | ⚠️ relay 이벤트 루프가 10 s 이상 막히면 서비스가 임무를 정지. relay는 2 s마다 갱신 |

### 10.5 비행 중 Voice 경로 전용 (셔틀 CLI엔 없음)

| # | 문제 | 어디서 | 상태 / 해결 |
|---|---|---|---|
| F1 | **시나리오 시계가 실비행을 끊음** | `survey.launch_mission()`이 `drone_execute_route` 승인 직후(**이륙 전**) 시계를 켬. `data/emergency-triage.json` 마감: monitor-3 **18 s**, monitor-1 **28 s**, monitor-2 **45 s**(`injuryWindowMs` 5 s). VLM이 그 전에 `targetPresent`를 주지 못하면 45 s에 셋 다 `too_late` → `_finish_if_resolved` → `missionPhase=complete` → `_watch_deadlines`의 `expired_terminal` → **`_stop_hardware()`** | ❌ 실제 비행은 이륙→ID1 도착만 25–30 s, 전체 77–100 s. fly3.py(직접 HTTP)로 날 땐 relay가 없어 이 시계가 없었다. 해결: feat/voice-drone-live처럼 `RELAY_SCENARIO_FILE`로 마감 150/180 s 시나리오를 쓰거나, live 모드에서 `too_late`로 인한 `_stop_hardware`를 제외 |
| F2 | 감시자 즉시 중단 | `_renew_lease`/`_run_live` → `_fail` | ❌ §5.4 |
| F3 | 브라우저 WS 종료 = 정지 요청 | `ws_endpoint finally → bridge.close()` | ⚠️ §5.3 |
| F4 | 캡처 훅 신선도 증명 3회 + PNG + sqlite | `field.py capture()` | ❌ §6.1 |
| F5 | 방문마다 **서로 다른 프레임 2장** 필수 | `field.py run()` 마지막 검사 | ⚠️ 1장이면 `route_completed`라도 `outcome_unknown`·`error="…without two distinct captures"` |
| F6 | `FieldVideoStream` 픽셀 동일성 검사 | `field.py FieldVideoStream.detect_latest` | ⚠️ detector를 바꾸면 `Wall detection pixels do not match the decoded snapshot` |
| F7 | `VideoBroker` 공유 스트림 | `camera.py` | ⚠️ 미리보기(`/camera/*`, `/ws/camera`)와 임무가 한 스트림. 임무 중엔 새 폰 연결 없음(폰 리더 1개). 임무 밖에서 미리보기 lease 5 s 만료 시 스트림 닫힘 |
| F8 | `emit`이 비행 루프 안에서 sqlite에 동기 저장 | `service._emit` | ⚠️ 4 MB base64 저장이 10 Hz 루프를 잠깐 막음(수십 ms). F4와 합쳐지면 위험 |
| F9 | 정지 후 상태 | `awaiting_rc_landing`/`verification_pending`/`droneStopState` | ⚠️ 어떤 경우든 착륙은 RC. UI가 "정지 확인"을 못 하면 `unknown` |
| F10 | VLM 실패가 비행 중단으로 | `_run_live` `vision.analyze` 예외 → `_fail` | ❌ §5.4 수정 2 |

### 10.6 폰·APK
`BUILD_ID`/`expected_bridge_build_id`/`OPERATOR_ARM_TOKEN` 정합(§9), `MODE_STATE_GRACE_MS`(§3.2), `FrameBuffer`(§3.1). Voice 여부와 무관하지만 Voice 스택은 PC 코드가 main이라 APK `.3`이 필요하다.

---

## 11. 이 PC에서 연결 끊김·불안정 문제로 시도한 것과 결과 (시간순)

"연결"은 세 가지를 뜻했다: ① 폰 앱 ↔ DJI(FC 조회 실패·권한 회수), ② PC ↔ 폰(핫스팟 IP·adb·영상), ③ PC 로직의 신선도 게이트가 정상 상태를 끊김으로 오판. 아래는 실제로 해 본 것만 적는다. 출처: `DRONE_PROJECT_LOG.md`, `FIELD_STATE_20260910.md`, `pc/logs/*.jsonl`, `docs/fix_ready_20260907/`.

### 11.1 08-30 ~ 09-01 (앱 복구·제어 경로 확립)

| 시도 | 결과 |
|---|---|
| 폰 화면이 꺼지면 앱 서버가 죽음 → 서버 기동을 Activity 수명주기가 아닌 **SDK 등록 성공 시점**으로 이동 | ✅ 해결 |
| 무선 adb 인증 잦은 만료, APK 232 MB 무선 설치 10분+ | ✅ **USB 설치·USB adb**로 통일 |
| `CONTROL_AUTH_TAKING_OFF`(자동이륙 중 arm) | ✅ `_arm_when_ready()` 재시도(보통 3회) |
| `CONTROL_AUTH_HAS_NO_CONTROL_AUTH`에서 해제 재시도 200회 폭주 | ✅ 조기 반환 + `MAX_RELEASE_ATTEMPTS=10` |
| 고도 텔레메트리가 정지 중 갱신을 멈춰 신선도 게이트 교착 | ✅ 당시 `_altitude_command` age 게이트 제거 (지금은 하방 표시 높이 + 시각 교차검사로 대체) |
| "지연 줄이려고" `client.status()`를 뺐다가 대기 루프 전부 고장(텔레메트리는 ACK에서만 갱신) | ✅ keepalive 4곳 복구, 모든 `limiter.wait()` 감사 |
| 엄격한 `sequence == last+1` 게이트가 프레임 1개 유실에 래칭 | ✅ 단조 증가로 완화(`AdvancedControlCommandHandler:62`) |
| 워치독 1500/2000 ms를 늘리자는 제안 | ❌ 거부. 실내에서 무명령 2.5–5 m 주행 위험. `stale_zeros` 계측만 추가 |
| 장애물 회피가 비공개 키(`VisionAvoidEnable`)로는 안 꺼짐(#716) | ✅ `PerceptionManager.setObstacleAvoidanceType(CLOSE)` + `oa_braking` 방향 계측 |
| 횡이동 실패의 근본(Basic 스틱, FC 실내 억제 #594, Sport 모드 검토) | ✅ 결론: 공식 Advanced **ANGLE** 모드(±1.5°/0.6°)로 전환해 해결(09-07~09-10) |

### 11.2 09-07 `fix_ready_20260907` — FC `REQUEST_HANDLER_NOT_FOUND` 근본 수정 계획
`FC_PERCEPTION`/`QUERY_TRANSPORT`/`VIDEO`/`PC_TELEMETRY` 4개 명세. 이 중 `FcHealthTracker`·`FcRecoveryCoordinator`·`MaintenanceGate`·`PendingSdkReads`·`GroundProof`는 브리지에 구현돼 main에 있다(`PcBridge.ensureRecovery`). **자동 복구는 `AUTOMATIC_FC_RECOVERY=false`로 꺼져 있다**("수동 SDK 변경 경로가 전부 gate를 안 거침"). 그래서 `HANDLER_FAULT`의 유일한 복구는 여전히 USB 재연결·앱 재시작이고, 다른 PC의 `automatic_fc_recovery_enabled:false / MANUAL_SDK_MUTATION_PATHS_NOT_FULLY_GATED`도 같은 것이다. 최초 발생 원인은 양쪽 다 미입증.

### 11.3 09-10 비행 16회 — 로그 기준 중단 원인과 조치 (`pc/logs`)

| 시각 | 결과 | 중단 원인(로그 `error`) | 조치 |
|---|---|---|---|
| 13:45 | 11.8 s | `arm rejected` (ACK 로그) | 토큰/권한 확인 |
| 13:50 | 13.2 s | `Fresh video not confirmed before takeoff` | 폰 영상 재기동 |
| 13:53 | 63.6 s, [6] | `ID1 not confirmed within 45s` | 구도 로직(Codex 오후 작업) |
| 14:15 | 0.1 s | `Fresh motors-off grounded RC state and bridge .6 required` | FC 조회 실패 → **USB 재연결** |
| 14:15 | 17.2 s | `no fresh height for climb` | 하방 높이 신선도 |
| 14:19 | 19.8 s | `Vertical correction exceeds corridor envelope` | 상승 제어 한도 |
| 14:21 | 12.9 s | **`Control authority changed; no automatic re-arm`** | 폰이 VS를 RC로 회수(당시 APK grace 1 s). 재시도로 통과 |
| 14:25 | 34.8 s | `Wall home ID6 was not centered` | 홈 중앙 정렬 요구 제거 → "보이기만 하면 확인" |
| 14:32 / 14:43 | [6] | `PairFramingError: …vertical_outside…` / `…too_wide…` | 구도 참조 재측정(`id1_tv_pair_reference.json`) |
| 14:51 | 65.5 s, [6] | `ID1 and mock footprint not framed within 45s` | **지나침 뒤 0.25° 펄스로 복귀 불가** → 연속 비례 보정+역방향 재탐색(`id1_pair_framing.py` 재작성) |
| 15:18 | 0.1 s | `Fresh motors-off grounded RC state … required` | `REQUEST_HANDLER_NOT_FOUND` → **USB 재연결·앱 재실행** |
| 15:23 | 39.9 s, [6] | **`Fresh airborne state lost; no resume`** — `is_flying` age 517 ms(armed·MSDK·고도 전부 정상) | 폰 IsFlying 폴링 0.54 s vs PC 한계 0.5 s → **`FLIGHT_STATE_FRESH_S=1.5`** (비행 플래그만; 영상·속도·고도는 0.5 s 유지) |
| 15:26 | **100.4 s 완주 [6,1,2,3,2,1,6]** | — | — |
| 15:34 | 66.7 s, [6,1,2] | `Height outside validated corridor envelope` — 하방 1.4→0.39 m, 수직속도 0 | 경로 아래 물체. **검사는 유지**(0.5–1.8 m), 경로 비우기 |

같은 날 로그의 `standalone_arm_transition`: 첫 샘플 `vs_authority:RC` → 125 ms 뒤 `MSDK`. 우리 폰·APK `.6`의 권한 확정은 이 정도다(§3.2 표).

### 11.4 09-11 (2·1·6 벽, 1.2 m, 6→1→2→1→6)
- 사전 점검이 FC 키 `REQUEST_HANDLER_NOT_FOUND`로 거부 → USB 재연결·앱 재실행.
- 복구 뒤 자동 출발용 `wait_fc_then_patrol.py`(스크래치): 폰 9997에 `GET FlightController IsFlying/AreMotorsOn`을 2 s마다 조회해 **둘 다 false가 3 s 연속**이면 `START_TAG_SHUTTLE.ps1 -Mode Patrol` 실행(그 안에서 지상·모터·RC·배터리·영상 재검증). 다른 PC의 `fly3.py` 게이트와 같은 발상이며, 저장소에 넣을 가치가 있다.
- 폰 IP가 핫스팟마다 바뀜(172.16.251.9 → 10.244.155.20) → `-PhoneIp` 인자로 처리. PC는 폰 핫스팟 SSID에 붙어 있어야 함(우리 PC는 회사 Wi-Fi가 없어 로밍 문제 없음).
- 16:01 **77.5 s 완주 [6,1,2,1,6]**, ID1·ID2 모크 사진. 착륙은 RC.
- 프로필 기반 코드로 바꾸면서 `climb_command`에 +0.30 m 여유(자동이륙 1.1–1.3 m 안착 대비), 고도 허용 1.0–1.6 m.

### 11.5 09-13 Voice 경로 준비 (이 PC에서 한 것)
- `VOICE_LIVE_CONTROL_GAP_20260913.md`: Voice 경로 관문 19개와 real 전환 후 남는 문제 정리.
- 워크트리 `feat/voice-drone-live`(origin/main f29a94a 기반): 셔틀 통합(프로필 기반 경로 + main 훅), `FieldAdapter` 프로필 기반·목적지 N개, `supported_ordered_sequences` 단일, 시나리오 `emergency-triage-216.json`(마감 150/180 s, `RELAY_SCENARIO_FILE`), `start-integrated.ps1` 216 기본값. **미커밋**. main이 그 뒤 셔틀·`field.py`·`live.py`를 고쳤으므로 재적용 필요(§9).
- 오프라인 검증: `field_smoke.py`(216 프로필로 `drone_get_capabilities` 정상, 잘못된 경로·프로필 거부 확인), `toolcall.py`(control.env 토큰으로 :8766 도구 호출). relay 테스트 294(기존 실패 6), PC 367(기존 실패 4). relay `mode=azure`·tools `mode=live` 기동 확인.
- 막힌 것: 이 노트북은 Windows Home·governance policy로 회사 계정 `az login` 불가 → V1 때문에 Voice Live 인증 불가 → **작업 PC로 이관** 결정. 키를 대화로 받는 것은 거절(비밀은 Git·OneDrive 밖 env로).
- `OTHER_PC_LIFT_DIAGNOSIS_20260913.md`: 다른 PC의 "LLM으로도 이륙 안 됨" = APK에 구운 토큰 ≠ PC 토큰(`invalid_confirmation_token`). `arm/takeoff/land`만 토큰 검사(`AdvancedControlCommandHandler` 84–102). 빠른 조치는 PC 설정 토큰을 설치된 APK 값으로 맞추기.

### 11.6 지금도 미해결 (이 PC 기준)
| 항목 | 상태 |
|---|---|
| FC `REQUEST_HANDLER_NOT_FOUND` 최초 원인 | 미입증. 복구는 USB 재연결·앱 재시작뿐(자동 복구 OFF) |
| 폰 권한 확정 지연(APK `.20260913.x`에서만 1.6–3.3 s) | 원인 미특정. §8 A/B 필요 |
| 우리 셔틀의 낡은 프레임 즉시 종료 | main의 `FramingCorrectionDeferred` 이식 필요(§4.5) |
| 하방 거리 0.5–1.8 m 검사 | 의도적으로 유지(사람 위 통과 방지). 경로 아래를 비우는 것으로 대응 |
| 폰 IP 변동 | 수동 `-PhoneIp`. USB 테더링이면 고정 가능 |
