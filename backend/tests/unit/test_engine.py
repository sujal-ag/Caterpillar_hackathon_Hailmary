"""Phase 3 gate: I1, the user's degraded-seat decision, alert-fatigue controls, hysteresis,
rule isolation, stale handling, snapshot round-trip and the I9 boundary (plan.md Phase 3
verification). Scenario fixtures and the crash test are in test_engine_scenarios.py."""

import dataclasses
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from edge.engine import registry
from edge.engine.alerts import AlertManager
from edge.engine.classify import Classifier
from edge.engine.context import MachineContext
from edge.engine.registry import Eval
from edge.engine.rules import RuleSet
from tests.engine_support import MODELS, RULESET, Run, inline, load, make_engine, step

T0 = datetime(2026, 10, 14, 5, 0, tzinfo=UTC)
BACKEND = Path(__file__).resolve().parents[2]


def with_enabled(*rule_ids) -> RuleSet:
    rules = tuple(
        dataclasses.replace(r, enabled=True) if r.id in rule_ids else r for r in RULESET.rules
    )
    return dataclasses.replace(RULESET, rules=rules)


def active_entry(engine, rule_id):
    return next(e for e in engine.alerts.active.values() if e["rule_id"] == rule_id)


# --- I1 and the degraded-seat decision ------------------------------------------------------


def test_i1_seat_switch_missing_belt_off_running():
    lines = inline(
        [{"at": 0, "mode": "IDLE"}, {"at": 5, "set": {"seatbelt": "UNFASTENED"}}],
        drop=("seat_occupied",),
    )
    run = Run(make_engine(), lines)
    state = run.engine.state
    assert state["sensor_health"]["seat"] == "UNKNOWN"
    assert all(s["class"] != "PRODUCTIVE" for _, s in run.states())
    assert state["class"] == "ATTENTION"
    # UNKNOWN intent does not raise Exit Guard (user decision) ...
    assert run.alerts("R03") == []
    # ... but R05 is shown as a BANNER listing the failed checks.
    raised = run.alerts("R05", "RAISED")
    assert len(raised) == 1 and raised[0][0] == 5
    assert active_entry(run.engine, "R05")["display"] == "BANNER"
    assert raised[0][2]["slots"]["failed_checks"] == [
        "ENGINE_RUNNING",
        "HYD_UNLOCKED",
        "IMPLEMENT_RAISED",
    ]


def test_i1_unknown_exit_check_counts_as_fail():
    lines = inline(
        [
            {"at": 0, "mode": "IDLE"},
            {"at": 5, "set": {"seatbelt": "UNFASTENED", "bucket_height_m": 0.1}},
            {"at": 6, "set": {"door_open": True}},
        ],
        drop=("hyd_lockout",),
    )
    run = Run(make_engine(), lines)
    ((t, _, alert),) = run.alerts("R03", "RAISED")
    assert t == 6
    assert "HYD_UNLOCKED" in alert["slots"]["failed_checks"]
    assert run.engine.state["exit_checks"]["HYD_UNLOCKED"] == "UNKNOWN"
    assert run.engine.state["exit_state"] == "UNSAFE"


def test_seat_and_door_unknown_belt_off_counts_as_intent():
    lines = inline(
        [{"at": 0, "mode": "IDLE"}, {"at": 5, "set": {"seatbelt": "UNFASTENED"}}],
        drop=("seat_occupied", "door_open"),
    )
    run = Run(make_engine(), lines)
    assert [t for t, _, _ in run.alerts("R03", "RAISED")] == [5]
    assert run.alerts("R05") == []  # intent is TRUE here, so the pre-warn path is not used
    assert len(run.events("EXIT_ATTEMPT")) == 1


def test_unknown_wet_never_prompts_three_point_contact():
    lines = [ln for ln in load("unsafe_exit_corrected") if not ln["topic"].endswith("/env")]
    run = Run(make_engine(), lines)
    assert run.alerts("R06") == []
    assert all(not row["three_point_prompted"] for _, out, _ in run.steps for row in out.exit_rows)


def test_unknown_input_holds_an_active_alert():
    lines = inline(
        [
            {"at": 0, "mode": "IDLE"},
            {"at": 5, "set": {"pitch_deg": 27.0}},
            {"at": 10, "set": {"pitch_deg": None}},
            {"at": 15, "set": {"pitch_deg": 3.0}},
        ],
        duration_s=20,
    )
    run = Run(make_engine(), lines)
    assert [(t, a) for t, a, _ in run.alerts("R11")] == [(5, "RAISED"), (15, "CLEARED")]


# --- stale / malformed ----------------------------------------------------------------------


def test_stale_stream_is_unknown_then_restored():
    engine = make_engine()
    lines = inline([{"at": 0, "mode": "WORK"}], duration_s=5)
    for line in lines:
        step(engine, line)
    last = datetime.fromisoformat(lines[-1]["payload"]["ts"])
    out = engine.tick(last + timedelta(seconds=4))
    assert [e.type for e in out.events] == ["DATA_STALE"]
    assert engine.state["data_stale"] and engine.state["class"] == "ATTENTION"
    assert engine.state["sensor_health"]["data"] == "UNKNOWN"
    assert engine.ctx.sig("engine_state") is None  # old values are never read as live (I6)
    frame = dict(lines[-1]["payload"], ts=(last + timedelta(seconds=5)).isoformat())
    out = engine.on_raw(frame, last + timedelta(seconds=5))
    assert "DATA_RESTORED" in [e.type for e in out.events]
    assert not engine.state["data_stale"]


def test_malformed_frame_dropped_and_counted():
    engine = make_engine()
    out = engine.on_raw({"schema": "raw.v1", "ts": "not-a-time"}, T0)
    assert out.telemetry is None and engine.dropped_frames == 1


# --- alert-fatigue controls -----------------------------------------------------------------


def _ctx():
    return MachineContext("EXC001", "SITE-PUN-01", MODELS["CAT-320"])


def _process(mgr, now, *rule_ids):
    return mgr.process([(RULESET.by_id(r), [Eval(True)]) for r in rule_ids], _ctx(), now)


def test_audio_budget_non_critical_once_per_30s_critical_always():
    mgr = AlertManager(RULESET.policy["audio_budget_s"])
    # Three non-critical rules with AUDIO on different subjects, within 10 s.
    first = _process(mgr, T0, "R10")
    second = _process(mgr, T0 + timedelta(seconds=4), "R10", "R18")
    third = _process(mgr, T0 + timedelta(seconds=8), "R10", "R18", "R13")
    crit = _process(mgr, T0 + timedelta(seconds=9), "R10", "R18", "R13", "R01")
    assert "AUDIO" in first[0][1]["channels"]
    assert second[0][1]["rule_id"] == "R18" and "AUDIO" not in second[0][1]["channels"]
    assert third[0][1]["rule_id"] == "R13" and "AUDIO" not in third[0][1]["channels"]
    assert third[0][1]["channels"] == ["VISUAL"]
    assert crit[0][1]["rule_id"] == "R01"
    assert crit[0][1]["channels"] == ["VISUAL", "AUDIO", "VIBRATION"]  # I4


def test_suppression_same_subject_and_no_audio_spent():
    mgr = AlertManager(RULESET.policy["audio_budget_s"])
    _process(mgr, T0, "R11")
    ((action, r10),) = _process(mgr, T0 + timedelta(seconds=1), "R11", "R10")
    assert action == "RAISED" and r10["suppressed"] and r10["suppress_reason"] == "HIGHER_ACTIVE"
    # R10 was suppressed, so the next non-critical audio is still available.
    ((_, r18),) = _process(mgr, T0 + timedelta(seconds=2), "R11", "R10", "R18")
    assert "AUDIO" in r18["channels"]


def test_critical_reraises_inside_its_cooldown():
    mgr = AlertManager(RULESET.policy["audio_budget_s"])
    rule = RULESET.by_id("R11")  # cooldown_s 20
    mgr.process([(rule, [Eval(True)])], _ctx(), T0)
    mgr.process([(rule, [Eval(False)])], _ctx(), T0 + timedelta(seconds=1))
    again = mgr.process([(rule, [Eval(True)])], _ctx(), T0 + timedelta(seconds=3))
    assert [a for a, _ in again] == ["RAISED"]  # I4: never rate-limited


def test_non_critical_cooldown_blocks_reraise():
    mgr = AlertManager(RULESET.policy["audio_budget_s"])
    rule = RULESET.by_id("R10")  # cooldown_s 60
    mgr.process([(rule, [Eval(True)])], _ctx(), T0)
    mgr.process([(rule, [Eval(False)])], _ctx(), T0 + timedelta(seconds=1))
    assert mgr.process([(rule, [Eval(True)])], _ctx(), T0 + timedelta(seconds=30)) == []
    assert mgr.process([(rule, [Eval(True)])], _ctx(), T0 + timedelta(seconds=61))


def test_alerts_per_operating_hour_metric():
    mgr = AlertManager(30)
    _process(mgr, T0, "R10", "R01")  # one non-critical, one critical
    assert mgr.alerts_per_operating_hour(1800) == 2.0
    assert mgr.alerts_per_operating_hour(0) is None


# --- hysteresis -----------------------------------------------------------------------------


def test_hysteresis_steps_down_only_after_10s_clear():
    c = Classifier(RULESET.policy["class_hysteresis_s"])
    assert c.update("UNSAFE", T0) == "UNSAFE"
    clear = T0 + timedelta(seconds=5)
    assert c.update("PRODUCTIVE", clear) == "UNSAFE"
    assert c.update("PRODUCTIVE", clear + timedelta(seconds=9.9)) == "UNSAFE"
    assert c.update("PRODUCTIVE", clear + timedelta(seconds=10)) == "PRODUCTIVE"
    assert c.update("UNSAFE", clear + timedelta(seconds=11)) == "UNSAFE"  # step up: immediate


# --- rule isolation, stretch rules ----------------------------------------------------------


def test_rule_raising_is_disabled_and_logged_others_keep_running(monkeypatch, caplog):
    def boom(inp, rule):
        raise RuntimeError("predicate bug")

    monkeypatch.setitem(registry.PREDICATES, "tilt_caution", boom)
    caplog.set_level(logging.ERROR, logger="edge.engine")
    run = Run(make_engine(), load("unsafe_exit_corrected"))
    assert "R10" in run.engine.metrics()["disabled_rules"]
    assert len(run.alerts("R03", "RAISED")) == 1
    logged = [json.loads(r.getMessage()) for r in caplog.records]
    assert any(e["event"] == "rule_disabled" and e["rule_id"] == "R10" for e in logged)
    assert sum(e["event"] == "rule_disabled" for e in logged) == 1  # disabled once, not per tick


def test_stretch_rules_never_fire_while_disabled():
    lines = inline(
        [
            {"at": 0, "mode": "IDLE"},
            {"at": 3, "set": {"coupler_lock": "UNLOCKED"}},
            {"at": 5, "set": {"coupler_lock": "LOCKED"}},
        ],
        duration_s=10,
    )
    for ruleset, expect in ((RULESET, []), (with_enabled("R19", "R22"), ["R19", "R22"])):
        engine = make_engine(ruleset)
        engine.ctx.fatigue = {"perclos_pct": 40, "long_blinks_per_min": 3}
        run = Run(engine, lines)
        fired = sorted({al["rule_id"] for _, _, al in run.alerts(action="RAISED")} & {"R19", "R22"})
        assert fired == expect


# --- snapshot, I9 ---------------------------------------------------------------------------


def test_snapshot_round_trip_mid_exit_continues_identically():
    lines = load("unsafe_exit_corrected")
    cut = next(
        i for i, ln in enumerate(lines) if ln["payload"]["ts"].endswith("07:01:05.000+05:30")
    )
    a = make_engine()
    Run(a, lines[:cut])
    assert a.exits.open is not None and any(e["rule_id"] == "R03" for e in a.alerts.active.values())
    b = make_engine()
    b.restore(json.loads(json.dumps(a.snapshot())))  # must survive a JSON column
    assert b.exits.open == a.exits.open
    assert b.alerts.active.keys() == a.alerts.active.keys()
    rest_a, rest_b = Run(a, lines[cut:]), Run(b, lines[cut:])

    def summary(run):
        return [(t, act, al["rule_id"]) for t, act, al in run.alerts()]

    assert summary(rest_a) == summary(rest_b)
    assert [e.type for _, e in rest_b.events("EXIT_ATTEMPT")] == []


def test_sim_label_never_reaches_rules_or_state():
    engine = make_engine()
    frames = [ln for ln in load("unsafe_exit_corrected") if ln["topic"].endswith("/raw")]
    labelled = next(ln for ln in frames if ln["payload"]["sim_label"])
    out = step(engine, labelled)
    assert "sim_label" not in out.telemetry and "sim_label" not in json.dumps(engine.snapshot())
    assert out.sim_labels == [
        {"machine_id": "EXC001", "ts": labelled["payload"]["ts"], "sim_label": "UNSAFE_EXIT"}
    ]
    # I9: in edge/ and common/, only the ingest boundary that strips it (machine.py), the
    # writer that stores it in sim_label_log (supervisor.py) and the storage model mention
    # sim_label; no rule, predicate, context or classifier code can read it.
    hits = sorted(
        str(p.relative_to(BACKEND))
        for d in ("edge", "common")
        for p in (BACKEND / d).rglob("*.py")
        if "sim_label" in p.read_text()
    )
    assert hits == ["common/db/models.py", "edge/engine/machine.py", "edge/engine/supervisor.py"]
