SCHEMA = {
    "service": {"host": str, "port": int},
    "logging": {"level": str, "json": bool},
}

DEFAULTS = {
    "service": {"host": "127.0.0.1", "port": 8080},
    "logging": {"level": "INFO", "json": False},
}
