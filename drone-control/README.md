# Drone control and Speech tool boundary

이 폴더는 드론 제어 담당 코드와 구현 계획입니다. Speech 대화·방문 순서 결정·대시보드·시나리오 채점은 상대 팀 담당입니다. 우리는 전달받은 목적지 순서를 검증하고 실행 상태·센서·사진·오류를 반환합니다.

**카메라 인식과 좌우 이동만 시험하려면:** [단독 AprilTag 왕복 안내](docs/STANDALONE_TAG_SHUTTLE.md)를 사용합니다.
배치는 벽 3·2·1·6, 바닥 0이며 경로는 6→1→2→3→2→1→6입니다. Speech/Azure 없이 실행합니다.

**2026-09-10: 최신 대시보드와 연결하는 HTTP 도구 서비스·실제 태그 비행 adapter·앱 연결 수정 코드를 추가했습니다.** 기본 실행은 mock입니다. 실제 모드는 새 APK, 현장 설정과 Azure 분석 설정을 명시해야 하며, 코드 검증과 실기 검증은 구분합니다. 설치·실행·검증 범위는 [현재 통합 안내](docs/CONTROL_INTEGRATION_20260910.md)를 따릅니다.

`DRONE_CONTROL_ADAPTER=field`는 최신 단독 시험 helper를 HTTP에 연결합니다.
1.5m, 벽 `[3,2,1,6]`, 85–95% 프레이밍으로 선택된 1·2·3 순서를 그대로 한 번씩 방문하고
각 방문에 서로 다른 신선한 PNG 두 장을 반환합니다. Home6 복귀 후 RC 수동 착륙입니다.
기존 `legacy` adapter와 기본 mock은 유지하며, `DRONE_CONTROL_MOCK_CAPTURES=1`만
명시적으로 시뮬레이션 fixture PNG를 제공하며 실기 촬영으로 표시하지 않습니다.

| 위치 | 내용 |
|---|---|
| [현재 통합 안내](docs/CONTROL_INTEGRATION_20260910.md) | Windows 실행, 7개 HTTP tools, durable journal, 실제 비행·촬영·중단 연결 |
| [integration/speech_control_contract](integration/speech_control_contract/README.md) | 공통 Tool 인자 스키마와 이전의 독립 mock 예제 |
| [Tool 정의](integration/speech_control_contract/tools.json) | capabilities/status/execute/get mission/stop/sensors/captures |
| [우리 구현 계획](docs/fix_ready_20260907/DRONE_TOOL_CONTROL_IMPLEMENTATION_PLAN_20260908.md) | 변경할 파일·함수, 실행 조건, 중단·오류·센서/사진 계약 |
| [앱 수정 적용 순서](docs/fix_ready_20260907/APPLY_ORDER.md) | FC·query·영상·PC 상세 계획 |
| [pc/drone_nav](pc/drone_nav) | 현재 PC 제어·태그·영상·프로토콜 source snapshot |
| [Android source](android/README.md) | 현재 사용한 MSDK5.18 bridge 및 sample/UX overlay |
| [trials](trials/README.md) | 단독 시험 기본 경로 유지; 최신 shuttle helper는 명시적 field HTTP adapter도 사용 |

## 기체 없이 연동 검사

Python3.11 이상에서 저장소 루트 기준:

```powershell
cd drone-control/integration/speech_control_contract
python -B -m unittest discover -s tests -v
python -B example.py
```

예제는 mock capability, accepted, 실제 상태 unknown, stop_requested를 보여줍니다. 네트워크나 기체를 호출하지 않습니다. 같은 실행 의도의 request_id는 재접속/재시도 때 유지합니다. 접수 응답과 실제 완료, 중단 요청과 물리 정지는 구별합니다.

PC 전체 테스트는 프로젝트의 vision 의존성이 있는 개발 환경에서 `drone-control`을 작업 디렉터리로 실행합니다.

```powershell
$env:PYTHONPATH = (Resolve-Path pc).Path
python -B -m unittest discover -s pc/tests -v
```

환경 준비가 필요한 경우 pyproject.toml의 `vision` 선택 의존성을 설치합니다. 샘플 설정은 측정값을 대신하지 않으며 `actual_measurements_confirmed`와 calibration이 완료된 현장 설정은 별도로 관리합니다. 개인 설정·키·APK·비행 원본 로그/사진은 이 커밋에 포함하지 않았습니다.

## 최초 snapshot 게시 검증과 출처 (수정 전 기록)

- PC 오프라인 테스트88개, Tool 계약 테스트11개 통과.
- PC production source는 줄바꿈·파일 끝 공백 정리 외에는 원래 작업 폴더와 같습니다. 테스트3개만 비공개 config.local 대신 샘플 설정을 사용하도록 정리했습니다.
- trial의 소나 함수2개는 원본 AST와 동일합니다. 경로·import를 저장소 기준으로 옮겼으며 새 실기 비행은 하지 않았습니다.
- Android 파일은 현재 앱의 source snapshot이며 이 게시용 overlay에서 APK를 새로 빌드하거나 설치하지 않았습니다.
- 계획의 로컬 증거 링크는 미게시 자료임을 표시했습니다. 문서 작성 시 source hash와 export hash는 [SOURCE_MANIFEST.json](SOURCE_MANIFEST.json)에 구분합니다.

PC 원본 기반: WhoAmI125/13_DRONE `437f9cb` 이후 작업 snapshot. DJI sample 기준 및 라이선스는 [Android 안내](android/README.md)를 따릅니다. 원래 PC·Android 작업 폴더는 변경하지 않았습니다.
