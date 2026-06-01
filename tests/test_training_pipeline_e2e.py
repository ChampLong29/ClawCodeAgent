"""End-to-end pilot test for the training data flywheel.

Verifies the complete pipeline using a mocked model client:

    Task suite (JSON)
      -> RolloutRunner / AgentEnv (with FakeModelClient)
      -> Sandbox writes + tests + diff
      -> compute_reward (test pass rate + diff accuracy)
      -> RolloutRunner.export_to_jsonl
      -> SlimeDataAdapter.to_slime_sample / export_sft_dataset / export_rl_dataset
      -> ReviewerAgent.combined_reward (5-way weighted)

Goals:
  1. Prove rewards actually distinguish a successful rollout from a failing
     one (i.e., the data flywheel is alive — not returning a flat constant).
  2. Prove SFT / RL exports produce valid JSONL with the expected schema.
  3. Prove the 5-way combined_reward formula assigns more weight to a
     better trajectory.

This test does NOT call any real LLM. The "model" is a scripted
``FakeOpenAIClient`` that emits a predetermined sequence of tool calls.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from typing import Any, Dict, List, Optional

# Ensure src/ on path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from claw.training.tasks import TaskSuite, CodingTask
from claw.training.sandbox import SandboxManager
from claw.training.agent_env import AgentEnv
from claw.training.runner import RolloutRunner, RolloutConfig, RolloutResult
from claw.training.slime_adapter import SlimeDataAdapter
from claw.training.reviewer import ReviewerAgent, ReviewReport, ReviewScore
from claw.training.determinism import DeterministicConfig


SAMPLE_SUITE = os.path.join(ROOT, "examples", "training", "sample_suite.json")


# ---------------------------------------------------------------------------
# FakeOpenAIClient — scripted model responses
# ---------------------------------------------------------------------------

class FakeOpenAIClient:
    """Drop-in replacement for OpenAICompatClient.

    Returns canned ``complete(...)`` responses from a queue. Each entry is
    a full response dict matching the shape ``LocalCodingAgent.run`` reads
    (content, tool_calls, usage). When the queue empties, returns a "done"
    response with no tool_calls so the agent loop terminates.
    """

    def __init__(self, scripted: List[Dict[str, Any]], model: str = "fake-model"):
        self._queue: List[Dict[str, Any]] = list(scripted)
        self.model = model
        self.call_count = 0

    def complete(self, *args, **kwargs) -> Dict[str, Any]:
        self.call_count += 1
        if self._queue:
            return self._queue.pop(0)
        # Default terminal response
        return {
            "content": "Done.",
            "tool_calls": None,
            "usage": {"input_tokens": 1, "output_tokens": 1, "model_calls": 1},
        }

    def stream(self, *args, **kwargs):
        # Not used (we run with stream=False), but agent code may probe.
        yield self.complete()


def _scripted_write_then_done(file_path: str, content: str) -> List[Dict[str, Any]]:
    """Two-step script: call write_file, then declare done."""
    return [
        {
            "content": "I'll create the file.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"path": file_path, "content": content}),
                    },
                }
            ],
            "usage": {"input_tokens": 50, "output_tokens": 20, "model_calls": 1},
        },
        # Final response (no tool_calls) terminates the run loop
        {
            "content": "Created hello.py successfully.",
            "tool_calls": None,
            "usage": {"input_tokens": 30, "output_tokens": 10, "model_calls": 1},
        },
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSampleSuiteLoads(unittest.TestCase):
    """Smoke test: the example suite parses cleanly."""

    def test_load(self):
        self.assertTrue(os.path.exists(SAMPLE_SUITE), f"missing: {SAMPLE_SUITE}")
        suite = TaskSuite.load_from_json(SAMPLE_SUITE)
        self.assertEqual(len(suite), 2)
        ids = [t.id for t in suite]
        self.assertIn("task_easy_hello", ids)
        self.assertIn("task_fail_demo", ids)


class TestSandboxRewardSignalAlive(unittest.TestCase):
    """Direct sandbox + reward exercise — no model loop.

    Proves: given a sandbox with the *correct* file vs the *wrong* file,
    `AgentEnv.compute_reward`-equivalent logic returns clearly different
    rewards. This is the heart of the flywheel: the reward signal must
    reflect outcome quality.
    """

    def setUp(self):
        self.mgr = SandboxManager()
        self.suite = TaskSuite.load_from_json(SAMPLE_SUITE)
        self.task_pass = self.suite.get_task_by_id("task_easy_hello")
        self.task_fail = self.suite.get_task_by_id("task_fail_demo")

    def _reward_for(self, file_content: str, task: CodingTask) -> Dict[str, Any]:
        path = self.mgr.create_sandbox(task_id=task.id)
        try:
            # Simulate what a model rollout would have produced: write file.
            with open(os.path.join(path, "hello.py"), "w") as f:
                f.write(file_content)

            test_res = self.mgr.execute_tests(path, task.test_commands)
            diff_res = self.mgr.compute_diff(path, task.ground_truth_files)

            test_score = test_res.passed_tests / max(test_res.total_tests, 1)
            diff_score = diff_res.matches / max(diff_res.total, 1)
            reward = 0.5 * test_score + 0.5 * diff_score

            return {
                "reward": reward,
                "test_passed": test_res.passed_tests,
                "test_total": test_res.total_tests,
                "diff_matches": diff_res.matches,
                "diff_total": diff_res.total,
            }
        finally:
            self.mgr.cleanup(path)

    def test_correct_solution_gets_high_reward(self):
        out = self._reward_for("print('hello')\n", self.task_pass)
        self.assertGreaterEqual(out["reward"], 0.95)
        self.assertEqual(out["test_passed"], 1)
        self.assertEqual(out["diff_matches"], 1)

    def test_wrong_solution_gets_low_reward(self):
        # File runs (test passes) but content is wrong (diff fails).
        out = self._reward_for("print('goodbye')\n", self.task_fail)
        # test_score = 1.0, diff_score = 0.0 -> reward = 0.5. Strictly < 1.
        self.assertLess(out["reward"], 0.6)
        self.assertEqual(out["diff_matches"], 0)

    def test_broken_solution_gets_zero_test_score(self):
        # File doesn't even run (syntax error)
        out = self._reward_for("print('hello'\n", self.task_fail)  # missing paren
        self.assertEqual(out["test_passed"], 0)
        self.assertEqual(out["diff_matches"], 0)
        self.assertLess(out["reward"], 0.1)

    def test_reward_distinguishes_good_from_bad(self):
        good = self._reward_for("print('hello')\n", self.task_pass)
        bad = self._reward_for("print('goodbye')\n", self.task_fail)
        self.assertGreater(good["reward"], bad["reward"] + 0.3,
                           "reward must clearly separate good from bad outcomes")


class TestRolloutRunnerWithMockedModel(unittest.TestCase):
    """Full path: RolloutRunner -> AgentEnv -> LocalCodingAgent.run with a
    fake client. Verifies messages get recorded, reward gets computed,
    and JSONL export carries the expected schema.
    """

    def setUp(self):
        self.suite = TaskSuite.load_from_json(SAMPLE_SUITE)
        self.task_pass = self.suite.get_task_by_id("task_easy_hello")

    def _run_with_fake(self, file_content: str, task: CodingTask) -> RolloutResult:
        """Run one episode using a fresh AgentEnv whose agent's client we
        replace with a FakeOpenAIClient post-construction.
        """
        import time
        sandbox_mgr = SandboxManager()
        det = DeterministicConfig(
            temperature=0.0,
            session_id=f"pilot/{task.id}",
            seed=42,
        )
        env = AgentEnv(sandbox_manager=sandbox_mgr, deterministic=det, model_name="fake-model")

        try:
            obs = env.reset(task)
            # Inject fake client AFTER the agent has been constructed.
            env._agent.client = FakeOpenAIClient(
                _scripted_write_then_done("hello.py", file_content),
                model="fake-model",
            )

            start = time.time()
            obs, reward, done, info = env.step()
            return RolloutResult(
                task_id=task.id,
                session_id=obs.session_id,
                stop_reason=obs.stop_reason,
                reward=reward,
                messages=obs.messages,
                usage=info.get("usage", {}),
                error=info.get("error"),
                execution_time=time.time() - start,
                test_result=info.get("test_result"),
                diff_result=info.get("diff_result"),
            )
        finally:
            env.close()

    def test_successful_rollout_high_reward(self):
        result = self._run_with_fake("print('hello')\n", self.task_pass)
        self.assertEqual(result.stop_reason, "completed")
        self.assertGreaterEqual(result.reward, 0.95,
                                f"expected high reward, got {result.reward}; messages={result.messages[:3]}")
        # The fake script produced one tool_call, so messages must contain it.
        self.assertTrue(any(m.get("role") == "tool" for m in result.messages),
                        "tool message not recorded in session")
        # Test/diff results were filled in
        self.assertIsNotNone(result.test_result)
        self.assertIsNotNone(result.diff_result)
        self.assertEqual(result.test_result["passed_tests"], 1)
        self.assertEqual(result.diff_result["matches"], 1)

    def test_failing_rollout_low_reward(self):
        result = self._run_with_fake("print('wrong')\n", self.task_pass)
        self.assertEqual(result.stop_reason, "completed")
        # test passes (file runs) but diff fails -> ~0.5
        self.assertLess(result.reward, 0.6)
        self.assertEqual(result.diff_result["matches"], 0)

    def test_jsonl_export_schema(self):
        good = self._run_with_fake("print('hello')\n", self.task_pass)
        bad = self._run_with_fake("print('wrong')\n", self.task_pass)

        runner = RolloutRunner(config=RolloutConfig(num_workers=1))
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            out = f.name
        try:
            runner.export_to_jsonl([good, bad], out)
            lines = [json.loads(line) for line in open(out, encoding="utf-8")]
            self.assertEqual(len(lines), 2)
            for entry in lines:
                # Required schema fields
                for key in ("task_id", "session_id", "reward", "messages",
                            "usage", "test_result", "diff_result"):
                    self.assertIn(key, entry, f"missing key: {key}")
                self.assertIsInstance(entry["reward"], (int, float))
                self.assertIsInstance(entry["messages"], list)
            # Confirm reward separation persists through serialization
            reward_good = next(e["reward"] for e in lines if e["reward"] > 0.6)
            reward_bad = next(e["reward"] for e in lines if e["reward"] <= 0.6)
            self.assertGreater(reward_good, reward_bad + 0.3)
        finally:
            os.unlink(out)


class TestSlimeAdapterColdStartAndRL(unittest.TestCase):
    """Cold-start (SFT) filtering and RL export, both directions."""

    def setUp(self):
        # Build mock samples representing two trajectories
        self.good_msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "write hello.py"},
            {"role": "assistant", "content": "ok",
             "tool_calls": [{"name": "write_file", "arguments": {"path": "hello.py"}}]},
            {"role": "tool", "content": "written"},
            {"role": "assistant", "content": "done"},
        ]
        self.good_sample = SlimeDataAdapter.to_slime_sample(
            self.good_msgs, reward=0.95, task_id="task_easy_hello", domain="cli-tool",
        ).to_dict()
        self.bad_sample = SlimeDataAdapter.to_slime_sample(
            self.good_msgs, reward=0.4, task_id="task_fail_demo", domain="cli-tool",
        ).to_dict()

    def test_sft_filter_keeps_only_high_reward(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            out = f.name
        try:
            n = SlimeDataAdapter.export_sft_dataset(
                [self.good_sample, self.bad_sample], out, min_reward=0.8,
            )
            self.assertEqual(n, 1)
            lines = [json.loads(line) for line in open(out, encoding="utf-8")]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["metadata"]["task_id"], "task_easy_hello")
            # SFT export drops reward
            self.assertNotIn("reward", lines[0])
        finally:
            os.unlink(out)

    def test_rl_export_keeps_all_with_reward(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            out = f.name
        try:
            n = SlimeDataAdapter.export_rl_dataset(
                [self.good_sample, self.bad_sample], out,
            )
            self.assertEqual(n, 2)
            lines = [json.loads(line) for line in open(out, encoding="utf-8")]
            self.assertEqual(len(lines), 2)
            rewards = sorted(e["reward"] for e in lines)
            self.assertAlmostEqual(rewards[0], 0.4, places=5)
            self.assertAlmostEqual(rewards[1], 0.95, places=5)
        finally:
            os.unlink(out)


class TestCombinedRewardWeights(unittest.TestCase):
    """Validate the 5-way reward formula in ReviewerAgent.combined_reward."""

    def test_only_test_and_diff(self):
        # When only test+diff signals are available, weights redistribute.
        # Default weights: test 0.30 + diff 0.20 = 0.50; redistribute to 1.0.
        # So with both 1.0 -> reward = 1.0
        r = ReviewerAgent.combined_reward(test_pass_rate=1.0, diff_accuracy=1.0)
        self.assertAlmostEqual(r, 1.0, places=4)

    def test_zero_signals_zero_reward(self):
        r = ReviewerAgent.combined_reward(test_pass_rate=0.0, diff_accuracy=0.0)
        self.assertAlmostEqual(r, 0.0, places=4)

    def test_with_review_report(self):
        # Review with score 1.0 should *not* worsen a perfect run
        review = ReviewReport(
            overall_score=1.0,
            dimensions={"security": ReviewScore(score=1.0, comment="ok")},
            issues=[],
            summary="",
        )
        r = ReviewerAgent.combined_reward(
            test_pass_rate=1.0, diff_accuracy=1.0, review=review,
        )
        self.assertAlmostEqual(r, 1.0, places=4)

    def test_high_better_than_low(self):
        good = ReviewerAgent.combined_reward(test_pass_rate=1.0, diff_accuracy=1.0)
        bad = ReviewerAgent.combined_reward(test_pass_rate=0.0, diff_accuracy=0.0)
        self.assertGreater(good, bad + 0.5)

    def test_reward_clamped_to_unit_interval(self):
        r = ReviewerAgent.combined_reward(test_pass_rate=2.0, diff_accuracy=2.0)
        self.assertLessEqual(r, 1.0)
        self.assertGreaterEqual(r, 0.0)


class TestEndToEndFlywheel(unittest.TestCase):
    """The whole pipeline in one shot: rollouts -> samples -> SFT + RL files.

    This is the "data flywheel pilot" the user asked for: confirm that
    starting from a task suite, with a mocked model, we can produce both
    a cold-start dataset (filtered) and an RL dataset (full) on disk.
    """

    def test_full_pipeline_produces_both_datasets(self):
        suite = TaskSuite.load_from_json(SAMPLE_SUITE)
        task = suite.get_task_by_id("task_easy_hello")

        # Two rollouts: one good, one bad (both via mocked model)
        sandbox_mgr = SandboxManager()
        results: List[RolloutResult] = []
        for content, tag in [("print('hello')\n", "good"), ("print('wrong')\n", "bad")]:
            det = DeterministicConfig(temperature=0.0, session_id=f"pilot/{tag}")
            env = AgentEnv(sandbox_manager=sandbox_mgr, deterministic=det, model_name="fake-model")
            try:
                env.reset(task)
                env._agent.client = FakeOpenAIClient(
                    _scripted_write_then_done("hello.py", content),
                )
                obs, reward, done, info = env.step()
                results.append(RolloutResult(
                    task_id=f"{task.id}_{tag}",
                    session_id=obs.session_id,
                    stop_reason=obs.stop_reason,
                    reward=reward,
                    messages=obs.messages,
                    usage=info.get("usage", {}),
                    test_result=info.get("test_result"),
                    diff_result=info.get("diff_result"),
                ))
            finally:
                env.close()

        # Convert to slime samples
        slime_samples = [
            SlimeDataAdapter.to_slime_sample(
                r.messages, reward=r.reward, task_id=r.task_id, domain="cli-tool",
            ).to_dict()
            for r in results
        ]

        with tempfile.TemporaryDirectory() as tmp:
            sft = os.path.join(tmp, "sft.jsonl")
            rl = os.path.join(tmp, "rl.jsonl")
            n_sft = SlimeDataAdapter.export_sft_dataset(slime_samples, sft, min_reward=0.8)
            n_rl = SlimeDataAdapter.export_rl_dataset(slime_samples, rl)

            # Cold-start: only the good run survives the >=0.8 filter
            self.assertEqual(n_sft, 1, f"expected 1 SFT sample, got {n_sft}")
            # RL: both runs preserved
            self.assertEqual(n_rl, 2)

            # SFT file is valid JSONL
            sft_lines = [json.loads(l) for l in open(sft, encoding="utf-8")]
            self.assertEqual(len(sft_lines), 1)
            self.assertIn("prompt", sft_lines[0])
            self.assertIn("response", sft_lines[0])

            # RL file carries reward
            rl_lines = [json.loads(l) for l in open(rl, encoding="utf-8")]
            self.assertEqual(len(rl_lines), 2)
            for line in rl_lines:
                self.assertIn("reward", line)


if __name__ == "__main__":
    unittest.main()
