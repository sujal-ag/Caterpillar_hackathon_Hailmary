"""`POST /env/ground`, `POST /env/manual` (plan.md §5.7, Phase 5 item 7; HLD §6.12 MANUAL
source). Each publishes an `env.v1` frame on `cat/{site}/env`, the same path as any other env
source: the runtime stores it once as `environment_obs` and every engine updates `wet`/heat.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from common.timeutil import to_site_iso
from edge.api.auth import Principal, require

router = APIRouter(tags=["env"])


class Ground(BaseModel):
    ground_condition: Literal["DRY", "WET", "MUDDY"]


class Manual(BaseModel):
    temp_c: float = Field(ge=-40, le=60)  # physical plausibility, not a threshold
    rh_pct: float = Field(ge=0, le=100)


async def _publish(rt, fields: dict) -> dict:
    frame = {
        "schema": "env.v1",
        "ts": to_site_iso(rt.clock()),
        "site_id": rt.site_id,
        "source": "MANUAL",
        **fields,
    }
    await rt.bus.publish(f"cat/{rt.site_id}/env", frame)
    return frame


@router.post("/env/ground")
async def env_ground(
    body: Ground, request: Request, who: Principal = Depends(require("operator"))
) -> dict:
    """One-tap ground condition; WET makes `wet` true (R06 three-point prompt)."""
    return await _publish(request.app.state.rt, body.model_dump())


@router.post("/env/manual")
async def env_manual(
    body: Manual, request: Request, who: Principal = Depends(require("operator"))
) -> dict:
    """Manual temperature/RH when no sensor or feed is available (heat index, dew point)."""
    return await _publish(request.app.state.rt, body.model_dump())
