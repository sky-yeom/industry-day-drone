# 9997 query 연결·요청 소유권 수정 — 적용 준비 명세

작성일 2026-09-08. **문서만 작성했으며 소스 적용·테스트·빌드·설치·실기 검증은 수행하지 않았다.** 실제 bridge 소스를 읽어 기존 통합 명세의 transport 위임 부분을 구체화했다. SDK handler 오류의 원인이나 이 패치의 실기 복구 효과는 입증되지 않았다.

## 1. 실제 수정 지점과 소유권

아래 경로는 모두 `drone-control/android/SampleCode-V5/android-sdk-v5-bridge/src/main/java/com/msdkremote/` 기준이다. sample의 동명 KeyItem 또는 비어 있는 livequery/KeyCommandHandler를 수정 대상으로 혼동하지 않는다.

| 파일·현행 signature | 구현할 변경 |
|---|---|
| `commandserver/CommandServer.java`: `private void run()`, `public synchronized void stopServer() throws InterruptedException`, `public long getConnectionEpoch()`, `public void sendMessage(@NonNull String message, long epoch)` | 아래 연결 시작/종료 계약과 scoped reply를 적용한다. 기존 일반 send overload는 다른 호출자 호환용으로 유지한다. |
| `livequery/QueryServerManager.java`: `public synchronized void startServer(int port, String armToken)`, `public synchronized void killServer() throws InterruptedException`, 내부 `commandServerStateListener` | QuerySessionRegistry를 소유하고 handler에 전달한다. 연결 종료/서버 종료 때 query listener와 논리 요청을 정리한다. 기존 외부 state listener 전달을 보존한다. |
| `livequery/QueryCommandHandler.java`: `public void onCommand(@NonNull CommandServer commandServer, @NonNull String command)` | 파싱·기존 TOKEN 검증 후 불변 QueryRequestContext 생성, admission, SDK adapter 호출. HELP/unknown/auth 오류를 포함한 모든 답변도 수신 연결에 묶는다. |
| `livequery/KeyItem.java`: `commandGet(CommandServer)`, `commandListen(CommandServer)`, `commandUnlisten(CommandServer)`, `commandSet(CommandServer,String)`, `commandAction(CommandServer)`, `commandAction(CommandServer,String)` — 모두 public void | 현재 호출자는 QueryCommandHandler다. 각 메서드의 서버 인자를 QueryRequestContext로 교체하고 해당 호출자를 함께 변경한다. private `sendMessage(CommandServer,String/Object)`도 context 기반으로 교체한다. getParameter/기존 key capability 검사/성공값 변환은 보존한다. |
| 새 `livequery/QueryRequestContext.java`, `QuerySessionRegistry.java`, `QuerySdkAdapter.java` | 요청 식별·단일 응답·listener holder·논리 deadline·SDK effect를 분리한다. Clock/Scheduler/SDK interface를 주입해 오프라인 테스트한다. |
| FC 명세의 새 `lifecycle/PendingSdkReads.java`, `MaintenanceGate.java` | **별도 FC budget/gate를 복제하지 않는다.** 아래 shared GET 한도와 최종 mutation admission을 같은 인스턴스로 처리한다. |

QueryRequestContext는 server identity, 수신 connectionEpoch, process_start_id, Product connection_generation, 내부 증가 requestId, method/module/key, replyOpen을 캡처한다. requestId는 내부 기록용이며 기존 9997 wire에 추가하지 않는다. 키의 budget 식별자는 표시 문자열만이 아니라 실제 생성한 DJIKey의 component/index 포함 식별자다.

## 2. 연결·응답·listener 수명

1. CommandServer는 현재 accept 때만 epoch를 증가시킨다. 개선판은 accept 때 queue lock 안에서 새 epoch와 active=true를 게시하고 queue를 비운다. reader가 받는 handler wrapper는 **그 accept의 epoch를 캡처**한다. 오래된 reader dispatch는 현재 연결로 재해석하지 않는다. CommandHandler에 기본 overload `onCommand(CommandServer,String,long)`를 추가해 기존 2인자 구현으로 위임하고, query handler는 3인자 경로를 구현한다. 기존 9998 동작과 sequence 규칙은 유지한다.
2. EOF/예외/stop/finally는 같은 `closeSession(expectedEpoch)` 경로를 사용한다. queue lock 안에서 해당 active session만 inactive로 만들고 epoch를 무효화·queue clear한 뒤, lock 밖에서 query owner 정리 알림을 정확히 한 번 전달한다. reader/writer 종료와 SDK cleanup은 queue lock 안에서 하지 않는다. 새 accept 이전에 이전 writer가 종료된 기존 순서를 보존한다.
3. `sendMessage(message,epoch)`는 같은 queue lock에서 epoch 일치와 active를 모두 검사한다. query의 즉시 오류·HELP·GET/SET/ACTION callback·LISTEN event가 모두 이 경로를 사용한다. reply의 논리 terminal CAS와 epoch 검사로 timeout 뒤 늦은 callback 또는 중복 callback의 두 번째 답변을 막는다. stale callback도 해당 물리 pending ID 완료 처리는 한 번 수행한다.
4. LISTEN은 세션별·키별 고유 holder를 생성한다. SDK 등록 전에 BINDING record를 만들고 동기 callback은 임시 보관, 등록 반환 뒤 CURRENT일 때만 게시한다. 동일 세션/키의 중복 LISTEN은 추가 등록과 새 성공 줄 없이 멱등 처리한다. 기존 LISTEN은 값 callback만 보내므로 새 등록 ACK를 끼워 넣지 않는다. 세션당 최대 16개 holder다.
5. UNLISTEN은 `cancelListen(DJIKey.create(keyInfo), capturedHolder)`로 그 등록만 취소한다. 기존 `cancelListen(key)`와 전역 KeysManager holder는 제거한다. source에 owner별 overload 사용 사례는 sample `keyvalue/KeyBaseStructure.java:189`에 있다. 해당 bridge SDK 타입으로 컴파일 확인해야 한다. UNLISTEN은 먼저 record를 무효화하고 성공 시 기존 `module key success`를 반환한다. 미등록 키는 success다.
6. 종료와 Product 세대 변경은 먼저 listener/request를 무효화한 뒤 저장한 KeyManager/holder로 각각 cleanup한다. cleanup 실패 holder는 보존해 실패 상태로 두고 자동 재등록하지 않는다. process 전체 active+cleanup 미확정 holder 상한도 16이며 재접속으로 우회하지 못한다. Product 변경은 현재 연결의 미완료 요청에 source-changed 오류를 한 번 보내고 명시적 재요청을 기다린다. 과거 LISTEN을 새 기체 세대에 자동 이식하지 않는다.

## 3. 실제 pending 상한·deadline·복구 우선권

| 항목 | 고정 계약 |
|---|---|
| 공통 GET ledger | FC 문서 PendingSdkReads **하나**에 TELEMETRY / GROUND_PROOF / QUERY owner를 기록한다. 통합 GET 총 pending ≤12, 같은 DJIKey ≤2. query만의 추가 12개 pool은 만들지 않는다. |
| query 하위 한도 | process 전체 QUERY GET pending ≤4. 세션의 일반 one-shot(GET/SET/ACTION)은 논리적으로 1개만 진행하며 추가 요청은 즉시 busy다. SDK 요청을 쌓는 대기 queue는 없다. |
| proof 예약 | 일반 FC+query GET 전체 pending ≤11로 제한해 순차 proof용 1개를 남긴다. IsFlying/AreMotorsOn 각각 일반 pending ≤1, 두 번째 key slot은 proof 전용이다. proof 시작 뒤 해당 일반 polling/query 발행은 양보한다. 이 규칙은 FC 기존 최대12/키별2를 늘리지 않는 통합 refinement다. |
| 논리 deadline | GET 2000ms, SET/ACTION 5000ms. deadline은 terminal timeout 응답과 논리 reply 종료만 한다. callback 미완료 물리 ID·mutation permit은 반환하지 않는다. 앱에서 실제 SDK 요청을 취소했다고 표시하지 않는다. |
| 물리 완료 | SDK 호출 전에 ID/permit을 예약한다. 성공/실패 callback의 최초 도착 때만 finishPhysicalOnce한다. 호출 직전 로컬 검증 실패는 미발행으로 반환 가능하다. SDK 메서드가 동기 예외를 던졌는데 미발행을 증명할 수 없으면 UNKNOWN_PENDING으로 slot을 유지한다. |
| exhaustion | QUERY 4개, 공통/키 상한 도달이면 SDK 호출 0과 capacity 오류. reconnect, deadline, Product 세대 변경, 논리 owner 교체가 물리 ID를 지우지 않는다. 늦은 callback은 ID만 반환하고 새 세션에 답하지 않는다. 자동 reset/재시도 없음. |
| proof 자체 미완료 | 예약 slot이 proof 자신의 미완료 호출로 막히면 FC 계약대로 WAIT_OPERATOR다. 예약은 query 때문에 생기는 starvation을 방지하며 SDK 무응답까지 해결하는 수단은 아니다. |

SET/ACTION은 읽기 ledger에 넣지 않는다. QuerySessionRegistry의 process 수명 mutation ledger에 일반 mutation pending 최대2와 **landing 전용 pending 최대1**을 따로 둔다. 같은 method/key의 미완료 mutation은 재발행하지 않는다. callback 없는 요청이 이 상한을 유지하며 일반 요청이 landing slot을 소비하지 못한다. 기존 9998 zero/disarm/emergency_stop/land는 이 query 한도에 들어가지 않는다. 따라서 앱의 모든 SDK 호출 총량을 제한했다고 주장하지 않는다.

LISTEN 중인 같은 키의 GET/SET/ACTION 또는 일반 one-shot pending 중 그 키의 LISTEN은 local busy로 거절한다. 기존 wire에는 requestId나 event 구분자가 없어 같은 키의 callback과 명령 응답을 신뢰성 있게 구별할 수 없기 때문이다. UNLISTEN과 검증된 landing은 이 일반 busy 규칙의 예외이며 query에는 STOP/zero/disarm 명령을 새로 발명하지 않는다.

## 4. 동일 MaintenanceGate로 조종·복구 경쟁 처리

기존 arm token 검증은 유지한다. 정상 시 기존 SET/ACTION capability를 유지하고 **모든 query SET/ACTION은 보수적으로 mutation**으로 분류한다. maintenance가 예약됐으면 거절한다. 예외는 확인된 `FlightControllerKey.KeyStartAutoLanding` ACTION뿐이며, 이미 존재하는 `FlightCommands.startLanding(StickControlManager.ResultCallback)`로 전달하고 기존 query의 `success`/SDK 오류 줄로 변환한다. 문자열의 stop/zero 포함 여부로 예외를 추정하지 않는다.

Gate는 handler의 사전 bool 검사에 그치지 않는다. mutation permit 발급과 recovery 예약을 같은 lock에서 원자적으로 결정하고, SDK effect는 lock 밖에서 한다. permit을 받았지만 아직 발행하지 않은 action도 복구 예약을 막는다. async pending permit은 timeout·연결 종료로 해제하지 않는다. 중복 query/FlightCommands 경유 호출은 같은 permit을 전달해 두 번 취득하지 않는다.

- 현행 `FlightCommands.startTakeoff(@NonNull StickControlManager.ResultCallback callback)`와 `StickControlManager.arm(@NonNull ResultCallback callback)`의 최종 admission에 shared gate를 둔다. `public boolean setVelocity(long sequence,double forward,double right,double up,double yawRate)`, `public boolean setAttitude(long sequence,double forwardTilt,double rightTilt,double up,double yawRate)`, `public void selectMode(@NonNull String requested,@NonNull ResultCallback callback)`의 새 제어 수락도 gate와 동일한 상태 전이에 묶는다. 기존 arm epoch/sequence/freshness 검사를 보존한다.
- `AdvancedControlCommandHandler.onCommand(CommandServer,String)`의 현행 types는 arm/takeoff/land/heartbeat/stick_mode/velocity/attitude/zero/disarm/emergency_stop/gimbal/obstacle_avoidance/status다. 새 조종과 SET 성격의 명령은 gate를 통과하며 STATUS/heartbeat는 유지한다. 실제 literal STOP type은 없으므로 문서의 STOP은 기존 zero/disarm/emergency_stop 의미다.
- `zero(long sequence)`, `disarm(ResultCallback)`, `emergencyStop()`, `FlightCommands.startLanding(ResultCallback)`는 maintenance/busy로 거절하지 않는다. 먼저 recovery ticket을 무효화해 이후 복구 단계가 계속 진행되지 않도록 하고 기존 처리·RC 인계·release 경로를 실행한다. 완료된 SDK 효과를 되돌렸다고 기록하지 않는다. 기존 token/sequence 검증을 우회하지 않는다.
- `disarm()`의 armed=false는 실제 SDK disable 완료 이전일 수 있다. recovery 예약은 pending arm/mutation뿐 아니라 pending release가 없다는 증거도 요구한다. `zero()`는 현행대로 목표값을 0으로 만들며 pending arm 취소로 확대하지 않는다. 현행 land의 disarm callback→startLanding 후속 실행은 원래 operation과 Product generation을 캡처하고 검사한다. 단순 소켓 단절은 이미 접수한 착륙 intent를 다른 세션에 넘기거나 복제하는 이유가 아니며, 기체 세대 변경이면 새 SDK action을 발행하지 않고 unresolved로 기록한다. 같은 기체 세대의 접수된 착륙은 기존대로 disarm 성공 여부와 관계없이 진행한다.
- takeoff와 실행 시점을 알 수 없는 항공기 mutation은 SDK callback 성공을 기체 실행 완료로 취급하지 않는다. admission 시 unsafe-intent latch를 잡고 ACK 뒤에도 유지한다. 실제 flight/motor 전이를 독립 관측한 뒤 착륙·새 ground proof를 확인하거나, 명시적 operator 확인 및 새 proof로 해소하기 전 자동 복구를 예약하지 않는다. timeout/오류 문자열만으로 latch를 지우지 않는다. SDK success 직후 false 두 번은 아직 시작 전일 수 있으므로 latch 해제 근거가 아니다.

MaintenanceGate부터 StickControlManager lock 순서로 획득하며, SDK 호출/외부 callback은 양쪽 lock 밖에서 한다. STOP 처리는 일반 mutation permit을 기다리지 않는다. 이 문서의 범위는 bridge/9997/9998 진입점이며, sample의 모든 별도 수동 key-value 화면을 gate로 통제했다고 주장하지 않는다. 제한된 자동 복구는 처음 OFF로 두고 통합 admission 검증 뒤에만 활성화한다.

자동 복구 ON의 추가 조건은 실제 앱에서 도달 가능한 모든 SDK 조종·설정 변경 경로가 같은 gate를 통과하는 것이다. sample 수동 화면 등 범위 밖 진입점이 남으면 자동 복구는 OFF로 유지하고 진단·연결 수명 수정만 배포한다. 이를 통합 검사 몇 개의 성공만으로 대체하지 않는다. 기존 9998 land의 disarm callback→startLanding 후속 실행도 원래 Product generation과 landing intent를 캡처해 검사한다. 교체된 기체/무효화된 intent에 지연 ACTION을 발행하지 않고 실행 불가 원인을 기록한다. 단순 응답 송신 epoch 검사만으로 후속 SDK 발행의 소유권이 보장됐다고 판단하지 않는다.

## 5. 9997 wire·오류·기록 계약

명령 문법, TOKEN 위치, 정상 `module key value`, `module key success`, `module key null`, 기존 SDK `IDJIError.toString()` 및 HELP/기존 auth 오류 내용을 유지한다. 9998 NDJSON v1 envelope와 sequence 검사를 변경하지 않는다. 새 오류는 **첫 토큰 `QUERY_LOCAL_ERROR`**로 시작해 SDK 원문과 구분한다.

```text
QUERY_LOCAL_ERROR <module> <key> <code>
```

code는 `BUSY`, `SDK_READ_CAPACITY_EXHAUSTED`, `SDK_MUTATION_CAPACITY_EXHAUSTED`, `SDK_DEADLINE_EXCEEDED`, `MAINTENANCE_IN_PROGRESS`, `SOURCE_CHANGED`, `LISTENER_LIMIT`, `LISTENER_CLEANUP_FAILED`, `INVALID_PARAMETER`, `SDK_SUBMISSION_UNCERTAIN`, `LANDING_ALREADY_PENDING`으로 고정한다. malformed 명령의 없는 module/key는 `-`다. local error가 오면 client는 SDK 값 또는 성공으로 파싱하지 않고 명시적으로 실패 처리해야 한다. 새 줄 접두어 인식 테스트가 배포 조건이다. SDK callback failure의 원문은 기존 형식으로 유지하며 token은 진단에 기록하지 않는다.

로그는 process_start_id, server epoch, Product generation, 내부 requestId, method/module/key, admitted/issued/terminal/physical-finished, pending counts, stale drops, cleanup 결과를 기록한다. callback 없는 요청은 `logical_timeout/physical_pending`으로 남긴다. 원본 오류를 정상값·현재 건강으로 합성하지 않는다.

## 6. 구현 후 검증·배포 조건

bridge `src/test/java/com/msdkremote/livequery/`의 QuerySessionTest / QueryPendingTest / QueryListenerTest와 `lifecycle/MaintenanceGateTest`에 fake clock/SDK/manual scheduler를 사용한다. 아래 검증은 **아직 실행하지 않았다**.

| 회귀 입력 | 필수 assertion |
|---|---|
| 연결 A GET→disconnect→B 연결→A callback | B 송신0, A 물리 slot만 한 번 해제. disconnect 직후 새 accept 전에도 stale enqueue0. |
| timeout→새 논리 요청→원래 callback 두 번 | 원래 terminal1회, 새 응답 오염0, physical finish1회. reconnect/세대 변경도 cap 리셋0. |
| QUERY GET 4개 전부 무응답 + 일반 FC 부하 | QUERY 추가발행0; 총12/키2 초과0; reserved ground 두 키의 순차 proof 발행 가능. proof도 무응답이면 WAIT_OPERATOR/자동reset0. |
| SDK 호출 중 동기 callback·그 뒤 throw / callback 없는 throw | 이중 slot 반환0; 불확실한 발행은 pending 유지. |
| LISTEN 동기 callback·중복 등록·disconnect·remove 실패 | commit 전 송신0, 키별 holder1, 타 모듈 cancel0, 실패 holder 상한 유지, 이전 세대 값 게시0. |
| LISTEN 같은 키 GET / malformed param / 잘못된 TOKEN | 명시적 local busy 또는 기존 auth 오류, SDK mutation0, 다른 정상 응답 형식 유지. |
| proof 마지막 결과와 arm/takeoff/query ACTION 동시 admission | gate 한쪽만 예약; 미완료/ACK만 받은 takeoff에서 recovery0. |
| QUERY 일반 cap 소진·maintenance 중 zero/disarm/emergency_stop/land | 기존 9998 처리가 일반 queue/permit에 막히지 않음; query landing은 별도 slot, duplicate 재발행0; 이후 recovery 단계 중단. |
| 기존 9997 fixture와 9998 ACK fixture | 정상/SDK 오류 원문 호환, 새 QUERY_LOCAL_ERROR를 값으로 해석0, 9998 sequence 완화0. |

적용 직전 원래 diff와 SHA256을 보존한다. JDK17/기존 SDK5.18에서 실제 adapter generic 타입·cancelListen overload를 컴파일하고 `android-sdk-v5-as`의 `gradlew.bat :bridge:testDebugUnitTest :sample:assembleDebug --offline --console=plain`을 통과시킨다. 기존 Recovery/FrameBuffer/DeliveryHealth 및 PC 호환 검증을 함께 실행한다. SDK/라이브러리 업그레이드는 넣지 않는다.

통합 APK 버전·hash·설치·지상 검증·롤백은 APPLY_ORDER를 따른다. 먼저 자동 지상 복구 OFF로 모터 정지 상태에서 GET/LISTEN 연결 수명과 진단을 확인한다. 착륙 ACTION/비행 명령은 오프라인 fake로 admission을 검증하며 단순 통신 점검 때문에 실제 발행하지 않는다. SDK 근본 복구 효과는 별도 실기 증거가 필요하다. 사용자 앱 데이터/SDK 등록정보를 삭제하지 않는다.

읽은 기준 SHA256: `CommandServer.java=597A8DDD6F7580EEA094555034A89DF54C5842CBE363CE7D9EEEA1171D886360`; `KeyItem.java=F442449B4621605572C1CCB9611BEDD3E8ADCA2202D6682A4D5D0032FE9A6EED`; `QueryCommandHandler.java=44C8A806625D696F86B0842A427BE42D031BCDBFA6EAEEDA21E341EC34D38829`; `QueryServerManager.java=B5CCEC0BD2DF2F1F8F3E9E549FA8135EC99D61884D2215249CA902251082884A`.

미확정 제약은 SDK 내부 실제 cancel/완료 특성, hardware mutation 실행 시점, 실제 복구 효과다. 따라서 timeout을 취소로 간주하지 않고, 미확정 요청이 남으면 보수적으로 용량 소진/복구 보류한다. 위 구현 계약을 시작하기 위한 추가 사용자 선택은 필요 없다. 다만 임의 query mutation 이후의 unsafe-intent는 자동 복구 가용성을 낮출 수 있으며 이 경계를 숨기지 않는다.
