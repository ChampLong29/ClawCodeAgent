from .merge import deep_merge
from .schema import DEFAULTS


def load_config(file_values=None, overrides=None):
    result = deep_merge(DEFAULTS, file_values or {})
    return deep_merge(result, overrides or {})
