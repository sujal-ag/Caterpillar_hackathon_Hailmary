"""1-min and hourly rollups (plan.md Phase 2 item 8; HLD §6.10, §6.13, D14, D17).

The single definition D14 asks for: everything Phase 3+ needs (fuel per productive hour,
idle split, fuel-per-load with the §6.13 guard) is computed here, once, from normalised
1 Hz samples plus the idle episodes `idle.py` finalizes.

Simplification (ponytail: documented, not hidden): a sample's entire inter-sample interval
is attributed to the minute/hour window containing that sample's own timestamp, and any
inter-sample gap over 2 s is capped at 2 s before being added to productive/idle time or
fuel split. At 1 Hz this misattributes at most a fraction of a second at each window
boundary — real drift only if the upstream feed is running well under 1 Hz. Upgrade path:
split the interval proportionally across the boundary if that ever matters.
"""

from common.ids import uuid7
from common.timeutil import SITE_TZ, parse_iso, to_site_iso

_MAX_DT_S = 2.0


def _active(machine_activity: str | None) -> bool | None:
    if machine_activity is None:
        return None
    return machine_activity in ("WORKING", "TRAVELLING", "LIFTING")


def _running(machine_activity: str | None) -> bool | None:
    if machine_activity is None:
        return None
    return machine_activity != "OFF"


class _MinuteAccum:
    def __init__(self, start, operator_id):
        self.start = start
        self.operator_id = operator_id
        self.fuel_l = 0.0
        self.load_delta = 0
        self.active_s = 0.0
        self.idle_s = 0.0
        self.load_pct_sum = 0.0
        self.load_pct_n = 0


class _HourAccum:
    def __init__(self, start, operator_id):
        self.window_id = uuid7()
        self.start = start
        self.operator_id = operator_id
        self.engine_hours = None
        self.fuel_l = 0.0
        self.load_delta = 0
        self.pass_delta = 0
        self.active_s = 0.0
        self.load_pct_sum = 0.0
        self.load_pct_n = 0
        self.max_hyd_oil_temp_c = None
        self.seatbelt_status = None
        self.belt_off_running_s = 0.0
        self.fuel_idle_l = 0.0
        self.fuel_work_l = 0.0
        self.idling_time_min = 0.0
        self.idle_short_count = 0
        self.idle_medium_count = 0
        self.idle_long_count = 0
        self.payload_kg_sum = 0.0
        self.last_load_count = None


class RollupAccumulator:
    """One instance per machine."""

    def __init__(self, machine_id: str, site_tz=SITE_TZ):
        self._machine_id = machine_id
        self._tz = site_tz
        self._minute: _MinuteAccum | None = None
        self._hour: _HourAccum | None = None
        self._last_ts = None
        self._last_fuel = None
        self._last_load_count = None
        self._last_pass_count = None

    def add_sample(self, sample: dict) -> tuple[dict | None, dict | None]:
        """Returns (finished_minute, finished_hour), either possibly None. Both are
        non-None only for the one sample per hour that crosses both boundaries at once."""
        ts = parse_iso(sample["ts"])
        local = ts.astimezone(self._tz)
        minute_start = local.replace(second=0, microsecond=0)
        hour_start = local.replace(minute=0, second=0, microsecond=0)
        operator_id = sample.get("operator_id")

        finished_minute = None
        finished_hour = None
        if self._minute is not None and minute_start != self._minute.start:
            finished_minute = self._finish_minute(minute_start)
        if self._hour is not None and hour_start != self._hour.start:
            finished_hour = self._finish_hour(hour_start)
        if self._minute is None:
            self._minute = _MinuteAccum(minute_start, operator_id)
        if self._hour is None:
            self._hour = _HourAccum(hour_start, operator_id)

        self._accumulate(sample, ts)
        return finished_minute, finished_hour

    def add_idle_episode(self, episode: dict) -> None:
        """Feed a just-finalized idle.IdleTracker episode into the currently-open hour
        (attributed wholly to the hour containing its start, D17)."""
        if self._hour is None:
            return
        start = parse_iso(episode["start_ts"]).astimezone(self._tz)
        if start < self._hour.start:
            return  # belongs to an hour already closed; caller fed it late
        self._hour.idling_time_min += episode["duration_min"]
        bucket = episode["bucket"]
        if bucket == "SHORT":
            self._hour.idle_short_count += 1
        elif bucket == "MEDIUM":
            self._hour.idle_medium_count += 1
        elif bucket == "LONG":
            self._hour.idle_long_count += 1
        # PAUSE counts toward idling_time_min only (D17), no bucket count.

    def close(self, at_ts: str) -> tuple[dict | None, dict | None]:
        """Force-close open windows (shift end). `at_ts` becomes the window_end."""
        end = parse_iso(at_ts).astimezone(self._tz)
        m = self._finish_minute(end) if self._minute is not None else None
        h = self._finish_hour(end) if self._hour is not None else None
        return m, h

    def _accumulate(self, sample: dict, ts) -> None:
        activity = sample.get("machine_activity")
        running, active = _running(activity), _active(activity)
        fuel_total = sample.get("fuel_used_total_l")
        load_count = sample.get("load_count")
        pass_count = sample.get("pass_count")
        load_pct = sample.get("engine_load_pct")
        seatbelt = sample.get("seatbelt")

        dt = 0.0
        if self._last_ts is not None:
            dt = min((ts - self._last_ts).total_seconds(), _MAX_DT_S)

        fuel_delta = 0.0
        if fuel_total is not None and self._last_fuel is not None:
            fuel_delta = max(fuel_total - self._last_fuel, 0.0)
        if fuel_total is not None:
            self._last_fuel = fuel_total

        load_delta = 0
        if load_count is not None and self._last_load_count is not None:
            load_delta = max(load_count - self._last_load_count, 0)
        if load_count is not None:
            self._last_load_count = load_count

        pass_delta = 0
        if pass_count is not None and self._last_pass_count is not None:
            pass_delta = max(pass_count - self._last_pass_count, 0)
        if pass_count is not None:
            self._last_pass_count = pass_count

        m, h = self._minute, self._hour
        m.fuel_l += fuel_delta
        m.load_delta += load_delta
        if load_pct is not None:
            m.load_pct_sum += load_pct
            m.load_pct_n += 1

        h.fuel_l += fuel_delta
        h.load_delta += load_delta
        h.pass_delta += pass_delta
        if load_pct is not None:
            h.load_pct_sum += load_pct
            h.load_pct_n += 1
        hyd_temp = sample.get("hyd_oil_temp_c")
        if hyd_temp is not None:
            h.max_hyd_oil_temp_c = (
                hyd_temp if h.max_hyd_oil_temp_c is None else max(h.max_hyd_oil_temp_c, hyd_temp)
            )
        if seatbelt is not None:
            h.seatbelt_status = seatbelt
        if sample.get("engine_hours") is not None:
            h.engine_hours = sample["engine_hours"]
        if load_delta and sample.get("payload_kg") is not None:
            h.payload_kg_sum += sample["payload_kg"]

        if active:
            m.active_s += dt
            h.active_s += dt
        elif running is False or (running and active is False):
            m.idle_s += dt
        if running and seatbelt == "UNFASTENED":
            h.belt_off_running_s += dt

        if running and active:
            h.fuel_work_l += fuel_delta
        elif running and active is False:
            h.fuel_idle_l += fuel_delta

        self._last_ts = ts

    def _finish_minute(self, next_start) -> dict:
        m = self._minute
        row = {
            "window_start": to_site_iso(m.start),
            "window_end": to_site_iso(next_start),
            "machine_id": self._machine_id,
            "operator_id": m.operator_id,
            "fuel_used_l": round(m.fuel_l, 4),
            "load_cycles": m.load_delta,
            "productive_min": m.active_s / 60,
            "idle_min": m.idle_s / 60,
            "mean_engine_load_pct": (m.load_pct_sum / m.load_pct_n) if m.load_pct_n else None,
            "state_class_mode": None,  # Phase 3: OSE class isn't computed yet
        }
        self._minute = _MinuteAccum(next_start, m.operator_id)
        return row

    def _finish_hour(self, next_start) -> dict:
        h = self._hour
        productive_min = h.active_s / 60
        loads = h.load_delta
        row = {
            "window_id": h.window_id,
            "window_start": to_site_iso(h.start),
            "window_end": to_site_iso(next_start),
            "machine_id": self._machine_id,
            "operator_id": h.operator_id,
            "engine_hours": h.engine_hours,
            "fuel_used_l": round(h.fuel_l, 4),
            "load_cycles": loads,
            "pass_count": h.pass_delta,
            "idling_time_min": h.idling_time_min,
            "idle_short_count": h.idle_short_count,
            "idle_medium_count": h.idle_medium_count,
            "idle_long_count": h.idle_long_count,
            "seatbelt_status": h.seatbelt_status,
            "seatbelt_unfastened_running_min": h.belt_off_running_s / 60,
            "safety_alert_triggered": None,  # Phase 3: no alerts exist yet
            "productive_min": productive_min,
            "idle_ratio": h.idling_time_min / 60,
            "fuel_idle_l": round(h.fuel_idle_l, 4),
            "fuel_work_l": round(h.fuel_work_l, 4),
            "fuel_per_productive_h": (h.fuel_work_l / (productive_min / 60))
            if productive_min > 0
            else None,
            "fuel_per_load": (h.fuel_work_l / loads) if loads >= 3 else None,  # §6.13 guard
            "loads_per_productive_h": (loads / (productive_min / 60))
            if productive_min > 0
            else None,
            "payload_t": h.payload_kg_sum / 1000,
            "mean_engine_load_pct": (h.load_pct_sum / h.load_pct_n) if h.load_pct_n else None,
            "max_hyd_oil_temp_c": h.max_hyd_oil_temp_c,
            "alert_count": None,  # Phase 3
            "critical_count": None,  # Phase 3
            "anomaly_score": None,  # Phase 6
            "state_class_mode": None,  # Phase 3
            "review_label": None,  # Phase 10 (D11)
        }
        self._hour = _HourAccum(next_start, h.operator_id)
        return row
