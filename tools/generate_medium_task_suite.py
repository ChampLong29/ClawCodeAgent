"""Generate a small, deterministic medium-complexity benchmark pilot."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from claw.episode.checkpoint import workspace_hash
from claw.experiment.schemas import TaskSpec, canonical_hash


COMMAND = "python -m unittest discover -s .claw_hidden_tests -q"
GENERATOR_REF = "tools/generate_medium_task_suite.py"


ORDER_TEMPLATE = {
    "order_app/__init__.py": "",
    "order_app/errors.py": '''class InvalidOrderError(ValueError):
    pass


class UnknownSkuError(LookupError):
    pass


class InsufficientStockError(RuntimeError):
    pass
''',
    "order_app/models.py": '''from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Product:
    sku: str
    unit_price: Decimal


@dataclass(frozen=True)
class OrderLine:
    sku: str
    quantity: int
    unit_price: Decimal
''',
    "order_app/catalog.py": '''from .errors import InsufficientStockError, UnknownSkuError


class Catalog:
    def __init__(self, products, stock):
        self._products = dict(products)
        self._stock = dict(stock)

    def product(self, sku):
        try:
            return self._products[sku]
        except KeyError as exc:
            raise UnknownSkuError(sku) from exc

    def available(self, sku):
        return self._stock.get(sku, 0)

    def reserve(self, sku, quantity):
        self.product(sku)
        if self.available(sku) < quantity:
            raise InsufficientStockError(sku)
        self._stock[sku] -= quantity
''',
    "order_app/service.py": '''from .models import OrderLine


class OrderService:
    def __init__(self, catalog):
        self.catalog = catalog

    def place_order(self, requested_lines):
        lines = []
        for sku, quantity in requested_lines:
            product = self.catalog.product(sku)
            self.catalog.reserve(sku, quantity)
            lines.append(OrderLine(sku, quantity, product.unit_price))
        return lines
''',
}

ORDER_ORACLE = {
    "order_app/models.py": '''from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Product:
    sku: str
    unit_price: Decimal


@dataclass(frozen=True)
class OrderLine:
    sku: str
    quantity: int
    unit_price: Decimal

    @property
    def subtotal(self):
        return self.unit_price * self.quantity


@dataclass(frozen=True)
class Order:
    lines: tuple[OrderLine, ...]
    total: Decimal
''',
    "order_app/catalog.py": '''from .errors import InsufficientStockError, UnknownSkuError


class Catalog:
    def __init__(self, products, stock):
        self._products = dict(products)
        self._stock = dict(stock)

    def product(self, sku):
        try:
            return self._products[sku]
        except KeyError as exc:
            raise UnknownSkuError(sku) from exc

    def available(self, sku):
        return self._stock.get(sku, 0)

    def reserve(self, sku, quantity):
        self.reserve_many({sku: quantity})

    def reserve_many(self, quantities):
        for sku, quantity in quantities.items():
            self.product(sku)
            if self.available(sku) < quantity:
                raise InsufficientStockError(sku)
        for sku, quantity in quantities.items():
            self._stock[sku] -= quantity
''',
    "order_app/service.py": '''from collections import OrderedDict
from decimal import Decimal

from .errors import InvalidOrderError
from .models import Order, OrderLine


class OrderService:
    def __init__(self, catalog):
        self.catalog = catalog

    def place_order(self, requested_lines):
        quantities = OrderedDict()
        for sku, quantity in requested_lines:
            if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
                raise InvalidOrderError("quantities must be positive integers")
            quantities[sku] = quantities.get(sku, 0) + quantity
        if not quantities:
            raise InvalidOrderError("an order must contain at least one line")

        products = {sku: self.catalog.product(sku) for sku in quantities}
        self.catalog.reserve_many(quantities)
        lines = tuple(
            OrderLine(sku, quantity, products[sku].unit_price)
            for sku, quantity in quantities.items()
        )
        return Order(lines, sum((line.subtotal for line in lines), Decimal("0")))
''',
}

ORDER_TESTS = {
    "test_order_service.py": '''import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from order_app.catalog import Catalog
from order_app.errors import InvalidOrderError, InsufficientStockError, UnknownSkuError
from order_app.models import Order, Product
from order_app.service import OrderService


class OrderServiceTests(unittest.TestCase):
    def setUp(self):
        products = {
            "pen": Product("pen", Decimal("1.20")),
            "book": Product("book", Decimal("4.50")),
        }
        self.catalog = Catalog(products, {"pen": 5, "book": 2})
        self.service = OrderService(self.catalog)

    def test_combines_duplicates_and_preserves_first_seen_order(self):
        order = self.service.place_order([("pen", 1), ("book", 1), ("pen", 2)])
        self.assertIsInstance(order, Order)
        self.assertEqual([(line.sku, line.quantity) for line in order.lines], [("pen", 3), ("book", 1)])
        self.assertEqual(order.total, Decimal("8.10"))
        self.assertEqual(self.catalog.available("pen"), 2)

    def test_insufficient_later_line_is_atomic(self):
        with self.assertRaises(InsufficientStockError):
            self.service.place_order([("pen", 2), ("book", 3)])
        self.assertEqual(self.catalog.available("pen"), 5)
        self.assertEqual(self.catalog.available("book"), 2)

    def test_unknown_sku_is_atomic(self):
        with self.assertRaises(UnknownSkuError):
            self.service.place_order([("pen", 2), ("missing", 1)])
        self.assertEqual(self.catalog.available("pen"), 5)

    def test_rejects_empty_and_invalid_quantities(self):
        for request in ([], [("pen", 0)], [("pen", -1)], [("pen", True)], [("pen", 1.5)]):
            with self.subTest(request=request), self.assertRaises(InvalidOrderError):
                self.service.place_order(request)
        self.assertEqual(self.catalog.available("pen"), 5)


if __name__ == "__main__":
    unittest.main()
'''
}

CONFIG_TEMPLATE = {
    "config_app/__init__.py": "",
    "config_app/errors.py": '''class UnknownConfigKey(KeyError):
    pass


class InvalidConfigType(TypeError):
    pass
''',
    "config_app/schema.py": '''SCHEMA = {
    "service": {"host": str, "port": int},
    "logging": {"level": str, "json": bool},
}

DEFAULTS = {
    "service": {"host": "127.0.0.1", "port": 8080},
    "logging": {"level": "INFO", "json": False},
}
''',
    "config_app/merge.py": '''def deep_merge(base, overlay):
    result = dict(base)
    result.update(overlay)
    return result
''',
    "config_app/loader.py": '''from .merge import deep_merge
from .schema import DEFAULTS


def load_config(file_values=None, overrides=None):
    result = deep_merge(DEFAULTS, file_values or {})
    return deep_merge(result, overrides or {})
''',
}

CONFIG_ORACLE = {
    "config_app/merge.py": '''from collections.abc import Mapping
from copy import deepcopy


def deep_merge(base, overlay):
    result = deepcopy(dict(base))
    for key, value in overlay.items():
        if key in result and isinstance(result[key], Mapping) and isinstance(value, Mapping):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result
''',
    "config_app/loader.py": '''from collections.abc import Mapping

from .errors import InvalidConfigType, UnknownConfigKey
from .merge import deep_merge
from .schema import DEFAULTS, SCHEMA


def _validate(values, schema, prefix=""):
    for key, value in values.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in schema:
            raise UnknownConfigKey(path)
        expected = schema[key]
        if isinstance(expected, Mapping):
            if not isinstance(value, Mapping):
                raise InvalidConfigType(path)
            _validate(value, expected, path)
        elif not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise InvalidConfigType(path)


def load_config(file_values=None, overrides=None):
    file_values = {} if file_values is None else file_values
    overrides = {} if overrides is None else overrides
    if not isinstance(file_values, Mapping) or not isinstance(overrides, Mapping):
        raise InvalidConfigType("configuration layers must be mappings")
    _validate(file_values, SCHEMA)
    _validate(overrides, SCHEMA)
    return deep_merge(deep_merge(DEFAULTS, file_values), overrides)
''',
}

CONFIG_TESTS = {
    "test_config_loader.py": '''import sys
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
'''
}


CASES = [
    {
        "task_id": "python-service-atomic-order-01",
        "family_id": "family-order-atomic-reservation",
        "domain": "python-service",
        "task_type": "fix_bug",
        "prompt": (
            "Fix the multi-module order workflow. place_order must reject empty orders and non-positive/non-integer quantities; combine duplicate SKUs in first-seen order; validate every SKU and all stock before mutating inventory; never partially reserve on failure; and return an immutable Order with tuple lines and an exact Decimal total. Add the required Order model and an atomic catalog operation. Do not weaken the domain exceptions."
        ),
        "template": ORDER_TEMPLATE,
        "oracle": ORDER_ORACLE,
        "tests": ORDER_TESTS,
    },
    {
        "task_id": "python-library-layered-config-01",
        "family_id": "family-layered-config-validation",
        "domain": "python-library",
        "task_type": "add_feature",
        "prompt": (
            "Implement the layered configuration loader across merge.py and loader.py. Precedence is overrides > file values > defaults; nested mappings must merge recursively; inputs and nested values must not be mutated or aliased; unknown keys must raise UnknownConfigKey containing the dotted path; type mismatches must raise InvalidConfigType containing the dotted path; bool is not a valid int; and both optional layers must be mappings."
        ),
        "template": CONFIG_TEMPLATE,
        "oracle": CONFIG_ORACLE,
        "tests": CONFIG_TESTS,
    },
]


def _write_files(root: Path, files: Dict[str, str]) -> None:
    for relative, content in files.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8", newline="\n")


def generate_suite(output_root: Path) -> Path:
    output_root = output_root.resolve()
    asset_root = output_root.parents[1]
    tasks = []
    for case in CASES:
        task_id = case["task_id"]
        template = output_root / "templates" / task_id
        oracle = output_root / "oracles" / task_id
        tests = output_root / "tests" / task_id
        for directory in (template, oracle, tests):
            if directory.exists():
                shutil.rmtree(directory)
            directory.mkdir(parents=True)
        _write_files(template, case["template"])
        _write_files(oracle, case["oracle"])
        _write_files(tests, case["tests"])
        task = TaskSpec(
            task_id=task_id,
            task_version="1.0.0",
            family_id=case["family_id"],
            domain=case["domain"],
            task_type=case["task_type"],
            difficulty="medium",
            split="test",
            prompt=case["prompt"],
            template_ref=template.relative_to(asset_root).as_posix(),
            template_hash=workspace_hash(template, normalize_exec=True),
            initial_checks=[COMMAND],
            test_commands=[COMMAND],
            oracle_ref=oracle.relative_to(asset_root).as_posix(),
            test_assets_ref=tests.relative_to(asset_root).as_posix(),
            test_assets_hash=workspace_hash(tests, normalize_exec=True),
            timeout_seconds=60.0,
            resource_limits={"cpu_seconds": 20, "memory_mb": 512, "processes": 8},
            source="claw-code-agent-curated-medium",
            license="MIT",
            tags=[case["domain"], case["task_type"], "medium", "hidden-tests", "multi-module"],
        )
        task.content_hash = task.compute_content_hash()
        tasks.append(task)

    validation = {
        "min_tasks": 2,
        "min_domains": 2,
        "min_task_types": 2,
        "required_splits": ["test"],
    }
    description = (
        "Curated medium-complexity Python tasks with multi-module changes, "
        "atomicity or validation constraints, and versioned hidden tests."
    )
    payload = {
        "suite_id": "claw-medium-pilot",
        "version": "1.0.0",
        "description": description,
        "generated_by": GENERATOR_REF,
        "validation": validation,
        "tasks": [task.to_dict() for task in tasks],
    }
    manifest = {
        "schema_version": "task_suite.v1",
        **payload,
        "content_hash": canonical_hash(payload),
    }
    destination = output_root / "manifest.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return destination


def main() -> None:
    print(generate_suite(PROJECT_ROOT / "task_suites" / "medium"))


if __name__ == "__main__":
    main()
