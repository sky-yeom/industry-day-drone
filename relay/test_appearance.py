import unittest
from unittest.mock import AsyncMock, patch

from relay.appearance import matches_appearance, validate_constraints
from relay.camera import FixtureCamera, SCENARIO
from relay.vision import MockVision, VisionError, validate_analysis


def condition(attribute, *values, operator="include"):
    return {"attribute": attribute, "operator": operator, "values": list(values)}


class AppearanceTests(unittest.TestCase):
    def test_partial_correct_appearance_and_alternatives(self):
        for constraints in (
            [],
            [condition("shirtColor", "green")],
            [condition("hairColor", "brown")],
            [condition("garment", "t-shirt")],
            [condition("shirtColor", "green", "blue")],
            [condition("shirtColor", "red", operator="exclude")],
            [condition("shirtColor", "green"), condition("hairColor", "brown")],
        ):
            with self.subTest(constraints=constraints):
                self.assertTrue(matches_appearance(SCENARIO["targetAppearance"], constraints, []))

    def test_wrong_color_type_negation_or_contradiction_fails(self):
        for constraints in (
            [condition("shirtColor", "red")],
            [condition("hairColor", "blond")],
            [condition("garment", "jacket")],
            [condition("shirtColor", "green"), condition("hairColor", "black")],
            [condition("shirtColor", "green", operator="exclude")],
            [condition("shirtColor", "green"), condition("shirtColor", "red")],
        ):
            with self.subTest(constraints=constraints):
                self.assertFalse(matches_appearance(SCENARIO["targetAppearance"], constraints, []))

    def test_unsupported_attributes_are_not_silently_dropped(self):
        self.assertFalse(matches_appearance(
            SCENARIO["targetAppearance"], [condition("shirtColor", "green")], ["안경을 쓴 사람"]))

    def test_validation_does_not_fill_omitted_features(self):
        actual, unsupported = validate_constraints([condition("hairColor", " BROWN ")], [])
        self.assertEqual(actual, [condition("hairColor", "brown")])
        self.assertEqual(unsupported, [])
        for conditions in (None, "green", [{}], [condition("unknown", "brown")],
                           [condition("hairColor")], [condition("hairColor", "")]):
            with self.subTest(conditions=conditions), self.assertRaises(ValueError):
                validate_constraints(conditions, [])

    def test_target_and_participant_match_are_both_required(self):
        for prompt, target in ((False, True), (True, False), (False, False)):
            with self.subTest(prompt=prompt, target=target):
                evidence = validate_analysis({
                    "matchesPrompt": prompt, "matchesTarget": target,
                    "description": "보이는 사람의 외형과 요청 또는 구조 대상의 조건이 일치하지 않습니다.",
                    "box": None,
                })
                self.assertFalse(evidence["targetPresent"])
        positive = validate_analysis({
            "matchesPrompt": True, "matchesTarget": True,
            "description": "초록색 티셔츠와 갈색 머리의 사람이 요청한 모습에 맞게 보입니다.",
            "box": [0.1, 0.1, 0.2, 0.3],
        })
        self.assertTrue(positive["targetPresent"])
        for invalid in (
            {"targetPresent": True, "description": "사람", "box": None},
            {"matchesPrompt": "true", "matchesTarget": True, "description": "사람", "box": None},
        ):
            with self.assertRaises(VisionError):
                validate_analysis(invalid)


class MockAppearanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_description_cannot_rescue_any_of_the_three_frames(self):
        camera, vision = FixtureCamera(), MockVision()
        with patch("relay.vision.asyncio.sleep", new_callable=AsyncMock):
            for person in SCENARIO["people"]:
                frame = await camera.capture(person["monitorId"])
                for constraints in (
                    [condition("shirtColor", "red")],
                    [condition("shirtColor", "green"), condition("hairColor", "blond")],
                    [condition("shirtColor", "green", operator="exclude")],
                ):
                    evidence = await vision.analyze(
                        frame, SCENARIO["targetAppearance"]["description"],
                        search_prompt="참가자가 확인한, 대상과 다른 외형의 탐색 지시",
                        appearance_constraints=constraints, unsupported_appearance=[])
                    self.assertFalse(evidence["targetPresent"])
                    self.assertIsNone(evidence["box"])

    async def test_partial_description_can_find_each_correct_frame(self):
        camera, vision = FixtureCamera(), MockVision()
        with patch("relay.vision.asyncio.sleep", new_callable=AsyncMock):
            for person in SCENARIO["people"]:
                frame = await camera.capture(person["monitorId"])
                evidence = await vision.analyze(
                    frame, SCENARIO["targetAppearance"]["description"],
                    search_prompt="갈색 머리인 사람을 찾아줘",
                    appearance_constraints=[condition("hairColor", "brown")],
                    unsupported_appearance=[])
                self.assertTrue(evidence["targetPresent"])

    async def test_missing_extraction_never_becomes_success_for_a_spoken_prompt(self):
        frame = await FixtureCamera().capture("monitor-1")
        with self.assertRaises(VisionError):
            await MockVision().analyze(
                frame, SCENARIO["targetAppearance"]["description"],
                search_prompt="빨간 옷을 입은 사람을 찾아줘")


if __name__ == "__main__":
    unittest.main()
