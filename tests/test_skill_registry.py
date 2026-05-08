"""Tests for SkillRegistry, ExternalSkill, and SkillRuntime."""

import os
import tempfile
import unittest

from src.skill_registry import (
    ExternalSkill,
    SkillRegistry,
    get_skill_registry,
    reset_skill_registry,
)


class TestExternalSkill(unittest.TestCase):
    def test_basic_creation(self):
        s = ExternalSkill(name="test", description="desc", _prompt="body")
        self.assertEqual(s.name, "test")
        self.assertEqual(s.parameters, None)
        self.assertEqual(s.source, "")

    def test_to_dict(self):
        s = ExternalSkill(name="t", description="d", _prompt="p",
                          parameters={"x": {"type": "string"}}, source="/f.md")
        d = s.to_dict()
        self.assertEqual(d["name"], "t")
        self.assertEqual(d["parameters"]["x"]["type"], "string")


class TestSkillRegistry(unittest.TestCase):
    def setUp(self):
        reset_skill_registry()
        self.registry = get_skill_registry()

    def tearDown(self):
        reset_skill_registry()

    def test_builtin_lookup(self):
        s = self.registry.get("explain-code")
        self.assertIsNotNone(s)
        self.assertEqual(s.name, "explain-code")

    def test_external_lookup(self):
        ext = ExternalSkill(name="my-skill", description="x", _prompt="body")
        self.registry.register_external(ext)
        s = self.registry.get("my-skill")
        self.assertIsNotNone(s)
        self.assertEqual(s.name, "my-skill")

    def test_builtin_takes_priority(self):
        # Register external with same name as builtin
        ext = ExternalSkill(name="explain-code", description="override", _prompt="x")
        self.registry.register_external(ext)
        s = self.registry.get("explain-code")
        # Should return builtin, not external
        self.assertNotEqual(s.description, "override")

    def test_list_names_includes_both(self):
        ext = ExternalSkill(name="zzz-custom", description="x", _prompt="body")
        self.registry.register_external(ext)
        names = self.registry.list_names()
        self.assertIn("explain-code", names)
        self.assertIn("zzz-custom", names)

    def test_unregister_external(self):
        ext = ExternalSkill(name="tmp", description="x", _prompt="body")
        self.registry.register_external(ext)
        self.assertTrue(self.registry.unregister_external("tmp"))
        self.assertIsNone(self.registry.get("tmp"))

    def test_unregister_nonexistent(self):
        self.assertFalse(self.registry.unregister_external("nope"))

    def test_register_externals_batch(self):
        skills = [
            ExternalSkill(name="a", description="", _prompt=""),
            ExternalSkill(name="b", description="", _prompt=""),
        ]
        self.registry.register_externals(skills)
        self.assertEqual(self.registry.external_count, 2)

    def test_list_all(self):
        ext = ExternalSkill(name="ext1", description="external skill", _prompt="x",
                            source="/tmp/skill.md")
        self.registry.register_external(ext)
        all_skills = self.registry.list_all()
        sources = {s["name"]: s["source"] for s in all_skills}
        self.assertEqual(sources.get("ext1"), "/tmp/skill.md")
        self.assertEqual(sources.get("explain-code"), "builtin")

    def test_singleton(self):
        r1 = get_skill_registry()
        r2 = get_skill_registry()
        self.assertIs(r1, r2)


class TestSkillRuntime(unittest.TestCase):
    def setUp(self):
        reset_skill_registry()
        self.tmpdir = tempfile.mkdtemp()
        from src.skill_runtime import SkillRuntime
        self.SkillRuntime = SkillRuntime

    def tearDown(self):
        reset_skill_registry()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_skill_dir(self, dirname, skill_name, body, params=None):
        skill_dir = os.path.join(self.tmpdir, dirname)
        os.makedirs(skill_dir, exist_ok=True)
        fm = f"---\nname: {skill_name}\ndescription: A skill\n"
        if params:
            fm += f"parameters:\n  topic:\n    type: string\n"
        fm += "---\n"
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write(fm + body)

    def _make_skill_file(self, dirname, filename, skill_name, body):
        skills_dir = os.path.join(self.tmpdir, dirname)
        os.makedirs(skills_dir, exist_ok=True)
        fm = f"---\nname: {skill_name}\ndescription: A skill\n---\n"
        with open(os.path.join(skills_dir, filename), "w") as f:
            f.write(fm + body)

    def test_discover_claw_skills_dir(self):
        self._make_skill_dir(".claw-skills/my-skill", "My Skill",
                             "# My Skill\n\nDo stuff.")
        rt = self.SkillRuntime(cwd=self.tmpdir)
        self.assertEqual(len(rt.skills), 1)
        self.assertEqual(rt.skills[0].name, "My Skill")

    def test_discover_flat_md_file(self):
        self._make_skill_file(".claw-skills", "custom.md", "Custom",
                              "# Custom\n\nCustom body.")
        rt = self.SkillRuntime(cwd=self.tmpdir)
        self.assertEqual(len(rt.skills), 1)
        self.assertEqual(rt.skills[0].name, "Custom")

    def test_discover_plugins_dir(self):
        self._make_skill_dir("plugins/tool", "Plugin Tool",
                             "# Plugin\n\nPlugin body.")
        rt = self.SkillRuntime(cwd=self.tmpdir)
        self.assertEqual(len(rt.skills), 1)
        self.assertEqual(rt.skills[0].name, "Plugin Tool")

    def test_with_parameters(self):
        self._make_skill_dir(".claw-skills/analyzer", "Analyzer",
                             "Analyze {topic} carefully.", params=True)
        rt = self.SkillRuntime(cwd=self.tmpdir)
        s = rt.skills[0]
        self.assertIsNotNone(s.parameters)
        self.assertEqual(s.parameters["topic"]["type"], "string")

    def test_no_parameters(self):
        self._make_skill_dir(".claw-skills/simple", "Simple",
                             "Just do stuff.", params=False)
        rt = self.SkillRuntime(cwd=self.tmpdir)
        self.assertIsNone(rt.skills[0].parameters)

    def test_empty_dir(self):
        os.makedirs(os.path.join(self.tmpdir, ".claw-skills"), exist_ok=True)
        rt = self.SkillRuntime(cwd=self.tmpdir)
        self.assertEqual(len(rt.skills), 0)

    def test_registers_into_global_registry(self):
        self._make_skill_dir(".claw-skills/global-test", "Global Test",
                             "body")
        self.SkillRuntime(cwd=self.tmpdir)
        r = get_skill_registry()
        self.assertIsNotNone(r.get("Global Test"))

    def test_render_summary(self):
        self._make_skill_dir(".claw-skills/s1", "S1", "body")
        self._make_skill_dir(".claw-skills/s2", "S2", "body")
        rt = self.SkillRuntime(cwd=self.tmpdir)
        summary = rt.render_summary()
        self.assertIn("S1", summary)
        self.assertIn("S2", summary)
        self.assertIn("external", summary)

    def test_get_state(self):
        self._make_skill_dir(".claw-skills/s1", "S1", "body")
        rt = self.SkillRuntime(cwd=self.tmpdir)
        state = rt.get_state()
        self.assertEqual(state["count"], 1)
        self.assertEqual(len(state["skills"]), 1)

    def test_duplicate_name_deduplication(self):
        self._make_skill_dir(".claw-skills/a", "SameName", "body1")
        self._make_skill_dir("plugins/a", "SameName", "body2")
        rt = self.SkillRuntime(cwd=self.tmpdir)
        self.assertEqual(len(rt.skills), 1)


class TestSkillIntegration(unittest.TestCase):
    """Integration: bundled_skills delegates to registry."""

    def setUp(self):
        reset_skill_registry()

    def tearDown(self):
        reset_skill_registry()

    def test_get_skill_returns_external(self):
        r = get_skill_registry()
        r.register_external(ExternalSkill(name="ext", description="d", _prompt="p"))
        from src.bundled_skills import get_skill
        s = get_skill("ext")
        self.assertIsNotNone(s)
        self.assertEqual(s.name, "ext")

    def test_list_skills_includes_externals(self):
        r = get_skill_registry()
        r.register_external(ExternalSkill(name="ext", description="d", _prompt="p"))
        from src.bundled_skills import list_skills
        names = [s.name for s in list_skills()]
        self.assertIn("ext", names)
        self.assertIn("explain-code", names)


if __name__ == "__main__":
    unittest.main()
