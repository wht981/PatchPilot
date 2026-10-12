import json
import os
import unittest
from unittest.mock import patch

from patchpilot.llm import LLMConfig, LLMError, OpenAICompatibleClient
from tests.llm_support import model_server


class TestLLMConfiguration(unittest.TestCase):
    def test_environment_and_cli_precedence(self):
        with patch.dict(os.environ, {"PATCHPILOT_MODEL": "env-model", "PATCHPILOT_API_KEY_ENV": "MY_LLM_KEY",
                                     "MY_LLM_KEY": "opaque-test-credential", "PATCHPILOT_MAX_TOKENS": "2048"}, clear=True):
            config = LLMConfig.from_env(model="flag-model")
        self.assertEqual(config.model, "flag-model")
        self.assertEqual(config.max_tokens, 2048)
        self.assertEqual(config.api_key, "opaque-test-credential")
        self.assertNotIn(config.api_key, repr(config))

    def test_local_endpoint_needs_no_key(self):
        LLMConfig(model="local", base_url="http://localhost:11434/v1")

    def test_invalid_configuration_fails_early(self):
        cases = [
            {"model": ""}, {"api_key": ""}, {"timeout": 0}, {"timeout": float("nan")},
            {"max_tokens": 0}, {"base_url": "http://example.com/v1"},
            {"base_url": "https://user:key@example.com/v1"},
            {"base_url": "https://example.com/v1?token=abc"},
            {"base_url": "https://example.com:bad/v1"},
            {"base_url": "https://example.com/line\nbreak"},
            {"token_parameter": "bad"}, {"api_key_env": "bad name"}, {"max_retries": 4},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                args = {"model": "test", "api_key": "test-credential"}
                args.update(overrides)
                with self.assertRaises(ValueError):
                    LLMConfig(**args)


class TestModelTransport(unittest.TestCase):
    def test_chat_protocol_usage_and_secret_redaction(self):
        with model_server([{"action": "stop", "reason": "done"}]) as (url, state):
            client = OpenAICompatibleClient(LLMConfig("test-model", url, "opaque-test-credential"))
            reply = client.complete([{"role": "system", "content": "Return JSON. opaque-test-credential"}])
        body = state["requests"][0]
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["max_completion_tokens"], 8192)
        self.assertNotIn("opaque-test-credential", json.dumps(body))
        self.assertEqual(state["headers"][0]["Authorization"], "Bearer opaque-test-credential")
        self.assertEqual((reply.input_tokens, reply.output_tokens), (10, 5))

    def test_vendor_token_limit_and_plain_json(self):
        with model_server([{"ok": True}]) as (url, state):
            client = OpenAICompatibleClient(LLMConfig("test", url, token_parameter="max_tokens", json_mode=False))
            client.complete([{"role": "user", "content": "Return JSON"}])
        self.assertEqual(state["requests"][0]["max_tokens"], 8192)
        self.assertNotIn("response_format", state["requests"][0])

    def test_rate_limit_retry_is_bounded(self):
        with model_server([429, {"ok": True}]) as (url, state), patch("patchpilot.llm.time.sleep"):
            client = OpenAICompatibleClient(LLMConfig("test", url, max_retries=1))
            client.complete([{"role": "user", "content": "JSON"}])
        self.assertEqual(len(state["requests"]), 2)

    def test_authentication_error_is_not_retried(self):
        with model_server([401]) as (url, state):
            client = OpenAICompatibleClient(LLMConfig("test", url))
            with self.assertRaisesRegex(LLMError, "HTTP 401"):
                client.complete([{"role": "user", "content": "JSON"}])
        self.assertEqual(len(state["requests"]), 1)

    def test_malformed_incomplete_and_refused_replies_are_rejected(self):
        client = OpenAICompatibleClient(LLMConfig("test", "http://localhost/v1"))
        for raw in (b"not json", b"{}", b'{"choices":[]}',
                    b'{"choices":[{"finish_reason":"stop","message":[]}]}',
                    b'{"choices":[{"finish_reason":"stop","message":{"content":"{}"}}],"usage":[1]}',
                    b'{"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}',
                    b'{"choices":[{"finish_reason":"stop","message":{"refusal":"no","content":"{}"}}]}'):
            with self.subTest(raw=raw), self.assertRaises(LLMError):
                client._parse_reply(raw)
