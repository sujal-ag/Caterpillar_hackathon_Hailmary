"""`/state/current`, `/alerts/{id}/ack`, `/alerts/{id}/why` (contracts/rest.md, Phase 4).
Nested payloads are the contract types (`state.v1`, `alert.v1` record, `hazards.v1`,
`sync_status.v1`) — JSON Schema in contracts/schemas is their source of truth."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlmodel import Session

from common.db.models import SafetyAlert
from common.timeutil import to_site_iso
from edge import live
from edge.api.auth import Principal, require

router = APIRouter(tags=["state"])


class Snapshot(BaseModel):
    as_of: str
    state: dict | None
    alerts: list[dict]
    hazards: dict
    sync: dict
    eta: dict | None


class Why(BaseModel):
    alert_id: str
    rule_id: str
    name: str
    level: str
    hld_ref: str
    message_key: str
    slots: dict
    inputs: dict
    thresholds: dict
    active: bool
    acknowledged_at: str | None
    as_of: str


def _runner(rt, machine_id: str, who: Principal):
    if machine_id not in rt.runners:
        raise HTTPException(404, f"unknown machine {machine_id}")
    if not who.may_see(machine_id):
        raise HTTPException(403, f"token is bound to machine {who.machine_id}")
    return rt.runners[machine_id]


@router.get("/state/current", response_model=Snapshot)
async def state_current(
    request: Request,
    machine: str = Query(..., description="machine_id, e.g. EXC001"),
    who: Principal = Depends(require("operator")),
) -> Snapshot:
    rt = request.app.state.rt
    _runner(rt, machine, who)
    return Snapshot(**await live.snapshot(rt, machine))


def _stored_alert(rt, alert_id: str) -> dict | None:
    with Session(rt.db) as session:
        row = session.get(SafetyAlert, alert_id)
        return row.model_dump() if row else None


@router.post("/alerts/{alert_id}/ack")
async def ack(
    alert_id: str, request: Request, who: Principal = Depends(require("operator"))
) -> dict:
    """Ack ≠ clear: the alert stays active until its condition is definitively false."""
    rt = request.app.state.rt
    for mid, runner in rt.runners.items():
        if any(a["alert_id"] == alert_id for a in runner.engine.alerts.active_records()):
            _runner(rt, mid, who)
            out = await runner.call(lambda e, now: e.ack(alert_id, who.sub, now))
            if out is None:
                break  # cleared between the lookup and the ack
            return dict(out.alerts[0][1])
    if await asyncio.to_thread(_stored_alert, rt, alert_id) is None:
        raise HTTPException(404, f"unknown alert {alert_id}")
    raise HTTPException(409, "alert is no longer active")


@router.get("/alerts/{alert_id}/why", response_model=Why)
async def why(
    alert_id: str, request: Request, who: Principal = Depends(require("operator"))
) -> Why:
    """The rule and the exact inputs + thresholds that fired it (HLD §10 "Why?")."""
    rt = request.app.state.rt
    live_alert = next(
        (
            a
            for r in rt.runners.values()
            for a in r.engine.alerts.active_records()
            if a["alert_id"] == alert_id
        ),
        None,
    )
    alert = live_alert or await asyncio.to_thread(_stored_alert, rt, alert_id)
    if alert is None:
        raise HTTPException(404, f"unknown alert {alert_id}")
    if alert["machine_id"] in rt.runners:
        _runner(rt, alert["machine_id"], who)
    rule = rt.ruleset.by_id(alert["rule_id"])
    snap = alert.get("state_snapshot") or {}
    return Why(
        alert_id=alert_id,
        rule_id=rule.id,
        name=rule.name,
        level=alert["level"],
        hld_ref=rule.hld_ref,
        message_key=alert["message_key"],
        slots=alert.get("slots") or {},
        inputs=snap.get("inputs", {}),
        thresholds=snap.get("thresholds", {}),
        active=alert["active"],
        acknowledged_at=alert.get("acknowledged_at"),
        as_of=to_site_iso(rt.clock()),
    )
