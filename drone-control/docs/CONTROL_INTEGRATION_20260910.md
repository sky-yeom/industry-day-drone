# 대시보드 × 드론 연동 가이드 — 고정 Tools v1 / Test·Real

> **팀원용 결론:** 이 PR이 main에 병합되고 기존 자동 배포가 끝나면,
> **기존 대시보드 주소에서 mock으로 계속 개발·테스트**합니다.
> 별도 mock 서버 실행, Docker 실행, Node/Python 설치, 드론 연결이 필요하지 않습니다.
> PR 브랜치에 push한 것과 main 배포 완료는 다릅니다.

## 통합 구성과 구현 범위

| 구성 | 역할 | 이번 변경 |
|---|---|---|
| 기존 대시보드 | 음성 입력·선택 순서·지도 애니메이션·이미지·결과 표시 | 9월 12일 main `a828ee0` 그대로 유지 |
| 기존 Voice | 대화·확인·출발 도구 호출 | 팀원의 prompt·도구·shimmer/openai 기본값 유지 |
| 기존 relay | `/api/config`, `/ws`, 요청 검증·실행·상태·캡처 전달 | 동일 실행기에서 Test/Real 대상 선택 |
| 내장 mock | 7개 고정 Tools의 모의 응답 | relay 프로세스 안에서 처리, 새 프로세스·포트 없음 |
| 실제 PC Tools | 같은 계약으로 실제 드론 제어 | 기존 HTTP/원격 연결·인증·안전 조건 유지 |
| 독립 Node mock | 백엔드 없이 별도 프런트 adapter를 개발할 때 사용 | 선택 사항, 배포 대시보드 사용에 불필요 |

새 Docker 구성·Node 사이드카·별도 mock 배포는 추가하지 않습니다.
기존 relay 배포에 이미 포함되는 Python 코드와 Tools 인자 schema만 사용합니다.

```text
기존 대시보드 / Voice
  │  GET /api/config + WebSocket /ws (변경 없음)
  ▼
기존 relay → LiveMissionRunner → DroneClient
                                 │ 같은 tool 이름·인자·응답 계약
                                 ├─ Test: 내장 contract mock (함수 호출)
                                 │        → 모의 방문·PNG·결과
                                 └─ Real: 실제 PC Tools (HTTP / 원격 WSS)
                                          → Android → DJI 기체
```

Test는 화면에 결과만 덮어씌우거나 기존 타이머 실행기로 우회하지 않습니다.
실기와 같은 `DroneClient` 인자 검증·요청 ID 처리·`LiveMissionRunner`를 통과한 뒤
**통신 대상만 내장 mock으로 교체**합니다. 실제 TCP/HTTP의 지연·유실까지
시험하려면 아래 선택 사항인 독립 HTTP mock을 사용합니다.

## A. 팀원: 기존 웹에서 바로 테스트

1. 기존 PR #3이 main에 병합되고, 저장소의 기존 자동 배포가 성공했는지 확인합니다.
2. 기존 대시보드 주소를 새로 엽니다. 별도 mock 주소를 입력하지 않습니다.
3. 기존 음성 절차로 조건을 확인하고 현장 2 → 3 → 1을 확정한 뒤 출발합니다.
4. 순서·방문 상태·모의 이미지·결과가 대시보드에 전달되는지 확인합니다.

음성은 Test에서도 **기존 Azure Voice**를 사용합니다. 마이크 권한과 기존 배포의
Voice 리소스 접근은 필요합니다. **기본 Test는 드론만 모의 처리하고 VLM은 실제 Azure를 호출**합니다.
`public/monitors`의 해당 원본 이미지를 촬영 대용으로 전달하며, 분석 시간도 시나리오 시한에 반영합니다.
자동화에서 외부 호출 없이 계약만 검사하려면 `TRIAGE_MODE=mock`을 명시합니다.
`/ws?voice=0`은 자동화된 프로토콜 시험용이며, 기존 UI의 음성 사용법을 바꾸지 않습니다.

relay의 `/api/config`에서 다음 값을 확인할 수 있습니다.

```json
{
  "runMode": "test",
  "mode": "azure",
  "droneControlMode": "mock",
  "droneControlUseTools": true,
  "droneControlTransport": "inprocess",
  "toolEndpoint": "inprocess://drone-tools/v1",
  "droneReady": true
}
```

`inprocess://...`는 브라우저가 접속할 URL이 아니라 **내장 모듈 식별자**입니다.
모의 상태는 대시보드 세션별로 분리되므로 팀원끼리 같은 드론 점유를 경쟁하지 않습니다.
세션 종료/서버 재시작 시 초기화됩니다. 실제 PC 한 대의 전역 점유·영속성은 별개입니다.

기존 배포처럼 `RELAY_HOST=0.0.0.0`이고 `DRONE_RUN_MODE`가 없으면 Test를 기본으로
선택합니다. 단, 명시적인 기존 `DRONE_CONTROL_MODE=live`는 자동으로 바꾸지 않습니다.
기존 mock용 remote 설정은 이 Test 선택에서 덮어쓰며 실제 PC 토큰을 사용하지 않습니다.
이미지 분석은 `TRIAGE_MODE=azure|mock`으로 별도 선택하며 미지정 시 Azure입니다.
로컬의 모드 미지정 실행은 기존 설정 방식을 유지합니다.

## B. 개발 PC에서 같은 화면으로 Test ↔ Real

이 절차는 **로컬에서 앱 자체를 실행할 개발자용**입니다. 배포된 웹을 쓰는 팀원에게
설치를 요구하는 절차가 아닙니다. 기존 앱의 frontend/relay 실행 환경을 사용합니다.

```powershell
# Test: 기존 UI + relay만 시작. mock용 Node 서버/PC 서비스는 시작하지 않음.
.\scripts\start-integrated.ps1 -Mode Test -AnalysisMode Azure

# 외부 AI 호출 없이 계약만 검사할 때 명시적으로 선택
.\scripts\start-integrated.ps1 -Mode Test -AnalysisMode Mock

# 기존 세션과 실행을 종료한 다음 Real로 새로 시작.
# 실제 PC 설정은 저장소 밖의 기존 개인 설정 파일을 사용.
.\scripts\start-integrated.ps1 -Mode Real -EnvFile "$env:LOCALAPPDATA\IndustryDayDrone\config\field-live.env"

# 프로세스/비행을 시작하지 않고 설정만 확인
.\scripts\start-integrated.ps1 -Mode Test -NoWeb -CheckOnly
.\scripts\start-integrated.ps1 -Mode Real -NoWeb -CheckOnly -EnvFile "C:\private\field-live.env"
```

로컬 기본 주소는 UI `http://127.0.0.1:3000`, relay `http://127.0.0.1:8080`입니다.
다른 포트를 쓰면 `-RelayPort`, `-WebPort`를 지정하고 UI의 기존
`NEXT_PUBLIC_RELAY_HTTP`/`NEXT_PUBLIC_RELAY_WS` 빌드 설정도 같은 relay로 맞춥니다.
`-NoWeb`은 relay만 실행합니다. 기존 가상환경 경로는 `-RelayPython`/
`-ControlPython`으로 지정할 수 있습니다.

| 선택 | Tools 대상 | 이미지 분석 | 필요한 실제 준비 |
|---|---|---|---|
| Test 기본 | 내장 mock, 추가 포트 없음 | 원본 현장 PNG를 실제 Azure VLM으로 분석 | Azure VLM·Voice 설정, 드론·PC 토큰 불필요 |
| Test + `TRIAGE_MODE=mock` | 내장 mock, 추가 포트 없음 | 생성된 MOCK PNG의 결정적 결과 | 계약 시험용, VLM 미호출 |
| Test + `DRONE_TEST_API_URL` + `TRIAGE_MODE=mock` | 명시한 loopback 독립 HTTP mock | 생성된 MOCK PNG의 모의 분석 | 선택적 Node mock 개발 환경 |
| 로컬 Real | `127.0.0.1:8766` PC Tools | 실제 Azure 이미지 분석 | 실제 nav/site·PC 토큰·APK·연결·지상 조건 |
| 배포 Real | 기존 원격 PC 커넥터 | 실제 Azure 이미지 분석 | 장치 인증·운영자 인증·커넥터·단일 replica |

모드 전환은 **실행 중인 비행의 hot switch가 아닙니다.** 기존 세션을 종료하고
relay를 새 설정으로 시작합니다. 실행 중인 포트를 강제로 종료하거나, 실패 시
Real을 mock으로 바꾸거나, 이전 출발 명령을 자동 재전송하지 않습니다.

## C. 배포 Real 전환: 사전 연결이 완료된 경우에만

Test용 기본 배포는 실제 PC 없이 동작합니다. 반대로 클라우드의
`127.0.0.1`은 사용자의 PC가 아니므로, **모드 하나만 바꾼다고 미연결 PC가 연결되지는 않습니다.**

기존 원격 경로를 준비한 후 `DRONE_RUN_MODE=real`로 전환합니다.
공개 바인딩에서는 Real 기본 transport가 `remote`, 로컬에서는 `local`이며,
명시적 선택은 `DRONE_REAL_TRANSPORT=remote|local`입니다.

원격 Real은 기존 `DRONE_REMOTE_DEVICE_ID`, `DRONE_REMOTE_DEVICE_TOKEN`,
`RELAY_OPERATOR_TOKEN` 및 `DRONE_REMOTE_SINGLE_REPLICA=1`이 필요합니다.
**실제 배포도 min/max replica를 1/1로 구성**해야 하며 환경변수만으로 인프라가
변경되지는 않습니다. 커넥터의 live opt-in·실제 PC live 설정·Azure 분석도 준비되어야 합니다.
장치가 없거나 모드/인증이 다르면 명시적으로 실패합니다.
이번 PR 준비 과정에서 실기 활성화나 배포 변경을 실행하지 않습니다.

실제 토큰·휴대폰 arm token·개인 nav/site는 저장소나 브라우저 설정에 넣지 않습니다.
이 문서는 별도 암호화 도구나 추가 로컬 로그인 절차를 요구하지 않습니다.

## D. 프런트/Voice 개발 시 유지할 상위 계약

| 경계 | 유지할 메시지/동작 |
|---|---|
| 초기 설정 | `GET /api/config` (추가 진단 필드는 무시해도 됨) |
| 기존 대화 도구 | `confirm_prompt`, `select_stop`, `confirm_route`, `clear_route`, `launch_mission`, `retry_mission`, `abort_mission`, `get_state` |
| 지도 전환 | `confirm_prompt` 이후 기존 애니메이션 완료 때 `route_intro.ready` 전송 |
| 화면 갱신 | 기존 `route.state`, `tool.finished` 및 캡처/결과 필드 |
| 우리 드론 경계 | 아래 7개 `drone_*` Tools — 상위 대화 도구와 혼동하지 않음 |

9월 12일 main의 지도·드론 탑승 애니메이션과 Voice 대화/확인 문구는 수정하지 않습니다.
`route_intro.ready` 이전에는 기존처럼 다음 질문을 보류합니다.
모의 이미지에는 MOCK 표시와 캡처 식별자가 포함됩니다. 이미지 분석의 결과는
시나리오 기반 모의 값이며 실제 인식 정확도의 증거가 아닙니다.

## 목적

별도 adapter를 개발하는 프런트 팀은 **우리 백엔드가 실행되지 않아도**, 아래 고정 계약을 기준으로
화면·상태 관리·도구 호출을 개발할 수 있습니다. 우리 실제 백엔드가 이 계약에
맞춰야 하며, 프런트가 우리 구현의 변경을 따라가도록 만드는 구조가 아닙니다.

- 계약·mock: [`contracts/drone-tools/v1`](../../contracts/drone-tools/v1)
- 계약 버전: **1.0.0**, wire `schema_version: 1`
- 아래 독립 모듈의 실행 의존성: **Node.js 20 이상만**. `npm install` 불필요
- 불필요한 것: Python, relay, `drone_nav`, Azure, DJI SDK, 드론, APK, 실제 토큰/설정, ZIP
- 해당 폴더만 checkout/copy해도 실행됩니다. 상위 저장소 코드·이미지를 import하지 않습니다.
- 기존 프런트엔드와 Voice 대화·확인 문구는 변경하지 않습니다.

## 1. 선택 사항: 독립 HTTP 계약 mock 실행

**위 A의 기존 대시보드 테스트에는 이 절차가 필요하지 않습니다.**
우리 relay와 독립적으로 HTTP adapter를 개발하거나 유실/지연 시나리오를 시험할 때만 사용합니다.

계약이 포함된 승인된 브랜치/커밋을 checkout한 뒤:

```powershell
Set-Location .\contracts\drone-tools\v1
node mock.mjs
```

기본 주소: **`http://127.0.0.1:18767`**.
실기 8766과 기존 relay 8080을 사용하지 않습니다.

```powershell
# 계약 잠금 확인
node verify.mjs

# 모듈 자체 테스트
node --test

# 다른 모의 시나리오
node mock.mjs --scenario manual-landing
node mock.mjs --scenario lost-ack
```

실제 토큰이나 로그인 없이 localhost에서 개발할 수 있습니다.
`--token`은 인증 오류를 시험하고 싶을 때만 사용하는 **mock 전용** 옵션입니다.
실기 자격 증명을 입력하지 않습니다.

이 문서의 코드가 아직 포함되지 않은 과거 브랜치를 clone하면 실행할 수 없습니다.
GitHub 반영 전에는 완료되었다고 간주하지 말고, 합의된 branch/commit에
`contracts/drone-tools/v1/package.json`이 있는지 확인합니다.

## 2. 고정되는 파일과 변경 규칙

| 파일 | 프런트 팀이 의존할 계약 |
|---|---|
| `tools.json` | 7개 tool 이름·필수 인자·타입·추가 인자 금지 |
| `contract.schema.json` | 요청 envelope·성공/오류·임무·방문·캡처 응답 JSON Schema |
| `types.d.ts` | TypeScript request/response, 상태·ID 타입 |
| `contract.lock.json` | 위 계약 파일의 SHA-256, 계약 버전 |
| `validate.mjs` | 이 계약에 대한 독립 검증 함수 |
| `mock.mjs` | 계약을 구현하는 Node 모의 서버 |
| `verify.mjs` | 고정된 계약 파일이 바뀌지 않았는지 검사 |

**v1 계약 파일을 조용히 수정하지 않습니다.** 필드명·필수 여부·타입·상태 의미를
바꿔야 하면 `v2` 등 새 버전에서 합의하고 기존 v1을 유지합니다.
mock 구현의 오류는 API 계약을 바꾸지 않는 범위에서 수정할 수 있습니다.

요청은 추가 키를 거절합니다. 응답은 필수 필드가 고정되어 있고 추가 진단 필드가
있을 수 있으므로 프런트는 모르는 응답 필드를 무시할 수 있어야 합니다.
명세에 없는 진단 필드를 필수 UI 의존성으로 만들지 않습니다.

우리 저장소의 `relay/test_contract_mock.py`는 내장 mock의 응답을 고정된 v1 schema로
검사합니다. 이 검사는 **우리 백엔드의 의무**이며,
독립 mock 실행에 Python이 필요하다는 뜻이 아닙니다.

## 3. 프런트에서 시작하는 최소 예제

```typescript
import type {
  ToolName, ToolArguments, Response
} from "./contracts/drone-tools/v1/types";

const base = "http://127.0.0.1:18767";
const callerId = "frontend-development";

async function callTool<T extends ToolName>(
  name: T,
  args: ToolArguments[T],
  requestId = crypto.randomUUID(),
): Promise<Response<T>> {
  const response = await fetch(`${base}/tools/${name}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Drone-Expected-Mode": "mock",
    },
    body: JSON.stringify({
      arguments: args,
      caller_id: callerId,
      request_id: requestId,
    }),
  });
  return response.json();
}
```

TypeScript 파일의 상대 경로는 팀원의 폴더 구조에 맞춥니다.
첫 호출은 capabilities이며 profile/site를 하드코딩하지 않습니다.

```typescript
const caps = await callTool("drone_get_capabilities", {});
if (!caps.ok) throw new Error(caps.error.message);

const requestId = crypto.randomUUID(); // 동일 출발 의도에 하나만 생성
const accepted = await callTool("drone_execute_route", {
  profile_id: caps.profile_id,
  site_revision: caps.site_revision,
  destination_ids: ["tag-2", "tag-3", "tag-1"],
}, requestId);
if (!accepted.ok) throw new Error(accepted.error.message);

const missionId = accepted.mission.mission_id;
// 같은 caller_id로 상태를 주기적으로 조회하며 임무 lease를 갱신합니다.
const state = await callTool("drone_get_mission", { mission_id: missionId });
const images = await callTool("drone_get_captures", { mission_id: missionId });
```

이 코드는 **mock용 개발 예제**입니다. 실제 PC Tools는 브라우저 Origin을
거절하고 backend bearer를 요구하므로 실기에서는 relay/backend adapter가
같은 계약을 호출해야 합니다. 실제 토큰을 브라우저에 넣거나 raw 기체 포트로
직접 연결하는 구조로 바꾸지 않습니다.

## 4. 프런트와 실제 백엔드 사이의 관계

```text
프런트 개발:
팀원 화면/adapter → 고정 v1 HTTP 계약 → 독립 Node mock
                                      (우리 서비스 없어도 됨)

실제 운용:
기존 프런트/Voice → relay/backend adapter → 동일 v1 Tools 계약 → PC Tools → Android → 기체
```

고정하는 경계는 **우리 7개 `drone_*` 도구의 HTTP 요청/응답**입니다.
독립 Node mock은 Voice 대화, 시나리오 점수, Azure 분석, DJI 비행 물리를 구현하지 않습니다.
통합 Test에서는 기존 relay가 Voice와 시나리오 처리를 담당하고 이미지 분석만 모의 처리합니다.
기존 Voice의 `confirm_prompt`, `select_stop`, `confirm_route`, `launch_mission`은
별도 상위 계약이며 `drone_*` 이름으로 교체하지 않습니다.

모니터 매핑은 다음과 같습니다.

| 프런트 ID | 우리 목적지 ID | 현장 태그 |
|---|---|---|
| `monitor-1` | `tag-1` | ID1 |
| `monitor-2` | `tag-2` | ID2 |
| `monitor-3` | `tag-3` | ID3 |
| 목적지 아님 | Home | ID6 |
| 목적지 아님 | 바닥 | ID0 |

프런트 선택 `2→3→1`은 `["tag-2","tag-3","tag-1"]`입니다.
우리 실제 경로는 **`6→2→3→1→6`**으로 확장됩니다. mock도 같은 순서 정보를
보여줍니다. Home/바닥을 `destination_ids`에 넣지 않습니다.
카드의 방문 순서 배지·배치 좌표는 태그 번호·물리 좌표와 별개입니다.

현재 현장 계약은 목적지 1/2/3 각각 한 번, 총 3개이며 6가지 순서를 지원합니다.
`tools.json`의 일반 배열 상한 32는 현재 목적지를 32개로 늘릴 수 있다는 뜻이 아닙니다.

## 5. HTTP 요청 계약

모든 tool 호출:

```http
POST /tools/{tool_name}
Content-Type: application/json
X-Drone-Expected-Mode: mock
```

```json
{
  "arguments": {},
  "caller_id": "frontend-development",
  "request_id": "one-command-id"
}
```

| tool | `arguments`의 정확한 키 | 응답 데이터 |
|---|---|---|
| `drone_get_capabilities` | 없음 | 설정·매핑·허용 순서 |
| `drone_get_status` | 없음 | 연결·지상·현재 임무 |
| `drone_execute_route` | `profile_id`, `site_revision`, `destination_ids` | `mission` |
| `drone_get_mission` | `mission_id` | `mission` |
| `drone_stop_mission` | `mission_id` | `mission`, `stop_requested`, `physical_stop_confirmed` |
| `drone_get_sensor_snapshot` | 없음 | `snapshot`, `sensor_semantics` |
| `drone_get_captures` | `mission_id` | `mission_id`, `captures` |

profile/site/mission/destination 인자는 1~64자 `[A-Za-z0-9_-]`입니다.
caller/request ID는 1~128자이며 첫 글자는 영문/숫자, 나머지는 `_.:-`도 허용합니다.
mode header `live`로 mock을 호출하면 dispatch 전에 `MODE_MISMATCH`입니다.

일반 성공:

```json
{
  "schema_version": 1,
  "ok": true,
  "status": "ok",
  "execution_mode": "mock",
  "physical_execution": false
}
```

일반 오류:

```json
{
  "schema_version": 1,
  "ok": false,
  "execution_mode": "mock",
  "physical_execution": false,
  "error": {"code": "MISSION_BUSY", "message": "A mission is active"}
}
```

HTTP 상태와 `ok`를 모두 확인합니다. 400은 잘못된 인자/상태, 401은 선택적 mock
인증 실패, 403은 허용되지 않은 origin/소유권 경계, 404는 없는 대상,
409는 모드·중복 의도·임무 점유 충돌 등에 사용합니다.
실제 오류 코드는 처리 기준이며 자연어 `message` 문자열에 로직을 묶지 않습니다.

## 6. 임무·방문·캡처

임무 상태:

```text
accepted → preflight → taking_off → running → returning
                                             ↓
                                    awaiting_rc_landing → completed

실패/중단: failed, stop_requested, stopped, outcome_unknown
```

방문 상태: `pending → moving → arrived → captured`.
`arrival_confirmed:true`와 `capture_ids`를 함께 사용합니다.
HTTP 접수 성공과 실제 도착·완료는 다릅니다.

캡처 한 장의 필수 필드:
`capture_id`, `mission_id`, `visit_index`, `destination_id`,
`arrival_confirmed:true`, `content_type:"image/png"`, `image_base64`,
`sha256`, `captured_at_unix_ms`.

목적지마다 서로 다른 PNG 두 장, 총 최대 6장입니다.
`visit_index`는 0부터 시작하므로 순서 2→3→1에서 index0은 tag2입니다.
표시할 때 `data:image/png;base64,`를 `image_base64` 앞에 붙입니다.
SHA-256은 base64 문자열이 아닌 PNG 바이트 기준입니다.

독립 Node mock과 `TRIAGE_MODE=mock`의 PNG는 생성한 가짜 이미지입니다.
내장 Test + Azure는 `public/monitors` 원본을 사용하며 두 번째 프레임은 같은
고정 이미지의 반복임을 표시합니다. 이는 실제 두 번의 촬영이나 기체 구도 보정 증거가 아닙니다.
`simulated:true`, `physical_execution:false`,
`physical_stop_confirmed:false`를 유지합니다.
real-mode에서도 `physical_execution:true`는 실제 모드라는 뜻이지 조회 요청이
비행을 실행했다는 뜻이 아닙니다.

`completed`, 시나리오 점수 완료, Voice 응답 종료를 혼동하지 않습니다.
실제 현장 비행은 Home6 복귀 뒤 RC 수동 착륙과 신선한 지상 증거가 필요합니다.
이 mock은 그 상태 전이만 재현하고 실제 착륙 성공을 증명하지 않습니다.

### VLM 프롬프트와 구조 대상 판정

Azure VLM은 촬영 이미지 전체에서 참가자의 검색 조건에 맞는 후보를 찾고,
같은 후보에게 구조가 필요한 시각적 근거가 있는지 별도로 판단합니다.
현재 방문의 `monitorId`, 현장명, 신고 내용은 임무 실행기가 함께 전달합니다.
신고는 맥락이며 이미지에서 확인한 사실이나 의학적 부상 판정을 대신하지 않습니다.

모델 내부 출력은 `matchesPrompt`, `matchesTarget`, `needsRescue`, `description`,
`box` 다섯 필드입니다. 사용자 조건·대상 외형·구조 필요 근거가 모두 참일 때만
기존 `targetPresent`로 변환합니다. `needsRescue:false`는 안전하다는 뜻이 아니라
이미지에서 구조 필요 근거를 확인하지 못했다는 뜻입니다.

좌표는 생성하지 않으며 `box`는 항상 `null`입니다. 외부 Tools·대시보드 필드는
유지하고, 기존 화면은 대상 확인 상태와 이미지를 표시하되 사각형만 생략합니다.
관찰 설명은 캡처 근거에 저장되지만 현재 이미지 패널이 본문을 직접 표시하지는 않습니다.

모델 판정은 세 boolean으로 결정하며, `남성` 같은 정상 표현이나 주변 물체에
대한 부정 표현 때문에 설명문 전체를 거절하지 않습니다. JSON 필드·타입,
한국어 설명의 길이·형식, 임무·이미지 연결 검사는 유지합니다.
구조 성공·부상 구조·시간 초과·점수는 기존 분석 반영 시각과 시나리오 규칙으로
계산합니다. VLM의 대상 발견은 실기체 비행 완료나 구조 성공을 뜻하지 않습니다.

## 7. 중복 호출·연결 실패·소유권

- 출발/정지는 business request ID를 하나만 생성합니다.
- 같은 caller/request와 같은 내용은 같은 접수 결과입니다.
- 같은 ID의 내용이 달라지면 `IDEMPOTENCY_CONFLICT`입니다.
- 응답이 유실되어도 새 ID로 다시 출발하지 않습니다.
- `GET /requests/{caller_id}/{request_id}`로 원래 접수를 확인합니다.
- 임무 조회·촬영·정지는 원래 caller가 수행합니다.
- `drone_get_mission`을 주기적으로 호출해 기본 10초 lease를 갱신합니다.
- 임무 점유/unknown을 화면 초기화만으로 지우지 않습니다.
- mock 데이터는 **메모리**에 있으므로 프로세스 재시작 시 초기화됩니다.
  실제 PC Tools의 SQLite 재시작 복구·영속성은 mock이 보장하지 않는 별도 동작입니다.

## 8. 독립 HTTP mock으로 재현할 오류 시나리오

| `--scenario` | 개발할 화면/오류 처리 |
|---|---|
| `nominal` | 전체 순서·2장씩 캡처·복귀·모의 착륙 |
| `preflight-failure` | 접수됐지만 비행 준비에서 실패 |
| `camera-unavailable` | 도착했지만 이미지 없음 |
| `connection-loss` | 연결 끊김·unknown·점유 유지 |
| `manual-landing` | 복귀 후 모의 RC 착륙 신호 대기 |
| `lost-ack` | 접수는 됐지만 첫 출발 응답만 유실 |

mock 전용 엔드포인트:

| 경로 | 용도 |
|---|---|
| `GET /health` | 독립 mock 프로세스 확인 |
| `GET /tools.json` | 고정 tool 인자 schema |
| `GET /contract.schema.json` | 고정 envelope/response schema |
| `GET /mock/status` | 모의 상태·시나리오 확인 |
| `POST /mock/land` | `{"mission_id":"...","caller_id":"..."}`로 모의 착륙 신호 |

`/mock/*`는 실제 PC Tools에 없습니다. 프런트의 실제 API adapter에 이 경로를
필수 의존성으로 넣지 않습니다. 테스트 harness에서만 사용합니다.

## 9. 프런트 변경 시 기준

| 변경 | 고정 계약에 미치는 영향 |
|---|---|
| 디자인·카드 배치 | 없음. ID와 상태 의미 유지 |
| UI 프레임워크 | 없음. HTTP 계약과 타입을 사용 |
| endpoint 주소 | adapter의 base URL만 구성. 실기는 backend proxy/auth 필요 |
| 화면에 optional 진단 필드 추가 | required로 가정하지 않음 |
| 목적지 개수/ID 변경 | capabilities와 버전 계약 합의 필요 |
| 필수 field/type/상태 의미 변경 | 새 계약 버전 필요. 기존 v1 유지 |
| Voice 대화/확인 문구 변경 | 다른 팀 영역. 이 mock에서 처리하지 않음 |

## 10. 확인 범위와 한계

| 항목 | 의미 |
|---|---|
| 계약/자동화 테스트 | 같은 인자·응답 구조, 선택 순서, 중복 호출, 상태/캡처 전달을 검사 |
| 통합 Test | 기존 `/ws` 명령과 지도 완료 신호를 거쳐 내장 mock 임무 수행 |
| 독립 HTTP Test | 같은 경로를 Node HTTP mock으로 실행해 직렬화/통신 경계 검사 |
| Real 신호 시험 | 실제 PC에는 capabilities/status 조회만 수행; 출발 payload는 시험용 수신기에서만 검사 |
| 실제 비행 | 이번 작업에서 실행하지 않음. APK arm 승인·물리 이동·현장 촬영·착륙은 미검증 |
| 배포 반영 | PR push와 다름. main 병합 + 기존 CI 성공 이후에 배포 웹에서 사용 가능 |

개발자용 기존 테스트 실행:

```powershell
python -m unittest relay.test_tool_target relay.test_drone_client relay.test_contract_mock relay.test_integrated_modes relay.test_real_signal
node --test .\contracts\drone-tools\v1\mock.test.mjs .\contracts\drone-tools\v1\validate.test.mjs
node .\contracts\drone-tools\v1\verify.mjs
```

문제가 생기면 먼저 `/api/config`의 `runMode`, `droneControlTransport`, `droneReady`,
`droneError`를 확인합니다. `inprocess`인데 18767 포트가 없다는 것은 정상입니다.
`runMode:null`이거나 이전 화면이라면 새 PR 코드가 배포되었는지 확인합니다.
Voice 인증 실패는 드론 mock 문제와 구분합니다. 실제 드론이 꺼진 Real에서는
준비 안 됨이 정상이며, 이를 숨기려고 Test 성공 상태로 바꾸지 않습니다.

시나리오 마감 시간(18/28/45초), Home6 복귀, RC 수동 착륙, 최종 음성 종료는
서로 다른 조건입니다. Test 완료는 실제 전체 비행이 이 시간 안에 끝난다는 보장이 아닙니다.
이전 `relay.frontend_lab`/ZIP은 현재 배포·팀원 테스트 절차가 아닙니다.
