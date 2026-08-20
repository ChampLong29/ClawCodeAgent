from __future__ import annotations

import unittest

from claw.benchmark.metrics import BenchmarkEpisodeResult, compute_metrics
from claw.trajectory import Trajectory, TrajectoryHeader, analyze_rollout_behavior


def make_trajectory() -> Trajectory:
    trajectory = Trajectory(
        header=TrajectoryHeader(
            trajectory_id="trajectory-diagnostics",
            episode_id="episode-diagnostics",
            task_ref="task@1",
            model_version="model@revision",
            runtime_version="runtime.v1",
            prompt_version="prompt.v1",
            tool_version="tool.v1",
            config_version="config.v1",
            artifact_base="artifacts",
            started_at="2026-08-12T00:00:00+00:00",
        )
    )
    trajectory.append("model_response", payload={})
    trajectory.append(
        "tool_call",
        payload={"tool_name": "read_file", "arguments": '{"path":"src/other.py"}'},
    )
    trajectory.append("tool_result", payload={"ok": True})
    trajectory.append("model_response", payload={})
    trajectory.append(
        "tool_call",
        payload={"tool_name": "read_file", "arguments": {"path": "src/target.py"}},
    )
    trajectory.append("tool_result", payload={"ok": False})
    trajectory.append(
        "runtime_guidance",
        payload={"guidance_type": "completion_critical", "remaining_tool_turns": 2},
    )
    trajectory.append(
        "runtime_guidance",
        payload={"guidance_type": "implementation_deadline"},
    )
    trajectory.append(
        "runtime_guidance",
        payload={"guidance_type": "implementation_escalation"},
    )
    trajectory.append("model_response", payload={})
    trajectory.append(
        "tool_call",
        payload={"tool_name": "edit_file", "arguments": {"path": "src/target.py"}},
    )
    trajectory.append("tool_result", payload={"ok": True})
    trajectory.append(
        "runtime_guidance",
        payload={"guidance_type": "post_edit_contract"},
    )
    trajectory.terminate("cancelled", detail="max turns")
    return trajectory


class RolloutBehaviorDiagnosticsTests(unittest.TestCase):
    def test_extracts_localization_edit_and_guidance_timing(self):
        diagnostics = analyze_rollout_behavior(
            make_trajectory(), target_path_patterns=["src/target.py"]
        )

        self.assertEqual(diagnostics.model_turns, 3)
        self.assertEqual(diagnostics.tool_calls, 3)
        self.assertEqual(diagnostics.first_target_path_turn, 2)
        self.assertEqual(diagnostics.first_direct_mutation_turn, 3)
        self.assertEqual(diagnostics.target_to_mutation_turn_lag, 1)
        self.assertEqual(diagnostics.tool_calls_before_first_mutation, 2)
        self.assertAlmostEqual(diagnostics.investigation_without_edit_ratio, 2 / 3)
        self.assertEqual(diagnostics.failed_tool_calls, 1)
        self.assertEqual(diagnostics.off_target_path_inspection_calls, 1)
        self.assertEqual(diagnostics.completion_critical_turn, 2)
        self.assertEqual(diagnostics.implementation_deadline_turn, 2)
        self.assertEqual(diagnostics.implementation_escalation_turn, 2)
        self.assertEqual(diagnostics.post_edit_contract_guidance_turn, 3)
        self.assertEqual(diagnostics.tool_calls_after_completion_critical, 1)
        self.assertFalse(diagnostics.terminated_without_direct_mutation)

    def test_no_edit_is_explicit_and_does_not_claim_shell_safety(self):
        trajectory = Trajectory(
            header=make_trajectory().header,
        )
        trajectory.header.termination = None
        trajectory.header.finished_at = None
        trajectory.append("model_response", payload={})
        trajectory.append(
            "tool_call",
            payload={"tool_name": "bash", "arguments": {"command": "git status"}},
        )
        trajectory.append("tool_result", payload={"ok": True})
        trajectory.terminate("cancelled")

        diagnostics = analyze_rollout_behavior(trajectory)

        self.assertTrue(diagnostics.terminated_without_direct_mutation)
        self.assertEqual(diagnostics.tool_calls_before_first_mutation, 1)
        self.assertEqual(diagnostics.investigation_without_edit_ratio, 1.0)

    def test_rejected_model_tool_request_still_records_localization(self):
        trajectory = Trajectory(
            header=make_trajectory().header,
        )
        trajectory.header.termination = None
        trajectory.header.finished_at = None
        trajectory.append(
            "model_response",
            payload={
                "tool_calls": [
                    {
                        "id": "rejected-call",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"src/target.py"}',
                        },
                    }
                ]
            },
        )
        trajectory.append(
            "runtime_stop",
            payload={"reason": "action_constraint_unsatisfied"},
        )
        trajectory.terminate("cancelled")

        diagnostics = analyze_rollout_behavior(
            trajectory, target_path_patterns=["src/target.py"]
        )

        self.assertEqual(diagnostics.first_target_path_turn, 1)
        self.assertEqual(diagnostics.model_requested_tool_calls, 1)
        self.assertEqual(diagnostics.tool_calls, 0)
        self.assertEqual(diagnostics.rejected_tool_calls, 1)

    def test_benchmark_metrics_aggregate_behavior_diagnostics(self):
        diagnostics = analyze_rollout_behavior(
            make_trajectory(), target_path_patterns=["src/target.py"]
        ).to_dict()
        result = BenchmarkEpisodeResult(
            task_id="task",
            family_id="family",
            domain="python",
            difficulty="medium",
            success=False,
            test_pass_rate=0.0,
            tool_calls=3,
            valid_tool_selections=3,
            valid_tool_arguments=3,
            format_valid=True,
            process_violations=0,
            turns=3,
            input_tokens=1,
            output_tokens=1,
            token_cost=0.0,
            latency_seconds=0.1,
            behavior_diagnostics=diagnostics,
        )

        aggregate = compute_metrics([result])["behavior_diagnostics"]

        self.assertEqual(aggregate["sample_count"], 1)
        self.assertEqual(aggregate["direct_mutation_rate"], 1.0)
        self.assertEqual(aggregate["target_path_localization_rate"], 1.0)
        self.assertEqual(aggregate["average_first_target_path_turn"], 2.0)
        self.assertEqual(aggregate["implementation_escalation_rate"], 1.0)
        self.assertEqual(aggregate["average_implementation_escalation_turn"], 2.0)
        self.assertEqual(aggregate["post_edit_contract_guidance_rate"], 1.0)
        self.assertEqual(
            aggregate["average_post_edit_contract_guidance_turn"], 3.0
        )


if __name__ == "__main__":
    unittest.main()
