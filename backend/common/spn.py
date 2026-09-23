"""J1939 SPN key in raw.v1 `can` -> normalised field name (plan §5.2, HLD §6.8)."""

SPN_FIELDS: dict[str, str] = {
    "190": "engine_rpm",
    "92": "engine_load_pct",
    "183": "fuel_rate_lph",
    "250": "fuel_used_total_l",
    "96": "fuel_level_pct",
    "1761": "def_level_pct",
    "247": "engine_hours",
    "110": "coolant_temp_c",
    "100": "engine_oil_press_kpa",
    "1638": "hyd_oil_temp_c",
    "168": "battery_v",
    "84": "travel_speed_kmh",
    "1856": "seatbelt",
    "70": "parking_brake",
    "523": "transmission_gear",
}

FIELD_SPNS: dict[str, str] = {v: k for k, v in SPN_FIELDS.items()}
