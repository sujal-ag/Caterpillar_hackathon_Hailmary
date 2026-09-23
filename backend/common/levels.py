"""Alert level order (HLD §4.4, §6.11)."""

LEVELS = ("INFO", "CAUTION", "WARNING", "CRITICAL")
RANK = {level: i for i, level in enumerate(LEVELS)}
