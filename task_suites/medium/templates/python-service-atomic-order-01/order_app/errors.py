class InvalidOrderError(ValueError):
    pass


class UnknownSkuError(LookupError):
    pass


class InsufficientStockError(RuntimeError):
    pass
