"""Validate spoken appearance predicates without guessing omitted features."""

ATTRIBUTES = ("shirtColor", "hairColor", "garment")


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
        return False
    for condition in conditions:
        matches = appearance[condition["attribute"]] in condition["values"]
        if (condition["operator"] == "include") != matches:
            return False
    return True
