"""Manually reviewed visible observations for exact bundled PNGs, not AI output.

SHA-256 pins prevent revised artwork from inheriting stale annotations. Boxes
cover the pictured people approximately; no identity or sensitive labels exist.
"""


def person(shirt, hair, description, box):
    return {
        "appearance": {"shirtColor": shirt, "hairColor": hair, "garment": "t-shirt"},
        "description": description,
        "box": box,
    }


FIXTURE_OBSERVATIONS = {
    "20d75754fd391c2c18e566fdfc1bac1c0b5951508470a44ed725ef5365cc888c": [
        person("green", "brown", "초록색 티셔츠와 갈색 머리의 사람이 앞쪽 물에서 팔을 들고 있습니다.",
               [0.25, 0.03, 0.44, 0.81]),
        person("blue", "brown", "파란색 티셔츠와 갈색 머리의 사람이 왼쪽 튜브에 기대어 있습니다.",
               [0.07, 0.24, 0.28, 0.22]),
        person("green", "blond", "초록색 티셔츠와 금발 머리의 사람이 오른쪽 주황색 튜브에 팔을 얹고 있습니다.",
               [0.63, 0.23, 0.22, 0.24]),
    ],
    "b9c9237527ff65d90ff0911a7bcf57ea2fdcbcff7dda3ebc2a2bfc1dc739b92a": [
        person("green", "brown", "초록색 티셔츠와 갈색 머리의 사람이 앞쪽 잔해 아래에 누워 있습니다.",
               [0.19, 0.38, 0.78, 0.62]),
        person("gray", "brown", "회색 티셔츠와 갈색 머리의 사람이 왼쪽 뒤 잔해 위에 손을 대고 있습니다.",
               [0.19, 0.15, 0.17, 0.24]),
        person("green", "black", "초록색 티셔츠와 검은 머리의 사람이 오른쪽 뒤에서 등을 돌리고 서 있습니다.",
               [0.58, 0.01, 0.16, 0.41]),
    ],
    "629c49dc4d32f6c909b068fb8284ea2f56589a42013bff05340ae7a8375cd9db": [
        person("green", "brown", "초록색 티셔츠와 갈색 머리의 사람이 가운데 창틀을 잡고 있습니다.",
               [0.43, 0.10, 0.28, 0.79]),
        person("orange", "brown", "주황색 티셔츠와 갈색 머리의 사람이 왼쪽에서 팔을 들고 있습니다.",
               [0.12, 0.38, 0.28, 0.62]),
        person("green", "brown", "초록색 티셔츠와 갈색 머리의 사람이 오른쪽 앞에서 팔로 얼굴을 가리고 있습니다.",
               [0.65, 0.43, 0.31, 0.57]),
    ],
}

# These are invented test roles, NOT observations of people in the colored PNGs.
# They belong only to the verified contract generator, never arbitrary images.
CONTRACT_SIMULATION = {
    "tag-1": {"shirtColor": "green", "hairColor": "brown", "garment": "t-shirt"},
    "tag-2": {"shirtColor": "green", "hairColor": "brown", "garment": "t-shirt"},
    "tag-3": {"shirtColor": "green", "hairColor": "brown", "garment": "t-shirt"},
}
