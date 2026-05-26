"""Tests for bundled skills."""

import unittest
from claw.bundled_skills import BUNDLED_SKILLS, BundledSkill, get_skill, list_skills


class TestBundledSkills(unittest.TestCase):
    """Test bundled skills registry and lookup."""

    def test_get_skill_general(self):
        """All 4 general skills are registered and findable."""
        for name in ["explain-code", "review-code", "generate-tests", "document-code"]:
            skill = get_skill(name)
            self.assertIsNotNone(skill, f"{name} should be registered")
            self.assertIsInstance(skill, BundledSkill)
            self.assertEqual(skill.name, name)

    def test_get_skill_devflow(self):
        """All 5 DevFlow skills are registered."""
        for name in ["devflow-architect", "devflow-step-planner",
                     "devflow-step-analyzer", "devflow-implementer", "devflow-verifier"]:
            skill = get_skill(name)
            self.assertIsNotNone(skill, f"{name} should be registered")
            self.assertEqual(skill.name, name)

    def test_get_skill_lifecycle(self):
        """All 6 lifecycle skills are registered."""
        for name in ["lifecycle-requirements", "lifecycle-design",
                     "lifecycle-code-review", "lifecycle-unit-test",
                     "lifecycle-integration-test", "lifecycle-acceptance"]:
            skill = get_skill(name)
            self.assertIsNotNone(skill, f"{name} should be registered")
            self.assertEqual(skill.name, name)

    def test_total_skill_count(self):
        """At least 15 skills total."""
        skills = list_skills()
        self.assertGreaterEqual(len(skills), 15,
                                f"Expected >=15 skills, got {len(skills)}")

    def test_get_skill_nonexistent(self):
        self.assertIsNone(get_skill("nonexistent-skill"))
        self.assertIsNone(get_skill(""))

    def test_list_skills_no_duplicates(self):
        skills = list_skills()
        names = [s.name for s in skills]
        self.assertEqual(len(names), len(set(names)),
                         "Skill names should be unique")

    # --- Skill structure ---

    def test_skill_has_required_attrs(self):
        skill = get_skill("explain-code")
        self.assertTrue(hasattr(skill, "name"))
        self.assertTrue(hasattr(skill, "description"))
        self.assertTrue(hasattr(skill, "prompt"))
        self.assertTrue(hasattr(skill, "parameters"))

    def test_skill_prompt_is_non_empty(self):
        for skill in list_skills():
            self.assertIsNotNone(skill.prompt, f"{skill.name}: prompt should not be None")
            self.assertGreater(len(skill.prompt.strip()), 0,
                               f"{skill.name}: prompt should not be empty")

    def test_skill_parameters_is_dict(self):
        for skill in list_skills():
            self.assertIsNotNone(skill.parameters, f"{skill.name}: parameters should not be None")
            self.assertIsInstance(skill.parameters, dict,
                                  f"{skill.name}: parameters should be a dict")

    # --- Prompt formatting ---

    def test_explain_code_formatting(self):
        skill = get_skill("explain-code")
        result = skill.prompt.format(code="print('hello')", language="python")
        self.assertIn("print('hello')", result)

    def test_devflow_architect_formatting(self):
        skill = get_skill("devflow-architect")
        result = skill.prompt.format(goal="Build auth", constraints="Use JWT")
        self.assertIn("Build auth", result)
        self.assertIn("Use JWT", result)

    def test_lifecycle_requirements_formatting(self):
        skill = get_skill("lifecycle-requirements")
        result = skill.prompt.format(goal="Build API", constraints="REST only")
        self.assertIn("Build API", result)
        self.assertIn("REST only", result)

    def test_lifecycle_acceptance_formatting(self):
        skill = get_skill("lifecycle-acceptance")
        result = skill.prompt.format(
            goal="Build API",
            requirements_summary="FR-1: REST API, FR-2: Auth",
            implementation_summary="Implemented all endpoints",
        )
        self.assertIn("Build API", result)
        self.assertIn("FR-1", result)
        self.assertIn("Implemented all endpoints", result)

    # --- BundledSkill serialization ---

    def test_to_dict(self):
        skill = get_skill("explain-code")
        d = skill.to_dict()
        self.assertEqual(d["name"], "explain-code")
        self.assertIn("description", d)
        self.assertIn("prompt", d)
        self.assertIn("parameters", d)


if __name__ == "__main__":
    unittest.main()
