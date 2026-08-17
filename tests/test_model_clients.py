import json
import unittest
from unittest.mock import patch

from claw.openai_compat import AnthropicClient, OpenAICompatClient


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ModelClientFinishReasonTests(unittest.TestCase):
    def test_openai_client_preserves_length_finish_reason(self):
        payload = {
            "choices": [{
                "finish_reason": "length",
                "message": {"role": "assistant", "content": ""},
            }],
            "usage": {"completion_tokens": 12},
        }
        client = OpenAICompatClient(
            base_url="https://example.invalid/v1",
            api_key="test",
            model="test-model",
        )
        with patch(
            "claw.openai_compat.urllib.request.urlopen",
            return_value=_Response(payload),
        ):
            result = client.complete(messages=[{"role": "user", "content": "x"}])

        self.assertEqual(result["finish_reason"], "length")

    def test_anthropic_client_preserves_max_tokens_stop_reason(self):
        payload = {
            "content": [{"type": "thinking", "thinking": "unfinished"}],
            "stop_reason": "max_tokens",
            "usage": {"output_tokens": 4096},
        }
        client = AnthropicClient(
            base_url="https://example.invalid",
            api_key="test",
            model="test-model",
            thinking_enabled="auto",
        )
        with patch(
            "claw.openai_compat.urllib.request.urlopen",
            return_value=_Response(payload),
        ):
            result = client.complete(messages=[{"role": "user", "content": "x"}])

        self.assertEqual(result["finish_reason"], "max_tokens")
        self.assertEqual(result["usage"]["output_tokens"], 4096)


if __name__ == "__main__":
    unittest.main()
