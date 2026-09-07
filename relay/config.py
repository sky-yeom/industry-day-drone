"""Voice Live configuration for the drone survey dashboard.

Every value was verified live against the industry-day-drone Foundry resource.
Override any of them with environment variables.

Note there is no model *deployment* involved. Voice Live is fully managed:
the model is provisioned by the service, so nothing here consumes OpenAI
deployment quota and nothing needs to exist under Foundry > Deployments.
"""

from __future__ import annotations

import os

# --- Azure AI Foundry / Voice Live -------------------------------------------

RESOURCE = os.getenv("VOICE_LIVE_RESOURCE", "industry-day-drone-boot-resource")
REGION = os.getenv("VOICE_LIVE_REGION", "southeastasia")

# 2026-07-15 carries the newest session fields. 2026-04-10 also works and is
# the only version that accepts model=azure-realtime, if you switch to it.
API_VERSION = os.getenv("VOICE_LIVE_API_VERSION", "2026-07-15")

# gpt-realtime    -> speech-to-speech + the full Azure TTS voice catalogue
# azure-realtime  -> Azure's finetune; only accepts azure-realtime-native voices
MODEL = os.getenv("VOICE_LIVE_MODEL", "gpt-realtime")

# 네이티브 GPT Realtime 보이스("openai" 타입)는 모델이 오디오 토큰을 직접
# 생성한다. 텍스트를 별도 TTS 엔진에 넘기지 않는 진짜 speech-to-speech라서
# 억양이 의미를 따라가고 첫 소리도 빨리 나온다.
# 이 리소스에서 측정: marin 461ms vs en-US-AvaNeural(azure-standard) 644ms.
#
#   openai          -> shimmer, marin, cedar, alloy, echo  (네이티브, 한국어 가능)
#   azure-standard  -> ko-KR-SunHiNeural 등                (Azure TTS, 한 단계 더 거침)
VOICE_NAME = os.getenv("VOICE_LIVE_VOICE", "shimmer")
VOICE_TYPE = os.getenv("VOICE_LIVE_VOICE_TYPE", "openai")

WS_URL = (
    f"wss://{RESOURCE}.cognitiveservices.azure.com/voice-live/realtime"
    f"?api-version={API_VERSION}&model={MODEL}"
)

TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"

# --- Turn detection -----------------------------------------------------------

# 세션 노트: "사람의 문장이 끝났을거라 생각하는게 350ms을 잡아먹는다. 근데 어디서
# 끊을지에 대한 silence duration동안 조금더 지켜본다 (이 2개가 합쳐지면 500ms정도
# 딜레이) 보통은 300ms로 잡는다"
#
# 이 값이 체감 지연의 가장 큰 덩어리다. 더 줄이면 한국어 종결어미("~할까요")가
# 잘려서 오히려 대화가 끊긴다.
SILENCE_DURATION_MS = int(os.getenv("VOICE_LIVE_SILENCE_MS", "300"))

# azure_semantic_vad는 문서상 영어 위주다. 한국어는 multilingual 쪽이 종료 시점을
# 제대로 잡는다. (지원 언어: en, es, fr, it, de, ja, pt, zh, ko, hi)
VAD_TYPE = os.getenv("VOICE_LIVE_VAD_TYPE", "azure_semantic_vad_multilingual")
VAD_LANGUAGES = os.getenv("VOICE_LIVE_VAD_LANGUAGES", "ko").split(",")

# 발화로 인정하는 문턱값. 기본 0.5는 조용한 방에서는 괜찮지만 행사장처럼 시끄러운
# 곳에서는 잡음이 발화로 잡혀서 없는 말이 전사된다. 하지만 0.6은 "네", "1번" 같은
# 짧고 조용한 대답을 발화로 아예 인정하지 않고 흘려버리는 문제가 더 커서, 0.5로
# 낮춰 짧은 대답의 인식률을 우선한다.
VAD_THRESHOLD = float(os.getenv("VOICE_LIVE_VAD_THRESHOLD", "0.5"))

# 발화 시작 앞쪽으로 얼마나 더 포함할지. API 2026-04-10부터 semantic VAD 기본값이
# 420이다. 너무 낮추면 첫 음절이 잘려 인식률이 떨어진다.
PREFIX_PADDING_MS = int(os.getenv("VOICE_LIVE_PREFIX_PADDING_MS", "420"))

# 발화로 인정하는 최소 길이. 이 시간보다 짧은 소리는 아예 발화로 치지 않는다.
# semantic VAD 기본값은 80ms인데, 그 정도면 문 닫는 소리나 기침도 발화로 잡혀서
# 잡음 구간이 전사 모델로 넘어가고 없는 말이 지어진다("쭈쭈쭈쭈!").
# 100ms는 짧은 잡음을 어느 정도 거르면서 "네", "1" 같은 한 음절 대답을 더
# 안정적으로 통과시키는 균형점이다. 시끄러운 행사장에서는 140ms 이상으로 올린다.
# 이 값은 응답 지연과는 무관하다. 발화 시작 판정에만 쓰인다.
SPEECH_DURATION_MS = int(os.getenv("VOICE_LIVE_SPEECH_DURATION_MS", "100"))

# --- 전사(transcription) -------------------------------------------------------
#
# 주의: MODEL이 gpt-realtime일 때 모델은 오디오를 직접 듣고 도구 호출 여부를
# 판단한다 (진짜 speech-to-speech). 아래 TRANSCRIPTION_MODEL/PROMPT는 브라우저에
# 보여줄 자막/로그를 만드는 별도 경로일 뿐, 모델이 무엇을 "들었다고" 판단해서
# 도구를 호출하는지에는 영향을 주지 않는다. 즉 "인식이 안 된다"는 문제는 여기나
# 위 VAD 값을 더 튜닝한다고 고쳐지지 않는다 — 실제 원인은 대개 프롬프트/확인
# 절차 쪽에 있다 (relay/tools.py의 SYSTEM_PROMPT, 되묻기-확인 절차 참고).

# whisper-1은 무음이나 잡음 구간에서 없는 말을 지어내는 것으로 악명 높다.
# ("쭈쭈쭈쭈!" 같은 환청) gpt-4o-transcribe는 같은 상황에서 훨씬 안정적이다.
TRANSCRIPTION_MODEL = os.getenv("VOICE_LIVE_TRANSCRIPTION_MODEL", "gpt-4o-transcribe")

# 전사 모델에 도메인 어휘를 미리 알려주면 "모니터 3"을 "모니터 세"처럼 잘못 듣는
# 경우가 줄고, 잡음을 엉뚱한 단어로 채우는 것도 억제된다. "네", "1번" 같은 한두
# 음절 대답은 특히 잘못 전사되거나 통째로 누락되기 쉬워서 숫자+번 표기를
# 명시적으로 나열해 우선순위를 높인다.
TRANSCRIPTION_PROMPT = os.getenv(
    "VOICE_LIVE_TRANSCRIPTION_PROMPT",
    "드론 조사 관제 대화입니다. 자주 나오는 말: 모니터 1, 모니터 2, 모니터 3, "
    "첫번째, 첫 번째, 첫째, 두번째, 두 번째, 둘째, 세번째, 세 번째, 셋째, "
    "일번, 한 번, 이번, 두 번, 삼번, 세 번, 1번, 2번, 3번, "
    "네, 예, 응, 맞아요, 아니요, "
    "경로, 확정, 다시, 취소.",
)

# --- Audio --------------------------------------------------------------------

SAMPLE_RATE = 24000

# --- Server -------------------------------------------------------------------

HOST = os.getenv("RELAY_HOST", "127.0.0.1")
PORT = int(os.getenv("RELAY_PORT", "8080"))

# The Next.js dev server. Next picks the next free port when 3000 is taken,
# so match any localhost port rather than pinning one.
WEB_ORIGIN_REGEX = os.getenv(
    "RELAY_WEB_ORIGIN_REGEX",
    r"http://(localhost|127\.0\.0\.1)(:\d+)?",
)
