"""Authorize voice actions from fresh participant turns, not model continuations."""

import asyncio
from dataclasses import dataclass, field
import re
import time
import unicodedata


def normalize(text):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).lower())


AFFIRMATIVES = {
    normalize(text) for text in (
        "네", "넵", "네네", "예", "응", "응응", "엉", "어", "맞아", "맞아요", "맞습니다",
        "오케이", "오키", "오케이야", "okay", "ok", "콜", "좋아", "좋아요", "그래", "그래요",
        "그렇지", "그거야", "그렇게 해", "그렇게 해줘", "동의해", "가자", "출발",
        "출발해", "출발해줘", "출발하자", "네 출발해", "응 출발해", "좋아 출발해",
        "확인", "확인이요", "확인해", "확인했어", "확인했어요", "네 확인",
    )
}


def is_affirmative(text):
    value = normalize(text)
    if re.search(r"아니|않|말고|말아|잠깐|아직|취소|멈|안돼|싫|모르", value):
        return False
    words = "|".join(sorted(AFFIRMATIVES, key=len, reverse=True))
    return bool(value) and re.fullmatch(f"(?:{words}){{1,3}}", value) is not None


def is_retry_input(text):
    return normalize(text) in {"", "음", "음음", "흠", "아", "어음", "뭐라고", "다시말해줘", "못들었어"}


STOP_ALIASES = {
    "monitor-1": ("1", "1번", "일번", "첫번째", "첫째", "현장하나", "현장1", "바다", "물",
                  "바다에빠진사람", "물에빠진사람", "물에빠져있는사람", "익수자"),
    "monitor-2": ("2", "2번", "이번", "두번째", "둘째", "현장둘", "현장2", "잔해",
                  "잔해아래", "잔해아래사람", "잔해아래의사람", "잔해아래있는사람",
                  "잔해아래에있는사람", "잔해아래에갇힌사람"),
    "monitor-3": ("3", "3번", "삼번", "세번째", "셋째", "현장셋", "현장3", "불",
                  "불난집", "불이난집", "불길속의사람", "불길속사람", "불이난집에갇힌사람"),
}


def names_stop(text, monitor):
    aliases = STOP_ALIASES.get(monitor, ())
    if not aliases:
        return False
    prefix = r"(?:(?:네|응|그럼|그러면|먼저|우선|첫번째는|두번째는|다음은|다음으로는|다음으로)){0,3}"
    suffix = (r"(?:부터|으로|로|을|를)?(?:먼저|우선)?"
              r"(?:가자|가줘|가주세요|갈래|구하자|구해줘|구해주세요|구조하자|선택할게|할게|할래|하자)?(?:요)?")
    return re.fullmatch(prefix + "(?:" + "|".join(aliases) + ")" + suffix, normalize(text)) is not None


@dataclass
class ParticipantTurn:
    item_id: str
    context: tuple
    prompt_revision: int | None = None
    route_readback_done: bool = False
    prompt_generation: int = 0
    text: str = ""
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    consumed: bool = False
    replied: bool = False
    timestamps: dict = field(default_factory=dict)
    ttfa_response_id: str | None = None
    ttfa_reported: bool = False
    input_failure_reported: bool = False


class VoiceTurns:
    def __init__(self):
        self.turns = {}
        self.latest = None
        self.responses = {}
        self.spoken_responses = set()
        self.early_transcripts = {}
        self.response_turn = None
        self.prompt_readback = None
        self.audible_prompt_revision = None
        self.prompt_generation = 0
        self.audible_prompt_generation = 0
        self.rejected_prompt_revision = None
        self.retry_prompt_revision = None
        self.last_rejection_code = None
        self.confirmed_route_replied = False
        self.route_readback = None
        self.route_generated = False
        self.response_timestamps = {}

    def mark_item(self, item_id, event):
        turn = self.turns.get(item_id)
        if turn:
            turn.timestamps.setdefault(event, time.perf_counter())
        return turn

    def mark_response(self, response_id, event):
        if not response_id:
            return None
        stamps = self.response_timestamps.setdefault(response_id, {})
        stamps.setdefault(event, time.perf_counter())
        if len(self.response_timestamps) > 128:
            del self.response_timestamps[next(iter(self.response_timestamps))]
        turn = self.responses.get(response_id)
        if turn:
            self.mark_item(turn.item_id, "response_" + event)
        return turn

    def first_audio_latency(self, response_id):
        turn = self.mark_response(response_id, "first_audio")
        if (turn is None or turn.ttfa_reported
                or "speech_stopped" not in turn.timestamps):
            return None
        turn.ttfa_response_id = response_id
        turn.ttfa_reported = True
        return turn, int((time.perf_counter() - turn.timestamps["speech_stopped"]) * 1000)

    @staticmethod
    def context(session):
        return (session.data["promptPhase"], tuple(session.state.draftRoute), session.phase)

    def begin(self, item_id, session):
        if not isinstance(item_id, str) or not item_id or item_id in self.turns:
            return
        context = self.context(session)
        self.latest = ParticipantTurn(
            item_id, context, self.audible_prompt_revision,
            self.confirmed_route_replied, self.audible_prompt_generation)
        self.turns[item_id] = self.latest
        if item_id in self.early_transcripts:
            self.transcribe(item_id, self.early_transcripts.pop(item_id))
        if len(self.turns) > 128:
            del self.turns[next(iter(self.turns))]

    def stop(self, item_id, session):
        self.begin(item_id, session)
        if item_id in self.turns:
            self.response_turn = self.turns[item_id]

    def transcribe(self, item_id, text):
        turn = self.turns.get(item_id)
        if turn is None:
            if isinstance(item_id, str) and item_id:
                self.early_transcripts[item_id] = text
                if len(self.early_transcripts) > 128:
                    del self.early_transcripts[next(iter(self.early_transcripts))]
            return
        if turn.ready.is_set():
            return
        turn.text = text.strip() if isinstance(text, str) else ""
        self.mark_item(item_id, "transcript_completed")
        turn.ready.set()
        if turn.prompt_revision is not None and is_retry_input(turn.text):
            self.retry_prompt_revision = turn.prompt_revision
        if (turn.prompt_revision is not None and not is_retry_input(turn.text)
                and not is_affirmative(turn.text)):
            self.rejected_prompt_revision = turn.prompt_revision
            if self.audible_prompt_revision == turn.prompt_revision:
                self.audible_prompt_revision = None
            turn.prompt_revision = None

    def bind_response(self, response_id, item_id=None, *, inherit=True):
        if response_id:
            self.responses[response_id] = (
                self.turns.get(item_id) if item_id else self.response_turn if inherit else None)
            if len(self.responses) > 128:
                del self.responses[next(iter(self.responses))]

    def prepare_prompt(self):
        self.prompt_readback = None
        self.audible_prompt_revision = None
        self.rejected_prompt_revision = None
        self.retry_prompt_revision = None

    def begin_prompt_readback(self, response_id, revision):
        self.prompt_generation += 1
        self.prompt_readback = (response_id, revision)

    def hear_response(self, response_id):
        self.spoken_responses.add(response_id)
        if self.prompt_readback and self.prompt_readback[0] == response_id:
            self.audible_prompt_revision = self.prompt_readback[1]
            self.audible_prompt_generation = self.prompt_generation

    def prepare_route(self):
        self.confirmed_route_replied = False
        self.route_readback = None
        self.route_generated = False

    def begin_route_readback(self, response_id, session):
        self.confirmed_route_replied = False
        self.route_generated = False
        self.route_readback = (response_id, tuple(session.state.confirmedRoute))

    def finish_route_playback(self, response_id, session):
        if (self.route_generated and session.phase == "ready"
                and self.route_readback == (response_id, tuple(session.state.confirmedRoute))):
            self.confirmed_route_replied = True

    def finish_response(self, response, session):
        response_id = response.get("id")
        spoken = response_id in self.spoken_responses or any(
            item.get("type") == "message" and item.get("role") == "assistant" and item.get("content")
            for item in response.get("output", [])
        )
        self.spoken_responses.discard(response_id)
        turn = self.responses.get(response_id)
        if response.get("status") != "completed":
            return
        readback_ids = {
            readback[0] for readback in (self.prompt_readback, self.route_readback) if readback
        }
        if turn and response_id not in readback_ids:
            turn.replied = True
        if not spoken:
            return
        if (session.phase == "ready"
                and self.route_readback == (response_id, tuple(session.state.confirmedRoute))):
            self.route_generated = True

    async def authorize(self, name, args, turn, session):
        self.last_rejection_code = None
        if name in {"get_state", "confirm_route"}:
            return None
        if turn is None or turn is not self.latest or turn.consumed:
            return self.reject("stale_or_missing_turn", "새로운 참가자 답변이 없습니다. 질문 뒤에는 참가자의 답을 기다리세요.")
        try:
            # Speech remains native/realtime; only mutations wait for input evidence.
            preceding = []
            if name == "confirm_prompt" and turn.prompt_revision is not None:
                for earlier in self.turns.values():
                    if earlier is turn:
                        break
                    if (earlier.prompt_revision == turn.prompt_revision
                            and (not earlier.ready.is_set() or earlier.prompt_generation == turn.prompt_generation)):
                        preceding.append(earlier)
            await asyncio.wait_for(asyncio.gather(
                turn.ready.wait(), *(earlier.ready.wait() for earlier in preceding)), timeout=5)
        except TimeoutError:
            return self.reject("transcript_timeout", "참가자의 말을 확인하지 못했습니다. 진행하지 말고 다시 물어보세요.")
        if turn is not self.latest or turn.consumed:
            return self.reject("superseded_turn", "이미 처리했거나 지난 참가자 답변입니다. 새 답변을 기다리세요.")
        if not turn.text:
            return self.reject("empty_transcript", "참가자의 말이 비어 있거나 전사에 실패했습니다. 진행하지 말고 다시 물어보세요.")
        if turn.context != self.context(session):
            return self.reject("wrong_stage", "이 답변은 이전 단계의 답변입니다. 현재 질문에 대한 새 답변을 기다리세요.")
        if name == "prepare_prompt":
            if is_affirmative(turn.text) or is_retry_input(turn.text):
                return self.reject("description_missing", "탐색 설명이 아직 없습니다. 어떤 사람을 찾을지 물어보세요.")
        elif name == "confirm_prompt":
            if not is_affirmative(turn.text):
                return self.reject("not_consent", "참가자가 동의하지 않았습니다. 수정 사항을 반영하거나 다시 물어보세요.")
            if (session.pending_prompt is None or turn.prompt_revision is None
                    or turn.prompt_revision != session.pending_prompt_revision
                    or self.rejected_prompt_revision == turn.prompt_revision
                    or any(not earlier.text or not is_affirmative(earlier.text) for earlier in preceding)):
                return self.reject("no_pending_readback", "확인할 설명을 prepare_prompt로 저장한 뒤 되말하고 새 동의를 기다리세요.")
        elif name == "select_stop":
            if not isinstance(args, dict) or not names_stop(turn.text, args.get("monitor")):
                return self.reject("stop_mismatch", "참가자의 이번 답변에서 그 목적지를 확인하지 못했습니다. 목적지를 대신 고르지 말고 다시 물어보세요.")
        elif name == "launch_mission":
            if not is_affirmative(turn.text) or not turn.route_readback_done:
                return self.reject("departure_not_confirmed", "경로 안내 뒤 새로운 출발 동의를 받아야 합니다. 출발하지 말고 답을 기다리세요.")
        return None

    def reject(self, code, message):
        self.last_rejection_code = code
        return message

    def commit(self, turn, name):
        if turn and name not in {"get_state", "confirm_route"}:
            turn.consumed = True
