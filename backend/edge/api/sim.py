"""`/sim/scenario`, `/sim/scenarios` (contracts/sim_control.md). `SIM_MODE=proxy` forwards to
P1's simulator at `SIM_CONTROL_URL`; `SIM_MODE=replay` plays fixtures from
`SIM_SCENARIOS_DIR`. Advisory tooling only: nothing here can command the machine (I2)."""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from edge.api.auth import Principal, require
from edge.sim import UnknownScenario

router = APIRouter(tags=["sim"])


class ScenarioRequest(BaseModel):
    name: str
    machine_id: str | None = None
    speed: float = Field(1.0, gt=0, description="replay mode only: 10 = ten times faster")


class ScenarioStarted(BaseModel):
    ok: bool
    name: str
    started_at: str | None = None


class ScenarioList(BaseModel):
    mode: str
    scenarios: list[str]


def _client(rt) -> httpx.AsyncClient:
    url = rt.settings.sim_control_url
    if not url:
        raise HTTPException(503, "SIM_MODE=proxy but SIM_CONTROL_URL is not set")
    return httpx.AsyncClient(base_url=url, timeout=10, transport=getattr(rt, "sim_transport", None))


@router.get("/sim/scenarios", response_model=ScenarioList)
async def scenarios(request: Request, _: Principal = Depends(require("admin"))) -> ScenarioList:
    rt = request.app.state.rt
    if rt.settings.sim_mode == "replay":
        return ScenarioList(mode="replay", scenarios=rt.replayer.names())
    async with _client(rt) as client:
        try:
            resp = await client.get("/scenarios")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"simulator: {exc}") from exc
    return ScenarioList(mode="proxy", scenarios=resp.json()["scenarios"])


@router.post("/sim/scenario", response_model=ScenarioStarted)
async def start(
    body: ScenarioRequest, request: Request, _: Principal = Depends(require("admin"))
) -> ScenarioStarted:
    rt = request.app.state.rt
    if body.machine_id is not None and body.machine_id not in rt.runners:
        raise HTTPException(404, f"unknown machine {body.machine_id}")
    if rt.settings.sim_mode == "replay":
        try:
            return ScenarioStarted(
                **await rt.replayer.start(body.name, body.machine_id, body.speed)
            )
        except UnknownScenario as exc:
            raise HTTPException(404, f"unknown scenario {body.name}") from exc
    async with _client(rt) as client:
        try:
            resp = await client.post(
                "/scenario", json=body.model_dump(exclude={"speed"}, exclude_none=True)
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"simulator: {exc}") from exc
    return ScenarioStarted(**resp.json())


class ScenarioStopped(BaseModel):
    ok: bool
    stopped: str | None  # the scenario that was playing/holding, if any


@router.post("/sim/stop", response_model=ScenarioStopped)
async def stop(request: Request, _: Principal = Depends(require("admin"))) -> ScenarioStopped:
    """Replay mode: stop the running scenario, including its hold frames, so nothing but the
    real source (or a probe) publishes for the machine. P1's control API has no stop call
    (sim_control.md), so proxy mode answers 409."""
    rt = request.app.state.rt
    if rt.settings.sim_mode != "replay":
        raise HTTPException(409, "SIM_MODE=proxy: the simulator control API has no stop call")
    stopped = rt.replayer.current
    await rt.replayer.stop()
    return ScenarioStopped(ok=True, stopped=stopped)
