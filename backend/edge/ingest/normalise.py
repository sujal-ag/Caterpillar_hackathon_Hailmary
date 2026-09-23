"""raw.v1 -> telemetry_sample-shaped dict (plan.md Phase 2 item 1; HLD §6.8, §5.2).

A missing or null signal stays `None` (UNKNOWN, I1) — it is never defaulted to a value
that could look like a real reading. `validate_raw` is the drop/count boundary: a frame
that fails it is never passed to `normalise`.
"""

from common.spn import SPN_FIELDS
from common.timeutil import parse_iso

REQUIRED_TOP_LEVEL = ("schema", "ts", "site_id", "machine_id")

# Bits set when a signal the rules engine treats as safety-critical is UNKNOWN on this
# frame. Not exhaustive telemetry validation — just enough for a "sensor health" glance
# without re-deriving it from every field on every read.
_QUALITY_BITS = {
    "seatbelt": 1 << 0,
    "seat_occupied": 1 << 1,
    "hyd_lockout": 1 << 2,
    "x_m": 1 << 3,
    "proximity_min_dist_m": 1 << 4,
}

_STANDBY_HYD_PRESS_KPA = 4000  # rules.yaml predicates.active
_TRAVEL_ACTIVE_KMH = 0.3


def validate_raw(frame: dict) -> list[str]:
    """Reasons this frame is malformed, empty if it's valid enough to normalise."""
    errors = []
    if frame.get("schema") != "raw.v1":
        errors.append(f"schema must be 'raw.v1', got {frame.get('schema')!r}")
    for key in REQUIRED_TOP_LEVEL:
        if not frame.get(key):
            errors.append(f"missing required field: {key}")
    ts = frame.get("ts")
    if ts:
        try:
            parse_iso(ts)
        except ValueError as exc:
            errors.append(f"bad ts: {exc}")
    return errors


def _activity(sens: dict, travel_speed_kmh: float | None) -> str | None:
    engine_state = sens.get("engine_state")
    if engine_state is None:
        return None
    if engine_state != "RUNNING":
        return "OFF"
    if sens.get("lift_mode"):
        return "LIFTING"
    if travel_speed_kmh is not None and travel_speed_kmh > _TRAVEL_ACTIVE_KMH:
        return "TRAVELLING"
    swing = sens.get("swing_rate_deg_s")
    hyd = sens.get("hyd_pump_press_kpa")
    joystick = sens.get("joystick_active")
    working = (
        (swing is not None and abs(swing) > 1)
        or (hyd is not None and hyd > _STANDBY_HYD_PRESS_KPA)
        or joystick
    )
    return "WORKING" if working else "IDLE"


def normalise(frame: dict) -> dict:
    """Assumes `validate_raw(frame) == []`."""
    can = frame.get("can") or {}
    sens = frame.get("sens") or {}
    by_field = {field: can.get(spn) for spn, field in SPN_FIELDS.items()}

    bucket_height = sens.get("bucket_height_m")
    grounded = None if bucket_height is None else bucket_height <= 0.3  # D12

    quality = 0
    for key, bit in _QUALITY_BITS.items():
        if by_field.get(key) is None and sens.get(key) is None:
            quality |= bit

    return {
        "ts": frame["ts"],
        "machine_id": frame["machine_id"],
        "operator_id": None,
        "shift_id": None,
        "engine_state": sens.get("engine_state"),
        "engine_rpm": by_field["engine_rpm"],
        "engine_load_pct": by_field["engine_load_pct"],
        "fuel_rate_lph": by_field["fuel_rate_lph"],
        "fuel_used_total_l": by_field["fuel_used_total_l"],
        "fuel_level_pct": by_field["fuel_level_pct"],
        "def_level_pct": by_field["def_level_pct"],
        "engine_hours": by_field["engine_hours"],
        "idle_hours_total": sens.get("idle_hours_total"),
        "coolant_temp_c": by_field["coolant_temp_c"],
        "engine_oil_press_kpa": by_field["engine_oil_press_kpa"],
        "hyd_oil_temp_c": by_field["hyd_oil_temp_c"],
        "hyd_pump_press_kpa": sens.get("hyd_pump_press_kpa"),
        "battery_v": by_field["battery_v"],
        "travel_speed_kmh": by_field["travel_speed_kmh"],
        "swing_rate_deg_s": sens.get("swing_rate_deg_s"),
        "boom_angle_deg": sens.get("boom_angle_deg"),
        "stick_angle_deg": sens.get("stick_angle_deg"),
        "bucket_angle_deg": sens.get("bucket_angle_deg"),
        "bucket_height_m": bucket_height,
        "boom_tip_height_m": sens.get("boom_tip_height_m"),
        "implement_grounded": grounded,
        "payload_kg": sens.get("payload_kg"),
        "pass_count": sens.get("pass_count"),
        "load_count": sens.get("load_count"),
        "machine_activity": _activity(sens, by_field["travel_speed_kmh"]),
        "pitch_deg": sens.get("pitch_deg"),
        "roll_deg": sens.get("roll_deg"),
        "x_m": sens.get("x_m"),
        "y_m": sens.get("y_m"),
        "heading_deg": sens.get("heading_deg"),
        "seatbelt": by_field["seatbelt"],
        "seat_occupied": sens.get("seat_occupied"),
        "door_open": sens.get("door_open"),
        "hyd_lockout": sens.get("hyd_lockout"),
        "parking_brake": by_field["parking_brake"],
        "transmission_gear": by_field["transmission_gear"],
        "articulation_deg": sens.get("articulation_deg"),
        "joystick_active": sens.get("joystick_active"),
        "lift_mode": sens.get("lift_mode"),
        "power_mode": sens.get("power_mode"),
        "attachment_id": sens.get("attachment_id"),
        "coupler_lock": sens.get("coupler_lock"),
        "cab_temp_c": sens.get("cab_temp_c"),
        "cab_ac_on": sens.get("cab_ac_on"),
        "proximity_min_dist_m": None,
        "proximity_sector": None,
        "data_quality": quality,
    }
