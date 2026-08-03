from collections import OrderedDict
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
