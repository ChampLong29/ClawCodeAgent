from .models import OrderLine


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
