"""Weighted total challenge."""

def weighted_total(values):
    if not isinstance(values, list):
        raise TypeError("values must be a list")
    return sum(values) * 6
