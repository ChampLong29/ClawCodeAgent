"""Tests for SlimeDataAdapter and slime_custom_rm."""

import json
import os
import tempfile
import unittest

from src.training.slime_adapter import SlimeDataAdapter, SlimeTrainingSample
from src.training.reviewer import ReviewReport, ReviewScore


class TestSlimeTrainingSample(unittest.TestCase):
    def test_serialization(self):
        sample = SlimeTrainingSample(
            prompt=[{"role": "system", "content": "You are helpful."}],
            response=[{"role": "assistant", "content": "Hello"}],
            reward=0.85,
            metadata={"task_id": "t1"},
        )
        data = sample.to_dict()
        s2 = SlimeTrainingSample.from_dict(data)
        self.assertEqual(s2.reward, 0.85)
        self.assertEqual(len(s2.prompt), 1)
        self.assertEqual(len(s2.response), 1)


class TestSlimeDataAdapter(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_to_slime_sample(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Write hello.py"},
            {"role": "assistant", "content": "I'll create it.",
             "tool_calls": [{"name": "write_file", "arguments": {"file_path": "hello.py", "content": "print('hello')"}}]},
            {"role": "tool", "content": "File written: hello.py"},
            {"role": "assistant", "content": "Done!"},
        ]
        sample = SlimeDataAdapter.to_slime_sample(
            messages, reward=0.9, task_id="t1", domain="web-backend",
        )
        # Prompt should be system + first user
        self.assertEqual(len(sample.prompt), 2)
        self.assertEqual(sample.prompt[0]["role"], "system")
        self.assertEqual(sample.prompt[1]["role"], "user")
        # Response should be everything after
        self.assertEqual(len(sample.response), 3)
        self.assertEqual(sample.reward, 0.9)
        self.assertEqual(sample.metadata["task_id"], "t1")
        self.assertEqual(sample.metadata["domain"], "web-backend")

    def test_to_slime_sample_with_review(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "write code"},
            {"role": "assistant", "content": "here you go"},
        ]
        review = ReviewReport(
            overall_score=0.8,
            dimensions={"security": ReviewScore(0.9, "ok")},
        )
        sample = SlimeDataAdapter.to_slime_sample(
            messages, reward=0.85, review=review,
        )
        self.assertIn("review", sample.metadata)
        self.assertEqual(sample.metadata["review"]["overall_score"], 0.8)

    def test_export_sft_dataset_filters_by_reward(self):
        results = [
            {
                "prompt": [{"role": "user", "content": "task1"}],
                "response": [{"role": "assistant", "content": "ok"}],
                "reward": 0.9,
                "metadata": {},
            },
            {
                "prompt": [{"role": "user", "content": "task2"}],
                "response": [{"role": "assistant", "content": "fail"}],
                "reward": 0.3,
                "metadata": {},
            },
        ]
        path = os.path.join(self.tmpdir, "sft.jsonl")
        count = SlimeDataAdapter.export_sft_dataset(results, path, min_reward=0.8)
        self.assertEqual(count, 1)  # only the 0.9 reward sample

        # Read back and verify
        with open(path) as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)

    def test_export_rl_dataset_keeps_all(self):
        results = [
            {
                "prompt": [{"role": "user", "content": "t1"}],
                "response": [{"role": "assistant", "content": "a1"}],
                "reward": 0.9,
                "metadata": {},
            },
            {
                "prompt": [{"role": "user", "content": "t2"}],
                "response": [{"role": "assistant", "content": "a2"}],
                "reward": 0.1,
                "metadata": {},
            },
        ]
        path = os.path.join(self.tmpdir, "rl.jsonl")
        count = SlimeDataAdapter.export_rl_dataset(results, path)
        self.assertEqual(count, 2)

    def test_filter_by_quality(self):
        results = [
            {"reward": 0.9, "metadata": {"review": {"overall_score": 0.8}}},
            {"reward": 0.9, "metadata": {"review": {"overall_score": 0.5}}},
            {"reward": 0.3, "metadata": {}},
        ]
        filtered = SlimeDataAdapter.filter_by_quality(
            results, min_reward=0.8, min_review_score=0.7,
        )
        self.assertEqual(len(filtered), 1)


class TestSlimeCustomRM(unittest.TestCase):
    """Test the standalone slime_custom_rm.py reward function."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        import sys

        # Find and load slime_custom_rm.py
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rm_path = os.path.join(repo_root, "slime_custom_rm.py")
        spec = importlib.util.spec_from_file_location("slime_custom_rm", rm_path)
        cls.rm_module = importlib.util.module_from_spec(spec)
        sys.modules["slime_custom_rm"] = cls.rm_module
        spec.loader.exec_module(cls.rm_module)

    def test_compute_reward_no_files(self):
        prompt = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Write code."},
        ]
        response = [
            {"role": "assistant", "content": "I don't want to write files."},
        ]
        reward = self.rm_module.compute_reward(prompt, response)
        # No verifiable criteria → neutral reward
        self.assertEqual(reward, 0.5)

    def test_compute_reward_str_input(self):
        prompt = json.dumps([
            {"role": "user", "content": "Write hello.py"},
        ])
        response = json.dumps([
            {"role": "assistant", "content": "ok"},
        ])
        reward = self.rm_module.compute_reward(prompt, response)
        self.assertGreaterEqual(reward, 0.0)

    def test_extract_written_files(self):
        response = [
            {
                "role": "assistant",
                "content": "Creating file",
                "tool_calls": [{
                    "name": "write_file",
                    "arguments": {
                        "file_path": "src/main.py",
                        "content": "print('hello')",
                    },
                }],
            },
        ]
        files = self.rm_module.extract_written_files(response)
        self.assertIn("src/main.py", files)
        self.assertEqual(files["src/main.py"], "print('hello')")


if __name__ == "__main__":
    unittest.main()
