"""`POST /readiness` (HLD §4.7, §7.4; plan.md Phase 5 item 3) → `readiness.v1`.

The browser sends the numbers (reaction test, sleep/feeling taps, optional camera long
blinks); frames never leave the tablet. Baseline = median of the operator's previous checks
once there are `baseline_min_checks`, else the population default ("no personal baseline
yet"). Heat comes from the site's current environment; unknown heat = NONE (no penalty).
The rating is advisory: no endpoint reads it to refuse anything (I10). If this operator's
shift on the machine is ACTIVE, the engine gets the rating now (R20 on RED); otherwise
`/shift/start` hands it over.
"""

import statistics
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from common.db import repo
from common.db.models import ReadinessCheck, Shift
from common.ids import uuid7
from common.shifts import current_shift
from common.timeutil import SITE_TZ, to_site_iso
from edge.api.auth import Principal, require
from edge.api.shift import machine_for
from edge.ingest.env import heat_level
from edge.readiness.score import score

router = APIRouter(tags=["readiness"])


class ReadinessIn(BaseModel):
    machine_id: str | None = None  # default: the token's machine
    sleep_last_24h_h: float = Field(ge=0, le=24)
    sleep_last_48h_h: float = Field(ge=0, le=48)
    feel_score: int = Field(ge=1, le=5)
    rt_mean_ms: float = Field(gt=0, le=5000)
    rt_sd_ms: float | None = Field(default=None, ge=0)
    rt_lapses: int = Field(ge=0, le=100)
    camera_used: bool = False
    long_blinks_20s: int | None = Field(default=None, ge=0)


class ReadinessOut(BaseModel):
    """readiness.v1"""

    schema_: str = Field("readiness.v1", alias="schema")
    check_id: str
    operator_id: str
    shift_id: str | None
    ts: str
    sleep_last_24h_h: float
    sleep_last_48h_h: float
    feel_score: int
    rt_mean_ms: float
    rt_sd_ms: float | None
    rt_lapses: int
    rt_delta_vs_baseline_pct: float | None
    baseline_source: str
    camera_used: bool
    long_blinks_20s: int | None
    heat_level: str
    score: int
    rating: str
    reasons: list[str]
    supervisor_override: str | None = None

    model_config = {"populate_by_name": True}


def baseline(session: Session, operator_id: str, params: dict) -> tuple[float, str]:
    rts = session.exec(
        select(ReadinessCheck.rt_mean_ms).where(ReadinessCheck.operator_id == operator_id)
    ).all()
    if len(rts) >= params["baseline_min_checks"]:
        return statistics.median(rts), "PERSONAL"
    return params["population_default_rt_ms"], "POPULATION_DEFAULT"


def _store(session: Session, rt, mid: str, who: Principal, body: ReadinessIn, now: datetime, env):
    params = rt.ruleset.readiness
    base_rt, source = baseline(session, who.sub, params)
    heat = (
        heat_level(env["heat_index_c"]) if env and env.get("heat_index_c") is not None else "NONE"
    )
    inputs = body.model_dump(exclude={"machine_id"})
    result = score(inputs, base_rt, heat, params)
    reasons = result["reasons"]
    if source == "POPULATION_DEFAULT":
        reasons = [*reasons, "readiness.reason.no_personal_baseline"]
    shift = current_shift(session, mid, now, SITE_TZ)
    row = ReadinessCheck(
        check_id=uuid7(),
        operator_id=who.sub,
        shift_id=shift.shift_id if shift else None,
        ts=to_site_iso(now),
        **inputs,
        rt_delta_vs_baseline_pct=result["rt_delta_vs_baseline_pct"],
        baseline_source=source,
        heat_level=heat,
        score=result["score"],
        rating=result["rating"],
        reasons=reasons,
    )
    session.add(row)
    data = row.model_dump()
    repo.enqueue_sync(session, "readiness_check", row.check_id, "UPSERT", data)  # RED -> P0
    active = shift is not None and shift.status == "ACTIVE" and shift.operator_id == who.sub
    if active:
        _link(session, shift, row.check_id)
    return data, active


def _link(session: Session, shift: Shift, check_id: str) -> None:
    shift.readiness_check_id = check_id
    session.add(shift)
    repo.enqueue_sync(session, "shift", shift.shift_id, "UPSERT", shift.model_dump())


@router.post("/readiness", response_model=ReadinessOut, response_model_by_alias=True)
async def submit(
    body: ReadinessIn, request: Request, who: Principal = Depends(require("operator"))
) -> ReadinessOut:
    rt = request.app.state.rt
    mid = machine_for(rt, who, body.machine_id)
    runner, now = rt.runners[mid], rt.clock()
    env = runner.engine.ctx.env
    data, active = await rt.write(lambda s: _store(s, rt, mid, who, body, now, env))
    if active:
        await runner.call(lambda e, t: e.set_readiness(data["rating"], t))
    return ReadinessOut(**data)
