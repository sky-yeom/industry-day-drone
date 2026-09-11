# 우리 담당: Speech Tool 호출을 위한 드론 제어 계층

2026-09-08 사용자 범위 정정 반영. **대시보드, Speech 대화·경로 계획, 시나리오·채점·이미지 의미 해석은 상대 팀 담당이다.** 우리 담당은 상대 팀이 정한 목적지 순서를 받아 검증·실행하고 기체 상태·센서·도착·사진·오류를 반환하는 제어 코드다. 이전 SPEECH_SCENARIO_INTEGRATION_PLAN은 우리 구현 명세에서 제외한다.

이 문서는 실제 코드 변경 위치와 배포 전 검증을 정한다. [연동 준비 코드](../../integration/speech_control_contract/README.md)는 별도 폴더의 모의 구현이다. **현재 실기 adapter/HTTP 서비스는 연결하지 않았고 비행·설치·대시보드 수정도 하지 않았다.** 기존 앱 FC/영상/query 수정은 APPLY_ORDER의 계획대로 별도 적용한다.

## 1. 양 팀 계약

| 상대 팀이 전달/수행 | 우리 코드가 제공/수행 |
|---|---|
| Azure 세션에 tools 등록, 완료된 function call 확인·인자 전달·결과 음성화 | 배포 가능한 tools JSON, 호출 wrapper, 기계적으로 검증되는 요청·응답 |
| 시나리오 이름/모니터 이름을 물리 destination_id에 대응; 방문 순서 결정 | 물리 목적지/실행 profile/site revision/capabilities 공개 |
| 실행 의도별 안정적인 request_id, 호출자 식별과 계획 완료 후 실행 요청 | 중복 실행 방지, 현재 기체 상태 확인, 같은 기체의 단일 실행 |
| 화면·경로 추천·목표 인물/장면 판단·결말 | 도착/통과 구분, 촬영 증거·시간·원시 센서·실패 이유 |

우리는 방문 우선순위를 계산·변경하지 않는다. 입력한 순서가 현재 지원되지 않으면 `ROUTE_UNSUPPORTED`와 지원 capability를 반환한다. 임의로 다른 순서나 가까운 목적지를 대신 실행하지 않는다. 실제 이동 방향·구간 제어는 검증된 profile을 이행하기 위한 낮은 수준의 실행 책임이다.

## 2. 외부 도구 표면

함수명과 schema의 배포 파일은 [tools.json](../../integration/speech_control_contract/tools.json)이다. schema와 Python 검증 코드는 함께 검사한다. 처음 제공할 명령은 다음7개다.

| Tool | 입력 | 응답 의미 |
|---|---|---|
| `drone_get_capabilities` | 없음 | API/version, mock/live 여부, live_ready, site_revision, 목적지·지원 profile·경로 |
| `drone_get_status` | 없음 | 현재 mission, 비행·실제 모터·VS/RC·연결 상태, 자료 age/unknown |
| `drone_execute_route` | profile_id, site_revision, destination_ids | 외부가 정한 순서 검증 후 임무 접수; mission_id/accepted |
| `drone_get_mission` | mission_id | 단계, 실행 결과, 현재 leg/stop, 이벤트 참조 |
| `drone_stop_mission` | mission_id | stop_requested 접수; 이후 실제 제어 해제/상태 확인을 별도 보고 |
| `drone_get_sensor_snapshot` | 없음 | 마지막 원시 OA360/상하방·단위·자료 age·FC 상태·출처 |
| `drone_get_captures` | mission_id | 해당 임무의 capture_id·시각·tag/stop·사진 조회 참조 |

초기7개에 raw attitude/velocity, 일반 SDK SET/ACTION, 임의 shell/script/파일 경로/host/port/토큰 입력을 넣지 않는다. 독립 go_to/takeoff/land 도구가 필요하면 해당 동작을 따로 검증한 capability로 추가한다. 초기 execute profile은 이륙부터 정지 방문·귀환·착륙을 포함하고, stop은 자동 조종 중단과 RC 인계를 뜻한다. `stop_mission`을 즉시 모터 정지나 자동 착륙 명령으로 해석하지 않는다.

요청 schema는 unknown field를 거절하고 명확한 자료형·문자 길이·목록 길이·등록 ID·site_revision을 검증한다. 반복 방문은 유효한 profile에 있을 수 있어 목적지 배열 전체의 중복을 일괄 금지하지 않는다. 예제의 `tag-1/2/3`는 물리 태그 ID이며 시나리오 모니터와 동일하다고 가정하지 않는다.

## 3. 모의 코드와 실기 코드의 경계

준비 코드 디렉터리 `integration/speech_control_contract/`:

- `contract.py`: tool 인자 검증, 호출 문맥, 주입 가능한 transport wrapper, 구조화 오류.
- `tools.json`: 상대 팀이 Voice Live에 등록할 함수 정의.
- `mock_service.py`: 기체 연결 없는 단일 임무·중복 실행 방지·중단 요청 모델.
- `example.py`: capability → 실행 접수 → 조회 → 중단 요청 연동 예제.
- `tests/test_contract.py`: 잘못된 인자·중복·동시 호출·시간초과·중단 의미·응답 변경 격리 검증.

모든 모의 응답은 `execution_mode=mock`, `physical_execution=false`이고 live_ready=false다. 실제 센서가 없으면 null/unknown이며 모의 성공값을 실제 기체 값처럼 생성하지 않는다. 예제의 지원 profile은 시험과 같은 물리 순서2→3→1→3→2지만 모의 실행만 지원한다. 향후 live capability는 코드·버전·현장 검증을 통과한 profile만 게시한다.

우리 실기 구현 대상은 `pc/drone_nav/tool_control/` 신규 package로 contracts/service/mission_manager/snapshot_store/artifact_store/live_adapter/execution_profiles를 나눈다. 준비용 모듈은 실기 API와 혼동되지 않도록 기존 CLI·패키지 entrypoint에 연결하지 않았다. 구현 때 schema를 한 곳에서 관리하고 배포본과 service 검증의 차이를 검사한다.

## 4. 실제 수정 파일과 함수

| 변경 지점 | 필요한 구체적 변경 |
|---|---|
| 신규 `tool_control/mission_manager.py` | 단일 기체 임무 admission, 영속 idempotency, 작업자 소유권, stop latch, 상태·사건 기록 |
| 신규 `tool_control/service.py` | loopback HTTP/JSON endpoint와 호출자 인증, 크기·schema·scope 검사. 명령을 HTTP event loop 안에서 끝까지 실행하지 않음 |
| 신규 `tool_control/live_adapter.py`, `execution_profiles.py` | 마지막 counted trial의 실행 흐름을 의존성 주입 가능한 profile로 이동. 외부 순서는 검사만 하고 선택/최적화하지 않음 |
| `protocol.py:601` `NDJSONClient` | 단일 작업자 사용 유지. send 완료 후 telemetry/result/motion/수신시각/sequence의 일관된 불변 snapshot 게시. 제한된 `interrupt_io()`로 blocked I/O 중단 경로 추가 |
| `protocol.py:658` `send` | 완료된 ACK 기준 snapshot, 요청/SDK제출/실제효과 구분, stop 후 비영점 명령 발행 방지, 현재 세대 확인. 다른 tool thread가 직접 send하지 않음 |
| `patrol.py:536,750,1014,1081,1146,1268` | acquire/traverse/photo-pause/zero-pause/floor-align/landing-wait에 cancel token·event sink·구조화 결과를 전달; 각 loop/대기와 발행 경계 확인 |
| `runtime.py:957,1355` | arm-wait/takeoff-wait를 취소 가능한 공통 helper로. 진단용 run_flight_test 자체는 API에 연결하지 않음 |
| 마지막 trial의 `run`, `CheckedClient.attitude`, `planned_direction` + sonar climb | import 시 sys.path/표준출력 변경 없이 재사용 가능한 함수로 옮김. 출발/구간 방향·실측고도·제한/로그를 보존하고 None/print 종료를 명시적 결과로 변환 |
| `sdk_api.py:44,141` | 9997 조회는 제한된 진단 worker로 직렬화. 허용 GET과 QUERY_LOCAL_ERROR/응답 prefix·key 검증. 일반 SET/ACTION을 도구에 노출하지 않음 |
| `patrol.py` `_DetectionLogger`, `vision.py` snapshot/FrameRecorder | 원본 로그/사진 기록 유지, capture index와 mission/leg/stop ID 추가. 조회 tool은 detect_latest를 직접 호출하지 않음 |
| 기존 Android FC/영상/query 파일 | APPLY_ORDER의 상태·세대·실제 모터·구독·부분 복구 수정. Speech 전용 음성/시나리오 로직은 Android에 넣지 않음 |

## 5. 일반 순찰 함수 래핑을 피해야 하는 실제 이유

`run_tag_patrol(config, arm_token, *, auto_takeoff, manual_takeoff_confirmed, land_after_hover, resume_wall_id=None)`는 client·video를 직접 소유하며 `obstacle_avoidance_off()`를 호출한다. 한 outbound 방향과 반대 inbound 방향을 쓰고 일부 예외를 print 후 None으로 종료한다. 이를 성공 tool로 포장하면 마지막 시험과 동작/실패 의미가 달라진다.

`PatrolRouteTracker(route_ids)`는2에서 시작하는 중복 없는 목록을 받고 내부에서 역순 귀환을 만든다. 전체 반복 방문 목록(2,3,1,3,2)을 그대로 넣는 API가 아니다. 마지막 trial은 tracker(2,3,1)와 별도 구간 방향 매핑으로 실제2→3→1→3→2를 만들었다. 이 차이를 adapter에서 명시적으로 유지한다.

초기 live 후보 profile은 마지막 trial의 sonar1.4m, 상승8초, 각도/회복 상한1.5°, 구간45초, 총 motion180초, 원래 heading 유지, ID1 통과 구간에서 방문/사진 사건 없음으로 고정한다. 저장 config의 고도1.65m·회복4°·구간90초를 조용히 상속하지 않는다. SDK/기체 회피 설정을 변경하는 호출은 제외하고 raw 센서 기록은 유지한다. PC의70cm 등 장애물 거리 중단 조건도 다시 넣지 않는다.

배터리 조건은 현재 출발15/20%·motion15%·사진 helper30%가 달라 그대로 하나의 최소값처럼 설명할 수 없다. 새 profile은 출발 최소30%로 통일하는 변경을 계획하며 실제 단계별 잔량/중단을 회귀 검증한다. 이는 기존 시험 로그를 바꾸는 것이 아니며 새 profile의 명시적 차이다. 이 밖의 기준을 바꾸면 profile/build revision을 올리고 동일 시험으로 합산하지 않는다.

## 6. 단일 연결·중단·증거

NDJSONClient.send는 동기 sendall/readline, 같은 sequence/reader/telemetry를 사용하며 thread-safe하지 않다. HTTP/tool마다 새9998 연결을 열거나 status/stop을 동시에 그 client로 보내지 않는다. Mission worker 하나가 control·video·detector·logger를 소유하고, 상태 조회는 SnapshotStore를 읽는다. GET tool이 상태 확인을 이유로 비행제어 메시지를 삽입하지 않는다.

stop 요청은 일반 작업 queue 뒤에 들어가지 않고 원자적 latch를 설정한다. 이륙/arm/각 비영점 명령 직전에 latch를 확인하고 취소된 명령을 복구 뒤 재실행하지 않는다. 타이머와 rate wait는 cancel.wait(timeout) 형태로 바꿔 각 loop의 응답성을 검사한다.

기존 느린 명령은20초 read timeout을 사용하므로 Event만 추가해서 즉시 멈춘다고 할 수 없다. service가 stop을 접수하면 owner가 다음 명령을 발행하지 못하게 하고, pending I/O가 있으면 저장된 동일 socket에 대한 `shutdown`으로 read를 깨우는 경로를 검증한다. interrupt는 새 제어 연결이나 두 번째 send를 만들지 않는다. 정상 연결이면 owner가 zero→disarm을 시도하고, 이미 I/O를 끊었으면 cleanup 전송 성공을 주장하지 않고 Android disconnect-release와 별도 RC/FC 조회의 확인 결과를 기록한다. 새 연결/자동 arm/자동 resume를 하지 않는다.

이미 SDK에 제출한 takeoff/action은 PC task 취소로 취소됐다고 보장할 수 없다. 이 경우 phase는 `verification_pending`/`outcome_unknown`이며 실제 상태 확인 전 다음 임무를 허용하지 않는다. 현재 기체 상태가 확인되지 않는데 착륙 성공·stopped를 반환하지 않는다. RC 개입은 자동 제어 종료 원인으로 남기며 앱의 지상 복구와 같은 MaintenanceGate 계약을 사용한다.

stop 접수 응답 목표와 실제 물리 중단 확인 시간을 따로 측정한다. 현재 코드의20초 대기를 제거하는 검증 전 실시간 중단 지연 보장은 하지 않는다. 영구 I/O hang·PC 강제 종료·휴대폰 단절은 각각 실기 수락 사례로 남긴다.

## 7. API·중복·상태 계약

최종 HTTP 위치/메서드는 준비 코드의 transport mapping을 기준으로 한다. 서비스는127.0.0.1에만 bind하고 포트는 설치 시 충돌 확인하는 구성값이다. 인증값은 양쪽 프로세스의 설정으로 전달하며 모델 인자/도구 결과에 넣지 않는다. mutation에는 신뢰된 `caller_id + request_id`를 사용하고 provider call_id는 추적 메타데이터로 보관한다. 같은 실행 의도의 retry/reconnect에는 request_id를 유지해야 하며 새 call_id만으로 새 이륙을 만들지 않는다.

실기 admission은 durable journal/SQLite에 요청의 canonical hash·mission_id·예약 상태를 먼저 commit한 뒤 effect를 실행한다. 같은 key/같은 body는 같은 mission으로 응답, 같은 key/다른 body는 conflict, 다른 key/활성 mission 존재는 busy다. 입구 HTTP와 내부 manager 양쪽에서 schema/profile을 검사한다. 프로세스 재시작 시 이전 nonterminal mission은 interrupted/verification_pending으로 복원하고 절대 자동 재실행하지 않는다. 예제의 메모리 저장 mock은 이 영속성을 구현한 것으로 간주하지 않는다.

쓰기 요청 전송 후 timeout은 `OUTCOME_UNKNOWN`이다. SDK 실패나 접수 거절로 바꿔 새 key로 자동 재시도하지 않는다. 읽기 transport 실패는 `TRANSPORT_ERROR`이고 원시 SDK 오류를 만들어 넣지 않는다. 실제 서비스에는 request_id 기반 상태 조회를 제공해 모호한 접수 결과를 찾는다. 동일 key 재접수도 journal에서 같은 mission을 반환하며 새 physical effect를 만들지 않는다.

임무 상태는 accepted/preflight/running/returning/landing/verifying/completed, 별도 stop_requested/stopped/failed/verification_pending/interrupted를 사용한다. completed는 경로·사진·독립 착륙 증거를 모두 충족한 상태다. stopped는 자동제어 중단/RC 인계가 확인된 상태이며 landed와 별도다. cleanup NO_CONTROL_AUTH, SDK 제출 성공, 도착, 착륙, 실패 단계를 각각 기록한다. terminal로 확정되지 않은 상태에서 단일 실행 잠금을 해제하지 않는다.

에러 분류: validation, stale site/profile, unsupported route, busy, idempotency conflict, not ready, transport, SDK rejection, authority lost, mission timeout, verification unavailable. 준비 코드 응답은 schema_version/ok/status/execution_mode/physical_execution과 임무별 mission·readings·captures 같은 최상위 필드를 포함한다. 오류는 error.code로 구분하며 실기 확장 때 retry_same_request 의미·evidence 참조를 추가한다. `ok=true`인 execute 응답은 접수 성공이지 비행 성공이 아니다.

## 8. 센서·사진·위치 반환

SnapshotStore는 마지막 완성된 ACK를 분리 복사하고 process/connection generation, PC receive sequence/time, telemetry source ages를 보관한다. 조회 시 수신 이후 경과시간을 더해 현재 freshness를 계산한다. 원래 age도 보존한다. OA callback age는 값 변경 callback 기준이며 heartbeat라고 해석하지 않는다. 마지막 값이 같거나60000이라는 이유로 단절을 단정하지 않는다.

항상 raw 수평360배열·상하방mm·interval·SDK 보고 flags·callback age·FC 값/오류/age·요청/ACK를 로그에 남긴다.60000/0/누락은 유효한 거리와 구분하되 원본을 지우지 않는다. 위치는 마지막 확인 tag/현재 leg와 age이며, SDK/AprilTag 근거가 없는 정밀 좌표를 만들지 않는다. GNSS 미사용 경로에서 위경도를 현재 위치처럼 생성하지 않는다.

capture 조회는 mission에 등록된 ID만 받으며 caller가 임의 파일 경로를 지정하지 않는다. 이미지는 session 소유 root 아래의 등록 JPEG만 반환한다. 각 사진은 PC 디코드 프레임 저장인지 기체 촬영인지 source를 표시한다. 기존 _pause_for_tag_photo는 PC 프레임이며 새 기체 사진이라고 이름 붙이지 않는다. 사진 없음/너무 오래됨/통과 태그 관측은 확인된 방문 촬영과 구분한다. 대상 인물/시나리오 의미 판정은 우리 API의 완료 판단이 아니다.

## 9. 적용 순서와 완료 조건

1. 준비용 schema/wrapper/mock 코드를 상대 팀 연동 기준으로 전달한다. 기존 변경 파일과 baseline hash를 보존하고 dashboard repo는 수정하지 않는다.
2. 앱 FC/query/영상/PC 진단 수정과 별도로 순수 mission manager·schema·journal·snapshot·capture index를 구현한다. 이 단계는 hardware adapter 없이 검증 가능하다.
3. 마지막 trial을 reusable profile로 옮겨 resource injection·cancel·결과/event를 붙인다. import만으로 연결/SDK/표준출력 변경이 없게 한다. 기존 generic patrol을 wrapper로 호출하지 않는다.
4. fake transport에서 동시 invoke·중복/다른 payload·stale revision·임무 중 stop·20초 pending read interrupt·ACK 지연/오염·재시작 미재개·사진 경로 제한·unknown 값·원시 센서 보존을 검사한다.
5. 새 APK/PC 조합과 build/profile/site ID를 대조한다. 지상에서 read-only 상태/영상/사진 조회 및 연결 복구를 확인하고 stop의 I/O 중단·RC 확인은 별도 supervised 실기 수락 항목으로 검증한다.
6. 검증된 profile 하나를 live capability에 올린 후 실제 route 접수/완료를 별도 시험한다. 확장 순서는 상대 팀의 요구 입력으로 받되 우리 실행 가능한 edge/profile 검증을 거쳐 추가한다. 모의/실기 결과를 섞지 않는다.

전체회귀: 기존 PC 테스트·새 contract/mission/protocol tests, Android unit/assemble, 원시로그/schema 호환, 새 버전/설치 확인. rollback은 이 변경의 파일/config/등록 profile만 되돌리고 사용자 기존 변경과 앱 데이터·SDK 등록정보를 지우지 않는다.

**현재 완료 범위는 모의 연동 준비 코드와 구체 수정 명세다.** 실제 control adapter·영속 journal·네트워크 서비스·앱 수정 적용·실기 검증은 후속 구현 작업으로 남는다. 시나리오 시간 제한·포스터·대시보드 채점은 우리 작업의 선행 조건이 아니다. 상대 팀에는 물리 목적지·지원 profile·현재 상태·관측 증거만 제공하면 된다.

준비 코드 검증(2026-09-08): `python -B -m unittest discover -s tests -v` **11개 통과**. example.py는 모의 capability·accepted·unknown 상태·stop_requested를 반환했다. socket.create_connection을 차단한 테스트이며 실기 결과가 아니다. 모의 응답과 schema는 같은 검증 파일에서 검사하며 소스의 네트워크/기체 client를 import하지 않는다.
