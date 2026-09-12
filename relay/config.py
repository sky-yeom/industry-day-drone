"""Voice Live and image analysis configuration for the rescue dashboard.

Voice Live defaults were verified against the industry-day-drone Foundry
resource. Image analysis requires a separately configured deployment.
Override service settings with environment variables.

Voice Live itself is fully managed:
the model is provisioned by the service, so nothing here consumes OpenAI
deployment quota. Azure image analysis separately requires a vision-capable
model deployment that supports structured outputs.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

try:
    from .tool_target import resolve_tool_target
except ImportError:
    from tool_target import resolve_tool_target

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

# --- Emergency triage image analysis (server only) -----------------------------

_tool_target = resolve_tool_target()
DRONE_RUN_MODE = _tool_target.run_mode
TRIAGE_MODE = _tool_target.triage_mode
# Without the explicit selector, flight and image-analysis settings stay independent.
DRONE_CONTROL_MODE = _tool_target.control_mode
DRONE_CONTROL_API_URL = _tool_target.api_url
DRONE_CONTROL_API_TOKEN = _tool_target.api_token
DRONE_CONTROL_TIMEOUT_SECONDS = 5.0
DRONE_CONTROL_TRANSPORT = _tool_target.transport
DRONE_CONTROL_USE_TOOLS = _tool_target.use_tools
DRONE_REMOTE_DEVICE_ID = os.getenv("DRONE_REMOTE_DEVICE_ID", "").strip()
DRONE_REMOTE_DEVICE_TOKEN = os.getenv("DRONE_REMOTE_DEVICE_TOKEN", "").strip()
DRONE_REMOTE_EXECUTION_MODE = (
    "live" if DRONE_RUN_MODE == "real" and DRONE_CONTROL_TRANSPORT == "remote"
    else os.getenv("DRONE_REMOTE_EXECUTION_MODE", "").strip().lower()
)
DRONE_REMOTE_SINGLE_REPLICA = os.getenv("DRONE_REMOTE_SINGLE_REPLICA", "0") == "1"
RELAY_OPERATOR_TOKEN = os.getenv("RELAY_OPERATOR_TOKEN", "").strip()
RELAY_PUBLIC_ORIGIN = os.getenv("RELAY_PUBLIC_ORIGIN", "").strip()
RELAY_LOCAL_DIRECT = os.getenv("RELAY_LOCAL_DIRECT", "0") == "1"
AZURE_VISION_ENDPOINT = os.getenv("AZURE_VISION_ENDPOINT", "").strip()
AZURE_VISION_DEPLOYMENT = os.getenv("AZURE_VISION_DEPLOYMENT", "").strip()
AZURE_VISION_API_VERSION = os.getenv("AZURE_VISION_API_VERSION", "v1").strip()
AZURE_VISION_API_KEY = os.getenv("AZURE_VISION_API_KEY", "").strip()
AZURE_VISION_MAX_COMPLETION_TOKENS = int(os.getenv("AZURE_VISION_MAX_COMPLETION_TOKENS", "1000"))
AZURE_VISION_REASONING_EFFORT = os.getenv("AZURE_VISION_REASONING_EFFORT", "").strip().lower()

# The documented v1 Chat Completions contract supports image input and strict
# JSON schema output on compatible deployments, independently of Voice Live.
VISION_TOKEN_SCOPE = "https://ai.azure.com/.default"
VISION_TIMEOUT_SECONDS = 30
VISION_MAX_IMAGE_BYTES = 4 * 1024 * 1024

# --- Turn detection -----------------------------------------------------------

# 세션 노트: "사람의 문장이 끝났을거라 생각하는게 350ms을 잡아먹는다. 근데 어디서
# 끊을지에 대한 silence duration동안 조금더 지켜본다 (이 2개가 합쳐지면 500ms정도
# 딜레이) 보통은 300ms로 잡는다"
#
# 이 값이 체감 지연의 가장 큰 덩어리다. 더 줄이면 한국어 종결어미("~할까요")가
# 잘려서 오히려 대화가 끊긴다.
SILENCE_DURATION_MS = int(os.getenv("VOICE_LIVE_SILENCE_MS", "300"))

# 실제 공급자 비교에서 semantic VAD가 누락한 짧은 응답을 acoustic VAD가 감지했습니다.
# 의미 기반 종료 감지가 필요하면 azure_semantic_vad_multilingual로 선택할 수 있습니다.
VAD_TYPE = os.getenv("VOICE_LIVE_VAD_TYPE", "server_vad")
VAD_LANGUAGES = os.getenv("VOICE_LIVE_VAD_LANGUAGES", "ko").split(",")

# 발화로 인정하는 문턱값. 기본 0.5는 조용한 방에서는 괜찮지만 행사장처럼 시끄러운
# 곳에서는 잡음이 발화로 잡혀서 없는 말이 전사된다. 하지만 0.6은 "네", "1번" 같은
# 짧고 조용한 대답을 발화로 아예 인정하지 않고 흘려버리는 문제가 더 커서, 0.5로
# 낮춰 짧은 대답의 인식률을 우선한다.
VAD_THRESHOLD = float(os.getenv("VOICE_LIVE_VAD_THRESHOLD", "0.5"))

# 발화 시작 앞쪽으로 얼마나 더 포함할지. API 2026-04-10부터 semantic VAD 기본값이
# 420이다. 너무 낮추면 첫 음절이 잘려 인식률이 떨어진다.
PREFIX_PADDING_MS = int(os.getenv("VOICE_LIVE_PREFIX_PADDING_MS", "420"))

# Azure semantic VAD 선택 시 발화 시작 판정의 최소 길이. 기본값은 80ms이며,
# 실제 상태 변경은 별도의 발화/동의 검증을 통과해야 합니다.
# 행사장 잡음과 실제 짧은 한국어 답변으로 확인한 뒤 환경 변수로 조정합니다.
SPEECH_DURATION_MS = int(os.getenv("VOICE_LIVE_SPEECH_DURATION_MS", "80"))
VOICE_DIAGNOSTICS = os.getenv("VOICE_LIVE_DIAGNOSTICS", "0") == "1"

# --- 전사(transcription) -------------------------------------------------------
#
# 주의: MODEL이 gpt-realtime일 때 모델은 오디오를 직접 듣고 도구 호출 여부를
# 판단한다 (진짜 speech-to-speech). 아래 TRANSCRIPTION_MODEL/PROMPT는 브라우저에
# 보여줄 자막/로그를 만드는 별도 경로이며 네이티브 음성 응답 생성을 지연시키지 않는다.
# 단, relay는 실제 상태를 바꾸는 도구 실행 전에 이 전사로 새 참가자 발화와
# 확인·출발 동의를 검증한다 (relay/voice_turns.py). 모델의 추측만으로는 진행하지 않는다.

# whisper-1은 무음이나 잡음 구간에서 없는 말을 지어내는 것으로 악명 높다.
# ("쭈쭈쭈쭈!" 같은 환청) gpt-4o-transcribe는 같은 상황에서 훨씬 안정적이다.
TRANSCRIPTION_MODEL = os.getenv("VOICE_LIVE_TRANSCRIPTION_MODEL", "gpt-4o-transcribe")

# 전사 모델에 도메인 어휘를 미리 알려주면 "현장 3"을 "현장 세"처럼 잘못 듣는
# 경우가 줄고, 잡음을 엉뚱한 단어로 채우는 것도 억제된다. "네", "1번" 같은 한두
# 음절 대답은 특히 잘못 전사되거나 통째로 누락되기 쉬워서 숫자+번 표기를
# 명시적으로 나열해 우선순위를 높인다.
TRANSCRIPTION_PROMPT = os.getenv(
    "VOICE_LIVE_TRANSCRIPTION_PROMPT",
    "드론 긴급 구조 관제 대화입니다. 들린 말만 전사하고 짧은 대답도 생략하거나 다른 말로 고치지 않습니다. "
    "자주 나오는 말: 현장 1, 현장 2, 현장 3, "
    "첫번째, 첫 번째, 첫째, 두번째, 두 번째, 둘째, 세번째, 세 번째, 셋째, "
    "일번, 한 번, 이번, 두 번, 삼번, 세 번, 1번, 2번, 3번, "
    "네, 예, 응, 어, 엉, 맞아, 맞아요, 오케이, 오키, 콜, 좋아, 가자, 아니요, "
    "구조, 바다에 빠진 사람, 물에 빠진 사람, 익수자, 잔해 아래의 사람, 불길 속의 사람, 불이 난 집, "
    "우선순위, 경로, 확정, 출발, 상태, 다시 시도, 중단, 다시, 취소.",
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

WEB_ORIGINS = tuple(value.strip() for value in os.getenv("RELAY_WEB_ORIGINS", "").split(",") if value.strip())
for origin in WEB_ORIGINS:
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or parsed.netloc.endswith(":")):
        raise ValueError("RELAY_WEB_ORIGINS must contain exact HTTPS origins without paths")
