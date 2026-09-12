"""No cloud calls: exercise observations, actual pixel uploads and failure cleanup."""

import asyncio
import base64
import copy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from relay import config
from relay.camera import Capture, FixtureCamera, PUBLIC_ROOT, SCENARIO
from relay.test_live_mission import png
from relay.vision import (AzureVision, ContractMockVision, MockVision, VisionError,
                          create_providers, validate_analysis, validate_evidence)


POSITIVE = {
    "targetPresent": True,
    "description": "초록색 티셔츠를 입고 갈색 머리를 한 사람이 바다에서 팔을 벌리고 물에 떠 있으려 합니다.",
    "box": [0.35, 0.35, 0.29, 0.37],
}
NEGATIVE = {
    "targetPresent": False,
    "description": "바다와 파도만 보이며 초록색 옷을 입고 갈색 머리를 한 사람은 보이지 않습니다.",
    "box": None,
}
AZURE_POSITIVE = dict(POSITIVE, box=None)


def completion(evidence=AZURE_POSITIVE, *, finish_reason="stop", refusal=None):
    observation = {
        "matchesPrompt": evidence["targetPresent"], "assessable": True,
        "needsRescue": evidence["targetPresent"],
        "description": evidence["description"], "box": evidence["box"],
    }
    return {
        "choices": [{
            "finish_reason": finish_reason,
            "message": {"content": json.dumps(observation, ensure_ascii=False), "refusal": refusal},
        }]
    }


def fake_http(body, *, status=200):
    async def chunks(_size):
        yield body if isinstance(body, bytes) else json.dumps(body).encode()

    response = MagicMock()
    response.status = status
    response.content.iter_chunked.side_effect = chunks
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.post.return_value = response
    return session, response


class EvidenceTests(unittest.TestCase):
    def test_person_nouns_do_not_gate_positive_observations(self):
        for noun in ("남성", "여성", "소년", "소녀", "아이"):
            with self.subTest(noun=noun):
                evidence = dict(AZURE_POSITIVE, description=(
                    f"중앙 창문에서 초록색 티셔츠와 갈색 머리의 {noun}이 몸을 내밀고 있습니다. "
                    "창틀을 잡고 있으며 주변에 불길이 보입니다."))
                self.assertEqual(validate_evidence(evidence), evidence)

    def test_model_observations_require_null_box_but_legacy_evidence_remains_valid(self):
        for box in ([0.1, 0.1, 0.2, 0.3], [0.47, 0.58, 0.38, 0.64]):
            with self.subTest(box=box), self.assertRaisesRegex(VisionError, "box=null"):
                validate_analysis({
                    "matchesPrompt": True, "assessable": True, "needsRescue": True,
                    "description": AZURE_POSITIVE["description"], "box": box,
                })
        self.assertEqual(validate_evidence(POSITIVE), POSITIVE)

    def test_rescue_need_is_required_in_addition_to_participant_appearance(self):
        for prompt in (False, True):
            for rescue in (False, True):
                with self.subTest(prompt=prompt, rescue=rescue):
                    result = validate_analysis({
                        "matchesPrompt": prompt, "assessable": True, "needsRescue": rescue,
                        "description": "초록색 티셔츠와 갈색 머리의 남성이 잔해 아래에 있습니다.",
                        "box": None,
                    })
                    self.assertEqual(result["targetPresent"], prompt and rescue)
                    self.assertEqual(set(result), {"targetPresent", "description", "box"})

    def test_structured_rescue_verdict_is_not_overridden_by_incidental_negation(self):
        observation = {
            "matchesPrompt": True, "assessable": True, "needsRescue": True,
            "description": "구조 필요 대상: 초록색 티셔츠와 갈색 머리의 남성이 물속에서 손을 들고 있습니다. "
                           "구명환은 보이지 않으며 물에 잠긴 몸을 지탱하고 있습니다.",
            "box": None,
        }
        self.assertTrue(validate_analysis(observation)["targetPresent"])
        for invalid in (None, 1, "true"):
            with self.subTest(needsRescue=invalid), self.assertRaises(VisionError):
                validate_analysis(dict(observation, needsRescue=invalid))
        missing = dict(observation)
        del missing["needsRescue"]
        with self.assertRaises(VisionError):
            validate_analysis(missing)

    def test_direct_script_imports_without_repository_on_python_path(self):
        result = subprocess.run(
            [
                sys.executable, "-I", "-c",
                "import sys; sys.path.insert(0, '.'); "
                "import camera, vision; "
                "camera_adapter, provider = vision.create_providers('mock'); "
                "assert isinstance(camera_adapter, camera.FixtureCamera); "
                "assert provider.readiness() is None",
            ],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_valid_positive_negative_and_unlocalized_positive(self):
        for evidence in (POSITIVE, NEGATIVE, dict(POSITIVE, box=None)):
            self.assertEqual(validate_evidence(evidence), evidence)

    def test_malformed_and_contradictory_evidence_never_succeeds(self):
        malformed = [
            None, [], {}, dict(POSITIVE, targetPresent="true"),
            dict(POSITIVE, targetPresent=1), dict(POSITIVE, description=""),
            dict(POSITIVE, description="A person wearing yellow is visible."),
            dict(POSITIVE, description="사람이 보입니다."),
            dict(POSITIVE, description="노란 옷을 입은 사람이 보이지 않습니다."),
            dict(POSITIVE, description="노란 옷을 입은 사람이 없습니다."),
            dict(POSITIVE, description="노란 옷을 입은 사람을 찾지 못했습니다."),
            dict(POSITIVE, description="사람이 손을 들었는지 확인할 수 없습니다."),
            dict(POSITIVE, description="노란 옷을 입은 사람이 있을 가능성이 있습니다."),
            dict(POSITIVE, targetPresent=False, box=None),
            dict(NEGATIVE, box=[0.1, 0.1, 0.2, 0.2]),
            dict(POSITIVE, box=[0, 0, 1]),
            dict(POSITIVE, box=[0, 0, 1, 1, 1]),
            dict(POSITIVE, box=[0, 0, -1, 1]),
            dict(POSITIVE, box=[0, 0, 0, 1]),
            dict(POSITIVE, box=[-0.1, 0, 1, 1]),
            dict(POSITIVE, box=[0.9, 0, 0.2, 0.2]),
            dict(POSITIVE, box=[0, 0.9, 0.2, 0.2]),
            dict(POSITIVE, box=[True, 0, 1, 1]),
            dict(POSITIVE, box=[0, 0, float("nan"), 1]),
            dict(POSITIVE, box=[0, 0, float("inf"), 1]),
            dict(POSITIVE, box=[0, 0, 10 ** 1000, 1]),
            dict(POSITIVE, box=[0, 0, "1", 1]),
            dict(POSITIVE, outcome="rescued"),
        ]
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(VisionError):
                validate_evidence(value)
        missing = copy.deepcopy(POSITIVE)
        del missing["box"]
        with self.assertRaises(VisionError):
            validate_evidence(missing)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.capture = await FixtureCamera().capture("monitor-1")
        self.target = SCENARIO["targetAppearance"]["description"]

    def test_mode_selection_is_explicit_and_never_falls_back(self):
        camera, vision = create_providers("mock")
        self.assertIsInstance(camera, FixtureCamera)
        self.assertIsInstance(vision, MockVision)
        self.assertEqual(vision.mode, "mock")
        with patch.object(config, "AZURE_VISION_ENDPOINT", ""):
            _, vision = create_providers("azure")
        self.assertIsInstance(vision, AzureVision)
        self.assertEqual(vision.mode, "azure")
        self.assertIn("AZURE_VISION_ENDPOINT", vision.readiness())
        with patch.object(config, "TRIAGE_MODE", "mock"):
            self.assertIsInstance(create_providers()[1], MockVision)
        for mode in ("auto", "AZURE", "", 1):
            with self.subTest(mode=mode), self.assertRaises(VisionError):
                create_providers(mode)

    async def test_mock_matches_known_pixels_and_prompt_with_explicit_label(self):
        camera, vision = create_providers("mock")
        self.assertIsNone(vision.readiness())
        with patch("relay.vision.asyncio.sleep", new_callable=AsyncMock) as sleep:
            for person in SCENARIO["people"]:
                capture = await camera.capture(person["monitorId"])
                evidence = await vision.analyze(capture, search_prompt=self.target)
                self.assertTrue(evidence["targetPresent"])
                self.assertIsNotNone(evidence["box"])
                self.assertIn("모의 분석", evidence["description"])
                self.assertIn("AI 미사용", evidence["description"])
            sleep.assert_awaited_with(SCENARIO["mockAnalysisMs"] / 1000)

    async def test_mock_unknown_pixels_cannot_succeed_and_request_mismatch_is_negative(self):
        vision = MockVision()
        unknown = Capture(self.capture.id, self.capture.monitor_id, png(), "image/png")
        with self.assertRaisesRegex(VisionError, "관찰 기록"):
            await vision.analyze(unknown, search_prompt=self.target)
        with patch("relay.vision.asyncio.sleep", new_callable=AsyncMock):
            result = await vision.analyze(self.capture, search_prompt="빨간색 옷을 입은 사람")
            self.assertFalse(result["targetPresent"])
            self.assertIsNone(result["box"])

    async def test_invalid_images_are_technical_errors_even_in_mock(self):
        for image, mime in ((b"", "image/png"), (self.capture.image_bytes, "image/svg+xml")):
            with self.subTest(mime=mime), self.assertRaises(VisionError):
                await MockVision().analyze(Capture("id", "monitor-1", image, mime), search_prompt=self.target)

    async def test_noncanonical_person_matching_confirmed_prompt_succeeds(self):
        with patch("relay.vision.asyncio.sleep", new_callable=AsyncMock):
            result = await MockVision().analyze(self.capture, search_prompt="파란색 티셔츠를 입은 사람")
        self.assertTrue(result["targetPresent"])
        self.assertIn("왼쪽", result["description"])
        self.assertEqual(result["box"], [0.07, 0.24, 0.28, 0.22])

    async def test_each_provider_rejects_blank_prompt(self):
        for prompt in ("", " ", None, "x" * 2001):
            with self.subTest(prompt=prompt), self.assertRaises(VisionError):
                await MockVision().analyze(self.capture, search_prompt=prompt)


class AzureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.capture = await FixtureCamera().capture("monitor-1")
        self.target = SCENARIO["targetAppearance"]["description"]
        # Never read or exercise ambient credentials.
        self.config_patch = patch.multiple(
            config,
            AZURE_VISION_ENDPOINT="https://fixture-resource.openai.azure.com",
            AZURE_VISION_DEPLOYMENT="fixture-vision-gpt-4.1",
            AZURE_VISION_API_VERSION="v1",
            AZURE_VISION_API_KEY="unit-test-key-not-a-secret",
        )
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.vision = AzureVision()

    def test_readiness_blocks_missing_or_unsupported_configuration(self):
        self.assertIsNone(self.vision.readiness())
        for field, value, message in (
            ("endpoint", "", "AZURE_VISION_ENDPOINT"),
            ("endpoint", "http://fixture-resource.openai.azure.com", "HTTPS"),
            ("endpoint", "https://example.com", "HTTPS"),
            ("endpoint", "https://fixture-resource.openai.azure.com/openai/v1", "기본 주소"),
            ("endpoint", "https://fixture-resource.openai.azure.com?token=x", "기본 주소"),
            ("endpoint", "https://fixture-resource.openai.azure.com:bad", "형식"),
            ("endpoint", "https://user:password@fixture-resource.openai.azure.com", "기본 주소"),
            ("deployment", "", "AZURE_VISION_DEPLOYMENT"),
            ("deployment", "../elsewhere", "AZURE_VISION_DEPLOYMENT"),
            ("api_version", "2023-12-01-preview", "v1"),
            ("max_completion_tokens", 0, "MAX_COMPLETION_TOKENS"),
            ("max_completion_tokens", 8193, "MAX_COMPLETION_TOKENS"),
            ("max_completion_tokens", True, "MAX_COMPLETION_TOKENS"),
            ("reasoning_effort", "unsupported", "REASONING_EFFORT"),
        ):
            with self.subTest(field=field, value=value):
                vision = AzureVision()
                setattr(vision, field, value)
                self.assertIn(message, vision.readiness())

    async def test_reasoning_budget_is_explicit_and_other_models_keep_their_default(self):
        for effort, budget in (("", 1000), ("minimal", 2000)):
            with self.subTest(effort=effort):
                self.vision.reasoning_effort, self.vision.max_completion_tokens = effort, budget
                session, _ = fake_http(completion())
                with patch("relay.vision.aiohttp.ClientSession", return_value=session):
                    await self.vision.analyze(self.capture, search_prompt=self.target)
                payload = session.post.call_args.kwargs["json"]
                self.assertEqual(payload["max_completion_tokens"], budget)
                if effort:
                    self.assertEqual(payload["reasoning_effort"], effort)
                else:
                    self.assertNotIn("reasoning_effort", payload)

    async def test_missing_config_errors_before_request(self):
        self.vision.deployment = ""
        with patch("relay.vision.aiohttp.ClientSession") as session:
            with self.assertRaisesRegex(VisionError, "AZURE_VISION_DEPLOYMENT"):
                await self.vision.analyze(self.capture, search_prompt=self.target)
            session.assert_not_called()

    async def test_positive_and_negative_structured_results_upload_exact_pixels(self):
        for observation in (AZURE_POSITIVE, NEGATIVE):
            with self.subTest(present=observation["targetPresent"]):
                session, response = fake_http(completion(observation))
                with patch("relay.vision.aiohttp.ClientSession", return_value=session):
                    result = await self.vision.analyze(self.capture, search_prompt=self.target)
                self.assertEqual(result, observation)
                args, kwargs = session.post.call_args
                self.assertEqual(args[0], "https://fixture-resource.openai.azure.com/openai/v1/chat/completions")
                self.assertFalse(kwargs["allow_redirects"])
                self.assertEqual(kwargs["headers"], {"api-key": "unit-test-key-not-a-secret"})
                payload = kwargs["json"]
                self.assertEqual(payload["model"], self.vision.deployment)
                self.assertTrue(payload["response_format"]["json_schema"]["strict"])
                self.assertFalse(payload["response_format"]["json_schema"]["schema"]["additionalProperties"])
                schema = payload["response_format"]["json_schema"]["schema"]
                self.assertEqual(schema["properties"]["box"]["type"], "null")
                self.assertEqual(set(schema["required"]),
                                 {"matchesPrompt", "assessable", "needsRescue", "description", "box"})
                content = payload["messages"][1]["content"]
                self.assertIn(self.target, content[0]["text"])
                self.assertEqual(content[1]["type"], "image_url")
                data_url = content[1]["image_url"]["url"]
                self.assertEqual(base64.b64decode(data_url.split(",", 1)[1]), self.capture.image_bytes)
                self.assertNotIn("monitor-1", json.dumps(payload))
                self.assertNotIn("mockBox", json.dumps(payload))
                session.__aexit__.assert_awaited_once()
                response.__aexit__.assert_awaited_once()

    async def test_current_scene_context_is_sent_without_scoring_or_ground_truth_coordinates(self):
        person = SCENARIO["people"][0]
        context = {"monitor_id": person["monitorId"], "label": person["label"], "report": person["clue"]}
        session, _ = fake_http(completion())
        with patch("relay.vision.aiohttp.ClientSession", return_value=session):
            await self.vision.analyze(
                self.capture, search_prompt=self.target, scene_context=context)
        payload = session.post.call_args.kwargs["json"]
        text = payload["messages"][1]["content"][0]["text"]
        for value in context.values():
            self.assertIn(value, text)
        for excluded in ("deadlineMs", "initiallyInjured", "mockBox", "28000"):
            self.assertNotIn(excluded, json.dumps(payload, ensure_ascii=False))
        self.assertIn("box는 발견 여부와 관계없이 항상 null", payload["messages"][0]["content"])

    async def test_invalid_or_mismatched_scene_context_never_reaches_azure(self):
        valid = {"monitor_id": "monitor-1", "label": "바다 현장", "report": "구조 요청"}
        for context in ({}, [], dict(valid, monitor_id="monitor-2"),
                        dict(valid, label=""), dict(valid, report=3), dict(valid, deadlineMs=28000)):
            with self.subTest(context=context):
                with patch("relay.vision.aiohttp.ClientSession") as session:
                    with self.assertRaisesRegex(VisionError, "현장명"):
                        await self.vision.analyze(self.capture, search_prompt=self.target, scene_context=context)
                    session.assert_not_called()

    async def test_participant_prompt_is_sent_with_the_actual_image(self):
        prompt = "초록색이 아닌 옷을 입은 사람을 찾아 주세요."
        session, _ = fake_http(completion())
        with patch("relay.vision.aiohttp.ClientSession", return_value=session):
            await self.vision.analyze(self.capture, search_prompt=prompt)
        payload = session.post.call_args.kwargs["json"]
        content = payload["messages"][1]["content"]
        self.assertIn(prompt, content[0]["text"])
        self.assertNotIn(self.target, content[0]["text"])
        self.assertEqual(json.loads(content[0]["text"]), {"confirmedSearchPrompt": prompt})
        schema = payload["response_format"]["json_schema"]["schema"]
        self.assertNotIn("matchesTarget", schema["properties"])
        self.assertNotIn("matchesTarget", schema["required"])
        self.assertNotIn("targetDescription", json.dumps(payload))
        self.assertIn("데이터", payload["messages"][0]["content"])
        self.assertEqual(content[1]["image_url"]["url"], self.capture.image_url)

    async def test_only_prompt_match_decides_success(self):
        for matches_prompt in (False, True):
            body = completion(NEGATIVE)
            body["choices"][0]["message"]["content"] = json.dumps({
                "matchesPrompt": matches_prompt, "assessable": True, "needsRescue": True,
                "description": ("빨간 티셔츠를 입은 사람이 오른쪽에 서 있습니다." if matches_prompt
                                else "사람이 보이지만 요청한 모습과 일치하지 않습니다."),
                "box": None,
            })
            session, _ = fake_http(body)
            with patch("relay.vision.aiohttp.ClientSession", return_value=session):
                evidence = await self.vision.analyze(
                    self.capture, search_prompt="빨간 티셔츠를 입은 사람")
            self.assertEqual(evidence["targetPresent"], matches_prompt)
            self.assertIsNone(evidence["box"])

    async def test_raw_visual_condition_outside_enums_is_authoritative(self):
        prompt = "안경을 쓴 사람을 찾아줘"
        session, _ = fake_http(completion({
            "targetPresent": True, "description": "안경을 착용한 사람이 오른쪽에 서 있습니다.",
            "box": None,
        }))
        with patch("relay.vision.aiohttp.ClientSession", return_value=session):
            evidence = await self.vision.analyze(self.capture, search_prompt=prompt,
                appearance_constraints=[], unsupported_appearance=["안경을 쓴 사람"])
        self.assertTrue(evidence["targetPresent"])
        text = session.post.call_args.kwargs["json"]["messages"][1]["content"][0]["text"]
        self.assertEqual(json.loads(text), {"confirmedSearchPrompt": prompt})

    async def test_unassessable_conditions_require_revision_without_hints(self):
        from relay.appearance import REVISION_REQUEST
        body = completion(NEGATIVE)
        body["choices"][0]["message"]["content"] = json.dumps({
            "matchesPrompt": False, "assessable": False, "needsRescue": False,
            "description": "요청한 특징을 확인할 수 없습니다.", "box": None,
        })
        session, _ = fake_http(body)
        with patch("relay.vision.aiohttp.ClientSession", return_value=session):
            with self.assertRaisesRegex(VisionError, REVISION_REQUEST):
                await self.vision.analyze(self.capture, search_prompt="안경을 쓴 사람")

    async def test_invalid_or_sensitive_prompts_never_reach_azure(self):
        with patch("relay.vision.aiohttp.ClientSession") as session:
            for prompt in ("", " ", None, "x" * 2001, "인종이 같은 사람", "얼굴로 신원을 확인해줘"):
                with self.subTest(prompt=prompt), self.assertRaises(VisionError):
                    await self.vision.analyze(self.capture, search_prompt=prompt)
            session.assert_not_called()

    async def test_malformed_refused_truncated_or_invalid_evidence_is_error(self):
        invalid = [
            b"not JSON", {}, [], None, {"choices": []}, {"choices": [None]},
            {"choices": [{"message": None}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": {}}}]},
            completion(finish_reason="length"),
            completion(refusal="not allowed"), completion(dict(POSITIVE, box=[1, 1, 1, 1])),
            completion(dict(POSITIVE, targetPresent="true")),
            {"choices": [{"finish_reason": "stop", "message": {"content": "not JSON"}}]},
            {"choices": [{"finish_reason": "stop", "message": {
                "content": '{"targetPresent":false,"targetPresent":true,'
                '"description":"노란색 옷을 입은 사람이 통로에서 손을 듭니다.","box":null}',
            }}]},
            b"x" * 65537,
        ]
        for body in invalid:
            with self.subTest(body=str(body)[:100]):
                session, response = fake_http(body)
                with patch("relay.vision.aiohttp.ClientSession", return_value=session):
                    with self.assertRaises(VisionError):
                        await self.vision.analyze(self.capture, search_prompt=self.target)
                session.__aexit__.assert_awaited_once()
                response.__aexit__.assert_awaited_once()

    async def test_http_error_is_visible_not_fallback_or_body_leak(self):
        for status in (301, 400, 401, 429, 500):
            with self.subTest(status=status):
                session, _ = fake_http(b"private-service-error-content", status=status)
                with patch("relay.vision.aiohttp.ClientSession", return_value=session):
                    with self.assertRaises(VisionError) as raised:
                        await self.vision.analyze(self.capture, search_prompt=self.target)
                self.assertIn(f"HTTP {status}", str(raised.exception))
                self.assertNotIn("private-service", str(raised.exception))

    async def test_timeout_and_transport_error_are_visible(self):
        for error, expected in (
            (asyncio.TimeoutError(), "시간이 초과"),
            (aiohttp.ClientError("private-error"), "연결 또는 인증"),
        ):
            with self.subTest(error=type(error).__name__):
                with patch.object(self.vision, "_authenticated_request", new_callable=AsyncMock, side_effect=error):
                    with self.assertRaisesRegex(VisionError, expected):
                        await self.vision.analyze(self.capture, search_prompt=self.target)

    async def test_entra_credential_is_closed_on_success_and_failure(self):
        self.vision.api_key = ""
        for failure in (None, VisionError("이미지 분석 오류")):
            with self.subTest(failure=failure):
                credential = MagicMock()
                credential.__aenter__ = AsyncMock(return_value=credential)
                credential.__aexit__ = AsyncMock(return_value=False)
                credential.get_token = AsyncMock(return_value=SimpleNamespace(token="test-token"))
                request = AsyncMock(return_value=NEGATIVE, side_effect=failure)
                with patch("relay.vision.DefaultAzureCredential", return_value=credential):
                    with patch.object(self.vision, "_request", request):
                        if failure:
                            with self.assertRaises(VisionError):
                                await self.vision.analyze(self.capture, search_prompt=self.target)
                        else:
                            self.assertEqual(await self.vision.analyze(self.capture, search_prompt=self.target), NEGATIVE)
                credential.get_token.assert_awaited_once_with("https://ai.azure.com/.default")
                self.assertEqual(request.call_args.args[1], {"Authorization": "Bearer test-token"})
                credential.__aexit__.assert_awaited_once()

    async def test_entra_authentication_is_inside_total_timeout(self):
        self.vision.api_key = ""
        self.vision.timeout = 0.01
        credential = MagicMock()
        credential.__aenter__ = AsyncMock(return_value=credential)
        credential.__aexit__ = AsyncMock(return_value=False)

        async def blocked_auth(_scope):
            await asyncio.Event().wait()

        credential.get_token = AsyncMock(side_effect=blocked_auth)
        with patch("relay.vision.DefaultAzureCredential", return_value=credential):
            with self.assertRaisesRegex(VisionError, "시간이 초과"):
                await self.vision.analyze(self.capture, search_prompt=self.target)
        credential.__aexit__.assert_awaited_once()

    async def test_cancellation_closes_http_and_credential_without_converting_error(self):
        self.vision.api_key = ""
        credential = MagicMock()
        credential.__aenter__ = AsyncMock(return_value=credential)
        credential.__aexit__ = AsyncMock(return_value=False)
        credential.get_token = AsyncMock(return_value=SimpleNamespace(token="test-token"))
        session, response = fake_http(completion())
        entered = asyncio.Event()

        async def blocked_read(_size):
            entered.set()
            await asyncio.Event().wait()
            yield b""

        response.content.iter_chunked.side_effect = blocked_read
        with patch("relay.vision.DefaultAzureCredential", return_value=credential):
            with patch("relay.vision.aiohttp.ClientSession", return_value=session):
                task = asyncio.create_task(self.vision.analyze(self.capture, search_prompt=self.target))
                await asyncio.wait_for(entered.wait(), timeout=1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        session.__aexit__.assert_awaited_once()
        response.__aexit__.assert_awaited_once()
        credential.__aexit__.assert_awaited_once()


class ContractSimulationTests(unittest.IsolatedAsyncioTestCase):
    def frame(self):
        from relay.contract_mock import _capture
        record = _capture("test-mission", 0, "tag-1", 1, 1, 1)
        return Capture(record["capture_id"], "monitor-1", base64.b64decode(record["image_base64"]),
                       "image/png", "test-mission", 0, "tag-1")

    async def test_only_verified_simulation_and_prompt_determine_result(self):
        frame = self.frame()
        with patch("relay.vision.asyncio.sleep", new_callable=AsyncMock):
            for prompt, present in (("초록색 옷을 입은 사람", True), ("파란색 옷을 입은 사람", False)):
                result = await ContractMockVision().analyze(frame, search_prompt=prompt)
                self.assertEqual(result["targetPresent"], present)
                self.assertIn("모의 계약 테스트 · AI 미사용", result["description"])

    async def test_unknown_pixels_or_forged_marker_never_become_simulated_success(self):
        from dataclasses import replace
        import struct
        import zlib
        frame = self.frame()
        marker = f"Description\0MOCK synthetic fixture {frame.id}".encode()
        chunk = (struct.pack(">I", len(marker)) + b"tEXt" + marker
                 + struct.pack(">I", zlib.crc32(b"tEXt" + marker) & 0xffffffff))
        unknown = png()
        for image in (unknown, unknown[:33] + chunk + unknown[33:]):
            with self.assertRaises(VisionError):
                await ContractMockVision().analyze(replace(frame, image_bytes=image),
                                                   search_prompt="사람을 찾아줘")
        with self.assertRaises(VisionError):
            await ContractMockVision().analyze(frame, search_prompt="안경을 쓴 사람")


if __name__ == "__main__":
    unittest.main()
