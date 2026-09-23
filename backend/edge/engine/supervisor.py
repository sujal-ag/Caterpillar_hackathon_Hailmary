"""Engine task + watchdog (plan.md Phase 3 item 7, D18; HLD §4.4 failure mode).

`EngineRunner` owns one `MachineEngine` on a bus: routes raw/dtc/env/proximity messages to
it, ticks it every `tick_s`, persists each `Out` through the single `DbWriter` (entity +
outbox in one unit of work, I7), publishes telemetry/state/alert/nudge/event/health through
the `Broadcaster` (WS + MQTT), and snapshots the engine to
`engine_snapshot` every second and on every change. If processing raises, the runner logs,
rebuilds the engine from the last snapshot and carries on — in-process, well under the 2 s
D18 target. Rule-level errors never get here: `MachineEngine.evaluate` disables just that
rule. The engine is only ever touched from this runner's task: API calls that change it
(ack) go through `call()`, which queues the function onto the same loop.
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
from edge.broadcast import Broadcaster
from edge.bus import Bus
from edge.engine.machine import MachineEngine, Out
from edge.ml.adapter import status as ml_status

log = logging.getLogger("edge.engine.supervisor")
_CALL = object()  # queue marker for EngineRunner.call


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
        sink: Broadcaster | None = None,
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
        self.sink = sink or Broadcaster(bus, ctx.site_id)
        self._last_health: dict | None = None
        self._tick_pending = False
        self._prefix = f"cat/{ctx.site_id}/{ctx.machine_id}"
        bus.subscribe(f"{self._prefix}/raw", self.queue)
        bus.subscribe(f"{self._prefix}/dtc", self.queue)
        bus.subscribe(f"{self._prefix}/+/proximity", self.queue)
        bus.subscribe(f"cat/{ctx.site_id}/env", self.queue)

    async def run(self) -> None:
        ticker = asyncio.create_task(self._ticker())
        try:
            await self._supervise()
        finally:
            ticker.cancel()

    async def _supervise(self) -> None:
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

    async def call(self, fn: Callable[[MachineEngine, datetime], Out | None]) -> Out | None:
        """Run `fn(engine, now)` on the engine's own task; its Out is persisted and
        broadcast like any other step. Returns that Out (None = nothing changed)."""
        fut = asyncio.get_running_loop().create_future()
        await self.queue.put((_CALL, (fn, fut)))
        return await fut

    async def _ticker(self) -> None:
        """Queue a timer tick every `tick_s` (at most one waiting). Ticks go through the same
        queue as messages so the loop never needs a timeout on `get()` — on Python 3.11 both
        `wait_for` and `asyncio.timeout` can lose or leak a cancellation that races a
        completing get(), which left the runner unstoppable at shutdown."""
        while True:
            await asyncio.sleep(self.tick_s)
            if not self._tick_pending:
                self._tick_pending = True
                self.queue.put_nowait((None, None))

    async def _loop(self) -> None:
        last_snap = time.monotonic()
        if self.engine.state is None:  # evaluate once so a snapshot never lacks a state
            await self._emit(self.engine.tick(self.clock()))
        while True:
            topic, payload = await self.queue.get()
            if topic is _CALL:
                await self._run_call(*payload)
                continue
            if topic is None:
                self._tick_pending = False
            out = self._dispatch(topic, payload)
            if out is not None:
                await self._emit(out)
                if out.changed() or time.monotonic() - last_snap >= self.tick_s:
                    await self._snapshot()
                    last_snap = time.monotonic()
            if topic is not None:
                self.handled += 1

    async def _run_call(self, fn, fut) -> None:
        try:
            out = fn(self.engine, self.clock())
            if out is not None:
                await self._emit(out)
                await self._snapshot()
        except Exception as exc:  # noqa: BLE001 - the caller gets the error; engine keeps running
            if not fut.done():
                fut.set_exception(exc)
            return
        if not fut.done():
            fut.set_result(out)

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
        mid, pub = self.engine.machine_id, self.sink.publish
        if out.telemetry_v1 is not None:
            await pub(mid, "telemetry", out.telemetry_v1)
        for event in out.events:
            await pub(mid, "event", {"schema": "event.v1", **event.model_dump()})
        for action, alert in out.alerts:
            await pub(
                mid,
                "alert",
                {"schema": "alert.v1", "ts": alert["ts"], "action": action, "alert": dict(alert)},
            )
        for nudge in out.nudges:
            await pub(mid, "nudge", nudge)
        if out.state is not None:
            await pub(mid, "state", out.state)
            health = out.state["sensor_health"]
            if health != self._last_health:
                self._last_health = health
                await pub(
                    mid,
                    "health",
                    {"sensor_health": health, "models": ml_status(), "as_of": out.state["ts"]},
                )

    async def _snapshot(self) -> None:
        snap = self.engine.snapshot()
        mid = self.engine.machine_id
        await asyncio.wrap_future(
            self.writer.submit(lambda s: repo.upsert_engine_snapshot(s, mid, snap))
        )
        self.last_snapshot = snap
