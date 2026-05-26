"""Tests for DomainConfig and DomainRegistry."""

import json
import os
import tempfile
import unittest

from claw.training.domain_config import (
    DomainConfig,
    DomainRegistry,
    BUILTIN_DOMAINS,
    default_registry,
)


class TestDomainConfig(unittest.TestCase):
    def test_default_config(self):
        dc = DomainConfig(name="test")
        self.assertEqual(dc.name, "test")
        self.assertEqual(dc.test_framework, "pytest")
        self.assertEqual(dc.tech_stack, {})

    def test_to_prompt_context(self):
        dc = DomainConfig(
            name="web-backend",
            display_name="Web Backend",
            tech_stack={"framework": "FastAPI", "db": "PostgreSQL"},
            test_framework="pytest + httpx",
            review_criteria=["SQL injection prevention"],
            acceptance_checklist=["All tests pass"],
        )
        ctx = dc.to_prompt_context()
        self.assertIn("Web Backend", ctx)
        self.assertIn("FastAPI", ctx)
        self.assertIn("PostgreSQL", ctx)
        self.assertIn("pytest + httpx", ctx)
        self.assertIn("SQL injection prevention", ctx)

    def test_serialization_roundtrip(self):
        dc = DomainConfig(
            name="cli-tool",
            tech_stack={"language": "Python", "cli_framework": "click"},
            recommended_patterns=["Single responsibility"],
            review_criteria=["Exit code correctness"],
        )
        data = dc.to_dict()
        dc2 = DomainConfig.from_dict(data)
        self.assertEqual(dc2.name, "cli-tool")
        self.assertEqual(dc2.tech_stack["language"], "Python")

    def test_skill_overrides(self):
        dc = DomainConfig(
            name="custom",
            skill_overrides={"CODE_REVIEW": "custom review prompt"},
        )
        self.assertEqual(dc.skill_overrides["CODE_REVIEW"], "custom review prompt")


class TestDomainRegistry(unittest.TestCase):
    def test_register_and_get(self):
        r = DomainRegistry()
        r.register(DomainConfig(name="test-domain"))
        self.assertIsNotNone(r.get("test-domain"))
        self.assertIsNone(r.get("nonexistent"))

    def test_list_names(self):
        r = DomainRegistry()
        r.register(DomainConfig(name="b"))
        r.register(DomainConfig(name="a"))
        self.assertEqual(r.list_names(), ["a", "b"])

    def test_load_from_dir(self):
        r = DomainRegistry()
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "test.json"), "w") as f:
                json.dump({"name": "from-file", "test_framework": "unittest"}, f)
            count = r.load_from_dir(tmp)
            self.assertEqual(count, 1)
            dc = r.get("from-file")
            self.assertIsNotNone(dc)
        finally:
            import shutil
            shutil.rmtree(tmp)

    def test_load_from_nonexistent_dir(self):
        r = DomainRegistry()
        count = r.load_from_dir("/nonexistent/path")
        self.assertEqual(count, 0)


class TestBuiltinDomains(unittest.TestCase):
    def test_all_five_domains_exist(self):
        self.assertEqual(len(BUILTIN_DOMAINS), 5)
        self.assertIn("web-backend", BUILTIN_DOMAINS)
        self.assertIn("web-frontend", BUILTIN_DOMAINS)
        self.assertIn("cli-tool", BUILTIN_DOMAINS)
        self.assertIn("data-pipeline", BUILTIN_DOMAINS)
        self.assertIn("sdk-library", BUILTIN_DOMAINS)

    def test_each_domain_has_tech_stack(self):
        for name, dc in BUILTIN_DOMAINS.items():
            with self.subTest(domain=name):
                self.assertGreater(len(dc.tech_stack), 0, f"{name} has no tech stack")

    def test_each_domain_has_review_criteria(self):
        for name, dc in BUILTIN_DOMAINS.items():
            with self.subTest(domain=name):
                self.assertGreater(len(dc.review_criteria), 0, f"{name} has no review criteria")

    def test_default_registry(self):
        r = default_registry()
        self.assertEqual(len(r.list_names()), 5)


if __name__ == "__main__":
    unittest.main()
