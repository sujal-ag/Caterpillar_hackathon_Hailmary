"""`/tasks`, `/tasks/{id}/status`, `/tasks/{id}/eta` (HLD §8.2; plan.md Phase 6 item 2).

Tasks are cloud-authoritative except `status` (HLD §4.13). A status change stamps
actual_start / actual_end, goes to the outbox (P1) and re-estimates the machine's ETAs.
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from common.db import repo
from common.db.models import Task
from common.timeutil import to_site_iso
from edge.api.auth import Principal, require
from edge.api.shift import machine_for
from edge.ml import eta_service

router = APIRouter(tags=["tasks"])


class StatusIn(BaseModel):
    status: Literal["SCHEDULED", "IN_PROGRESS", "PAUSED", "DONE", "BACKLOG"]
    delay_reason: (
        Literal["WEATHER", "WAITING_TRUCKS", "BREAKDOWN", "SAFETY_STOP", "OTHER"] | None
    ) = None
    actual_quantity: float | None = Field(default=None, ge=0)


def _list(rt, machine_id: str, shift_id: str | None) -> list[dict]:
    with Session(rt.db) as session:
        if shift_id:
            rows = session.exec(
                select(Task)
                .where(Task.shift_id == shift_id, Task.machine_id == machine_id)
                .order_by(Task.scheduled_start)
            ).all()
        else:
            rows = eta_service.shift_tasks(session, machine_id, rt.clock())
        return [eta_service.task_v1(t) for t in rows]


@router.get("/tasks")
async def list_tasks(
    request: Request,
    shift_id: str | None = Query(None, description="default: the machine's current shift"),
    machine: str | None = Query(None, description="default: the token's machine"),
    who: Principal = Depends(require("operator")),
) -> dict:
    rt = request.app.state.rt
    mid = machine_for(rt, who, machine)
    tasks = await asyncio.to_thread(_list, rt, mid, shift_id)
    return {"as_of": to_site_iso(rt.clock()), "tasks": tasks}


def _task(rt, task_id: str) -> Task:
    with Session(rt.db) as session:
        row = session.get(Task, task_id)
    if row is None:
        raise HTTPException(404, f"unknown task {task_id}")
    return row


@router.post("/tasks/{task_id}/status")
async def set_status(
    task_id: str, body: StatusIn, request: Request, who: Principal = Depends(require("operator"))
) -> dict:
    rt = request.app.state.rt
    task = await asyncio.to_thread(_task, rt, task_id)
    mid = machine_for(rt, who, task.machine_id)
    now = to_site_iso(rt.clock())

    def unit(session):
        t = session.get(Task, task_id)
        t.status = body.status
        if body.status == "IN_PROGRESS" and t.actual_start is None:
            t.actual_start = now
        if body.status == "DONE":
            t.actual_end = now
        if body.delay_reason is not None:
            t.delay_reason = body.delay_reason
        if body.actual_quantity is not None:
            t.actual_quantity = body.actual_quantity
        session.add(t)
        repo.enqueue_sync(session, "task", task_id, "UPSERT", t.model_dump())  # P1

    await rt.write(unit)
    await rt.safe(eta_service.recompute(rt, mid, f"status:{body.status}"))
    return eta_service.task_v1(await asyncio.to_thread(_task, rt, task_id))


@router.get("/tasks/{task_id}/eta")
async def task_eta(
    task_id: str, request: Request, who: Principal = Depends(require("operator"))
) -> dict:
    rt = request.app.state.rt
    task = await asyncio.to_thread(_task, rt, task_id)
    machine_for(rt, who, task.machine_id)
    pred = eta_service.prediction_of(task)
    if pred is None:
        raise HTTPException(404, "no prediction for this task (done, time-boxed or not estimated)")
    return {**pred, "task_id": task_id, "as_of": to_site_iso(rt.clock())}
