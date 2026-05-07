"""Tests for ReviewerAgent and related data structures."""

import unittest

from src.training.reviewer import (
    ReviewerAgent,
    ReviewReport,
    ReviewScore,
    ReviewIssue,
    REVIEW_PROMPT,
)


class TestReviewDataStructures(unittest.TestCase):
    def test_review_issue_serialization(self):
        issue = ReviewIssue(
            severity="critical",
            dimension="security",
            file_path="src/auth.py",
            description="SQL injection in login endpoint",
            suggestion="Use parameterized queries",
        )
        data = issue.to_dict()
        i2 = ReviewIssue.from_dict(data)
        self.assertEqual(i2.severity, "critical")
        self.assertEqual(i2.file_path, "src/auth.py")

    def test_review_score_serialization(self):
        score = ReviewScore(score=0.85, comment="Good but could be better")
        data = score.to_dict()
        s2 = ReviewScore.from_dict(data)
        self.assertEqual(s2.score, 0.85)

    def test_review_report_serialization(self):
        report = ReviewReport(
            overall_score=0.78,
            dimensions={
                "security": ReviewScore(0.9, "No vulnerabilities"),
                "code_quality": ReviewScore(0.7, "Some long functions"),
            },
            issues=[
                ReviewIssue("minor", "code_quality", "src/x.py", "long function", "split it"),
            ],
            summary="Decent implementation with minor issues.",
        )
        data = report.to_dict()
        r2 = ReviewReport.from_dict(data)
        self.assertEqual(r2.overall_score, 0.78)
        self.assertEqual(len(r2.dimensions), 2)
        self.assertEqual(len(r2.issues), 1)

    def test_empty_report(self):
        rpt = ReviewReport.empty()
        self.assertEqual(rpt.overall_score, 0.5)
        self.assertEqual(rpt.summary, "Review skipped (reviewer unavailable).")

    def test_issue_counts(self):
        report = ReviewReport(
            overall_score=0.6,
            dimensions={},
            issues=[
                ReviewIssue("critical", "sec", "", "", ""),
                ReviewIssue("critical", "sec", "", "", ""),
                ReviewIssue("major", "qual", "", "", ""),
                ReviewIssue("minor", "style", "", "", ""),
            ],
        )
        self.assertEqual(report.critical_count(), 2)
        self.assertEqual(report.major_count(), 1)
        self.assertEqual(report.minor_count(), 1)


class TestReviewerAgent(unittest.TestCase):
    def test_parse_valid_json(self):
        agent = ReviewerAgent(model_config=None)
        raw = '{"overall_score":0.9,"dimensions":{},"issues":[],"summary":"good"}'
        rpt = agent._parse_review_response(raw)
        self.assertEqual(rpt.overall_score, 0.9)

    def test_parse_markdown_code_block(self):
        agent = ReviewerAgent(model_config=None)
        raw = 'Some text\n```json\n{"overall_score":0.7,"dimensions":{},"issues":[],"summary":"ok"}\n```'
        rpt = agent._parse_review_response(raw)
        self.assertEqual(rpt.overall_score, 0.7)

    def test_parse_invalid_fallback(self):
        agent = ReviewerAgent(model_config=None)
        rpt = agent._parse_review_response("not json at all")
        self.assertEqual(rpt.overall_score, 0.5)  # empty fallback


class TestCombinedReward(unittest.TestCase):
    def test_all_signals(self):
        review = ReviewReport(overall_score=0.8, dimensions={})
        reward = ReviewerAgent.combined_reward(
            test_pass_rate=1.0, diff_accuracy=0.9, review=review,
        )
        # Weights redistribute: test 0.462, diff 0.308, review 0.231
        # 1.0*0.462 + 0.9*0.308 + 0.8*0.231 ≈ 0.924
        self.assertAlmostEqual(reward, 0.924, places=2)

    def test_no_review(self):
        reward = ReviewerAgent.combined_reward(
            test_pass_rate=1.0, diff_accuracy=1.0, review=None,
        )
        # Weights redistribute: test 0.60, diff 0.40 → 1.0
        self.assertAlmostEqual(reward, 1.0, places=2)

    def test_custom_weights(self):
        review = ReviewReport(overall_score=1.0, dimensions={})
        reward = ReviewerAgent.combined_reward(
            test_pass_rate=1.0, diff_accuracy=1.0, review=review,
            weights={"test": 0.5, "diff": 0.3, "review": 0.2, "process": 0.0, "format": 0.0},
        )
        # Custom weights sum to 1.0, all signals available → 1.0
        self.assertAlmostEqual(reward, 1.0, places=2)


class TestReviewPrompt(unittest.TestCase):
    def test_prompt_has_required_sections(self):
        self.assertIn("Review Criteria", REVIEW_PROMPT)
        self.assertIn("overall_score", REVIEW_PROMPT)
        self.assertIn("Task", REVIEW_PROMPT)
        self.assertIn("Written Files", REVIEW_PROMPT)


if __name__ == "__main__":
    unittest.main()
