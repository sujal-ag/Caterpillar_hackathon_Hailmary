"""Shared helpers for the Phase 3 engine tests: build a MachineEngine from the real
contracts/seed files and drive it over fixture frames with a clock taken from frame `ts`."""

import copy
import json
from pathlib import Path

from common.spn import FIELD_SPNS
from common.timeutil import parse_iso
from edge.engine.machine import MachineEngine, Out
from edge.engine.rules import load_catalogue, load_machine_models, load_rules
from edge.ingest.dtc import build_catalogue_index
from tools.fake_machine import generate

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCENARIOS = FIXTURES / "scenarios"  # contract-named replay scenarios (SIM_MODE=replay)
ENGINE_FIXTURES = FIXTURES / "engine"  # Phase 3 engine-only fixtures
RULESET = load_rules()
MODELS = load_machine_models()
CATALOGUE = build_catalogue_index(load_catalogue())

# Minimal initial signals for inline fake_machine specs (CAT 320, seated + belted, level).
INITIAL = dict(
    fuel_used_total_l=100.0,
    engine_hours=1524.0,
    coolant_temp_c=86,
    hyd_oil_temp_c=62,
    seatbelt="FASTENED",
    parking_brake="ON",
    bucket_height_m=2.1,
    boom_tip_height_m=6.2,
    pass_count=0,
    load_count=0,
    pitch_deg=3.2,
    roll_deg=1.1,
    x_m=184.2,
    y_m=92.7,
    heading_deg=211,
    seat_occupied=True,
    door_open=False,
    hyd_lockout="UNLOCKED",
    lift_mode=False,
    coupler_lock="LOCKED",
    cab_ac_on=True,
)


def make_engine(ruleset=RULESET, has_rear_camera=False) -> MachineEngine:
    return MachineEngine(
        "EXC001",
        "SITE-PUN-01",
        MODELS["CAT-320"],
        ruleset,
        CATALOGUE,
        has_rear_camera=has_rear_camera,
    )


def inline(timeline: list[dict], duration_s: int = 30, drop: tuple = (), **initial) -> list[dict]:
    """Frames from a small inline fake_machine spec. `drop` removes signals (UNKNOWN)."""
    init = {k: v for k, v in {**INITIAL, **initial}.items() if k not in drop}
    lines = generate(
        {
            "site_id": "SITE-PUN-01",
            "machine_id": "EXC001",
            "start": "2026-10-14T07:00:00.000+05:30",
            "duration_s": duration_s,
            "initial": init,
            "timeline": timeline,
        }
    )
    for line in lines:  # fake_machine re-adds mode preset keys; honour `drop` on every frame
        payload = line["payload"]
        for k in drop:
            payload.get("sens", {}).pop(k, None)
            payload.get("can", {}).pop(FIELD_SPNS.get(k), None)
    return lines


def load(name: str) -> list[dict]:
    path = SCENARIOS / f"{name}.jsonl"
    if not path.exists():
        path = ENGINE_FIXTURES / f"{name}.jsonl"
    return [json.loads(s) for s in path.read_text().splitlines()]


def step(engine: MachineEngine, line: dict) -> Out:
    topic, payload = line["topic"], line["payload"]
    now = parse_iso(payload["ts"])
    if topic.endswith("/raw"):
        return engine.on_raw(payload, now)
    if topic.endswith("/dtc"):
        return engine.on_dtc(payload, now)
    if topic.endswith("/env"):
        return engine.on_env(payload, now)
    return engine.on_proximity(payload, now)


class Run:
    """Every output of a fixture run, with alerts deep-copied at the moment they happened
    (the alert manager keeps mutating its live records)."""

    def __init__(self, engine: MachineEngine, lines: list[dict], on_out=None):
        self.engine = engine
        self.t0 = parse_iso(lines[0]["payload"]["ts"])
        self.steps: list[tuple[float, Out, list]] = []
        for line in lines:
            out = step(engine, line)
            if on_out:
                on_out(out)
            t = (parse_iso(line["payload"]["ts"]) - self.t0).total_seconds()
            self.steps.append((t, out, copy.deepcopy(out.alerts)))

    def alerts(self, rule_id=None, action=None) -> list[tuple[float, str, dict]]:
        return [
            (t, a, al)
            for t, _, alerts in self.steps
            for a, al in alerts
            if (rule_id is None or al["rule_id"] == rule_id) and (action is None or a == action)
        ]

    def events(self, type_=None) -> list[tuple[float, object]]:
        return [
            (t, e)
            for t, out, _ in self.steps
            for e in out.events
            if type_ is None or e.type == type_
        ]

    def states(self) -> list[tuple[float, dict]]:
        return [(t, out.state) for t, out, _ in self.steps if out.state]

    def class_at(self, t_query: float) -> str:
        cls = None
        for t, s in self.states():
            if t <= t_query:
                cls = s["class"]
        return cls
