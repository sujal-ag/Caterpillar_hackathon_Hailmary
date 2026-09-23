"""Idle episode segmentation (plan.md Phase 2 item 7; HLD §4.4, §6.10, D17).

`idle` only becomes true after >= 30 s of "running but not active" (HLD §4.4), so a
sub-30 s dip is noise, never logged. Once confirmed, the episode is backdated to when
inactivity actually started (`inactive_since`), not to the 30 s confirmation moment — a
fixture-intended "2-minute pause" must roll up as a 2-minute episode, not 1.5.

Buckets (HLD §6.10): PAUSE < 3 min, SHORT 3-6, MEDIUM 6-9, LONG >= 9. D17: `idling_time_min`
counts every bucket including PAUSE; `idle_short/medium/long_count` excludes PAUSE.
"""

from common.ids import uuid7
from common.timeutil import parse_iso

IDLE_MIN_S = 30
PAUSE_MAX_MIN = 3
SHORT_MAX_MIN = 6
MEDIUM_MAX_MIN = 9


def bucket_for(duration_min: float) -> str:
    if duration_min < PAUSE_MAX_MIN:
        return "PAUSE"
    if duration_min < SHORT_MAX_MIN:
        return "SHORT"
    if duration_min < MEDIUM_MAX_MIN:
        return "MEDIUM"
    return "LONG"


class _Accum:
    __slots__ = (
        "inactive_since",
        "started_emitted",
        "rpm_sum",
        "rpm_n",
        "belt_off_s",
        "last_ts",
        "fuel_start",
        "fuel_last",
    )

    def __init__(self, inactive_since, fuel_used_total_l):
        self.inactive_since = inactive_since
        self.started_emitted = False
        self.rpm_sum = 0.0
        self.rpm_n = 0
        self.belt_off_s = 0.0
        self.last_ts = inactive_since
        self.fuel_start = fuel_used_total_l
        self.fuel_last = fuel_used_total_l


class IdleTracker:
    """One instance per machine."""

    def __init__(self, low_idle_rpm: int):
        self._low_idle_rpm = low_idle_rpm
        self._accum: _Accum | None = None

    def update(
        self,
        *,
        running: bool,
        active: bool | None,
        seatbelt: str | None,
        engine_rpm: float | None,
        fuel_used_total_l: float | None,
        ts: str,
    ) -> tuple[str | None, dict | None]:
        """One call per 1 Hz sample. Returns (event_type, idle_episode dict), both
        Optional. `active is None` (unknown) is treated as "not confirmed active", same
        as False, since I1 says an unknown signal must never read as safe/normal."""
        now = parse_iso(ts)
        is_inactive = running and not active

        if not is_inactive:
            return self._finalize(now, resumed_active=running)

        if self._accum is None:
            self._accum = _Accum(now, fuel_used_total_l)
        acc = self._accum
        # ponytail: dt is 0 on the first tick of a run (nothing to measure from yet), so
        # belt_off_s/mean_rpm undercount by ~one sample period per episode. duration_min
        # itself is exact (computed from inactive_since at finalize, not by summing dt).
        # Upgrade path: track the pre-run sample's ts too, if that precision ever matters.
        dt = (now - acc.last_ts).total_seconds()
        acc.last_ts = now
        if engine_rpm is not None:
            acc.rpm_sum += engine_rpm
            acc.rpm_n += 1
        if seatbelt == "UNFASTENED":
            acc.belt_off_s += dt
        if fuel_used_total_l is not None:
            acc.fuel_last = fuel_used_total_l

        elapsed_s = (now - acc.inactive_since).total_seconds()
        if elapsed_s >= IDLE_MIN_S and not acc.started_emitted:
            acc.started_emitted = True
            return "IDLE_START", None
        return None, None

    def _finalize(self, now, *, resumed_active: bool) -> tuple[str | None, dict | None]:
        acc, self._accum = self._accum, None
        if acc is None:
            return None, None
        duration_min = (now - acc.inactive_since).total_seconds() / 60
        if not acc.started_emitted:
            return None, None  # never reached 30 s: not an episode at all

        mean_rpm = (acc.rpm_sum / acc.rpm_n) if acc.rpm_n else None
        fuel_l = (
            acc.fuel_last - acc.fuel_start
            if acc.fuel_start is not None and acc.fuel_last is not None
            else None
        )
        episode = {
            "episode_id": uuid7(),
            "start_ts": acc.inactive_since.isoformat(timespec="milliseconds"),
            "end_ts": now.isoformat(timespec="milliseconds"),
            "duration_min": duration_min,
            "bucket": bucket_for(duration_min),
            "fuel_l": fuel_l,
            "mean_rpm": mean_rpm,
            "elevated_rpm": (mean_rpm is not None and mean_rpm > self._low_idle_rpm + 300),
            "seatbelt_off_min": acc.belt_off_s / 60,
        }
        return "IDLE_END", episode
