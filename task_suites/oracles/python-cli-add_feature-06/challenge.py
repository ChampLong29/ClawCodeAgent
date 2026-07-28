"""Command formatting challenge."""

def build_command(name):
    if not isinstance(name, str) or not name:
        raise ValueError("name must be a non-empty string")
    return f"run6:{name}"
