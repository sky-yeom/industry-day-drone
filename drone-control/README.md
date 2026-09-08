# Drone control and Speech tool boundary

이 폴더는 드론 제어 담당 코드와 구현 계획입니다. Speech 대화·방문 순서 결정·대시보드·시나리오 채점은 상대 팀 담당입니다. 우리는 전달받은 목적지 순서를 검증하고 실행 상태·센서·사진·오류를 반환합니다.

**현재 Tool 실행은 mock 전용입니다.** 실제 비행 adapter, 영속 중복 방지, HTTP service 및 계획된 앱 통신 수정은 아직 적용하지 않았습니다. 기존 기체 제어 소스가 포함돼 있다는 사실을 Speech 실기 연동 완료로 해석하지 마세요.

| 위치 | 내용 |
|---|---|
| [integration/speech_control_contract](integration/speech_control_contract/README.md) | Tool7개, 엄격한 인자 검증, 비동기 호출 wrapper, 단일 mock 임무·중복/중단 처리와 테스트 |
| [Tool 정의](integration/speech_control_contract/tools.json) | capabilities/status/execute/get mission/stop/sensors/captures |
| [우리 구현 계획](docs/fix_ready_20260907/DRONE_TOOL_CONTROL_IMPLEMENTATION_PLAN_20260908.md) | 변경할 파일·함수, 실행 조건, 중단·오류·센서/사진 계약 |
| [앱 수정 적용 순서](docs/fix_ready_20260907/APPLY_ORDER.md) | FC·query·영상·PC 상세 계획 |
| [pc/drone_nav](pc/drone_nav) | 현재 PC 제어·태그·영상·프로토콜 source snapshot |
| [Android source](android/README.md) | 현재 사용한 MSDK5.18 bridge 및 sample/UX overlay |
| [trials](trials/README.md) | 마지막 supervised trial의 재현 참고 소스; Speech Tool에 연결되지 않음 |

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

## 게시 검증과 출처

- PC 오프라인 테스트88개, Tool 계약 테스트11개 통과.
- PC production source는 줄바꿈·파일 끝 공백 정리 외에는 원래 작업 폴더와 같습니다. 테스트3개만 비공개 config.local 대신 샘플 설정을 사용하도록 정리했습니다.
- trial의 소나 함수2개는 원본 AST와 동일합니다. 경로·import를 저장소 기준으로 옮겼으며 새 실기 비행은 하지 않았습니다.
- Android 파일은 현재 앱의 source snapshot이며 이 게시용 overlay에서 APK를 새로 빌드하거나 설치하지 않았습니다.
- 계획의 로컬 증거 링크는 미게시 자료임을 표시했습니다. 문서 작성 시 source hash와 export hash는 [SOURCE_MANIFEST.json](SOURCE_MANIFEST.json)에 구분합니다.

PC 원본 기반: WhoAmI125/13_DRONE `437f9cb` 이후 작업 snapshot. DJI sample 기준 및 라이선스는 [Android 안내](android/README.md)를 따릅니다. 원래 PC·Android 작업 폴더는 변경하지 않았습니다.
