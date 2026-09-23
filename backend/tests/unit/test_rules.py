"""Phase 3 gate: one test per rule R01–R24 with a true case, a false case and an
UNKNOWN-input case (plan.md Phase 3 verification). Rules that read later-phase data
(zones, readiness, anomaly, fuel baseline, fatigue) get it injected into MachineContext.
"""

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from edge.engine.context import MachineContext
from edge.engine.registry import compute_inputs, evaluate_rule
from edge.engine.rules import load_machine_models, load_rules

RULESET = load_rules()
MODELS = load_machine_models()
NOW = datetime(2026, 10, 14, 5, 0, tzinfo=UTC)

# A running CAT 320, operator seated and belted, hydraulics locked, bucket grounded, level,
# not working. Each case changes only what it needs.
BASE = dict(
    engine_state="RUNNING",
    seatbelt="FASTENED",
    seat_occupied=True,
    door_open=False,
    hyd_lockout="LOCKED",
    parking_brake="ON",
    travel_speed_kmh=0.0,
    swing_rate_deg_s=0.0,
    hyd_pump_press_kpa=3100,
    joystick_active=False,
    bucket_height_m=0.1,
    boom_tip_height_m=3.0,
    pitch_deg=2.0,
    roll_deg=1.0,
    coolant_temp_c=86,
    hyd_oil_temp_c=62,
    x_m=100.0,
    y_m=50.0,
    lift_mode=False,
    coupler_lock="LOCKED",
    cab_ac_on=True,
)
ENV_DRY = dict(
    temp_c=25.0,
    rh_pct=50,
    dew_point_c=13.9,
    wbgt_est_c=24.3,
    rain_flag=False,
    ground_condition="DRY",
)


def ctx_with(model="CAT-320", timers=None, **over) -> MachineContext:
    ctx = MachineContext("EXC001", "SITE-PUN-01", MODELS[model])
    ctx.signals = {**BASE, **over}
    ctx.env = dict(ENV_DRY)
    ctx.timers.update(last_frame_at=NOW, last_position_ts=NOW)
    for name, seconds_ago in (timers or {}).items():
        ctx.timers[name] = NOW - timedelta(seconds=seconds_ago)
    return ctx


def evals(rule_id: str, ctx: MachineContext, exit_open=None):
    rule = dataclasses.replace(RULESET.by_id(rule_id), enabled=True)
    return evaluate_rule(rule, compute_inputs(ctx, NOW, RULESET, exit_open))


def value(rule_id: str, ctx: MachineContext, exit_open=None):
    return evals(rule_id, ctx, exit_open)[0].value


def sim_prox(ctx, dist, frame_ok=True):
    ctx.proximity["sim"] = {
        "msg": {"min_dist_m": dist, "frame_ok": frame_ok, "source": "sim"},
        "at": NOW,
    }
    return ctx


WORKING = dict(travel_speed_kmh=3.0)
OPEN_EXIT = {"exit_id": "EXIT-1", "three_point_prompted": True}

# rule_id -> (true ctx, false ctx, unknown ctx[, exit_open for all three])
CASES = {
    "R01": (
        ctx_with(seatbelt="UNFASTENED", **WORKING),
        ctx_with(**WORKING),
        ctx_with(seatbelt=None, **WORKING),
    ),
    "R02": (
        ctx_with(seatbelt="UNFASTENED"),
        ctx_with(),
        ctx_with(seatbelt="UNFASTENED", seat_occupied=None),
    ),
    "R03": (
        ctx_with(seatbelt="UNFASTENED", door_open=True, hyd_lockout="UNLOCKED"),
        ctx_with(seatbelt="UNFASTENED", door_open=True),  # secured: SAFE_ENGINE_ON
        ctx_with(seatbelt="UNFASTENED", door_open=None, hyd_lockout="UNLOCKED"),
    ),
    "R04": (
        ctx_with(seatbelt="UNFASTENED", door_open=True),
        ctx_with(seatbelt="UNFASTENED", door_open=True, bucket_height_m=2.0),
        ctx_with(seatbelt="UNFASTENED", door_open=None),
    ),
    "R05": (
        ctx_with(seatbelt="UNFASTENED", timers={"belt_unfastened_at": 3}),
        ctx_with(seatbelt="UNFASTENED", timers={"belt_unfastened_at": 30}),
        ctx_with(seatbelt="UNFASTENED", engine_state=None, timers={"belt_unfastened_at": 3}),
    ),
    "R07": (
        ctx_with(seat_occupied=False, timers={"seat_vacated_since": 6}),
        ctx_with(seat_occupied=False, timers={"seat_vacated_since": 3}),
        ctx_with(seat_occupied=None),
    ),
    "R08": (
        ctx_with(seatbelt="UNFASTENED", timers={"inactive_since": 600}),
        ctx_with(seatbelt="UNFASTENED", timers={"inactive_since": 300}),
        ctx_with(seatbelt="UNFASTENED", engine_state=None, timers={"inactive_since": 600}),
    ),
    "R09": (
        ctx_with(timers={"inactive_since": 600}),
        ctx_with(timers={"inactive_since": 300}),
        ctx_with(hyd_pump_press_kpa=None, joystick_active=None, timers={"inactive_since": 600}),
    ),
    "R10": (ctx_with(pitch_deg=18.0), ctx_with(), ctx_with(pitch_deg=None)),
    "R11": (ctx_with(pitch_deg=27.0), ctx_with(pitch_deg=18.0), ctx_with(pitch_deg=None)),
    "R12": (
        sim_prox(ctx_with(**WORKING), 3.0),
        sim_prox(ctx_with(), 3.0),  # person close but machine not active
        sim_prox(ctx_with(**WORKING), None, frame_ok=False),
    ),
    "R13": (
        sim_prox(ctx_with(**WORKING), 6.0),
        sim_prox(ctx_with(**WORKING), 10.0),
        sim_prox(ctx_with(**WORKING), None, frame_ok=False),
    ),
    "R18": (ctx_with(coolant_temp_c=106), ctx_with(), ctx_with(coolant_temp_c=None)),
}


def _zone(ctx, **z):
    ctx.zones_inside = {
        "PIN-1": {"type": "WORKER_ZONE", "entry_id": "E1", "line_clearance_m": None, **z}
    }
    return ctx


def _set(ctx, **attrs):
    for k, v in attrs.items():
        setattr(ctx, k, v)
    return ctx


LINE = dict(type="OVERHEAD_LINE", line_clearance_m=8.0)
CASES |= {
    "R14": (_zone(ctx_with()), ctx_with(), _zone(ctx_with(x_m=None))),
    "R15": (
        _zone(ctx_with(boom_tip_height_m=5.5), **LINE),
        _zone(ctx_with(boom_tip_height_m=4.5), **LINE),
        _zone(ctx_with(boom_tip_height_m=None), **LINE),
    ),
    "R19": (
        _set(ctx_with(), fatigue={"perclos_pct": 20, "long_blinks_per_min": 0}),
        _set(ctx_with(), fatigue={"perclos_pct": 5, "long_blinks_per_min": 0}),
        ctx_with(),  # no camera data
    ),
    "R20": (
        _set(ctx_with(), readiness="RED", shift_id="SH-1"),
        _set(ctx_with(), readiness="GREEN"),
        ctx_with(),
    ),
    "R21": (
        _set(ctx_with(seat_occupied=False), env={**ENV_DRY, "wbgt_est_c": 30.0}),
        _set(ctx_with(seat_occupied=False), env={**ENV_DRY, "wbgt_est_c": 20.0}),
        _set(ctx_with(seat_occupied=False), env=None),
    ),
    "R22": (
        ctx_with(bucket_height_m=2.0, timers={"coupler_cycled_at": 30}),
        ctx_with(bucket_height_m=2.0, timers={"coupler_cycled_at": 30, "ground_press_ok_at": 10}),
        ctx_with(bucket_height_m=2.0, coupler_lock=None, timers={"coupler_cycled_at": 30}),
    ),
    "R23": (
        _set(ctx_with(), anomaly={"window_id": "W1", "score": 0.99, "threshold_top3pct": 0.9}),
        _set(ctx_with(), anomaly={"window_id": "W1", "score": 0.5, "threshold_top3pct": 0.9}),
        ctx_with(),  # model unavailable
    ),
    "R24": (_set(ctx_with(), fuel_ratio=1.6), _set(ctx_with(), fuel_ratio=1.2), ctx_with()),
}


@pytest.mark.parametrize("rule_id", sorted(CASES))
def test_rule_true_false_unknown(rule_id):
    true_ctx, false_ctx, unknown_ctx = CASES[rule_id]
    exit_open = {"exit_id": "EXIT-1"} if rule_id == "R04" else None
    assert value(rule_id, true_ctx, exit_open) is True
    assert value(rule_id, false_ctx, exit_open) is False
    assert value(rule_id, unknown_ctx, exit_open) is None


def test_every_rule_has_a_case():
    covered = set(CASES) | {"R06", "R16", "R17"}  # the three with dedicated tests below
    assert covered == {f"R{i:02d}" for i in range(1, 25)}


def test_r06_follows_exit_tracker_prompt():
    # R06 reads the ExitTracker's once-per-exit prompt flag; `wet` itself is tri-state and
    # tested in test_engine.py (UNKNOWN wet never prompts).
    assert value("R06", ctx_with(), OPEN_EXIT) is True
    assert value("R06", ctx_with(), {**OPEN_EXIT, "three_point_prompted": False}) is False
    assert value("R06", ctx_with(), None) is False


@pytest.mark.parametrize("rule_id,action_class", [("R16", "STOP"), ("R17", "MONITOR")])
def test_r16_r17_dtc_by_catalogue_action_class(rule_id, action_class):
    def dtc(ac):
        return _set(ctx_with(), active_dtcs={"OCC-1": {"spn": 110, "fmi": 0, "action_class": ac}})

    hit = evals(rule_id, dtc(action_class))
    assert [(e.value, e.key) for e in hit] == [(True, "OCC-1")]
    assert value(rule_id, ctx_with()) is False
    other = "MONITOR" if action_class == "STOP" else "STOP"
    assert value(rule_id, dtc(other)) is False
    # An undocumented code is never guessed into STOP/MONITOR (I3): neither rule fires.
    assert value(rule_id, dtc("UNDOCUMENTED")) is False


def test_r10_lift_mode_uses_stricter_roll_threshold():
    assert value("R10", ctx_with(roll_deg=7.0)) is False
    assert value("R10", ctx_with(roll_deg=7.0, lift_mode=True)) is True
    # Unknown lift mode on an excavator: the stricter threshold applies (I1).
    assert value("R10", ctx_with(roll_deg=7.0, lift_mode=None)) is True


def test_r15_power_line_boundary_from_plan_gate():
    # plan.md Phase 5 gate numbers: clearance 8 m -> boom tip 5.5 m fires, 4.5 m doesn't.
    assert value("R15", _zone(ctx_with(boom_tip_height_m=5.5), **LINE)) is True
    assert value("R15", _zone(ctx_with(boom_tip_height_m=4.5), **LINE)) is False


def test_r24_waits_for_a_pause():
    assert value("R24", _set(ctx_with(**WORKING), fuel_ratio=1.6)) is False


def test_stale_data_makes_every_signal_unknown():
    ctx = ctx_with(seatbelt="UNFASTENED", **WORKING)
    ctx.data_stale = True
    assert value("R01", ctx) is None
