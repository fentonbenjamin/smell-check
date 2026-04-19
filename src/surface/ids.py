"""Prefixed time-sortable ID generation.

Format: {prefix}_{timestamp_hex}_{random_hex}
"""

import secrets
import time


def generate_id(prefix: str) -> str:
    """Generate a prefixed, time-sortable unique ID.

    Args:
        prefix: Type prefix (e.g. "snap", "art", "evt", "view")

    Returns:
        ID string like "snap_018d5f3a1b_4f2a1c"
    """
    timestamp_hex = format(int(time.time() * 1000), "x")
    random_hex = secrets.token_hex(3)
    return f"{prefix}_{timestamp_hex}_{random_hex}"
