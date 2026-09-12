"""Tests for agent types."""

import unittest
from claw.agent_types import (
    DEFAULT_MODEL_NAME, AgentPermissions, ModelConfig, ModelPricing, BudgetConfig, UsageStats,
    ToolCall, AssistantTurn, AgentRunResult,
)


class TestAgentPermissions(unittest.TestCase):
    def test_workspace_restriction_round_trip(self):
        permissions = AgentPermissions(
            allow_write=True,
            allow_shell=True,
            restrict_workspace=True,
            allowed_write_paths=["src/**"],
        )
        restored = AgentPermissions.from_dict(permissions.to_dict())
        self.assertTrue(restored.restrict_workspace)
        self.assertEqual(restored.allowed_write_paths, ["src/**"])


class TestModelPricing(unittest.TestCase):
    def test_calculate_cost(self):
        pricing = ModelPricing(input_token_price=0.001, output_token_price=0.002)
        cost = pricing.calculate_cost(100, 50)
        self.assertEqual(cost, 0.2)

    def test_to_dict_from_dict(self):
        pricing = ModelPricing(input_token_price=0.001, output_token_price=0.002)
        d = pricing.to_dict()
        restored = ModelPricing.from_dict(d)
        self.assertEqual(restored.input_token_price, 0.001)
        self.assertEqual(restored.output_token_price, 0.002)


class TestModelConfig(unittest.TestCase):
    def test_deepseek_flash_is_shared_default(self):
        self.assertEqual(DEFAULT_MODEL_NAME, "deepseek-flash")
        self.assertEqual(ModelConfig().name, DEFAULT_MODEL_NAME)
        self.assertEqual(ModelConfig.from_dict({}).name, DEFAULT_MODEL_NAME)


class TestUsageStats(unittest.TestCase):
    def test_iadd(self):
        u1 = UsageStats(input_tokens=100, output_tokens=50)
        u2 = UsageStats(input_tokens=200, output_tokens=100)
        u1 += u2
        self.assertEqual(u1.input_tokens, 300)
        self.assertEqual(u1.output_tokens, 150)

    def test_to_dict_from_dict(self):
        usage = UsageStats(input_tokens=100, output_tokens=50, model_calls=2, tool_calls=3)
        d = usage.to_dict()
        restored = UsageStats.from_dict(d)
        self.assertEqual(restored.input_tokens, 100)
        self.assertEqual(restored.tool_calls, 3)


class TestToolCall(unittest.TestCase):
    def test_to_dict_from_dict(self):
        tc = ToolCall(id="call_123", name="read_file", arguments={"path": "/tmp/test"})
        d = tc.to_dict()
        restored = ToolCall.from_dict(d)
        self.assertEqual(restored.id, "call_123")
        self.assertEqual(restored.name, "read_file")


class TestAgentRunResult(unittest.TestCase):
    def test_to_dict_from_dict(self):
        usage = UsageStats(input_tokens=100, output_tokens=50)
        result = AgentRunResult(
            stop_reason="completed",
            final_message="Hello",
            usage=usage,
        )
        d = result.to_dict()
        restored = AgentRunResult.from_dict(d)
        self.assertEqual(restored.stop_reason, "completed")
        self.assertEqual(restored.final_message, "Hello")


if __name__ == "__main__":
    unittest.main()
