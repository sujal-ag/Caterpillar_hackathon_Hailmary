"""`/lessons/assigned`, `/lessons/{assignment_id}`, `/lessons/{assignment_id}/complete`
(HLD §4.11, §8.2; plan.md Phase 6 item 4). `{assignment_id}` is the lesson_assignment id: the
lesson content comes with it, plus the Replay scenario built from the triggering event.

I5, checked here and not only in the UI: a lesson is never served or completed while the
operator's machine is running (409).
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from common.db import repo
from common.db.models import Lesson, LessonAssignment, Scenario
from common.timeutil import to_site_iso
from edge.api.auth import Principal, require
from edge.lessons import engine as lessons

router = APIRouter(tags=["lessons"])


class CompleteIn(BaseModel):
    score: int = Field(ge=0, le=100)
    answers: list = Field(default_factory=list, max_length=50)


def _assigned(rt, operator_id: str) -> tuple[list[dict], str | None]:
    with Session(rt.db) as session:
        rows = session.exec(
            select(LessonAssignment, Lesson)
            .join(Lesson, Lesson.lesson_id == LessonAssignment.lesson_id)
            .where(LessonAssignment.operator_id == operator_id)
            .order_by(LessonAssignment.assigned_at.desc())
        ).all()
        machine = lessons.machine_of(session, operator_id)
        out = [
            {
                **a.model_dump(),
                "lesson": {
                    "lesson_id": les.lesson_id,
                    "title_key": les.title_key,
                    "format": les.format,
                    "duration_s": les.duration_s,
                },
            }
            for a, les in rows
        ]
        return out, machine


@router.get("/lessons/assigned")
async def assigned(
    request: Request,
    operator_id: str | None = Query(None, description="supervisor: another operator"),
    who: Principal = Depends(require("operator")),
) -> dict:
    rt = request.app.state.rt
    op = operator_id or who.sub
    if op != who.sub and not who.at_least("supervisor"):
        raise HTTPException(403, "operators see only their own lessons")
    rows, machine = await asyncio.to_thread(_assigned, rt, op)
    ok = lessons.deliverable(rt, machine)
    for r in rows:
        r["deliverable"] = ok and r["completed_at"] is None
    return {"as_of": to_site_iso(rt.clock()), "assignments": rows}


def _load(rt, assignment_id: str, who: Principal):
    with Session(rt.db) as session:
        a = session.get(LessonAssignment, assignment_id)
        if a is None:
            raise HTTPException(404, f"unknown assignment {assignment_id}")
        if a.operator_id != who.sub and not who.at_least("supervisor"):
            raise HTTPException(403, "not your lesson")
        les = session.get(Lesson, a.lesson_id)
        sc = session.get(Scenario, a.scenario_id) if a.scenario_id else None
        return (
            a.model_dump(),
            les.model_dump(),
            sc and sc.model_dump(),
            lessons.machine_of(session, a.operator_id),
        )


def _check_deliverable(rt, machine: str | None) -> None:
    if not lessons.deliverable(rt, machine):
        raise HTTPException(409, "not while the machine is running — at the next safe moment (I5)")


@router.get("/lessons/{assignment_id}")
async def get_lesson(
    assignment_id: str, request: Request, who: Principal = Depends(require("operator"))
) -> dict:
    rt = request.app.state.rt
    a, les, scenario, machine = await asyncio.to_thread(_load, rt, assignment_id, who)
    _check_deliverable(rt, machine)
    now = to_site_iso(rt.clock())

    def unit(session):
        row = session.get(LessonAssignment, assignment_id)
        if row.started_at is None:
            row.started_at = now
            session.add(row)
            repo.enqueue_sync(
                session, "lesson_assignment", assignment_id, "UPSERT", row.model_dump()
            )
        return row.model_dump()

    a = await rt.write(unit)
    return {"as_of": now, "assignment": a, "lesson": les, "replay": scenario}


@router.post("/lessons/{assignment_id}/complete")
async def complete(
    assignment_id: str,
    body: CompleteIn,
    request: Request,
    who: Principal = Depends(require("operator")),
) -> dict:
    rt = request.app.state.rt
    _, _, _, machine = await asyncio.to_thread(_load, rt, assignment_id, who)
    _check_deliverable(rt, machine)
    now = to_site_iso(rt.clock())

    def unit(session):
        row = session.get(LessonAssignment, assignment_id)
        row.completed_at, row.score = now, body.score
        row.attempts += 1
        row.started_at = row.started_at or now
        session.add(row)
        data = row.model_dump()
        repo.enqueue_sync(
            session, "lesson_assignment", assignment_id, "UPSERT", {**data, "answers": body.answers}
        )
        return data

    return {"as_of": now, "assignment": await rt.write(unit)}
