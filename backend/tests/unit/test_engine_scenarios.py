"""Phase 3 gate: scenario fixtures through the engine + DbWriter, the MemoryBus runner, and
the crash/rehydrate test (plan.md Phase 3 verification)."""

import asyncio
import json
import time
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from sqlmodel import Session, select

from common.db.models import ExitEvent, MachineEvent, SafetyAlert, SimLabelLog, SyncQueue
from common.db.session import make_engine as make_db_engine
from common.db.session import sqlite_url
from common.db.writer import DbWriter
from common.timeutil import parse_iso
from edge.bus import MemoryBus
from edge.engine.supervisor import EngineRunner, load_snapshot, persist
from tests.engine_support import ENGINE_FIXTURES, FIXTURES, SCENARIOS, Run, load, make_engine
from tools import eval_rules, seed
from tools.fake_machine import generate

SPECS = FIXTURES / "specs"
SCHEMAS = Path(__file__).resolve().parents[3] / "contracts" / "schemas"
# (spec, generated JSONL) for every fixture: replay scenarios + engine-only ones.
FIXTURE_PAIRS = [(SPECS / f"{p.stem}.yaml", p) for p in sorted(SCENARIOS.glob("*.jsonl"))] + [
    (ENGINE_FIXTURES / "specs" / f"{p.stem}.yaml", p)
    for p in sorted(ENGINE_FIXTURES.glob("*.jsonl"))
]


def validator(name):
    return Draft202012Validator(json.loads((SCHEMAS / f"{name}.json").read_text()))


@pytest.fixture
def db(tmp_path):
    eng = make_db_engine(sqlite_url(str(tmp_path / "edge.db")))
    seed.run(eng)  # machines + diagnostic catalogue: real FKs for alerts/events/exits
    return eng


FIXTURE_DAY = "2026-10-14"  # fixtures run on this date; seeded demo history is days earlier


@pytest.fixture
def writer(db):
    w = DbWriter(db, batch_window_s=0.05)
    w.start()
    yield w
    w.stop()


def run_persisted(name, writer) -> Run:
    return Run(
        make_engine(),
        load(name),
        on_out=lambda out: writer.submit(lambda s: persist(s, out)).result(),
    )


@pytest.mark.parametrize("spec,jsonl", FIXTURE_PAIRS, ids=lambda p: p.stem)
def test_fixture_regenerates_from_spec(spec, jsonl):
    lines = [json.loads(s) for s in jsonl.read_text().splitlines()]
    assert generate(yaml.safe_load(spec.read_text())) == lines


def test_replay_scenarios_are_the_contract_names():
    # User decision (Phase 4): the demo-script subset of contracts/sim_control.md, plus the
    # Phase 8 demo machine-specific fault (demo_e001).
    assert sorted(p.stem for p in SCENARIOS.glob("*.jsonl")) == [
        "belt_bypass",
        "demo_e001",
        "drive_into_zone",
        "dtc_1638_16",
        "proximity_intrusion",
        "reset",
        "safe_exit",
        "tilt_excursion",
        "unsafe_exit",
    ]


def test_unsafe_exit(writer, db):
    run = run_persisted("unsafe_exit", writer)
    # R03 raised on the frame that makes intent true (door opens at t=63, belt already off).
    ((t_r03, _, r03),) = run.alerts("R03", "RAISED")
    assert t_r03 == 63
    assert r03["state_snapshot"]["thresholds"]["grounded_max_m"] == 0.3
    # Checklist ticks FAIL -> PASS in the order the operator corrects them.
    order, prev = [], {}
    for _, st in run.states():
        for check, v in st["exit_checks"].items():
            if prev.get(check) == "FAIL" and v == "PASS":
                order.append(check)
        prev = st["exit_checks"] or prev
    assert order == ["IMPLEMENT_RAISED", "HYD_UNLOCKED", "ENGINE_RUNNING"]
    # exit_event: UNSAFE at intent, corrected when it leaves UNSAFE (72 s, D1) -> 9 s.
    with Session(db) as s:
        (ex,) = s.exec(select(ExitEvent).where(ExitEvent.ts >= FIXTURE_DAY)).all()
        assert ex.state_at_intent == "UNSAFE" and ex.corrected
        assert ex.time_to_correct_s == pytest.approx(9, abs=1)
        assert ex.three_point_prompted and ex.wet_conditions
        assert ex.time_outside_s == pytest.approx(38, abs=1)
        exit_rows = s.exec(select(SyncQueue).where(SyncQueue.entity == "exit_event")).all()
        assert exit_rows and all(r.priority == 0 for r in exit_rows)  # UNSAFE exit -> P0
        types = [e.type for e in s.exec(select(MachineEvent).order_by(MachineEvent.seq))]
        assert types.count("EXIT_ATTEMPT") == 1
        assert types.index("EXIT_ATTEMPT") < types.index("EXIT_COMPLETED") < types.index("MOUNT")
        assert s.exec(select(SimLabelLog)).first().sim_label == "UNSAFE_EXIT"  # I9: stored
    # R06 exactly once (wet: RH 95 %, dew-point spread < 2 C).
    assert len(run.alerts("R06", "RAISED")) == 1
    # Class UNSAFE while R03 is active, and only lower >= 10 s after it cleared (t=72).
    assert run.class_at(70) == "UNSAFE"
    assert run.class_at(81) == "UNSAFE"
    assert run.class_at(82) == "OFF"  # engine stopped at 77, R06 has no state effect


def test_safe_exit_engine_running(writer):
    run = run_persisted("safe_exit", writer)
    assert run.alerts("R03") == []
    ((t, _, r04),) = run.alerts("R04", "RAISED")
    assert t == 33 and r04["level"] == "CAUTION"
    assert run.alerts("R06") == []  # dry


def test_belt_off_parked_silent_then_escalates(writer):
    run = run_persisted("belt_off_parked", writer)
    assert run.alerts("R01") == []
    ((t, _, raised),) = run.alerts("R02", "RAISED")
    assert t == 10 and raised["level"] == "INFO" and raised["channels"] == ["VISUAL"]
    ((t_esc, _, esc),) = run.alerts("R02", "UPDATED")
    assert t_esc == 310 and esc["level"] == "CAUTION" and "AUDIO" in esc["channels"]


def test_belt_off_moving_critical_repeats_every_20s(writer):
    run = run_persisted("belt_off_moving", writer)
    r01 = [(t, a) for t, a, al in run.alerts("R01")]
    assert r01 == [(10, "RAISED"), (30, "UPDATED"), (50, "UPDATED")]
    assert all("AUDIO" in al["channels"] for _, _, al in run.alerts("R01"))


def test_belt_bypass_escalated_p0(writer, db):
    run = run_persisted("belt_bypass", writer)
    ((t, _, r07),) = run.alerts("R07", "RAISED")
    assert t == 16 and r07["level"] == "WARNING" and r07["escalated"]
    with Session(db) as s:
        rows = s.exec(select(SyncQueue).where(SyncQueue.entity_id == r07["alert_id"])).all()
        assert rows and rows[0].priority == 0
        assert s.get(SafetyAlert, r07["alert_id"]).escalated


def test_tilt_excursion_caution_then_critical_suppresses(writer):
    run = run_persisted("tilt_excursion", writer)  # roll 11 deg at 10 s, 17 deg at 20 s
    assert [t for t, _, _ in run.alerts("R10", "RAISED")] == [10]
    assert [t for t, _, _ in run.alerts("R11", "RAISED")] == [20]
    ((t, _, sup),) = [x for x in run.alerts("R10", "UPDATED") if x[2]["suppressed"]]
    assert t == 20 and sup["suppress_reason"] == "HIGHER_ACTIVE"
    assert {al["rule_id"] for _, _, al in run.alerts(action="CLEARED")} >= {"R10", "R11"}


def test_proximity_sim(writer):
    run = run_persisted("proximity_sim", writer)
    assert [t for t, _, _ in run.alerts("R13", "RAISED")] == [5]
    assert [t for t, _, _ in run.alerts("R12", "RAISED")] == [7]  # 3.0 m < 3.8 m, working
    assert [t for t, _, _ in run.alerts("R12", "CLEARED")] == [11]
    # 25-28 s: person at 3 m but machine idle -> nothing.
    assert all(t < 20 for t, _, _ in run.alerts(action="RAISED"))
    # 35-37 s: CV frame_ok:false and no fresh sim frame -> UNKNOWN, never "no person".
    assert run.engine.state["sensor_health"]["proximity"] == "UNKNOWN"
    assert run.engine.ctx.proximity_now(
        parse_iso(load("proximity_sim")[-1]["payload"]["ts"]), 1
    ) == (None, "UNKNOWN")


def test_dtc_stop_catalogue_action_class(writer):
    run = run_persisted("dtc_stop", writer)
    ((t, _, r16),) = run.alerts("R16", "RAISED")
    assert t == 10 and r16["level"] == "CRITICAL" and r16["slots"]["action_class"] == "STOP"
    assert [t for t, _, _ in run.alerts("R16", "CLEARED")] == [40]


def test_eval_rules_on_p1_traces():
    if not eval_rules.TRACES_DIR.is_dir() or not any(eval_rules.TRACES_DIR.glob("*.jsonl")):
        pytest.skip(
            "P1 labelled traces not delivered yet (data/history/traces/, "
            "contracts/history.md); run tools/eval_rules.py once they are"
        )
    assert eval_rules.main() == 0  # 100 % recall on UNSAFE_EXIT and BELT_BYPASS (HLD §7.3)


# --- runner on MemoryBus: contracts on the bus, crash + rehydrate ---------------------------


class Clock:
    def __init__(self):
        self.now = None

    def __call__(self):
        return self.now


async def _drive(runner, bus, clock, lines, until=None):
    for line in lines:
        clock.now = parse_iso(line["payload"]["ts"])
        before = runner.handled
        await bus.publish(line["topic"], line["payload"])
        while runner.handled == before:
            await asyncio.sleep(0.001)
        if until and until(line):
            return


def test_runner_publishes_contract_payloads(writer):
    lines = load("unsafe_exit")

    async def main():
        bus, clock, seen = MemoryBus(), Clock(), asyncio.Queue()
        bus.subscribe("cat/SITE-PUN-01/EXC001/+", seen)
        runner = EngineRunner(make_engine, bus, writer, clock=clock, tick_s=3600)
        task = asyncio.create_task(runner.run())
        await _drive(runner, bus, clock, lines)
        task.cancel()
        out = []
        while not seen.empty():
            out.append(seen.get_nowait())
        return out

    published = asyncio.run(main())
    schemas = {
        "state": validator("state.v1"),
        "alert": validator("alert.v1"),
        "event": validator("event.v1"),
    }
    kinds = set()
    for topic, payload in published:
        kind = topic.rsplit("/", 1)[1]
        if kind in schemas:
            kinds.add(kind)
            errors = [e.message for e in schemas[kind].iter_errors(payload)]
            assert not errors, (topic, errors)
    assert kinds == {"state", "alert", "event"}
    assert any(
        p.get("action") == "RAISED" and p["alert"]["rule_id"] == "R03"
        for t, p in published
        if t.endswith("/alert")
    )


def test_crash_mid_unsafe_exit_restarts_and_rehydrates(writer, db):
    lines = load("unsafe_exit")
    crash_ts = "2026-10-14T07:01:06.000+05:30"  # 3 s into the unsafe exit, R03 active
    crashed = {"done": False}

    def factory():
        engine = make_engine()
        real = engine.on_raw

        def on_raw(frame, now):
            if frame["ts"] == crash_ts and not crashed["done"]:
                crashed["done"] = True
                raise RuntimeError("injected engine fault")
            return real(frame, now)

        engine.on_raw = on_raw
        return engine

    async def main():
        bus, clock = MemoryBus(), Clock()
        runner = EngineRunner(factory, bus, writer, clock=clock, tick_s=3600)
        task = asyncio.create_task(runner.run())
        before = {}

        def at_crash(line):
            return line["payload"]["ts"] == crash_ts

        await _drive(runner, bus, clock, lines, until=at_crash)
        t0 = time.monotonic()
        while runner.restarts == 0:
            await asyncio.sleep(0.001)
        before["restart_s"] = runner.last_restart_s
        before["wall_s"] = time.monotonic() - t0
        before["exit_open"] = runner.engine.exits.open
        before["active"] = {e["rule_id"] for e in runner.engine.alerts.active.values()}
        rest = lines[[ln["payload"]["ts"] for ln in lines].index(crash_ts) + 1 :]
        await _drive(runner, bus, clock, rest)
        task.cancel()
        return runner, before

    runner, before = asyncio.run(main())
    assert runner.restarts == 1
    assert before["restart_s"] < 2 and before["wall_s"] < 2  # D18
    assert before["exit_open"] is not None  # open exit restored
    assert "R03" in before["active"]  # active alerts restored
    with Session(db) as s:
        types = [e.type for e in s.exec(select(MachineEvent))]
        assert types.count("EXIT_ATTEMPT") == 1  # no duplicate after rehydrate
        (ex,) = s.exec(select(ExitEvent).where(ExitEvent.ts >= FIXTURE_DAY)).all()
        assert ex.corrected and ex.time_to_correct_s == pytest.approx(9, abs=1)
        r03 = s.exec(
            select(SafetyAlert).where(SafetyAlert.rule_id == "R03", SafetyAlert.ts >= FIXTURE_DAY)
        ).all()
        assert len(r03) == 1 and not r03[0].active  # same alert, cleared once corrected
        assert load_snapshot(s, "EXC001") is not None


def test_replay_10min_fixture_for_p3():
    # The fixture P3 builds against: one unsafe exit. Under D1 it is corrected 9 s after
    # intent (t=303 -> 312, hydraulics locked with the bucket down), not the 14 s its spec
    # comment assumes (that counts from seat vacated to engine off) — see PROGRESS.md.
    lines = [
        json.loads(s) for s in (SCENARIOS.parent / "replay_10min.jsonl").read_text().splitlines()
    ]
    run = Run(make_engine(), lines)
    assert [t for t, _, _ in run.alerts("R03", "RAISED")] == [303]
    rows = [row for _, out, _ in run.steps for row in out.exit_rows]
    assert rows[-1]["time_to_correct_s"] == 9 and rows[-1]["three_point_prompted"]
    assert len(run.events("EXIT_ATTEMPT")) == 1 and len(run.events("MOUNT")) == 1
