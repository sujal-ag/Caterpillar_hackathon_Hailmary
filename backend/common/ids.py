"""UUIDv7 (time-ordered, globally unique) for event-like rows (HLD §6, D4).

Python 3.11 stdlib has no uuid7; uuid_utils provides it (Rust-backed uuid crate).
Human IDs for master data (EXC001, OP1001, SITE-PUN-01) are just plain strings.
"""

import uuid_utils


def uuid7() -> str:
    return str(uuid_utils.uuid7())
