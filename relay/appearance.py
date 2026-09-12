"""Validate spoken appearance predicates without guessing omitted features."""

import re

ATTRIBUTES = ("shirtColor", "hairColor", "garment")
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
    "빨간색": "red", "빨간": "red", "빨강": "red",
    "파란색": "blue", "파란": "blue", "파랑": "blue",
    "갈색": "brown", "검은색": "black", "검은": "black", "검정": "black",
    "금색": "blond", "노란색": "yellow", "노란": "yellow",
    "회색": "gray", "주황색": "orange", "주황": "orange",
    "흰색": "white", "하얀": "white",
}
_COLOR = "(?:" + "|".join(_COLORS) + ")"
_FEATURE = re.compile(
    rf"(?P<colors>{_COLOR}(?:\s*(?:또는|혹은|이나)\s*{_COLOR})*)"
    r"\s*(?P<neg>(?:이|가)?\s*아닌)?\s*"
    r"(?P<item>티셔츠|상의|옷|머리카락|머리)|(?P<blond>금발)|"
    r"(?P<garment>티셔츠|재킷|자켓)"
)


def fixture_prompt_constraints(text):
    """Deliberately limited fixture grammar; every unparsed clause needs revision.

    This is not natural-language AI. Extracted enums cannot authorize dropping
    conditions from the confirmed raw prompt.
    """
    text = validate_search_prompt(text)
    conditions = []

    def feature(match):
        if match["blond"]:
            conditions.append(dict(attribute="hairColor", operator="include", values=["blond"]))
        elif match["garment"]:
            value = "t-shirt" if match["garment"] == "티셔츠" else "jacket"
            conditions.append(dict(attribute="garment", operator="include", values=[value]))
        else:
            attribute = "hairColor" if match["item"] in ("머리", "머리카락") else "shirtColor"
            colors = [_COLORS[color] for color in re.findall(_COLOR, match["colors"])]
            conditions.append(dict(attribute=attribute,
                                   operator="exclude" if match["neg"] else "include", values=colors))
            if match["item"] == "티셔츠":
                conditions.append(dict(attribute="garment", operator="include", values=["t-shirt"]))
        return " "

    remainder = _FEATURE.sub(feature, text)
    # Only grammatical glue and a neutral search command may remain.
    if not re.fullmatch(
        r"(?:\s|[,.!?·]|를|을|와|과|의|인|이고|이면서|하고|입은|입고|한|"
        r"가진|사람|인물|찾아\s*주세요|찾아줘|찾아|주세요)*",
        remainder,
    ) or (not conditions and re.search(r"사람|인물", text) is None):
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
