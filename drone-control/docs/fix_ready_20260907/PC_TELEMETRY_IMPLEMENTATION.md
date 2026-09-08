# PC·프로토콜 오류 수정 실행 명세 — 2026-09-07

이 문서는 분석 담당 에이전트가 작성한 **구현 전 명세**다. 이 담당자는 앱·PC 실행 소스·설정·패키지를 변경하지 않았으며, 장비 접속·비행·SDK 명령·네트워크 실험을 실행하지 않았다. 별도 root 작업의 비집계 비행과 격리 runner 정책 수정은 아래의 구분을 따른다.

## 1. 기준 소스와 실제 장애

| 파일 | 검토 기준 SHA256 |
|---|---|
| [protocol.py](../../pc/drone_nav/protocol.py) | `17D7E46D95B1D5A4654CB3BA3AD669391B9C6326E9206B76E6C62644969D78DF` |
| [runtime.py](../../pc/drone_nav/runtime.py) | `503CAAB24B7AFEE3EBAA3F3FC9E9FAE2639A55664942DDC0DDBA715DF182206F` |
| [patrol.py](../../pc/drone_nav/patrol.py) | `B399D29193FE03072F3598328EA5935994B0EC832C70306F53C6B9D17F8FEDE5` |
| full_patrol_21312_check.py (local reference; not published: `full_patrol_21312_check.py`) — root 정책 수정 전 | `C60C36E251A1AB40B2E185FDA7123CB4FF94980FD709757B6F3F4E1C423570A3` |

근거 문서: 앱 진단 (local reference; not published: `APP_CONNECTION_DIAGNOSIS_20260907.md`), 비집계 2→1→3→1→2 시험 (local reference; not published: `UNCOUNTED_21312_TRIAL_20260907.md`).

- 15:39 시험 `20260907T153944-ee259ff2`는 ID2 촬영 후 ID1 이동 중 ACK sequence203의 좌측 586mm 때문에 **PC 0.7m gate**에서 중단했다. OA 마지막 콜백 age80ms, FC height/velocity/attitude age26ms였다. 이 구간의 FC poll 실패41은 고정됐다. FC 조회 장애나 DJI 제동을 중단 원인으로 기록하지 않는다.
- 지상 OA 콜백 age186→269초만으로 연결 단절을 입증할 수 없다. `ObstacleDataListener.onUpdate`는 변경 알림이다. 주기적 생존 신호를 보장하는 문서가 아니다. [DJI 공식 callback 설명](https://developer.dji.com/api-reference-v5/android-api/Components/IPerceptionManager/IPerceptionManager_ObstacleDataListener.html)
- 최신 root 시험 `20260907T154906-2ab39d4c` 로그에는 전체 경로와 자동 착륙 완료가 있다. 뒤따른 cleanup disarm sequence1013은 `CONTROL_AUTH_HAS_NO_CONTROL_AUTH`이고 같은 ACK는 is_flying=false, armed=false, VS=false, authority=RC다. root는 별도 15:50:56 조회에서 모터 정지까지 확인했다고 보고했다. cleanup 오류와 비행 실패를 섞지 않는다. 이 문서는 해당 tail을 파일에서 재확인했다.

## 2. 최신 시험 정책과 센서 관측을 분리한다

사용자가 “70cm 유지하지 마”라고 한 뒤 **모든 거리 제한 스크립트를 제외하고 전방100cm·후방제한없음으로 정식 Test1을 진행**하도록 범위를 넓혔다고 root가 전달했다. 최신 지시가 우선한다. root는 과거 비집계 runner와 별도의 counted 시험 진입점을 만들어 검증하고 있으며, 이 문서 담당자는 runner를 편집하지 않는다. 기존 차단은 두 호출 경로에 있다.

1. runner `motion_gate()`의 `clearance <= .7`: 좌우 이동 모두 적용된다. `None`/비유한값 검사와 숫자 임계값 검사가 같은 조건문에 들어 있다.
2. `patrol.py::_traverse_to_expected()` 990–999행의 `obstacle_m <= patrol.obstacle_stop_m`: `config.local.json`의 현행 값은0.7이다. runner의 비교만 제거해도 이 공유 함수에서 다시 중단된다.

root가 명시한 적용은 **새 Test1 runner의 motion_gate에서 OA 측방 최소거리·unavailable·callback age 차단 및 상방500mm 차단을 제외**하고, 메모리 config의 `patrol.obstacle_stop_m=0`으로 공유 함수의 중복 수치 차단을 제외하는 것이다. 현행 helper는 양수float 또는None을 반환하므로 `<=0` 조건에 걸리지 않는 방식이다. 앱·공유 runtime·기존 runner·프로덕션 config는 변경 대상이 아니다. 센서 helper monkeypatch나 원시값 덮어쓰기는 하지 않으며 원시 OA는 ACK에 계속 기록한다. 사용자 요구가 이전 비행에 이미 적용됐다고 소급 기록하지 않는다.

거리 차단 제외는 `60000`을 실제60m로 인정하는 결정도, 센서 구독 해제도, DJI 회피 설정 변경도, 펌웨어 근접 제동 해제도 아니다. 기존 고도·카메라·제어권·RC 개입·배터리·명령 상한·시간 제한은 유지한다. 센서 observation은 unknown을그대로표시하면서 비행 차단 정책에서만 제외한다. 새 baseline metadata는 counted=true, test_number=1, 환경front100cm/rearclear를 기록하며 root의 최종 runner 해시·테스트 결과·실제 실행 결과를 별도 확인한다. 원래2회 비집계 기록은 계속counted=false다.

## 3. 구현 순서와 변경 파일

다음은 앱 오류 수정 착수 때 적용할 단위다. PC 개선을 SDK 내부 `REQUEST_HANDLER_NOT_FOUND` 해결이라고 표현하지 않는다.

| 순서 | 파일·함수 | 구체 변경 | 완료 증거 |
|---|---|---|---|
| P1 | `protocol.py::Telemetry`, `from_ack_payload()` | 기존 원본에 있는 is_flying age, bridge/FC 진단, OA 구독 진단을 파싱한다. 아래 additive 계약 사용 | .4 캡처와 새 fixture 모두 파싱, null과 false 구분 |
| P2 | 새 `pc/drone_nav/observation.py` | 범위 파싱·방향별 표본·콜백 경과시간을 표현하는 순수 함수/불변 dataclass를 둔다 | 원본 seq203 재현, sentinel/음수/각도 검증 |
| P3 | `runtime.py::_nearest_obstacle_m`, `_directional_obstacle_m` 및 `patrol.py` 거리 사용부 | P2를 호출해 원시값 해석을 통일한다. 정책 결과와 관측 결과를 분리해 로그에 기록 | 60000→60.0 경로 없음, 두 중단 지점의 정책 일치 |
| P4 | `protocol.py::NDJSONClient.connect/send/close` | 수신시각·연결 세대·건강 상태 delta·파싱 실패를 기록하고 오래된 last_telemetry 재사용 방지 | 재연결/누락 ACK/오래된 콜백 fixture 검증 |
| P5 | `patrol.py::_wait_for_landing`, runner 완료 처리 | 비행 종료·모터 정지·VS 해제 확인 단계를 분리한다 | 경로 완료/착륙 대기/실제 지상 확인을 다른 이벤트로 증명 |
| P6 | 각 격리 runner `trial_metadata`, `CheckedClient.attitude`, 예외/cleanup | 정책 버전·소스 해시·원시 센서 참조·직접 중단 원인 기록. 기존비집계false와새Test1true를구분 | 원본 ACK와 판정 이벤트를 session+sequence로 재구성 |

P1/P2/P4는 통신 가시성과 데이터 정확성이다. P3의 새 해석을 비행 중단에 적용하는 작업은 기존 1초 freshness 및 unknown 정책을 바꾸므로 **별도 커밋·기준 시험**으로 둔다. 새 오류 수정을 현행 비행 중 자동 배포하지 않는다.

## 4. NDJSON v1 호환 스키마

현재 envelope의 6개 필드 및 요청/ACK sequence 규칙은 유지한다. `protocol.py::_validate_payload()`는 telemetry 내부 추가 키를 허용하므로 additive 필드는 `payload.telemetry` 안에 넣는다. envelope나 ACK 최상위에 임의 키를 추가하면 현행 strict parser가 거부한다.

| wire 필드 | PC 표현·자료형 | 의미/기본값 |
|---|---|---|
| `is_flying_age_ms` — .4에도 존재 | `is_flying_age_s: float | None` | 비행 상태 마지막 수신 경과시간, 음수·비유한값은unknown |
| `are_motors_on`, `are_motors_on_age_ms` — Android 추가 필요 | `are_motors_on: bool | None`, `are_motors_on_age_s` | armed는 VS 앱 상태이므로 모터 상태 대용으로 사용하지 않음 |
| `bridge_health` — .4에도 존재 | `BridgeHealth` | 기존 sdk_registered/product_connected/connection_generation/bindings_ready를 보존 |
| `bridge_health.process_start_id` — 추가 | `str | None` | 앱 프로세스 세대 식별자. PID만으로 재사용을 구분하지 않음 |
| `telemetry_generation`, `telemetry_poll_failures`, `telemetry_expired_gets`, `telemetry_last_error_age_ms` — .4에도 존재 | 정수/seconds 및 raw error | 누적값과 이번 구간 증가량을 구분 |
| `fc_health` — Android 추가 | `FcHealth | None` | state, generation, key별 listener_age_ms/get_success_age_ms/consecutive_failures/last_error_code. 소스 시각을 현재 시각으로 덮어쓰지 않음 |
| `oa_diagnostics` — Android 추가 | `OaDiagnostics | None` | generation, listener_state, callback_sequence, last_callback_age_ms, last_value_change_age_ms, source_process_start_id |
| `oa_diagnostics.status_fields` — Android 추가 | 키별 `{reported:bool, value:any, age_ms:number|null}` | oa_type/enabled/sensors_working의 실제 callback 보고와 초기 기본값 구분 |
| 기존 `oa_obstacle_data_age_ms` | 기존 필드 유지 | 새 코드에서는 `last_callback_age`의 legacy alias로 표시. 새 diagnostics가 있으면 같은 의미인지 비교 기록 |

정수 ID/counter는 bool을 거부하고 정수로 보존한다. 숫자 필드는 finite 검증 후 단위 변환한다. 현재 `integer_tuple()`의 원시 거리 구조는 유지하되 잘못된 요소 하나로 배열 전체를 None 처리한 이유를 `parse_issue`에 남긴다. 부분 손상 배열을 정상 측정으로 살려 비행 판정에 사용하지 않는다.

새 Android 필드가 없는 .4 로그/앱은 값이unknown인 채 동작한다. 누락을false/0으로 채워 넣지 않는다. 새 필드 기반 착륙 자동 확정 등은 필요한 capability가 없을 때 `AWAITING_INDEPENDENT_VERIFICATION`으로 반환한다. 기능 부족을 숨기려고 fresh timestamp를 만들어 내지 않는다.

Android 담당과 이 표의 필드명을 확정했다. [FC/Perception 명세 8.1](FC_PERCEPTION_IMPLEMENTATION.md)의 exact wire 계약을 사용한다. `fc_health`와 `oa_diagnostics`는 payload.telemetry 내부 최상위 필드이며 별도 competing alias를 만들지 않는다. 기존 oa_obstacle_data_age_ms만 동일 snapshot의 last_callback_age_ms legacy alias로 유지한다. `fc_health.keys`는 SDK key identifier별 object이고, 모터 상태는 실제 KeyAreMotorsOn LISTEN/GET 값으로만 채운다.

## 5. 거리 관측의 정확한 계약

새 `observation.py`에 `RangeObservation`과 `SectorObservation`을 만들고 `classify_range_mm(raw)` 및 `observe_sector(telemetry, direction, half_width_deg=5)`를 구현한다. 반환값은 다음을 포함한다.

```json
{
  "direction": "left",
  "range_mm": 586,
  "range_status": "FINITE_RANGE",
  "valid_count": 11,
  "selected_count": 11,
  "unknown_count": 0,
  "callback_age_s": 0.08,
  "callback_recency": "RECENT_CHANGE",
  "source_liveness": "UNCONFIRMED",
  "source_generation": null,
  "callback_sequence": null,
  "geometry_status": "REPORTED_INTERVAL",
  "direction_mapping": "body_front0_right90_assumed_not_physically_calibrated"
}
```

예시는 seq203 좌측 구간 해석이며 새 필드는 제안 스키마다. .4는 source generation/callback sequence를 제공하지 않으므로 생성해 넣지 않는다.

- `0 < raw < 60000`만 현재 시험 분석의 유효 수치 범위로 분류한다. `60000`은 `UNRESOLVED_RANGE_CODE`로 보존하고 range_mm/m은null이다. 이것은 제조사의 모든 상황에 대한 sentinel 정의를 입증했다는 뜻이 아니며 **이번 로그에서 실제60m라고 볼 수 없다는 데이터 처리 규칙**이다. 더 큰 수는 `OUTSIDE_ANALYSIS_DOMAIN`, 0/음수는 `NONPOSITIVE`, 누락/빈 배열은 별도 상태다.
- 혼합 섹터는 유효 수치의 최솟값을 제공하되 `PARTIAL_COVERAGE`를 함께 붙인다. 일부60000이 있어도 유효한586mm를 버리지 않는다. 모두unknown이면 clear가 아니라 unknown이다.
- interval이 양수·finite이고 배열 크기와 한 바퀴 구성이 맞는지 확인한다. 기본 fixture360개/1도, 기존 4개/90도는 허용한다. 누락 때 `360/n` 추론은 `INFERRED_INTERVAL`로만 기록하고 측정된 방향 검증을 대체하지 않는다. 모순이면 `INVALID_GEOMETRY`로 보고 방향별 결론을 내리지 않는다. 0도 경계의 ±5도 wraparound를 지원한다.
- callback_age가0–1초이면 `RECENT_CHANGE`, 1초보다 크면 `NO_RECENT_CALLBACK`, 누락/음수/비유한값이면 `UNKNOWN_AGE`다. **NO_RECENT_CALLBACK ≠ SOURCE_DISCONNECTED**. 새 ACK를 받았다고 OA callback age를0으로 리셋하지 않는다.
- 콜백 자체가 매번 모든 각도의 독립 재측정을 뜻하는지는 미확인이다. 현재는 전체 callback age만 있고 **각도별 센서 측정 timestamp는 없다**. 같은 전체 age를 per-sector measurement freshness로 과장하지 않는다.
- enabled/type/working은 별도 상태 관측이다. `horizontal_enabled=false`가 모든 거리0/60000을 뜻하지 않으며, `oa_sensors_working=""`도 원시 유효 거리 제거 근거가 아니다. 기존 공백 문자열 보존은 유지한다.
- 하방 mm와 표시 고도 m는 별개 필드이며 대체/평균하지 않는다. 이 센서 스키마에는 사람·의자·벽 분류나 객체ID가 없다.

기존 `_directional_obstacle_m()`의 `float | None` 결과만으로 unknown 이유가 사라지므로 새로운 호출부는 전체 Observation을 사용한다. 호환 wrapper는 실제finite 범위만 float로 반환하고, 기존 1초 제한을 유지하는 wrapper임을 명시한다. 관측 함수에는 중단 정책을 넣지 않는다.

## 6. 연결·시계·오류 상태

`NDJSONClient.connect()`는 이미 Protocol과 last_telemetry/motion streak를 초기화한다. 이 부분을 유지하고 연결 전 기존 소켓이 열려 있으면 명시적으로 종료하게 한다. 매 새 연결에 `pc_connection_epoch`를 증가시키고 로그 이벤트에 넣는다. sequence는 연결 안에서만 비교한다.

`send()`는 원본 ACK를 우선 보존한 뒤 decode/type/sequence 검증을 수행한다. decode 실패 때 `protocol_error`에 expected_sequence, actual_sequence, connection_epoch와 오류를 기록한다. 검증 실패 또는 ACK telemetry 누락이면 이전 `last_telemetry`를 최신 응답처럼 재사용하지 않고 None으로 무효화한다. `last_telemetry_received_pc_monotonic_ns`를 추가하고 호출부가 보관된 객체를 장시간 사용하는 경우의 수신 age를 함께 검사한다.

시간은 세 종류를 그대로 구분한다.

1. PC envelope timestamp_ns / log pc_wall_time_ns: PC UTC epoch ns.
2. ACK timestamp_ns 및 Android age 계산: Android 단조 시계. PC epoch에서 빼서 지연을 계산하지 않는다.
3. RTT와 PC 내부 deadline: PC monotonic 두 시점의 차이. 기존 rtt_ms 방식 유지.

소스 age + ACK 수신 후 PC 경과시간은 보수적인 캐시 보유시간 지표로 쓸 수 있지만 두 기기의 시계 자체를 직접 뺄 수 없다. 정확한 센서 생성 시각/편도 지연이라고 표시하지 않는다. 프로세스나 telemetry_generation이 바뀌면 이전 counter baseline과 health streak를 새 구간으로 시작한다.

FC 오류 delta는 같은 process_start_id/telemetry_generation에서만 계산한다. 41→41은새오류0, 344→41은리셋/새세대 가능성이지음수오류량이 아니다. 최근 정상 GET 및 증가하는 연속 오류를 현재 건강 판단에 사용한다. 과거 마지막 오류 문자열이 존재한다는 이유만으로 현재FC고장 표시를 하지 않는다. 기존 .4에 충분한 정보가 없으면 `EVIDENCE_INCOMPLETE`를 남긴다.

9997 query의 과거 연결 callback이 새 연결로 보내질 수 있는 문제는 Android `KeyItem`/`CommandServer`의 connectionEpoch 송신 경로에서 해결해야 한다. [QUERY_TRANSPORT_IMPLEMENTATION.md](QUERY_TRANSPORT_IMPLEMENTATION.md)에 함수별 변경·예산·구독 소유권·검증을 구체화했다. PC9998 sequence검사를 느슨하게 하는 방법으로 고치지 않는다. query client는 요청module/key를 응답과 비교하고 순차처리하며, text protocol에 없는 request_id를 있다고 가정하지 않는다. 새 QUERY_LOCAL_ERROR 접두어는 SDK 값으로 파싱하지 않고 명시적 로컬 실패로 처리한다.

현재 `_check_control()`은 RC/authority를 검사하고 runner는 높이age를 검사하지만, 모든 이동 경로가 매번 속도/자세 freshness를 확인하는 것은 아니다. 따라서 “현재 모든 FC gate가 완비됐다”고 문서화하지 않는다. 향후 공통 FC motion validator는 height/velocity/attitude/is_flying의 finite·nonnegative age≤0.5초를 요구하고, timeout 복구를 이유로 이 기준을 완화하지 않는다. 이는 출발 전 fresh 기준 검증을 거친 별도 동작 변경이다.

## 7. 성공·중단·cleanup 판정과 로그

`_wait_for_landing()`은 현재 is_flying=false 한 항목만 보고 return한다. 이를 최종 성공으로 쓰는 호출부를 다음 단계로 바꾼다.

- `route_completed`: 지정 순서와 tag_pause_complete/사진 증거가 모두 있음.
- `landing_command_accepted`: 요청 수락. 착륙 완료와 다름.
- `ground_reported`: 최신 is_flying=false 확인.
- `ground_verified`: 최신 IsFlying=false + AreMotorsOn=false, VS=false/armed=false/authority=RC. 각 증거의 요청ID/수신시각/age/세대를 저장.
- `trial_completed`: 경로·자동착륙·ground_verified를 합친 결과. 수동착륙이면 별도 outcome으로 보존하며 자동 완주로 계산하지 않음. counted는시험요청별metadata를사용하며기존2회비집계와새정식Test1을구분.

새 모터 필드 미지원 시 자동 재arm/비행 재개 없이 독립9997 조회 결과가 들어오기 전까지 awaiting verification이다. 착륙 후 cleanup disarm이 NO_CONTROL_AUTH를 반환하면 raw error를 남기고, 이미 독립 지상/RC 확인이 있는 경우만 `cleanup_already_released`로 분류한다. 그 오류를 무조건 성공 처리하거나 비행 중 authority 상실까지 억제하지 않는다.

각 motion 판정 로그는 session_id, pc_connection_epoch, request/ACK sequence, phase, requested direction, 원시 OA callback age, sector observation, FC ages, policy_id/threshold/decision/reason을 기록한다. 낮은 거리 관측은 최초 진입·상태 변화 및 최대1Hz 요약으로 남기고 원본 ACK는 계속 보존한다. 원시 거리 한 값 때문에 중단했으면 바로 그 ACK를 링크하며 중단 후 더 낮아진 값으로 원인을 바꾸지 않는다.

`trial_metadata`에는 counted, requested_route, physical_layout_confirmation_source, runner/runtime/protocol/patrol SHA256, 앱 build_id, config의 비밀값 제외한 효과적 설정, 소프트웨어 거리 정책, 시작 배터리와 각 단계 최소 배터리를 기록한다. 현행 출발15/20 및 runtime15와 달리 `_pause_for_tag_photo()`는30% 미만에서 중단한다. 공통 배터리 정책 정리는 별도 변경이고 이 문서에서 임의로 낮추지 않는다. confirmation_token은 기존 redact를 유지한다.

## 8. 구현 후 회귀 검증 — 아직 실행하지 않음

새 테스트는 순수 데이터와 mock socket/callback을 사용하며 기체에 접속하지 않는다. 최소 묶음은 다음과 같다.

| 테스트 파일 | fixture·상황 | 필수 assertion |
|---|---|---|
| `pc/tests/test_telemetry_observation.py` 신규 | 원본15:39 seq203 | left586mm, down1337mm, rearunknown, callback0.08s, FC0.026s. 60000→60m 없음 |
| 같은 파일 | 전60000, 빈배열,0/음수,malformed,bool,NaN/inf, mixedfinite | 상태 구별 및 raw보존, mixed는586mm 보존, missing≠clear |
| 같은 파일 | callback269초+FC정상, callback -1ms | NO_RECENT_CALLBACK와UNKNOWN_AGE이며 SOURCE_DISCONNECTED 자동판정 없음 |
| 같은 파일 | 360/1도,4/90도,wraparound,잘못된interval | 섹터 인덱스와구조오류 분류, 추론표시 |
| `pc/tests/test_core.py` 확장 | .4 oldpayload와새진단payload | backward compatibility, missingfalse혼동없음, is_flying_age 파싱,모터unknown |
| `pc/tests/test_regressions_20260906.py` 확장 | reconnect,늦은ACK,telemetry누락,세대counter리셋 | last telemetry/streak reset,wrongseqreject,낡은healthdelta재사용없음 |
| `pc/tests/test_landing_evidence.py` 신규 | is_flyingfalse만,모터true,staleground,독립정상조회,cleanupNOAUTH | 단일false는최종PASS불가,모든fresh증거후완료,cleanup오류원문보존 |
| 각 격리 runner 테스트 | 이전586mm 사건과새거리정책 | 과거비집계동작보존; 새Test1에서좌우숫자·unavailable·OAage·상방거리차단제외,원시기록유지,공유patrol두번째gate제외; height/video/RC/authority조건독립유지 |

실행 명령은 구현 후 `drone-control\.venv\Scripts\python.exe -m unittest discover -s drone-control\pc\tests`를 PC 디렉터리에서 실행한다. 격리 runner 테스트는 그 디렉터리에서 unittest로 별도 실행한다. 실제 원본 로그 전체를 테스트 디렉터리에 무분별하게 복사하지 말고 필요한 ACK만 비밀값 없는 fixture로 추출하고 원본 session/sequence/SHA를 metadata에 남긴다.

mock 단위 테스트 통과가 SDK 구독 복구나 센서 실제 측정, 폰 검은 화면 해결의 증거는 아니다. 실기 확인은 별도 지상 단계에서 같은 세대의 FC GET 성공, OA 환경의 실제 거리 변화에 따른 callback 반응, 구독 중복 없음, 영상raw/decode/Surface 단계를 확인한다. 신규 코드가 만든 timestamp나 callback count만 증가하는 것으로 복구 성공을 선언하지 않는다.

## 9. 완료 범위와 미확정 항목

이 문서는 변경 위치·필드·자료형·이벤트·회귀 fixture·검증 기준까지 정했다. Android wire 진단 명칭도 공통 계약으로 확정했으므로 추가 구조 결정 없이 코드 작업에 착수할 수 있다. 60000의 제조사 원시 의미 전체, OA callback의 측정 갱신 보장, 물리 방향교정, SDK FC 내부 오류의 최초 원인, 구독 재등록의 실제 회복 효과는 하드웨어 증거가 없는 미확정 항목으로 남는다.

이번 문서 작성만으로 앱 오류가 고쳐졌다고 하지 않는다. root의새Test1거리정책변경은이통신수정묶음과별도이며, 실제실행결과와증거는root시험기록에서평가한다.

## 10. Test8 종료 후 접속 시간초과 — 2026-09-08 반영

원본 후속 조회 (local reference; not published: `20260907T083205_counted_test8_postflight_query.json`)의 2026-09-07 17:31:58~17:32:05 KST 구간에는 IsFlying·AreMotorsOn·UltrasonicHeight·Battery GET 및 STATUS의 transport timeout이 기록됐다. SDK의 REQUEST_HANDLER_NOT_FOUND 응답을 수신한 Test4·5와 구분한다. 네트워크·핫스팟·앱 종료 여부를 독립 관측하지 않았으므로 원인은 미확정이다.

`transportTimeoutAfterMissionComplete`를 계획된 회귀 사례에 추가한다. 요청한 작업과 transport 실패 단계를 기록하고 SDK 오류를 만들어 넣지 않는다. 이전 정상 telemetry는 마지막 관측값으로 보존하되 현재 상태로 재사용하지 않는다. 경로 완료·자동 착륙 보고는 유지하면서 독립 모터 정지/RC 확인 결과는 `AWAITING_INDEPENDENT_VERIFICATION`으로 남긴다. 사용자 시험 종료 보고와 기계적 검증 상태를 별도 기록하며 이 사례로 자동 재arm·이륙·재개를 유발하지 않는다. Test8 비행 중 FC 누적 오류는41로 일정했으며, 후속 TCP 실패를 비행 중 신규 FC 오류로 합산하지 않는다.
