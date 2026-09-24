"""Edge runtime: everything the FastAPI lifespan starts and stops (plan.md Phase 4; §2).

Nothing here is hard-coded: the site and machines come from the DB (`EDGE_SITE_ID`,
`EDGE_MACHINE_IDS` narrow it), machine thresholds from `machine_model` rows, the DTC
catalogue from `diagnostic_code` rows, rules from `CONTRACTS_DIR/rules.yaml`, and every
host/interval from `Settings`. One `EngineRunner` per machine; one `DbWriter`; one bus.
"""

import asyncio
import logging
import os
from collections.abc import Callable
from datetime import datetime

from sqlmodel import Session, func, select

from common.config import Settings
from common.db.models import DiagnosticCode, Machine, MachineModel, Site, SyncQueue
from common.db.session import create_all, make_engine, sqlite_url
from common.db.writer import DbWriter
from common.log import jlog
from common.timeutil import MonotonicClock, parse_iso, to_site_iso
from edge import hazards
from edge.broadcast import OVERFLOW, Broadcaster
from edge.bus import Bus, MqttBus
from edge.engine.machine import MachineEngine
from edge.engine.rules import RuleSet, load_rules
from edge.engine.supervisor import EngineRunner, load_snapshot
from edge.ingest.dtc import build_catalogue_index
from edge.ingest.env import build_environment_obs
from edge.lessons import engine as lessons
from edge.ml import anomaly_job, eta_service
from edge.ml.adapter import MlAdapter
from edge.ml.eta_service import load_soils
from edge.sim import Replayer

log = logging.getLogger("edge.runtime")


class Runtime:
    def __init__(
        self,
        settings: Settings,
        bus: Bus | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.settings = settings
        self._bus_override = bus
        self.clock = clock or MonotonicClock()
        self.runners: dict[str, EngineRunner] = {}
        self.tasks: list[asyncio.Task] = []
        self.env_queue: asyncio.Queue = asyncio.Queue()
        self.hazard_queue: asyncio.Queue = asyncio.Queue()
        self.window_queue: asyncio.Queue = asyncio.Queue()  # closed hourly windows -> anomaly
        self._deliverable: dict[str, bool] = {}  # assignment_id -> last pushed deliverability
        self._rain: bool | None = None

    # --- lifecycle ------------------------------------------------------------------------

    async def start(self) -> None:
        s = self.settings
        s.check()
        if s.auth_disabled:
            jlog(
                log,
                logging.WARNING,
                "AUTH_DISABLED",
                detail="auth is OFF — for P3 development only, never on a real machine",
            )
        self.db = make_engine(sqlite_url(s.edge_db_path))
        if s.edge_seed_on_start:
            from tools import seed  # idempotent; also runs create_all

            await asyncio.to_thread(seed.run, self.db, self.clock(), s)
        else:
            await asyncio.to_thread(create_all, self.db)
        self.writer = DbWriter(self.db)
        self.writer.start()

        self.ruleset: RuleSet = load_rules(s.contracts_dir / "rules.yaml")
        self.ml = await asyncio.to_thread(MlAdapter(s.models_dir, self.db).load)
        self.soils = load_soils(s.data_dir)
        self.lesson_templates = lessons.load_templates(
            s.lessons_path, s.contracts_dir / "schemas" / "lesson.v1.json"
        )
        site_id, machines, models, codes = await asyncio.to_thread(self._load_master_data)
        self.site_id = site_id
        self.bus = self._bus_override or MqttBus(
            s.mqtt_host,
            s.mqtt_port,
            s.mqtt_client_id or f"edge-api-{site_id}-{os.getpid()}",
            s.mqtt_reconnect_s,
        )
        self.sink = Broadcaster(self.bus, site_id, s.ws_queue_max)
        catalogue = build_catalogue_index(codes)

        for m in machines:
            self.runners[m["machine_id"]] = EngineRunner(
                self._engine_factory(m, models[m["model_id"]], catalogue),
                self.bus,
                self.writer,
                clock=self.clock,
                snapshot=await asyncio.to_thread(self._snapshot_of, m["machine_id"]),
                tick_s=s.engine_tick_s,
                sink=self.sink,
                on_window=lambda mid, w: self.window_queue.put_nowait((mid, w)),
            )
        self.bus.subscribe(f"cat/{site_id}/env", self.env_queue)
        self.bus.subscribe(hazards.topic(site_id), self.hazard_queue)
        self.replayer = Replayer(self.bus, site_id, s)
        # In-process listener (not MQTT): LEARN/PREDICT triggers keep working with no broker.
        self.listener = self.sink.subscribe(None, {"alert", "event", "state"})

        if hasattr(self.bus, "run"):
            self.tasks.append(asyncio.create_task(self.bus.run(), name="bus"))
        for mid, runner in self.runners.items():
            self.tasks.append(asyncio.create_task(runner.run(), name=f"engine-{mid}"))
        self.tasks.append(asyncio.create_task(self._record_env(), name="env-recorder"))
        self.tasks.append(asyncio.create_task(self._sync_status_loop(), name="sync-status"))
        self.tasks.append(asyncio.create_task(self._consume_hazards(), name="hazards-in"))
        self.tasks.append(asyncio.create_task(self._expire_hazards(), name="hazards-expiry"))
        self.tasks.append(asyncio.create_task(self._learn_predict(), name="learn-predict"))
        self.tasks.append(asyncio.create_task(self._anomaly_loop(), name="anomaly"))
        await hazards.publish(self)  # the retained topic always reflects this edge's DB
        jlog(
            log,
            logging.INFO,
            "edge_started",
            site_id=site_id,
            machines=list(self.runners),
            sim_mode=s.sim_mode,
        )

    async def stop(self) -> None:
        await self.replayer.stop()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        self.writer.stop()

    # --- master data ----------------------------------------------------------------------

    def _load_master_data(self):
        s = self.settings
        with Session(self.db) as session:
            if s.edge_site_id:
                site_ids = [s.edge_site_id]
            else:
                site_ids = [row.site_id for row in session.exec(select(Site))]
            if len(site_ids) != 1:
                raise RuntimeError(
                    f"cannot pick a site: {site_ids or 'none in the DB'} — set EDGE_SITE_ID "
                    "(and seed the DB: tools/seed.py or EDGE_SEED_ON_START=true)"
                )
            site_id = site_ids[0]
            rows = session.exec(
                select(Machine).where(Machine.site_id == site_id, Machine.status == "ACTIVE")
            ).all()
            by_id = {r.machine_id: r.model_dump() for r in rows}
            wanted = s.machine_ids or sorted(by_id)
            unknown = [m for m in wanted if m not in by_id]
            if unknown:
                raise RuntimeError(f"EDGE_MACHINE_IDS not ACTIVE at {site_id}: {unknown}")
            if not wanted:
                raise RuntimeError(f"no ACTIVE machines at {site_id}")
            models = {r.model_id: r.model_dump() for r in session.exec(select(MachineModel))}
            codes = [r.model_dump() for r in session.exec(select(DiagnosticCode))]
        return site_id, [by_id[m] for m in wanted], models, codes

    def _engine_factory(self, machine: dict, model: dict, catalogue: dict):
        def make() -> MachineEngine:
            return MachineEngine(
                machine["machine_id"],
                machine["site_id"],
                model,
                self.ruleset,
                catalogue,
                has_rear_camera=machine["has_rear_camera"],
            )

        return make

    def _snapshot_of(self, machine_id: str) -> dict | None:
        with Session(self.db) as session:
            return load_snapshot(session, machine_id)

    # --- site-level background tasks ------------------------------------------------------

    async def _record_env(self) -> None:
        """environment_obs is site-level: stored once here, not once per machine engine."""
        from common.db.models import EnvironmentObs

        while True:
            _, payload = await self.env_queue.get()
            try:
                obs = build_environment_obs(payload)
                parse_iso(obs["ts"])
            except (KeyError, TypeError, ValueError) as exc:
                jlog(log, logging.WARNING, "env_dropped", error=repr(exc))
                continue
            self.writer.submit(lambda session, o=obs: session.add(EnvironmentObs(**o)))
            rain = obs.get("rain_flag")
            if rain is not None and rain != self._rain:  # rain start/stop re-estimates ETAs
                changed_from, self._rain = self._rain, rain
                if changed_from is not None:
                    for mid in self.runners:
                        await self.safe(eta_service.recompute(self, mid, "rain"))

    async def _consume_hazards(self) -> None:
        """Retained `cat/{site}/hazards` -> every engine's zone set + WS `hazards`. The only
        way an engine learns pins, so pins from another edge on the broker work the same."""
        while True:
            _, payload = await self.hazard_queue.get()
            pins = payload.get("pins")
            if not isinstance(pins, list):
                jlog(log, logging.WARNING, "hazards_dropped", reason="no pins list")
                continue
            for mid, runner in self.runners.items():
                try:
                    await runner.call(lambda e, now, p=pins: e.set_hazards(p, now))
                except Exception as exc:  # noqa: BLE001 - one bad list must not stop the consumer
                    jlog(
                        log, logging.ERROR, "hazards_apply_failed", machine_id=mid, error=repr(exc)
                    )
                await self.sink.publish(mid, "hazards", payload)

    async def _expire_hazards(self) -> None:
        while True:
            await asyncio.sleep(self.settings.hazard_expiry_check_s)
            try:
                expired = await hazards.expire(self)
            except Exception as exc:  # noqa: BLE001 - keep the job alive; log and retry
                jlog(log, logging.ERROR, "hazards_expiry_failed", error=repr(exc))
                continue
            if expired:
                jlog(log, logging.INFO, "hazards_expired", pin_ids=expired)

    # --- LEARN / PREDICT (Phase 6) ---------------------------------------------------------

    async def safe(self, coro) -> None:
        try:
            await coro
        except Exception as exc:  # noqa: BLE001 - a learn/predict failure never touches safety
            jlog(log, logging.ERROR, "learn_predict_failed", error=repr(exc))

    async def _learn_predict(self) -> None:
        """Alert RAISED -> lesson (+ Replay); stop/start events -> ETA; state -> lesson
        deliverability. On overflow: resync everything instead of silently skipping."""
        stop_events = eta_service.STOP_EVENTS
        while True:
            env = await self.listener.queue.get()
            if env is OVERFLOW:
                self.listener.overflowed = False
                for mid in self.runners:
                    await self.safe(eta_service.recompute(self, mid, "resync"))
                    await self.safe(self.push_lessons(mid))
                continue
            data = env["data"]
            if env["type"] == "alert" and data["action"] == "RAISED":
                await self.safe(self._assign_lesson(data["alert"]))
            elif env["type"] == "event" and data["type"] in stop_events:
                await self.safe(eta_service.recompute(self, data["machine_id"], data["type"]))
            elif env["type"] == "state":
                await self.safe(self.push_lessons(data["machine_id"]))

    async def _assign_lesson(self, alert: dict) -> None:
        mid = alert["machine_id"]
        rule = self.ruleset.by_id(alert["rule_id"])
        family, now = self.runners[mid].engine.ctx.family, self.clock()
        tpl = self.lesson_templates
        row = await self.write(lambda s: lessons.on_alert_raised(s, alert, rule, family, tpl, now))
        if row is not None:
            self._deliverable.pop(row["assignment_id"], None)  # always announce a new/changed one
            await self.push_lessons(mid)

    def _open_assignments(self, operator_id: str) -> list[str]:
        from common.db.models import LessonAssignment

        with Session(self.db) as session:
            return list(
                session.exec(
                    select(LessonAssignment.assignment_id).where(
                        LessonAssignment.operator_id == operator_id,
                        LessonAssignment.completed_at == None,  # noqa: E711 - SQL expression
                    )
                ).all()
            )

    async def push_lessons(self, machine_id: str) -> None:
        """WS `lesson {assignment_id, deliverable}` when deliverability changes (I5)."""
        operator = self.runners[machine_id].engine.ctx.operator_id
        if operator is None:
            return
        ids = await asyncio.to_thread(self._open_assignments, operator)
        ok = lessons.deliverable(self, machine_id)
        for aid in ids:
            if self._deliverable.get(aid) != ok:
                self._deliverable[aid] = ok
                payload = {"ts": to_site_iso(self.clock()), "assignment_id": aid, "deliverable": ok}
                await self.sink.publish(machine_id, "lesson", payload)

    async def _anomaly_loop(self) -> None:
        while True:
            mid, window = await self.window_queue.get()
            await self.safe(self.score_window(mid, window["window_id"]))

    async def score_window(self, machine_id: str, window_id: str) -> dict | None:
        """Score one stored hourly window; R23 via the engine. None = no model / no score."""
        from common.db.models import Task, TelemetryWindow

        runner = self.runners[machine_id]
        family = runner.engine.ctx.family
        if family not in self.ml.anomaly:
            return None  # UNAVAILABLE: no score, R23 stays UNKNOWN (plan.md §5.6)
        power_mode = runner.engine.ctx.sig("power_mode")

        def build():
            with Session(self.db) as session:
                w = session.get(TelemetryWindow, window_id)
                task = session.exec(
                    select(Task).where(Task.machine_id == machine_id, Task.status == "IN_PROGRESS")
                ).first()
                return anomaly_job.features(
                    session, w, self.site_id, task and task.task_type_id, power_mode
                )

        feats, base = await asyncio.to_thread(build)
        result = self.ml.score_anomaly(family, feats, base)
        if result is None:
            return None
        stored = await self.write(lambda s: anomaly_job.store(s, window_id, result))
        anomaly = {
            "window_id": window_id,
            "score": result["score"],
            "threshold_top3pct": result["threshold_top3pct"],
            "top_features": result["top_features"],
        }
        await runner.call(lambda e, now: e.set_anomaly(anomaly, now))
        return stored

    async def write(self, fn):
        """Run a unit of work on the single DbWriter and await its result (or exception)."""
        return await asyncio.wrap_future(self.writer.submit(fn))

    def sync_counts(self) -> dict[int, int]:
        with Session(self.db) as session:
            rows = session.exec(
                select(SyncQueue.priority, func.count())
                .where(SyncQueue.status == "PENDING")
                .group_by(SyncQueue.priority)
            ).all()
        return {int(p): int(n) for p, n in rows}

    async def _sync_status_loop(self) -> None:
        from edge.live import sync_status

        while True:
            status = await sync_status(self)
            for mid in self.runners:
                await self.sink.publish(mid, "sync", status)
            await asyncio.sleep(self.settings.sync_status_interval_s)
