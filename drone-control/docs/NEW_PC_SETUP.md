# 새 PC 준비 — 2026-09-13 인계

## 최신 인계 경로

현재 인계 브랜치는 `handoff/drone-backend-20260913`입니다. 최신 `main`의 UI/UX #7을
유지하면서 PC 영상 초기 수신 복구, 실제 출발 준비 검사, Voice 원문 기록,
Android의 영구 진단 로그를 포함합니다. 아래의 이전 `feat/drone` 안내보다 이 절을 우선합니다.

```powershell
git clone --branch handoff/drone-backend-20260913 https://github.com/sky-yeom/industry-day-drone.git
Set-Location industry-day-drone
npm.cmd ci
python -m venv relay\.venv
& .\relay\.venv\Scripts\python.exe -m pip install -r .\relay\requirements.lock.txt
python -m venv drone-control\.venv
& .\drone-control\.venv\Scripts\python.exe -m pip install -e '.\drone-control[vision]'
$env:NEXT_PUBLIC_RELAY_HTTP = 'http://127.0.0.1:8080'
$env:NEXT_PUBLIC_RELAY_WS = 'ws://127.0.0.1:8080/ws'
npm.cmd run build
az login
```

토큰·서명 키·실측 설정·APK·실제 로그는 GitHub에 포함하지 않습니다. 별도 로컬
`private-handoff` 묶음을 이 PC의 **OneDrive 밖**으로 복사한 뒤 실행합니다.
Azure 로그인 캐시는 이식하지 않으므로 `az login`은 새 PC에서 필요합니다.

```powershell
.\scripts\import-private-handoff.ps1 -Bundle 'D:\private-handoff' -PhoneIp '10.244.155.20'
.\scripts\start-integrated.ps1 -Mode Real -CheckOnly `
  -EnvFile "$env:LOCALAPPDATA\IndustryDayDrone\handoff-config\field-live.env"
.\scripts\start-integrated.ps1 -Mode Real `
  -EnvFile "$env:LOCALAPPDATA\IndustryDayDrone\handoff-config\field-live.env" `
  -VoiceTraceDirectory "$env:LOCALAPPDATA\IndustryDayDrone\logs\voice-trace"
```

IP는 예시이며 현재 폰 주소를 사용합니다. 가져오기는 개인 설정 안의 이전 PC 경로를 새
위치로 바꿉니다. 기존 파일을 덮어쓰지 않으며 현장 확인값을 새로 만들어 내지 않습니다.
이전 임무 DB는 이력으로 보존하되 새 PC에서 재개하지 않습니다. 기체가 실제로 정지한
상태인지 RC로 확인하고, 미해결 임무를 재전송하지 마세요.

### Android 업데이트와 로그

필요한 앱은 `com.ms.voice`, **`5.18-connectivity.20260913.3`**입니다.
PC `BUILD_ID`와 private site의 `expected_bridge_build_id`가 일치해야 합니다.
기존 서명으로 `adb install -r`만 사용하며 삭제·데이터 초기화·서명 변경은 하지 않습니다.
APK는 로컬 인계 묶음에 포함하거나 다음 명령으로 재생성합니다.

```powershell
.\scripts\build-android.ps1 `
  -PrivateProperties "$env:LOCALAPPDATA\IndustryDayDrone\handoff-config\android-private\build.local.properties"
```

이 스크립트는 고정된 DJI 원본 revision을 받아 Git의 overlay를 적용하므로 Gradle wrapper와
원본 sample이 누락되지 않습니다. JDK17, Android SDK35 및 SDK 라이선스 승인이 필요합니다.
원본 다운로드 없이 로컬의 깨끗한 동일 revision을 쓰려면 `-SourceRoot`를 지정합니다.
실제 키는 외부 properties에서 주입하고 빌드 폴더도 Git·OneDrive 밖에 생성합니다.

앱의 private `files/field-diagnostics/field-0.jsonl`부터 `field-3.jsonl`에
SDK 초기화·해제 호출 위치, product/USB/activity 이벤트, FC 첫 오류·회복과 상태가 기록됩니다.
최대 4개×2MiB 순환 기록이며 raw 오류문·토큰·좌표·영상은 새 기록에 넣지 않습니다.

```powershell
adb devices -l
.\scripts\collect-phone-diagnostics.ps1 -Serial '<ADB 목록의 장치 ID>'
```

이 기록기는 내부 저장소에 쓰므로 RC에 USB로 연결하는 동안 PC 연결이 없어도 기록됩니다.
무선 ADB는 선택사항이며 자동 활성화하지 않습니다. PC 수집 파일도 Git에 넣지 마세요.

### 미해결 사항과 인계 범위

실제 FC `REQUEST_HANDLER_NOT_FOUND`의 최초 발생 원인은 아직 입증되지 않았습니다.
진단 앱은 상태 변화를 보존하기 위한 변경이며 SDK 근본 해결 완료나 실비행 성공을 뜻하지
않습니다. 자동 SDK 복구·강제 지상 판정·자동 이륙 재시도는 추가하지 않았습니다.
실제 영상 수신이 정체된 경우에만 지상에서 초기 스트림을 한 번 교체하는 PC 동작과,
FC 상태가 나쁜 경우 임무 접수 전에 막는 동작을 구분합니다.

개인화 MAI 이미지와 이전 VLM 실험 산출물은 로컬 인계 묶음의 실험 자료입니다.
현재 UI에 개인화 workflow가 연결된 것은 아니며 원본 `public\monitors`는 유지합니다.
별도 Astra 원인 분석은 완료 보고서가 확보되지 않았으므로 결과가 있다고 가정하지 마세요.

## 이전 환경 안내 (참고용)

이 브랜치는 PC 드론 제어, 7개 HTTP 도구, Speech 연결, Android 수정 소스,
태그 없는 COEX 시험 코드와 가상 시나리오를 포함합니다. 클론만으로 실비행을
시작할 수는 없습니다. 아래 개인 설정과 장치 연결은 새 PC에서 준비해야 합니다.

## 1. 저장소와 실행 환경

아래 `feat/drone`은 이전 통합 브랜치입니다. 현재 인계에는 위 명령을 사용합니다.

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

- 휴대폰 APK: `com.ms.voice`, `5.18-connectivity.20260913.3` (`.20260910.6`의 첫 연결·모터 상태 갱신을 유지하고 SDK/USB/FC 진단 기록을 추가). `live.py`의 `BUILD_ID`와 정확히 같아야 하며, 다르면 실제 출발이 거절됩니다.
- PC의 실제 `config.local.json`: 휴대폰 IP, 앱 arm token, 카메라·태그 실측/보정값.
- 실제 site 설정과 공유 PC API token, Azure 로그인 또는 개인 자격 증명.
- 실제 이미지 분석용 Azure endpoint/deployment 설정.
- Android를 새로 빌드할 때 필요한 DJI API key 및 서명 키/속성.

실제 값은 별도 비공개 경로로 옮깁니다. 새 PC와 휴대폰을 같은 네트워크에 연결하고,
현재 휴대폰 IP와 경로를 갱신합니다. 기존 장소의 측정값이 새 장소를 증명하지는
않으므로 현장 확인 플래그를 임의로 켜지 않습니다.

같은 휴대폰에 필요한 `.6` APK가 이미 설치되어 있으면 PC를 바꾼다는 이유로
Android를 다시 빌드할 필요는 없습니다. 최신 현장 기록은
[FIELD_STATE_20260910.md](FIELD_STATE_20260910.md)를 따릅니다. 그 기록은 새 PC의
네트워크·카메라 보정·앱 연결까지 확인됐다는 뜻은 아닙니다. APK 파일은 Git에
포함돼 있지 않습니다.

현장 단독 순찰은 `trials/START_TAG_SHUTTLE.ps1`과
`pc/config.tag-shuttle.local.json`을 사용합니다. 기본 프로필은 높이 1.5m,
벽 배치(왼쪽부터) 3·2·1·6, 첫 방문 촬영 구간 85~95%입니다.
이 경로는 HTTP 도구 서비스의 기존 1.4m 프로필과 별개입니다. 파일을 받았다는
이유만으로 단독 촬영 로직이 Speech 도구 7개에 연결되지는 않습니다.

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
