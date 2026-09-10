# FC / Perception 연결 수명 수정 명세 — 적용 준비본

작성일 2026-09-07. **설계 문서만 작성했으며 앱·PC 실행 코드, 설정, 패키지를 변경하지 않았다.** 실기체 접속이나 SDK 호출을 수행하지 않았다. 이 문서의 수치·필드·클래스는 아래에 명시한 현행 항목을 제외하면 적용할 구현 명세이다.

## 1. 해결 범위와 판정

목표는 (1) Product·영상은 정상인데 FC 조회만 실패하는 상태를 구분하고, (2) 실제 지상 증거가 있을 때만 제한된 구독 복구를 수행하며, (3) perception의 일회성 등록 플래그·세대 혼합을 없애는 것이다. SDK 내부 `REQUEST_HANDLER_NOT_FOUND`의 최초 발생 원인은 미확정이다. 구독 재등록이 그 내부 장애를 반드시 해결한다고 약속하지 않는다.

근거는 앱 장애 분석 (local reference; not published: `APP_CONNECTION_DIAGNOSIS_20260907.md`), 비집계 시험 기록 (local reference; not published: `UNCOUNTED_21312_TRIAL_20260907.md`), 원본 기반 감사 자료 (local reference; not published: `20260907T153944-ee259ff2.audit.json`)이다.

| 구분 | 확인된 사실 | 구현상의 결론 |
|---|---|---|
| FC 부분 장애 | 9/6 기본 FC GET 6종이 즉시 handler 오류, 다른 구성요소·영상은 정상 | Product 연결과 FC 준비 상태를 따로 표시한다 |
| 복구 공백 | `RetryableInit.ready`는 listener 설치 성공만 뜻하며 FC 조회 실패로 무효화되지 않음 | 설치 여부와 현재 건강 상태를 다른 필드로 둔다 |
| perception 등록 결함 | 등록 전 static `listenerStarted=true`, 예외 때 복원 없음, 재연결 무효화 없음 | 두 listener를 하나의 세대에 묶어 등록·해제한다 |
| OA 나이 의미 | 공식 콜백은 값 변경 알림이며 일정 주기를 보장하지 않음 | 마지막 콜백 나이만으로 장애·복구를 유발하지 않는다 |
| 9/7 시험 | FC 누적 실패 41 고정, 신선한 좌측 586mm로 PC가 중단 | 이번 좌측 거리 중단을 FC 통신 장애로 분류하지 않는다 |

비행 데이터의 유효성 기준, 자세·속도 제한, 1.4m/8초는 이 Android 통신 패치의 변경 대상이 아니다. PC 거리 정책은 root가 별도 지정한 최신 시험 runner와 metadata를 따르며, 이 패치가 이전 PC 0.7m 제한을 다시 넣지 않는다. 60000을 유효 60m로 취급하는 PC 판정은 별도 호환 패치가 필요하며, 이 문서의 구독 복구로 해결됐다고 표시하지 않는다.

## 2. 변경 파일을 고정한다

기준 루트는 `drone-control/android/SampleCode-V5`이다. 아래 새 파일들은 `android-sdk-v5-bridge/src/main/java/com/msdkremote/lifecycle/`에 둔다.

| 파일 / 함수 | 구현 내용 |
|---|---|
| [PcBridge.java:54](../../android/SampleCode-V5/android-sdk-v5-bridge/src/main/java/com/msdkremote/PcBridge.java#L54), `onProductConnected`, `onProductDisconnected`, `invalidateBindings`, `ensureSafely`, `diagnostics` | 이벤트를 단일 worker에서 순서대로 처리. FC coordinator와 perception 소유자 연결. 부분 복구에는 전체 `invalidateBindings()`를 호출하지 않는다 |
| [TelemetryProvider.java:93](../../android/SampleCode-V5/android-sdk-v5-bridge/src/main/java/com/msdkremote/livecontrol/advanced/TelemetryProvider.java#L93), `start`, `stop`, `pollSafely`, `pollValue`, `snapshotJson` | 키별 LISTEN/GET 성공 시각·오류·요청 ID 분리, FC 상태로 이벤트 전달, 요청 수 제한, 설정된 backoff. `.4`의 lock 안 시각 읽기 보존 |
| [ObstacleAvoidanceController.java:121](../../android/SampleCode-V5/android-sdk-v5-bridge/src/main/java/com/msdkremote/livecontrol/advanced/ObstacleAvoidanceController.java#L121), `startPerceptionListener`와 getters | static boolean을 `PerceptionBinding` 소유자로 대체. 불변 snapshot을 한 번 읽어 모든 OA 필드를 직렬화 |
| [MSDKManagerVM.kt:46](../../android/SampleCode-V5/android-sdk-v5-sample/src/main/java/dji/sampleV5/aircraft/models/MSDKManagerVM.kt#L46), `onProductChanged` | `PcBridge.onProductChanged(productId)`도 전달. 수신 이벤트 자체를 연결 성공이나 복구 성공으로 해석하지 않는다 |
| 새 `FcHealthTracker.java` | 순수 Java 상태 판정·키별 통계. Clock 주입, Android/SDK 의존 없음 |
| 새 `FcRecoveryCoordinator.java` | 지상 증거 수집·한 번의 복구·검증·cooldown 실행. SDK access와 scheduler는 interface 주입 |
| 새 `GroundProof.java` | 직접 GET의 IsFlying/AreMotorsOn 두 라운드와 세대·유효기간을 보관 |
| 새 `PendingSdkReads.java` | 논리적 lease 종료와 실제 미완료 SDK 요청 수를 분리해 추적 |
| 새 `PerceptionBinding.java` | 두 listener의 참조·등록 트랜잭션·세대·snapshot·정리 상태 소유 |
| 새 `MaintenanceGate.java` | 앱 takeoff/arm/새 이동과 복구 시작의 경쟁을 같은 lock으로 직렬화 |

`RetryableInit`는 TCP 서버 설치 등 기존 용도로 유지한다. `PollLease`의 기존 의미를 다른 호출자에게 바꾸지 않고, FC GET 발행 앞에 `PendingSdkReads` 제한을 추가한다. query 응답 epoch 격리·공통 GET 한도·조종 admission의 세부 계약은 [QUERY_TRANSPORT_IMPLEMENTATION.md](QUERY_TRANSPORT_IMPLEMENTATION.md)를 따른다. query용 별도 물리 GET pool을 만들지 않으며 총12/키별2 안에서 proof 예약을 둔다. 영상 Surface 복구는 영상 명세를 따른다.

## 3. 공통 식별자·스레드 규칙

- 앱 프로세스마다 `process_start_id`를 한 번 생성하고 `process_started_elapsed_ms`, PID, build ID를 기록한다. 매 STATUS마다 새 ID를 만들지 않는다.
- `connection_generation`: bridge worker에서 실제 연결 경계마다 증가. `telemetry_generation`, `perception_generation`: 해당 구독 교체/해제 전에 증가. `request_id`: 프로세스 내 증가하는 64비트 값.
- callback은 생성 당시의 `(process_start_id, connection_generation, owner_generation, request_id)`를 캡처한다. 상태 변경은 소유자 lock 안에서 세대 일치와 요청 소유권을 확인한 뒤 수행한다.
- 늦은 callback은 `discarded_old_generation_callbacks`만 증가시킨다. 새 데이터·age·오류 streak를 갱신하거나 새 요청을 완료시키지 않는다. 단, 실제 SDK 미완료 요청 카운트는 그 요청 ID에 대해 한 번만 해제한다.
- lifecycle SDK 등록/해제는 단일 worker에서 수행하되, 소유자 lock을 잡은 채 SDK 메서드를 호출하지 않는다. 동기 callback·예외가 발생해도 deadlock하지 않아야 한다.
- worker에 이벤트를 넣을 때 `productId`, 연결 상태, event serial을 값으로 캡처한다. 전역 `productConnected` 최신값만 읽어 이전 disconnect를 최신 connect로 오인하지 않는다. 이벤트 폭주를 합쳐도 마지막 경계 무효화와 세대 증가는 생략하지 않는다.

## 4. FC 데이터·건강 상태 명세

감시 대상은 기존 `AircraftVelocity`, `AircraftAttitude`, `UltrasonicHeight`, `IsFlying`, `FlightMode`이다. Battery는 FC handler 실패 집계에서 제외한다. `FlightControllerKey.KeyConnection`은 별도 owner의 LISTEN 및 초기 직접 GET으로 구성요소 변화를 기록한다. `KeyAreMotorsOn`은 전용 LISTEN과 1Hz 직접 GET을 추가한다. 지상 증거 수집 시 같은 발행 조정기에 우선권을 주어 두 라운드 직접 GET을 수행한다.

키별 필드:

```text
last_listener_received_ms, last_get_started_ms, last_get_success_ms
last_get_completed_ms, last_nonnull_value_ms, last_value_source = LISTEN | GET
consecutive_get_failures, consecutive_handler_not_found
last_error_code, last_error_type, last_error_ms
get_issued_total, get_success_total, get_failure_total, get_timeout_total
pending_sdk_request_ids, next_get_due_ms, failure_streak_started_ms
```

`onSuccess(null)`은 정상 값 증거가 아니다. `get_empty_total` 증가 및 EMPTY_RESULT 이벤트만 남기며 이전 값을 현재 시각으로 새로 찍지 않는다. LISTEN null은 해당 값 unknown으로 처리한다. 키의 기존 정상 값에 마지막 오류 문자열을 붙여도 `current_health`는 현재 시각과 최근 성공으로 별도 계산한다.

상태와 전이:

| 상태 | 진입 조건 / 다음 처리 |
|---|---|
| DISCONNECTED | SDK 미등록 또는 Product/FC 명시적 단절. 기존 송신 중단/RC 인계 경로 유지, 읽기 snapshot 무효화 |
| WARMING | 최초 binding 설치 또는 승인된 지상 교체 후. 설치 성공만으로 READY 금지 |
| HEALTHY | 핵심 5종 각각 현재 세대에서 직접 GET nonnull 성공이 있고, 각 마지막 성공 1초 이내. 비행 제어의 별도 0.5초 기준은 그대로 |
| DEGRADED | 핵심 키 하나라도 연속 GET 실패 3회, 또는 직접 GET 성공이 1초 이상 없음. LISTEN 무변화만으로 진입하지 않음 |
| HANDLER_FAULT | 서로 다른 FC 키 2종 이상이 각각 연속 handler 오류 3회 이상이며, 그 키들의 마지막 GET 성공이 1초 이상 전 |
| PROVING_GROUND | 오류 episode에서 복구 후보 선정 후 아래 직접 증거 검사 |
| REBINDING / VERIFYING | 유효 ground proof와 maintenance 예약 후 구독 교체 및 직접 GET 검증 |
| WAIT_OPERATOR | ground proof 불가, SDK pending cap 소진, 해제 실패, 또는 한 번의 복구 검증 실패 |

오류 종류는 현재 sample에서 사용 중인 `IDJIError.errorCode()`를 호출해 `"REQUEST_HANDLER_NOT_FOUND".equals(error.errorCode())`로 비교한다. 문자열 전체나 특정 영문 description에 의존하지 않는다. raw `error.toString()`은 진단 기록용으로 별도 보존한다.

한 키의 정상 callback이 오면 그 키 streak만 초기화한다. 전체 HEALTHY가 10초 유지됐을 때만 새 error episode를 시작할 수 있다. 누적 카운터는 복구 때 0으로 지우지 않는다.

## 5. 지상 증거와 복구 발행 순서

`TelemetryProvider.isGroundedFresh()`는 현재 IsFlying 하나만 보므로 **복구 허가로 사용하지 않는다**. PC의 이전 착륙 메시지·오래된 false·RC 스틱 0 역시 자동 복구 증거가 아니다.

1. coordinator가 maintenance 예약을 얻는다. `armed=false`, `enabling=false`, 현재 binding 세대에서 받은 VS 상태가 enabled=false여야 한다. 이 변경 알림의 age만으로 최신성을 판정하지 않는다. 이 단계는 제어권을 취득하거나 disarm을 새로 보내지 않는다.
2. 예약 후 신규 takeoff/arm/이동 요청을 `MAINTENANCE_IN_PROGRESS`로 거부한다. STATUS/GET/zero/disarm/land는 계속 허용한다. `AdvancedControlCommandHandler`, `FlightCommands.startTakeoff`, `StickControlManager.arm` 진입부 및 query ACTION/SET의 같은 항공기 변경 경로가 이 gate를 공유한다. STOP 요청은 언제나 우선한다. FlightCommands와 query 경로의 적용은 transport 담당과 같은 패치 세트로 묶는다.
3. 캐시 API가 아닌 `KeyManager.getValue(key, callback)`로 `KeyIsFlying`과 `KeyAreMotorsOn`을 순차 조회한다. 두 값이 모두 false인 라운드를 200ms 이상 간격으로 2회 확보한다. 각 GET timeout 1000ms, 전체 proof deadline 3000ms, 동시에 proof GET 최대 1개. 관련 일반 polling은 잠시 양보한다.
4. 두 라운드 모두 같은 connection generation, 성공값 false, latest proof 전체 나이 ≤500ms여야 한다. FC 오류·null·timeout·IsFlying/모터 true·세대 변경이면 예약 해제 후 WAIT_OPERATOR. 실패한 proof를 자동 반복하지 않는다.
5. 이 시점에 `connectionGeneration`, control reservation, VS disabled, proof 나이를 다시 확인한 후 **telemetry owner만** `stop()` → 새 owner generation/holder → `start()` 한다. RC·영상·VS·TCP 서버를 함께 재시작하지 않는다. 정상인 perception도 이 FC 부분 복구에 묶어 재시작하지 않는다.
6. 5초 동안 기존 빈도로 직접 GET을 받아 핵심 5종 모두 2회 이상 성공하고 2초 연속 HEALTHY인지 확인한다. 동시에 proof의 두 키를 500ms마다 직접 조회하되 일반 polling과 중복 발행하지 않는다. IsFlying/모터 true가 되면 추가 재바인딩은 금지하고 읽기 구독만 유지, maintenance를 해제하며 현재 상태를 보고한다.
7. 성공하면 상태 HEALTHY, `recovery_result=SUBSCRIPTIONS_VERIFIED`, 마지막 복구 원인·소요시간을 기록하고 예약 해제. 그 이전의 비행/음성 요청은 다시 실행하지 않는다.
8. 실패하면 WAIT_OPERATOR로 마무리. 오류 episode당 자동 교체 1회, 마지막 교체부터 최소 30초 간격. 재연결 이벤트 폭주로 이 예산을 초기화하지 않는다. 명시적 실제 Product 단절/재연결은 별도 episode로 기록하되 ground proof 없이 공중 교체하지 않는다.

**실제 9/6처럼 IsFlying/AreMotorsOn까지 전부 handler 오류인 경우 이 절차는 자동 재바인딩을 허용하지 못한다.** SDK 내부를 강제로 reset하지 않고, 사용자 RC 착륙 후 프로세스 재실행/USB 재연결을 별도 수동 복구로 기록한다. 이 경계는 기능 누락이 아니라 확인할 수 없는 기체 상태에서 자동 reset하지 않기 위한 결정이다. `SDKManager.destroy/init/registerApp` 자동 호출은 본 패치에 추가하지 않는다.

처음 앱이 시작할 때의 읽기 listener 설치는 ground proof를 기다리지 않는다. 그것이 상태를 알아내는 경로이기 때문이다. 이미 활동 중인 구독을 재구성하는 **복구**와, Product 단절 직후 소유 callback을 무효화하는 **정리**를 구분한다. 공중의 ProductChanged는 이벤트와 세대를 기록하고 복구 pending만 설정하며, 정상 구독을 통째로 교체하지 않는다.

## 6. GET 빈도와 미완료 요청의 상한

첫 적용의 HEALTHY/WARMING 주기는 기존 핵심 5종5Hz + Battery1Hz를 유지하고, 모터 상태1Hz를 더해 최대27 GET/s로 한다. 모터 GET을 추가하는 이유는 착륙 모터 정지의 독립 증거를 wire에 제공하기 위해서다. proof 중 같은 모터/IsFlying GET은 중복하지 않는다. 지상 정상 polling 감축은 원인 비교용 두 번째 실험으로 분리한다. 새 MSDK 패키지나 숨은 SDK 설정은 필요 없다.

- 각 키 연속 오류가 3회에 이르면 그 키의 다음 GET 간격을 1초 → 2초 → 5초로 늘린다. 지상/공중 모두 오류가 난 조회의 폭주를 멈추되 공중 송신의 유효성 제한을 늦추지 않는다. 한 번의 nonnull GET 성공으로 200ms 주기로 복귀한다.
- callback 미도착의 기존 2초 PollLease 만료는 SDK 요청 취소가 아니다. `PendingSdkReads`는 실제 callback이 오지 않은 요청 ID를 유지한다. 이 패치 소유 요청은 키별 최대 2개, 전체 최대 12개를 넘지 않는다. cap 도달 시 `SDK_READ_CAPACITY_EXHAUSTED`를 기록하고 자동 재발행하지 않는다.
- 논리 generation 변경만으로 pending 실물 카운트를 삭제하지 않는다. 늦은 callback이 오면 실물 pending은 해제하되 오래된 세대 데이터는 폐기한다. 프로세스 재시작 또는 별도로 검증된 SDK lifecycle 종료만 실물 pending을 초기화할 수 있다.
- recovery proof와 기존 IsFlying GET은 같은 발행 조정기를 사용한다. SDK 호출을 추가 스레드에서 중복해 보내지 않는다. 외부 query 사용자의 요청은 별도 transport 한도에도 포함해야 하며, 이 패치만으로 앱 전체 모든 GET의 총량이 제한됐다고 주장하지 않는다.
- 진단 JSON 생성이 SDK GET을 발생시키지 않도록 한다. STATUS는 snapshot 읽기만 수행한다.

## 7. Perception의 원자적 등록·해제

현행 `startPerceptionListener()`를 같은 공개 진입점으로 유지하고 내부를 `PerceptionBinding.ensureBound(connectionGeneration)`으로 위임한다. 대상 API는 기존에 사용 중인 `PerceptionManager.addPerceptionInformationListener`, `addObstacleDataListener`와 공식 대응 `removePerceptionInformationListener`, `removeObstacleDataListener`이다. 다른 UX 위젯 소유 구독에 영향을 주는 `clearAll...`는 사용하지 않는다.

새 owner 필드:

```text
state = UNBOUND | BINDING | BOUND | RETRY_WAIT | CLEANUP_FAILED
generation, connection_generation, register_attempts, failed_attempts
manager_ref, info_listener_ref, obstacle_listener_ref
info_added, obstacle_added, registration_started_ms, installed_ms
last_info_callback_ms, last_obstacle_callback_ms, last_actual_value_change_ms
info_callback_count, obstacle_callback_count, discarded_old_generation_callbacks
registration_error, cleanup_error, next_attempt_ms
snapshot: immutable PerceptionSnapshot
```

등록 순서:

```java
// worker; lock 안에서는 상태 준비만 한다.
BindingAttempt attempt = beginGeneration(connectionGeneration); // BINDING
try {
    sdk.addPerceptionInformationListener(attempt.infoListener);
    attempt.markInfoAdded();
    sdk.addObstacleDataListener(attempt.obstacleListener);
    attempt.markObstacleAdded();
    commitIfCurrent(attempt); // 두 add가 끝났을 때만 BOUND
} catch (RuntimeException e) {
    invalidateGeneration(attempt); // 먼저 late callback 차단
    removeOwnedListeners(attempt); // 각각 try; 한쪽 실패로 다른쪽 cleanup 생략 금지
    scheduleBoundedRetryOrManual(attempt, e);
}
```

SDK add가 동기 callback을 호출할 수 있으므로 BINDING 중 현재 attempt callback은 임시 snapshot으로 저장한다. BOUND commit 이후에만 공개 snapshot으로 게시한다. 실패한 attempt의 값은 공개하지 않는다. add가 내부 등록 후 예외를 던질 가능성에 대비해 참조를 만든 두 listener 모두에 대해 제거를 시도할 수 있게 한다.

해제는 generation 증가 → 공개 snapshot unknown 처리 → 저장해 둔 **기존 manager와 listener 참조**에서 각각 remove → 참조 해제 순서다. remove 하나라도 실패하면 CLEANUP_FAILED, 같은 manager에 새 listener를 계속 추가하지 않는다. 성공한 rollback의 등록 재시도만 1/2/5초 후 최대 3회, 이후 실제 연결 경계 또는 명시적 수동 복구 전까지 중지한다. 반복적인 `ensureSafely` tick이 이 예산을 리셋하지 않는다.

`PcBridge.invalidateBindings()`의 단절 정리에 `perception.invalidateAndUnbind()`를 추가한다. 최초 연결은 새 owner를 바인딩한다. 연결 상태가 유지되는 중 강제 perception 교체는 등록 실패·명시적 세대 불일치에 한정하고 위 ground proof를 공유한다. **callback age가 1초 또는 269초를 넘었다는 이유만으로 재등록하지 않는다.**

## 8. 거리·작동 상태의 프로토콜 의미

원시 배열과 단위는 보존한다. 새 `PerceptionSnapshot`에는 한 callback의 배열 복사, interval, up/down, callback receipt time, generation을 함께 넣어 array와 age가 서로 다른 callback에서 섞이지 않게 한다. 현재 개별 volatile getter 조합은 이 불변 snapshot을 읽는 방식으로 교체한다.

기존 `oa_obstacle_data_age_ms`는 호환성을 위해 유지하되 **마지막 SDK callback 수신 후 시간**이라는 의미를 명시한다. 이를 임의로 0으로 초기화하거나 STATUS 시각으로 덮어쓰지 않는다. 새 부가 필드는 다음으로 고정한다.

```json
{
  "oa_diagnostics": {
    "listener_state": "BOUND",
    "connection_generation": 3,
    "generation": 4,
    "source_process_start_id": "process-uuid",
    "callback_sequence": 18,
    "callback_semantics": "ON_CHANGE",
    "periodic_heartbeat_expected": false,
    "source_timestamp_available": false,
    "last_callback_age_ms": 269000,
    "last_value_change_age_ms": 269000,
    "info_received": true,
    "last_info_callback_age_ms": 350,
    "range_observation_state": "NO_USABLE_RANGE",
    "registration_error": null
  }
}
```

`callback_sequence`는 현재 세대의 수신 callback마다 증가한다. 같은 배열이면 `last_actual_value_change_ms`는 유지한다. 상태는 `NEVER_RECEIVED`, `EMPTY`, `NO_USABLE_RANGE`, `HAS_REPORTED_RANGE`로 나눈다. `HAS_REPORTED_RANGE`도 현재 장애물 여유 공간의 보증이 아니다. 배열의 유효성 검사용 0<raw<60000 정책은 분석 정책이며, DJI가 60000의 모든 의미를 공식 확정했다는 주장은 하지 않는다. 원시값을 삭제하지 않는다.

작동 상태는 새 snapshot에서 nullable Boolean 6방향으로 보존한다. 초기 false와 callback false를 구분하는 `info_received`/`field_present`를 둔다. 기존 `oa_sensors_working` 문자열은 호환 필드일 뿐이며 빈 문자열을 센서 OFF로 번역하지 않는다. `oa_type`, enabled, working, range 각각은 별개 정보다.

OA 설정 SET이 실행되는 기존 `close...`/`set...` 진입점에는 `operation_id`, 호출 출처(PC request ID 또는 UI action), 요청값, SDK 완료값/오류, 완료 시각을 남긴다. 읽기 getter·구독 재등록 경로에서 OA SET을 호출하지 않는다. 이 기록이 있어야 기존 BRAKE→CLOSE 변화의 주체를 판단할 수 있다.

PC 호환 요구: 새 진단 필드는 무시 가능한 추가 필드로 제공하며 NDJSON version1을 유지한다. 기존 PC age/60000 gate 의미는 이번 Android 패치에서 몰래 바꾸지 않는다. 그 판정 변경은 별도 버전·오프라인 재생·지상 검증 후 동시 배포한다.

## 8.1. PC 담당과 확정한 공통 wire 계약

[PC 구현 명세](PC_TELEMETRY_IMPLEMENTATION.md)의 표와 아래를 공통 계약으로 사용한다. 별도의 `perception_health` alias나 두 번째 competing DTO를 만들지 않는다. 모든 필드는 **ACK payload.telemetry 내부**이며 envelope를 바꾸지 않는다.

| 정확한 wire 경로 | 자료형·초기값 | 발행 주체/의미 |
|---|---|---|
| `bridge_health.process_start_id` | string, 프로세스 UUID | PcBridge.start 최초1회 생성; 모든 SDK 세대가 공유 |
| `are_motors_on` | boolean 또는 null | 실제 KeyAreMotorsOn 값. 초기 unknown이며 armed 대입 금지 |
| `are_motors_on_age_ms` | nonnegative integer 또는 null | 마지막 현재세대 nonnull 모터 callback/GET 저장 경과시간 |
| `fc_health.state` | 위 상태 enum string | FcHealthTracker 상태 |
| `fc_health.generation` | integer | telemetry owner generation |
| `fc_health.keys` | object keyed by SDK key identifier | 각 키 `{listener_age_ms,get_success_age_ms,consecutive_failures,last_error_code}`; 미수신 age/error는 null |
| `fc_health.recovery_state` | string | coordinator 상태 및 WAIT_OPERATOR 이유는 별도 reason |
| `oa_diagnostics.generation` | integer | perception owner generation |
| `oa_diagnostics.listener_state` | 위 binding state string | BOUND는 설치 완료라는 뜻 |
| `oa_diagnostics.callback_sequence` | integer | 현재세대 obstacle callback 번호, 초기0 |
| `oa_diagnostics.last_callback_age_ms` | integer 또는 null | obstacle callback 미수신이면 null |
| `oa_diagnostics.last_value_change_age_ms` | integer 또는 null | raw 배열/간격/up/down 실제 변화 이후 경과시간 |
| `oa_diagnostics.source_process_start_id` | string | bridge_health.process_start_id와 동일 |
| `oa_diagnostics.status_fields` | object | 아래 상태 필드별 `{reported:boolean,value:any,age_ms:integer|null}` |
| `oa_obstacle_data_age_ms` | 기존 integer | 동일 snapshot의 last_callback_age_ms legacy alias. 새 값 null이면 기존 호환값 -1 |

`status_fields`의 정확한 key는 `oa_type`, `oa_horizontal_enabled`, `oa_upward_enabled`, `oa_downward_enabled`, `vision_positioning_enabled`, `oa_sensors_working`이다. 초기값은 각각 `{reported:false,value:null,age_ms:null}`이다. 현재세대 PerceptionInfo callback이 왔을 때만 reported=true로 바꾸며, 실제 nullable 값을 false로 채우지 않는다. `oa_sensors_working.value`는 기존 문자열을 유지하고 nullable6방향은 부가 `working_directions` object로 제공한다. PerceptionInfo와 ObstacleData는 callback이 다르므로 서로의 age를 복사하지 않는다.

FC `keys` 식별자는 `AircraftVelocity`, `AircraftAttitude`, `UltrasonicHeight`, `IsFlying`, `FlightMode`, `AreMotorsOn`, `Connection`으로 고정한다. Battery 통계는 별도이며 FC handler fault 판정의 분모에 넣지 않는다. owner와 connection_generation, pending 수, 폐기 callback 수 등의 추가 진단은 이 object에 확장 가능하다. 일반 STATUS에서 이 필드를 채우려고 즉석 SDK GET을 발행하지 않는다.

새 Android 필드가 없는 .4의 PC 파싱은 unknown을 보존한다. 새 필드와 legacy alias가 불일치하면 PC는 raw 둘 다 기록하고 `DIAGNOSTIC_ALIAS_MISMATCH`를 남긴다. 새 프로토콜 수신 시 기존 나이 필드를 새로 만들어 신선하게 보이게 하지 않는다.

## 9. 핵심 의사 diff

```diff
 TelemetryProvider.pollValue(...):
+ if (!readBudget.canIssue(key, now) || now < health.nextDue(key)) return;
+ RequestId request = readBudget.issue(key, connectionGeneration, generation);
  KeyManager.getInstance().getValue(key, callback);
  callback:
+   readBudget.finishPhysicalOnce(request);
    synchronized (lock) {
      if (!currentGeneration(request) || !lease.complete(request.lease)) return;
+     health.recordGetResult(key, request, nonNullValueOrError, now);
      // existing stored values and .4 timestamp locking remain
    }
+   bridgeWorker.execute(() -> recovery.onHealthChanged(health.copy()));

 PcBridge.ensureSafely():
  // existing TCP server lifetimes remain
+ fcRecovery.tick(clock.now()); // does not block waiting for SDK callback
+ perception.ensureBound(connectionGeneration);
- // bindings_ready is used only as installed status, never FC_READY
+ // PcBridge.diagnostics supplies bridge_health.process_start_id only.
+ // TelemetryProvider.snapshotJson adds these INSIDE payload.telemetry:
+ json.put("fc_health", fcRecovery.snapshot());
+ json.put("oa_diagnostics", perception.healthSnapshot());

 MSDKManagerVM.onProductChanged(id):
  lvProductChanges.postValue(id)
+ PcBridge.onProductChanged(id)
```

구현 시 위 의사 코드를 이미 컴파일된 API로 취급하지 않는다. 실제 Java facade와 fake를 함께 추가해 SDK 5.18 타입 검사를 통과시킨 뒤 APK를 만든다.

## 10. 오프라인 회귀 테스트의 완료 조건

JUnit4.13.2는 기존 bridge 의존성에 있다. 새 외부 라이브러리를 설치하지 않고 Clock/Scheduler/KeyReads/PerceptionApi fake를 사용한다. 실제 소켓, SDK 실기체 호출, Thread.sleep을 금지한다.

| 테스트 이름 | 입력 | 필수 검증 |
|---|---|---|
| `FcHealthTrackerTest.oldErrorDoesNotMeanCurrentFault` | counter41 고정, 모든 키 성공 지속 | HEALTHY, 복구0회, last_error 이력은 보존 |
| `partialHandlerFaultIsSeparateFromProduct` | Product true, FC2종 각3회 실패,1초 성공 없음 | HANDLER_FAULT, 영상/RC 상태를 고장으로 바꾸지 않음 |
| `changeOnlyListenerSilenceIsNotFault` | LISTEN 값 불변, GET 성공 | HEALTHY 유지 |
| `emptySuccessCannotRefreshValue` | GET onSuccess(null) | 이전 값 age 그대로, EMPTY_RESULT 증가 |
| `GroundProofTest.requiresTwoKeysTwoRounds` | false/false1회, 한쪽 stale, null, wrongGeneration 조합 | 모두 거부. 두 라운드의 fresh false만 허용 |
| `cannotRecoverDuringTakeoffAdmissionRace` | proof 최종 callback과 takeoff/arm 경쟁 | 동일 gate로 한쪽만 예약, 복구와 신규 제어 동시 실행0 |
| `fullFcOutageDoesNotGuessGround` | 모든 FC handler error | WAIT_OPERATOR, stop/start/destroy/arm 호출0 |
| `oneRebindPerFaultEpisode` | 동일 오류100tick | telemetry 재등록1회 이하,30초 내 추가0 |
| `airborneFailureDoesNotResetSdk` | IsFlying true/모터 true/unknown | 읽기 진단만 수행, 구독 교체/SDK reset0 |
| `lateGenerationCallbackCannotOverwrite` | stop/start 뒤 이전 GET/LISTEN 반환 | 새 snapshot 불변, discard counter만 증가 |
| `PendingSdkReadsTest.noCallbackCannotFlood` | callback 전부 폐기, 가상시각10분 진행 | 키별2개, 소유 전체12개 이하. generation만 바뀌어도 예산 소거 금지 |
| `PerceptionBindingTest.secondRegistrationThrows` | info add 성공, obstacle add 예외 | BOUND 금지, 소유 참조2개 cleanup, retry 한도 보존 |
| `synchronousCallbackBeforeCommit` | add 안에서 callback | commit 전 공개 금지, 실패 시 폐기 |
| `oldManagerCannotPublish` | manager 교체 후 이전 callback | 새 세대의 range/info/age 불변 |
| `cleanupFailureStopsReregister` | remove 하나가 예외 | 다른 remove 실행, CLEANUP_FAILED, 신규 add0 |
| `steady60000IsNotConnectivityFailure` | 전부60000,269초 무변화 | NO_USABLE_RANGE, binding BOUND, 재등록0, age를0으로 덮지 않음 |
| `workingFalseOrUnknownCanCoexistWithRange` | 빈 working과586/1337 실제 기록 | 거리 보존, 센서 OFF 단정 없음 |
| `snapshotIsSingleCallback` | callback2개와 읽기 경쟁 | 배열/간격/위/아래/callback_sequence가 같은 snapshot, 음수 age0 |

현행 `RecoveryTest`, FrameBuffer/DeliveryHealth tests도 회귀 실행한다. 이는 적용 시 수행할 테스트 목록이며 지금 실행됐다는 뜻이 아니다.

## 11. 지상 수락 시험과 중단 조건

사용자 착륙·모터 정지 및 직접 GET 확인 후 수행한다. 7회 연속 비행 횟수에는 포함하지 않는다. 설치·복구 시험 중 자동 이륙하지 않는다.

1. **기준군**: .4, 같은 폰/RC/기체/케이블, 앱 전경,15분 지상 대기를3구간 수행한다. GET latency, 키별 오류, PID, Product/FC 이벤트, OA callback/change 시각, 조명·전원 조작을 기록한다.
2. **수정판 진단만 활성화**: `automatic_ground_rebind=false`, 기존 GET 주기로 같은 조건3구간. 옛 오류 문자열이나 일정한60000만으로 복구 후보가 생기면 실패다.
3. **등록 lifecycle**: 모터 정지 상태에서 USB 재연결3회, 화면 재생성3회. 한 세대에 info/obstacle owner 각각1개, 이전 세대 callback의 공개값 덮기0, 정상 FC/영상 불필요 재시작0이 합격 기준이다. PID 변화와 화면 재생성을 구분한다.
4. **제한된 자동 복구**: 지상 proof를 얻을 수 있는 부분 장애를 debug fake로 만들고, 등록 재실행1회,5초 내 검증 성공 후에도 takeoff/arm/경로 재개0을 확인한다. 실기체 장애가 재현되면 동일 기준으로 검증한다. 실제 SDK가 회복되지 않으면 ‘탐지·억제 성공 / 근본 복구 미달’로 기록한다.
5. **거리 변화**: 모터 정지 상태에서 알려진 방향·거리의 무늬 있는 물체를 이동해 callback과 원시값 반응을 기록한다. 지상에서 관측할 수 없으면 미확인으로 남긴다. 기체나 모터를 움직여 callback을 만들도록 자동 시험을 바꾸지 않는다.
6. **전체 FC 장애**: debug fake로 모든 직접 GET을 실패시킨다. WAIT_OPERATOR에서 멈추며 무한 poll·프로세스 reset·SDK destroy0을 확인한다.

실제 SDK handler 오류가 재발하면 최초 발생 원인이 해결됐다고 판정하지 않는다. 15분×3 무재발은 해당 관측 구간의 결과다. 값 뒤섞임, 예기치 않은 OA SET, 중복 listener, 제어 접수 경쟁, 요청 상한 초과가 하나라도 있으면 비행 적용을 보류한다.

## 12. 빌드·배포·롤백

기준 MSDK5.18 / 기존 JDK17 / 같은 Gradle 캐시를 사용하며 의존성 업그레이드를 섞지 않는다. 아래는 **나중에 적용할 때 실행할 명령**이며 이번에는 실행하지 않았다.

```powershell
# cwd: drone-control/android/SampleCode-V5\android-sdk-v5-as
.\gradlew.bat :bridge:testDebugUnitTest --offline
.\gradlew.bat :sample:assembleDebug --offline
```

단독 패치 배포명은 `5.18-fc-perception.20260907.5`, versionCode `20260910`으로 지정한다. 다른 담당 패치와 같은 APK에 묶는 경우 상위 통합 명세의 공통 버전 하나를 사용한다. sample `build.gradle`과 telemetry `bridge_build_id` 불일치를 빌드 검증에서 거부한다. 기존 PC runner는 .4를 엄격 비교하므로 업데이트 APK로 비행하려면 별도 검토된 runner의 허용 build ID도 갱신해야 한다. 새 APK에 옛 ID를 붙여 통과시키지 않는다.

적용 전에 변경 대상 파일, 기존 미커밋 diff, 기존 APK, SHA256, versionCode를 보존한다. 기체가 작동 중일 때 설치·앱 재시작을 하지 않는다. 개발 설정의 `automatic_ground_rebind=false`로 지상 단계2까지 검증한 뒤 단계4에서 제한적으로 활성화한다. 이 플래그는 내부 개발 설정이며 조종 화면의 불필요한 설정 흐름으로 추가하지 않는다.

롤백의 첫 수단은 자동 복구 플래그를 비활성화한 재빌드다. 이전 로직으로 돌아가야 하면 저장한 해당 diff만 복원하고 더 높은 versionCode로 이전 로직을 다시 빌드해 앱 데이터를 유지한다. Android 환경에서 이전 .4 APK 직접 downgrade가 허용되지 않을 수 있으므로 앱 삭제를 자동 실행하지 않는다. 설정·SDK 등록 정보를 보존하고 runner와 build ID 일치를 다시 확인한다.

읽은 기준 소스 SHA256:

```text
PcBridge.java                    163EA7FBC48F6D5378F5566EA0A1E79FD89C60C464E21329A09A629046285120
TelemetryProvider.java           909E6C1CD2A9A9D940A42DCBEFEED9D3918E04A349939D002BDB5AA7A2B21E05
ObstacleAvoidanceController.java AB224332CAA6F5CB9D999E91712062FFE9702C8C5E1888A12C39848C74A385AF
```

## 13. 공개 API 근거와 남은 제약

이번 세션 앞부분에 이미 확인한 공식 자료를 연결한다. 이번 구현 명세 작성에서는 네트워크에 추가로 접속하지 않았다.

- [DJI IKeyManager](https://developer.dji.com/api-reference-v5/android-api/Components/IKeyManager/IKeyManager.html): 비동기 GET과 캐시 GET의 차이, owner를 지정하는 listen/cancelListen.
- [DJI IPerceptionManager](https://developer.dji.com/api-reference-v5/android-api/Components/IPerceptionManager/IPerceptionManager.html): 각 listener의 add/remove API. SDK 내부 FC request handler를 재생성하는 공개 API가 있다는 뜻은 아니다.
- [DJI ObstacleDataListener](https://developer.dji.com/api-reference-v5/android-api/Components/IPerceptionManager/IPerceptionManager_ObstacleDataListener.html): 값 변경 시 onUpdate.
- [DJI ObstacleData](https://developer.dji.com/api-reference-v5/android-api/Components/IPerceptionManager/IPerceptionManager_ObstacleData.html): 거리 mm 단위와 수평 배열 각도 간격.

구현을 시작하는 데 필요한 구조·수치·소유권 선택은 위에서 고정했다. 미확정 항목은 실제 SDK의 복구 효과와 지상 OA 관측 조건이며, 검증 전에 자동 비행 준비 완료나 근본 수정 완료로 표시하지 않는다.

## 14. 16:25 Test4 사전검사 재발 캡처 추가 — 구현 전 회귀 근거

사용자의 오류 기록 요청을 반영했다. 전방100cm·후방90cm 물체3개인 새 환경의 Test4 이륙 전,16:25:41에 FC6종 GET이모두 `REQUEST_HANDLER_NOT_FOUND`를 반환했다. Product/RC/AirLink/Camera 연결true, Battery 직접 GET60%는 성공했다. 이 실패 검사에서는 비행 명령0이며 `NOT_STARTED`다. 직전 정상 확인16:11:06과 검출 사이 약14분36초는 **최초 고장 시각 또는 고정된 재발 주기**가 아니다.

- 실패 GET fixture 원본 (local reference; not published: `20260907T072542032824Z.json`): FC Connection/IsFlying/AreMotorsOn/UltrasonicHeight/AircraftVelocity/FlightMode 실패6종, 지연0–46ms.
- 동시 STATUS fixture 원본 (local reference; not published: `20260907T072617_test4_rear90_three_preflight_fc_error.json`): 진단 경로는 `status.telemetry`. bindings_ready=true이나 FC값null, poll318/last_error_age127ms/expired0/generation3. 직접 Battery GET60%와 telemetry battery=null도 별도 보존한다. raw video frame0, enabled=true, PC client=false이므로 영상 건강을정상으로합성하지않는다.
- 사용자 USB 재연결 후 복구 fixture (local reference; not published: `20260907T072633956948Z.json`):16:26:33 FC6종과나머지6종모두성공, IsFlying=false/AreMotorsOn=false/속도0/배터리60%. 실제 재조회 회복은 확인됐으며 근본 원인 해결은 미확정이다.

기존 `partialHandlerFaultIsSeparateFromProduct`, `fullFcOutageDoesNotGuessGround`, `steady60000IsNotConnectivityFailure` 테스트에 더해 이 사건을 `CapturedOutage20260907Test` fixture로 넣는다. 필수 assertion은 (1) 기본연결true와listener설치상태로 FC_READY를 만들지 않음, (2) FC값unknown을 지상false로 바꾸지 않음, (3) 최근 실제handler오류와누적이력을구분, (4) 빈OA/0/콜백age2174ms를FC고장의원인으로단정하지않음, (5) 새세대/직접성공증거후에만회복표시, (6) 회복 뒤 자동takeoff/arm/경로재개0이다. 기존의 두 라운드 지상proof 조건을 단일 복구캡처 하나로 충족했다고 처리하지 않는다.

이 추가는 기록·회귀 명세만 갱신한다. SDK·앱·실행기 코드는 변경하지 않았으며 USB 재연결로 일시 회복했다는 결과를 앱 수정 완료로 기록하지 않는다.

## 15. Test5 16:34 재발 fixture 및 재연결 상태 추적 보강

Test4 성공·16:28:29 지상 정상 확인 이후 Test5 사전검사16:34:34에 다시 FC6종 `REQUEST_HANDLER_NOT_FOUND`가 발생했다. Product/RC/AirLink/Camera 연결true, Battery53%는 정상이며 FC 오류 응답은0–16ms다. 재발 GET 원본 (local reference; not published: `20260907T073434585318Z.json`). 이 실패 검사에서 비행 명령0, 결과NOT_STARTED다. 약6분5초는 마지막 정상 확인과 검출 사이 간격이며 실제 최초 발생 시각은 미확정이다. **사용자 USB 재연결 뒤16:35:49 재조회도FC6종전부실패해 USB단독으로는회복되지않았다.** 이후사용자앱강제중지·재실행보고뒤16:37:03FC6종모두회복을확인했다.

회귀 fixture에 `recurrenceAfterPriorSuccessfulRecovery`를 추가한다. 앞서 복구됐다는 이유로 다음 FC오류 episode를 무시하지 않고, 이전 episode의성공기록을보존하면서 새오류를탐지해야 한다. 현재 FC상태unknown에서 과거지상proof를재사용하지 않으며 OA60000·콜백무변화만으로복구를시작하지 않는다. 이 캡처만으로 SDK 내부 타이머나 특정유휴시간을원인으로확정하지 않는다.

또한 Test3의 horizontal_enabled는첫ACK부터true923개, 재연결후Test4는첫지상STATUS부터false951개였고 Test4의 PC요청에OA설정명령은없었다. 상태필드의 `reported/value/age` 및 process/connection/perception generation을 함께 기록하는 기존 명세의 수락사례로 추가한다. 상태차이는보존하되 모든센서OFF·펌웨어제동OFF·PC설정변경을자동추론하지 않는다. 구현은아직수행하지않았다.

16:35:03 STATUS fixture (local reference; not published: `20260907T073503_test5_rear90_three_preflight_fc_error.json`)도추가한다. `status.telemetry`에서 poll3096/last_error_age176ms/expired6/generation3, FC값null인동시에 raw camera_frames15210/age24ms/keyframe_age1731ms다. PC video client=false일때오래된deliveryage만으로VIDEO_FAULT를만들지않고 **RAW_VIDEO_ALIVE와FC_FAULT를동시에표현**해야한다. horizontal_enabled=true와OA콜백age415419ms도원문보존하되true/false변화나콜백무변화를FC고장원인으로합성하지않는다.

사용자USB재연결뒤 16:35:49 GET fixture (local reference; not published: `20260907T073549448143Z.json`)는FC6종전부같은handler오류로실패했다. `reconnectEventWithoutFreshSuccessIsNotRecovery` 테스트를추가하여 USB연결이벤트/사용자완료보고만으로HEALTHY를만들지않고 실제핵심GET성공증거를요구한다. 이번USB단독복구실패는사실로기록하며실패사전검사는NOT_STARTED로보존한다. SDK reset·자동arm/takeoff/resume는추가하지않았다.

사용자강제중지·재실행보고뒤 16:37:03 회복 fixture (local reference; not published: `20260907T073703495878Z.json`)는FC6종을포함한12개GET모두성공했다. IsFlying=false/AreMotorsOn=false/속도0/배터리53%이며FC응답15–47ms다. 같은오류episode에서 **USB후실패→앱재실행보고후직접GET회복**을순서대로보존하는검증자료로추가한다. 프로세스교체자체의PID독립증거는없으므로회복의내부원인을확정하지않고 근본수정완료도선언하지않는다. 이한번의GET묶음으로기존두라운드지상proof와무재발수락시험을대체하지않는다.

## 16. Test6의 다중 구성요소 조회 실패 fixture

16:41:43 정상 GET (local reference; not published: `20260907T074143066322Z.json`)은12개모두성공/ProductType=DJI_MINI_4_PRO/지상·모터정지/배터리48%였다. 약3분17초뒤 16:44:59 실패 GET (local reference; not published: `20260907T074459856998Z.json`)은ProductConnection=true/RCConnection=true이나 ProductType=UNRECOGNIZED이며 AirLink·Camera·Battery와FC6종까지9개가REQUEST_HANDLER_NOT_FOUND로실패했다. FC단독부분장애와증거범위를구분한다. 이검사에서는비행명령0/NOT_STARTED이며 실제최초발생시각과원인은미확정이다. 전원·USB 확인과 앱 강제 중지·재실행 요청 뒤 사용자의 “연결했어” 보고 및16:48:59 조회 회복을 확인했다. 이후 사용자가 배터리 교체를 확인했으며, 정확한 탈착 시각과 그 밖의 앱 조작은 독립 확인하지 못했다.

`unrecognizedProductWithMultipleComponentErrorsNotFcOnly` fixture를추가한다. 필수검증은 Product/RCtrue만으로READY를만들지않음, ProductType오인식과구성요소별GET오류를동시에보존, 옛기체타입·배터리·지상proof재사용금지, FC단독원인또는특정전원/케이블원인으로자동단정하지않음, SDKreset/자동arm/takeoff/resume0이다. 기존FC추적기의이벤트자료에다른구성요소오류도연결해기록하되 원인미확정인채복구범위를무작정확대하지않는다. 새경로2→3→1→3→2를이오류의원인으로기록하지않으며 앱소스변경은없다.

16:45:34 STATUS fixture (local reference; not published: `20260907T074534_test6_preflight_expanded_error.json`)에는 poll3829/last_error_age50ms/expired6와FC값null, bridge readytrue가공존한다. raw frame12682/camera_age86960ms/keyframe_age87330ms, stream_enabledtrue/binding_generation19/attempts8/retry22409ms다. Test5의raw영상age24ms와달리이번은약87초갱신없음도함께표현해야한다. PC client없음의deliveryage문제와구분하며 stream_enabled/installedtrue만으로VIDEO_HEALTHY를만들지않는기존검증에이fixture를추가한다. OAage425584ms는단독장애판정트리거로사용하지않는다.

16:48:59 회복 fixture (local reference; not published: `20260907T074859082838Z.json`)는12개 GET 모두 성공, ProductMini4Pro/지상·모터 정지/속도0/배터리99%다. 이전 정상48%와 달라진 이유에 대해 사용자가 **배터리를 직접 교체했다고 확인했다.** 이 fixture는 배터리 교체·기체 전원 전환 맥락을 함께 보존하고, FC 앱 버그 확정 사례 또는 앱 강제 종료 단독 복구 사례로 집계하지 않는다. 정확한 탈착 시각과 SDK 오류의 동기 계측은 없어 세부 인과관계는 미확정이다. 재연결 경계에서 장치 식별과 지상 proof를 다시 확인하는 검증자료로 사용하며 SDK 근본 수정 완료 증거로 사용하지 않는다. 앞선 Test4·5의 FC 부분 장애 기록과 회귀 fixture는 그대로 유효하다.
