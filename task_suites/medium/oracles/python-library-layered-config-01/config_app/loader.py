from collections.abc import Mapping

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
