"""UTC in the DB, site offset on the wire (plan D22)."""

import os
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

# ponytail: one site per edge; moves to site.yaml in Phase 1.
SITE_TZ = ZoneInfo(os.environ.get("SITE_TZ", "Asia/Kolkata"))


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_site_iso(dt: datetime) -> str:
    return dt.astimezone(SITE_TZ).isoformat(timespec="milliseconds")


class MonotonicClock:
    """Engine clock: UTC anchored once at start, then advanced by `time.monotonic()`, so a
    wall-clock jump never stretches or shrinks an engine timer (CLAUDE.md "monotonic clock
    for engine timers") while timers stay plain datetimes that snapshots can persist."""

    def __init__(self):
        self._wall, self._mono = now_utc(), time.monotonic()

    def __call__(self) -> datetime:
        return self._wall + timedelta(seconds=time.monotonic() - self._mono)


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp without offset: {s}")
    return dt
