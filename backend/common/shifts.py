"""Day-shift identity and planned window (HLD §6.6; plan.md Phase 5 item 2).

One shift per machine per site-local date: `SH-YYYYMMDD-{machine}-D`. The planned window
comes from `SHIFT_START`/`SHIFT_END` (site-local HH:MM); an END at or before START means the
shift runs past midnight and ends the next day. Used by the seed, login and /shift/*.
"""

from datetime import date, datetime, time, timedelta, tzinfo


def shift_id_for(machine_id: str, local_date: date) -> str:
    return f"SH-{local_date:%Y%m%d}-{machine_id}-D"


def _hhmm(v: str) -> time:
    h, m = v.split(":")
    return time(int(h), int(m))


def planned_window(local_date: date, start: str, end: str, tz: tzinfo) -> tuple[datetime, datetime]:
    s = datetime.combine(local_date, _hhmm(start), tzinfo=tz)
    e = datetime.combine(local_date, _hhmm(end), tzinfo=tz)
    if e <= s:
        e += timedelta(days=1)
    return s, e


def current_shift(session, machine_id: str, now: datetime, tz: tzinfo):
    """The machine's shift right now: an ACTIVE one (it may have started on an earlier date,
    e.g. across midnight), else the PLANNED/CLOSED row for today's site-local date, else None."""
    from sqlmodel import select

    from common.db.models import Shift

    active = session.exec(
        select(Shift).where(Shift.machine_id == machine_id, Shift.status == "ACTIVE")
    ).first()
    if active is not None:
        return active
    return session.get(Shift, shift_id_for(machine_id, now.astimezone(tz).date()))
