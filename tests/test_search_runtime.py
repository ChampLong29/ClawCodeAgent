"""Tests for search runtime."""

import unittest
import tempfile
import os
import json
from claw.search_runtime import SearchRuntime, SearchProvider


class TestSearchRuntime(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_discover_search_config(self):
        config_path = os.path.join(self.tempdir, ".claw-search.json")
        with open(config_path, "w") as f:
            json.dump({
                "providers": [
                    {"name": "test", "provider": "searxng", "baseUrl": "http://localhost:8080"}
                ]
            }, f)

        runtime = SearchRuntime(cwd=self.tempdir)
        state = runtime.get_state()
        self.assertEqual(state["count"], 1)

    def test_provider_field_name_compatibility(self):
        # Test camelCase
        p1 = SearchProvider.from_dict({"name": "t", "provider": "searxng", "baseUrl": "http://x"})
        self.assertEqual(p1.base_url, "http://x")

        # Test snake_case
        p2 = SearchProvider.from_dict({"name": "t", "provider": "searxng", "base_url": "http://y"})
        self.assertEqual(p2.base_url, "http://y")


if __name__ == "__main__":
    unittest.main()