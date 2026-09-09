# COEX: 태그 없는 1.8m 높이·좌측 1m 왕복 계획

작성 2026-09-09. 예정일 2026-09-10 KST. 사용자 확정 종료 방식은 **복귀 → 호버링 → RC 직접 착륙**이다.

**준비 상태: 별도 PC 실행기와 모의 시험 구현 완료. 실제 비행은 미검증이다.** 바로 사용할 명령은 [현장 실행 안내](COEX_QUICKSTART_20260910.md)에 있다. 기존 시험·앱·Speech 코드는 수정하지 않았다. 이 임무는 과거 7연속 trial 카운트에 포함하지 않는다.

[설정](../trials/profiles/coex_tagless_left_return_1m_1p8m.json)은 `implemented_offline_tested` 상태이며 전용 runner에 연결했다. 기본 CLI는 계획만 출력한다. `--execute --site-ready`와 현재 폰 IP, 기존 비공개 토큰, 이륙 전 실제 FC 조회가 필요하다. 현장 검증 관련 false 값은 실기 정확도 검증이 아직 안 됐다는 기록이며 이를 임의로 true로 표시하지 않는다.

## 동작 순서와 기준

1. 기체가 지상이고 실제 모터가 정지한 상태에서 FC·명령 경로·센서 값을 확인하고 로그를 시작한다.
2. 이륙 후 **현재 SDK 하방 높이 표시가 1.8m**가 되도록 상승한다. 자동 이륙 높이에 1.8m를 더하지 않는다.
3. 높이 1.8m ±0.1m에서 2초 안정화한 뒤 출발 위치 추정치를 0으로, 현재 기체 방향을 기준 방향으로 고정한다.
4. 기체 기준 **왼쪽 약 1m** 이동한다. 최대 속도 제안값은 0.20m/s이고 시작·도착 때 감속한다.
5. 수평 영점 명령으로 최소 2초 호버링한다. 실제 수평 속도 ≤0.05m/s가 1초 유지되는지 확인한 뒤에만 반전한다.
6. 같은 방향을 바라보면서 **오른쪽으로 출발 위치 추정치 0 부근까지** 돌아온다. 왼쪽에서 1.04m 이동한 것으로 추정되면 복귀 목표도 그 변위를 되돌리는 것이다.
7. 복귀 후 3초 호버링하고 Virtual Stick을 해제한다. VS 해제와 RC 제어권을 확인한 뒤 사용자가 RC로 착륙한다. 자동 착륙·RTH는 사용하지 않는다.

순항만 계산하면 편도 1m / 0.2m/s = 5초다. 가속·감속·이륙·높이 안정화·호버링이 추가되므로 전체 예상은 약 30~50초이며 실측 보장은 아니다. 각 이동 20초, 상승 15초, 전체 90초는 완료 판정이 아니라 실패 시 종료 기한이다.

## 높이와 위치를 어떻게 구분하는가

현재 Android `TelemetryProvider`는 `FlightControllerKey.KeyUltrasonicHeight`의 dm 값을 10으로 나누어 `height_m`으로 보낸다. 명시적인 다른 API로의 fallback은 없다. 다만 DJI는 이 SDK 값 자체에 기압계·하방·적외선 센서 정보가 결합될 수 있다고 설명한다. 따라서 **SDK가 제공하는 하방 기준 융합 높이**라고 표기하며 순수 원시 초음파 거리라고 부르지 않는다. 원시 `oa_downward_distance_mm`는 별도로 계속 기록하고 임의로 교체하지 않는다. [DJI FlightControllerKey 문서](https://developer.dji.com/api-reference-v5/android-api/Components/IKeyManager/Key_FlightController_FlightControllerKey.html)

하방 **거리** 하나로 왼쪽 1m나 출발점 복귀를 측정할 수는 없다. DJI `KeyAircraftVelocity`의 NED 속도를 이용해 수평 변위를 추정한다. 태그·외부 위치 기준 없이 수행하는 속도 적분에는 오차가 누적되므로 1m 및 복귀 허용 오차는 **추정값에 대한 판정 기준**이다. 실제 위치 정확도가 ±10cm 또는 ±15cm라고 보장하지 않는다.

Mini 4 Pro의 하방 비전 측정 범위는 0.3~12m이며, 바닥 무늬·반사 특성·조명이 작동 조건에 포함된다. 1.8m는 사양 범위 안에 있지만 코엑스 바닥에서 위치 유지와 속도 추정이 실제로 유효한지는 별도 확인해야 한다. [DJI Mini 4 Pro 사양](https://www.dji.com/mini-4-pro/specs)

현재 높이·속도 age는 Android LISTEN callback 또는 5Hz GET 성공 시각 기준이며 센서 자체의 측정 시각이 아니다. 앱 내 `/10` 변환은 확인했지만, SDK 내부 캐시가 실제 새 측정을 보장하는지는 이 필드만으로 증명하지 못한다. 새 ACK에서 telemetry가 빠졌으면 이전 `last_telemetry`를 재사용하지 않고 관측 누락으로 처리해야 한다.

### 이전 성공 로그에서 확인한 근거

로컬 보관 로그 `20260907T172959-0d676055.jsonl`을 2026-09-09 읽기 전용으로 집계했다. 로그 자체는 이 저장소에 게시하지 않았다.

| 항목 | 결과 |
|---|---:|
| 전체 ACK / 속도·age 유효 | 973 / 973 |
| velocity age | 0~200ms |
| 비영 수평 attitude 명령 ACK | 274 |
| 수평 속도 크기 ≥0.04m/s / 정확히 0 | 239 / 35 |
| 명령 방향 투영 속도 ≥0.04m/s / 반대 ≤−0.04m/s | 228 / 0 |
| 이동 명령 중 높이 | 1.0~1.4m |

해당 로그는 수평 속도 정보를 사용할 근거이며 독립적으로 거리를 실측한 결과는 아니다. 태그 없는 1m 정확도·출발점 복귀·높이 1.8m와 새 VELOCITY 제어 모드의 성공을 이 로그로 대체하지 않는다.

## 수평 추정 및 제어 구현 명세

기준 yaw를 ψ₀라 하고 SDK 실제 속도를 vN, vE라 할 때 아래 값을 사용한다. 단위는 m/s, yaw는 rad이다.

```text
v_forward_reference =  vN*cos(ψ₀) + vE*sin(ψ₀)
v_right_reference   = -vN*sin(ψ₀) + vE*cos(ψ₀)
delta_right        = (v_right_previous + v_right_current) / 2 * dt
delta_forward      = (v_forward_previous + v_forward_current) / 2 * dt
left target: right_estimate = -1.0m
return target: right_estimate = 0.0m
```

- 첫 시료는 기준 시각만 설정하며 첫 적분 거리는 0이다. PC monotonic 시각과 ACK 내 자료 age로 유효 구간을 판단한다.
- 좌측·정지·우측·복귀 호버링 동안 하나의 부호 있는 변위를 계속 적분한다. 반전 시 누적 변위를 초기화하지 않는다. 정지 중 미끄러짐도 포함한다.
- 모든 수치가 유한하고 age ≥0이어야 한다. 높이·속도·자세 자료와 ACK가 0.5초 이내이며, 적분 구간은 0.25초 이하여야 한다. 연결 세대 변경·누락·긴 간격을 0 속도 또는 명령 속도로 메우지 않는다. 적분이 끊기면 임무를 중단하고 위치 결과를 unknown으로 남긴다.
- 값이 같은 ACK 여러 개를 독립적인 새 센서 측정으로 세지 않는다. sample freshness는 최근 성공한 데이터 갱신·GET 증거로 판정하며, 값 변화가 없다는 이유만으로 통신 장애를 단정하지 않는다. 반대로 ACK 도착만으로 오래된 센서 값의 age를 새로 쓰지도 않는다.
- 구현 명령 모드는 `advanced`의 BODY+VELOCITY다. `forward_mps=0`, 좌측 `right_mps<0`, 우측 `right_mps>0`; 높이 유지와 기준 yaw 유지를 함께 수행한다. 기존 성공 trial의 BODY+ANGLE과 다르므로 현장에서 방향·응답 확인이 필요하다. 장애물 회피 설정을 바꾸거나 실행 중 ANGLE 모드로 자동 전환하지 않는다.
- PC `velocity`의 의미를 그대로 사용한다. Android의 BODY+VELOCITY 매핑은 SDK Roll←forward, Pitch←right이며, ANGLE 매핑과 다르므로 명칭만 보고 Roll/Pitch를 뒤집지 않는다.
- 제안 감속 규칙은 `min(0.20, 0.20*t/1.5, 0.20*remaining/0.30)`이다. 목표 허용 범위 진입 시 수평 명령을 0으로 하고 실제 정지를 확인한다. 감속·정지 확인 중에도 적분한다. 반대 방향 이동이나 횡방향 오차를 이동 완료로 합산하지 않는다.
- 도착 판정: 좌측 목표 오차 ≤0.10m, 복귀 right 오차 ≤0.15m, forward 오차 ≤0.20m, 높이 안정 및 실제 정지 조건을 함께 만족해야 한다. overshoot로 범위를 벗어나면 실패 처리하며 임의 왕복 보정 루프를 추가하지 않는다.
- yaw는 시작 방향을 유지한다. 제안 이득은 0.8/s, yaw rate 상한 10°/s, yaw 이탈 15° 초과 시 중단이다. 수평 전후 보정 이동은 넣지 않고 추정 전후 이탈 0.20m 초과 시 RC에 인계한다.
- 편도 진행 방향의 유효 변위가 명령 후 2초 동안 0.05m 미만이면 `MOTION_UNCONFIRMED`로 중단한다. 속도가 계속 0이라고 표시되면 명령×시간으로 1m 도착을 생성하지 않는다. 실제 기체가 움직였는지 여부는 이 데이터만으로 구별 불가능할 수 있다.

위 속도·허용 오차·기한은 **현장 검증 전 설계 제안값**이다. 목표를 달성하지 못했다고 자동으로 속도·각도·기한을 늘리지 않는다. SDK Advanced 전송 주기는 공식 권장 5~25Hz 범위 안에서 Android 실제 발행 주기와 함께 확인한다. PC 목표는 10Hz다. [DJI Virtual Stick 문서](https://developer.dji.com/api-reference-v5/android-api/Components/IVirtualStickManager/IVirtualStickManager.html)

## 구현 파일과 기존 코드 경계

| 위치 | 구체 작업 |
|---|---|
| `trials/coex_tagless_left_return.py` | 별도 profile runner. 기본 plan-only, 읽기 점검/모의/실행 경로 분리. 지상 GET → 이륙1회 → 자동이륙 안정화 → arm1회 → 제어기 → RC 인계 → 수동 착륙 확인 |
| `pc/drone_nav/protocol.py` | 기존 wire protocol 및 토큰 마스킹 JSONL을 subclass가 활용. 기존 파일 변경 없음 |
| `trials/coex_io.py` | 현재 ACK raw를 보존하고 수신 시각/connection epoch를 붙임. 누락 시 이전 last_telemetry 재사용 금지. 제어 소켓 단일 소유, 전체 요청 기한 및 sequence 확인 |
| `trials/coex_io.py` 모터 조회 | 별도 read-only query 소켓으로 `GET FlightController AreMotorsOn`. 0.5초 주기, 전체 기한 0.4초, 미완료 최대1개, 응답 age≤1초. 최초 실패 latch. 실제 모터·비행 상태를 제어 전후에도 조회 |
| `trials/coex_mission.py` | 순수 제어기 `CLIMB → LEFT → BRAKE_LEFT → RIGHT → FINAL_HOVER → DONE`. 부호 있는 적분·세대/age/gap 검증·도착/실제 정지 확인. 기존 hypot 누적 거리는 사용하지 않음 |
| `trials/coex_mission.py` climb | 높이1.8m ±0.1m·2초 및 수평/수직 속도 각각≤0.05m/s·1초 정지 확인. 상승≤0.18, 하강≤0.10m/s, 15초 기한. 이동 높이1.5~2.1m |
| `trials/coex_sim.py`, `trials/tests/` | 지연·관성을 가진 전체 가상 실행, 순수 제어기·가짜 소켓·runner 수명주기 검증. 기체/네트워크 연결 없음 |
| `trials/START_COEX.ps1` | 현재 PC의 Python/비공개 설정 사용. 기본 지상점검, `-Execute` 명시 시1회비행 |
| `trials/bounded_sonar_climb.py` | 기존 파일 유지. target≤1.5m 및 ID0 시각 교차 확인에 의존하므로 태그 없는 1.8m용으로 직접 호출하지 않음 |
| `pc/drone_nav/runtime.py:run_flight_test` | 직접 호출하지 않음. 시작 OA CLOSE 호출, 같은 방향의 여러 leg, 방향 없는 거리 합산, timeout도 leg 완료로 표시하는 동작이 이번 요구와 맞지 않음 |
| 현재 FC/query/영상 수정 계획 | 연결 장애 수정을 완료했다고 가정하지 않음. 실기 runner가 사용하는 상태·자료의 신뢰 조건은 별도로 검증 |

일반 `AppConfig.flight.target_altitude_m` 검증 범위는 현재 0.4~1.2m다. 이 profile을 일반 config로 넣거나 검증 플래그를 무조건 켜지 않는다. 전용 profile에 finite·범위·단위 검증을 두고, 기존 일반 설정 상한을 넓히지 않는다. 현재 CLI `--climb-to`만으로 1.8m 무태그 왕복을 구현했다고 간주하지 않는다.

실제 모터 조회는 `SdkApiClient`의 GET 인코딩·파싱을 재사용하고 연결·송수신 전체 기한을 별도 적용했다. 결과는 connection epoch와 응답 시각을 포함한 불변 snapshot으로 전달하며 generation 변경 시 폐기한다. 비영점 발행 전 flying/motors와 age를 확인한다. 현장에서 2Hz 모터 조회가 FC 경로를 방해하지 않는지도 확인해야 한다. DNS 지연이 기한을 우회하지 않도록 숫자 IP만 받는다.

기존 70cm 같은 **PC 장애물 거리 중단 조건은 이번 profile에도 넣지 않는다.** 좌우 1m 목표 판정과 최대 추정 변위 1.3m는 임무 이동 범위이며 장애물 센서 거리 기준이 아니다. 원시 360도·상방·하방 거리와 오류는 계속 기록한다. `60000` 같은 원시값을 실제 60m의 빈 공간 또는 센서 정상/고장 판정으로 바꾸지 않는다. 기체 펌웨어의 근접 제동은 계속 발생할 수 있다.

AprilTag 검출·ID0 재획득·태그 촬영 정지는 필요 없다. 카메라 영상은 가능하면 기록하되 태그 없는 위치 제어 입력은 아니다. 영상이 검게 나오는 기존 증상은 별도 오류로 남긴다.

## 시작·중단·인계 확인

- 현장 전방향/높이/이동 구역 확인과 바닥 위치 유지 확인을 site revision에 기록한다. 출발점·왼쪽 1m 위치는 줄자 등으로 현장 검증할 수 있으나 프로그램의 AprilTag 입력은 아니다.
- 이륙 전 직접 FC 상태 조회로 `IsFlying=false`와 `AreMotorsOn=false`를 확인한다. `armed`는 앱 제어 arm 상태이므로 실제 모터 정지의 대체 증거가 아니다. 조회 실패나 통신 결과 불명 상태에서는 이륙하지 않는다.
- 배터리 설계값은 시작 30% 이상, 비행 중 20% 이상이다. 현재 전화 IP·PC 연결은 현장 확인한다. 이 파일에 과거 hotspot IP·비밀 토큰을 고정하지 않는다.
- 이륙 ACTION의 결과가 timeout이면 재전송하지 않는다. VS 제어를 받기 전과 각 비영점 명령 전에 실제 비행·VS enabled/advanced/MSDK authority·RC override·자료 상태를 확인한다.
- 상승 단계에는 1.5m 하한을 적용하지 않는다. 유효한 이륙 높이에서 시작하고 2.1m 초과·센서 불명·상승 기한 초과는 중단한다. 수평 이동은 1.8m 안정화 이후에만 허용한다.
- RC override, 링크 단절, 비행/모터 상태 불명, 자료 추정 단절, 제어권 상실, 높이/변위/시간/배터리 조건 실패 시 이후 비영점 명령과 자동 재개를 막는다. 제어권이 유효하고 소켓이 응답할 때만 제한 시간 안에서 zero → VS 해제 시도. 이미 RC 인계/소켓 불명 상태이면 추가 비행 명령 없이 연결 해제와 사용자 인계를 진행한다.
- 일반 제어/조회 전체 기한0.4초, takeoff/arm/stick_mode3초, disarm1초로 제한했다. `SLOW_COMMAND_TIMEOUT_S=20`을 새 loop에 사용하지 않는다. 전송 뒤 결과가 불명확하면 소켓을 폐기하고 재-arm/retry하지 않는다. 종료 시 제어 해제를 먼저 시도하고 모터 조회 작업자를 종료한 뒤 착륙을 읽기 조회한다.
- 기존 앱에는 시간 watchdog/명령 TTL이 없다. PC 연결 종료 자체가 폰에 전달되지 않는 Wi-Fi blackhole에서는 PC만으로 기체 정지를 보장할 수 없다. 사용자의 물리 RC 인계가 필요하며 이 앱 제약을 수정했다고 보고하지 않는다.
- RC 반환은 ACK만으로 확인하지 않는다. VS disabled 및 RC authority 증거가 없으면 `HANDOVER_UNCONFIRMED`로 알린다. RC 착륙 후 `IsFlying=false` 및 실제 모터 정지 조회가 성공하면 `LANDED_CONFIRMED`; 조회 실패라면 사용자 착륙 보고와 기계적 확인 미완료를 별도 기록한다.

## 기록과 시험 판정

미션별로 설정 JSON/hash, 시작 시각, site revision, Android build·펌웨어 확인값, 상태 전환·실패 원인, 요청 type/sequence/시각/payload와 ACK/SDK 결과, 원시 높이·속도·자세·OA 전체·age·SDK 오류, 적분 입력 dt와 출발/좌측/복귀 추정 좌표, VS 해제/RC/착륙 증거를 남긴다. 확인 토큰은 마스킹하고 로그 원본은 로컬 보관한다.

`route_completed_estimated`, `rc_handover_confirmed`, `landing_confirmed`를 분리한다. 시간 종료·ACK 성공·사용자가 봤다는 사실 하나만으로 프로그램의 1m 위치 정확도나 모든 단계 성공을 표시하지 않는다. 실기 없는 현재 준비 작업은 비행 성공/실패에 포함하지 않는다.

## 구현 전·후 검증 순서

1. 계획/JSON의 목표·방향·단위·종료 방식·참조 링크 및 PowerShell 문법을 검사했다. 기존 Python/비공개 설정/선택적 영상 의존성을 읽기 확인했다.
2. 모의 telemetry와 가짜 소켓으로 N/E yaw 0/90/±180°, 지연/관성, 잘못된 방향, duplicate/age/NaN/gap, timeout, RC override, ACK만 성공/실제 이동 없음, 높이 미도달, RC 인계 미확인을 검증했다. 모의 전체 실행에서는 약35초에 완료됐다. 실제 네트워크가 없는 기본 경로와 명시적 실행 옵션도 검사했다.
3. 모터 정지 상태에서 읽기 전용 FC·센서·소켓 검사를 수행한다. 지상 0 속도만으로 비행 중 속도 관측 가능 여부를 검증 완료로 표시하지 않는다.
4. 별도 현장 지시에 따라 짧은 감독 비행에서 육안/줄자 기준 실제 이동과 SDK 속도 방향·적분을 대조한다. 특히 새 BODY+VELOCITY 명령 경로와 하방 표시 높이를 확인한다. 무태그 속도 정보가 유효하지 않으면 이 계획을 자동 시간 이동으로 바꾸지 않고 RC 수동 시연으로 전환한다.
5. 현장 준비 상태에서 사용자 실행 지시에 따라 감독 왕복한다. 실행 CLI는 [현장 실행 안내](COEX_QUICKSTART_20260910.md)에 있다. 실기 정확도·연결 검증 여부는 결과에 별도로 기록한다.
