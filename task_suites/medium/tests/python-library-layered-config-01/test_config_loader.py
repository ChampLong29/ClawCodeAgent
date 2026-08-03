import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config_app.errors import InvalidConfigType, UnknownConfigKey
from config_app.loader import load_config


class ConfigLoaderTests(unittest.TestCase):
    def test_precedence_and_recursive_merge(self):
        result = load_config(
            {"service": {"host": "file-host"}, "logging": {"level": "DEBUG"}},
            {"service": {"port": 9000}},
        )
        self.assertEqual(result["service"], {"host": "file-host", "port": 9000})
        self.assertEqual(result["logging"], {"level": "DEBUG", "json": False})

    def test_inputs_and_nested_values_are_not_mutated_or_aliased(self):
        file_values = {"service": {"host": "api"}}
        overrides = {"logging": {"json": True}}
        result = load_config(file_values, overrides)
        result["service"]["host"] = "changed"
        result["logging"]["json"] = False
        self.assertEqual(file_values, {"service": {"host": "api"}})
        self.assertEqual(overrides, {"logging": {"json": True}})

    def test_rejects_unknown_key_with_dotted_path(self):
        with self.assertRaises(UnknownConfigKey) as captured:
            load_config({"service": {"timeout": 5}})
        self.assertIn("service.timeout", str(captured.exception))

    def test_rejects_wrong_types_including_bool_as_int(self):
        for values, path in [({"service": {"port": "9000"}}, "service.port"), ({"service": {"port": True}}, "service.port"), ({"logging": "verbose"}, "logging")]:
            with self.subTest(values=values), self.assertRaises(InvalidConfigType) as captured:
                load_config(values)
            self.assertIn(path, str(captured.exception))

    def test_rejects_non_mapping_layers(self):
        with self.assertRaises(InvalidConfigType):
            load_config([], {})


if __name__ == "__main__":
    unittest.main()
