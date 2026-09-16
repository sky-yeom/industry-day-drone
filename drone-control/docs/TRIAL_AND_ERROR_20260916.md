# 시행착오 기록 — 2026-09-16 실내 AprilTag 비행

## 증상

드론이 바닥 태그(ID0)에서 자동 이륙해 1.1 m까지는 올라간다. 거기서
목표 1.5 m로 **상승 명령을 보내는데 전혀 안 올라간다.**

- 폰은 `setpoint_up_mps = 0.30`을 정상적으로 보고한다
- `official_advanced_frames_sent` 카운터도 정상적으로 증가한다
- 그런데 `velocity_down_mps`가 **모든 샘플에서 0.0**이다
- 오류 플래그도, SDK 에러도 없다
- 호버링은 완벽하다

17:52와 18:41에는 성공했고, 그 뒤 모든 시도가 실패했다.

## 원인 — 호출자 리스(caller lease) 만료

### 리스가 뭔가

미션을 시작한 쪽이 **"나 아직 살아있다"고 주기적으로 알려야 하는 시한부 소유권**이다.

PC 미션 서비스는 미션을 아무나 시작해 놓고 사라지는 걸 막는다. 보이스 앱이
죽거나 네트워크가 끊겼는데 드론만 계속 날면 위험하기 때문이다. 그래서
서비스는 타이머를 건다.

| 항목 | 값 | 위치 |
|---|---|---|
| 리스 기간 | **10초** | `pc/drone_nav/tool_control/service.py:114` |
| 갱신 방법 | `drone_get_mission` 호출 | `service.py:246-256` |
| 만료 시 | `stop_requested` + `cancel.set()` | `service.py:391-402` |

10초 안에 `drone_get_mission`을 다시 부르지 않으면 미션이 스스로 취소된다.
취소되면 정리 경로가 `zero` → `disarm`을 보낸다 (`tool_control/live.py:416-423`).

### 왜 "상승만" 안 되는 것처럼 보였나

타이밍이 정확히 겹쳤다.

```
t = 0.0s   미션 시작
t = 3.0s   자동 이륙 시작
t = 6.3s   1.1 m 도달, 호버 안정
t = 8.6s   VIRTUAL_STICK 진입
t = 10.4s  상승 명령 시작  ←┐
t = 10.0s  리스 만료        ←┘  겹침
```

이륙에 8초가 걸리므로, 상승을 시작하는 바로 그 순간이 리스 만료 시점이다.
그래서 **"명령은 나가는데 기체가 무시한다"**로 보였다. 실제로는 서비스가
미션을 취소하면서 `zero`를 밀어넣고 있었다.

이륙·호버는 자동 이륙(`AUTO_TAKE_OFF`)이라 영향이 없었다. 기체가 스스로
자세를 잡기 때문이다. 그래서 딱 상승만 고장난 것처럼 보였다.

## 증거

리스를 제대로 갱신하고 다시 날린 비행
(`20260916T211908-dffd9703.jsonl`):

```
t=10.39  h=1.2  setpt=0.24  vdown=-0.2
t=10.89  h=1.3  setpt=0.16  vdown=-0.2
t=11.86  h=1.4  setpt=0.15  vdown=-0.1
t=12.42  h=1.5  setpt=0     vdown=0     ← 목표 1.5 m 도달
```

최고 고도 **1.50 m**. Virtual Stick 수직 제어는 처음부터 정상이었다.

취소된 비행의 미션 레코드:

```json
"stop_reason": "caller_lease_expired",
"error": "InterruptedError: Mission cancelled/deadline reached; no resume"
```

## 보이스 경로는 안전하다

보이스(릴레이)는 전용 백그라운드 태스크로 2초마다 갱신한다. 영상 분석이나
내레이션이 아무리 오래 걸려도 갱신은 멈추지 않는다.

```python
# relay/live_mission.py:194
self._lease = asyncio.create_task(self._renew_lease())

# relay/live_mission.py:296-301
# "Analysis can take longer than the service lease. Keep this
#  independent of vision, browser narration and the ordered
#  capture/analysis loop."
while not self._closing and self._stop_task is None:
    await asyncio.sleep(2.0)
```

| 경로 | 갱신 주기 | 상승 | 좌우 이동 | 촬영·분석 |
|---|---|---|---|---|
| 보이스 (릴레이) | 2초, 독립 태스크 | 안전 | 안전 | 안전 |
| 수동 도구 호출 | 직접 구현해야 함 | 터짐 | 터짐 | 터짐 |

좌우 이동 구간이 상승보다 길지만, 갱신이 단계와 무관하므로 영향이 없다.

남은 위험은 하나다. `_read_mission_retrying`이 10초 넘게 실패하면 리스가
끊긴다. 읽기 재시도 예산은 6초다 (`live_mission.py:32`).

## 배제한 가설 — 다시 파지 말 것

전부 비행 로그로 반증했다.

| 가설 | 반증 근거 |
|---|---|
| 상방 장애물 회피 | 7번 비행 전부 OFF, `oa_upward_distance_mm=60000` |
| FC 액추에이션 데드밴드 | 0.30 m/s에서도 실패 |
| arm→상승 대기시간 부족 | 2.00초를 줘도 실패 |
| 앱 프로세스 재시작 필요 | 같은 pid 22496이 성공·실패 양쪽 |
| SDK 푸시 리스너 사망 | 명령 시점에 성공·실패 둘 다 ~4초 정지 상태 |
| 와이파이/핫스팟 속도 | 실패 비행이 오히려 더 빨랐다 |
| 배터리 잔량 | 96% 실패, 73% 성공, 99% 실패 |
| `OFFICIAL_ADVANCED_ANGLE` 모드 | 7번 비행 전부 동일 |
| `maintenance_block_reason` | 보고 순서 차이일 뿐, 양쪽 다 `unsafe_intent_latched: True` |

SDK 리스너 신호는 7/7로 깔끔하게 갈렸지만 **결과였지 원인이 아니었다.**
DJI 리스너는 값이 바뀔 때만 푸시한다. 기체가 안 움직이면 속도가 0으로
고정되므로 리스너가 안 뜬다. 움직인 비행만 리스너가 살아있었던 것이다.

## 수동으로 도구를 부를 때 지켜야 할 것

`drone_execute_route`를 직접 호출하면 리스도 직접 유지해야 한다.

1. 발사 직후 **간격 없이** 폴링 시작 (첫 `sleep`를 앞에 두지 말 것)
2. `drone_get_mission`을 **2초마다** 호출
3. 종료 상태 목록을 정확히 쓸 것 — `taking_off`, `climbing` 등 진행
   상태를 종료로 오인하면 폴링이 멈추고 미션이 죽는다

실제로 이 실수를 두 번 했다. 첫 번째는 첫 폴링을 12초 뒤에 시작해서,
두 번째는 `taking_off`을 종료 상태로 착각해서.

동작하는 러너는 `files/flyrun.ps1`에 있다.

## 미션 슬롯이 안 풀릴 때

취소된 미션은 **지상 확인이 될 때까지** 슬롯을 잡는다. 새 미션은
`MISSION_BUSY`(409)로 거절된다.

```json
"verification_pending": true,
"manual_landing_required": true
```

푸는 방법:

1. RC로 착륙시킨다
2. 기체 링크가 살아 있어야 한다 (`physicalConnected: true`) —
   끊겼으면 RC↔폰 USB를 다시 꽂는다
3. `drone_get_mission`을 부르면 서비스가 지상 상태를 재확인하고 슬롯을 푼다

`physicalConnected: false`면 텔레메트리가 없어서 지상 확인이 영원히 안 된다.


---

## 증상 2 — 태그를 보고도 멈추지 않고 벽까지 날아간다

### 무슨 일이 있었나

21:30 경로 비행(`20260916T213041-8ba9f25f.jsonl`). 계획은 6→1→2→3→2→1→6.
ID1 앞에서 멈추지 않고 그대로 1번을 지나쳐 2, 3쪽 벽까지 갔다. 사용자가 RC로 직접 회수했다.

### 증거 — ID1은 도착 밴드 안에 있었다

프레임 폭 1920 px 기준, `center_px[0] / 1920`:

| t (s) | x | 도착 밴드(0.75~0.90) |
|---|---|---|
| 38.33 | 0.097 | |
| 40.14 | 0.302 | |
| 42.64 | 0.602 | |
| 43.81 | 0.714 | |
| 44.33 | 0.801 | **안** |
| 45.05 | 0.850 | **안** |
| 46.98 | (ID2 검출 시작, ID1 사라짐) | |

**두 번이나 밴드 안이었는데도 도착 판정이 안 났다.** 밴드 안에 머문 시간은 0.72 초뿐이었다.

### 원인 — 도착은 "밴드 안"이 아니라 "밴드 안에서 정지"다

`id1_pair_framing.py`의 도착 조건은 "밴드 안에 있음"이 아니라
**"밴드 안에서 정지함"**이다:

```python
BRAKE_MAX_DEG   = .6     # 제동 각도 상한 (phase가 ±0.6°를 넘길 수 없음)
CAPTURE_ZERO_S  = 1.     # 0 명령 유지 1.0 s
CAPTURE_HOLD_S  = .5     # 정지 확인 0.5 s
STILL_SPEED_MPS = .08    # 정지로 인정하는 속도
```

수평 속도 0.28 m/s를 0.6°로 죽이려면 관성 때문에 **0.9 초 / 0.25 m를 더 간다**.

멈추는 데 드는 총 시간:

| 단계 | 시간 |
|---|---|
| 제동 후 관성 | 0.9 s |
| `CAPTURE_ZERO_S` | 1.0 s |
| `CAPTURE_HOLD_S` | 0.5 s |
| **합계** | **2.4 s** |
| 밴드 0.75~0.90을 통과하는 데 걸린 시간 | **1.45 s** |

기체는 초당 약 0.112 프레임폭으로 움직였다(38.33 s 0.097 → 45.05 s 0.850).
폭 0.15인 밴드는 약 1.3 s 만에 지나가고, 그마저도 디코드 주기가
1.7 Hz(0.6 초 간격)라 **두세 번**밖에 못 본다.

즉 밴드는 물리적으로 통과 불가능한 폭이었다. 밴드를 놓친 뒤에는
`still_ahead`도 없어서 그대로 직진했다(45.05 s 이후 31 초 동안 ID1 검출 0회).

### 조치

`trials/profiles/id1_tv_pair_reference.json`

```diff
- "arrival_center_x_fraction": [0.75, 0.90],
+ "arrival_center_x_fraction": [0.60, 0.90],
```

오버슛 쪽 상한(0.90)은 그대로 뒀다. 관성 거리 약 0.024프레임폭을 더해도
0.924로, `margin_fraction` 0.03 안에 들어온다.
하한만 0.75 → 0.60으로 내려 **밴드 폭을 0.15 → 0.30 프레임폭**으로 두 배 넓혔다.

| | 이전 | 이후 |
|---|---|---|
| 밴드 폭 | 0.15 | 0.30 |
| 통과 소요 시간 | 1.45 s | 2.68 s |
| 정지 비용 | 2.4 s | 2.4 s |
| 여유 | 없음(불가능) | 있음(가능) |

이 값은 `standalone_tag_shuttle.py`가 그대로 읽어 `arrival_band`로 쓴다.
프로파일 하나만 고치면 되고, 코드 변경은 필요 없다.

### 추가 발견 (미해결)

디코더가 **1.7 Hz**다. 영상 지연은 문제가 아니었고(`frame_age_s` 0.03~0.14)
AprilTag 디코드 자체가 0.6 초에 한 번씩만 나왔다. 이 주기는 증상 4의
진짜 원인이 되므로 반드시 같이 읽을 것.

### 배제한 가설

- 상방 장애물 회피 / `oa_type` → 수평 이동과는 무관
- 영상 지연(`delivery_age_ms`) → 모든 검출에서 `frame_age_s`가 0.03~0.14 s로 신선했다
- 밴드를 최근에 바꿔서 생긴 회귀 → 아니다. 밴드는 09-10 `462f497` 이후 **바뀐 적 없다**
- 오늘 추가한 `still_ahead` 가드가 원인 → 아니다. 로그에 발생 0건
- 경로 계획 오류 → 6→1→2→3→2→1→6 순서는 테스트로 고정되어 있다
  (`pc/tests/test_pair_patrol_route.py:25`)


---

## 증상 3 — 상승 명령이 씹힌다 (내가 만든 회귀)

### 무슨 일이 있었나

21:50 비행(`20260916T215010-ff702dad.jsonl`). 이륙은 됐는데 목표 고도까지
올라가지 못하고 미션이 죽었다.

```
21:50:32  standalone_arm_settle_wait     {"wait_s": 0.86, "settle_s": 1.5}
21:50:38  standalone_climb_stalled       {"height_m": 1.1, "target_height_m": 1.5,
                                          "commanded_up_mps": 0.3, "stalled_s": 6.03}
21:50:38  standalone_interrupted
```

```
RuntimeError: Aircraft held 1.10m for 6s while commanded up at 0.30m/s;
it is refusing vertical control, not climbing slowly
```

### 원인 — 내가 그날 넣은 상한이었다

`standalone_tag_shuttle.py`에 내가 직접 추가한 상수:

```python
CLIMB_STALL_S = 6.0
CLIMB_STALL_MIN_M = 0.05
```

의도는 좋았다. "수직 권한을 못 받았으면 빨리 알려주자." 결과는 최악이었다.

DJI auto-takeoff 직후 비행제어기가 자체 고도 setpoint를 **먼저 정리한다**.
그 handover에 걸리는 시간이 대략 6~12 초다. 21:19 비행
(`20260916T211908-dffd9703.jsonl`)이 증거다: 1.10 m에서 멈춰 있다가
t=10.39 s에 움직이기 시작해 t=12.42 s에 1.50 m에 도달했다.
**정상 동작이다.** 내 6 초 상한이 그 handover 도중에 터진 것이다.

게다가 이미 **세 겹의 상한**이 있었다:

| 값 | 위치 | 크기 |
|---|---|---|
| `timeouts.climb_s` | `profiles/standalone_tag_6321236.json` | **30.0 s** |
| `CLIMB_TIMEOUT_S` | `bounded_sonar_climb.py` (프로파일에 값 없을 때) | 8.0 s |
| `CLIMB_STALL_S` | 내가 추가 | **6.0 s** ← 가장 짧은 값이 이김 |

30 초를 주기로 해놓고 6 초에 죽인 셈이다.

### 조치

1. `CLIMB_STALL_S`, `CLIMB_STALL_MIN_M`, `stall_since`/`stall_height` 추적과
   `_climb`의 해당 분기를 **전부 삭제**했다.
2. `CLIMB_TIMEOUT_S` 8.0 → **25.0**. 이 값은 프로파일에 `climb_s`가 없을 때만
   쓰이는 보조값이지만, 8 초는 handover 시간 안에 들어와서 위험했다.

### 배운 것

- **새 상한을 넣지 마라. 이미 있는지 먼저 찾아라.** 여기엔 `climb_s` 30 초가
  이미 있었다.
- 여러 상한이 겹치면 항상 **가장 짧은 것**이 이긴다. 가장 짧은 값이 의도한
  값인지 확인해야 한다.
- 실패를 RC 탓으로 돌렸던 것도 내 잘못이다. 로그는 `standalone_climb_stalled`라고
  정확히 말하고 있었다. 증상만 보고 원인을 추측하지 말고 로그를 먼저 읽을 것.

---

## 증상 4 — 태그를 가운데 잡고도 착륙을 못 끝낸다

21:54 비행(`20260916T215438-bdea7911.jsonl`)은 **경로를 처음으로 완주**했다.
`visited_ids=[6,1,2,3,6]`, `error=None`, 사진 9장. 그런데 착륙을 못 했다.
ID6 앞에서 20 초를 버티다 `standalone_home_centering_gave_up`, 이어서
`landing_search_nudge` 8 번, `landing_abandoned`, 164 초에 RC 인계.

사용자 지적: **"6번을 넘어섰으면 돌아가야지. 착륙 할려면 멈춰야지."** 맞는 말이었다.

### 로그가 말한 것

ID6 중심 오차(박스 반폭 240 px):

| 시각 | center_x 오차 | 박스 안? |
|---|---|---|
| t+0.00 s | **+484 px** | 밖 |
| t+0.63 s | **+32 px** | **안** |
| t+1.25 s | **-35 px** | **안** |
| t+1.74 s | **-170 px** | **안** |
| t+2.36 s | **-423 px** | 밖 |

**박스 안에 1.11 초 동안 연속 3 프레임 있었다.** `landing_confirm_s`는 0.5 초다.
확정됐어야 한다. 안 됐다.

### 진짜 원인 — 디코더가 신선도 한계보다 느렸다

AprilTag 디코드 간격을 전부 재봤다.

| 항목 | 값 |
|---|---|
| 디코드 간격 중앙값 | **0.625 s** |
| 최소 / 최대 | 0.54 s / 0.719 s |
| `FRESH_S` (`standalone_tag_shuttle.py`) | **0.5 s** |
| 0.5 s를 넘는 간격 | 30 개 중 **25 개** |

두 정렬 루프 모두 `age > FRESH_S`인 틱에서 `centered_since = None`을 했다.
디코드 사이 틱은 거의 항상 `age > 0.5`다. 즉 **타이머가 매번 0으로 리셋됐다.**

> 0.625 s(디코드 주기) > 0.5 s(신선도 한계) ⇒ 0.5 초 확정은 **수학적으로 불가능**.

태그를 아무리 잘 잡아도 확정이 안 되는 구조였다. 밴드도, 박스 크기도,
조종도 문제가 아니었다. **관측 주기와 확정 조건이 서로 모순**이었다.

### 두 번째 결함 — 돌아갈 방법이 없었다

지나쳐서 태그를 놓치면 루프는 그냥 제자리에서 타임아웃까지 버텼다.
"넘어섰으면 돌아간다"는 동작이 **아예 구현돼 있지 않았다**.

### 세 번째 결함 — 착륙 탐색이 앞으로만 갔다

`SEARCH_NUDGE_DEG = 0.3`을 8 번 모두 **전방 피치**로 썼다.
기체는 **옆으로** 밀려 있었다. 앞으로 8 번 밀어봐야 옆 오프셋은 안 줄어든다.

### 조치

1. `CENTER_MISS_GRACE_S = 1.2` 신설. 디코드 공백이 이 값보다 짧으면
   확정 상태를 **유지**하고 `zero()`를 낸다. 1.2 s는 관측 중앙값 0.625 s의 1.9 배.
2. `CENTER_CONFIRM_FRAMES = 2`. 시계만으로는 확정 못 한다.
   **서로 다른 디코드 2 개**가 박스 안이어야 한다 (`PairFramingGate._stable_frames`와 동일 규칙).
3. 진짜 소실(공백 > 1.2 s) 시 마지막 진행 방향의 **반대**로
   `CENTER_RECOVERY_DEG = 0.4`를 `CENTER_RECOVERY_PULSE_S = 0.6` 동안,
   최대 `MAX_CENTER_RECOVERIES = 3` 회. 로그: `standalone_home_recovery_pulse`.
4. `SEARCH_NUDGE_PATTERN = ((1,0), (0,-1), (0,1), (-1,0))` — 전/좌/우/후 순환.
   0.3° 는 `landing` 페이즈의 `lateral_bounds = (-0.6, 0.6)` 안이다.

### 회귀 테스트로 못 박았다

`pc/tests/test_standalone_tag_shuttle.py::HomeCentringDecodeGapTests` 4 개.
0.625 s 간격으로 중심 32 px 안쪽 ID6을 먹였을 때:

| 코드 | 결과 |
|---|---|
| 수정 전 (`CENTER_MISS_GRACE_S=0`, 1 프레임 확정) | **실패** — 20.0 s 전량 타임아웃 |
| 수정 후 | **성공** — 0.8 s 만에 확정 |

실제 비행의 `home_centering_gave_up`이 테스트에서 그대로 재현됐다.

### 배운 것

- **확정 조건은 관측 주기보다 느슨해야 한다.** 0.5 초 확정을 0.625 초 주기로
  검증할 수는 없다. 둘 중 하나만 보면 둘 다 멀쩡해 보인다.
- "센서가 없다"와 "센서가 아직 안 왔다"는 다른 상태다. 전자만 상태를 지워야 한다.
- 접근 로직을 짤 때 **되돌아오는 경로**도 같이 짜라. 지나친 뒤의 계획이 없으면
  기체는 그냥 벽까지 간다.
- 탐색 패턴은 **오차가 생길 수 있는 모든 축**을 덮어야 한다. 한 축만 밀면
  다른 축 오차는 8 번을 밀어도 그대로다.

---

## 증상 5 — 가만히 떠 있는데 "안전 봉투 밖"이라며 미션을 버린다

**날짜:** 2026-09-17 08:47 (전날 기록과 같은 현장 캠페인이라 이 문서에 이어 적는다)

### 무슨 일이 있었나

ID6 → ID3 까지 정상 촬영하고 ID1 로 가서 멈춘 직후 미션이 끝났다.
UI 에는 이렇게 떴다.

```
작전 오류: 실제 드론 작업을 중단했습니다 (MISSION_OUTCOME_UNKNOWN).
자동 재개하지 않습니다.
```

`MISSION_OUTCOME_UNKNOWN` 은 relay 가 씌운 껍데기다. 실제 예외는 드론 로그에 있었다.

```
InterruptedError: Capture is outside the fresh height/battery safety envelope
visited_ids=[6, 3]
```

### 먼저 틀린 진단을 했다 (기록해 둔다)

처음에 "높이 텔레메트리가 구조적으로 항상 393 ms 라서 0.5 초 한계에 여유가
0.107 초뿐이다" 라고 설명했다. **틀렸다.** 전수 집계해 보니 이렇다.

| 비행 | 표본 | 중앙값 | p90 | 최대 | 500 ms 초과 |
|---|---|---|---|---|---|
| 09-17 08:40 (성공) | 3521 | 181 ms | 352 ms | 405 ms | 0 건 |
| 09-17 08:46 (실패) | 1220 | 170 ms | 350 ms | 402 ms | 0 건 |
| 09-16 21:54 | 2697 | 170 ms | 346 ms | 402 ms | 0 건 |

`height_age_ms` 는 **상수가 아니라 톱니파**다. 0 → 400 → 0 을 반복한다.
393 ms 는 그 톱니의 꼭대기였을 뿐이다. 숫자 하나만 보고 "항상 그렇다"고
말하면 안 된다. 분포를 봐야 한다.

### 진짜 원인 — 독립적인 톱니 두 개가 동시에 꼭대기를 친다

`field.py` 의 판정식은 이렇다.

```python
# field.py — 수정 전
or not shuttle._number(telemetry.height_age_s + elapsed, 0., .5)
```

더하는 두 값이 **각각 다른 주기로 도는 톱니**다.

| 값 | 출처 | 주기 | 최댓값 |
|---|---|---|---|
| `height_age_s` | 폰 브리지의 느린 폴링 그룹 (~2.5 Hz) | ~0.40 s | **0.405 s** |
| `elapsed` | PC 의 `RateLimiter(10)` 상태 폴링 | 0.10 s | **~0.10 s** |
| 합 | | | **~0.50 s** ← 한계와 같다 |

두 톱니는 서로 위상이 어긋나 있어서 대부분 겹치지 않는다. 그래서 ID3 은
통과하고 ID1 에서 걸렸다. **운에 맡긴 상태였다.**

### 결정적 증거 — 같은 폴링 그룹인데 예산이 3 배 다르다

`height` · `is_flying` · `are_motors_on` · `flight_mode` 는 폰이 **한 번에
한 묶음으로** 갱신한다. 로그에서 네 값의 `*_age_ms` 가 항상 같은 값이다.

그런데 예산은 이랬다.

| 값 | 검사 위치 | 예산 |
|---|---|---|
| `is_flying` | `standalone_tag_shuttle.FLIGHT_STATE_FRESH_S` | **1.5 s** |
| `height` | `field.py:_capture_proof` | **0.5 s** |

같은 데이터가 같은 순간에 도착하는데 한쪽은 1.5 초를 주고 한쪽은 0.5 초를
줬다. **0.5 쪽이 원래 이상값이었다.**

### 조치

```python
# field.py — 수정 후
MAX_HEIGHT_AGE_S = .8
...
or not shuttle._number(telemetry.height_age_s + elapsed, 0., MAX_HEIGHT_AGE_S)
```

0.8 을 고른 근거:

- 실측 최악 조합 0.505 s 의 **1.58 배** — 폴링이 한 번 늦게 와도 통과한다.
- 같은 그룹인 `is_flying` 의 1.5 s 보다는 **여전히 엄격하다.** 높이는 안전값이니
  비행 상태보다 느슨해지면 안 된다.
- 폴링을 두 번 놓치면(>0.8 s) 여전히 거부한다. 진짜 멈춘 텔레메트리는 잡는다.

**높이 범위(0.5~1.8 m)와 배터리 하한(30 %)은 건드리지 않았다.** 실패 순간의
실측값은 높이 1.5 m, 배터리 33 % 로 둘 다 통과했다. 원인이 아니다.

### 회귀 테스트로 못 박았다

`drone-control/pc/tests/test_capture_proof_freshness.py` — 7 건.

실측 최악 조합(0.405 + 0.100)을 실제 `_capture_proof` 에 그대로 먹인다.
`MAX_HEIGHT_AGE_S` 를 옛 `.5` 로 되돌리면 **그 테스트가 즉시 실패한다.**
확인한 결과다. 숫자를 지키는 테스트가 아니라 현장 실패를 재현하는 테스트다.

나머지 6 건은 완화가 다른 걸 뚫지 않았는지 지킨다 — 움직이는 기체 거부,
RC 개입 거부, 높이/배터리 한계 불변, 얼어붙은 높이 거부, 높이 나이 없음 거부.

### 배운 것

- **같은 소스에서 오는 값은 같은 예산을 받아야 한다.** 아니라면 왜 다른지
  주석에 적혀 있어야 한다. 안 적혀 있으면 그건 실수다.
- 나이 두 개를 더해서 비교할 거면 **둘 다의 최댓값을 알고** 한계를 정해야 한다.
- 한 번 측정한 값으로 "구조적으로 항상 그렇다"고 말하지 말 것. 분포를 뽑을 것.
- 증상 4(디코드 공백)와 **같은 유형**이다. 데이터 주기보다 신선도 한계가 빡빡했다.
  이 코드베이스에서 세 번째다.

---

## 증상 6 — 브라우저가 떠나면 "relay failure" 로 죽고 UI 가 결과를 못 받는다

### 무슨 일이 있었나

08:43:14 에 ID3 을 정상 촬영했는데 UI 에 아무것도 안 올라왔다.
드론은 멀쩡히 남은 경로를 돌고 **정상 착륙**했다(`ground_verified: true`).
사진도 DB 도 다 남아 있었다. UI 만 깜깜했다.

```
2026-09-17 08:44:03,281 ERROR relay: relay failure
  File "relay/server.py", line 1357, in ws_endpoint
    await pumps[1]
  File "relay/server.py", line 1175, in pump_browser
    raw = await self.browser.receive_text()
  File "starlette/websockets.py", line 118, in receive_text
    raise RuntimeError('WebSocket is not connected. Need to call "accept" first.')
```

### 원인 — 3 단계로 죽는다

**1단계.** `send_browser` 가 실패를 통째로 삼켰다.

```python
# server.py:323-328 — 수정 전
async with self._browser_lock:
    with contextlib.suppress(Exception):
        await self.browser.send_text(...)
```

**2단계.** starlette 은 예외를 던지기 **전에** 상태를 먼저 바꾼다.

```python
# starlette/websockets.py:85-88
try:
    await self._send(message)
except OSError:
    self.application_state = WebSocketState.DISCONNECTED   # ← 먼저 바뀐다
    raise WebSocketDisconnect(code=1006)                   # ← 우리가 삼킨 것
```

예외는 삼켜지고 **상태 변경은 영구히 남는다.** relay 는 아무 일도 없었다고 믿는다.

**3단계.** 그다음 `receive_text()` 는 `WebSocketDisconnect` 가 아니라
`RuntimeError` 를 던진다.

```python
# starlette/websockets.py:116-118
if self.application_state != WebSocketState.CONNECTED:
    raise RuntimeError('WebSocket is not connected. ...')
```

`server.py:1357` 의 `await pumps[1]` 은 `except WebSocketDisconnect: pass` 로
보호돼 있었지만 `RuntimeError` 는 그걸 통과해서 `except Exception` 에 떨어진다.
→ `log.exception("relay failure")` → 이미 죽은 소켓에 에러 프레임 전송 시도.

### 핵심 — 같은 사건인데 결과가 정반대다

브라우저가 떠날 때 **수신 쪽이 먼저 알아채면** `WebSocketDisconnect` 가 나고
조용히 정리된다. **송신 쪽이 먼저 알아채면** `RuntimeError` 가 나고 "relay
failure" 로 터진다. 어느 쪽이 먼저인지는 **순수한 레이스**다. 같은 F5 가
어떤 날은 조용하고 어떤 날은 트레이스백을 남긴 이유다.

### 조치

```python
# server.py — send_browser: 삼키지 말고 기록한다
except Exception as exc:
    kind = payload.get("type", "?") if isinstance(payload, dict) else "?"
    if self.browser.application_state is not WebSocketState.CONNECTED:
        if not self._browser_gone:
            self._browser_gone = True
            log.warning("browser socket lost while sending %s: %r", kind, exc)
    else:
        log.warning("browser send failed (%s): %r", kind, exc)
```

```python
# server.py — await pumps[1]: 떠난 게 확인된 경우만 정상 종료로 본다
try:
    await pumps[1]
except RuntimeError:
    if not bridge._browser_gone:
        raise                      # 진짜 버그는 여전히 터뜨린다
    log.info("browser left during flight (phase=%s); mission records stand", ...)
```

### 이 수정이 하지 않는 것 — 분명히 해 둔다

- **잃어버린 ID3 푸시를 되살리지 않는다.** 브라우저가 떠난 뒤의 메시지는 갈 곳이 없다.
- **미션 중단을 막지 않는다.** `finally` 의 `bridge.close()` 가 `abort_mission()` 을
  부르는 건 그대로다. 관제사가 화면을 잃었으면 미션을 접는 게 맞는 동작이다.
- 브라우저 재접속으로 세션을 이어받는 기능은 **없다.** 별도 설계가 필요하다.

### 그래서 뭐가 좋아지나

1. **왜 끊겼는지 로그에 남는다.** 지금까지 `suppress(Exception)` 이 원인을 통째로
   지워서 "소켓이 죽었다"는 사실조차 사후에만 알 수 있었다.
2. **정상 이탈이 장애로 기록되지 않는다.** F5 한 번에 ERROR 트레이스백이 남으면
   진짜 장애를 찾을 때 방해가 된다.
3. **진짜 `RuntimeError` 는 여전히 터진다.** `_browser_gone` 이 안 서 있으면
   그대로 올린다. 무조건 삼키는 게 아니다.

### 배운 것

- `contextlib.suppress(Exception)` 은 **예외만 지우지 부작용은 못 지운다.**
  라이브러리가 예외 던지기 전에 상태를 바꿨다면 그 상태는 남는다.
- 정리 경로가 **두 갈래**면 둘 다 같은 결말이어야 한다. 한쪽만 조용하면
  나머지 한쪽은 언젠가 현장에서 터진다.
- 예외 타입은 라이브러리가 정한다. `WebSocketDisconnect` 만 잡아 두고
  "끊김은 다 잡았다"고 생각하면 안 된다. 실제로 오는 건 `RuntimeError` 였다.

---

## 09-17 에 확인했지만 손대지 않은 것

| 항목 | 확인 내용 | 왜 안 고쳤나 |
|---|---|---|
| ID3 촬영 실패 의혹 | 조사한 모든 비행에서 **촬영 성공**. 사진·DB 행 모두 존재 | 문제가 아니었다 |
| 판정 못 한 사진 | `live_mission.py:440` 이 이미 `unjudged_capture` 로 보존 | 이미 올바르다 |
| monitor-2 / monitor-3 `no verdict` | 둘 다 `PromptRevisionRequired`. ID3 왼쪽은 실제로 빈 유리벽 | 모니터 미설치 때문인지 프롬프트 문제인지 미확정 |
| Azure IMDS 지연 | 분석마다 `169.254.169.254` 타임아웃 후 CLI 폴백, **장당 3~10 초** | 인증 경로 변경이라 현장에서 안 건드림 |
| APK 리빌드 | 폰 보고값 `5.18-connectivity.20260913.3` = `live.py` `BUILD_ID` = `build.gradle` versionName. 오늘 `land` 명령 전달·착륙 성공 | Android 파일 변경 0 건. 리빌드 불필요 |

---

## 폰 IP — 매번 반복하지 말 것

핫스팟 서브넷은 재접속할 때마다 바뀐다. `field-live-nav.json` 의 `network.host`
는 **항상 낡아 있다**고 가정해야 한다.

| 사실 | 내용 |
|---|---|
| 방향 | **PC 가 폰을 부른다.** 폰은 PC 를 부르지 않는다. 폰 앱에 PC IP 를 넣는 설정은 없다 |
| 폰 주소 | 폰이 핫스팟이면 **폰이 곧 Wi-Fi 기본 게이트웨이**다 |
| 찾는 법 | `(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -InterfaceIndex <wifi>).NextHop` |
| 확인 | **TCP 9998 만 믿을 것.** 폰은 ICMP 에 답하지 않는다. `ping` 은 항상 실패한다 |
| 무관한 것 | `DRONE_CONTROL_TRANSPORT=local`, `8766` 의 `127.0.0.1` 바인딩은 relay↔PC 구간이다. 폰과 무관 |

`scripts/field-start.ps1` 이 이 과정을 자동으로 한다. **사용자에게 폰 IP 를
묻지 말 것.**
