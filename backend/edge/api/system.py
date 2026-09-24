"""`/health`, `/system/status` (plan.md §5.7). Status reports what is true right now,
including what is *not* working (disabled rules, broker down, auth off, models missing)."""

import asyncio

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from common.timeutil import now_utc, to_site_iso
from edge.api.auth import Principal, require
from edge.ml.adapter import status as ml_status

router = APIRouter(tags=["system"])


class MachineStatus(BaseModel):
    state_class: str | None
    data_stale: bool | None
    sensor_health: dict | None
    disabled_rules: dict
    alerts_per_operating_hour: float | None
    dropped_frames: int
    restarts: int
    last_restart_s: float | None


class SystemStatus(BaseModel):
    as_of: str
    service: str
    site_id: str
    machines: dict[str, MachineStatus]
    mqtt: dict
    sync: dict
    models: dict
    model_detail: dict  # loaded versions + why a model is UNAVAILABLE (Phase 6)
    sim: dict
    auth_disabled: bool
    rules_version: int


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "edge-api", "ts": to_site_iso(now_utc())}


@router.get("/system/status", response_model=SystemStatus)
async def system_status(
    request: Request, _: Principal = Depends(require("operator"))
) -> SystemStatus:
    rt = request.app.state.rt
    machines = {}
    for mid, runner in rt.runners.items():
        e = runner.engine
        m = e.metrics()
        machines[mid] = MachineStatus(
            state_class=e.state and e.state["class"],
            data_stale=e.state and e.state["data_stale"],
            sensor_health=e.state and e.state["sensor_health"],
            disabled_rules=m["disabled_rules"],
            alerts_per_operating_hour=m["alerts_per_operating_hour"],
            dropped_frames=m["dropped_frames"],
            restarts=runner.restarts,
            last_restart_s=runner.last_restart_s,
        )
    counts = await asyncio.to_thread(rt.sync_counts)
    bus = rt.bus
    return SystemStatus(
        as_of=to_site_iso(rt.clock()),
        service="edge-api",
        site_id=rt.site_id,
        machines=machines,
        mqtt={
            "connected": getattr(bus, "connected", None),
            "dropped_in": getattr(bus, "dropped_in", 0),
            "dropped_out": getattr(bus, "dropped_out", 0),
        },
        sync={"online": False, "pending_by_priority": counts, "queue_depth": sum(counts.values())},
        models={**ml_status(), "llm": rt.llm_status},
        model_detail=rt.ml.versions(),
        sim={"mode": rt.settings.sim_mode, "running": rt.replayer.current},
        auth_disabled=rt.settings.auth_disabled,
        rules_version=rt.ruleset.version,
    )
