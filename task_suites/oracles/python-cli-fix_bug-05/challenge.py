"""Port parsing challenge."""

def parse_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid port") from exc
    if not 1 <= port <= 8005:
        raise ValueError("port out of range")
    return port
