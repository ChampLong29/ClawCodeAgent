"""Tests for session naming utilities."""

import unittest

from claw.session_naming import make_session_id, make_project_dir_name


class TestSessionNaming(unittest.TestCase):
    def test_english_goal(self):
        sid = make_session_id("Build a club management system")
        self.assertIn("club-management-system", sid)
        # Should have a 4-char hash suffix
        parts = sid.split("-")
        self.assertEqual(len(parts[-1]), 4)

    def test_chinese_goal(self):
        sid = make_session_id("实现学生社团管理系统 web 端")
        # Chinese goal falls back to "project-xxxx"
        self.assertTrue(sid.startswith("project-") or
                        any(c.isascii() and c.isalpha() for c in sid[:5]))

    def test_prefix_override(self):
        sid = make_session_id("实现学生社团管理系统", "lifecycle")
        self.assertTrue(sid.startswith("lifecycle-"))

    def test_mixed_goal(self):
        sid = make_session_id("Build a Web 应用 for club management")
        # "a" is 1 char and filtered out by {2,} regex
        self.assertIn("build", sid.lower())
        self.assertIn("web", sid.lower())
        self.assertIn("club", sid.lower())

    def test_short_goal(self):
        sid = make_session_id("Todo app")
        self.assertIn("todo-app", sid.lower())

    def test_unique_ids(self):
        ids = {make_session_id("Build app") for _ in range(20)}
        # All should be unique due to hash suffix
        self.assertEqual(len(ids), 20)

    def test_project_dir_english(self):
        name = make_project_dir_name("Build a club management system")
        self.assertIn("club-management-system", name)
        self.assertNotIn(" ", name)

    def test_project_dir_chinese(self):
        name = make_project_dir_name("实现学生社团管理系统")
        self.assertTrue(name.startswith("project-"))
        self.assertEqual(len(name), len("project-") + 8)  # "project-" + 8-char hash


if __name__ == "__main__":
    unittest.main()
