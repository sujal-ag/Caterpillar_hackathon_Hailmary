"""Daily purge of telemetry_sample older than 7 days (plan.md Phase 2 item 9).

A function, not a scheduled job — Phase 11 wires the daily trigger into edge/main.py's
lifespan (or a cron-style asyncio loop); this module just does the delete correctly.
"""

from datetime import timedelta

from sqlmodel import Session, delete

from common.db.models import TelemetrySample
from common.timeutil import now_utc, to_site_iso

RETENTION_DAYS = 7


def purge_old_samples(session: Session, now=None, retention_days: int = RETENTION_DAYS) -> int:
    cutoff = to_site_iso((now or now_utc()) - timedelta(days=retention_days))
    result = session.exec(delete(TelemetrySample).where(TelemetrySample.ts < cutoff))
    session.commit()
    return result.rowcount or 0
