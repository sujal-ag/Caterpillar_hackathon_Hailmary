"""One predicate function per rule R01–R24 (plan.md Phase 3 item 3; HLD §4.4 rule table).

Each function takes `(inp, rule)` and returns a list of `Eval`s. `value` is tri-state: None
= UNKNOWN, which the alert manager treats as "hold" (never raise, never clear). `key` scopes
multi-instance / once-per rules: one alert per DTC occurrence (R16/R17), per zone entry
(R14), per power-line pin (R15), per exit (R04/R06), per episode (R09), per shift (R20),
per hint event (R05), per anomaly window (R23). Thresholds come from `rule.params`,
rules.yaml `predicates` and the machine model — never from literals here.
"""

from dataclasses import dataclass, field
from datetime import datetime

from common.timeutil import to_site_iso
from edge.engine import exits, predicates
from edge.engine.context import MachineContext
from edge.engine.predicates import eq, gt, k_and, k_not, k_or
from edge.engine.rules import Rule, RuleSet


@dataclass
class Eval:
    value: bool | None
    key: str = ""
    inputs: dict = field(default_factory=dict)
    thresholds: dict = field(default_factory=dict)
    slots: dict = field(default_factory=dict)
    display: str | None = None  # overrides rule.display (R05 degraded-seat banner)


@dataclass
class Inputs:
    """Everything rules read for one evaluation, computed once per tick."""

    ctx: MachineContext
    now: datetime
    ruleset: RuleSet
    p: dict  # predicates.compute()
    checks: dict  # exit checks, always computed
    exit_state: str  # from `checks`
    exit_open: dict | None  # ExitTracker.open after this tick's update


def compute_inputs(ctx, now, ruleset, exit_open=None) -> Inputs:
    p = predicates.compute(ctx, now, ruleset.predicates)
    checks = exits.compute_exit_checks(ctx, p)
    return Inputs(ctx, now, ruleset, p, checks, exits.exit_state(checks), exit_open)


def _iso(dt) -> str:
    return "" if dt is None else to_site_iso(dt)


def _sigs(ctx: MachineContext, *names) -> dict:
    return {n: ctx.sig(n) for n in names}


def _one(value, **kw) -> list[Eval]:
    return [Eval(value, **kw)]


# --- seatbelt / exit ---------------------------------------------------------------------


def r01_belt_off_active(inp: Inputs, rule: Rule):
    p = inp.p
    return _one(
        k_and(p["belt_off"], p["active"]),
        inputs={
            **_sigs(
                inp.ctx,
                "seatbelt",
                "travel_speed_kmh",
                "swing_rate_deg_s",
                "hyd_pump_press_kpa",
                "joystick_active",
            ),
            "active": p["active"],
        },
        thresholds=inp.ruleset.predicates["active"],
    )


def r02_belt_off_parked(inp: Inputs, rule: Rule):
    p, ctx = inp.p, inp.ctx
    return _one(
        k_and(
            p["belt_off"],
            p["running"],
            eq(ctx.sig("hyd_lockout"), "LOCKED"),
            k_not(p["active"]),
            ctx.sig("seat_occupied"),
        ),
        inputs={
            **_sigs(ctx, "seatbelt", "engine_state", "hyd_lockout", "seat_occupied"),
            "active": p["active"],
        },
    )


def _exit_inputs(inp: Inputs) -> dict:
    return {
        **_sigs(
            inp.ctx,
            "engine_state",
            "seat_occupied",
            "seatbelt",
            "door_open",
            "hyd_lockout",
            "bucket_height_m",
            "travel_speed_kmh",
            "pitch_deg",
            "roll_deg",
            "parking_brake",
        ),
        "exit_intent": inp.p["exit_intent"],
        "exit_checks": inp.checks,
        "exit_state": inp.exit_state,
    }


def _exit_thresholds(inp: Inputs) -> dict:
    P, m = inp.ruleset.predicates, inp.ctx.model
    return {
        "grounded_max_m": P["grounded"]["bucket_height_m_le"],
        "seat_vacated_s": P["exit_intent"]["seat_vacated_gt_s"],
        "moving_kmh": P["moving"]["travel_kmh_gt"],
        "pitch_caution_deg": m["pitch_caution_deg"],
        "roll_caution_deg": m["roll_caution_deg"],
    }


def r03_exit_guard_unsafe(inp: Inputs, rule: Rule):
    return _one(
        k_and(inp.p["exit_intent"], inp.exit_state == "UNSAFE"),
        inputs=_exit_inputs(inp),
        thresholds=_exit_thresholds(inp),
        slots={"failed_checks": exits.failed(inp.checks)},
    )


def r04_exit_safe_engine_on(inp: Inputs, rule: Rule):
    ex = inp.exit_open
    return _one(
        k_and(inp.p["exit_intent"], inp.exit_state == "SAFE_ENGINE_ON"),
        key=ex["exit_id"] if ex else "",
        inputs=_exit_inputs(inp),
        thresholds=_exit_thresholds(inp),
    )


def r05_exit_prewarn(inp: Inputs, rule: Rule):
    """Normal: exit_hint AND NOT exit_intent (INFO chip). Degraded (user decision, I1):
    seat switch UNKNOWN, belt OFF, running, intent not confirmed and any exit check
    FAIL/UNKNOWN -> BANNER listing the failed checks, since the strong-intent path is blind."""
    p, ctx = inp.p, inp.ctx
    bad = exits.failed(inp.checks)
    if (
        ctx.sig("seat_occupied") is None
        and p["belt_off"] is True
        and p["running"] is True
        and p["exit_intent"] is not True
    ):
        return _one(
            bool(bad),
            key="degraded@" + _iso(ctx.timers["belt_off_since"]),
            inputs=_exit_inputs(inp),
            slots={"failed_checks": bad},
            display=rule.params.get("degraded_seat_display"),
        )
    return _one(
        k_and(p["exit_hint"], k_not(p["exit_intent"])),
        key=_iso(ctx.timers["belt_unfastened_at"]),
        inputs=_exit_inputs(inp),
        slots={"failed_checks": bad},
    )


def r06_three_point(inp: Inputs, rule: Rule):
    ex = inp.exit_open
    prompted = ex is not None and ex["three_point_prompted"]
    return _one(
        prompted, key=ex["exit_id"] if ex else "", inputs={"wet": inp.p["wet"], "env": inp.ctx.env}
    )


def r07_belt_bypass(inp: Inputs, rule: Rule):
    ctx, s = inp.ctx, rule.params["seat_empty_gt_s"]
    return _one(
        k_and(
            eq(ctx.sig("seatbelt"), "FASTENED"),
            predicates.seat_vacated_for(ctx, inp.now, s),
            inp.p["running"],
        ),
        inputs=_sigs(ctx, "seatbelt", "seat_occupied", "engine_state"),
        thresholds={"seat_empty_gt_s": s},
    )


def _idle_ge(inp: Inputs, rule: Rule):
    minutes = rule.params["idle_episode_ge_min"]
    long_ = predicates.seconds_at_least(inp.ctx, "inactive_since", inp.now, minutes * 60)
    return k_and(inp.p["idle"], long_), minutes


def r08_long_idle_unbelted(inp: Inputs, rule: Rule):
    long_idle, minutes = _idle_ge(inp, rule)
    return _one(
        k_and(long_idle, inp.p["belt_off"], inp.p["running"]),
        inputs={**_sigs(inp.ctx, "seatbelt", "engine_state"), "idle": inp.p["idle"]},
        thresholds={"idle_episode_ge_min": minutes},
    )


def r09_long_idle(inp: Inputs, rule: Rule):
    long_idle, minutes = _idle_ge(inp, rule)
    return _one(
        long_idle,
        key=_iso(inp.ctx.timers["inactive_since"]),
        inputs={"idle": inp.p["idle"], "idle_since": _iso(inp.ctx.timers["inactive_since"])},
        thresholds={"idle_episode_ge_min": minutes},
    )


# --- tilt, proximity, zones ---------------------------------------------------------------


def _abs_gt(v, thr):
    return None if v is None or thr is None else abs(v) > thr


def r10_tilt_caution(inp: Inputs, rule: Rule):
    ctx, m = inp.ctx, inp.ctx.model
    lift = inp.p["lift_mode"]
    roll_thr = m["roll_caution_deg"]
    if lift is True and m.get("roll_caution_lift_deg") is not None:
        roll_thr = m["roll_caution_lift_deg"]
    elif lift is None and m.get("roll_caution_lift_deg") is not None:
        roll_thr = m["roll_caution_lift_deg"]  # unknown lift mode: stricter threshold (I1)
    return _one(
        k_or(
            _abs_gt(ctx.sig("pitch_deg"), m["pitch_caution_deg"]),
            _abs_gt(ctx.sig("roll_deg"), roll_thr),
        ),
        inputs={**_sigs(ctx, "pitch_deg", "roll_deg"), "lift_mode": lift},
        thresholds={"pitch_caution_deg": m["pitch_caution_deg"], "roll_caution_deg": roll_thr},
    )


def r11_tilt_critical(inp: Inputs, rule: Rule):
    ctx, m = inp.ctx, inp.ctx.model
    return _one(
        k_or(
            _abs_gt(ctx.sig("pitch_deg"), m["pitch_critical_deg"]),
            _abs_gt(ctx.sig("roll_deg"), m["roll_critical_deg"]),
        ),
        inputs=_sigs(ctx, "pitch_deg", "roll_deg"),
        thresholds={
            "pitch_critical_deg": m["pitch_critical_deg"],
            "roll_critical_deg": m["roll_critical_deg"],
        },
    )


def _proximity(inp: Inputs):
    fresh = inp.ruleset.policy["proximity_fresh_s"]
    return inp.ctx.proximity_now(inp.now, fresh)


def r12_proximity_critical(inp: Inputs, rule: Rule):
    dist, status = _proximity(inp)
    crit = inp.ctx.model["proximity_critical_m"]
    return _one(
        k_and(None if dist is None else dist < crit, inp.p["active"]),
        inputs={"min_dist_m": dist, "proximity": status, "active": inp.p["active"]},
        thresholds={"proximity_critical_m": crit},
    )


def r13_proximity_warning(inp: Inputs, rule: Rule):
    dist, status = _proximity(inp)
    m = inp.ctx.model
    crit, warn = m["proximity_critical_m"], m["proximity_warning_m"]
    return _one(
        k_and(None if dist is None else crit <= dist < warn, inp.p["active"]),
        inputs={"min_dist_m": dist, "proximity": status, "active": inp.p["active"]},
        thresholds={"proximity_critical_m": crit, "proximity_warning_m": warn},
    )


def _position_known(inp: Inputs) -> bool:
    return inp.ctx.sensor_health(inp.now, inp.ruleset.policy)["position"] == "OK"


def r14_geofence_enter(inp: Inputs, rule: Rule):
    zones = inp.ctx.zones_inside
    if not zones:
        return _one(False)
    value = True if _position_known(inp) else None  # position stale: geofence paused (§4.12)
    return [
        Eval(
            value,
            key=z["entry_id"],
            inputs={"pin_id": pin_id, **_sigs(inp.ctx, "x_m", "y_m")},
            slots={"hazard_type": z["type"]},
        )
        for pin_id, z in zones.items()
    ]


def r15_powerline(inp: Inputs, rule: Rule):
    lines = {k: z for k, z in inp.ctx.zones_inside.items() if z["type"] == "OVERHEAD_LINE"}
    if not lines:
        return _one(False)
    tip = inp.ctx.sig("boom_tip_height_m")
    margin = rule.params["clearance_margin_m"]
    return [
        Eval(
            None
            if tip is None or not _position_known(inp)
            else tip > z["line_clearance_m"] - margin,
            key=pin_id,
            inputs={"boom_tip_height_m": tip, "pin_id": pin_id},
            thresholds={"line_clearance_m": z["line_clearance_m"], "clearance_margin_m": margin},
        )
        for pin_id, z in lines.items()
    ]


# --- machine health, operator, environment ------------------------------------------------


def r16_r17_dtc(inp: Inputs, rule: Rule):
    want = rule.params["action_class"]
    hits = [
        Eval(
            True,
            key=key,
            inputs=dict(d),
            slots={"spn": d["spn"], "fmi": d["fmi"], "action_class": d["action_class"]},
        )
        for key, d in inp.ctx.active_dtcs.items()
        if d["action_class"] == want
    ]
    return hits or _one(False)


def r18_temp_rising(inp: Inputs, rule: Rule):
    ctx, m = inp.ctx, inp.ctx.model
    return _one(
        k_or(
            None
            if ctx.sig("coolant_temp_c") is None
            else ctx.sig("coolant_temp_c") >= m["coolant_caution_c"],
            None
            if ctx.sig("hyd_oil_temp_c") is None
            else ctx.sig("hyd_oil_temp_c") >= m["hyd_oil_caution_c"],
        ),
        inputs=_sigs(ctx, "coolant_temp_c", "hyd_oil_temp_c"),
        thresholds={
            "coolant_caution_c": m["coolant_caution_c"],
            "hyd_oil_caution_c": m["hyd_oil_caution_c"],
        },
    )


def r19_fatigue(inp: Inputs, rule: Rule):
    f = inp.ctx.fatigue or {}
    prm = rule.params
    blinks = f.get("long_blinks_per_min")
    return _one(
        k_or(
            gt(f.get("perclos_pct"), prm["perclos_pct_gt"]),
            None if blinks is None else blinks >= prm["long_blinks_per_min_ge"],
        ),
        inputs=dict(f),
        thresholds=dict(prm),
    )


def r20_readiness_red(inp: Inputs, rule: Rule):
    r = inp.ctx.readiness
    return _one(eq(r, "RED"), key=inp.ctx.shift_id or "", inputs={"readiness": r})


def r21_heat(inp: Inputs, rule: Rule):
    ctx, env = inp.ctx, inp.ctx.env or {}
    thr = rule.params["wbgt_est_c_ge"]
    outside = k_or(k_not(ctx.sig("seat_occupied")), inp.exit_open is not None)
    return _one(
        k_and(
            None if env.get("wbgt_est_c") is None else env["wbgt_est_c"] >= thr,
            k_or(outside, k_not(ctx.sig("cab_ac_on"))),
        ),
        inputs={"wbgt_est_c": env.get("wbgt_est_c"), **_sigs(ctx, "seat_occupied", "cab_ac_on")},
        thresholds={"wbgt_est_c_ge": thr},
    )


def r22_coupler(inp: Inputs, rule: Rule):
    ctx, tm = inp.ctx, inp.ctx.timers
    if ctx.sig("coupler_lock") is None:
        return _one(None)
    cycled = tm["coupler_cycled_at"]
    recent = (
        cycled is not None and (inp.now - cycled).total_seconds() <= rule.params["test_window_s"]
    )
    tested = (
        cycled is not None
        and tm["ground_press_ok_at"] is not None
        and (tm["ground_press_ok_at"] >= cycled)
    )
    return _one(
        k_and(recent, not tested, k_not(inp.p["grounded"])),
        key=_iso(cycled),
        inputs={
            "coupler_cycled_at": _iso(cycled),
            "ground_press_ok_at": _iso(tm["ground_press_ok_at"]),
            "grounded": inp.p["grounded"],
        },
        thresholds=dict(rule.params),
    )


def r23_anomaly(inp: Inputs, rule: Rule):
    a = inp.ctx.anomaly
    if a is None or a.get("score") is None:
        return _one(None)  # model UNAVAILABLE: no score, rules keep running (plan §5.6)
    return _one(a["score"] >= a["threshold_top3pct"], key=a["window_id"], inputs=dict(a))


def r24_fuel(inp: Inputs, rule: Rule):
    ratio, thr = inp.ctx.fuel_ratio, rule.params["ratio_gt"]
    at_pause = k_not(inp.p["active"]) if rule.params.get("deliver_at_pause") else True
    return _one(
        k_and(gt(ratio, thr), at_pause),
        inputs={"fuel_ratio": ratio, "active": inp.p["active"]},
        thresholds={"ratio_gt": thr},
    )


PREDICATES = {
    "seatbelt_off_while_active": r01_belt_off_active,
    "seatbelt_off_parked": r02_belt_off_parked,
    "exit_guard_unsafe": r03_exit_guard_unsafe,
    "exit_safe_engine_on": r04_exit_safe_engine_on,
    "exit_hint_only": r05_exit_prewarn,
    "exit_and_wet": r06_three_point,
    "belt_bypass": r07_belt_bypass,
    "long_idle_unbelted": r08_long_idle_unbelted,
    "long_idle": r09_long_idle,
    "tilt_caution": r10_tilt_caution,
    "tilt_critical": r11_tilt_critical,
    "proximity_critical": r12_proximity_critical,
    "proximity_warning": r13_proximity_warning,
    "geofence_enter": r14_geofence_enter,
    "powerline_clearance": r15_powerline,
    "dtc_action_class": r16_r17_dtc,
    "temp_rising": r18_temp_rising,
    "fatigue_camera": r19_fatigue,
    "readiness_red": r20_readiness_red,
    "heat_risk": r21_heat,
    "coupler_unverified": r22_coupler,
    "anomaly_top_pct": r23_anomaly,
    "fuel_vs_baseline": r24_fuel,
}


def evaluate_rule(rule: Rule, inp: Inputs) -> list[Eval]:
    return PREDICATES[rule.predicate](inp, rule)
