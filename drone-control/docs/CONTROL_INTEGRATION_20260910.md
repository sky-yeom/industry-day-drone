# 고정 Drone Tools v1 계약과 독립 mock

## 목적

프런트 팀은 **우리 백엔드가 실행되지 않아도**, 아래 고정 계약을 기준으로
화면·상태 관리·도구 호출을 개발할 수 있습니다. 우리 실제 백엔드가 이 계약에
맞춰야 하며, 프런트가 우리 구현의 변경을 따라가도록 만드는 구조가 아닙니다.

- 계약·mock: [`contracts/drone-tools/v1`](../../contracts/drone-tools/v1)
- 계약 버전: **1.0.0**, wire `schema_version: 1`
- 실행 의존성: **Node.js 20 이상만**. `npm install` 불필요
- 불필요한 것: Python, relay, `drone_nav`, Azure, DJI SDK, 드론, APK, 실제 토큰/설정, ZIP
- 해당 폴더만 checkout/copy해도 실행됩니다. 상위 저장소 코드·이미지를 import하지 않습니다.
- 기존 프런트엔드와 Voice 대화·확인 문구는 변경하지 않습니다.

## 1. GitHub에서 받아 바로 실행

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

우리 저장소의 `relay/test_fixed_tools_contract.py`는 실제 tool 인자 정의와
고정된 `tools.json`이 같은지 검사합니다. 이 검사는 **우리 백엔드의 의무**이며,
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
이 mock이 Voice 대화, 시나리오 점수, Azure 분석, DJI 비행 물리를 흉내 내지는 않습니다.
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

mock PNG는 독립 모듈 안에서 생성한 가짜 이미지입니다. 실제 사진·시나리오
이미지를 가져오지 않습니다. `simulated:true`, `physical_execution:false`,
`physical_stop_confirmed:false`를 유지합니다.
real-mode에서도 `physical_execution:true`는 실제 모드라는 뜻이지 조회 요청이
비행을 실행했다는 뜻이 아닙니다.

`completed`, 시나리오 점수 완료, Voice 응답 종료를 혼동하지 않습니다.
실제 현장 비행은 Home6 복귀 뒤 RC 수동 착륙과 신선한 지상 증거가 필요합니다.
이 mock은 그 상태 전이만 재현하고 실제 착륙 성공을 증명하지 않습니다.

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

## 8. 팀원이 재현할 mock 시나리오

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

이전 `relay.frontend_lab`는 우리 Python 서비스 재사용을 위한 내부 시험기였으며,
프런트 팀 독립 실행 요구를 충족하는 배포 단위가 아닙니다. 팀원은 이 문서의
**`contracts/drone-tools/v1`만** 사용합니다. ZIP이나 우리 PC 실행을 기다릴 필요가 없습니다.
