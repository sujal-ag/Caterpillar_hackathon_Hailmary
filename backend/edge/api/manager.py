"""`/manager/*`: supervisor view of operators and task assignment (HLD §4.15, §9.2 screens 4
and 7). The contract did not exist; it is written down in contracts/rest.md.

An assigned task goes on the machine's day shift for the task's site-local date (HLD §6.6:
one shift, one operator per machine per day), so the operator sees it in `GET /tasks`
after logging onto that machine. The day's PLANNED shift is created if missing. Readiness
is shown, never checked (I10). Certification is checked: it is master data, not a threshold.
"""

import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AwareDatetime, BaseModel, Field
from sqlmodel import Session, func, select

from common.db import repo
from common.db.models import (
    Lesson,
    LessonAssignment,
    Machine,
    MachineModel,
    Operator,
    ReadinessCheck,
    SafetyAlert,
    Shift,
    Task,
    TaskType,
)
from common.ids import uuid7
from common.shifts import planned_window, shift_id_for
from common.timeutil import SITE_TZ, to_site_iso
from edge.api.auth import Principal, require
from edge.ml import eta_service

router = APIRouter(tags=["manager"])

LOCKED = {"IN_PROGRESS", "DONE"}  # a started or finished task keeps its operator and time
HISTORY = 20
PROFILE = (
    "operator_id",
    "name",
    "employee_code",
    "role",
    "experience_years",
    "experience_level",
    "certified_families",
    "cert_expiry",
    "languages",
)  # never pin_hash, never persona (I9)


class TaskIn(BaseModel):
    operator_id: str
    machine_id: str
    task_type_id: str
    zone_id: str | None = Field(default=None, max_length=64)
    planned_quantity: float = Field(gt=0)
    soil_type: str | None = None
    priority: int = Field(default=3, ge=1, le=5)
    scheduled_start: AwareDatetime
    scheduled_end: AwareDatetime


class TaskPatch(BaseModel):
    operator_id: str | None = None
    machine_id: str | None = None
    zone_id: str | None = Field(default=None, max_length=64)
    planned_quantity: float | None = Field(default=None, gt=0)
    priority: int | None = Field(default=None, ge=1, le=5)
    scheduled_start: AwareDatetime | None = None
    scheduled_end: AwareDatetime | None = None


def _profile(op: Operator) -> dict:
    return {k: getattr(op, k) for k in PROFILE}


def _today(rt) -> str:
    return f"{rt.clock().astimezone(SITE_TZ):%Y%m%d}"


def _readiness(session: Session, operator_id: str, limit: int) -> list[dict]:
    rows = session.exec(
        select(ReadinessCheck)
        .where(ReadinessCheck.operator_id == operator_id)
        .order_by(ReadinessCheck.ts.desc())
        .limit(limit)
    ).all()
    return [
        {k: getattr(r, k) for k in ("check_id", "ts", "score", "rating", "reasons")} for r in rows
    ]


def _alert(a: SafetyAlert) -> dict:
    keys = ("alert_id", "ts", "machine_id", "rule_id", "level", "subject", "message_key", "slots")
    return {
        **{k: getattr(a, k) for k in keys},
        "active": a.active,
        "acknowledged_at": a.acknowledged_at,
    }


def _summary(session: Session, op: Operator, today: str, since: str) -> dict:
    shift = session.exec(
        select(Shift)
        .where(Shift.operator_id == op.operator_id, Shift.shift_id.like(f"SH-{today}-%"))
        .order_by(Shift.status)  # ACTIVE < CLOSED < PLANNED
    ).first()
    counts = dict(
        session.exec(
            select(Task.status, func.count())
            .where(Task.operator_id == op.operator_id, Task.shift_id.like(f"SH-{today}-%"))
            .group_by(Task.status)
        ).all()
    )
    alerts = session.exec(
        select(func.count()).where(
            SafetyAlert.operator_id == op.operator_id, SafetyAlert.ts >= since
        )
    ).one()
    pending = session.exec(
        select(func.count()).where(
            LessonAssignment.operator_id == op.operator_id,
            LessonAssignment.completed_at == None,  # noqa: E711 - SQL expression
        )
    ).one()
    ready = _readiness(session, op.operator_id, 1)
    return {
        **_profile(op),
        "shift": shift and {k: getattr(shift, k) for k in ("shift_id", "machine_id", "status")},
        "tasks_today": {"total": sum(counts.values()), **{k.lower(): v for k, v in counts.items()}},
        "alerts_7d": alerts,
        "lessons_pending": pending,
        "readiness": ready[0] if ready else None,
    }


def _operators(rt) -> list[dict]:
    since = to_site_iso(rt.clock() - timedelta(days=7))
    with Session(rt.db) as session:
        ops = session.exec(select(Operator).order_by(Operator.operator_id)).all()
        return [_summary(session, op, _today(rt), since) for op in ops]


@router.get("/manager/operators")
async def operators(request: Request, who: Principal = Depends(require("supervisor"))) -> dict:
    """Every operator with today's shift, task counts, 7-day alert count, pending lessons and
    the latest readiness rating (shown for context only, I10)."""
    rt = request.app.state.rt
    return {"as_of": to_site_iso(rt.clock()), "operators": await asyncio.to_thread(_operators, rt)}


def _detail(rt, operator_id: str) -> dict:
    since = to_site_iso(rt.clock() - timedelta(days=7))
    with Session(rt.db) as session:
        op = session.get(Operator, operator_id)
        if op is None:
            raise HTTPException(404, f"unknown operator {operator_id}")
        shifts = session.exec(
            select(Shift)
            .where(Shift.operator_id == operator_id)
            .order_by(Shift.shift_id.desc())
            .limit(7)
        ).all()
        tasks = session.exec(
            select(Task)
            .where(Task.operator_id == operator_id)
            .order_by(Task.scheduled_start.desc())
            .limit(HISTORY)
        ).all()
        alerts = session.exec(
            select(SafetyAlert)
            .where(SafetyAlert.operator_id == operator_id)
            .order_by(SafetyAlert.ts.desc())
            .limit(HISTORY)
        ).all()
        lessons = session.exec(
            select(LessonAssignment, Lesson)
            .join(Lesson, Lesson.lesson_id == LessonAssignment.lesson_id)
            .where(LessonAssignment.operator_id == operator_id)
            .order_by(LessonAssignment.assigned_at.desc())
            .limit(HISTORY)
        ).all()
        return {
            **_summary(session, op, _today(rt), since),
            "shifts": [s.model_dump() for s in shifts],
            "tasks": [eta_service.task_v1(t) for t in tasks],
            "alerts": [_alert(a) for a in alerts],
            "lessons": [
                {
                    **{k: getattr(a, k) for k in ("assignment_id", "reason", "assigned_at")},
                    **{k: getattr(a, k) for k in ("completed_at", "score", "attempts")},
                    "lesson_id": les.lesson_id,
                    "title_key": les.title_key,
                    "format": les.format,
                }
                for a, les in lessons
            ],
            "readiness_history": _readiness(session, operator_id, 5),
        }


@router.get("/manager/operators/{operator_id}")
async def operator_detail(
    operator_id: str, request: Request, who: Principal = Depends(require("supervisor"))
) -> dict:
    rt = request.app.state.rt
    detail = await asyncio.to_thread(_detail, rt, operator_id)
    return {"as_of": to_site_iso(rt.clock()), "operator": detail}


def _options(rt) -> dict:
    with Session(rt.db) as session:
        machines = session.exec(
            select(Machine, MachineModel).join(
                MachineModel, MachineModel.model_id == Machine.model_id
            )
        ).all()
        types = session.exec(select(TaskType).order_by(TaskType.task_type_id)).all()
        zones = session.exec(select(Task.zone_id).where(Task.zone_id != None).distinct()).all()  # noqa: E711
        return {
            "machines": [
                {
                    "machine_id": m.machine_id,
                    "model_id": m.model_id,
                    "family": mm.family,
                    "status": m.status,
                }
                for m, mm in machines
            ],
            "task_types": [
                {k: getattr(t, k) for k in ("task_type_id", "name", "unit", "machines")}
                for t in types
            ],
            "soil_types": sorted(rt.soils),
            "zones": sorted(zones),
        }


@router.get("/manager/options")
async def options(request: Request, who: Principal = Depends(require("supervisor"))) -> dict:
    """Pick-lists for the assign form: machines, task types, soils, known zones."""
    rt = request.app.state.rt
    return {"as_of": to_site_iso(rt.clock()), **await asyncio.to_thread(_options, rt)}


def _day_tasks(rt, day: str) -> list[dict]:
    with Session(rt.db) as session:
        rows = session.exec(
            select(Task, Operator.name)
            .join(Operator, Operator.operator_id == Task.operator_id)
            .where(Task.shift_id.like(f"SH-{day}-%"))
            .order_by(Task.machine_id, Task.scheduled_start)
        ).all()
        return [{**eta_service.task_v1(t), "operator_name": name} for t, name in rows]


@router.get("/manager/tasks")
async def day_tasks(
    request: Request,
    date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="site-local"),
    who: Principal = Depends(require("supervisor")),
) -> dict:
    """Every machine's tasks for one site-local day (default today), plan vs actual."""
    rt = request.app.state.rt
    day = date.replace("-", "") if date else _today(rt)
    return {"as_of": to_site_iso(rt.clock()), "tasks": await asyncio.to_thread(_day_tasks, rt, day)}


def _place(session: Session, rt, operator_id: str, machine_id: str, task_type_id: str, start):
    """Validate the assignment and return the shift the task belongs on (created PLANNED if
    the machine has none that day). 404 for unknown ids, 409 when the plan can't take it."""
    op = session.get(Operator, operator_id)
    if op is None:
        raise HTTPException(404, f"unknown operator {operator_id}")
    if op.role != "operator":
        raise HTTPException(409, f"{operator_id} is a {op.role}, not an operator")
    machine = session.get(Machine, machine_id)
    if machine is None or machine_id not in rt.runners:
        raise HTTPException(404, f"unknown machine {machine_id}")
    tt = session.get(TaskType, task_type_id)
    if tt is None:
        raise HTTPException(404, f"unknown task type {task_type_id}")
    if tt.machines and machine.model_id not in tt.machines:
        raise HTTPException(409, f"{task_type_id} is not done with a {machine.model_id}")
    family = session.get(MachineModel, machine.model_id).family
    if family not in (op.certified_families or []):
        raise HTTPException(409, f"{operator_id} is not certified for {family}")
    local = start.astimezone(SITE_TZ).date()
    if op.cert_expiry and op.cert_expiry < local.isoformat():
        raise HTTPException(409, f"{operator_id}'s certification expired on {op.cert_expiry}")
    sid = shift_id_for(machine_id, local)
    shift = session.get(Shift, sid)
    if shift is None:
        s, e = planned_window(local, rt.settings.shift_start, rt.settings.shift_end, SITE_TZ)
        shift = Shift(
            shift_id=sid,
            operator_id=operator_id,
            machine_id=machine_id,
            site_id=rt.site_id,
            planned_start=to_site_iso(s),
            planned_end=to_site_iso(e),
        )
        session.add(shift)
        repo.enqueue_sync(session, "shift", sid, "UPSERT", shift.model_dump())
    elif shift.operator_id != operator_id:
        raise HTTPException(
            409, f"{machine_id} is {shift.status.lower()} for {shift.operator_id} on {local}"
        )
    elif shift.status == "CLOSED":
        raise HTTPException(409, f"{sid} is already closed")
    return sid, tt


def _window(start: datetime, end: datetime) -> None:
    if end <= start:
        raise HTTPException(422, "scheduled_end must be after scheduled_start")


def _soil(rt, soil: str | None) -> None:
    if soil is not None and soil not in rt.soils:
        raise HTTPException(422, f"unknown soil_type {soil}")


async def _refresh(rt, machine_ids, task_id: str) -> None:
    """Re-estimate and make sure the operator's screen reloads its task list (WS `eta`)."""
    for mid in dict.fromkeys(machine_ids):
        await rt.safe(eta_service.recompute(rt, mid, "assigned"))
        pred = eta_service.prediction_of(await asyncio.to_thread(_get, rt, task_id))
        await rt.sink.publish(
            mid, "eta", {"ts": to_site_iso(rt.clock()), "task_id": task_id, "prediction": pred}
        )


def _get(rt, task_id: str) -> Task:
    with Session(rt.db) as session:
        row = session.get(Task, task_id)
    if row is None:
        raise HTTPException(404, f"unknown task {task_id}")
    return row


@router.post("/manager/tasks", status_code=201)
async def assign(
    body: TaskIn, request: Request, who: Principal = Depends(require("supervisor"))
) -> dict:
    """Create a task and assign it to an operator on a machine (outbox P1, I7)."""
    rt = request.app.state.rt
    _window(body.scheduled_start, body.scheduled_end)
    _soil(rt, body.soil_type)
    task_id = uuid7()

    def unit(session):
        sid, tt = _place(
            session, rt, body.operator_id, body.machine_id, body.task_type_id, body.scheduled_start
        )
        t = Task(
            task_id=task_id,
            shift_id=sid,
            machine_id=body.machine_id,
            operator_id=body.operator_id,
            task_type_id=body.task_type_id,
            zone_id=body.zone_id,
            planned_quantity=body.planned_quantity,
            unit=tt.unit,
            soil_type=body.soil_type,
            priority=body.priority,
            scheduled_start=to_site_iso(body.scheduled_start),
            scheduled_end=to_site_iso(body.scheduled_end),
        )
        session.add(t)
        repo.enqueue_sync(session, "task", task_id, "UPSERT", t.model_dump())

    await rt.write(unit)
    await _refresh(rt, [body.machine_id], task_id)
    return eta_service.task_v1(await asyncio.to_thread(_get, rt, task_id))


@router.patch("/manager/tasks/{task_id}")
async def reassign(
    task_id: str, body: TaskPatch, request: Request, who: Principal = Depends(require("supervisor"))
) -> dict:
    """Reassign (operator/machine) or reschedule a task that has not started."""
    rt = request.app.state.rt
    old = await asyncio.to_thread(_get, rt, task_id)

    def unit(session):
        t = session.get(Task, task_id)
        if t.status in LOCKED:
            raise HTTPException(409, f"task is {t.status}; only unstarted tasks can be changed")
        start = body.scheduled_start or datetime.fromisoformat(t.scheduled_start)
        end = body.scheduled_end or datetime.fromisoformat(t.scheduled_end)
        _window(start, end)
        t.shift_id, _ = _place(
            session,
            rt,
            body.operator_id or t.operator_id,
            body.machine_id or t.machine_id,
            t.task_type_id,
            start,
        )
        t.operator_id = body.operator_id or t.operator_id
        t.machine_id = body.machine_id or t.machine_id
        t.scheduled_start, t.scheduled_end = to_site_iso(start), to_site_iso(end)
        for k in ("zone_id", "planned_quantity", "priority"):
            if getattr(body, k) is not None:
                setattr(t, k, getattr(body, k))
        session.add(t)
        repo.enqueue_sync(session, "task", task_id, "UPSERT", t.model_dump())
        return t.machine_id

    new_mid = await rt.write(unit)
    await _refresh(rt, [old.machine_id, new_mid], task_id)
    return eta_service.task_v1(await asyncio.to_thread(_get, rt, task_id))
