"""Manually reviewed visible observations for exact bundled PNGs, not AI output.

SHA-256 pins prevent revised artwork from inheriting stale annotations. Boxes
cover the pictured people approximately; no identity or sensitive labels exist.
"""


def person(shirt, hair, description, box, headwear=None):
    appearance = {"shirtColor": shirt, "hairColor": hair, "garment": "t-shirt"}
    if headwear is not None:
        appearance["headwear"] = headwear
    return {
        "appearance": appearance,
        "description": description,
        "box": box,
    }


FIXTURE_OBSERVATIONS = {
    "a3124f6567cd6a0dfa9bbd491cfdbe24b2bffa111634dd2337e1c032837dfd70": [
        person("orange", "black", "주황색 구조복을 입은 사람이 가운데 바다에서 구명튜브를 붙잡고 헤엄치고 있습니다.",
               [0.47, 0.48, 0.13, 0.15]),
        person("gray", "brown", "회색 우비를 입은 사람이 왼쪽 배 위에서 바다 쪽을 보고 있습니다.",
               [0.10, 0.62, 0.10, 0.32]),
        person("black", "black", "검은색 제복을 입은 사람이 오른쪽 해양경비정 갑판 위에 서 있습니다.",
               [0.55, 0.23, 0.03, 0.08]),
    ],
    "c9d787e78db1cf2e1e4129fea189000e78cae4baf08952cc5c030c9a50a8c9b3": [
        person("gray", "black", "회색 구조복을 입은 사람이 왼쪽 앞에서 구조견 목줄을 잡고 있습니다.",
               [0.19, 0.28, 0.19, 0.55]),
        person("gray", "brown", "회색 구조복을 입은 사람이 가운데 앞에서 잔해 틈을 살펴보고 있습니다.",
               [0.30, 0.28, 0.14, 0.50]),
        person("black", "black", "검은색 제복을 입은 사람이 오른쪽 뒤편 잔해 위에 서 있습니다.",
               [0.56, 0.10, 0.06, 0.28]),
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
