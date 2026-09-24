"""`/hazards` (HLD §4.12, §6.11, §8.2; plan.md Phase 5 item 6). Every write stores the pin +
its P0 outbox row, then republishes the full retained list; engines pick it up from the topic.

GET  /hazards?bbox=minx,miny,maxx,maxy   hazards.v1 (ACTIVE pins), any role
POST /hazards                            create (operator)
PATCH /hazards/{id} {action}             CONFIRM (any operator) | RESOLVE (creator or supervisor)
DELETE /hazards/{id}                     tombstone (supervisor)
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from shapely.geometry import box, shape

from common.db.models import HazardPin
from edge import hazards, live
from edge.api.auth import Principal, require

router = APIRouter(tags=["hazards"])

HazardType = Literal[
    "WORKER_ZONE", "OVERHEAD_LINE", "SOFT_GROUND", "BURIED_UTILITY", "TRENCH", "DROP_OFF", "OTHER"
]


class Geometry(BaseModel):
    """GeoJSON-shaped Point or Polygon in local site metres."""

    type: Literal["Point", "Polygon"]
    coordinates: list

    @model_validator(mode="after")
    def _valid(self):
        try:
            geom = shape(self.model_dump())
        except Exception as exc:  # noqa: BLE001 - shapely raises several types on bad input
            raise ValueError(f"bad geometry: {exc}") from exc
        if geom.is_empty or not geom.is_valid:
            raise ValueError("geometry is empty or invalid")
        return self


class HazardIn(BaseModel):
    type: HazardType
    geometry: Geometry
    radius_m: float | None = Field(default=None, gt=0)
    line_clearance_m: float | None = Field(default=None, gt=0)


class Patch(BaseModel):
    action: Literal["CONFIRM", "RESOLVE"]


def validate_pin(body: dict, policy: dict) -> dict:
    """Apply HLD §6.11 ranges; point pins default to 10 m. Returns the cleaned body."""
    body = dict(body)
    if body["geometry"]["type"] == "Point":
        r = body.get("radius_m") or policy["default_radius_m"]
        if not policy["radius_m_min"] <= r <= policy["radius_m_max"]:
            raise HTTPException(
                422, f"radius_m must be {policy['radius_m_min']}–{policy['radius_m_max']} m"
            )
        body["radius_m"] = r
    else:
        body["radius_m"] = None
    lc = body.get("line_clearance_m")
    if body["type"] == "OVERHEAD_LINE":
        lo, hi = policy["line_clearance_m_min"], policy["line_clearance_m_max"]
        if lc is None or not lo <= lc <= hi:
            raise HTTPException(422, f"OVERHEAD_LINE needs line_clearance_m {lo}–{hi} m")
    else:
        body["line_clearance_m"] = None
    return body


async def create(rt, body: dict, created_by: str) -> dict:
    """Validate, store (+ outbox), publish. Shared with POST /incidents `create_hazard`."""
    policy = rt.ruleset.hazards
    pin = hazards.new_pin(rt.site_id, validate_pin(body, policy), created_by, rt.clock(), policy)
    stored = await rt.write(lambda s: hazards.insert(s, pin))
    await hazards.publish(rt)
    return stored


def _bbox(value: str):
    try:
        minx, miny, maxx, maxy = (float(v) for v in value.split(","))
    except ValueError as exc:
        raise HTTPException(422, "bbox must be minx,miny,maxx,maxy (local metres)") from exc
    return box(minx, miny, maxx, maxy)


def _pin_shape(pin: dict):
    geom = shape(pin["geometry"])
    return geom.buffer(pin["radius_m"]) if pin["geometry"]["type"] == "Point" else geom


@router.get("/hazards")
async def list_hazards(
    request: Request,
    bbox: str | None = Query(None, description="minx,miny,maxx,maxy in local metres"),
    who: Principal = Depends(require("operator")),
) -> dict:
    payload = await live.hazards(request.app.state.rt)
    if bbox:
        area = _bbox(bbox)
        payload["pins"] = [p for p in payload["pins"] if _pin_shape(p).intersects(area)]
    return payload


@router.post("/hazards", status_code=201)
async def create_hazard(
    body: HazardIn, request: Request, who: Principal = Depends(require("operator"))
) -> dict:
    return await create(request.app.state.rt, body.model_dump(), who.sub)


async def _change(rt, pin_id: str, action: str, who: Principal) -> dict:
    now, policy = rt.clock(), rt.ruleset.hazards

    def unit(session):
        if action == "RESOLVE" and not who.at_least("supervisor"):
            row = session.get(HazardPin, pin_id)
            if row is not None and row.created_by != who.sub:
                raise HTTPException(403, "only the reporter or a supervisor can resolve a pin")
        return hazards.change(session, pin_id, action, now, policy)

    pin = await rt.write(unit)
    if pin is None:
        raise HTTPException(404, f"no active hazard {pin_id}")
    await hazards.publish(rt)
    return pin


@router.patch("/hazards/{pin_id}")
async def patch_hazard(
    pin_id: str, body: Patch, request: Request, who: Principal = Depends(require("operator"))
) -> dict:
    return await _change(request.app.state.rt, pin_id, body.action, who)


@router.delete("/hazards/{pin_id}")
async def delete_hazard(
    pin_id: str, request: Request, who: Principal = Depends(require("supervisor"))
) -> dict:
    return await _change(request.app.state.rt, pin_id, "DELETE", who)
