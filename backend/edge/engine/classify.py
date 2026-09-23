"""State class + hysteresis (plan.md Phase 3 item 6, §5.3; HLD §4.3).

Order: any active UNSAFE state_effect -> UNSAFE; any ATTENTION state_effect, any sensor
UNKNOWN or data stale -> ATTENTION; engine OFF -> OFF; running AND hyd LOCKED AND not
active -> PARKED; else PRODUCTIVE. An UNKNOWN engine state is ATTENTION, never PRODUCTIVE
(I1). Step up immediately; step down only after `hysteresis_s` clear.
"""

from datetime import datetime

_RANK = {"UNSAFE": 2, "ATTENTION": 1, "OFF": 0, "PARKED": 0, "PRODUCTIVE": 0}


def raw_class(state_effects: set[str], sensor_health: dict, preds: dict, hyd_lockout) -> str:
    if "UNSAFE" in state_effects:
        return "UNSAFE"
    unknown_sensor = any(v == "UNKNOWN" for v in sensor_health.values())
    if "ATTENTION" in state_effects or unknown_sensor or preds["running"] is None:
        return "ATTENTION"
    if preds["running"] is False:
        return "OFF"
    if hyd_lockout == "LOCKED" and preds["active"] is False:
        return "PARKED"
    return "PRODUCTIVE"


class Classifier:
    PERSIST = ("current", "clear_since")

    def __init__(self, hysteresis_s: float):
        self.hysteresis_s = hysteresis_s
        self.current = "OFF"
        self.clear_since: datetime | None = None

    def update(self, raw: str, now: datetime) -> str:
        if _RANK[raw] >= _RANK[self.current]:
            self.current, self.clear_since = raw, None
        elif self.clear_since is None:
            self.clear_since = now
        elif (now - self.clear_since).total_seconds() >= self.hysteresis_s:
            self.current, self.clear_since = raw, None
        return self.current
