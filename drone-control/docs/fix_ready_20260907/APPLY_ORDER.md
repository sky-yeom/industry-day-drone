# 앱 오류 수정 적용 순서 — 구현 준비 완료

상태: **파일·함수·상태 전이·스키마·테스트·롤백 명세 완료. 앱 코드 적용/빌드/설치/수정 효과 검증은 아직 하지 않았다.** 사용자는 비행 검증과 동시에 바로 구현할 수 있을 정도의 준비를 요청했다. 거리로 중단하는 PC 시험 정책 변경과 앱 통신 오류 수정은 별도 작업이다.

**사용자 범위 정정(2026-09-08): 대시보드·Speech의 계획/시나리오는 상대 팀 담당이다.** 우리 후속 작업은 [드론 Tool 제어 코드·수정 계획](DRONE_TOOL_CONTROL_IMPLEMENTATION_PLAN_20260908.md)과 [모의 연동 준비 코드](../../integration/speech_control_contract/README.md)를 따른다. 외부가 정한 목적지 순서를 검증·실행하고 상태/센서/사진/오류를 반환하는 계층을 준비한다. 이전 시나리오 플래닝 문서는 우리 구현 범위에서 제외하며 시간 창·최종 이미지·채점은 우리 코드의 선행 조건이 아니다. 실기 연결은 공통 진단 계약과 실행 profile 검증 뒤에 수행한다.

## 공통 계약과 담당 명세

| 적용 묶음 | 확정 명세 | 실제 수정 범위 |
|---|---|---|
| FC·센서 구독 | [FC_PERCEPTION_IMPLEMENTATION.md](FC_PERCEPTION_IMPLEMENTATION.md) | PcBridge, TelemetryProvider, ObstacleAvoidanceController, query/flight command admission, 새 FcHealthTracker/FcRecoveryCoordinator/MaintenanceGate 및 구독 상태 관리 |
| 9997 조회 연결·요청 제한 | [QUERY_TRANSPORT_IMPLEMENTATION.md](QUERY_TRANSPORT_IMPLEMENTATION.md) | CommandServer, QueryServerManager, QueryCommandHandler, KeyItem의 연결별 응답·listener 정리, 공통 GET 예산과 조종 admission |
| 영상 활성화·표시 | [VIDEO_IMPLEMENTATION.md](VIDEO_IMPLEMENTATION.md) | AvailableCameraListener, FrameBuffer, VideoServerManager, DeliveryHealth, FPVWidget/Model, 새 순수 activation/surface policy, PC vision diagnostics |
| PC·프로토콜 | [PC_TELEMETRY_IMPLEMENTATION.md](PC_TELEMETRY_IMPLEMENTATION.md) | protocol Telemetry/parser, runtime 거리 해석, 로그/세대별 오류 집계, patrol 착륙 증거·cleanup 결과 분리 |

wire 필드명의 기준은 FC 명세8.1이다. `bridge_health.process_start_id`, `fc_health.keys`, `oa_diagnostics.generation/listener_state/callback_sequence/last_callback_age_ms/last_value_change_age_ms/source_process_start_id/status_fields`, `are_motors_on/are_motors_on_age_ms`를 사용한다. 기존 `oa_obstacle_data_age_ms`는 같은 스냅샷의 callback age alias로 유지한다. PC 시험의 회차 필드는 실제 runner와 동일한 **trial_number**다. v1 envelope 및 요청 sequence 의미를 변경하지 않는다.

2026-09-08 보완한 query 명세는 FC 문서의 transport 위임 부분을 대체한다. PendingSdkReads는 FC·query·지상 확인이 공유하며 총12/같은 키2 상한을 유지한다. query 하위 상한4, 일반 요청 총11과 지상 확인용 예약1 등 세부 배분은 query 명세3절을 기준으로 한다. deadline과 재연결만으로 물리 요청 슬롯을 반환하지 않는다. 새 `QUERY_LOCAL_ERROR` 응답 호환 검사와 연결 종료 시 구독 정리는 같은 패치로 묶는다. 공유 gate를 거치지 않는 수동 SDK 변경 경로가 있으면 자동 지상 복구는 OFF로 배포한다.

## 순서대로 적용할 변경

1. **기준 보존·진단 자료형.** 문서의 SHA256과 작업 트리를 비교하고 실제 변경 파일과 미커밋 차이를 보존한다. 새 프로세스 ID/구독 generation/값의 출처를 추가하고 Android snapshot을 일관되게 만든다. SDK 호출이 없는 자료형/parser 테스트부터 통과시킨다. 모터 정지는 armed=false가 아니라 실제 AreMotorsOn 값으로 처리한다.
2. **FC poll/오류 상태와 query 응답 세대.** 과거 오류 문자열과 최근 실패 증가를 분리한다. 현재 세대/요청 lease에 속하지 않는 callback은 갱신하지 않는다. 미완료 요청은 실제 완료 전 재사용하지 않고 상한을 지킨다. 기존26GET/s에서 실제 모터1Hz를 추가하면27GET/s라는 점을 계측한다. 정식 오류 재현 원본을 fixture로 사용한다.
3. **센서 listener 생명주기.** 두 listener 등록을 하나의 트랜잭션으로 관리하고 실패 정리/재등록/세대 무효화를 명시한다. `listenerStarted=true`만으로 성공을 판단하는 경로를 없앤다. 마지막 변경 callback 경과시간을 단독 연결 고장 신호로 사용하지 않는다. 60000/0/빈 배열을 유효 거리와 구분하지만 raw 값은 보존한다.
4. **영상 activation과 Surface 수명.** 현재 manager/camera/generation의 enabled callback에서 필요한 활성화를 처리하고 다음 detach까지 방치하지 않는다. 파괴되거나0크기인 Surface를 재등록하지 않는다. raw/전송/keyframe/decode/폰표시는 따로 진단한다. decode probe는 기본OFF이며 자체 영향 비교를 포함한다.
5. **제한된 지상 복구 조정.** 위 진단과 세대 검증이 먼저 통과한 뒤 적용한다. 같은 장애당1회/최소30초 간격, 최신 독립 IsFlying=false·AreMotorsOn=false 두 라운드와 조종 명령 admission lock이 확보됐을 때만 진행한다. FC 전체 조회가 실패해 지상 증거가 없으면 WAIT_OPERATOR다. SDK 전체 destroy/reset, 자동 스틱·모터 입력, 자동 이륙/재arm/resume는 추가하지 않는다.
6. **PC 착륙 완료·cleanup 분리.** 실제 모터 정지·비행 상태·VS/RC를 확인한 결과와 cleanup 예외를 구분한다. 이미 지상/제어권 해제된 뒤의 disarm 거절을 경로 실패로 오인하지 않되 원본 오류는 남긴다. 이번 비집계 성공과 정식 Test1 실패를 서로 다른 fixture로 유지한다.
7. **통합 검사·버전·지상 검증.** 각 담당 명세의 단위/회귀 검사를 실행하고 이어 Android sample assemble을 수행한다. 설치 전 APK hash, build ID, versionCode를 대조한다. 통합 후보 build ID는 `5.18-connectivity.20260907.5`, versionCode는현재값보다큰`20260910`을 예정값으로 하되 실제 적용 직전 최신 설치버전과 중복 여부를 재검증한다. 새 APK를 옛.4 ID로 표시하지 않는다. 지상복구 플래그는 처음OFF로 두고 지상 수락 시험을 통과한 뒤만 제한 활성화한다.

명세의 회귀 사례는 **계획된 검증 항목**이며 지금 통과했다고 표현하지 않는다. 기존 거리 관측 전용 PC 시험기의5개 검사 외에 새 경로3개 검사와 Test8 회차 번호 확장 검사가 오프라인에서 통과했다. 모두 시험기 변경 검증이며 앱 수정의 회귀 검사와 구분한다. FC/영상 결함 수정의 효과나 실제SDK 핸들러 소실의 복구를 아직 증명하지 않았다.

2026-09-08 사용자가 시험 종료를 확인했다. 마지막 Test8의 경로·자동 착륙 완료 이벤트와 종료 후 독립 조회 접속 시간초과는 별도 보존했다. 후자는 SDK 오류 응답을 받은 사례가 아니므로 PC 명세10절의 transport/SDK 오류 분리 사례로 추가했다. 시험 종료 뒤 기체에 다시 접속하거나 추가 비행하지 않는다. 구현 준비는 완료됐고 실제 코드 적용·빌드·설치·지상 수락 검증은 남아 있다.

## 최소 검증과 완료 기준

- 비행제어기 오류의 신규/과거 구분, 늦은 callback, USB 재연결, 실패한 listener 등록, 반복되는 camera available/disabled 이벤트, Surface destroy/recreate, 동시 조종 명령과 지상복구 경쟁을 오프라인에서 검증한다.
- 원시 센서값이 계속 같을 때 불필요한 재등록을 하지 않고, 실제 데이터가 바뀌면 새 세대의 값이 갱신되는지 지상에서 확인한다.
- 스틱 조작 없이 PC 디코딩 및 폰 표시가 시작되는지 별도로 검증한다. PC raw 수신만으로 폰 표시 성공을 선언하지 않는다.
- FC 전체 handler 실패는 자동 복구 성공을 미리 가정하지 않는다. 복구 가능한 부분구독 문제와 SDK 내부 handler 문제를 로그로 구분한다.
- 새 APK와 PC parser의 호환성, 기능별 지상 검사, 장시간 지상 대기를 통과한 뒤 새 빌드로 비행 검증 구간을 별도로 시작한다. 이전 성공과 합쳐 연속7회로 집계하지 않는다.

## 현재 비행 정책과 원본

사용자는 센서 기록 유지와 PC의 장애물 거리 중단 조건 전체 제외를 명시했다. 정식 테스트1 (local reference; not published: `COUNTED_TEST1_20260907.md`)은 별도 counted runner에서 그 정책으로 실행했다. raw 수평360배열/상하방/age/상태는 전부 남아 있다. 앱/펌웨어 회피 설정은 이 변경에 포함되지 않는다. 기존 고도·영상·제어권·RC·배터리·명령 상한·시간 제한은 유지된다. 앱 오류 수정이 이 사용자 정책을 몰래 되돌리지 않도록 별도 회귀 검사를 둔다.

테스트1 복귀 구간에서는42.313초 동안 오른쪽+1.5도 제출과 새 telemetry/영상이 유지되는데 수평속도0이 보고됐다. 이것은 FC 조회 단절과 다른 진단 대상이다. 현재 API에는 기체의 각 명령 적용을 증명하는 callback이 없으므로, SDK 제출을 기체 실행 성공이라고 표시하지 않는 것이 이 수정 묶음의 범위다. 실제 정체 원인의 확정은 추가 기체 진단 대상으로 남긴다.

롤백은 보존한 해당 변경만 대상으로 하고 사용자의 기존 변경·앱 데이터·SDK 등록정보를 삭제하지 않는다. 자세한 지상 배포/롤백 절차는 각 명세의 마지막 절을 따른다.
