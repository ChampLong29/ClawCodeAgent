"""Tests for ordered runtime events and declarative plugin hooks."""

import tempfile
import unittest

from claw.plugin_runtime import PluginConfig, PluginRuntime
from claw.runtime_events import EventDirective, RuntimeEventBus


class TestRuntimeEventBus(unittest.TestCase):
    def test_ordered_mutation_and_cancellation(self):
        bus = RuntimeEventBus()
        observed = []

        def later(event):
            observed.append(("later", event.payload["value"]))
            return EventDirective(cancel=True, reason="stop")

        def earlier(event):
            observed.append(("earlier", event.payload["value"]))
            return {"payload_updates": {"value": 2}}

        bus.on("before_tool_call", later, priority=10)
        bus.on("before_tool_call", earlier, priority=-10)
        result = bus.emit("before_tool_call", {"value": 1})

        self.assertEqual(observed, [("earlier", 1), ("later", 2)])
        self.assertTrue(result.cancelled)
        self.assertEqual(result.reason, "stop")

    def test_listener_failure_isolated_unless_fail_closed(self):
        bus = RuntimeEventBus()

        def broken(_event):
            raise RuntimeError("boom")

        bus.on("turn_start", broken)
        result = bus.emit("turn_start")
        self.assertFalse(result.cancelled)
        self.assertIn("boom", result.errors[0])

        bus.on("turn_start", broken, fail_closed=True, name="guard")
        result = bus.emit("turn_start")
        self.assertTrue(result.cancelled)
        self.assertIn("failed closed", result.reason)

    def test_declarative_plugin_prompt_and_tool_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = PluginRuntime(tmp)
        runtime.plugins = [
            PluginConfig(
                name="guard",
                hooks={
                    "before_agent_start": {
                        "append_system_prompt": "Use the project policy."
                    }
                },
                tool_hooks={
                    "bash": {
                        "before": {
                            "deny": True,
                            "argument_equals": {"command": "unsafe"},
                            "reason": "unsafe command",
                        }
                    }
                },
            )
        ]
        bus = RuntimeEventBus()
        runtime.bind_event_bus(bus)

        prompt = bus.emit("before_agent_start", {"system_prompt": "Base"})
        self.assertEqual(
            prompt.event.payload["system_prompt"],
            "Base\n\nUse the project policy.",
        )
        allowed = bus.emit(
            "before_tool_call",
            {"actual_tool_name": "bash", "arguments": {"command": "safe"}},
        )
        denied = bus.emit(
            "before_tool_call",
            {"actual_tool_name": "bash", "arguments": {"command": "unsafe"}},
        )
        self.assertFalse(allowed.cancelled)
        self.assertTrue(denied.cancelled)
        self.assertEqual(denied.reason, "unsafe command")


if __name__ == "__main__":
    unittest.main()
