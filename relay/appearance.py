"""Validate spoken appearance predicates without guessing omitted features."""

import re

ATTRIBUTES = ("shirtColor", "hairColor", "garment", "headwear")
REVISION_REQUEST = "이 설명으로는 탐지 조건을 확인할 수 없습니다. 설명을 수정한 뒤 다시 확인해 주세요."


def validate_search_prompt(text):
    """Validate request text, never infer sensitive attributes from an image."""
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
        raise ValueError(REVISION_REQUEST)
    text = text.strip()
    if re.search(
        r"인종|민족|혈통|국적|백인|흑인|황인|아시아인|동양인|서양인|"
        r"한국인|중국인|일본인|얼굴\s*(?:인식|식별|비교|대조)|안면\s*(?:인식|식별)|"
        r"동일인|같은\s*사람|신원|누구인지|누군지|누구야|생체정보|"
        r"(?:사람|인물|얼굴)의?\s*이름|이름을\s*(?:알려|추정|맞혀)|"
        r"\b(?:race|racial|ethnic\w*|ancestry|nationality|caucasian|asian|"
        r"identity|identify|recognize|recognise|biometric\w*)\b|"
        r"\b(?:black|white)\s+(?:person|people|man|woman)\b|"
        r"\b(?:face|facial)\s+(?:recognition|matching|identification)\b|"
        r"\bsame\s+person\b",
        text, re.IGNORECASE,
    ):
        raise ValueError(REVISION_REQUEST)
    return text


_COLORS = {
    "초록색": "green", "초록": "green", "녹색": "green",
    "빨간색": "red", "빨간": "red", "빨강": "red", "빨강색": "red",
    "파란색": "blue", "파란": "blue", "파랑": "blue", "파랑색": "blue",
    "갈색": "brown",
    "검은색": "black", "검은": "black", "검정색": "black", "검정": "black",
    "금색": "blond",
    "노란색": "yellow", "노란": "yellow", "노랑": "yellow", "노랑색": "yellow",
    "회색": "gray", "주황색": "orange", "주황": "orange",
    "흰색": "white", "하얀색": "white", "하얀": "white", "하양": "white", "하양색": "white",
    "핑크색": "pink", "핑크": "pink", "분홍색": "pink", "분홍": "pink",
    "형광 핑크색": "pink", "형광핑크색": "pink", "형광핑크": "pink",
}
# Sorted longest-first so overlapping spellings (e.g. "검정색" vs "검정") never
# get shadowed by a shorter alternative matching first and leaving a
# trailing "색" that the grammar can't otherwise account for.
_COLOR = "(?:" + "|".join(sorted(_COLORS, key=len, reverse=True)) + ")"
# Hard-hat/headwear phrasing (construction scenario): "안전모" or "헬멧",
# optionally followed by a negation ("안 쓴", "미착용", "쓰지 않은") or a
# positive marker ("쓴", "착용한"). No negation words present at all is
# treated as a positive ("wearing it") mention, matching the phrasing this
# scenario's system prompt and voice descriptions actually use.
_HEADWEAR_ITEM = "안전모|헬멧"
_FEATURE = re.compile(
    rf"(?P<colors>{_COLOR}(?:\s*(?:또는|혹은|이나)\s*{_COLOR})*)"
    r"\s*(?P<neg>(?:이|가)?\s*아닌)?\s*"
    r"(?P<item>티셔츠|상의|옷|머리카락|머리)"
    r"(?P<postneg>\s*(?:을|를)?\s*(?:입지\s*않은|입지\s*않는|안\s*입은|안\s*입는|아닌))?"
    r"|(?P<blond>금발)|"
    r"(?P<garment>티셔츠|재킷|자켓)|"
    rf"(?P<hw_item>{_HEADWEAR_ITEM})(?:을|를)?\s*"
    r"(?P<hw_neg>안\s*쓴|안\s*쓰고|미착용|착용\s*안\s*한|쓰지\s*않은|쓰지\s*않는|없는)?"
    r"\s*(?P<hw_pos>쓴|착용한|착용|쓰고)?"
)


def fixture_prompt_constraints(text):
    """Extract every recognizable color/hair/garment feature anywhere in the
    text and ignore everything else (extra words, particles, verb endings,
    unmatched items like "모자"/"안경" the mock vision engine has no ground
    truth for). Only rejects when literally nothing usable for matching was
    found at all and there isn't even a generic "find a person" request —
    the mock matching engine needs at least one signal (or an explicit
    "match anything" fallback) to do anything meaningful with a capture.
    """
    text = validate_search_prompt(text)
    conditions = []

    def feature(match):
        if match["hw_item"]:
            negated = bool(match["hw_neg"])
            conditions.append(dict(attribute="headwear", operator="include",
                                   values=["bare" if negated else "hardhat"]))
        elif match["blond"]:
            conditions.append(dict(attribute="hairColor", operator="include", values=["blond"]))
        elif match["garment"]:
            value = "t-shirt" if match["garment"] == "티셔츠" else "jacket"
            conditions.append(dict(attribute="garment", operator="include", values=[value]))
        else:
            attribute = "hairColor" if match["item"] in ("머리", "머리카락") else "shirtColor"
            colors = [_COLORS[color] for color in re.findall(_COLOR, match["colors"])]
            negated = bool(match["neg"]) or bool(match["postneg"])
            conditions.append(dict(attribute=attribute,
                                   operator="exclude" if negated else "include", values=colors))
            if match["item"] == "티셔츠" and not negated:
                conditions.append(dict(attribute="garment", operator="include", values=["t-shirt"]))
        return " "

    _FEATURE.sub(feature, text)
    if not conditions and re.search(r"사람|인물", text) is None:
        raise ValueError(REVISION_REQUEST)
    return conditions


def validate_constraints(constraints, unsupported):
    if not isinstance(constraints, list) or len(constraints) > 12:
        raise ValueError("외형 조건은 12개 이내의 목록이어야 합니다.")
    if (not isinstance(unsupported, list) or len(unsupported) > 12
            or any(not isinstance(value, str) or not value.strip() or len(value) > 200
                   for value in unsupported)):
        raise ValueError("추가 외형 조건의 형식이 올바르지 않습니다.")
    normalized = []
    for condition in constraints:
        if (not isinstance(condition, dict)
                or set(condition) != {"attribute", "operator", "values"}
                or condition["attribute"] not in ATTRIBUTES
                or condition["operator"] not in ("include", "exclude")):
            raise ValueError("외형 조건의 항목이나 비교 방식이 올바르지 않습니다.")
        values = condition["values"]
        if (not isinstance(values, list) or not 1 <= len(values) <= 8
                or any(not isinstance(value, str) or not value.strip() or len(value) > 60
                       for value in values)):
            raise ValueError("외형 조건에는 확인한 특징을 담아야 합니다.")
        normalized.append({
            "attribute": condition["attribute"],
            "operator": condition["operator"],
            "values": list(dict.fromkeys(value.strip().lower() for value in values)),
        })
    return normalized, [value.strip() for value in unsupported]


def prompt_confidence(constraints, unsupported):
    """How confident the drone is it can pick the described person out of a
    crowd, from the confirmed prompt alone (before any flight/vision data
    exists). Deterministic so it stays testable: more distinct, confirmed
    appearance attributes raise it; clauses the vision step can't check
    (unsupported) lower it, since the drone will have to ignore those."""
    attributes_covered = {condition["attribute"] for condition in constraints}
    confidence = 35 + 20 * min(len(attributes_covered), 3)
    confidence -= 15 * min(len(unsupported), 4)
    confidence = max(10, min(100, confidence))
    if attributes_covered:
        parts = ", ".join(sorted(ATTRIBUTE_LABELS.get(attribute, attribute) for attribute in attributes_covered))
        reasoning = f"{parts} 조건으로 구별할 수 있어."
    else:
        reasoning = "이미지로 구별할 수 있는 외형 조건이 없어서 확신도가 낮아."
    if unsupported:
        reasoning += f" '{unsupported[0]}'처럼 이미지로 확인 못 하는 조건은 참고만 할 거야."
    return confidence, reasoning


ATTRIBUTE_LABELS = {"shirtColor": "상의 색", "hairColor": "머리색", "garment": "옷 종류",
                    "headwear": "안전모 착용 여부"}


def matches_appearance(appearance, constraints, unsupported):
    conditions, unknown = validate_constraints(constraints, unsupported)
    if unknown:
        raise ValueError(REVISION_REQUEST)
    for condition in conditions:
        if condition["attribute"] not in appearance:
            raise ValueError(REVISION_REQUEST)
        matches = appearance[condition["attribute"]] in condition["values"]
        if (condition["operator"] == "include") != matches:
            return False
    return True
