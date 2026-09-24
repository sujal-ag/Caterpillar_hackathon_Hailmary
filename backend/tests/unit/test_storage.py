"""Phase 1 gate: schema, outbox atomicity, coalescing, telemetry not syncable, concurrency,
seed idempotency (plan.md Phase 1 verification)."""

import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlmodel import Session, SQLModel, select

from common.db import repo
from common.db.models import (
    Incident,
    Machine,
    MachineEvent,
    MachineModel,
    Operator,
    Site,
    SyncQueue,
    TelemetrySample,
)
from common.db.session import create_all, make_engine, sqlite_url
from common.db.writer import DbWriter
from common.ids import uuid7
from common.timeutil import now_utc, to_site_iso


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(sqlite_url(str(tmp_path / "edge.db")))
    create_all(eng)
    # Time-series tables FK to machine.machine_id (real integrity, not test ceremony) —
    # seed the minimal graph a machine needs so writer/repo tests can reference EXC001.
    # Committed one at a time: two FKs elsewhere in the schema (shift.walkaround_id <->
    # walkaround.shift_id) make this a cyclic dependency graph, so a single flush can't
    # reliably topo-sort insert order across unrelated tables.
    with Session(eng) as s:
        s.add(Site(site_id="SITE-PUN-01", name="Test site", timezone="Asia/Kolkata"))
        s.commit()
        s.add(
            MachineModel(
                model_id="CAT-320",
                family="EXCAVATOR",
                engine_model="C4.4",
                net_power_kw=128.5,
                operating_weight_kg=22600,
                bucket_capacity_m3=1.19,
                fuel_tank_l=345,
                def_tank_l=39,
                max_implement_press_kpa=35000,
                low_idle_rpm=1000,
                rated_rpm=2200,
                high_idle_rpm=2200,
                idle_fuel_lph=2.0,
                fuel_lph_light=8.5,
                fuel_lph_med=12.5,
                fuel_lph_heavy=17.0,
                nominal_cycle_s=18,
                tail_swing_radius_m=2.83,
                pitch_caution_deg=15,
                pitch_critical_deg=25,
                roll_caution_deg=10,
                roll_critical_deg=15,
                coolant_caution_c=105,
                coolant_critical_c=110,
                hyd_oil_caution_c=95,
                hyd_oil_critical_c=105,
                proximity_critical_m=3.8,
                proximity_warning_m=8.0,
            )
        )
        s.commit()
        s.add(Machine(machine_id="EXC001", model_id="CAT-320", site_id="SITE-PUN-01"))
        s.commit()
    return eng


@pytest.fixture
def writer(engine):
    w = DbWriter(engine, batch_window_s=0.05, batch_max=50)
    w.start()
    yield w
    w.stop()


def make_incident(**over) -> Incident:
    base = dict(
        incident_id=uuid7(),
        type="NEAR_MISS",
        category="PERSON_PROXIMITY",
        ts=to_site_iso(now_utc()),
        reporter_id="OP1001",
        severity_self=2,
        status="OPEN",
    )
    return Incident(**{**base, **over})


def test_create_all_sqlite(engine):
    tables = SQLModel.metadata.tables
    assert {"telemetry_sample", "sync_queue", "incident", "machine_state_snapshot"} <= tables.keys()
    with Session(engine) as s:
        assert s.exec(select(Incident)).all() == []


def _postgres_reachable(url: str) -> bool:
    try:
        host_port = url.split("@")[-1].split("/")[0]
        host, port = host_port.split(":")
        with socket.create_connection((host, int(port)), timeout=1):
            return True
    except OSError:
        return False


POSTGRES_URL = os.environ.get(
    "POSTGRES_TEST_URL", "postgresql+psycopg://companion:companion-dev@localhost:5432/companion"
)


@pytest.mark.skipif(
    not _postgres_reachable(POSTGRES_URL),
    reason="postgres not reachable; start it with "
    "`docker compose -f infra/docker-compose.yml --profile cloud up -d postgres`",
)
def test_create_all_postgres():
    engine = make_engine(POSTGRES_URL)
    SQLModel.metadata.drop_all(engine)
    create_all(engine)
    with Session(engine) as s:
        s.add(make_incident())
        s.commit()
        assert len(s.exec(select(Incident)).all()) == 1
    SQLModel.metadata.drop_all(engine)


def test_outbox_atomicity_failure_persists_nothing(writer, engine):
    incident = make_incident()

    def uow_fails(session: Session):
        repo.insert_incident(session, incident)
        raise RuntimeError("boom")

    fut = writer.submit(uow_fails)
    with pytest.raises(RuntimeError, match="boom"):
        fut.result(timeout=5)

    with Session(engine) as s:
        assert s.get(Incident, incident.incident_id) is None
        assert s.exec(select(SyncQueue)).all() == []


def test_outbox_atomicity_success_persists_both(writer, engine):
    incident = make_incident()
    incident_id = incident.incident_id  # read before the writer's session commits+closes it
    writer.submit(lambda s: repo.insert_incident(s, incident)).result(timeout=5)

    with Session(engine) as s:
        assert s.get(Incident, incident_id) is not None
        rows = s.exec(select(SyncQueue)).all()
        assert len(rows) == 1
        assert rows[0].entity == "incident"
        assert rows[0].entity_id == incident_id
        assert rows[0].priority == 0  # incident is always P0


def test_machine_state_snapshot_coalesces_to_one_pending_row(writer, engine):
    for i in range(3):
        payload = {"class": "PRODUCTIVE", "tick": i}
        writer.submit(
            lambda s, p=payload: repo.upsert_machine_state_snapshot(s, "EXC001", p)
        ).result(timeout=5)

    with Session(engine) as s:
        rows = s.exec(select(SyncQueue).where(SyncQueue.entity == "machine_state_snapshot")).all()
        assert len(rows) == 1
        assert rows[0].payload == {"class": "PRODUCTIVE", "tick": 2}
        assert rows[0].priority == 1


def test_telemetry_sample_never_enqueues_outbox(writer, engine):
    writer.submit_telemetry(
        {"ts": to_site_iso(now_utc()), "machine_id": "EXC001", "engine_rpm": 1000}
    )
    writer.submit(lambda s: None).result(timeout=5)  # forces a flush + confirms ordering

    with Session(engine) as s:
        assert len(s.exec(select(TelemetrySample)).all()) == 1
        assert s.exec(select(SyncQueue)).all() == []


def test_priority_for_rejects_telemetry_sample_and_unknown_entities():
    with pytest.raises(ValueError, match="not syncable"):
        repo.priority_for("telemetry_sample", {})
    with pytest.raises(ValueError, match="priority table"):
        repo.priority_for("not_a_real_entity", {})


@pytest.mark.parametrize(
    ("entity", "payload", "expected"),
    [
        ("safety_alert", {"level": "CRITICAL"}, 0),
        ("safety_alert", {"level": "WARNING", "escalated": True}, 0),
        ("safety_alert", {"level": "WARNING"}, 1),
        ("readiness_check", {"rating": "RED"}, 0),
        ("readiness_check", {"rating": "GREEN"}, 1),
        ("exit_event", {"state_at_intent": "UNSAFE"}, 0),
        ("exit_event", {"state_at_intent": "SAFE"}, 1),
        ("telemetry_window", {}, 2),
        ("telemetry_minute", {}, 3),
    ],
)
def test_priority_table(entity, payload, expected):
    assert repo.priority_for(entity, payload) == expected


def test_concurrent_writes_and_telemetry_never_lock(writer, engine):
    """5 concurrent API-style writes racing 60 s of simulated 1 Hz telemetry."""
    for i in range(60):
        writer.submit_telemetry(
            {"ts": to_site_iso(now_utc()), "machine_id": "EXC001", "engine_rpm": 1000 + i}
        )

    def write_event(i: int):
        def uow(session: Session):
            session.add(
                MachineEvent(
                    event_id=uuid7(),
                    ts=to_site_iso(now_utc()),
                    machine_id="EXC001",
                    type="IGNITION_ON",
                    severity="INFO",
                    seq=i,
                    source="operator",
                )
            )

        return writer.submit(uow).result(timeout=10)

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(write_event, range(5)))

    writer.submit(lambda s: None).result(timeout=5)  # drain any pending telemetry batch
    time.sleep(0.1)

    with Session(engine) as s:
        assert len(s.exec(select(TelemetrySample)).all()) == 60
        assert len(s.exec(select(MachineEvent)).all()) == 5


def test_seed_is_idempotent(tmp_path):
    from tools import seed

    eng = make_engine(sqlite_url(str(tmp_path / "seed.db")))
    first = seed.run(eng)
    second = seed.run(eng)
    assert first == second
    assert first["operator"] == 4  # OP1001, OP1002, GUEST0, SUP001
    assert first["task"] == 2

    with Session(eng) as s:
        op = s.get(Operator, "OP1001")
        assert op.pin_hash.startswith("$2b$")
        import bcrypt

        assert bcrypt.checkpw(b"1234", op.pin_hash.encode())


def test_load_history_stub_is_a_clean_noop_when_files_absent(capsys):
    from tools import load_history

    assert load_history.missing_files()  # data/history/ isn't populated yet
    load_history.main()
    assert "Nothing to load yet" in capsys.readouterr().err


def test_writer_survives_a_cancelled_caller(tmp_path):
    """A caller that stops waiting cancels its future; the writer thread must keep running
    (found in Phase 5: a cancelled wait killed the thread and every later write)."""
    import threading

    eng = make_engine(sqlite_url(str(tmp_path / "w.db")))
    create_all(eng)
    w = DbWriter(eng)
    w.start()
    gate = threading.Event()
    blocker = w.submit(lambda s: gate.wait(5))
    cancelled = w.submit(lambda s: "ignored")
    assert cancelled.cancel()  # still queued behind the blocker
    gate.set()
    blocker.result(5)
    assert w.submit(lambda s: "still alive").result(5) == "still alive"
    w.stop()
