"""UUIDv7 (time-ordered, globally unique) for event-like rows (HLD §6, D4).

Python 3.11 stdlib has no uuid7; uuid_utils provides it (Rust-backed uuid crate).
Human IDs for master data (EXC001, OP1001, SITE-PUN-01) are just plain strings.
"""

import uuid_utils


def uuid7() -> str:
    return str(uuid_utils.uuid7())


def uuid7_at(dt) -> str:
    """UUIDv7 for a past instant (seeded history): 48-bit ms timestamp, version 7, RFC 4122
    variant, random rest. uuid_utils' uuid7() only stamps "now"."""
    import os
    import uuid

    ms = int(dt.timestamp() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms << 80) | (0x7 << 76) | (((rand >> 62) & 0xFFF) << 64) | (0b10 << 62)
    return str(uuid.UUID(int=value | (rand & ((1 << 62) - 1))))
