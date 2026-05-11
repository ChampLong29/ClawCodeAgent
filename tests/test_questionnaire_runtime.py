"""Tests for QuestionnaireRuntime."""

import os
import tempfile
import unittest

from claw.questionnaire_runtime import (
    QuestionnaireRuntime,
    Questionnaire,
    Question,
)


class TestQuestion(unittest.TestCase):
    def test_serialization_roundtrip(self):
        q = Question(id="q1", text="Who are the users?", answer="Devs", status="answered")
        data = q.to_dict()
        q2 = Question.from_dict(data)
        self.assertEqual(q2.id, "q1")
        self.assertEqual(q2.text, "Who are the users?")
        self.assertEqual(q2.answer, "Devs")
        self.assertEqual(q2.status, "answered")


class TestQuestionnaire(unittest.TestCase):
    def test_progress(self):
        q = Questionnaire(
            session_id="test",
            overall_goal="Build app",
            questions=[
                Question(id="q1", text="Q1", status="answered"),
                Question(id="q2", text="Q2", status="skipped"),
                Question(id="q3", text="Q3", status="pending"),
            ],
            status="active",
        )
        p = q.progress()
        self.assertEqual(p["total"], 3)
        self.assertEqual(p["answered"], 1)
        self.assertEqual(p["skipped"], 1)
        self.assertEqual(p["pending"], 1)

    def test_all_answered(self):
        q = Questionnaire(
            session_id="test",
            overall_goal="Build app",
            questions=[
                Question(id="q1", text="Q1", status="answered"),
                Question(id="q2", text="Q2", status="skipped"),
            ],
            status="active",
        )
        self.assertTrue(q.all_answered())

    def test_not_all_answered(self):
        q = Questionnaire(
            session_id="test",
            overall_goal="Build app",
            questions=[
                Question(id="q1", text="Q1", status="answered"),
                Question(id="q2", text="Q2", status="pending"),
            ],
            status="active",
        )
        self.assertFalse(q.all_answered())

    def test_get_current_question(self):
        q = Questionnaire(
            session_id="test",
            overall_goal="Build app",
            questions=[
                Question(id="q1", text="Q1"),
                Question(id="q2", text="Q2"),
            ],
            current_question_index=1,
            status="active",
        )
        cur = q.get_current_question()
        self.assertEqual(cur.id, "q2")


class TestQuestionnaireRuntime(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.rt = QuestionnaireRuntime(cwd=self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_start_session(self):
        q = self.rt.start("Build a todo app")
        self.assertEqual(q.status, "awaiting_generation")
        self.assertEqual(q.overall_goal, "Build a todo app")

    def test_save_and_load(self):
        q = self.rt.start("Build app")
        sid = q.session_id

        rt2 = QuestionnaireRuntime(cwd=self.tmpdir)
        loaded = rt2.load(sid)
        self.assertEqual(loaded.overall_goal, "Build app")

    def test_sequential_navigation(self):
        self.rt.questionnaire = Questionnaire(
            session_id="test",
            overall_goal="Build app",
            questions=[
                Question(id="q1", text="Q1"),
                Question(id="q2", text="Q2"),
                Question(id="q3", text="Q3"),
            ],
            status="active",
        )

        # Answer first
        cur = self.rt.get_current_question()
        self.assertEqual(cur.id, "q1")
        self.rt.answer_current("Answer 1")
        cur = self.rt.get_current_question()
        self.assertEqual(cur.id, "q2")

        # Go back
        self.rt.go_back()
        cur = self.rt.get_current_question()
        self.assertEqual(cur.id, "q1")
        self.assertEqual(cur.answer, "Answer 1")

        # Skip, then go_to
        self.rt.skip_current()
        cur = self.rt.get_current_question()
        self.assertEqual(cur.id, "q2")
        # Q1 should be skipped
        self.assertEqual(self.rt.questionnaire.questions[0].status, "skipped")

    def test_go_back_at_start(self):
        self.rt.questionnaire = Questionnaire(
            session_id="test",
            overall_goal="Build app",
            questions=[Question(id="q1", text="Q1")],
            status="active",
        )
        result = self.rt.go_back()
        # Stays at first question, index remains 0
        self.assertEqual(result.id, "q1")

    def test_goto(self):
        self.rt.questionnaire = Questionnaire(
            session_id="test",
            overall_goal="Build app",
            questions=[
                Question(id="q1", text="Q1"),
                Question(id="q2", text="Q2"),
                Question(id="q3", text="Q3"),
            ],
            status="active",
        )
        cur = self.rt.go_to(2)
        self.assertEqual(cur.id, "q3")

    def test_revise_answer(self):
        self.rt.questionnaire = Questionnaire(
            session_id="test",
            overall_goal="Build app",
            questions=[
                Question(id="q1", text="Q1", answer="Old", status="answered"),
                Question(id="q2", text="Q2"),
            ],
            status="active",
        )
        self.rt.revise_answer(0, "New answer")
        self.assertEqual(self.rt.questionnaire.questions[0].answer, "New answer")

    def test_finalize(self):
        self.rt.questionnaire = Questionnaire(
            session_id="test",
            overall_goal="Build app",
            questions=[
                Question(id="q1", text="Q1?", answer="A1", status="answered"),
                Question(id="q2", text="Q2?", status="skipped"),
            ],
            status="active",
        )
        result = self.rt.finalize()
        self.assertIn("Build app", result)
        self.assertIn("Q1?", result)
        self.assertIn("A1", result)
        self.assertIn("Q2?", result)

    def test_parse_empty(self):
        result = self.rt._parse_questions_json("no json here")
        self.assertEqual(result, [])

    def test_parse_valid_json(self):
        raw = '[{"id":"q1","text":"Who?"},{"id":"q2","text":"Why?"}]'
        result = self.rt._parse_questions_json(raw)
        self.assertEqual(len(result), 2)

    def test_parse_markdown_code_block(self):
        raw = 'Some text\n```json\n[{"id":"q1","text":"Who?"}]\n```\nMore text'
        result = self.rt._parse_questions_json(raw)
        self.assertEqual(len(result), 1)

    def test_runtimebase_methods(self):
        self.assertIsNone(self.rt.get_state())
        summary = self.rt.render_summary()
        self.assertIn("No active session", summary)
        guidance = self.rt.get_prompt_guidance()
        self.assertEqual(guidance, "")


if __name__ == "__main__":
    unittest.main()
