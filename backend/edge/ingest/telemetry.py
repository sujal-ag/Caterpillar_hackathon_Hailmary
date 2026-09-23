"""Normalised telemetry dict -> `telemetry.v1` (HLD §6.15 nested shape) for MQTT/WS.

Values are passed through as-is: a missing signal stays `null` (UNKNOWN, I1), never a
default. `proximity.min_dist_m` is null both for "no person in a good frame" and "can't
tell" here; `state.sensor_health.proximity` is what separates the two for the UI.
"""

import math

from common.timeutil import parse_iso, to_site_iso


def to_telemetry_v1(t: dict, proximity: tuple[float | None, dict | None]) -> dict:
    dist, msg = proximity
    return {
        "schema": "telemetry.v1",
        "ts": to_site_iso(parse_iso(t["ts"])),
        "machine_id": t["machine_id"],
        "operator_id": t["operator_id"],
        "shift_id": t["shift_id"],
        "engine": {
            "state": t["engine_state"],
            "rpm": t["engine_rpm"],
            "load_pct": t["engine_load_pct"],
            "fuel_rate_lph": t["fuel_rate_lph"],
            "fuel_used_total_l": t["fuel_used_total_l"],
            "fuel_level_pct": t["fuel_level_pct"],
            "def_level_pct": t["def_level_pct"],
            "hours": t["engine_hours"],
            "idle_hours_total": t["idle_hours_total"],
            "coolant_c": t["coolant_temp_c"],
            "oil_press_kpa": t["engine_oil_press_kpa"],
            "battery_v": t["battery_v"],
            "power_mode": t["power_mode"],
        },
        "hyd": {
            "pump_press_kpa": t["hyd_pump_press_kpa"],
            "oil_temp_c": t["hyd_oil_temp_c"],
            "lockout": t["hyd_lockout"],
        },
        "implement": {
            "boom_deg": t["boom_angle_deg"],
            "stick_deg": t["stick_angle_deg"],
            "bucket_deg": t["bucket_angle_deg"],
            "bucket_height_m": t["bucket_height_m"],
            "boom_tip_height_m": t["boom_tip_height_m"],
            "grounded": t["implement_grounded"],
            "payload_kg": t["payload_kg"],
            "attachment_id": t["attachment_id"],
            "coupler": t["coupler_lock"],
        },
        "motion": {
            "activity": t["machine_activity"],
            "travel_kmh": t["travel_speed_kmh"],
            "swing_deg_s": t["swing_rate_deg_s"],
            "pitch_deg": t["pitch_deg"],
            "roll_deg": t["roll_deg"],
            "articulation_deg": t["articulation_deg"],
            "parking_brake": t["parking_brake"],
            "transmission_gear": t["transmission_gear"],
        },
        "cab": {
            "seatbelt": t["seatbelt"],
            "seat_occupied": t["seat_occupied"],
            "door_open": t["door_open"],
            "cab_temp_c": t["cab_temp_c"],
            "ac_on": t["cab_ac_on"],
            "joystick_active": t["joystick_active"],
            "lift_mode": t["lift_mode"],
        },
        "pos": {"x_m": t["x_m"], "y_m": t["y_m"], "heading_deg": t["heading_deg"]},
        "counters": {"pass_count": t["pass_count"], "load_count": t["load_count"]},
        "proximity": {
            "min_dist_m": dist if dist is not None and math.isfinite(dist) else None,
            "sector": (msg or {}).get("sector"),
        },
        "quality": t["data_quality"],
    }
