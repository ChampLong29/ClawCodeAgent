from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from claw.agent_runtime import LocalCodingAgent
from claw.agent_types import AgentPermissions
from claw.benchmark import (
    BenchmarkConfig,
    BenchmarkRunner,
    LocalAgentBenchmarkAdapter,
)
from claw.episode.checkpoint import workspace_hash
from claw.experiment.schemas import TaskSpec


class SequencedBenchmarkClient:
    def __init__(self, model: str, responses):
        self.model = model
        self.responses = list(responses)

    def complete(self, **_kwargs):
        if not self.responses:
            raise AssertionError("unexpected model call")
        return self.responses.pop(0)


def tool_response(arguments):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call-1",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps(arguments),
                },
            }
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "model_calls": 1,
            "tool_calls": 1,
        },
    }


def final_response():
    return {
        "role": "assistant",
        "content": "implemented and tested",
        "finish_reason": "stop",
        "usage": {
            "input_tokens": 8,
            "output_tokens": 2,
            "model_calls": 1,
            "tool_calls": 0,
        },
    }


class LocalAgentBenchmarkAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.model_ref = "model/base@revision"

    def tearDown(self):
        self.temporary.cleanup()

    def make_task(self, name: str = "success"):
        template = self.root / f"template-{name}"
        template.mkdir()
        (template / "README.md").write_text("starter\n", encoding="utf-8")
        task = TaskSpec(
            task_id=f"benchmark-task-{name}",
            task_version="1.0.0",
            family_id=f"benchmark-family-{name}",
            domain="python-cli",
            task_type="add_feature",
            difficulty="medium",
            split="test",
            prompt="Create solution.txt containing done.",
            template_ref=str(template),
            template_hash=workspace_hash(template, normalize_exec=True),
            initial_checks=[
                (
                    "python -c \"import pathlib; "
                    "raise SystemExit(0 if pathlib.Path('solution.txt').exists() "
                    "else 1)\""
                )
            ],
            test_commands=[
                (
                    "python -c \"import pathlib; "
                    "assert pathlib.Path('solution.txt').read_text().strip() "
                    "== 'done'\""
                )
            ],
            timeout_seconds=15,
            source="generated",
            license="MIT",
        )
        task.content_hash = task.compute_content_hash()
        return task

    def config(self):
        return BenchmarkConfig(
            group_name="base",
            model_ref=self.model_ref,
            test_manifest_ref="task_suites/manifest.json",
            decoding_config={"temperature": 0, "max_turns": 3},
            tool_schema_version="tool.v1",
            runtime_version="runtime.v1",
            verifier_bundle_version="verifier-policy.v1",
            seed=42,
        )

    def adapter(self, factory):
        return LocalAgentBenchmarkAdapter(
            self.root / "episodes",
            project_root=self.root,
            agent_factory=factory,
            model_ref=self.model_ref,
            runtime_version="runtime.v1",
            prompt_version="prompt.v1",
            tool_version="tool.v1",
            config_version="benchmark-config.v1",
            allowed_paths_resolver=lambda _task: ["solution.txt"],
            input_token_price=0.001,
            output_token_price=0.002,
        )

    def make_agent(self, cwd, model, responses):
        agent = LocalCodingAgent(
            cwd=cwd,
            permissions=AgentPermissions(allow_write=True).to_dict(),
        )
        agent.client = SequencedBenchmarkClient(model, responses)
        return agent

    def test_real_episode_success_produces_metrics_and_auditable_refs(self):
        task = self.make_task()
        observed_configs = []

        def factory(cwd, inference_config):
            observed_configs.append(inference_config)
            return self.make_agent(
                cwd,
                self.model_ref,
                [
                    tool_response(
                        {"path": "solution.txt", "content": "done\n"}
                    ),
                    final_response(),
                ],
            )

        result = BenchmarkRunner(self.root / "benchmark").run(
            [task],
            self.adapter(factory),
            self.config(),
        )
        episode = result.episodes[0]

        self.assertTrue(episode.success)
        self.assertEqual(episode.test_pass_rate, 1.0)
        self.assertEqual(episode.tool_calls, 1)
        self.assertEqual(episode.valid_tool_selections, 1)
        self.assertEqual(episode.valid_tool_arguments, 1)
        self.assertTrue(episode.format_valid)
        self.assertEqual(episode.process_violations, 0)
        self.assertEqual(episode.turns, 2)
        self.assertEqual(episode.input_tokens, 18)
        self.assertEqual(episode.output_tokens, 7)
        self.assertAlmostEqual(episode.token_cost, 0.032)
        self.assertTrue(Path(episode.trajectory_ref).is_file())
        self.assertTrue(Path(episode.verification_ref).is_file())
        self.assertEqual(
            observed_configs,
            [{"temperature": 0, "max_turns": 3}],
        )
        self.assertEqual(result.metrics["task_success_rate"], 1.0)
        self.assertTrue(
            (self.root / "benchmark" / "base" / "benchmark-run.json").is_file()
        )

    def test_relative_template_ref_is_resolved_from_project_root(self):
        task = self.make_task("relative-template")
        task.template_ref = str(Path(task.template_ref).relative_to(self.root))
        task.content_hash = task.compute_content_hash()

        def factory(cwd, _inference_config):
            return self.make_agent(
                cwd,
                self.model_ref,
                [
                    tool_response(
                        {"path": "solution.txt", "content": "done\n"}
                    ),
                    final_response(),
                ],
            )

        result = BenchmarkRunner(self.root / "benchmark").run(
            [task],
            self.adapter(factory),
            self.config(),
        )

        self.assertTrue(result.episodes[0].success)

    def test_invalid_tool_arguments_are_distinct_from_tool_selection(self):
        task = self.make_task("invalid-arguments")

        def factory(cwd, _inference_config):
            return self.make_agent(
                cwd,
                self.model_ref,
                [
                    tool_response({"path": "solution.txt"}),
                    final_response(),
                ],
            )

        result = BenchmarkRunner(self.root / "benchmark").run(
            [task],
            self.adapter(factory),
            self.config(),
        )
        episode = result.episodes[0]

        self.assertFalse(episode.success)
        self.assertEqual(episode.test_pass_rate, 0.0)
        self.assertEqual(episode.tool_calls, 1)
        self.assertEqual(episode.valid_tool_selections, 1)
        self.assertEqual(episode.valid_tool_arguments, 0)
        self.assertIn("test_failure", episode.bad_cases)

    def test_model_identity_mismatch_is_preserved_as_infrastructure_failure(
        self,
    ):
        task = self.make_task("model-mismatch")

        def factory(cwd, _inference_config):
            return self.make_agent(cwd, "other/model", [final_response()])

        result = BenchmarkRunner(self.root / "benchmark").run(
            [task],
            self.adapter(factory),
            self.config(),
        )
        episode = result.episodes[0]

        self.assertFalse(episode.success)
        self.assertEqual(episode.bad_cases, ["environment_or_infra"])
        self.assertIn("agent model mismatch", episode.error)


if __name__ == "__main__":
    unittest.main()
