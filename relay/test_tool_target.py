"""Offline selector tests, including fresh-process environment resolution."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from relay.tool_target import EMBEDDED_API_URL, REAL_API_URL, TEST_API_URL, resolve_tool_target, validate_test_api_url


class ToolTargetTests(unittest.TestCase):
    def test_absent_selector_preserves_legacy_defaults(self):
        target = resolve_tool_target({})
        self.assertIsNone(target.run_mode)
        self.assertEqual((target.control_mode, target.api_url, target.api_token,
            target.transport, target.use_tools, target.triage_mode),
            ("mock", REAL_API_URL, "", "local", False, "mock"))

    def test_absent_selector_preserves_independent_legacy_flags_and_parsing(self):
        env = dict(DRONE_CONTROL_MODE=" LIVE ", DRONE_CONTROL_API_URL=" http://127.0.0.1:8766/ ",
            DRONE_CONTROL_API_TOKEN=" legacy-secret ", DRONE_CONTROL_TRANSPORT=" REMOTE ",
            DRONE_CONTROL_USE_TOOLS="1", TRIAGE_MODE=" MOCK ", DRONE_TEST_API_URL="ignored-invalid",
            DRONE_TEST_API_TOKEN="ignored-test-secret")
        target = resolve_tool_target(env)
        self.assertEqual((target.control_mode, target.api_url, target.api_token,
            target.transport, target.use_tools, target.triage_mode),
            ("live", REAL_API_URL + "/", "legacy-secret", "remote", True, "mock"))
        for value in ("true", " 1 ", "0", ""):
            self.assertFalse(resolve_tool_target(dict(env, DRONE_CONTROL_USE_TOOLS=value)).use_tools)
        self.assertEqual(resolve_tool_target({"DRONE_CONTROL_API_URL": "invalid"}).api_url, "invalid")

    def test_test_selector_overrides_all_opposing_legacy_flags_without_real_token(self):
        target = resolve_tool_target(dict(DRONE_RUN_MODE=" TEST ", DRONE_CONTROL_MODE="live",
            DRONE_CONTROL_API_URL="https://untrusted.invalid", DRONE_CONTROL_API_TOKEN="real-secret",
            DRONE_CONTROL_TRANSPORT="remote", DRONE_CONTROL_USE_TOOLS="0", TRIAGE_MODE="azure"))
        self.assertEqual((target.run_mode, target.control_mode, target.api_url, target.api_token,
            target.transport, target.use_tools, target.triage_mode),
            ("test", "mock", EMBEDDED_API_URL, "", "inprocess", True, "azure"))

    def test_test_flight_keeps_azure_analysis_by_default_and_allows_explicit_mock(self):
        self.assertEqual(resolve_tool_target({"DRONE_RUN_MODE": "test"}).triage_mode, "azure")
        self.assertEqual(resolve_tool_target({"DRONE_RUN_MODE": "test", "TRIAGE_MODE": "mock"}).triage_mode, "mock")
        for mode in ("", "auto", "invalid"):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "TRIAGE_MODE"):
               resolve_tool_target({"DRONE_RUN_MODE": "test", "TRIAGE_MODE": mode})

    def test_existing_hosted_deployment_defaults_to_embedded_test_without_new_settings(self):
        target = resolve_tool_target({"RELAY_HOST": "0.0.0.0", "TRIAGE_MODE": "azure",
                                      "DRONE_CONTROL_TRANSPORT": "remote"})
        self.assertEqual((target.run_mode, target.transport, target.api_url, target.use_tools),
                         ("test", "inprocess", EMBEDDED_API_URL, True))
        live = resolve_tool_target({"RELAY_HOST": "0.0.0.0", "DRONE_CONTROL_MODE": "live"})
        self.assertIsNone(live.run_mode)
        self.assertEqual(live.control_mode, "live")
        real = resolve_tool_target({"RELAY_HOST": "0.0.0.0", "DRONE_RUN_MODE": "real"})
        self.assertEqual((real.control_mode, real.transport), ("live", "remote"))

    def test_test_target_and_auth_are_opt_in_and_never_exposed_in_repr(self):
        target = resolve_tool_target(dict(DRONE_RUN_MODE="test",
            DRONE_TEST_API_URL=" http://127.0.0.1:18768/ ", DRONE_TEST_API_TOKEN=" test-secret ",
            DRONE_CONTROL_API_TOKEN="real-secret"))
        self.assertEqual(target.api_url, "http://127.0.0.1:18768")
        self.assertEqual(target.api_token, "test-secret")
        self.assertNotIn("test-secret", repr(target))
        self.assertNotIn("real-secret", repr(target))

    def test_real_selector_is_fixed_and_only_uses_original_pc_token(self):
        target = resolve_tool_target(dict(DRONE_RUN_MODE="real", DRONE_CONTROL_MODE="mock",
            DRONE_CONTROL_API_URL=TEST_API_URL, DRONE_CONTROL_API_TOKEN=" real-secret ",
            DRONE_CONTROL_TRANSPORT="remote", DRONE_CONTROL_USE_TOOLS="0", TRIAGE_MODE="mock",
            DRONE_TEST_API_URL="invalid", DRONE_TEST_API_TOKEN="test-secret"))
        self.assertEqual((target.run_mode, target.control_mode, target.api_url, target.api_token,
            target.transport, target.use_tools, target.triage_mode),
            ("real", "live", REAL_API_URL, "real-secret", "local", True, "azure"))
        self.assertNotIn("real-secret", repr(target))

    def test_real_selector_requires_nonempty_original_token(self):
        for token in (None, "", " \t "):
            env = dict(DRONE_RUN_MODE="real", DRONE_TEST_API_TOKEN="test-secret")
            if token is not None:
                env["DRONE_CONTROL_API_TOKEN"] = token
            with self.subTest(token=token), self.assertRaisesRegex(ValueError, "DRONE_CONTROL_API_TOKEN"):
                resolve_tool_target(env)

    def test_explicit_remote_real_preserves_cloud_to_pc_transport(self):
        target = resolve_tool_target({"DRONE_RUN_MODE": "real", "DRONE_REAL_TRANSPORT": "remote"})
        self.assertEqual((target.run_mode, target.control_mode, target.transport, target.api_token),
                         ("real", "live", "remote", ""))
        with self.assertRaisesRegex(ValueError, "DRONE_REAL_TRANSPORT"):
            resolve_tool_target({"DRONE_RUN_MODE": "real", "DRONE_REAL_TRANSPORT": "invalid"})

    def test_present_but_empty_or_unknown_selector_never_falls_back(self):
        for mode in ("", " \t ", "mock", "live", "production", "typo-secret"):
            with self.subTest(mode=mode), self.assertRaises(ValueError) as caught:
                resolve_tool_target(dict(DRONE_RUN_MODE=mode, DRONE_CONTROL_MODE="mock"))
            self.assertIn("DRONE_RUN_MODE", str(caught.exception))
            self.assertNotIn("typo-secret", str(caught.exception))

    def test_only_literal_http_loopback_high_ports_are_allowed_for_test(self):
        for port in (1024, 18767, 18768, 65535):
            for suffix in ("", "/"):
                url = f"http://127.0.0.1:{port}"
                self.assertEqual(validate_test_api_url(url + suffix), url)
        invalid = [
            "", "http://127.0.0.1", "http://localhost:18767", "https://127.0.0.1:18767",
            "http://127.0.0.2:18767", "http://[::1]:18767", "http://2130706433:18767",
            "http://127.1:18767", "http://127.0.0.1.:18767", "http://127.0.0.1:18767/path",
            "http://127.0.0.1:18767//", "http://127.0.0.1:18767?", "http://127.0.0.1:18767?q=secret",
            "http://127.0.0.1:18767#", "http://127.0.0.1:18767/#fragment",
            "http://user:secret@127.0.0.1:18767", "http://@127.0.0.1:18767",
            "http://127.0.0.1:18767@evil.invalid", "http://127.0.0.1:18767\\",
            "http://127.0.0.1:\t18767", "http://127.0.0.1:18767\n",
            "http://127.0.0.1:+18767", "http://127.0.0.1:port",
        ]
        invalid += [f"http://127.0.0.1:{port}" for port in (0, 80, 1023, 8766, 9997, 9998, 9999, 65536, -1)]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValueError) as caught:
                validate_test_api_url(url)
            self.assertNotIn("secret", str(caught.exception))

    def test_resolver_does_not_read_navigation_files_or_enable_live_adapter(self):
        env = dict(DRONE_RUN_MODE="real", DRONE_CONTROL_API_TOKEN="real-secret")
        before = dict(env)
        with patch("builtins.open", side_effect=AssertionError("No aircraft configuration reads")):
            resolve_tool_target(env)
        self.assertEqual(env, before)
        self.assertNotIn("DRONE_CONTROL_ENABLE_LIVE", env)


class ConfigSubprocessTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]
    fields = (
        "DRONE_RUN_MODE", "DRONE_CONTROL_MODE", "DRONE_CONTROL_API_URL",
        "DRONE_CONTROL_TRANSPORT", "DRONE_CONTROL_USE_TOOLS", "TRIAGE_MODE",
        "VOICE_NAME", "VOICE_TYPE", "MODEL", "TRANSCRIPTION_MODEL", "TRANSCRIPTION_PROMPT",
        "VAD_TYPE", "VAD_LANGUAGES", "SILENCE_DURATION_MS", "SPEECH_DURATION_MS",
        "PREFIX_PADDING_MS", "VAD_THRESHOLD",
    )

    def run_config(self, values, *, script_import=False):
        env = {key: value for key, value in os.environ.items()
            if not key.upper().startswith(("DRONE_", "TRIAGE_", "VOICE_LIVE_", "AZURE_VISION_", "RELAY_"))
            and key.upper() != "PYTHONPATH"}
        env.update(values)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        prelude = "sys.path.insert(0, str(Path.cwd() / 'relay')); import config" if script_import else "from relay import config"
        code = (
            "import json, os, sys; from pathlib import Path; from unittest.mock import patch\n"
            "with patch('socket.socket.connect', side_effect=AssertionError('offline test')):\n"
            f"    {prelude}\n"
            f"    value = {{key: getattr(config, key) for key in {self.fields!r}}}\n"
            "    value['config_file'] = str(Path(config.__file__).resolve())\n"
            "    value['has_token'] = bool(config.DRONE_CONTROL_API_TOKEN)\n"
            "    value['uses_real_token'] = bool(config.DRONE_CONTROL_API_TOKEN) and config.DRONE_CONTROL_API_TOKEN == os.getenv('DRONE_CONTROL_API_TOKEN', '').strip()\n"
            "    value['uses_test_token'] = bool(config.DRONE_CONTROL_API_TOKEN) and config.DRONE_CONTROL_API_TOKEN == os.getenv('DRONE_TEST_API_TOKEN', '').strip()\n"
            "    print(json.dumps(value))\n"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=self.root, env=env,
            capture_output=True, text=True, timeout=20)
        for secret in ("real-secret-sentinel", "test-secret-sentinel", "invalid-secret-sentinel"):
            self.assertNotIn(secret, result.stdout + result.stderr)
        return result

    def snapshot(self, env, **kwargs):
        result = self.run_config(env, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(Path(value["config_file"]), self.root / "relay" / "config.py")
        return value

    def test_clean_process_defaults_are_legacy_and_voice_matches_main_a828ee0(self):
        value = self.snapshot({})
        self.assertIsNone(value["DRONE_RUN_MODE"])
        self.assertEqual(value["DRONE_CONTROL_API_URL"], REAL_API_URL)
        self.assertEqual(value["DRONE_CONTROL_MODE"], "mock")
        self.assertEqual(value["TRIAGE_MODE"], "mock")
        self.assertEqual(value["DRONE_CONTROL_TRANSPORT"], "local")
        self.assertFalse(value["DRONE_CONTROL_USE_TOOLS"])
        self.assertFalse(value["has_token"])
        self.assertEqual((value["VOICE_NAME"], value["VOICE_TYPE"], value["MODEL"],
            value["TRANSCRIPTION_MODEL"]), ("shimmer", "openai", "gpt-realtime", "gpt-4o-transcribe"))
        self.assertEqual(value["VAD_TYPE"], "server_vad")
        self.assertEqual(value["VAD_LANGUAGES"], ["ko"])
        self.assertEqual((value["SILENCE_DURATION_MS"], value["SPEECH_DURATION_MS"],
            value["PREFIX_PADDING_MS"], value["VAD_THRESHOLD"]), (300, 80, 420, 0.5))

    def test_process_selection_wins_inherited_flags_and_preserves_voice(self):
        baseline = self.snapshot({})
        inherited = dict(DRONE_CONTROL_MODE="live", DRONE_CONTROL_TRANSPORT="remote",
            DRONE_CONTROL_USE_TOOLS="0", DRONE_CONTROL_API_URL="http://127.0.0.1:9998",
            DRONE_CONTROL_API_TOKEN="real-secret-sentinel", TRIAGE_MODE="azure")
        test = self.snapshot(dict(inherited, DRONE_RUN_MODE="test"))
        real = self.snapshot(dict(inherited, DRONE_RUN_MODE="real", DRONE_CONTROL_MODE="mock",
            DRONE_TEST_API_URL="invalid-secret-sentinel", DRONE_TEST_API_TOKEN="test-secret-sentinel", TRIAGE_MODE="mock"))
        for value, mode, control, url, triage in (
            (test, "test", "mock", EMBEDDED_API_URL, "azure"),
            (real, "real", "live", REAL_API_URL, "azure"),
        ):
            self.assertEqual((value["DRONE_RUN_MODE"], value["DRONE_CONTROL_MODE"],
                value["DRONE_CONTROL_API_URL"], value["TRIAGE_MODE"]), (mode, control, url, triage))
            self.assertTrue(value["DRONE_CONTROL_USE_TOOLS"])
            self.assertEqual(value["DRONE_CONTROL_TRANSPORT"], "inprocess" if mode == "test" else "local")
            for field in self.fields[6:]:
                self.assertEqual(value[field], baseline[field], field)
        self.assertFalse(test["has_token"])
        self.assertTrue(real["uses_real_token"])
        self.assertFalse(real["uses_test_token"])

    def test_process_legacy_flags_and_direct_script_import_are_preserved(self):
        value = self.snapshot(dict(DRONE_CONTROL_MODE=" LIVE ", DRONE_CONTROL_TRANSPORT=" REMOTE ",
            DRONE_CONTROL_USE_TOOLS="1", DRONE_CONTROL_API_TOKEN="real-secret-sentinel", TRIAGE_MODE="mock"),
            script_import=True)
        self.assertIsNone(value["DRONE_RUN_MODE"])
        self.assertEqual((value["DRONE_CONTROL_MODE"], value["DRONE_CONTROL_TRANSPORT"], value["TRIAGE_MODE"]),
            ("live", "remote", "mock"))
        self.assertTrue(value["DRONE_CONTROL_USE_TOOLS"])
        self.assertTrue(value["uses_real_token"])

    def test_process_custom_test_url_and_token(self):
        value = self.snapshot(dict(DRONE_RUN_MODE="test", DRONE_TEST_API_URL="http://127.0.0.1:18768/",
            DRONE_TEST_API_TOKEN="test-secret-sentinel", DRONE_CONTROL_API_TOKEN="real-secret-sentinel"))
        self.assertEqual(value["DRONE_CONTROL_API_URL"], "http://127.0.0.1:18768")
        self.assertTrue(value["uses_test_token"])
        self.assertFalse(value["uses_real_token"])

    def test_process_hosted_real_and_test_use_configured_transport_without_voice_changes(self):
        real = self.snapshot({"DRONE_RUN_MODE": "real", "DRONE_REAL_TRANSPORT": "remote"})
        test = self.snapshot({"DRONE_RUN_MODE": "test", "DRONE_REAL_TRANSPORT": "remote",
                              "DRONE_CONTROL_TRANSPORT": "remote", "TRIAGE_MODE": "azure"})
        self.assertEqual(real["DRONE_CONTROL_TRANSPORT"], "remote")
        self.assertEqual(test["DRONE_CONTROL_TRANSPORT"], "inprocess")
        self.assertEqual(real["VOICE_NAME"], test["VOICE_NAME"])
        self.assertFalse(real["has_token"])

    def test_existing_hosted_environment_needs_no_node_or_new_deployment_settings(self):
        value = self.snapshot({"RELAY_HOST": "0.0.0.0", "TRIAGE_MODE": "azure"})
        self.assertEqual((value["DRONE_RUN_MODE"], value["DRONE_CONTROL_TRANSPORT"],
                          value["DRONE_CONTROL_API_URL"]),
                         ("test", "inprocess", EMBEDDED_API_URL))
        self.assertTrue(value["DRONE_CONTROL_USE_TOOLS"])
        self.assertFalse(value["has_token"])

    def test_process_invalid_configuration_fails_clearly_without_secrets(self):
        cases = [
            (dict(DRONE_RUN_MODE=""), "DRONE_RUN_MODE"),
            (dict(DRONE_RUN_MODE="invalid-secret-sentinel"), "DRONE_RUN_MODE"),
            (dict(DRONE_RUN_MODE="test", DRONE_TEST_API_URL=""), "DRONE_TEST_API_URL"),
            (dict(DRONE_RUN_MODE="test", DRONE_TEST_API_URL=REAL_API_URL), "DRONE_TEST_API_URL"),
            (dict(DRONE_RUN_MODE="test", DRONE_TEST_API_URL="http://invalid-secret-sentinel@127.0.0.1:18767"), "DRONE_TEST_API_URL"),
            (dict(DRONE_RUN_MODE="real", DRONE_TEST_API_TOKEN="test-secret-sentinel"), "DRONE_CONTROL_API_TOKEN"),
        ]
        for env, field in cases:
            with self.subTest(field=field):
                result = self.run_config(env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(field, result.stderr)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
