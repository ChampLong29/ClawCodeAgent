from dataclasses import dataclass
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
