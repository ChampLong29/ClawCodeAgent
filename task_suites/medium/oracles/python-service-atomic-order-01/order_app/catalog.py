from .errors import InsufficientStockError, UnknownSkuError


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
