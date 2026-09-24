"""`/shift/start`, `/shift/end`, `/shift/current` (HLD §6.6, §10 handover note; plan.md Phase 5
item 2). One day shift per machine per site-local date, `SH-YYYYMMDD-{machine}-D`, planned
window from SHIFT_START/SHIFT_END. /shift/start creates today's row if there is none (a login
past midnight lands on a new date with no seeded shift). The engine is told who is operating
(tags telemetry/events/alerts) and gets the operator's latest readiness rating (R20 fires on
RED at shift start). Readiness never blocks a start (I10).
"""

import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from common.db import repo
from common.db.models import ReadinessCheck, Shift
from common.shifts import current_shift, planned_window, shift_id_for
from common.timeutil import SITE_TZ, to_site_iso
from edge.api.auth import Principal, require
from edge.ml import eta_service

router = APIRouter(tags=["shift"])


class StartRequest(BaseModel):
    machine_id: str | None = None  # default: the token's machine


class EndRequest(BaseModel):
    machine_id: str | None = None
    handover_note: str | None = Field(default=None, max_length=2000)


class ShiftOut(BaseModel):
    shift: dict | None
    previous_handover_note: str | None
    as_of: str


def machine_for(rt, who: Principal, machine_id: str | None) -> str:
    mid = machine_id or who.machine_id
    if mid is None:
        raise HTTPException(422, "machine_id required (token is not bound to a machine)")
    if mid not in rt.runners:
        raise HTTPException(404, f"unknown machine {mid}")
    if not who.may_see(mid):
        raise HTTPException(403, f"token is bound to machine {who.machine_id}")
    return mid


def latest_readiness(session: Session, operator_id: str, since: datetime) -> ReadinessCheck | None:
    return session.exec(
        select(ReadinessCheck)
        .where(ReadinessCheck.operator_id == operator_id, ReadinessCheck.ts >= to_site_iso(since))
        .order_by(ReadinessCheck.ts.desc())
    ).first()


def _previous_note(session: Session, machine_id: str) -> str | None:
    """The note the last closed shift on this machine left for whoever operates it next."""
    q = select(Shift).where(
        Shift.machine_id == machine_id,
        Shift.status == "CLOSED",
        Shift.handover_note != None,  # noqa: E711 - SQL expression
    )
    row = session.exec(q.order_by(Shift.actual_end.desc())).first()
    return row.handover_note if row else None


def _start(session: Session, rt, mid: str, who: Principal, now: datetime, hours) -> dict:
    local_date = now.astimezone(SITE_TZ).date()
    active = current_shift(session, mid, now, SITE_TZ)
    if active is not None and active.status == "ACTIVE":
        if active.operator_id != who.sub:
            raise HTTPException(409, f"{active.shift_id} is active for {active.operator_id}")
        linked = active.readiness_check_id and session.get(
            ReadinessCheck, active.readiness_check_id
        )
        return {**active.model_dump(), "_readiness": linked.rating if linked else None}
    sid = shift_id_for(mid, local_date)
    row = session.get(Shift, sid)
    if row is not None and row.status == "CLOSED":
        raise HTTPException(409, f"{sid} is already closed for today")
    if row is None:
        start, end = planned_window(
            local_date, rt.settings.shift_start, rt.settings.shift_end, SITE_TZ
        )
        row = Shift(
            shift_id=sid,
            operator_id=who.sub,
            machine_id=mid,
            site_id=rt.site_id,
            planned_start=to_site_iso(start),
            planned_end=to_site_iso(end),
        )
    midnight = datetime.combine(local_date, datetime.min.time(), tzinfo=SITE_TZ)
    check = latest_readiness(session, who.sub, midnight)
    row.operator_id = who.sub
    row.status = "ACTIVE"
    row.actual_start = to_site_iso(now)
    row.engine_hours_start = hours
    row.readiness_check_id = check.check_id if check else None
    session.add(row)
    data = row.model_dump()
    repo.enqueue_sync(session, "shift", sid, "UPSERT", data)
    return {**data, "_readiness": check.rating if check else None}


@router.post("/shift/start", response_model=ShiftOut)
async def shift_start(
    body: StartRequest, request: Request, who: Principal = Depends(require("operator"))
) -> ShiftOut:
    rt = request.app.state.rt
    mid = machine_for(rt, who, body.machine_id)
    runner, now = rt.runners[mid], rt.clock()
    hours = runner.engine.ctx.sig("engine_hours")  # None when stale: honest, not a guess
    shift = await rt.write(lambda s: _start(s, rt, mid, who, now, hours))
    rating = shift.pop("_readiness", None)
    await runner.call(lambda e, t: e.set_shift(shift["operator_id"], shift["shift_id"], rating, t))
    await rt.safe(eta_service.recompute(rt, mid, "shift_start"))  # never blocks the start
    note = await asyncio.to_thread(_note, rt, mid)
    return ShiftOut(shift=shift, previous_handover_note=note, as_of=to_site_iso(now))


def _end(session: Session, mid: str, who: Principal, now: datetime, hours, note) -> dict:
    row = session.exec(
        select(Shift).where(Shift.machine_id == mid, Shift.status == "ACTIVE")
    ).first()
    if row is None:
        raise HTTPException(409, f"no active shift on {mid}")
    if row.operator_id != who.sub and not who.at_least("supervisor"):
        raise HTTPException(403, f"{row.shift_id} belongs to {row.operator_id}")
    row.status, row.actual_end = "CLOSED", to_site_iso(now)
    row.engine_hours_end, row.handover_note = hours, note
    session.add(row)
    data = row.model_dump()
    repo.enqueue_sync(session, "shift", row.shift_id, "UPSERT", data)
    return data


@router.post("/shift/end", response_model=ShiftOut)
async def shift_end(
    body: EndRequest, request: Request, who: Principal = Depends(require("operator"))
) -> ShiftOut:
    rt = request.app.state.rt
    mid = machine_for(rt, who, body.machine_id)
    runner, now = rt.runners[mid], rt.clock()
    hours = runner.engine.ctx.sig("engine_hours")
    shift = await rt.write(lambda s: _end(s, mid, who, now, hours, body.handover_note))
    await runner.call(lambda e, t: e.set_shift(None, None, None, t))
    return ShiftOut(shift=shift, previous_handover_note=None, as_of=to_site_iso(now))


def _note(rt, mid: str) -> str | None:
    with Session(rt.db) as session:
        return _previous_note(session, mid)


def _current(rt, mid: str, now: datetime) -> tuple[dict | None, str | None]:
    with Session(rt.db) as session:
        row = current_shift(session, mid, now, SITE_TZ)
        shift = row.model_dump() if row else None
        return shift, _previous_note(session, mid)


@router.get("/shift/current", response_model=ShiftOut)
async def shift_current(
    request: Request,
    machine: str | None = Query(None, description="default: the token's machine"),
    who: Principal = Depends(require("operator")),
) -> ShiftOut:
    """The machine's shift now (ACTIVE, else today's row, else null) + the last handover note
    left on this machine by a previous shift."""
    rt = request.app.state.rt
    mid = machine_for(rt, who, machine)
    now = rt.clock()
    shift, note = await asyncio.to_thread(_current, rt, mid, now)
    return ShiftOut(shift=shift, previous_handover_note=note, as_of=to_site_iso(now))
