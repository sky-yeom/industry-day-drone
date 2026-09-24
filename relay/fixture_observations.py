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
    # Security Breach scenario fixtures. The vault and exec-office images
    # deliberately have no candidate matching the black-shirt/black-hair
    # suspect (or anyone plausible) so those zones always resolve as false
    # alarms; the server-room image has one matching candidate plus decoys,
    # the same "correct answer among decoys" pattern used by the three real
    # people above.
    "25bd62fd1217883d082b6c1635f8e59354e96a33a3cb513c74014225b64097c4": [
        person("blue", "blond", "파란색 유니폼을 입은 금발 머리의 경비원이 열린 금고 문 앞에 서 있습니다.",
               [0.33, 0.24, 0.12, 0.55]),
    ],
    "12f9a2671e874bd98f3ca8dd7e9561dc4f3ac80bc527de6a35c94e478fec72a8": [
        person("black", "black", "검은색 티셔츠와 검은 머리의 사람이 서버 랙 사이에 웅크리고 있습니다.",
               [0.22, 0.48, 0.22, 0.52]),
        person("gray", "brown", "회색 티셔츠와 갈색 머리의 사람이 뒤쪽 모니터 앞에 앉아 있습니다.",
               [0.77, 0.29, 0.13, 0.19]),
    ],
    "977571d417d03e8b4ad84ad4bd8fd859a1a4893da8a222e925aaf7aa62bf44ec": [
        person("blue", "brown", "파란색 유니폼과 갈색 머리의 청소 담당 직원이 열린 창문 옆에 서 있습니다.",
               [0.55, 0.20, 0.08, 0.40]),
    ],
    # Construction Site Safety scenario fixtures. Every hot-pink worker
    # visible in all three real reference photos is bareheaded (no hard
    # hat), so each zone genuinely contains a matching violation; the
    # decoys are other-colored workers who are all wearing a hard hat
    # (headwear="hardhat") plus their shirt color alone already excludes
    # them from a "pink + bare" prompt regardless of headwear.
    "78d3b4aede2fa772d3d196c202fc29cf7f908391cce4059e270497903c81f477": [
        person("pink", "black", "핑크색 작업복을 입고 안전모 없이 걷고 있는 사람이 오른쪽 통로 가운데에 있습니다.",
               [0.57, 0.22, 0.10, 0.24], headwear="bare"),
        person("pink", "black", "핑크색 작업복을 입고 안전모 없이 서류를 보고 있는 사람이 오른쪽 통로 안쪽에 있습니다.",
               [0.73, 0.28, 0.11, 0.34], headwear="bare"),
        person("orange", "black", "주황색 작업복과 노란 안전모를 쓴 사람이 왼쪽 통로에 있습니다.",
               [0.03, 0.28, 0.10, 0.32], headwear="hardhat"),
        person("blue", "black", "파란색 작업복과 노란 안전모를 쓴 사람이 왼쪽 통로 안쪽에 있습니다.",
               [0.18, 0.24, 0.07, 0.20], headwear="hardhat"),
        person("orange", "black", "주황색 작업복과 흰색 안전모를 쓴 사람이 오른쪽 자재 더미 옆에 있습니다.",
               [0.86, 0.28, 0.10, 0.34], headwear="hardhat"),
    ],
    "a0eeefcc1314d6007322a611148a200d83ea75c7ddfab1d02e3b42330670822d": [
        person("pink", "black", "핑크색 작업복을 입고 안전모 없이 서류를 보고 있는 사람이 목재 더미 옆에 있습니다.",
               [0.29, 0.32, 0.06, 0.20], headwear="bare"),
        person("pink", "black", "핑크색 작업복을 입고 안전모 없이 목재 더미를 만지고 있는 사람이 가운데에 있습니다.",
               [0.57, 0.49, 0.06, 0.38], headwear="bare"),
        person("orange", "black", "주황색 작업복과 노란 안전모를 쓴 사람이 왼쪽 목재 더미에서 작업 중입니다.",
               [0.06, 0.43, 0.10, 0.34], headwear="hardhat"),
        person("orange", "black", "주황색 작업복과 흰색 안전모를 쓴 사람이 뒤쪽에서 걸어오고 있습니다.",
               [0.66, 0.24, 0.06, 0.25], headwear="hardhat"),
        person("blue", "black", "파란색 작업복과 노란 안전모를 쓴 사람이 오른쪽 철근 더미 옆에 있습니다.",
               [0.76, 0.43, 0.09, 0.44], headwear="hardhat"),
    ],
    "28d2e6ecaa029f4eeca870600716a51d2d525d0f6fe59bf67d7fa5991e1847ad": [
        person("pink", "black", "핑크색 작업복을 입고 안전모 없이 서류를 보고 있는 사람이 배관 자재 앞에 있습니다.",
               [0.41, 0.35, 0.11, 0.29], headwear="bare"),
        person("pink", "black", "핑크색 작업복을 입고 안전모 없이 거푸집을 만지고 있는 사람이 오른쪽 안쪽에 있습니다.",
               [0.76, 0.17, 0.09, 0.36], headwear="bare"),
        person("blue", "black", "파란색 작업복과 노란 안전모를 쓴 사람이 왼쪽에서 배관 자재를 정리하고 있습니다.",
               [0.24, 0.23, 0.11, 0.34], headwear="hardhat"),
        person("orange", "black", "주황색 작업복과 흰색 안전모를 쓴 사람이 목재를 들고 가운데를 지나가고 있습니다.",
               [0.54, 0.16, 0.12, 0.35], headwear="hardhat"),
        person("orange", "black", "주황색 작업복과 노란 안전모를 쓴 사람이 오른쪽 아래에서 철근 작업을 하고 있습니다.",
               [0.80, 0.43, 0.13, 0.53], headwear="hardhat"),
    ],
}

# These are invented test roles, NOT observations of people in the colored PNGs.
# They belong only to the verified contract generator, never arbitrary images.
CONTRACT_SIMULATION = {
    "tag-1": {"shirtColor": "green", "hairColor": "brown", "garment": "t-shirt"},
    "tag-2": {"shirtColor": "green", "hairColor": "brown", "garment": "t-shirt"},
    "tag-3": {"shirtColor": "green", "hairColor": "brown", "garment": "t-shirt"},
}
