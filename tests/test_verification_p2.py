"""P2 contracts for hard-gated verification and bad-case taxonomy."""

import unittest

from claw.experiment.schemas import TaskSpec
from claw.training.reviewer import ReviewReport, ReviewScore
from claw.trajectory import rollout_result_to_trajectory
from claw.verification import (
    BadCaseRecord,
    ReviewerEvidence,
    VerificationContext,
    VerificationPolicy,
    VerifierPipeline,
)


def make_task(task_id="task-1"):
    task = TaskSpec(
        task_id=task_id,
        task_version="1.0.0",
        family_id=f"family-{task_id}",
        domain="python-cli",
        task_type="fix_bug",
        difficulty="easy",
        split="train",
        prompt=f"Fix {task_id}",
        template_ref=f"templates/{task_id}",
        template_hash=(task_id.encode("utf-8").hex() + "0" * 64)[:64],
        initial_checks=["python -m unittest"],
        test_commands=["python -m unittest"],
        timeout_seconds=30,
        source="test",
        license="MIT",
    )
    task.content_hash = task.compute_content_hash()
    return task


def make_trajectory(
    *,
    task_id="task-1",
    passed_tests=1,
    total_tests=1,
    changed_files=None,
    include_tests=True,
    stop_reason="completed",
):
    result = {
        "task_id": task_id,
        "session_id": f"session-{task_id}",
        "stop_reason": stop_reason,
        "messages": [
            {"role": "user", "content": f"Fix {task_id}"},
            {
                "role": "assistant",
                "content": "editing",
                "tool_calls": [
                    {
                        "id": f"call-{task_id}",
                        "name": "write_file",
                        "arguments": {
                            "file_path": "src/main.py",
                            "content": "VALUE = 1",
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": f"call-{task_id}",
                "content": "written",
            },
            {"role": "assistant", "content": "done"},
        ],
        "diff_result": {"changed_files": changed_files or ["src/main.py"]},
    }
    if include_tests:
        result["test_result"] = {
            "passed_tests": passed_tests,
            "total_tests": total_tests,
        }
    return rollout_result_to_trajectory(result)


def policy():
    return VerificationPolicy(
        version="verifier-policy.test.v1",
        required_signals=[
            "test_pass_rate",
            "build",
            "static_check",
            "diff_scope",
            "process_permission",
            "format_schema",
        ],
    )


def context(trajectory, **facts):
    default_facts = {
        "build": True,
        "static_check": True,
        "process_trace_complete": True,
        "format_valid": True,
    }
    default_facts.update(facts)
    return VerificationContext(
        trajectory=trajectory,
        task=make_task(trajectory.header.task_ref),
        policy=policy(),
        allowed_path_patterns=["src/**"],
        facts=default_facts,
    )


def reviewer(score):
    return ReviewerEvidence(
        report=ReviewReport(
            overall_score=score,
            dimensions={"correctness": ReviewScore(score, "evidence")},
        ),
        model="reviewer-model",
        prompt_version="review-prompt.v1",
        session_id="review-session-1",
        reviewer_version="reviewer.v1",
    )


class TestVerifierPipeline(unittest.TestCase):
    def test_all_hard_signals_pass_with_versioned_reviewer(self):
        report = VerifierPipeline().verify(
            context(make_trajectory()), reviewer=reviewer(0.9)
        )
        self.assertEqual(report.verdict, "success")
        self.assertTrue(report.hard_gate_passed)
        self.assertGreaterEqual(report.aggregate_score, 0.9)
        self.assertEqual(report.reviewer_metadata["model"], "reviewer-model")
        self.assertEqual(report.bad_cases, [])

    def test_reviewer_cannot_override_hard_test_failure(self):
        report = VerifierPipeline().verify(
            context(make_trajectory(passed_tests=0)), reviewer=reviewer(1.0)
        )
        self.assertEqual(report.verdict, "failure")
        self.assertFalse(report.hard_gate_passed)
        self.assertEqual(report.bad_cases[0]["primary_category"], "test_failure")

    def test_missing_required_hard_signal_is_not_verifiable(self):
        report = VerifierPipeline().verify(
            context(make_trajectory(include_tests=False))
        )
        self.assertEqual(report.verdict, "not_verifiable")
        self.assertFalse(report.hard_gate_passed)

    def test_out_of_scope_diff_is_hard_failure(self):
        report = VerifierPipeline().verify(
            context(make_trajectory(changed_files=["tests/hidden_test.py"]))
        )
        self.assertEqual(report.verdict, "failure")
        self.assertEqual(report.bad_cases[0]["primary_category"], "over_editing")
        diff_signal = next(
            signal for signal in report.signals if signal.name == "diff_scope"
        )
        self.assertEqual(
            diff_signal.details["violations"], ["tests/hidden_test.py"]
        )

    def test_environment_failure_is_a_hard_gate(self):
        report = VerifierPipeline().verify(
            context(make_trajectory(), environment_valid=False)
        )
        self.assertEqual(report.verdict, "failure")
        self.assertEqual(
            report.bad_cases[0]["primary_category"], "environment_or_infra"
        )

    def test_output_schema_is_validated_without_external_dependency(self):
        report = VerifierPipeline().verify(
            context(
                make_trajectory(),
                format_valid=None,
                output_schema={
                    "type": "object",
                    "required": ["status"],
                    "properties": {"status": {"type": "string"}},
                },
                output_value={"wrong": True},
            )
        )
        self.assertEqual(report.verdict, "failure")
        signal = next(
            item for item in report.signals if item.name == "format_schema"
        )
        self.assertIn("missing required property status", signal.details["errors"][0])

    def test_low_reviewer_score_is_recorded_but_does_not_break_hard_gate(self):
        report = VerifierPipeline().verify(
            context(make_trajectory()), reviewer=reviewer(0.4)
        )
        self.assertEqual(report.verdict, "success")
        self.assertTrue(report.hard_gate_passed)
        self.assertEqual(report.bad_cases[0]["primary_category"], "review_quality")

    def test_human_override_appends_revision_without_erasing_automatic_record(self):
        report = VerifierPipeline().verify(
            context(make_trajectory(passed_tests=0))
        )
        bad_case = BadCaseRecord.from_dict(report.bad_cases[0])
        bad_case.revise(
            primary_category="task_understanding",
            secondary_categories=["test_failure"],
            author="human-reviewer",
            reason="The test failure is downstream of a misunderstood requirement.",
        )
        data = bad_case.to_dict()
        self.assertEqual(data["primary_category"], "test_failure")
        self.assertEqual(
            data["effective_primary_category"], "task_understanding"
        )
        self.assertEqual(len(data["revisions"]), 1)


if __name__ == "__main__":
    unittest.main()
