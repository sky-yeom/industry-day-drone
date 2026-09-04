# 드론 조사 대시보드 — Voice Live

Industry Day 데모: Azure AI Voice Live API로 음성 제어하는 Next.js 대시보드.
드론이 둘러볼 모니터를 말로 고르면 경로가 그려지고, 확정 후 조사를 실행하면
지도와 이상 징후 갤러리가 실시간으로 갱신된다.

## 경로 계획 흐름

1. 에이전트가 **어느 모니터부터 갈지** 묻는다
2. 사용자가 첫 번째를 고른다 → 두 번째를 묻는다
3. 사용자가 두 번째를 고른다 → **남은 한 곳은 시스템이 자동으로 마지막에 추가**
4. 전체 경로를 읽어주고 **이대로 확정할지 묻는다**
5. 사용자가 동의하면 확정, 이후 조사 실행

"첫번째", "아무거나", 알아듣지 못한 소리에는 **도구를 부르지 않고 다시 묻는다.**
임의로 모니터를 고르거나 경로를 초기화하지 않는다.

## 구조

```
브라우저 (Next.js :3000)
  FlightPathMap / AnomalyGallery / ChatPanel   ← 렌더링만
  VoiceControl + lib/voiceClient.ts            ← 마이크 캡처, PCM 재생
        │  ws://127.0.0.1:8080/ws              (평문 WS, 자격증명 없음)
        ▼
  relay/server.py (:8080)
        Entra 토큰 보관 · 경로 상태 소유 · 도구 실행
        │  wss + Bearer
        ▼
  Voice Live  (gpt-realtime, southeastasia)
```

**릴레이가 필요한 이유.** Foundry 리소스가 `disableLocalAuth=true`라서 Voice
Live는 Entra 베어러 토큰만 받는다. 브라우저는 WebSocket에 `Authorization`
헤더를 붙일 수 없고, 붙일 수 있더라도 자격증명을 클라이언트에 내려보내는 건
옳지 않다. 그래서 릴레이가 대신 자격증명을 들고 있다.

**상태를 릴레이가 갖는 이유.** 경로 상태는 React가 아니라 `relay/survey.py`가
소유한다. 도구 호출마다 릴레이가 `route.state`를 밀어주고 대시보드는 그리기만
하므로, 에이전트와 화면이 다른 경로를 보고 있을 수 없다.

## 모델 배포는 필요 없다

Voice Live는 완전 관리형이라 서비스가 모델을 알아서 띄운다. **Foundry >
배포**에서 만들 게 없고, 이 앱은 OpenAI 배포 쿼터를 전혀 쓰지 않는다. 그 화면의
쿼터 오류는 Voice Live와 무관하다.

`gpt-realtime-2.1`은 여기서 쓸 수 없다. 그건 직접 배포하는 Azure OpenAI 모델일
뿐이고, Voice Live의 모델 목록은 별개다.

## 실행

프로세스 두 개가 모두 떠 있어야 한다.

**1. 릴레이**

```powershell
cd relay
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
az login                      # DefaultAzureCredential이 CLI 토큰을 읽는다
$env:PYTHONIOENCODING="utf-8" # 없으면 한국어 출력이 cp949에서 깨진다
.\.venv\Scripts\python.exe server.py
```

**2. 대시보드**

```powershell
npm install
npm run dev
```

<http://localhost:3000>에서 **세션 시작**을 누르고 마이크를 허용한 뒤 말한다:

> "모니터 2부터 갈게요." → "그다음은 모니터 1이요." → "네, 확정해주세요."
> → "조사 시작해주세요."

텍스트 입력도 같은 에이전트를 거친다.

### 스모크 테스트

마이크 없이 전체 경로(도구, 상태 전이, 자동 3번째, 이상 징후)를 점검한다:

```powershell
cd relay
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe smoke_test.py
```

## TTS가 아니라 네이티브 오디오

기본 보이스는 **네이티브 GPT Realtime 보이스**(`shimmer`, 타입 `openai`)다.
모델이 오디오 토큰을 직접 만들어내는 진짜 speech-to-speech라서, 텍스트를 별도
TTS 엔진에 넘기지 않는다. 억양이 의미를 따라가고 첫 소리도 빨리 나온다.

이 리소스에서 3회씩 측정한 TTFA 중앙값(`response.create` → 첫 오디오):

| 보이스 | 타입 | TTFA |
| --- | --- | --- |
| `shimmer` | `openai` (네이티브) | **485 ms** |
| `marin` | `openai` (네이티브) | 500 ms |
| `ko-KR-SunHiNeural` | `azure-standard` (TTS) | 641 ms |
| `azure-realtime` + `sunhi` | 네이티브 | 656 ms |
| `azure-realtime` + `hyunsu` | 네이티브 | 733 ms |

세션 노트는 `azure-realtime`이 "리스판드는 빠름"이라고 적고 있지만, 이 리소스에서
직접 재보니 `gpt-realtime` + 네이티브 보이스가 150~250ms 더 빨랐다. 노트가 쓰인
이후 `gpt-realtime` 쪽이 개선된 것으로 보인다.

`ko-KR-SunHi:MAI-Voice-2-Flash` 같은 한국어 MAI 보이스는 `session.update`는
통과하지만 실제로 오디오가 나오지 않는다. 이 리전에서는 쓸 수 없다.

한국어 커스텀 보이스나 특정 화자가 꼭 필요할 때만 Azure TTS로 바꾼다:

```powershell
$env:VOICE_LIVE_VOICE="ko-KR-SunHiNeural"
$env:VOICE_LIVE_VOICE_TYPE="azure-standard"
```

### 잡음에서 없는 말이 전사되는 문제

`whisper-1`은 무음이나 잡음 구간에서 없는 말을 지어내는 것으로 악명 높다.
실제로 "쭈쭈쭈쭈!", "You suck." 같은 환청이 대화창에 찍힌다. 세 겹으로 막는다.

1. **전사 모델을 `gpt-4o-transcribe`로.** 같은 상황에서 훨씬 안정적이다.
   `prompt`에 도메인 어휘("모니터 1, 경로, 확정…")를 넣어 잡음을 엉뚱한 단어로
   채우는 것도 억제한다.
2. **VAD를 덜 예민하게.** `threshold` 0.5 → **0.6**,
   `speech_duration_ms` 80 → **140**. 문 닫는 소리나 기침이 발화로 잡히지 않는다.
   행사장처럼 시끄러운 곳이면 `VOICE_LIVE_VAD_THRESHOLD=0.7`까지 올린다.
3. **화면단 필터.** 그래도 새어나오는 "한 음절 4회 이상 반복" 형태는
   `looksHallucinated()`가 걸러낸다. ("네네네" 같은 자연스러운 반복은 통과)

## 지연(latency) 튜닝

체감 지연은 대부분 보이스가 아니라 **턴 감지**에서 나온다. 사용자가 말을 멈춘 뒤:

```
350ms (문장이 끝났다고 판단)  +  300ms (silence_duration)  →  speech_stopped
                                                          →  ~485ms (첫 오디오)
```

- **`azure_semantic_vad_multilingual` + `languages: ["ko"]`** 를 쓴다.
  기본 `azure_semantic_vad`는 문서상 영어 위주라, 한국어 종결어미에서 종료 시점을
  잘못 잡는다. 지원 언어는 en, es, fr, it, de, ja, pt, zh, **ko**, hi.
- **`silence_duration_ms`는 300ms** 가 하한선이다. 더 줄이면 "~할까요"가 잘린다.
- **`prefix_padding_ms`는 420** (semantic VAD 기본값). 낮추면 첫 음절이 잘려서
  인식률이 떨어진다.
- **`remove_filler_words: true`** 로 "음", "어" 때문에 잘못 끼어들기 판정되는 걸
  줄인다.

환경 변수로 현장에서 바로 조정할 수 있다:

```powershell
$env:VOICE_LIVE_VAD_THRESHOLD="0.7"       # 더 시끄러운 곳
$env:VOICE_LIVE_SILENCE_MS="250"          # 더 빠른 응답 (잘림 위험)
$env:VOICE_LIVE_SPEECH_DURATION_MS="200"  # 짧은 잡음을 더 걸러냄
```

`speech_duration_ms`는 **응답 속도와 무관하다.** "이 정도 길이는 되어야 발화로
친다"는 문턱일 뿐이라, 올려도 답변이 느려지지 않는다. 조용한 방이면 낮춰서
반응을 예민하게, 시끄러운 곳이면 올려서 잡음 전사를 막는 쪽으로 쓴다.

| 값 | 성격 |
| --- | --- |
| 80 (기본값) | 기침, 문 닫는 소리도 발화로 잡힘 |
| **140 (현재)** | 짧은 잡음은 거르고 "네", "1"은 통과 |
| 200+ | 아주 시끄러운 현장용. 짧은 대답이 씹힐 수 있음 |

### 보이스별 TTFA (도구 없이 순수 발화, 각 3회 중앙값)

| 모델 + 보이스 | TTFA |
| --- | --- |
| **`gpt-realtime` + `shimmer`** | **515 ms** |
| `azure-realtime` + `sunhi` | 1000 ms |
| `azure-realtime` + `hyunsu` | 1235 ms |

세션 노트는 `azure-realtime`(선희/현수)이 "리스판드는 빠름"이라고 적고 있지만,
이 리소스에서 직접 재보니 정반대로 2배 느렸다. `sunhi`도 도구 호출 자체는 정상
동작하므로 목소리 취향으로 바꿀 수는 있다. 다만 TTFA 1초 목표를 아슬아슬하게
걸치므로 데모에서는 `shimmer`를 유지하는 편이 안전하다.

### 리전

GPT Realtime은 한국에 없다. 서울에서 잰 왕복 시간:

| 리전 | RTT | Voice Live gpt-realtime |
| --- | --- | --- |
| `southeastasia` (싱가포르) | **~105 ms** | 지원 |
| `koreacentral` | 더 가까움 | **미지원** |
| `japaneast` | 더 가까움 | **미지원** |

한국에서 가장 가까우면서 `gpt-realtime` 계열을 지원하는 곳은 싱가포르뿐이다.
릴레이를 싱가포르로 옮겨도 브라우저가 한국에 있는 한 한국↔싱가포르 구간은
그대로라서 이득이 없다. 로컬 릴레이로 충분하다.

## 말투가 기계처럼 들리지 않게 하는 법

가장 큰 원인은 **도구가 완성된 문장을 돌려주고 모델이 그걸 그대로 읽는 것**이다.
그래서 이 프로젝트의 도구는 문장이 아니라 `facts`(사실)와 `ask`(다음에 물을 것)만
돌려주고, 프롬프트가 "그대로 읽지 말고 네 말로 바꿔 말하라"고 못박는다.

```python
# 나쁨 - 모델이 이 문장을 그대로 낭독한다
return {"speech": "첫 번째 경유지는 모니터 2입니다. 두 번째로 갈 곳을 말씀해 주세요."}

# 좋음 - 모델이 "네, 모니터 2부터 갈게요. 다음은 어디로 갈까요?"처럼 바꿔 말한다
return {"facts": "첫 번째 경유지는 모니터 2",
        "ask": "두 번째로 갈 곳을 물어볼 것. 선택지는 모니터 1, 모니터 3"}
```

## 어디를 고치면 되나

| 무엇 | 파일 |
| --- | --- |
| 시스템 프롬프트, 인사말, 도구 정의 | `relay/tools.py` |
| 경로 로직, 자동 3번째, 이상 징후 생성 | `relay/survey.py` |
| 모델, 보이스, 리전, VAD 튜닝 | `relay/config.py` |
| Voice Live에 보내는 세션 설정 | `relay/server.py` → `build_session()` |
| 마이크 캡처 / 재생 / 끼어들기 | `lib/voiceClient.ts`, `public/audio-worklets.js` |

프롬프트는 접속할 때 `session.update`로 한 번만 전송되므로, 고친 뒤에는
**릴레이를 재시작**해야 한다.

## 도구 추가하기

1. `relay/tools.py`의 `TOOLS`에 스키마를 추가
2. `dispatch()`에 분기를 추가
3. `relay/survey.py`의 `SurveySession`에 구현

`{"ok": bool, "facts": str, "ask": str}`를 돌려준다. `facts`에는 실제로 일어난
일만 적는다. 상태 코드만 돌려주면 모델이 결과를 지어낸다.

## 알아둘 함정

- **인사말이 끝날 때까지 마이크는 음소거된다.** 오디오 그래프가 연결되는 즉시
  마이크가 흐르는데, 인사말 도중 VAD가 발동하면
  `conversation_already_has_active_response`로 인사말이 죽는다.
- **`getUserMedia` 뒤에 `AudioContext`를 resume 해야 한다.** 권한 팝업이 클릭
  제스처를 먹어서 Chrome이 suspended 상태로 돌려주고, 인사말이 허공에 재생된다.
- **말풍선 순서.** Whisper 전사는 약 1초 뒤에 오는데 에이전트는 485ms 만에 답한다.
  그대로 두면 질문보다 답이 위에 그려지므로, `speech_started` 시점에 사용자
  말풍선 자리를 미리 잡아둔다.
- **모호한 답에 도구를 부르지 않게 해야 한다.** 도구 스키마가 enum이라 모델이
  "첫번째" 같은 말을 임의로 `monitor-1`로 바꿔 넣는다. 프롬프트에서 되묻도록
  막고, `resolve_monitor()`가 순서를 가리키는 말을 한 번 더 거절한다.
- **응답은 한 번에 하나.** 생성 중에 `response.create`를 또 보내면 거부되므로 그
  동안 입력창을 잠근다.
- **`az login`이 유효해야 한다.** 공용 데모 장비라면 CLI 자격증명 대신 관리 ID나
  서비스 주체를 쓴다.
- **한국어 콘솔 출력.** `PYTHONIOENCODING=utf-8` 없이 실행하면 Windows cp949에서
  `UnicodeEncodeError`가 난다.

## 이전 목업 에이전트

`lib/mockAgent.ts`는 음성 도입 전의 결정론적 에이전트다. 참고용으로 남겨뒀고
아무 데서도 import하지 않는다. 음성 도입 전 `app/page.tsx`는 커밋 `5fe12d4`에
그대로 있다.
