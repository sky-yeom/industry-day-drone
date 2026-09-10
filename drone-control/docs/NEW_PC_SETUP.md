# 새 PC 준비 — feat/drone

이 브랜치는 PC 드론 제어, 7개 HTTP 도구, Speech 연결, Android 수정 소스,
태그 없는 COEX 시험 코드와 가상 시나리오를 포함합니다. 클론만으로 실비행을
시작할 수는 없습니다. 아래 개인 설정과 장치 연결은 새 PC에서 준비해야 합니다.

## 1. 저장소와 실행 환경

최신 통합 브랜치는 `feat/drone`입니다. `feat/drone-control`은 이전 브랜치입니다.

```powershell
git clone --branch feat/drone --single-branch https://github.com/sky-yeom/industry-day-drone.git
cd industry-day-drone
```

Git, Node.js 20.9 이상, Python 3.11 이상을 먼저 설치합니다. 기존 PC 검증 환경은
Node 24.19.0 / Python 3.12.10입니다. 음성을 사용하려면 Azure CLI도 설치하고,
`VOICE_LIVE_RESOURCE`에 접근할 수 있는 계정으로 `az login`을 완료해야 합니다.
CLI 설치 후 새 PowerShell을 열어 PATH를 적용합니다.
PC 영상 의존성은 최소 버전 범위로 지정돼 있으므로 설치 후 해당 PC에서도
의존성·연결 검증이 필요합니다. 완전히 동일한 환경을 고정한 배포 패키지는 아닙니다.

```powershell
npm.cmd ci
python -m venv relay/.venv
relay/.venv/Scripts/python.exe -m pip install -r relay/requirements.lock.txt
python -m venv drone-control/.venv
drone-control/.venv/Scripts/python.exe -m pip install -e './drone-control[vision]'
npm.cmd run build
az login
Copy-Item relay/.env.example relay/.env
```

`relay/.env`를 편집해 실제 사용하는 Voice Live 리소스를 지정합니다. 기본 모의
비행·이미지 분석도 음성은 실제 Azure를 호출합니다. `.env` 복사 자체가 Azure
로그인, 리소스 권한 또는 이미지 모델 배포를 대신하지 않습니다.

별도 PowerShell 두 개에서 저장소 루트를 작업 디렉터리로 사용합니다.

```powershell
# 터미널 1: .env를 명시적으로 읽어 릴레이 시작
./scripts/start-drone.ps1 -Component relay -EnvFile relay/.env
```

```powershell
# 터미널 2: 빌드된 웹 실행
npm.cmd run start -- --hostname 127.0.0.1 --port 3000
```

`http://127.0.0.1:3000/`에서 시작합니다. 웹 표시, Azure 음성 응답, 모의 경로
실행을 각각 확인합니다. 실행 중인 웹을 다시 빌드할 때는 먼저 해당 웹 터미널에서
Ctrl+C로 종료한 뒤 빌드·재실행합니다.

## 2. 실제 드론을 연결할 때

Git에 없는 항목:

- 휴대폰 APK: `com.ms.voice`, `5.18-connectivity.20260910.6` (첫 연결·모터 상태 갱신 수정).
- PC의 실제 `config.local.json`: 휴대폰 IP, 앱 arm token, 카메라·태그 실측/보정값.
- 실제 site 설정과 공유 PC API token, Azure 로그인 또는 개인 자격 증명.
- 실제 이미지 분석용 Azure endpoint/deployment 설정.
- Android를 새로 빌드할 때 필요한 DJI API key 및 서명 키/속성.

실제 값은 별도 비공개 경로로 옮깁니다. 새 PC와 휴대폰을 같은 네트워크에 연결하고,
현재 휴대폰 IP와 경로를 갱신합니다. 기존 장소의 측정값이 새 장소를 증명하지는
않으므로 현장 확인 플래그를 임의로 켜지 않습니다.

같은 휴대폰에 필요한 APK가 이미 설치되어 있으면 PC를 바꾼다는 이유로 Android를
다시 빌드할 필요는 없습니다. 이번 .5 APK는 기존 PC에서 빌드됐지만 설치·기체
연결 검증은 아직 완료되지 않았습니다. APK 파일은 Git에 포함돼 있지 않습니다.

실제 모드의 환경 변수, PC 서비스 실행, 태그 배치와 RC 착륙 흐름은
[통합 안내](CONTROL_INTEGRATION_20260910.md)를 따릅니다. 실제 비행을 시작하기 전
새 PC–앱–RC–기체의 지상 연결을 확인합니다.
태그 없는 COEX 실행기를 사용할 때는 `trials/START_COEX.ps1`의 `-ConfigPath`에
새 PC의 개인 설정 경로를 명시합니다. 기본값은 이전 PC 경로입니다.

## 3. Android를 새로 빌드할 때

`drone-control/android/SampleCode-V5`는 완성된 독립 프로젝트가 아니라 DJI sample에
적용하는 수정 소스입니다. DJI 원본 commit `a48aa4e7811d824c27abfa973f5655579bfb8a77`,
Gradle wrapper와 SDK 의존성, JDK 17, Android SDK 35, 개인 Gradle API/operator/signing
속성이 별도로 필요합니다. 정확한 적용 경로·명령은 [Android 안내](../android/README.md)를
참고하세요. 이미 설치된 앱을 업데이트하려면 기존 앱과 같은 서명 키를 사용합니다.
