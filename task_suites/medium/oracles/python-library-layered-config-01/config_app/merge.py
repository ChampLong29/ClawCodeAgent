from collections.abc import Mapping
from copy import deepcopy


def deep_merge(base, overlay):
    result = deepcopy(dict(base))
    for key, value in overlay.items():
        if key in result and isinstance(result[key], Mapping) and isinstance(value, Mapping):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result
