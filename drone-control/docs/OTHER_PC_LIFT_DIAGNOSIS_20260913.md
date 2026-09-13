# 다른 PC용 진단 안내 — "Voice/LLM 경로에서 이륙조차 안 된다"

작성 2026-09-13. 대상: 회사 PC(`t-hajongkim`)에서 `scripts\start-integrated.ps1 -Mode Real`로
Voice Live → 릴레이 → PC 도구 서비스 → 드론 경로를 돌렸을 때 **기체가 뜨지도 않는** 문제.
이 문서는 그 PC에서 AI/개발자가 **5분 안에 어느 게이트에서 막혔는지 판정**하고 조치할 수 있게 쓴다.
배경 분석은 [VOICE_LIVE_CONTROL_GAP_20260913.md](VOICE_LIVE_CONTROL_GAP_20260913.md)(게이트 19개 전체),
실기로 검증된 것은 [FIELD_STATE_20260910.md](FIELD_STATE_20260910.md)를 본다.

---

## 0. 전제와 한 줄 결론

- **검증된 것**: PC에서 `START_TAG_SHUTTLE.ps1 -Mode Patrol`로 직접 실행하면 6→1→2→1→6(1.2 m) 완주,
  ID1·ID2 모크 구도 촬영 성공(9/11 16:01). 이 경로는 릴레이·LLM을 거치지 않는다.
- **안 되는 것**: 같은 기체·앱으로 Voice/LLM 경로(`start-integrated.ps1 -Mode Real`)에서는 출발 자체가 거부.
- **한 줄 결론(가장 유력)**: 그 PC의 env는 `DRONE_CONTROL_FIELD_PROFILE=…standalone_tag_6321236.json`
  (네 장 배치 3·2·1·6, 1.5 m)를 가리키고, `FieldAdapter`(`pc/drone_nav/tool_control/field.py`)는
  이 배치와 1.5 m를 **코드로 고정** 검사한다. 현재 물리 배치는 2·1·6, 1.2 m다. 사이트 파일을 현재
  배치로 바꾸면 어댑터 생성이 실패해 `live_ready=false` → 릴레이가 `PROFILE_UNAVAILABLE`로 출발을
  거부한다(이륙 명령 자체가 안 나감). 사이트 파일을 옛 값으로 두면 이륙은 되지만 없는 ID3을 찾다 실패한다.
  **"이륙조차 안 됨"은 첫 번째 경우다.** 아래 절차로 확정하라.

---

## 1. 5분 판정 절차 (순서대로)

각 단계의 출력 문구를 표에서 찾으면 원인이 나온다. 모든 명령은 저장소 루트에서, env 파일은
`%LOCALAPPDATA%\IndustryDayDrone\config\field-live.env`(그 PC 기준) 를 쓴다.

### A. 통합 기동 스크립트의 사전 점검만 실행

```powershell
.\scripts\start-integrated.ps1 -Mode Real -AnalysisMode Azure -CheckOnly -NoWeb
```

| 나오는 문구 | 원인 | 조치 |
|---|---|---|
| `Real mode requires the existing private connection environment file.` | env 파일 경로가 다름 | `-EnvFile`로 지정 |
| `Real mode requires DRONE_CONTROL_CONFIG_PATH` / `…SITE_CONFIG` | env의 경로가 없거나 파일 없음 | 경로 확인 |
| `Selected backend target is not configured. No process was started.` | 릴레이 쪽 readiness 실패: 토큰 없음 또는 Vision 설정 오류 | 아래 D의 표 |
| `The actual PC profile is not valid.` | **PC 어댑터 생성 실패 (`live_ready` 불가)** | 아래 B에서 정확한 ValueError 확인 |
| `Port 8766 is occupied…` | 이전 PC 서비스가 살아 있음 | 프로세스 종료 |

### B. PC 어댑터 단독 검증 (기체 명령 없음)

env를 프로세스에 올린 뒤(스크립트가 하는 것과 같게) 아래 한 줄을 실행한다.

```powershell
# env 로드 (KEY=value 줄만)
Get-Content "$env:LOCALAPPDATA\IndustryDayDrone\config\field-live.env" | Where-Object { $_ -match '^[A-Z][A-Z0-9_]*=' } | ForEach-Object { $k,$v = $_ -split '=',2; [Environment]::SetEnvironmentVariable($k,$v,'Process') }
$env:DRONE_CONTROL_MODE='live'; $env:DRONE_CONTROL_ENABLE_LIVE='1'; $env:DRONE_CONTROL_ADAPTER='field'
Set-Location drone-control\pc
..\.venv\Scripts\python.exe -B -c "from drone_nav.tool_control.server import adapter_from_environment; a=adapter_from_environment(); print('live_ready=', a.live_ready, 'destinations=', a.destination_ids, 'height=', a.target_height_m); a.video_broker.close()"
```

| 예외 문구 (`field.py`) | 원인 | 조치 |
|---|---|---|
| `Private field site requires explicit layout and this-PC setup confirmation` | site.json 키가 정확히 `schema_version, profile_id, site_revision, wall_ids_left_to_right, floor_tag_id, home_tag_id, target_height_m, expected_bridge_build_id, layout_confirmed, field_setup_confirmed` 가 아니거나 두 확인 플래그가 true 아님 (`site.example.json`은 키 구성이 달라 그대로 쓰면 실패) | site.json을 위 10개 키로 다시 작성 |
| `Field site requires APK.6, floor0, Home6, 1.5m and left-to-right [3,2,1,6]` | **site가 현재 배치(2·1·6 / 1.2 m)로 돼 있음. 코드가 옛 배치를 고정 검사** | §3의 정식 수정(브랜치) 또는 임시 조치 |
| `Field HTTP adapter requires the latest 1.5m profile` | 프로필이 1.2 m(216) | 같음 |
| `Field HTTP adapter requires the latest 85-95% edge arrival band` | `id1_tv_pair_reference.json`의 `arrival_center_x_fraction`이 `[0.85, 0.95]`가 아님 | 파일 확인 |
| `Confirmed private floor/calibration measurements and actual private phone IP required` | nav 설정(`field-live-nav.json`)의 `actual_measurements_confirmed`가 false 이거나 `network.host`가 사설 IP가 아님(127.0.0.1 불가) | 플래그 true, 폰 핫스팟 IP로 |
| `Live mode requires explicit DRONE_CONTROL_ENABLE_LIVE=1` | env 누락 | 추가 |
| `Set a private DRONE_CONTROL_API_TOKEN of at least 24 characters in both backends` | 토큰 없음/짧음 | 24자 이상, 릴레이와 동일 |
| (예외 없이 `live_ready= True`) | PC 쪽은 정상 | C로 |

### C. PC 서비스 기동 후 capabilities 확인

```powershell
# 서비스 기동 (별도 창) : ..\.venv\Scripts\python.exe -B -m drone_nav.tool_control.server
curl.exe -s -X POST http://127.0.0.1:8766/tools/drone_get_capabilities -H "Authorization: Bearer <토큰>" -H "X-Drone-Expected-Mode: live" -H "Content-Type: application/json" -d "{\"arguments\":{},\"caller_id\":\"diag\",\"request_id\":\"cap-1\"}"
```

확인: `"live_ready": true`, `"destinations"`(각 `destination_id`/`monitor_id`), `"supported_ordered_sequences"`,
`"home_tag_id"`, `"target_height_m"`. `live_ready`가 false면 B로 돌아간다. HTTP 401이면 토큰 불일치.

### D. 릴레이 `/api/config`

릴레이를 띄우고(`relay\.venv\Scripts\python.exe -B -m relay.server`) `http://127.0.0.1:8080/api/config`를 연다.

| `droneError` 문구 | 원인 |
|---|---|
| `DRONE_CONTROL_API_TOKEN을 relay와 PC 서비스에 설정해야 합니다.` | 릴레이 env에 토큰 없음 |
| `실제 드론은 TRIAGE_MODE=azure로 실제 촬영 이미지를 분석해야 합니다.` | TRIAGE_MODE가 azure 아님 |
| `Azure 이미지 분석 주소 AZURE_VISION_ENDPOINT를 서버에 설정해 주세요.` 등 Vision 문구 | Vision env 오류 (엔드포인트는 HTTPS 기본 주소만, 배포명 필요, API 버전 v1) |
| `선택한 Test/Real 모드와 연결된 Tools의 모드가 다릅니다.` | PC 서비스가 mock으로 떠 있음 |
| `실기 프로필·기체 연결·현재 지상 상태를 확인하지 못했습니다…` | PC `drone_get_status`가 기체 미연결/지상 미확인 (폰 앱·USB·기체 전원) |
| `기존 임무가 아직 점유 중입니다…` | 이전 임무가 `awaiting_rc_landing`/`outcome_unknown`으로 남음 → `field-tools.sqlite3`의 missions 확인, 착륙·모터 정지 후 `drone_get_status` 재조회 |
| `원격 PC가 연결되어 있지 않습니다…` | transport가 remote로 잡힘 (`RELAY_HOST=0.0.0.0`이면 자동 remote) → `RELAY_HOST=127.0.0.1`, `DRONE_REAL_TRANSPORT=local` |

### E. 음성/대시보드에서 출발했을 때 돌아오는 문구 (`relay/live_mission.py launch()`)

| 문구/코드 | 원인 |
|---|---|
| `경로와 탐색 프롬프트를 먼저 확인해야 합니다.` | 세션 phase가 ready 아님 (경로 확정·프롬프트 확인 전) |
| `… 세션의 실행 모드가 일치하지 않습니다.` | 세션 droneControlMode ≠ 릴레이 모드 (세션 새로 시작) |
| `MOCK 도구 실행은 DRONE_CONTROL_USE_TOOLS=1로…` | 릴레이가 mock 모드 |
| `실제 드론 촬영 분석은 TRIAGE_MODE=azure가 필요합니다.` | TRIAGE_MODE |
| `PROFILE_UNAVAILABLE` / `현장 프로파일과 실제 비행 준비 상태를 먼저 확인해야 합니다.` | **`live_ready=false` — B의 원인** |
| `INVALID_CAPABILITIES` | capabilities의 destinations가 세션의 모니터와 1:1로 안 맞음 (모니터 3개 vs 목적지 수) |
| `ROUTE_UNSUPPORTED` / `확인한 방문 순서는 현장 드론 프로파일에서 지원하지 않습니다.` | 확정 순서가 `supported_ordered_sequences`에 없음 |
| `DRONE_BUSY` / `기존 드론 임무가 종료되었는지…` | 이전 임무 점유 (D의 마지막 행) |
| `LIVE_NOT_READY` (PC `_admit`) | 서비스 기동 후 어댑터가 live_ready 아님 |
| `UNSUPPORTED_ROUTE` (PC) | 목적지 집합/순서 불일치 |
| `SITE_MISMATCH` (PC) | capabilities를 읽은 뒤 profile_id/site_revision이 바뀜 |
| `OUTCOME_UNKNOWN`/`TRANSPORT_ERROR` | 릴레이↔PC HTTP 실패(포트·토큰·5초 타임아웃) |

### F. 로그 위치

- 통합 기동 로그: `%LOCALAPPDATA%\IndustryDayDrone\logs\integrated\<yyyyMMdd-HHmmss-fff>\` (relay/control/web 각각)
- PC 비행 로그(JSONL): `DRONE_NAV_LOG_DIR` 또는 `drone-control/pc/logs/`. 이벤트 `standalone_plan`,
  `standalone_takeoff_settle_sample`, `standalone_interrupted`(error 문구), `standalone_result`
- 임무 DB: `DRONE_CONTROL_DB`(sqlite) `missions` 테이블의 `state` (`accepted/awaiting_rc_landing/outcome_unknown/stopped`)
- 사진: `drone-control/pc/captures/<세션>/`

---

## 2. 그 PC env에서 이미 보이는 의심점

| env | 값 | 문제 |
|---|---|---|
| `DRONE_CONTROL_FIELD_PROFILE` | `standalone_tag_6321236.json` | 옛 네 장 배치·1.5 m. 현재 벽은 2·1·6·1.2 m |
| `DRONE_CONTROL_SITE_CONFIG` | `field-live-site.json` | 내용이 `[3,2,1,6]`/1.5면 코드 검사는 통과하지만 물리 배치와 불일치 → 비행 중 ID3 탐색 실패. 현재 배치로 고쳤다면 어댑터 생성 실패 → 이륙 거부 |
| `DRONE_CONTROL_CONFIG_PATH` | `field-live-nav.json` | `actual_measurements_confirmed: true`, `network.host`=폰 핫스팟 IP(사설), `network.confirmation_token`(앱 arm 토큰) 확인 |
| `DRONE_CONTROL_API_TOKEN` | (값 있음) | 릴레이·PC 양쪽 동일해야 함. 이 값은 채팅에 노출됐으니 교체 권장 |
| `TRIAGE_MODE=azure`, `AZURE_VISION_*` | drone-build / drone-vision | 그 PC에서 `az login` 필요 (DefaultAzureCredential) |

---

## 3. 조치

### 정식 (권장): 브랜치 `feat/voice-drone-live`
main에서 분기해 다음을 구현 중(푸시되면 `git fetch && git checkout feat/voice-drone-live`):
- `standalone_tag_shuttle.py`를 프로필 기반으로 통합(2·1·6, 1.2 m, 연속 보정, `external_route`/촬영 훅 유지)
- `FieldAdapter`의 `[3,2,1,6]`·1.5 m 고정 제거 → site.json/프로필 값 비교, 목적지 수 가변(`tag-1`,`tag-2`), 지원 순서 1개
- 릴레이가 목적지 N개(2개) 시나리오를 처리
- `start-integrated.ps1` 기본 프로필을 216으로
- site.json 예제를 `SITE_FIELDS` 키로 교정, 실행 런북 추가
그 PC에서 할 일: 브랜치 받기 → `field-live-site.json`을 `{schema_version:1, profile_id, site_revision, wall_ids_left_to_right:[2,1,6], floor_tag_id:0, home_tag_id:6, target_height_m:1.2, expected_bridge_build_id:"5.18-connectivity.20260910.6", layout_confirmed:true, field_setup_confirmed:true}`로 → env의 `DRONE_CONTROL_FIELD_PROFILE`를 `standalone_tag_216.json`으로 → A~D 재실행.

### 임시 (브랜치 전): 옛 배치로 되돌리기
벽에 3·2·1·6 네 장을 다시 붙이고 1.5 m로, site.json도 `[3,2,1,6]`/1.5로. 코드 수정 없이 통과하지만
현재 현장과 맞지 않으므로 권하지 않는다.

---

## 4. 분석을 위해 보내줄 것 (그 PC에서 복사)

1. A·B·C·D 각 단계의 출력 전문(토큰 값은 가리기)
2. `%LOCALAPPDATA%\IndustryDayDrone\logs\integrated\<최근>\` 안의 relay/control 로그
3. `field-live-site.json`, `field-live-nav.json`(토큰 가리기), 사용 중인 프로필 파일 이름
4. 마지막 시도의 PC JSONL 로그에서 `standalone_interrupted`/`standalone_result` 줄
