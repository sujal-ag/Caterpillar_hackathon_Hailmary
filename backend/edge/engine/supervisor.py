"""Engine task + watchdog (plan.md Phase 3 item 7, D18; HLD §4.4 failure mode).

`EngineRunner` owns one `MachineEngine` on a bus: routes raw/dtc/env/proximity messages to
it, ticks it every second, persists each `Out` through the single `DbWriter` (entity +
outbox in one unit of work, I7), publishes state/alert/event, and snapshots the engine to
`engine_snapshot` every second and on every change. If processing raises, the runner logs,
rebuilds the engine from the last snapshot and carries on — in-process, well under the 2 s
D18 target. Rule-level errors never get here: `MachineEngine.evaluate` disables just that
rule. MQTT publishing proper, nudges and the FastAPI lifespan are Phase 4.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime

from sqlmodel import Session

from common.db import repo
from common.db.models import EngineSnapshot, SimLabelLog
from common.db.writer import DbWriter
from common.log import jlog
from common.timeutil import MonotonicClock
from edge.bus import Bus
from edge.engine.machine import MachineEngine, Out

log = logging.getLogger("edge.engine.supervisor")


def persist(session: Session, out: Out) -> None:
    """One unit of work for one engine step. Telemetry goes through
    `DbWriter.submit_telemetry` instead (batched, never in the outbox, I8). Env obs are
    site-level and persisted once per site by the Phase 4 ingest wiring, not per engine."""
    for label in out.sim_labels:
        session.add(SimLabelLog(**label))
    for row in out.dtc_occurrences:
        repo.upsert_dtc_occurrence(session, row)
    for row in out.idle_episodes:
        repo.insert_idle_episode(session, row)
    for event in out.events:
        repo.insert_machine_event(session, event)
    for row in out.exit_rows:
        repo.upsert_exit_event(session, row)
    for _action, alert in out.alerts:
        repo.upsert_safety_alert(session, alert)


def load_snapshot(session: Session, machine_id: str) -> dict | None:
    row = session.get(EngineSnapshot, machine_id)
    return row.payload if row else None


class EngineRunner:
    def __init__(
        self,
        make_engine: Callable[[], MachineEngine],
        bus: Bus,
        writer: DbWriter,
        *,
        clock: Callable[[], datetime] | None = None,
        snapshot: dict | None = None,
        tick_s: float = 1.0,
    ):
        self.make_engine, self.bus, self.writer = make_engine, bus, writer
        self.clock = clock or MonotonicClock()
        self.tick_s = tick_s
        self.engine = make_engine()
        self.last_snapshot = snapshot
        if snapshot:
            self.engine.restore(snapshot)
        self.restarts = 0
        self.last_restart_s: float | None = None
        self.handled = 0  # bus messages fully processed (or lost to a crash)
        self.queue: asyncio.Queue = asyncio.Queue()
        ctx = self.engine.ctx
        self._prefix = f"cat/{ctx.site_id}/{ctx.machine_id}"
        bus.subscribe(f"{self._prefix}/raw", self.queue)
        bus.subscribe(f"{self._prefix}/dtc", self.queue)
        bus.subscribe(f"{self._prefix}/+/proximity", self.queue)
        bus.subscribe(f"cat/{ctx.site_id}/env", self.queue)

    async def run(self) -> None:
        while True:
            try:
                await self._loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - watchdog: any engine fault -> restart
                t0 = time.monotonic()
                jlog(
                    log,
                    logging.ERROR,
                    "engine_crashed",
                    machine_id=self.engine.machine_id,
                    error=repr(exc),
                )
                self.engine = self.make_engine()
                if self.last_snapshot:
                    self.engine.restore(self.last_snapshot)
                self.restarts += 1
                self.handled += 1  # the message that crashed is dropped, not retried
                self.last_restart_s = time.monotonic() - t0
                jlog(
                    log,
                    logging.WARNING,
                    "engine_restarted",
                    machine_id=self.engine.machine_id,
                    restart_s=self.last_restart_s,
                    restarts=self.restarts,
                )

    async def _loop(self) -> None:
        next_tick = time.monotonic() + self.tick_s
        last_snap = time.monotonic()
        while True:
            timeout = max(0.0, next_tick - time.monotonic())
            try:
                topic, payload = await asyncio.wait_for(self.queue.get(), timeout)
            except TimeoutError:
                topic, payload = None, None
            out = self._dispatch(topic, payload)
            if topic is None:
                next_tick = time.monotonic() + self.tick_s
            if out is not None:
                await self._emit(out)
                if out.changed() or time.monotonic() - last_snap >= self.tick_s:
                    await self._snapshot()
                    last_snap = time.monotonic()
            if topic is not None:
                self.handled += 1

    def _dispatch(self, topic: str | None, payload: dict | None) -> Out | None:
        now, e = self.clock(), self.engine
        if topic is None:
            return e.tick(now)
        if topic.endswith("/raw"):
            return e.on_raw(payload, now)
        if topic.endswith("/dtc"):
            return e.on_dtc(payload, now)
        if topic.endswith("/proximity"):
            return e.on_proximity(payload, now)
        if topic.endswith("/env"):
            return e.on_env(payload, now)
        return None

    async def _emit(self, out: Out) -> None:
        if out.telemetry is not None:
            self.writer.submit_telemetry(out.telemetry)
        await asyncio.wrap_future(self.writer.submit(lambda s: persist(s, out)))
        for event in out.events:
            await self.bus.publish(
                f"{self._prefix}/event", {"schema": "event.v1", **event.model_dump()}
            )
        for action, alert in out.alerts:
            await self.bus.publish(
                f"{self._prefix}/alert",
                {"schema": "alert.v1", "ts": alert["ts"], "action": action, "alert": dict(alert)},
            )
        if out.state is not None:
            await self.bus.publish(f"{self._prefix}/state", out.state)

    async def _snapshot(self) -> None:
        snap = self.engine.snapshot()
        mid = self.engine.machine_id
        await asyncio.wrap_future(
            self.writer.submit(lambda s: repo.upsert_engine_snapshot(s, mid, snap))
        )
        self.last_snapshot = snap
