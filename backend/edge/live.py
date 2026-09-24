"""Current-truth views shared by the WebSocket `snapshot` and `GET /state/current`
(contracts/ws.md). Every one carries `as_of` / `ts` (I6)."""

import asyncio

from sqlmodel import Session, select

from common.db.models import HazardPin
from common.timeutil import to_site_iso


def _hazard_pins(rt) -> list[dict]:
    with Session(rt.db) as session:
        rows = session.exec(
            select(HazardPin).where(
                HazardPin.site_id == rt.site_id,
                HazardPin.deleted == False,  # noqa: E712 - SQL expression
                HazardPin.status == "ACTIVE",
            )
        ).all()
        return [r.model_dump() for r in rows]


async def hazards(rt) -> dict:
    """hazards.v1 from the `hazard_pin` table (empty until Phase 5 creates pins)."""
    pins = await asyncio.to_thread(_hazard_pins, rt)
    return {
        "schema": "hazards.v1",
        "site_id": rt.site_id,
        "as_of": to_site_iso(rt.clock()),
        "pins": pins,
    }


async def sync_status(rt) -> dict:
    """sync_status.v1. There is no sync agent until Phase 9: the queue only grows, and the
    edge reports itself offline with no last success rather than guessing."""
    counts = await asyncio.to_thread(rt.sync_counts)
    return {
        "schema": "sync_status.v1",
        "ts": to_site_iso(rt.clock()),
        "online": False,
        "forced_offline": False,
        "queue_depth": sum(counts.values()),
        "last_success_at": None,
    }


def _current_eta(rt, machine_id: str) -> dict | None:
    from edge.ml.eta_service import current_eta

    return current_eta(rt, machine_id)


async def snapshot(rt, machine_id: str) -> dict:
    engine = rt.runners[machine_id].engine
    return {
        "as_of": to_site_iso(rt.clock()),
        "state": engine.state,
        "alerts": [dict(a) for a in engine.alerts.active_records()],
        "hazards": await hazards(rt),
        "sync": await sync_status(rt),
        "eta": await asyncio.to_thread(_current_eta, rt, machine_id),
    }
