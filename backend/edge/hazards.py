"""Hazard pins: store, version, expire, publish (HLD §4.12, §6.11; plan.md Phase 5 item 6).

Every change is one DbWriter unit of work: the pin row (version + 1, `updated_at`) and its
P0 outbox row together (I7). After it, `publish()` puts the full ACTIVE list on the RETAINED
topic `cat/{site}/hazards`. Engines learn pins only from that topic (`Runtime._consume_hazards`),
never from the DB directly, so a second edge on the same broker gets the same list (plan.md §2).

Resolved, expired and deleted pins drop out of the retained list; the DB keeps the row as a
tombstone (`deleted=true` for delete/expiry). Hazard LWW merge between edges is cut (24 h plan).
"""

from datetime import datetime, timedelta

from sqlmodel import Session, select

from common.db import repo
from common.db.models import HazardPin
from common.ids import uuid7
from common.timeutil import to_site_iso
from edge import live


def topic(site_id: str) -> str:
    return f"cat/{site_id}/hazards"


async def publish(rt) -> dict:
    payload = await live.hazards(rt)
    await rt.bus.publish(topic(rt.site_id), payload, retain=True)
    return payload


def _enqueue(session: Session, row: HazardPin) -> dict:
    data = row.model_dump()
    repo.enqueue_sync(
        session, "hazard_pin", row.pin_id, "DELETE" if row.deleted else "UPSERT", data
    )
    return data


def expiry_for(pin_type: str, now: datetime, policy: dict) -> str | None:
    if pin_type not in policy["expiring_types"]:
        return None  # lines/utilities don't expire (HLD §6.11)
    return to_site_iso(now + timedelta(hours=policy["expiry_h"]))


def new_pin(site_id: str, body: dict, created_by: str, now: datetime, policy: dict) -> dict:
    ts = to_site_iso(now)
    return {
        "pin_id": uuid7(),
        "site_id": site_id,
        "type": body["type"],
        "geometry": body["geometry"],
        "radius_m": body.get("radius_m"),
        "line_clearance_m": body.get("line_clearance_m"),
        "created_by": created_by,
        "created_at": ts,
        "confirmations": 0,
        "expires_at": expiry_for(body["type"], now, policy),
        "status": "ACTIVE",
        "version": 1,
        "updated_at": ts,
        "deleted": False,
    }


def insert(session: Session, pin: dict) -> dict:
    row = HazardPin(**pin)
    session.add(row)
    return _enqueue(session, row)


def change(session: Session, pin_id: str, action: str, now: datetime, policy: dict) -> dict | None:
    """CONFIRM | RESOLVE | DELETE on a live pin. None if it doesn't exist or is gone."""
    row = session.get(HazardPin, pin_id)
    if row is None or row.deleted or row.status != "ACTIVE":
        return None
    if action == "CONFIRM":  # another operator saw it: still there, restart the 24 h clock
        row.confirmations += 1
        row.expires_at = expiry_for(row.type, now, policy)
    elif action == "RESOLVE":
        row.status = "RESOLVED"
    elif action == "DELETE":
        row.deleted = True
    row.version += 1
    row.updated_at = to_site_iso(now)
    session.add(row)
    return _enqueue(session, row)


def expire_due(session: Session, site_id: str, now: datetime) -> list[str]:
    """Tombstone every ACTIVE pin whose `expires_at` has passed (HLD §4.12 auto-expire)."""
    rows = session.exec(
        select(HazardPin).where(
            HazardPin.site_id == site_id,
            HazardPin.status == "ACTIVE",
            HazardPin.deleted == False,  # noqa: E712 - SQL expression
            HazardPin.expires_at != None,  # noqa: E711 - SQL expression
        )
    ).all()
    expired = []
    for row in rows:
        if datetime.fromisoformat(row.expires_at) <= now:
            row.deleted, row.version, row.updated_at = True, row.version + 1, to_site_iso(now)
            session.add(row)
            _enqueue(session, row)
            expired.append(row.pin_id)
    return expired


async def expire(rt) -> list[str]:
    now = rt.clock()
    expired = await rt.write(lambda s: expire_due(s, rt.site_id, now))
    if expired:
        await publish(rt)
    return expired
