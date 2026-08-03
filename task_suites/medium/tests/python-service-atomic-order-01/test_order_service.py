import sys
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
