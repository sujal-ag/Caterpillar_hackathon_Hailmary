"""Phase 4: nudge generator + telemetry.v1 wire shape (plan.md Phase 4 item 1, §5.4)."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from edge.engine.machine import MachineEngine
from tests.engine_support import CATALOGUE, MODELS, RULESET, Run, load, make_engine

SCHEMAS = Path(__file__).resolve().parents[3] / "contracts" / "schemas"


def validator(name):
    return Draft202012Validator(json.loads((SCHEMAS / f"{name}.json").read_text()))


def nudges(run: Run, rule_id=None):
    return [
        (t, n)
        for t, out, _ in run.steps
        for n in out.nudges
        if rule_id is None or n["rule_id"] == rule_id
    ]


def test_every_nudge_and_telemetry_frame_matches_the_contract():
    v_nudge, v_tel = validator("nudge.v1"), validator("telemetry.v1")
    for name in (
        "unsafe_exit",
        "proximity_intrusion",
        "dtc_1638_16",
        "tilt_excursion",
        "belt_off_parked",
    ):
        run = Run(make_engine(), load(name))
        for _, n in nudges(run):
            assert not list(v_nudge.iter_errors(n)), (name, n)
        for _, out, _ in run.steps:
            if out.telemetry_v1:
                assert not list(v_tel.iter_errors(out.telemetry_v1)), name


def test_checklist_follows_config_order_and_machine_check_set():
    order = RULESET.exit_checks["checklist_order"]
    ((_, r03),) = nudges(Run(make_engine(), load("unsafe_exit")), "R03")
    assert r03["checklist"] == [c for c in order if c != "PARK_BRAKE_OFF"]  # excavator

    loader = MachineEngine("WL001", "SITE-PUN-01", MODELS["CAT-950GC"], RULESET, CATALOGUE)
    lines = [
        {
            **ln,
            "topic": ln["topic"].replace("EXC001", "WL001"),
            "payload": {**ln["payload"], "machine_id": "WL001"}
            if "machine_id" in ln["payload"]
            else ln["payload"],
        }
        for ln in load("unsafe_exit")
    ]
    ((_, r03),) = nudges(Run(loader, lines), "R03")
    assert r03["checklist"] == order  # the wheel loader adds PARK_BRAKE_OFF


def test_escalation_nudges_with_the_configured_chime():
    run = Run(make_engine(), load("belt_off_parked"))
    r02 = nudges(run, "R02")
    assert [t for t, _ in r02] == [10, 310]
    first, esc = r02[0][1], r02[1][1]
    assert first["channels"] == ["VISUAL"] and first["tone_pattern"] is None  # silent chip
    esc_cfg = RULESET.by_id("R02").escalate_to
    assert esc["level"] == esc_cfg["level"] and esc["audio_clip"] == esc_cfg["audio_clip"]
    assert esc["tone_pattern"] == RULESET.policy["tone_patterns"][esc_cfg["level"]]


def test_repeats_nudge_and_suppressed_alerts_do_not():
    run = Run(make_engine(), load("belt_off_moving"))
    assert [t for t, _ in nudges(run, "R01")] == [10, 30, 50]  # repeat_while_true_s: 20

    run = Run(make_engine(), load("tilt_excursion"))
    assert [t for t, _ in nudges(run, "R10")] == [10]  # suppressed by R11 at 20: no nudge
    assert [t for t, _ in nudges(run, "R11")] == [20]
