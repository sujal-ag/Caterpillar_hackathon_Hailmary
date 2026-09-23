"""Tri-state derived predicates (plan.md Phase 3 item 2; HLD §4.4 "Derived predicates").

True / False / None, where None is UNKNOWN and propagates by Kleene logic: `k_and` is
False if any input is definitely False, `k_or` is True if any input is definitely True,
otherwise any None makes the result None. Nothing here ever turns UNKNOWN into False (I1).
Thresholds come from rules.yaml `predicates` (passed in as `P`) and the machine model.
"""

from datetime import datetime

from edge.engine.context import MachineContext


def k_and(*xs):
    if any(x is False for x in xs):
        return False
    return None if any(x is None for x in xs) else True


def k_or(*xs):
    if any(x is True for x in xs):
        return True
    return None if any(x is None for x in xs) else False


def k_not(x):
    return None if x is None else not x


def gt(v, thr):
    return None if v is None else v > thr


def eq(v, target):
    return None if v is None else v == target


def running(ctx: MachineContext):
    return eq(ctx.sig("engine_state"), "RUNNING")


def active(ctx: MachineContext, now: datetime, P: dict):
    a = P["active"]
    swing = ctx.sig("swing_rate_deg_s")
    joystick_age = ctx.age_s("last_joystick_ts", now)
    joystick = (
        True
        if joystick_age is not None and joystick_age <= a["joystick_window_s"]
        else ctx.sig("joystick_active")
    )
    return k_or(
        gt(ctx.sig("travel_speed_kmh"), a["travel_kmh_gt"]),
        None if swing is None else abs(swing) > a["swing_deg_s_abs_gt"],
        gt(ctx.sig("hyd_pump_press_kpa"), a["hyd_pump_press_kpa_gt"]),
        joystick,
    )


def seconds_at_least(ctx: MachineContext, timer: str, now: datetime, s: float) -> bool:
    age = ctx.age_s(timer, now)
    return age is not None and age >= s


def idle(ctx: MachineContext, now: datetime, P: dict, run, act):
    return k_and(run, k_not(act), seconds_at_least(ctx, "inactive_since", now, P["idle"]["min_s"]))


def grounded(ctx: MachineContext, P: dict):
    h = ctx.sig("bucket_height_m")
    return None if h is None else h <= P["grounded"]["bucket_height_m_le"]


def moving(ctx: MachineContext, P: dict):
    return gt(ctx.sig("travel_speed_kmh"), P["moving"]["travel_kmh_gt"])


def belt_off(ctx: MachineContext):
    return eq(ctx.sig("seatbelt"), "UNFASTENED")


def seat_vacated_for(ctx: MachineContext, now: datetime, s: float):
    """seat_occupied false for more than `s` seconds; None if the seat switch is UNKNOWN."""
    seat = ctx.sig("seat_occupied")
    if seat is None:
        return None
    if seat:
        return False
    age = ctx.age_s("seat_vacated_since", now)
    return age is not None and age > s


def exit_intent(ctx: MachineContext, now: datetime, P: dict, run):
    """Strong intent = running AND (seat vacated > 2 s OR (belt OFF AND door open)).

    User decision (Phase 3, I1): with the seat switch AND the door sensor both UNKNOWN, an
    unfastened belt on a running machine counts as intent — there is no signal left that
    could show the operator is still seated, so Exit Guard must not stay silent."""
    belt, door = belt_off(ctx), ctx.sig("door_open")
    if run is True and belt is True and ctx.sig("seat_occupied") is None and door is None:
        return True
    return k_and(
        run,
        k_or(seat_vacated_for(ctx, now, P["exit_intent"]["seat_vacated_gt_s"]), k_and(belt, door)),
    )


def exit_hint(ctx: MachineContext, now: datetime, P: dict, run):
    age = ctx.age_s("belt_unfastened_at", now)
    return k_and(run, age is not None and age <= P["exit_hint"]["belt_off_within_s"])


def wet(ctx: MachineContext, P: dict):
    env = ctx.env
    if env is None:
        return None
    temp, dew, rh = env.get("temp_c"), env.get("dew_point_c"), env.get("rh_pct")
    humid = k_and(
        None if rh is None else rh >= P["wet"]["rh_pct_ge"],
        None if temp is None or dew is None else temp - dew <= P["wet"]["dew_point_spread_c_le"],
    )
    return k_or(env.get("rain_flag"), humid, eq(env.get("ground_condition"), "WET"))


def lift_mode(ctx: MachineContext):
    if ctx.family != "EXCAVATOR":
        return False  # lift mode is an excavator control (HLD §4.4)
    return k_or(ctx.sig("lift_mode"), ctx.task_type == "LIFTING")


def compute(ctx: MachineContext, now: datetime, P: dict) -> dict:
    run = running(ctx)
    act = active(ctx, now, P)
    return {
        "running": run,
        "active": act,
        "idle": idle(ctx, now, P, run, act),
        "grounded": grounded(ctx, P),
        "moving": moving(ctx, P),
        "belt_off": belt_off(ctx),
        "exit_intent": exit_intent(ctx, now, P, run),
        "exit_hint": exit_hint(ctx, now, P, run),
        "wet": wet(ctx, P),
        "lift_mode": lift_mode(ctx),
    }
