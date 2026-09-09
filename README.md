# 긴급 구조 작전 — Emergency Triage

Industry Day 데모: 세 사람의 위급함을 판단하고 드론의 방문 순서를 정하는
한국어 구조 체험입니다. Azure AI Voice Live의 실시간 음성 대화로 참여하며
프롬프트 작성·확인, 경로 선택, 출발은 음성으로 조작합니다.
네 탭은 클릭해서 자유롭게 살펴볼 수 있으며, 출발 후 드론은 자동으로 이동·촬영·분석합니다.

**가상의 훈련 시나리오입니다.** 이미지에서 대상자를 찾는 것을 구조로 간주하는
데모 규칙이며, 실제 구조 활동이나 의료 판단을 대신하지 않습니다.

## 시나리오와 구조 조건

프롬프트 화면에서는 세 건의 긴급 신고와 한 대의 드론이라는 전체 상황을 듣고,
참고 인물 사진을 보고 찾을 사람의 외형을 말합니다. 세 현장의 대상자는 서로
다른 사람이지만 모두 **초록색 티셔츠와 갈색 머리**라는 특징을 공유합니다.
참가자의 말을 되읽어 확인한 뒤 비행 경로 화면에서 각 현장의 긴급도를 비교합니다.

| 지점 | 상황 | 긴급도 |
| --- | --- | --- |
| 모니터 1 | 바다에 빠져 물에 떠 있으려고 힘겹게 버티는 사람 | 높음 |
| 모니터 2 | 잔해 아래에 갇혀 가벼운 부상을 입은 사람 | 세 경우 중 상대적으로 낮음, 구조 시한은 있음 |
| 모니터 3 | 불이 난 집에 갇힌 사람 | 가장 높음 |

게임의 권장 순서는 **불길 → 바다 → 잔해 (3 → 1 → 2)**입니다.
이는 가상 시나리오의 규칙이며 일반적인 의료 우선순위나 실제 생존 시간을 뜻하지 않습니다.
참가자가 다른 순서를 고르더라도 시스템이
몰래 고치지 않습니다. 첫 번째와 두 번째 지점을 정하면 남은 곳을 마지막에
추가하고, **출발에 동의한 순간부터 실제 시간이 흐릅니다.**

도착만으로는 구조되지 않습니다. 해당 지점의 **촬영 이미지에서 대상자를 확인한
분석 결과가 구조 시한 전에 도착해야** 구조가 인정됩니다. 이동·촬영·분석·재촬영
중에도 시간이 흐르며, 촬영이 빨라도 분석 완료가 늦으면 구조 시한을 놓칩니다.
시한과 정확히 같은 순간에 완료된 탐지도 시한 초과입니다.

확정한 외형 설명도 탐지 조건입니다. "녹색 옷"이나 "갈색 머리"처럼 일부 특징만
말해도 맞으면 탐지할 수 있지만, 빨간 티셔츠·금발·"초록색이 아닌 옷"처럼
명시한 특징이 다르면 대상자를 발견한 것으로 처리하지 않습니다.
틀린 설명을 대신 고치거나 빠진 정답 특징을 덧붙이지 않습니다.

| 결과 | 판정 |
| --- | --- |
| 구조 완료 | 시한 전에 대상자를 확인했고 부상이 없는 경우 |
| 부상 상태로 구조 | 시한 전에 확인했지만 원래 부상이 있거나 상태가 악화된 경우 |
| 구조 시한 초과 | 구조가 확인되지 않은 채 구조 시한이 지난 경우 |

모니터 2는 처음부터 부상이 있으므로 제때 도착해도 **부상 상태로 구조**됩니다.
두 가지 구조 성공 결과 모두 구조 인원에 포함됩니다.

정상 이미지에서 사람을 찾지 못하면 한 번 더 촬영·분석하고 다음 지점으로
이동합니다. 아직 시한이 남은 사람을 즉시 시한 초과로 처리하지는 않습니다.
카메라·모델의 기술적 오류는 명시적으로 전체 시계를 일시 정지시키고, 참가자가
재시도하거나 임무를 중단하도록 안내합니다.

## 이미지 입력과 탐지 모드

현재 비행과 카메라는 **시나리오 이미지 기반 모의 장치**입니다.
`public/monitors/`의 직접 제작한 SVG를 PNG로 렌더링했으며, 화면과 분석기에
동일한 PNG 픽셀을 전달합니다. 빈 장면과 다른 대상자 이미지도 회귀 테스트용으로
포함합니다.

| `TRIAGE_MODE` | 이미지 판정 | 필요한 연결 |
| --- | --- | --- |
| `mock` (기본) | 음성에서 추출한 외형 조건과 원본 픽셀을 비교하는 결정론적 모의 탐지 | 로컬 릴레이, 음성용 Voice Live |
| `azure` | 촬영한 실제 이미지 바이트를 Azure 멀티모달 모델로 분석 | 별도 Azure 이미지 분석 배포 |

모드 전환은 릴레이 환경 변수로 명시적으로 설정합니다. Azure 설정이 없거나
분석이 실패해도 모의 결과로 자동 대체하지 않습니다. 음성 사용 여부는 탐지
모드와 별개입니다. 참가자 체험은 음성 세션으로 시작하며, 모의 탐지에서도
마이크 권한과 Voice Live 인증이 필요합니다.

모의 모드는 Voice Live가 발화에서 추출한 상의 색·종류와 머리색 조건을
사용합니다. 생략한 특징은 제한하지 않고, 부정·선택 조건도 보존합니다.
안경 등 지원하지 않는 추가 외형 조건은 버리지 않고 모의 탐지에서 미확인으로
처리합니다. 실제 컴퓨터 비전이나 신원 인식은 아닙니다.
Azure 모드에서는 실제 픽셀의 같은 후보가 참가자의 원래 설명과 구조 대상의
공통 외형을 **모두** 만족해야 탐지가 인정됩니다.

실제 드론 연결은 추후 작업입니다. `relay/camera.py`의 캡처 어댑터를 실제
장치의 이미지 수신 방식에 연결하면 됩니다. 프레임은 지점·캡처 ID와 연결되어야
하며, 모니터 번호만 받거나 클라이언트가 보낸 성공 여부만 믿고 구조 처리하면
안 됩니다. 실제 비행 제어, 스트림 프로토콜, 생체 신원 인식은 구현 범위에
포함되지 않습니다.

## 구조

```text
브라우저 — Next.js :3000
  브리핑 / 경로 / 카운트다운 / 촬영 이미지 / 구조 결과
  VoiceSession: 마이크 캡처와 네이티브 음성 재생
        │ 로컬 WebSocket, 자격 증명 없음
        ▼
릴레이 — FastAPI :8080
  SurveySession: 경로·시계·구조 판정의 단일 원본
  MissionRunner: 자동 이동·촬영·분석, 독립적인 구조 시한 처리
        ├── 캡처 어댑터 → PNG 이미지 → 모의 탐지 또는 Azure 멀티모달 분석
        └── 음성 사용 시 Azure AI Voice Live
```

Azure 자격 증명은 릴레이에만 보관합니다. 브라우저는 릴레이의 `route.state`
스냅샷을 표시하며 구조 성공 여부나 시한을 별도로 판정하지 않습니다.
재접속은 새 임무입니다. 연결이 끊기거나 초기화된 실행의 비동기 작업은 취소하며,
이전 이미지 분석 결과를 새 임무에 적용하지 않습니다.

## 빠른 시작

다른 컴퓨터에서 다시 세팅할 때는 아래 명령으로 시작합니다. 자세한 수동 절차와
Azure 연동은 이어지는 "실행" 절을 참고하세요.

```bash
npm run setup    # relay/.venv 생성, relay/requirements.lock.txt 설치, npm install
npm run dev:all  # 릴레이(mock)와 Next.js 대시보드를 한 번에 실행
```

`.env.example` → `.env.local`, `relay/.env.example` → `relay/.env`로 복사해 기본값을
바꿀 수 있습니다. Node 버전은 `.nvmrc`(>=20.9.0), 릴레이 파이썬 의존성은
`relay/requirements.lock.txt`(고정 버전)로 관리합니다. Windows에서는
`scripts/setup.sh` 대신 README의 수동 절차(아래 "1. 릴레이" PowerShell 예시)를 따르세요.

## 실행

대시보드와 릴레이 두 프로세스가 모두 필요합니다. `npm run setup && npm run dev:all`로
한 번에 실행할 수도 있습니다(위 "빠른 시작" 참고). 아래는 각 단계를 수동으로 실행하는
방법입니다.

### 1. 릴레이

macOS/Linux:

```bash
python3 -m venv relay/.venv
relay/.venv/bin/python -m pip install -r relay/requirements.txt
TRIAGE_MODE=mock relay/.venv/bin/python relay/server.py
```

Windows PowerShell:

```powershell
python -m venv relay/.venv
.\relay\.venv\Scripts\python.exe -m pip install -r relay/requirements.txt
$env:PYTHONIOENCODING="utf-8"
$env:TRIAGE_MODE="mock"
.\relay\.venv\Scripts\python.exe relay/server.py
```

체험 전에 Azure CLI에서 `az login`을 실행합니다.

### 2. 대시보드

```bash
npm install
npm run dev
```

<http://localhost:3000>에서 **시작**을 누르고 마이크를 허용한 뒤 말합니다:

> 참고 사진을 보고 외형 설명 → 설명 확인에 동의 → "불이 난 집부터 가자."
> → "바다에 빠진 사람을 다음으로." → 경로를 듣고 "출발해."

화면은 **프롬프트 → 비행 경로 → 드론 이미지 → 결과** 순서로 자동 전환되며,
언제든 탭을 클릭하거나 화살표 키로 살펴볼 수 있습니다. 탭 이동은 임무를
변경하거나 출발시키지 않습니다. 음성 전사와 답변은 오른쪽 음성 시각화 아래에
표시됩니다. 브리핑은 한 페이지에 표시하고, 확정된 프롬프트 영역은 입력 전에도
빈 상태로 유지합니다. 각 탭은 화면 높이에 맞춰 표시하고, 긴 프롬프트·최종 설명은
이전/다음 페이지로 읽습니다. 경로 화면에는 현장 이미지와 상황만 표시하며
구조 시한·남은 시간·경과 시간은 숨깁니다. 출발 전 음성 안내도 정확한 시간을
알려주지 않습니다. 시간은 **출발한 뒤 드론 이미지 탭에서만** 확인할 수 있고,
탭을 미리 열어도 카운트다운은 나타나지 않습니다. 촬영 기록은 이전/다음 촬영으로 확인합니다.
음성 시각화와 마이크 버튼은 겹치지 않게 분리하고 대화 영역은 패널 하단에 둡니다.
작전이 끝나면 결과 화면의 **처음으로** 버튼으로 시작 화면에 돌아갈 수 있습니다.

### Azure 멀티모달 이미지 분석

이미지 입력과 구조화된 응답을 지원하는 Azure OpenAI 모델을 배포한 뒤 릴레이
프로세스에 설정합니다. 아래 엔드포인트·배포 이름은 설명용이며 실제 리소스의
값으로 바꿔야 합니다.

```bash
export TRIAGE_MODE=azure
export AZURE_VISION_ENDPOINT="https://YOUR-RESOURCE.openai.azure.com"
export AZURE_VISION_DEPLOYMENT="YOUR-VISION-DEPLOYMENT"
az login
relay/.venv/bin/python relay/server.py
```

기본적으로 Entra 인증을 사용합니다. API 키를 쓰는 경우에는
`AZURE_VISION_API_KEY`를 프로세스 환경에만 설정하고 소스·브라우저에 넣지
마세요. 현재 어댑터의 `AZURE_VISION_API_VERSION`은 `v1`만 지원합니다.
다른 API 버전은 오류로 안내하며 자동 대체하지 않습니다.

**Voice Live와 이미지 분석 배포는 별개입니다.** Voice Live의 관리형 음성 모델은
직접 Azure OpenAI 배포를 만들 필요가 없지만, 이미지 분석기는 별도의 지원 모델
배포와 해당 배포의 쿼터를 사용합니다. 기존 Voice Live 설정만으로 이미지 분석
배포가 자동 생성되지는 않습니다.

정상적인 추론 지연도 게임 시간에 포함됩니다. 분석이 느린 배포에서는 최적
순서라도 시한을 놓칠 수 있으므로, 행사 환경에서 실측한 뒤 공유 시나리오의
시간 값을 조정하세요. 모델이나 카메라가 연결되지 않은 상태를 실제 분석 성공으로
표시하지 않습니다.

## 시나리오 조정

`data/emergency-triage.json`을 TypeScript와 Python이 함께 읽습니다.
브리핑·대상자 묘사·초기 부상·구조 시한·모의 비행 시간·이미지가
이 파일의 단일 정의를 사용합니다.

`injuryWindowMs: 5000` is shared by the relay and frontend. A rescue confirmed
with five seconds or less remaining counts as **부상 상태로 구조**, including
exactly five seconds remaining. A confirmation at the deadline is **too late**.
Initially injured people remain injured even when rescued earlier.

기본 모의 환경은 이동 7초, 촬영 1초, 분석 2초로 설정되어 있습니다.
게임상 구조 시한은 모니터 3이 18초, 모니터 1이 28초, 모니터 2가 45초입니다.
바다의 대상자는 23초부터 상태가 악화되어 제때 탐지해도 부상 상태로 집계됩니다.
따라서 정상 모의 탐지에서 3 → 1 → 2는 세 사람을 모두 살릴 수 있고,
긴급한 사람을 늦게 방문하면 실제 시한을 넘깁니다. 판정 자체를 정답 경로와
문자열 비교하는 방식은 아닙니다.

이미지를 바꿀 때는 PNG와 SVG 원본이 일치하도록 함께 갱신하세요. 실제 Azure
분석은 PNG 픽셀을 사용하며, 모의 모드의 박스 좌표는 `mockBox`로 별도 표시합니다.

설치된 Next.js 이미지 처리 의존성으로 PNG를 다시 만들 수 있습니다:

```bash
node --input-type=module -e 'import sharp from "sharp"; for (const name of ["monitor-1", "monitor-2", "monitor-3", "empty-scene", "wrong-target", "wrong-hair", "reference-person"]) { await sharp(`public/monitors/${name}.svg`).png().toFile(`public/monitors/${name}.png`); }'
```

## 음성 동작

기본 음성은 `gpt-realtime` + `shimmer`의 네이티브 speech-to-speech입니다.
음성 응답을 생성할 때 별도 전사가 끝나기를 기다리지 않습니다.
`gpt-4o-transcribe`의 전사는 대화 로그용 보조 경로입니다.

Korean turn detection uses `azure_semantic_vad_multilingual`, with server echo
cancellation and noise suppression. Automatic interruption is disabled so
assistant chat replies are spoken completely. The microphone stays muted
through each reply, including tool continuations, and reopens only after its
audio playback queue drains—not when generation finishes. Replies still stream
natively without waiting for transcription. The X button can explicitly stop
the session. Tune these settings for the venue:

```bash
export VOICE_LIVE_VAD_THRESHOLD="0.5"
export VOICE_LIVE_SILENCE_MS="300"
export VOICE_LIVE_SPEECH_DURATION_MS="100"
```

도구는 `facts`와 `ask`를 반환하고 음성 모델은 사실을 자연스러운 한국어로
요약합니다. 모호한 발화 때문에 경로를 임의 선택하거나 초기화하지 않습니다.
자동 임무는 음성 모델이 매번 도구를 호출해 주지 않아도 계속 진행됩니다.
첫 인사는 아래 고정 문장을 그대로 읽고 참가자의 답을 기다립니다:

> 안녕하세요. 지금 긴급 구조 요청이 세 건 들어와 있고, 드론 한 대로 모두 찾아내야 합니다. 화면의 참고 사진을 보고, 드론이 어떤 사람을 찾아야 하는지 직접 설명해 주시겠어요?

첫 인사의 요약·의역이나 추가 안내는 허용하지 않습니다. 응답별 안내를
추가할 때에도 세션의 대화 규칙을 유지하며, 에이전트가 참가자의 프롬프트를
대신 만들거나 다른 외형을 지어내지 않도록 합니다.

출발이 확정되면 **“지금 출발했습니다. 경로 따라 탐색과 구조를 시작합니다.”**를
말합니다.

After that announcement finishes playing, the microphone stops and the image
workspace opens. The original upstream voice connection closes, while the
mission WebSocket and authorized audio playback context remain available
silently. Flight, analysis, and countdowns continue.

When results arrive, the browser automatically requests a new output-only
Voice Live connection. The agent summarizes the final rescue counts once,
without another welcome, question, or microphone request. After the final
audio finishes playing, playback and the mission connection close.
If results arrive before departure audio finishes, narration waits for it.
A narration failure displays an error without discarding the visible results.

The image workspace shows the captured image and target box without the
**탐지 근거 / 내 프롬프트** sidebar. The participant's prompt and analysis evidence
remain part of the authoritative rescue decision.

For technical pauses after departure, use **다시 시도 / 작전 중단**.
Starting a replacement voice session is disabled during an active mission.

## 테스트

결정론적 상태·캡처·분석 테스트는 외부 Azure 호출 없이 실행합니다:

```bash
relay/.venv/bin/python -m unittest discover -s relay -p 'test_*.py'
relay/.venv/bin/python -m relay.smoke_test
relay/.venv/bin/python -m relay.smoke_test --wrong-description
npm run lint
npm run build
```

스모크 테스트는 릴레이가 먼저 실행 중이어야 합니다. 실제 Azure 분석 및 음성
경로는 설정된 리소스가 있는 환경에서 별도로 확인해야 합니다. 자동 회귀 확인용
`/ws?voice=0` 연결은 내부 테스트 전용이며 참가자 화면에서는 제공하지 않습니다.

## 주요 파일

| 무엇 | 파일 |
| --- | --- |
| 공유 시나리오·구조 시한·이미지 | `data/emergency-triage.json` |
| 경로·시계·구조 판정 | `relay/survey.py` |
| 자동 임무와 일시 정지·재시도 | `relay/mission_runner.py` |
| 이미지 캡처와 추후 카메라 연결 | `relay/camera.py` |
| Azure 멀티모달 분석과 모의 탐지 | `relay/vision.py` |
| 한국어 인사말·도구·대화 규칙 | `relay/tools.py` |
| 음성·탐지 모델 설정 | `relay/config.py` |
| WebSocket·상태·음성 이벤트 | `relay/server.py`, `lib/voiceClient.ts` |
| UI 상태와 패널 | `app/page.tsx`, `components/` |
| 마이크 캡처·재생 | `public/audio-worklets.js` |

릴레이 프롬프트나 설정을 바꾼 뒤에는 릴레이를 재시작하고 새 임무를 시작하세요.
