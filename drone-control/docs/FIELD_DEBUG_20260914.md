# 현장 디버깅 기록 — 2026-09-14

Voice/LLM → 드론 제어 복구 작업. 증상은 하나였다: **이륙은 하는데 옆으로 안 가고 끊긴다.**
실제 원인은 여섯 개였고, 그중 다섯 개를 고쳤다.

이 문서는 "무엇을 고쳤나"보다 **"어떻게 알았나"**를 남기려고 쓴다. 측정값 없이 내린
결론은 이 작업에서 네 번 틀렸고, 네 번 다 운용자가 먼저 의심했다. 반증된 가설 목록을
§5에 따로 둔 이유다.

---

## 1. 지금 상태 요약

| 항목 | 상태 |
|---|---|
| 이륙 → 목표고도 상승 | ✅ 성공 (1.5 m 도달 확인) |
| 홈 태그 ID6 인식 | ✅ 성공 (29회 관측) |
| 횡이동 (왼쪽) | ✅ 성공 (4.7 m 이동) |
| 이동 중 태그1 인식 | ✅ 성공 (16회 관측) |
| **태그2 · 태그3 인식** | ❌ **0회 — 최대 미해결 과제** |
| 경로 완주 (6→3→1→2→6) | ❌ 미완 |
| Voice → 드론 제어 | ❌ 미착수 (원인은 특정함, §4.3) |

배터리·기체 손상 없음. 모든 비행은 안전하게 착륙했거나 이륙 전에 중단됐다.

---

## 2. 비행 이력

| 시각 | 로그 | 결과 |
|---|---|---|
| 19:56:11 | `20260914T195611-ac50f96e` | 상승 중 11.4 s에 프레임 만료로 사망 |
| 20:46:30 | `20260914T204630-c5f97bd2` | 59 s 비행, 왕복 지연 1회로 사망 |
| 21:00:14 | `20260914T210014-18eeed88` | 상승·이동 성공, 36.3 s에 **운용자가 RC로 회수** |
| 21:10:48 | `20260914T211048-7f15d082` | 이륙 안 함 (브리지 고장, 안전 거부) |

로그 위치: `%LOCALAPPDATA%\IndustryDayDrone\logs\navigation\`

---

## 3. 해결한 문제

### 3.1 태그 검출이 자기가 쓸 프레임을 굶겨 죽이고 있었다

**커밋** `25b8705` · **증상** `InterruptedError: Detected camera frame expired before command dispatch`

프레임이 `_guard_dispatch`에 도착할 때 이미 350–440 ms 낡아 있었다. 신선도 예산은
500 ms 전체. 즉 모든 attitude 명령이 동전던지기였다.

`MixedDetector.detect()`를 실제 1080p 현장 캡처로 프로파일링:

| 단계 | 비용 |
|---|---|
| `cv2.undistort(frame, matrix, distortion)` | **50.6 ms** — 호출마다 rectify 맵 재생성 |
| `cvtColor` BGR→GRAY | 1.7 ms |
| `detector.detect(gray)` 벽 통과 | 85.0 ms |
| `base.detect(frame)` 바닥 자세 통과 — **같은 원본을 다시 undistort 후 다시 검출** | **141.6 ms** |
| **합계** | **279.9 ms** |

280 ms짜리 검출이 CPU/GIL을 점유해 **다음 프레임을 만들 디코더 스레드를 굶겼다.**
자기가 만든 지연으로 자기가 죽는 되먹임 고리.

`pupil_apriltags.Detector` 기본값이 `nthreads=1`인 것도 확인. 이 장비는 8코어다.

**수정**
- `cv2.initUndistortRectifyMap`으로 맵을 프레임 shape별 캐싱 → `cv2.remap` 사용
- 바닥 자세 통과에 이미 보정된 픽셀을 넘겨 두 번째 undistort 제거
- `Detector(..., nthreads=4)`

**정확도를 속도와 바꾸지 않았다는 증거**
- `remap`은 `undistort`와 **픽셀 단위 동일** (최대 차이 0)
- 저장된 현장 캡처 175장 전부에서 검출 결과 불변
- `nthreads` 스윕: 1→50.5 ms, **4→27.9 ms**, 6→43.0 ms, 8→47.6 ms

**결과** 280 ms → **40 ms** (6.9배)

---

### 3.2 영상 전달률이 0.03 %였다

**커밋** `25b8705` · **파일** `android/.../livevideo/FrameBuffer.java`

`openReader()`가 GOP 중간부터 시작하는 버퍼를 재생해서, 디코더가 첫 키프레임이
나올 때까지 전부 버렸다.

**수정** open 시점에 `trimToNewestKeyFrame()` 호출 + `addFrame()`에 오버플로 가드

**검증** (드론을 띄우지 않고 `/camera/start` HTTP 엔드포인트로 지상 측정)

| | 수정 전 | 수정 후 |
|---|---|---|
| 30 s 전달률 | 0.03 % | **100.00 %** (739/739, 유실 0) |
| 첫 프레임까지 | 4.63 s | **1.23 s** |

---

### 3.3 일시적 프레임 만료가 임무 전체를 죽였다

**커밋** `023f363`

20:46 비행은 홈 ID6에 도달해 ID1로 보정하던 59 s에 죽었다. 폰 왕복 지연 전수 분석:

| 왕복 1099회 | 값 |
|---|---|
| 중앙값 | **16 ms** |
| p99 | 63 ms |
| 150 ms 초과 | **2회 (0.2 %)** |

그 2회 중 **344 ms 한 번**이 결정에 쓰인 스냅샷을 0.17 s → **0.51 s**로 늙혔다.
예산 0.5 s. **0.01 초 차이로 59 초짜리 비행을 통째로 버렸다.**
바로 다음 루프가 만든 프레임은 0.078 s짜리 새것이었다.

**판단** 게이트 자체는 옳다. 실내엔 GPS가 없고 카메라가 유일한 위치 감각이라,
낡은 프레임으로 계산한 횡방향 기울기는 "태그가 **있었던** 곳"으로 기체를 민다.
**틀린 건 반응이다.** 거부는 전선에 아무것도 나가기 전에 일어나고, 조건은 다음
패스에서 저절로 낫는다. 그런데 임무를 끝내버렸다.

**수정**
- `observe_frame()`의 **나이 만료만** `FramingCorrectionDeferred`로 변경
  (`InterruptedError` 서브클래스 → 기존 핸들러가 전부 그대로 잡는다, 하위호환)
- 상승(`_climb`)·벽이동(`traverse_horizontal`) 발사점에 제자리 유지 + 재관측 추가
- **비디오 generation 변경은 치명 유지** — 스트림 재연결이고 저절로 낫지 않는다
- `CLIMB_TIMEOUT_S` / `leg_timeout_s`가 여전히 상한 → 카메라가 영영 안 돌아오면 종료

**검증** 다음 비행에서 `deferred` 이벤트 **0건**, 프레임 만료 재발 없음. 회귀 테스트 추가
(`test_stale_frame_defers_the_command_while_a_new_generation_ends_the_mission`).

---

### 3.4 실패 지점을 알 수 없었다

**커밋** `25b8705` · 중단 핸들러에 traceback 기록 추가.

이전엔 메시지만 있고 호출 지점이 없어 현장 실패가 전부 미귀속으로 남았다.
이 로깅이 §3.1과 §3.3의 원인을 **둘 다** 특정했다.

---

### 3.5 설정이 코드에 하드코딩돼 있었다

**증상** 목표고도를 1.5 m → 1.6 m로 바꾸려는데 **코드 3곳**을 고쳐야 했다.

```python
# field.py (수정 전)
home_tag_id, floor_tag_id, target_height_m = 6, 0, 1.5
...
or site["target_height_m"] != 1.5          # 리터럴 고정
...
if self.profile["target_height_m"] != 1.5: # 리터럴 고정
```

검사의 **의도**는 옳았다 — site와 profile이 어긋난 채 날면 안 된다. 리터럴로 고정한
방식이 틀렸다.

**수정** site/profile에서 값을 읽고 다음 둘만 검사한다.
1. site와 profile이 **서로 일치**하는가
2. 값이 상승 제어기가 검증된 **범위(1.4–1.6 m)** 안인가

```python
target_height_band_m = (1.4, 1.6)
...
self.target_height_m = site["target_height_m"]
if self.profile["target_height_m"] != self.target_height_m:
    raise ValueError(f"Site asks for {...}m but the profile climbs to {...}m")
```

이제 고도 변경은 **설정 파일 2개만** 고치면 된다. 안전 성질(불일치 탐지)은 그대로다.
기존 테스트는 더 나은 것을 검증하게 됐다 — 리터럴이 아니라 불일치를 잡는다.

> ⚠️ `live.py`의 `LiveAdapter`에 같은 패턴(`site["target_height_m"] != 1.4`)이 잔존.
> 이번엔 실제 사용 중인 `FieldAdapter`만 고쳤다.

---

### 3.6 지상 증명이 너무 빨리 포기했다

**증상** 21:10 비행은 `no_flight_action_dispatched: true`로 **이륙 시도조차 안 했다.**
`RuntimeError: Fresh motors-off grounded RC state and bridge ... required`

프리플라이트에서 `fc=HANDLER_FAULT`라 텔레메트리가 전부 `None`이었고 4 초 만에 포기.
**그런데 그 브리지는 291 초에 스스로 회복했다.** 기다렸으면 날았다.

검사 자체는 안전상 필수다 — 지상·모터오프 증명 없이 이륙 금지. 원저자 주석이 이미
조정 레버를 지정해뒀다:

> *"Keep requiring a genuinely fresh proof; **only allow more time** to obtain one."*

**수정** `GROUND_PROOF_TIMEOUT_S` 4.0 → **30.0** (`live.py`, `standalone_tag_shuttle.py`)
낡은 텔레메트리는 여전히 절대 수용하지 않는다. 묻는 시간만 늘렸다.

---

### 3.7 오류 1번에 웹 UI가 종료화면으로 직행했다

**파일** `app/page.tsx`

```js
const terminal = next.missionPhase === "complete" || next.missionPhase === "aborted";
if (!advancedToResultsRef.current && terminal) {
  advancedToResultsRef.current = true;
  ... beginReturn(next)   // → 결과화면. 선택지 없음.
}
```

`"paused"`일 땐 "다시 시도 / 작전 중단"을 주면서 `"aborted"`는 선택지 없이 디브리핑으로
보냈다. 기체는 그냥 멈춘 것뿐인데 화면은 "작전 끝"으로 읽힌다.

**릴레이는 건드리지 않았다.** `live_mission.py:185-188`의
`"실제 비행 오류 후에는 자동 재개하지 않습니다"`는 의도된 안전 정책이다.

**수정** 오류로 중단된 경우 지도에 머물고 운용자에게 선택지를 준다.

```js
if (next.missionPhase === "aborted" && next.error) {
  setHalted(next.error);          // 지도 유지 + 사유 표시
} else { ...기존 흐름... }
```

기존 오류 배너에 **새 작전 시작** / **결과 보기** 버튼 추가. `tsc --noEmit` 통과.

---

### 3.8 런처가 고장난 브리지에 임무를 던졌다

**파일** `%TEMP%\fly3.py` (저장소 밖 운영 스크립트)

프리플라이트 게이트 추가:
1. `drone_get_status`로 `connected` / `fc == HEALTHY` / 배터리 ≥ 25 % / `is_flying == False` 확인
2. 25 초 대기 → 안 되면 **adb로 폰 앱 재시작** → 90 초 재대기
3. 그래도 불량이면 **이륙 시도 없이 중단**

실제로 작동했다 — 폰이 네트워크에서 빠진 상태를 감지하고
`bridge still unhealthy; not launching (no takeoff attempted)`로 거부했다.

---

## 4. 아직 해결 못한 문제

### 4.1 🔴 태그 2와 3을 한 번도 못 봤다 (최우선)

21:00 비행에서 `visible_ids` 기준 실측:

| 태그 | 실제 관측 | 처음 | 마지막 |
|---|---|---|---|
| 6 (홈) | 29회 | 17.5 s | 21.7 s |
| 1 | 16회 | 24.4 s | 26.9 s |
| **2** | **0회** | — | — |
| **3** | **0회** | — | — |

시야 변화:
```
17.5s [6] → 21.9s 없음 → 24.4s [1] → 27.0s 없음 → (36.2s까지 계속 없음)
```

- 방향은 **맞았다**: 벽 배치가 좌→우 `[3, 2, 1, 6]`이고 6 다음에 1을 봤다
- 이동도 **했다**: 왼쪽으로 **4.7 m** (18.8 s간, 0.2–0.3 m/s)
- 태그1 이후 **9.3 초 / 약 2.8 m를 완전 실명 주행**
- 6→1 간격은 2.7 초였는데 1→2는 9.3 초를 넘겼다 (약 3.4배)

> **기록해 둘 자기 오류:** 처음엔 "태그3을 252번 봤다"고 보고했다. 틀렸다. 그건
> `expected_id`/`tag_id`(목표)를 센 것이고 **관측이 아니다.** 실제 관측은 `visible_ids`에
> 있고, 태그3은 0회다. 목표 필드를 관측으로 읽는 실수는 반복되기 쉬우니 명시해 둔다.

**모르는 것** 태그2가 물리적으로 없는지, 가려졌는지, 기체가 벽에서 멀어졌는지.
전후축(`forward_tilt_deg`)이 **한 번도 지령되지 않아서**(항상 0) 19 초간 벽과의 거리가
무제어로 표류한다. ID6 캡처 사진을 보면 태그가 **유리 파티션**에 붙어 있고 기체가
상당히 가까이 있다.

**다음 단계 (코드는 이미 반영, 비행만 남음)**
`capture_id1_pair`에 **실명 구간 진단 프레임 저장** 추가 — 태그가 하나도 안 보일 때
1.5 초마다, 최대 12장. 진단 전용이며 어떤 게이트도 통과시키지 않는다.

```python
if not tags and snapshot is not None and blind_images < 12 \
        and time.monotonic() - blind_last_s >= 1.5:
    write_image(logger.photo_root / f"diagnostic_blind_{blind_images:02d}_to_ID{expected}.jpg", ...)
```

다음 비행이면 "태그2가 거기 있었나"가 즉시 판명된다.

---

### 4.2 🟡 초음파 고도가 바닥 물체에 속는다

21:00 비행에서 `height_m`이 1.5 → **0.9** → 1.3으로 움직였다. 처음엔 기체가 가라앉는
줄 알고 고도 유지 패치(`_lateral_hold_up_mps`)를 작성했다. **틀렸다.**

| 증거 (26–31 s) | 값 |
|---|---|
| `velocity_down_mps` | **0.000 내내** (최대 0.00, 최소 0.00) |
| 실제 `height_m` 변화 | −0.20 m |
| 속도 적분 예상 변화 | +0.000 m |

실제로 하강했다면 하강속도가 찍혀야 한다. 0이다. 즉 **기체는 제자리였고, 초음파가
바닥 대신 그 밑을 지나던 약 0.5 m 높이 물체를 읽었다.**

**작성했다가 되돌린 수정** `climb_command` 기반 고도 유지를 두 횡이동 경로에 배선했었다.
그대로 뒀다면 드론이 물체 위를 지날 때마다 **센서 착시를 쫓아 0.5 m씩 천장 방향으로
상승**했을 것이다. `git checkout --`으로 전량 폐기했다.

**남은 위험** 이 고도는 "지면 위 높이"가 아니라 "바로 밑 물체 위 높이"다.
`_climb`의 목표 확인과 `_visual_floor_height_m` 교차검사도 같은 센서에 의존한다.
바닥에 물건이 있으면 상승 목표가 어긋날 수 있다. **이 신호로 고도 제어기를 만들지 말 것.**

---

### 4.3 🟡 Voice 경로 — 원인은 특정했으나 미착수

드론 쪽 경로는 LLM과 Voice가 **동일**하다. 오늘 고친 것은 양쪽 모두에 유효하다.

```
LLM  ─┐
      ├─→ relay tools.py → :8766 → 폰 → 드론
Voice ─┘
```

바뀌는 것은 **호출을 누가 만드느냐**다.

| | LLM | Voice |
|---|---|---|
| 입력 | 텍스트 | PCM16 24 kHz 오디오 (WebSocket) |
| 판단 | LLM 응답 | Azure Voice Live |
| 호출 신호 | tool call | `response.function_call_arguments.done` |
| 통과 검사 | 없음 | **`voice_turns.authorize()`** ← Voice에만 존재 |

**문제 1 — 조용한 거부** (`relay/voice_turns.py:227`)

도구 호출마다 다음을 전부 통과해야 한다.

| 조건 | 거부 코드 |
|---|---|
| 최신 미소비 발화 턴이어야 함 | `stale_or_missing_turn` |
| 전사가 5 초 안에 완료 | `transcript_timeout` |
| 전사가 비어있지 않음 | `empty_transcript` |
| 현재 단계에 맞는 답변 | `wrong_stage` |
| 확인 단계면 긍정 응답 | (확인 거부) |

거부 결과는 `{"ok": False, ..., "silent": True}` — **운용자에게 아무 표시가 없다.**
"드론 제어가 사라진다"던 증상의 정체다.

**문제 2 — 출발 후 음성 무시** (`relay/server.py:963`)

```python
if self.departure_started:
    log.warning("Ignoring a voice tool call after departure")
    continue
```

이륙 뒤 음성 도구 호출이 통째로 무시된다.

**제안** 관문은 유지하되 거부를 **가시화**한다 (`silent: True` 제거 또는 UI 배너 노출).
`departure_started` 무시는 안전 의도가 있을 수 있으므로 소유자 확인 후 판단.

---

### 4.4 🟡 연결 안정성

오늘 반복 발생:
- 폰 adb가 `offline`으로 빠짐 (재연결로 복구)
- 폰 브리지 `HANDLER_FAULT` (앱 재시작 필요)
- **PC가 폰 핫스팟에서 회사 Wi-Fi(`MSFTCONNECT`)로 이탈** → 폰 `10.244.155.20` 도달 불가

마지막 항목이 가장 잦다. PC가 자동으로 다른 SSID에 붙으면 스택 전체가 죽는다.
`fly3.py` 프리플라이트 게이트가 이제 이걸 **이륙 전에** 잡아낸다.

브리지 `HANDLER_FAULT`에는 자동 복구가 없다 —
`automatic_fc_recovery_enabled: false`,
`recovery_reason: MANUAL_SDK_MUTATION_PATHS_NOT_FULLY_GATED`,
`unsafe_intent_latched: true`. 현재는 앱 재시작이 유일한 복구 경로다.

---

### 4.5 ⚪ 미해결 잔여

| 항목 | 상태 |
|---|---|
| 17:33 `cv2.error` | 이후 4회 비행에서 재현 안 됨. 원인 불명 |
| `android-private-fixed.properties` | 낡은 47자 토큰 보유. 빌드는 `android-private-build.properties` 사용할 것 |
| `live.py`의 1.4 m 리터럴 | §3.5와 동일 패턴이 `LiveAdapter`에 잔존 |

---

## 5. 반증된 가설 — 다시 조사하지 말 것

그럴듯했지만 **측정으로 틀렸다고 확인된** 것들이다.

| 가설 | 반증 근거 |
|---|---|
| 폰/네트워크 지연이 원인 | 왕복 중앙값 16–31 ms, p99 63 ms |
| FrameBuffer 수정이 상승 실패를 유발 | 프레임 나이 0.344 s(구 APK) vs 0.360 s(신 APK) — 사실상 동일 |
| Voice 브랜치가 방어 게이트를 추가함 | `observe_frame` / `_guard_dispatch`가 `feat/drone-control`, `feat/drone`, `main`, HEAD에서 **바이트 단위 동일**. 게이트는 최초 셔틀 커밋 `2cd49d1`(9/10)에서 도입 |
| `f5e53d1`이 신선도 기준을 조임 | 조이지 않았다. `CLIMB_TAG_MISS_GRACE_S = 1.0` **완화**만 추가 |
| 이동이 너무 빨라 태그를 지나침 | 0.2–0.3 m/s. 태그1이 2.5 초 / 16 샘플 동안 시야에 있었다 (검출 10 Hz) |
| 고도가 꺼져서 실명 | `velocity_down_mps = 0.000`. 기체는 안 움직였고 센서가 물체를 읽었다 (§4.2) |
| 과다 로깅이 지연 유발 | logcat 수집은 20:04 종료, 비행은 20:46. 나비 로그는 277 KB/s로 무겁지만 왕복 중앙값 16 ms |
| 마지막 비행의 권한 상실이 버그 | **운용자가 RC로 회수한 것.** `CONTROL_AUTH_HAS_NO_CONTROL_AUTH (-36873)`는 정상 동작 |

---

## 6. 변경 파일 목록

### 커밋 완료 (2026-09-14)

| 커밋 | 내용 |
|---|---|
| `f5e53d1` | 브리지 빌드 ID 통일, StickControlManager 유예 1000 ms→3 s, 지상증명 재시도, `CLIMB_TAG_MISS_GRACE_S` |
| `03ebb4d` | 형제 저장소 사본에 덮여 사라진 `GroundVideoUnavailable` / `_prepare_ground_video()` 복원 |
| `25b8705` | 검출 280→40 ms, `FrameBuffer.trimToNewestKeyFrame()`, 중단 traceback 로깅 |
| `023f363` | 프레임 만료 → `FramingCorrectionDeferred` (임무 종료 대신 재시도) |

`25b8705` 상세: `livevideo/FrameBuffer.java`(+39), `livevideo/FrameBufferTest.java`(+36, 회귀 3개),
`diagnostics/DiagnosticRecorderTest.java`(기존 Java 11 API 파손 수정),
`trials/standalone_tag_shuttle.py`, `pc/drone_nav/vision.py`(`nthreads=4`).

### 이번 커밋

| 파일 | 내용 |
|---|---|
| `pc/drone_nav/tool_control/field.py` | 목표고도를 설정에서 읽기 (§3.5) |
| `pc/drone_nav/tool_control/live.py` | 지상증명 타임아웃 4 s → 30 s (§3.6) |
| `trials/standalone_tag_shuttle.py` | 타임아웃 30 s, 실명 구간 진단 캡처, 고도 로깅 (§3.6, §4.1) |
| `trials/profiles/standalone_tag_6321236.json` | `target_height_m` 1.5 → 1.6 |
| `app/page.tsx` | 오류 중단 시 종료화면 직행 금지 (§3.7) |
| `drone-control/docs/FIELD_DEBUG_20260914.md` | 이 문서 |

### 저장소 밖 (운영 환경)

| 파일 | 내용 |
|---|---|
| `%LOCALAPPDATA%\IndustryDayDrone\config\field-live-site.json` | `target_height_m` 1.6 |
| `%TEMP%\fly3.py` | 프리플라이트 게이트 + 브리지 자동복구 (§3.8) |

**테스트** 변경 후 전 모듈 통과:
`test_standalone_tag_shuttle` 45, `test_tool_camera` 35, `test_tool_live_boundary` 38,
`test_wall_framing` 25, `test_id1_pair_integration` 17, `test_regressions_20260906` 10,
`test_patrol` 21, `test_core` 35, `test_pair_patrol_route` 5, `test_standalone_climb_freshness` 4.
`npx tsc --noEmit` 통과.

---

## 7. 운영 노트

작업 중 시간을 잡아먹은 환경 특성들.

**설정 로딩**
```python
shuttle.prepare_config(nav_path, profile_dict)
# profile = drone-control/trials/profiles/standalone_tag_6321236.json
# nav     = %LOCALAPPDATA%\IndustryDayDrone\config\field-live-nav.json
# profile에 None을 넘기면 ValueError: ... do not match schema 2
```
어댑터는 site/profile을 **기동 시점에만** 읽는다. 설정을 바꾸면 스택 재시작 필수.

**드론 없이 영상 파이프라인 검증** — `:8766`에 `/camera/start`, `/camera/frame`(리스 갱신),
`/camera/stop`. 인증·본문 형식은 `/tools/*`와 동일.

**영상 카운터 위치** `status["raw_telemetry"]["video"]`
(`status["status"]`가 아님 — 그건 문자열)

**PC는 영상을 자동 재연결하지 않는다** (`camera.py:144` "no automatic reconnect").
폰 앱을 재시작했으면 스택도 재시작해야 한다.

**미션 리스는 10 초**이며 `drone_get_mission`만 갱신한다. `fly3.py`에 2 초 갱신 스레드 있음.

**폰 앱 패키지** `com.ms.voice`
(`com.dji.sampleV5.aircraft` 아님. `com.sampleapp`은 배달의민족이다.)
재기동: `adb shell monkey -p com.ms.voice -c android.intent.category.LAUNCHER 1`

**제어 API 주의**
- `zero()`는 내부에서 `velocity()`를 호출한다 (`attitude()` 아님) → `_guard_dispatch`를
  우회한다. 오류 처리 경로에서 안전하게 호출 가능
- `attitude(forward_tilt_deg, right_tilt_deg, up_mps, yaw_rate_rps)`
- `climb_command(h, age, target)`는 **상승 전용** — 목표 위면 `RuntimeError`
  ("no automatic descent"). `0 ≤ age ≤ 0.5`, `0.5 ≤ h ≤ 1.8`, `0.5 ≤ target ≤ 1.6`.
  출력은 0.10–0.18 m/s로 클램프, 5.1 cm 데드밴드
- `seek_tilt_cap_deg = 0.6` — 주석에 **0.25° 펄스는 기체를 전혀 못 움직였다**고 기록돼 있다
- `arrival_band_fraction [0.85, 0.95]`, 정책 `tag_right_edge_band`
- 벽 순서는 좌→우 `[3, 2, 1, 6]` → **6→3은 왼쪽 이동**. 중간 태그는 의도적으로 무시한다

**나비게이션 로그** 타임스탬프는 `pc_monotonic_ns`, 페이로드는 `data` 아래.
20:46 비행은 59.3 s에 4783 레코드 / 16.4 MB (81 rec/s, 277 KB/s).

**네트워크** 폰 브리지 `10.244.155.20` (9997/9998/9999 + adb 5555).
PC는 폰 핫스팟 SSID에 붙어 있어야 한다.

**Windows PowerShell**
- BOM 없는 UTF-8 `.ps1`을 cp949로 읽는다 → 헬퍼 스크립트는 ASCII로 쓸 것
- Node가 읽을 JSON은
  `[System.IO.File]::WriteAllText(path, json, (New-Object System.Text.UTF8Encoding $false))`
  (`Set-Content -Encoding UTF8`은 BOM을 붙여 `JSON.parse` 실패)
- `git show branch:file`을 파일로 바로 파이프하면 UTF-16이 된다

**스택 기동**
```powershell
scripts\start-integrated.ps1 -Mode Real `
  -RelayPython   <sibling>\relay\.venv\Scripts\python.exe `
  -ControlPython <sibling>\drone-control\.venv\Scripts\python.exe `
  -VoiceTraceDirectory <dir>
```
venv는 형제 저장소 `industry-day-drone`에 있다 (handoff 저장소가 아님).
종료는 `Stop-Process -Id` (python 8766, node 3000).

---

## 8. 다음 작업 순서

1. **폰 핫스팟을 켜고 PC를 거기에 붙인다** (현재 최대 차단 요인)
2. `adb connect 10.244.155.20:5555` → 스택 재기동 → `fly3.py`로 6→3→1→2→6 재비행
3. `diagnostic_blind_*.jpg`를 열어 **태그2가 거기 있었는지 확인** (§4.1)
4. 태그가 프레임에 있는데 검출 실패면 → 거리/각도/흐림 → 전후축 제어 검토
   태그가 프레임에 없으면 → 배치 또는 표류 문제
5. 경로 완주 후 Voice 관문 가시화 (§4.3)

성공 판정: `visited_ids`가 `[6]`을 넘어 늘어나는 것.

---

## 9. 외부 리뷰 교차검증 — `MAIN_FIELD_DEBUG_REVIEW_20260914`

`origin/feat/drone`에 다른 PC(`WhoAmI125`, `a2c49ee`)가 올린 리뷰 문서를 우리 코드와
한 줄씩 대조했다. 문서를 믿지 않고 전부 원본 코드에서 확인했다.

| 주장 | 검증 결과 | 조치 |
|---|---|---|
| `ShuttleClient.arm`이 권한을 2초만 기다린다 | **사실**. `live.py:27` 주석은 "현장에서 3.3초까지 관측"인데 `standalone_tag_shuttle.py:317`은 `+ 2.`였다. `AUTHORITY_HANDOFF_GRACE_S = 4.0`은 `if self._armed` 뒤라 `arm()` 대기 중에는 아예 안 탄다 | `ARM_AUTHORITY_TIMEOUT_S = 5.0` (커밋 `1d1d57a`) |
| 캡처 훅이 `FramingCorrectionDeferred`를 안 잡는다 | **사실**. `023f363`에서 내가 만든 예외가 `on_capture` 경로로 새어나갔고, `field.py`는 사진 한 장당 `observe_frame()`을 3번 부른다 | try/except 추가 + 회귀 테스트 (커밋 `1d1d57a`) |
| lease 읽기 한 번 실패로 기체를 세운다 | **사실** | §9.1 |
| 시나리오 시계가 실비행을 죽인다 | **사실** | §9.2 |
| 경로 6개 순열을 다 받아준다 | **사실이지만 고치면 안 됨** | §9.3 |
| 셋업 문서의 APK 버전이 틀렸다 | **사실**, 2개 문서 3곳 | §9.4 |
| `RELAY_SCENARIO_FILE`로 시나리오를 바꿀 수 있다 | **거짓**. 그 환경변수는 우리 main에 없었다. 리뷰 작성자의 커밋 안 된 작업 트리 기준 | 우리가 새로 만듦 (§9.2) |

### 9.1 lease 읽기 한 번 실패로 비행이 끝났다

`_renew_lease()`는 2초마다 `drone_get_mission`을 읽어 lease를 갱신한다. 이건 **워치독**이지
명령이 아니다. 그런데 이 읽기가 한 번이라도 던지면 `except Exception → _fail(exc)`로 가서
임무를 중단하고 비행 중인 기체에 정지를 보냈다. 로컬 HTTP 호출이 한 번 거절된 것은
기체 상태에 대해 아무것도 말해주지 않는데도 그랬다.

`TRANSPORT_ERROR` / `INVALID_RESPONSE`만 `read_retry_budget_seconds`(6초) 안에서
0.3초 간격으로 재시도한다. 진짜 임무 실패 상태는 예전처럼 즉시 올린다.

6초인 이유: 서비스 lease가 10초(`service.py:114`)이고 갱신 주기가 2초다.
`2 + 6 = 8 < 10`이라 재시도 중에 lease가 만료되지 않는다.

회귀 테스트는 예산을 `0.0`으로 두면 실패하고 `1.0`이면 통과하는 것까지 확인했다
(빈 통과가 아님을 증명).

### 9.2 모의 시나리오 시계가 실비행을 45초에 끊는다

`data/emergency-triage.json`의 데드라인은 18 / 28 / 45초다. 45초에 3명 전원이
`too_late`가 되고 → `_finish_if_resolved()`가 `complete`로 바꾸고 →
`live_mission.py:_watch_deadlines`가 `expired_terminal`을 보고 `_stop_hardware()`를
호출하며 `_work`를 취소한다. **실비행은 3구간에 77–100초**다. 45초면 기체는 아직 공중이다.

지금까지 안 터진 이유: 최근 비행은 전부 `%TEMP%\fly3.py`로 `:8766`을 직접 불러서
릴레이를 거치지 않았다. **Voice/UI로 처음 띄우는 순간 터진다.**

#### 왜 제어 흐름을 안 고쳤나

처음엔 `_watch_deadlines`를 고쳐 "실기가 떠 있으면 시나리오 데드라인으로는 세우지 않는다"로
바꿨다. 그랬더니 `test_deadline_stops_actual_mission`이 깨졌다. 이 테스트는 live 모드에서
시계를 60초 돌리고 `drone_stop_mission`이 정확히 1번 불리는 걸 **의도적으로** 검증한다.
즉 저자가 일부러 그렇게 만든 안전 동작이다. 내 변경은 "실기를 안 세우는" 쪽 —
덜 보수적인 방향 — 이었고, 그걸 혼자 판단해서 바꾸는 건 월권이라 **되돌렸다.**

진짜 결함은 제어 흐름이 아니라 **데이터**다. 18/28/45는 모의 타임라인
(`travelMs 7000 + captureMs 1000 + mockAnalysisMs 2000` × 3 ≈ 30초)에 맞춰진 숫자다.

#### 실제 조치 — 데이터와 설정으로

```
RELAY_SCENARIO_FILE=<path>   # relay/config.py:SCENARIO_FILE
```

기본값은 커밋된 `data/emergency-triage.json` 그대로다. 기본 파일을 그 자리에서 고치면
모의 데모와 `test_scenario_contract.py`가 같이 깨지므로 건드리지 않았다.

새로 만든 `data/emergency-triage-live.json`은 데드라인만 다르다:

| 대상 | 모의 | 라이브 | 근거 |
|---|---|---|---|
| person-3 (가장 촉박) | 18 s | 72 s | 1~2번째로 가야만 구조 가능 |
| person-1 | 28 s | 112 s | |
| person-2 (가장 여유) | 45 s | 180 s | 전체 경로(≈102 s)보다 길다 |
| `injuryWindowMs` | 5 s | 20 s | 비율 유지 |

우선순위 관계(`people[2] < people[0] < people[1]`)는 그대로 둬서 훈련 의미가 안 변한다.
가장 촉박한 창(72 s)은 여전히 `3 × 34 s`보다 짧아서 **마지막에 가면 못 구한다** —
선택의 무게가 유지된다.

`34 s`는 현장 실측 1구간 최악값(전체 77–100초 / 3구간)을 올림한 값이다.

새 `LiveScenarioContractTests`가 (1) 기본값이 여전히 모의 파일인지 (2) 라이브 파일이
데드라인 말고 **한 글자도** 다르지 않은지 (3) 실비행 시간을 넘기는지를 검사한다.

#### 쓰는 법

```powershell
$env:RELAY_SCENARIO_FILE = "$repo\data\emergency-triage-live.json"
# 릴레이 재시작 (모듈 로드 시점에 읽는다)
```

### 9.3 경로 기하 — 고치지 않은 이유

리뷰는 "`field.py`에 `supported_ordered_sequences`를 넣어 검증된 순서 1개만 허용하라"고
제안했다. 실제로 넣어봤고 **되돌렸다.** 두 가지가 깨진다:

- `contracts/drone-tools/v1/contract.schema.json:93` — `"minItems":6,"maxItems":6`.
  공개 계약이 6개를 **요구**한다.
- `pc/tests/test_tool_field.py:290` — 6개 순열을 전부 실제로 비행시키는 기존 테스트.

또 경로는 데이터가 정하지 않는다. `survey.py:187`의 `add_destination`으로 **조종자가
음성으로 앞의 두 곳을 고르고** 세 번째는 `MONITOR_IDS` 순서로 자동으로 붙는다.
데드라인은 LLM의 판단 재료일 뿐 경로를 강제하지 않는다.

그래서 이건 코드가 아니라 **운용 지침**이다:

> 벽은 왼쪽부터 `[3, 2, 1, 6]`, 집은 6번이다.
> `tag-1 → tag-2 → tag-3` (= 6→1→2→3)만 모든 구간이 **이웃 간 이동**이다.
> 실제로 난 경로 6→3→1→2→6은 구간 길이가 태그 3칸/2칸/1칸/2칸이다.
> 페어 게이트는 다음 태그 쪽으로만 기울고 지나치는 태그는 무시하며,
> **전후축은 한 번도 명령되지 않는다**(`forward_tilt_deg`가 늘 0).
> 따라서 태그를 건너뛰는 구간은 그 길이 내내 실명 상태로 날고 벽과의 거리가 보정 없이 표류한다.
> §4.1(태그 2·3 미검출, 실명 9.3초 ≈ 2.8 m)의 유력 용의자다.

### 9.4 빌드 ID 문서 드리프트

코드의 진실은 `.20260913.3`이다 (`live.py:25`, `build.gradle:14`,
`TelemetryProvider.java:420`, `site.example.json:9`). 그런데 문서는:

| 위치 | 있던 값 |
|---|---|
| `docs/NEW_PC_SETUP.md:43` | `.20260913.1` |
| `docs/NEW_PC_SETUP.md:139` | `.20260910.6` |
| `android/README.md:17` | `.20260913.1` |

`live.py:432`와 `field.py:85`가 이 값으로 **출발을 거절**한다. 문서대로 설치하면
현장에서 이유 모르고 거절당한다. 세 곳을 고치고, 다시 어긋나지 않도록
`BuildIdDriftTests`를 추가했다 — APK versionName, telemetry 문자열, site 예시,
두 문서를 모두 읽어 `BUILD_ID` 외의 `5.18-connectivity.*` 토큰이 하나라도 있으면 실패한다.

`trials/`는 일부러 제외했다. 그 스크립트들은 당시 비행한 빌드에 고정돼 있어야 한다.

### 9.5 리뷰가 지적했지만 손대지 않은 것

| 항목 | 위치 | 왜 안 했나 |
|---|---|---|
| VLM 분석 실패가 비행을 중단시킨다 | `live_mission.py` `_run_live`의 `vision.analyze → _fail` | 결과 판정 의미가 바뀐다. 사용자 승인 필요 |
| 브라우저 WS 끊김 → `_stop_hardware()` | `live_mission.py` | 안전 정책 결정이다. 혼자 정할 문제가 아님 |
| `voice_turns.authorize()`의 무음 거절 | `relay/voice_turns.py:227` `{"silent": True}` | 아직 직접 검증 못 함 |
| 출발 후 voice tool 누락 | `relay/server.py:963` | 아직 직접 검증 못 함 |
| `drone_get_captures`가 매 tick마다 누적 전체(≤6 × ≤4 MiB base64) 반환 | | 성능 문제, 비행 차단 요인 아님 |

### 9.6 이번 커밋

| 파일 | 내용 |
|---|---|
| `relay/live_mission.py` | 일시적 lease 읽기 실패 재시도 (§9.1) |
| `relay/config.py` | `SCENARIO_FILE` + `RELAY_SCENARIO_FILE` (§9.2) |
| `relay/survey.py`, `relay/camera.py` | 하드코딩 경로 → `config.SCENARIO_FILE` |
| `data/emergency-triage-live.json` | 실비행용 데드라인 (신규) |
| `relay/test_scenario_contract.py` | `LiveScenarioContractTests` |
| `relay/test_live_mission.py` | 재시도 회귀 테스트 |
| `drone-control/pc/tests/test_tool_live_boundary.py` | `BuildIdDriftTests` |
| `drone-control/docs/NEW_PC_SETUP.md`, `drone-control/android/README.md` | 빌드 ID 정정 (§9.4) |

**되돌린 것**: `live_mission.py:_watch_deadlines` (§9.2),
`field.py:supported_ordered_sequences` (§9.3).

**테스트**: 릴레이 전 모듈 314개 중 실패 10건은 HEAD 워크트리 기준선과 동일한
사전 존재/환경 의존 항목이다(Node·배포 복사 레이아웃 계열). 내가 건드린 모듈
(`test_scenario_contract` 포함 6개 71개)은 전부 통과.
`RELAY_SCENARIO_FILE` 오버라이드는 `survey`·`camera` 양쪽이 같은 파일을 읽고
`deteriorationMs`가 92000/0/52000으로 맞게 파생되는 것까지 실행으로 확인했다.

