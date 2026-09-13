# Voice(main)로는 왜 드론 제어가 안 되는가 — 원인·real 모드 전환 시 남는 문제·해결 절차

작성: 2026-09-13. 대상 코드: GitHub `sky-yeom/industry-day-drone` **`main`**(0c06be0) 과
**`feat/drone`**(33ac704). 이 문서는 다른 AI/개발자에게 그대로 넘겨 작업을 시작할 수 있도록
파일·줄·조건·환경변수를 전부 적는다.

---

## 0. 한 줄 결론

도구 계약(7개 이름·인자)은 LLM 경로와 Voice 경로가 **완전히 같다.** 하지만 실제로
비행에 성공한 것은 PC에서 `drone-control/trials/standalone_tag_shuttle.py`를 **직접 실행**한
경로이고, Voice는 그 스크립트를 부르지 않는다. Voice는
`relay → HTTP 도구 서비스 → FieldAdapter → shuttle.run()` 이라는 **별도 스택**을 타며,
이 스택은 (1) 이 노트북에서 mock으로 설정돼 있고, (2) real로 바꿔도 **코드에 고정된
어제 배치(3·2·1·6, 1.5 m)와 목적지 3개 전제** 때문에 `live_ready`가 절대 true가 되지 않으며,
(3) main의 셔틀 코드는 9/11 변경(2·1·6, 1.2 m)을 포함하지 않는다. 즉 **mock→real 전환만으로는
해결되지 않고 코드 수정이 필요하다.**

---

## 1. 두 경로 비교

```
[검증된 경로: "LLM 버전"]
  PC 터미널 → START_TAG_SHUTTLE.ps1 -Mode Patrol
    → trials/standalone_tag_shuttle.py run()
      → NDJSON/TCP(9998) → 폰 앱 com.ms.voice(.6) → RC-N2 → Mini 4 Pro
  실기 성공: 9/10 15:26(4태그), 9/11 16:01(2·1·6, 1.2 m, 77초 완주)

[Voice 경로: main]
  브라우저(Next.js) ──WS── relay/server.py(:8080) ──WSS── Azure Voice Live(gpt-realtime)
    Voice Live 도구 호출 → relay/tools.py dispatch()
      launch_mission → relay/live_mission.py LiveMissionRunner.launch()
        → relay/drone_client.py (HTTP :8766, Bearer 토큰, X-Drone-Expected-Mode)
          → drone-control/pc/drone_nav/tool_control/server.py
            → service.py MissionService._admit() → 워커 → adapter.run()
              → field.py FieldAdapter.run() → shuttle.run(external_route=..., on_capture=...)
                → 폰 앱 → 드론
        ← drone_get_mission / drone_get_captures 폴링 → Azure Vision(TRIAGE_MODE=azure)으로 판정
```

Voice Live에는 드론 도구 7개가 직접 등록되지 않는다. Voice Live에 등록되는 도구는
`select_stop / confirm_route / clear_route / launch_mission / retry_mission / abort_mission /
get_state / prepare_prompt / confirm_prompt`(`relay/tools.py` `voice_context()`)이고, 드론 7개
도구는 **relay가 내부적으로** `DroneClient`로 호출한다. 따라서 "계약을 Voice로 바꿨다"는 것은
정확히는 "relay가 드론 서비스에 HTTP로 계약을 호출하게 했다"는 뜻이다.

---

## 2. Voice 경로의 게이트 전부 (main 기준, 순서대로)

| # | 위치 | 조건 | 이 노트북 현재 | real로 바꾸면 |
|---|---|---|---|---|
| 1 | `relay/tool_target.py:44-55` | `DRONE_RUN_MODE` 미설정 + `DRONE_CONTROL_MODE=mock` → 레거시 mock, `use_tools=False` | `relay/.env`: `DRONE_CONTROL_MODE=mock`, RUN_MODE 없음 | 해소 (RUN_MODE=real이면 control=live, use_tools=True) |
| 2 | `relay/live_mission.py:104` | mock인데 `DRONE_CONTROL_USE_TOOLS≠1` → "MOCK 도구 실행은 …활성화" 거부 | 거부됨. **어떤 HTTP도 나가지 않음** | 해소 |
| 3 | `relay/live_mission.py:106` | live면 `TRIAGE_MODE=azure` 필수 (모의 분석으로 대체 안 함) | `TRIAGE_MODE=mock` | **남음** → Azure Vision 엔드포인트/키 필요 |
| 4 | `relay/tool_target.py:78-80` | real+local 전송이면 `DRONE_CONTROL_API_TOKEN` 필수 | 없음 | **남음** → 양쪽 동일 토큰(24자 이상) |
| 5 | `relay/drone_client.py:115-120` `readiness()` | 토큰 없으면 "DRONE_CONTROL_API_TOKEN을 relay와 PC 서비스에 설정" | 없음 | 4와 동일 |
| 6 | PC `tool_control/server.py adapter_from_environment()` | live면 `DRONE_CONTROL_ENABLE_LIVE=1`, `DRONE_CONTROL_ADAPTER=field`, `DRONE_CONTROL_SITE_CONFIG`, `DRONE_CONTROL_CONFIG_PATH`, `DRONE_CONTROL_FIELD_PROFILE`, `DRONE_CONTROL_FIELD_REFERENCE`, 토큰 24자 | `integration/control.env` 없음, 서비스 미기동 | **남음** → 파일·env 작성, 서비스 기동 |
| 7 | `field.py:67-72` | site.json 필드가 정확히 `SITE_FIELDS`이고 `layout_confirmed=true`, `field_setup_confirmed=true` | site.json 없음 (`site.example.json`은 필드 구성이 다름) | **남음** → site.json 작성 |
| 8 | `field.py:73-79` | site가 **`wall_ids_left_to_right == [3,2,1,6]`, `home=6`, `floor=0`, `target_height_m == 1.5`**, `expected_bridge_build_id == .6` | 현재 배치 2·1·6, 1.2 m | **남음, 코드 수정 필요** |
| 9 | `field.py:82-84` | 프로필 `target_height_m == 1.5` | 1.2 | **남음, 코드 수정 필요** |
| 10 | `field.py:91-95` | `actual_measurements_confirmed == true`, 폰 IP가 사설망(루프백 아님) | 개인 설정 `false` | **남음** → 설정 플래그 |
| 11 | `field.py:62-64`, `service.py capabilities()` | 목적지 고정 `["tag-1","tag-2","tag-3"]`, `supported_ordered_sequences` = 3개 순열 | 벽 태그가 1·2 두 개 | **남음, 코드 수정 필요** |
| 12 | `field.py run():140-149` | 임무 목적지 정확히 3개, 경로 `[6, d1, d2, d3, 6]`를 `validate_external_route`로 검증 | 2개 | 11과 동일 |
| 13 | `relay/live_mission.py:112-132` | `caps.live_ready == true`, 대시보드 모니터 3개 ↔ `destinations[].monitor_id` 일대일, 확정 순서가 `supported_ordered_sequences`에 포함 | 모니터 3개 vs 태그 2개 | 11과 동일 |
| 14 | `service.py _admit():296` | `mode=="live" and not adapter.live_ready` → `LIVE_NOT_READY` | 7~10 때문에 false | 7~10 해결 시 해소 |
| 15 | main `standalone_tag_shuttle.py` | `run(..., external_route, video_broker, on_capture, capture_count)`; `load_config(..., image_only_walls=True)`; `validate_external_route` | **feat/drone 셔틀에는 이 인자들이 없고, main 셔틀에는 9/11 변경(프로필 기반 배치, 1.2 m, climb 허용폭)이 없음** | **남음, 두 셔틀 통합 필요** |
| 16 | `field.py _capture_proof` | 촬영 순간 속도 ≤0.08 m/s, 하방 0.5~1.8 m, 배터리 ≥30 %, RC 개입 5초 내 없음, 프레임 신선 ≤0.5 s | 셔틀 단독 비행에서 충족됨 | 그대로 통과 예상 |
| 17 | `field.py capture()` | **방문마다 서로 다른 프레임 2장** 필수(`capture_count=2`), 아니면 `outcome_unknown` | 셔틀 단독은 1장 | main 셔틀은 2장 지원 |
| 18 | `service.py` lease 10 s | relay가 `drone_get_mission`으로 lease 갱신 안 하면 `caller_lease_expired` → 정지 | relay `_renew_lease` 구현됨 | 유지 |
| 19 | `relay/server.py /api/config` | live면 `DroneClient.readiness()`, `read_drone_status()`의 `executionMode`가 설정과 같아야 함 | — | 4·6 해결 시 통과 |

요약: **mock→real 전환은 1·2만 푼다. 3·4·6·7·10은 설정/파일 작업, 8·9·11·12·13·15는 코드 수정이다.**

---

## 3. "real로 연결해도 이런 문제가 생기는가?" — 예. 이유

1. `DRONE_RUN_MODE=real`을 주면 relay는 `control_mode=live`, `api_url=http://127.0.0.1:8766`,
   `use_tools=True`가 되고 토큰을 요구한다(`tool_target.py:74-87`). 여기까지는 설정이다.
2. relay는 `launch()`에서 PC 서비스의 `drone_get_capabilities`를 읽고 `live_ready`가 true가
   아니면 `PROFILE_UNAVAILABLE`로 거부한다(`live_mission.py:113-114`).
3. `live_ready`는 `FieldAdapter.__init__`가 예외 없이 끝나야만 true인데(`field.py:96`),
   그 생성자는 **배치 `[3,2,1,6]`·1.5 m·목적지 3개·`actual_measurements_confirmed=true`**를
   하드코딩으로 요구한다. 지금 현장(2·1·6, 1.2 m, 벽 태그 2개)에서는 생성자 자체가
   `ValueError`로 실패해 PC 서비스가 뜨지 않거나(`SystemExit`) `live_ready=false`다.
4. 설령 생성자를 통과해도 `field.run()`은 목적지 3개를 요구하고 `[6,d1,d2,d3,6]` 경로만
   만든다. 대시보드 모니터 3개 ↔ 태그 3개 전제다.
5. main의 셔틀은 9/10 저녁 상태(연속 보정 포함)까지만 반영돼 있고 9/11의 프로필 기반
   배치·1.2 m·climb 허용폭이 없다. 반대로 feat/drone 셔틀에는 field 어댑터가 쓰는
   `external_route / on_capture / capture_count / video_broker / image_only_walls`가 없다.
   **어느 브랜치의 셔틀을 써도 그대로는 안 맞는다.**

따라서 Voice로 실기 제어를 하려면 아래 4절이 전부 필요하다.

---

## 4. 해결 절차 (상세)

### 4-A. 코드 수정 (main 기준, PR 하나로)

1. **셔틀 통합**: `drone-control/trials/standalone_tag_shuttle.py`
   - feat/drone(33ac704)의 변경을 main에 반영: `validate_profile` 구조 검증(홈에서 왼쪽으로
     인접 이동, 끝 태그에서 1회 반전, 대칭), `planned_direction(…, wall_ids, route_ids)`,
     `outbound_leg_count(profile)`, `MixedDetector(config, wall_ids=…)`,
     `acquire_wall_home(…, home)`, `run()`의 `route/home/outbound_legs` 프로필 기반,
     `target_height_m` 허용 1.0~1.6, `bounded_sonar_climb.climb_command` 목표+0.3 m까지 도달 처리.
   - main의 `external_route / external_direction / on_capture / capture_count / video_broker /
     image_only_walls`는 유지. `external_direction`도 프로필 벽 순서를 쓰도록 바꾼다.
   - 기본 프로필을 `trials/profiles/standalone_tag_216.json`(2·1·6, 1.2 m)로.
2. **FieldAdapter 프로필 기반화**: `pc/drone_nav/tool_control/field.py`
   - `field.py:73-79`의 `[3,2,1,6]`·`1.5` 고정 비교 삭제 → site.json의
     `wall_ids_left_to_right`·`target_height_m`가 **프로필과 일치하는지**만 검사.
   - `destination_ids`를 클래스 상수가 아니라 `site["wall_ids_left_to_right"]`에서 홈을 뺀
     태그로 생성: 예 `["tag-1","tag-2"]`. `home_tag_id/floor_tag_id/target_height_m`도 site에서.
   - `run()`의 `len(destinations) != 3` → `!= len(self.destination_ids)`.
     경로는 `[home, *dest_tags, home]`이 아니라 **프로필의 대칭 경로**(6→1→2→1→6)여야 한다.
     즉 목적지 순서가 벽 순서(왼쪽 이동 순)와 같을 때만 지원: `supported_ordered_sequences`를
     순열 전체가 아니라 **프로필 route에서 파생된 순서 1개**(예 `[["tag-1","tag-2"]]`)로 제한.
     (음성으로 순서를 바꾸는 시나리오는 벽 한 줄 배치에서는 물리적으로 의미가 없다.)
   - `captured = {i: [] for i in range(len(destinations))}`.
3. **service.py**: `capabilities()`의 `supported_ordered_sequences`는 어댑터가 준 값을 그대로
   쓰므로 위 2번으로 충분. `destinations[].monitor_id`는 `"monitor-"+숫자`로 자동 생성되므로
   대시보드 모니터 id가 `monitor-1`, `monitor-2`여야 한다.
4. **relay**: `live_mission.launch()`는 `set(mapping) == set(confirmedRoute)`를 요구하므로
   대시보드/Voice 시나리오의 모니터 수를 태그 수(2)에 맞추거나, 시나리오는 3개를 유지하되
   드론 목적지가 없는 모니터를 허용하도록 `launch()`의 매핑 검증을 완화해야 한다.
   (`relay/tools.py`의 "세 번째는 자동 추가" 문구도 함께.)
5. **LegacyAdapter(`live.py LiveAdapter`)는 1.4 m·`{1,2,3,home}` 고정**이라 쓰지 않는다.
   `DRONE_CONTROL_ADAPTER=field`만 사용.

### 4-B. 파일·설정 작성 (Git에 올리지 않는 것 포함)

1. `drone-control/integration/site.json` (필드는 정확히 `SITE_FIELDS`):
   ```json
   {
     "schema_version": 1,
     "profile_id": "apriltag-wall-216-v1",
     "site_revision": "coex-2026-09-13-r1",
     "wall_ids_left_to_right": [2, 1, 6],
     "floor_tag_id": 0,
     "home_tag_id": 6,
     "target_height_m": 1.2,
     "expected_bridge_build_id": "5.18-connectivity.20260910.6",
     "layout_confirmed": true,
     "field_setup_confirmed": true
   }
   ```
2. `drone-control/pc/config.tag-shuttle.local.json`: `actual_measurements_confirmed: true`
   (카메라 내부 파라미터는 이미 `calibrated: true`; 이 플래그는 "현장 실측을 확인했다"는 선언).
   `network.host`는 현재 폰 IP(사설망, 예 `10.244.155.20`).
3. `drone-control/integration/control.env` (PC 도구 서비스):
   ```
   DRONE_CONTROL_MODE=live
   DRONE_CONTROL_ENABLE_LIVE=1
   DRONE_CONTROL_ADAPTER=field
   DRONE_CONTROL_API_TOKEN=<24자 이상, relay와 동일>
   DRONE_CONTROL_SITE_CONFIG=drone-control/integration/site.json
   DRONE_CONTROL_CONFIG_PATH=drone-control/pc/config.tag-shuttle.local.json
   DRONE_CONTROL_FIELD_PROFILE=drone-control/trials/profiles/standalone_tag_216.json
   DRONE_CONTROL_FIELD_REFERENCE=drone-control/trials/profiles/id1_tv_pair_reference.json
   DRONE_CONTROL_PORT=8766
   ```
4. `relay/.env`:
   ```
   DRONE_RUN_MODE=real
   DRONE_REAL_TRANSPORT=local
   DRONE_CONTROL_API_TOKEN=<위와 동일>
   TRIAGE_MODE=azure
   AZURE_VISION_ENDPOINT=…  AZURE_VISION_DEPLOYMENT=…  AZURE_VISION_API_KEY=…(또는 AAD)
   VOICE_LIVE_RESOURCE=…  VOICE_LIVE_REGION=…  VOICE_LIVE_MODEL=gpt-realtime
   RELAY_HOST=127.0.0.1  RELAY_PORT=8080
   ```
   `RELAY_HOST=0.0.0.0`이면 `DRONE_REAL_TRANSPORT`가 자동으로 `remote`가 되어 디바이스 허브
   (`/ws/device`, `DRONE_REMOTE_DEVICE_ID/TOKEN`, `DRONE_REMOTE_SINGLE_REPLICA=1`,
   PC 쪽 `relay/pc_connector.py` + `DRONE_RELAY_URL`)가 필요해진다. **현장 데모는 local로.**
5. 웹(`.env.local`): relay URL이 `ws://127.0.0.1:8080/ws`인지 확인.

### 4-C. 기동 순서

1. 폰: RC-N2 USB 연결, 앱 `.6` 실행, 핫스팟 켬. PC를 핫스팟에 연결.
2. PC 도구 서비스: `control.env`를 로드한 뒤
   `drone-control/.venv/Scripts/python.exe -m drone_nav.tool_control.server` (cwd `drone-control/pc`).
   기동 로그에 `FieldAdapter` 예외가 없어야 한다. 예외가 나면 `live_ready`가 될 수 없다.
3. 지상 확인:
   `curl -s -H "Authorization: Bearer <토큰>" -H "X-Drone-Expected-Mode: live" -H "Content-Type: application/json" -d "{\"arguments\":{},\"caller_id\":\"check\",\"request_id\":\"c1\"}" http://127.0.0.1:8766/tools/drone_get_capabilities`
   → `"live_ready": true`, `"destinations"` 2개, `"supported_ordered_sequences"` 확인.
4. relay: `relay/.venv/Scripts/python.exe relay/server.py` → `GET /api/config`에서 `droneError`가
   null인지 확인.
5. 웹 `npm run dev` → Voice로 경로 확정 → `launch_mission`. 첫 실기 1회는 RC 잡고 관찰.

### 4-D. 검증 시 볼 것

- PC 서비스 로그의 `drone_execute_route` 수락(`state: accepted`) → 워커 → `shuttle.run` 시작.
- 방문마다 `visit_state: arrived → captured` 2회, 사진 PNG(≤4 MiB) base64.
- relay `_run_live`가 `drone_get_captures`를 읽어 Azure Vision에 넘기는지(`TRIAGE_MODE=azure`).
- 종료 후 `verification_pending`이 남으면 지상 확인(`ground_verified`)이 안 된 것 → 다음
  임무가 `MISSION_BUSY`로 막힌다. 착륙·모터 정지 뒤 `drone_get_status`를 한 번 더 읽는다.

---

## 5. 대안: 데모 최소 경로 (코드 수정 최소)

Voice는 **경로 확정까지만** 담당하고, 출발은 검증된 스크립트로 한다.
- relay `launch_mission` 핸들러가 HTTP 대신 `START_TAG_SHUTTLE.ps1 -Mode Patrol -PhoneIp …`를
  서브프로세스로 실행하고 종료 코드/`standalone_result`를 읽어 대시보드에 반영.
- 장점: 실기 검증된 코드 그대로, 게이트 3~15 전부 우회. 단점: 사진 분석(Azure Vision)과
  방문별 진행 상태 연동은 별도 구현(`pc/captures/<세션>/` 사진과 JSONL 로그를 읽으면 된다).
- 이 방식이면 오늘 코드로 30분 내 연결 가능.

---

## 6. 참고: 관련 파일 (main)

- relay: `relay/tool_target.py`, `relay/config.py`, `relay/server.py`, `relay/tools.py`,
  `relay/live_mission.py`, `relay/drone_client.py`, `relay/contract_mock.py`, `relay/pc_connector.py`
- PC 도구 서비스: `drone-control/pc/drone_nav/tool_control/{server,service,field,live,camera}.py`
- 비행: `drone-control/trials/standalone_tag_shuttle.py`, `id1_pair_framing.py`, `bounded_sonar_climb.py`,
  `profiles/standalone_tag_216.json`, `profiles/id1_tv_pair_reference.json`
- 계약: `contracts/drone-tools/v1/tools.json` == `drone-control/integration/speech_control_contract/tools.json`
- 예제: `drone-control/integration/site.example.json`(필드 구성이 `SITE_FIELDS`와 다름 — 그대로 쓰면 실패),
  `drone-control/integration/control.env.example`, `relay/.env.example`
- 실기 검증 기록: `drone-control/docs/FIELD_STATE_20260910.md`, `STANDALONE_TAG_SHUTTLE.md`
