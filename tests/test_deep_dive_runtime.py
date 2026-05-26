"""Tests for DeepDiveRuntime."""

import os
import tempfile
import unittest

from claw.deep_dive_runtime import (
    DeepDiveRuntime,
    DeepDiveSession,
    DeepDiveQuery,
)


class TestDeepDiveQuery(unittest.TestCase):
    def test_serialization_roundtrip(self):
        q = DeepDiveQuery(
            id="dd-123",
            technology="PostgreSQL",
            context="primary database",
            result="## Analysis\nPostgreSQL is...",
            status="completed",
        )
        data = q.to_dict()
        q2 = DeepDiveQuery.from_dict(data)
        self.assertEqual(q2.id, "dd-123")
        self.assertEqual(q2.technology, "PostgreSQL")
        self.assertEqual(q2.status, "completed")


class TestDeepDiveSession(unittest.TestCase):
    def test_serialization_roundtrip(self):
        s = DeepDiveSession(
            session_id="ds-1",
            parent_phase="ARCHITECTURE",
            parent_session_id="life-abc",
            queries=[DeepDiveQuery(id="dd-1", technology="Redis")],
        )
        data = s.to_dict()
        s2 = DeepDiveSession.from_dict(data)
        self.assertEqual(s2.session_id, "ds-1")
        self.assertEqual(len(s2.queries), 1)


class TestTechnologyExtraction(unittest.TestCase):
    def test_extract_common_backend(self):
        text = "We recommend using FastAPI for the backend, PostgreSQL as the database, and Redis for caching."
        techs = DeepDiveRuntime.extract_technologies(text)
        self.assertIn("FastAPI", techs)
        self.assertIn("PostgreSQL", techs)
        self.assertIn("Redis", techs)

    def test_extract_unique(self):
        text = "React is great. React has many features. React is popular."
        techs = DeepDiveRuntime.extract_technologies(text)
        self.assertEqual(techs.count("React"), 1)

    def test_extract_from_architecture_output(self):
        text = """## Technology Choices
        - **Backend**: FastAPI (Python)
        - **Database**: PostgreSQL with SQLAlchemy ORM
        - **Cache**: Redis
        - **Frontend**: React with TypeScript
        - **Message Queue**: Kafka
        """
        techs = DeepDiveRuntime.extract_technologies(text)
        self.assertIn("FastAPI", techs)
        self.assertIn("PostgreSQL", techs)
        self.assertIn("Redis", techs)
        self.assertIn("React", techs)
        self.assertIn("Kafka", techs)
        self.assertIn("SQLAlchemy", techs)

    def test_extract_empty(self):
        techs = DeepDiveRuntime.extract_technologies("No special tech here.")
        self.assertEqual(techs, [])


class TestDeepDiveRuntime(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.rt = DeepDiveRuntime(cwd=self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_start_session(self):
        s = self.rt.start_session("ARCHITECTURE", "parent-1")
        self.assertEqual(s.parent_phase, "ARCHITECTURE")

    def test_add_query(self):
        self.rt.start_session("ARCHITECTURE", "parent-1")
        q = self.rt.add_query("PostgreSQL")
        self.assertEqual(q.technology, "PostgreSQL")
        self.assertEqual(q.status, "pending")
        self.assertEqual(len(self.rt.session.queries), 1)

    def test_cancel_query(self):
        self.rt.start_session("ARCHITECTURE", "parent-1")
        q = self.rt.add_query("Redis")
        self.rt.cancel_query(q.id)
        reloaded = self.rt.session.queries[0]
        self.assertEqual(reloaded.status, "failed")

    def test_save_and_load(self):
        self.rt.start_session("ARCHITECTURE", "parent-1")
        self.rt.add_query("MongoDB")
        sid = self.rt.session.session_id

        rt2 = DeepDiveRuntime(cwd=self.tmpdir)
        s = rt2.load(sid)
        self.assertEqual(len(s.queries), 1)
        self.assertEqual(s.queries[0].technology, "MongoDB")

    def test_format_for_parent(self):
        self.rt.start_session("ARCHITECTURE", "parent-1")
        self.rt.session.queries.append(
            DeepDiveQuery(
                id="dd-1", technology="FastAPI",
                result="FastAPI is a modern Python web framework...",
                status="completed",
            )
        )
        output = self.rt.format_for_parent()
        self.assertIn("FastAPI", output)
        self.assertIn("Deep-Dive Results", output)

    def test_format_for_parent_specific_query(self):
        self.rt.start_session("ARCHITECTURE", "parent-1")
        self.rt.session.queries = [
            DeepDiveQuery(id="dd-1", technology="A", result="Result A", status="completed"),
            DeepDiveQuery(id="dd-2", technology="B", result="Result B", status="completed"),
        ]
        output = self.rt.format_for_parent("dd-1")
        self.assertIn("Result A", output)
        self.assertNotIn("Result B", output)

    def test_get_result(self):
        self.rt.start_session("ARCHITECTURE", "parent-1")
        self.rt.session.queries.append(
            DeepDiveQuery(id="dd-1", technology="X", result="analysis", status="completed")
        )
        result = self.rt.get_result("dd-1")
        self.assertEqual(result, "analysis")

    def test_runtimebase_methods(self):
        self.assertIsNone(self.rt.get_state())
        summary = self.rt.render_summary()
        self.assertIn("No active session", summary)
        guidance = self.rt.get_prompt_guidance()
        self.assertEqual(guidance, "")


if __name__ == "__main__":
    unittest.main()
