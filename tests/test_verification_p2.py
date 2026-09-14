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
    assess_final_response,
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
    final_message="done",
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
            {"role": "assistant", "content": final_message},
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
    def test_evaluation_error_is_not_mislabeled_as_test_failure(self):
        trajectory = make_trajectory()
        test_event = next(
            event for event in trajectory.events if event.event_type == "test_result"
        )
        test_event.payload["test_result"].update(
            {
                "total_tests": 0,
                "passed_tests": 0,
                "evaluation_prepared": False,
                "tests_executed": False,
                "evaluation_errors": [{"error_type": "hidden_patch_conflict"}],
            }
        )
        verification_policy = policy()
        verification_policy.required_signals.append("evaluation_integrity")
        verification_context = context(trajectory)
        verification_context.policy = verification_policy
        report = VerifierPipeline().verify(verification_context)
        signals = {signal.name: signal for signal in report.signals}

        self.assertEqual(signals["evaluation_integrity"].status, "fail")
        self.assertEqual(signals["test_pass_rate"].status, "unknown")
        self.assertIsNone(signals["test_pass_rate"].score)
        self.assertEqual(report.verdict, "failure")
        self.assertEqual(
            report.bad_cases[0]["primary_category"],
            "evaluation_preparation_failure",
        )
        self.assertNotIn(
            "test_failure", report.bad_cases[0]["secondary_categories"]
        )

    def test_all_hard_signals_pass_with_versioned_reviewer(self):
        report = VerifierPipeline().verify(
            context(make_trajectory()), reviewer=reviewer(0.9)
        )
        self.assertEqual(report.verdict, "success")
        self.assertTrue(report.hard_gate_passed)
        self.assertGreaterEqual(report.aggregate_score, 0.9)
        self.assertEqual(report.reviewer_metadata["model"], "reviewer-model")
        self.assertEqual(report.bad_cases, [])
        quality = next(
            signal for signal in report.signals
            if signal.name == "final_response_quality"
        )
        self.assertEqual(quality.kind, "soft")
        self.assertEqual(quality.status, "unknown")
        self.assertEqual(quality.details["classification"], "legacy_placeholder")

    def test_raw_tool_markup_is_advisory_failure_not_a_hard_gate(self):
        trajectory = make_trajectory()
        trajectory.header.termination.detail = (
            '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="bash">'
            "</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>"
        )
        report = VerifierPipeline().verify(context(trajectory))
        signal = next(
            item for item in report.signals
            if item.name == "final_response_quality"
        )
        self.assertEqual(signal.status, "fail")
        self.assertEqual(signal.details["classification"], "raw_tool_call_markup")
        self.assertEqual(report.verdict, "success")
        self.assertTrue(report.hard_gate_passed)
        self.assertEqual(report.bad_cases, [])

    def test_human_readable_final_response_passes_advisory_signal(self):
        trajectory = make_trajectory()
        trajectory.header.termination.detail = "Implemented the fix; all tests pass."
        report = VerifierPipeline().verify(context(trajectory))
        signal = next(
            item for item in report.signals
            if item.name == "final_response_quality"
        )
        self.assertEqual(signal.status, "pass")
        self.assertEqual(signal.details["classification"], "user_facing_text")

    def test_empty_completed_response_fails_advisory_signal(self):
        trajectory = make_trajectory()
        trajectory.header.termination.detail = ""
        report = VerifierPipeline().verify(context(trajectory))
        signal = next(
            item for item in report.signals
            if item.name == "final_response_quality"
        )
        self.assertEqual(signal.status, "fail")
        self.assertEqual(signal.details["classification"], "empty")
        self.assertTrue(report.hard_gate_passed)

    def test_thinking_only_response_is_detected(self):
        assessment = assess_final_response(
            "<think>I should run one more test.</think>", completed=True
        )
        self.assertEqual(assessment.status, "fail")
        self.assertEqual(assessment.classification, "thinking_only")

    def test_non_completed_response_quality_is_unknown(self):
        assessment = assess_final_response("partial answer", completed=False)
        self.assertEqual(assessment.status, "unknown")
        self.assertEqual(assessment.classification, "not_completed")

    def test_action_constraint_stop_is_not_mislabeled_as_infrastructure(self):
        trajectory = make_trajectory(passed_tests=0)
        trajectory.events.pop()
        trajectory.header.termination = None
        trajectory.header.finished_at = None
        trajectory.append(
            "runtime_stop",
            payload={
                "reason": "action_constraint_unsatisfied",
                "detail": "provider did not satisfy the required action constraint",
            },
        )
        trajectory.append(
            "runtime_error",
            payload={
                "error": "provider did not satisfy the required action constraint",
                "stop_reason": "stopped",
            },
        )
        trajectory.terminate(
            "cancelled",
            detail="provider did not satisfy the required action constraint",
        )
        report = VerifierPipeline().verify(context(trajectory))
        self.assertEqual(
            report.bad_cases[0]["primary_category"],
            "action_constraint_violation",
        )
        self.assertNotIn(
            "environment_or_infra",
            report.bad_cases[0]["secondary_categories"],
        )

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

    def test_budget_runtime_error_is_not_misclassified_as_infrastructure(self):
        trajectory = make_trajectory(
            passed_tests=0,
            changed_files=["scratch.py"],
            stop_reason="budget_exceeded",
        )
        trajectory.events.pop()
        trajectory.header.termination = None
        trajectory.header.finished_at = None
        trajectory.append(
            "runtime_error",
            payload={
                "stop_reason": "budget_exceeded",
                "error": "max_total_tokens exceeded",
            },
        )
        trajectory.terminate(
            "budget_exceeded", detail="max_total_tokens exceeded"
        )

        report = VerifierPipeline().verify(context(trajectory))

        self.assertEqual(
            report.bad_cases[0]["primary_category"], "budget_or_timeout"
        )
        self.assertNotIn(
            "environment_or_infra",
            report.bad_cases[0]["secondary_categories"],
        )

    def test_model_output_truncation_is_not_misclassified_as_infrastructure(self):
        trajectory = make_trajectory(passed_tests=0)
        trajectory.events.pop()
        trajectory.header.termination = None
        trajectory.header.finished_at = None
        trajectory.append(
            "runtime_stop",
            payload={
                "reason": "model_output_truncated",
                "finish_reason": "length",
                "content_present": False,
            },
        )
        trajectory.append(
            "runtime_error",
            payload={
                "stop_reason": "stopped",
                "error": (
                    "Model response reached its per-request token limit before "
                    "producing a complete response."
                ),
            },
        )
        trajectory.terminate(
            "cancelled",
            detail=(
                "Model response reached its per-request token limit before "
                "producing a complete response."
            ),
        )

        report = VerifierPipeline().verify(context(trajectory))

        self.assertEqual(
            report.bad_cases[0]["primary_category"], "budget_or_timeout"
        )
        self.assertNotIn(
            "environment_or_infra",
            report.bad_cases[0]["secondary_categories"],
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
