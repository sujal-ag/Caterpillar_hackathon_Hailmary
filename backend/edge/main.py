"""edge-api (plan.md §2, Phase 4). `create_app()` builds the FastAPI app; the lifespan starts
the `Runtime` (DB, writer, bus, one engine per machine, background tasks) from `Settings`.
Importing this module has no side effects, so `tools/export_openapi.py` can import `app`.
Bind address/port are uvicorn's (`infra/Dockerfile.edge`: 0.0.0.0:8000 so the tablet can
reach it over the LAN — docs/RUNBOOK.md)."""

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from common.config import Settings
from common.log import install_redaction
from edge.api import auth, sim, state, system
from edge.bus import Bus
from edge.runtime import Runtime
from edge.ws import live


def create_app(
    settings: Settings | None = None,
    bus: Bus | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=settings.log_level)
        install_redaction("uvicorn.access", "uvicorn.error")  # WS URLs carry the JWT
        rt = Runtime(settings, bus=bus, clock=clock)
        await rt.start()
        app.state.rt = rt
        try:
            yield
        finally:
            await rt.stop()

    app = FastAPI(title="Operator Companion edge-api", version="0.4.0", lifespan=lifespan)
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    for module in (system, auth, state, sim, live):
        app.include_router(module.router)
    return app


app = create_app()
