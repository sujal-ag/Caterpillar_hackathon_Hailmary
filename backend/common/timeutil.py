"""UTC in the DB, site offset on the wire (plan D22)."""

import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

# ponytail: one site per edge; moves to site.yaml in Phase 1.
SITE_TZ = ZoneInfo(os.environ.get("SITE_TZ", "Asia/Kolkata"))


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_site_iso(dt: datetime) -> str:
    return dt.astimezone(SITE_TZ).isoformat(timespec="milliseconds")


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp without offset: {s}")
    return dt
