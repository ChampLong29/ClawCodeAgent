import json
import os
import unittest
from unittest.mock import patch

from claw.agent_types import DEFAULT_MODEL_NAME
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
    def test_clients_share_deepseek_flash_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(OpenAICompatClient().model, DEFAULT_MODEL_NAME)
            self.assertEqual(AnthropicClient().model, DEFAULT_MODEL_NAME)

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
        self.assertEqual(
            result["_provider_metadata"]["protocol"],
            "openai_chat_completions",
        )

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
        self.assertEqual(
            result["_provider_metadata"]["protocol"],
            "anthropic_messages",
        )

    def test_anthropic_client_disables_thinking_and_requires_any_tool(self):
        payload = {
            "content": [{
                "type": "tool_use",
                "id": "edit-1",
                "name": "edit_file",
                "input": {"path": "source.py", "old_text": "a", "new_text": "b"},
            }],
            "stop_reason": "tool_use",
            "usage": {"output_tokens": 12},
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
        ) as mocked:
            result = client.complete(
                messages=[{"role": "user", "content": "x"}],
                tools=[{
                    "name": "edit_file",
                    "description": "edit",
                    "input_schema": {"type": "object"},
                }],
                tool_choice="required",
                thinking_mode="disabled",
            )

        request = mocked.call_args.args[0]
        sent = json.loads(request.data.decode("utf-8"))
        self.assertEqual(sent["thinking"], {"type": "disabled"})
        self.assertEqual(sent["tool_choice"], {"type": "any"})
        self.assertEqual(
            result["tool_calls"][0]["function"]["name"], "edit_file"
        )


if __name__ == "__main__":
    unittest.main()
