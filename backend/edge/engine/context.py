"""Per-machine state vector (plan.md Phase 3 item 1; HLD §4.3).

`sig(name)` is the only way rules read a signal: it returns `None` (UNKNOWN) when the
stream is stale or the signal was missing on the last frame, so no rule can ever read an
old value as live (I1, I6). Timers are plain datetimes on the engine clock.

Fields marked (Phase 5)/(Phase 6) stay empty until those phases feed them; rules that read
them evaluate UNKNOWN meanwhile, and tests inject them directly.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime

_TIMERS = (
    "seat_vacated_since",
    "belt_off_since",
    "belt_unfastened_at",  # last FASTENED -> UNFASTENED transition (exit_hint)
    "last_active_ts",
    "inactive_since",  # running and not active since (idle / idle episode length)
    "last_joystick_ts",
    "coupler_unlock_ts",
    "coupler_cycled_at",  # UNLOCKED -> LOCKED after an unlock (R22)
    "ground_press_since",
    "ground_press_ok_at",
    "last_position_ts",
    "last_frame_at",
)


@dataclass
class MachineContext:
    machine_id: str
    site_id: str
    model: dict  # machine_models.yaml row: family + thresholds
    has_rear_camera: bool = False

    signals: dict = field(default_factory=dict)  # last normalised telemetry
    data_stale: bool = False
    running_s: float = 0.0  # engine-running seconds seen (alerts/operating-hour metric)
    timers: dict = field(default_factory=lambda: dict.fromkeys(_TIMERS))

    env: dict | None = None  # environment_obs-shaped (ingest/env.py)
    proximity: dict = field(default_factory=dict)  # source -> {"msg": ..., "at": datetime}
    active_dtcs: dict = field(default_factory=dict)  # "spn-fmi@first_seen" -> {spn, fmi, ...}

    operator_id: str | None = None
    shift_id: str | None = None
    task_type: str | None = None  # (Phase 6)
    hazard_pins: dict = field(default_factory=dict)  # pin_id -> ACTIVE hazards.v1 pin (retained)
    zones_inside: dict = field(
        default_factory=dict
    )  # pin_id -> {type, line_clearance_m, entry_id} (Phase 5)
    readiness: str | None = None  # shift-start rating GREEN|YELLOW|RED (Phase 5)
    anomaly: dict | None = None  # {window_id, score, threshold_top3pct} (Phase 6)
    fuel_ratio: float | None = None  # fuel_per_productive_h / 14-day baseline (Phase 6)
    fatigue: dict | None = None  # {perclos_pct, long_blinks_per_min} (stretch)

    @property
    def family(self) -> str:
        return self.model["family"]

    def sig(self, name: str):
        if self.data_stale or self.timers["last_frame_at"] is None:
            return None
        return self.signals.get(name)

    def age_s(self, timer: str, now: datetime) -> float | None:
        t = self.timers[timer]
        return None if t is None else (now - t).total_seconds()

    def apply_telemetry(
        self, t: dict, now: datetime, derive, ground_press_hold_s: float = 2.0
    ) -> None:
        """Store a debounced frame and move the timers. `derive(ctx)` is called once the new
        signals are in place and returns (active, ground_press) for this frame — both are
        predicates (predicates.py), kept out of here so thresholds stay in one place."""
        prev, tm = self.signals, self.timers
        if tm["last_frame_at"] is not None and prev.get("engine_state") == "RUNNING":
            self.running_s += min((now - tm["last_frame_at"]).total_seconds(), 2.0)
        tm["last_frame_at"] = now
        self.signals = t
        if t.get("joystick_active"):
            tm["last_joystick_ts"] = now
        active, ground_press = derive(self)

        seat = t.get("seat_occupied")
        tm["seat_vacated_since"] = (tm["seat_vacated_since"] or now) if seat is False else None

        belt = t.get("seatbelt")
        tm["belt_off_since"] = (tm["belt_off_since"] or now) if belt == "UNFASTENED" else None
        if belt == "UNFASTENED" and prev.get("seatbelt") == "FASTENED":
            tm["belt_unfastened_at"] = now

        if active:
            tm["last_active_ts"] = now
        inactive = t.get("engine_state") == "RUNNING" and active is False
        tm["inactive_since"] = (tm["inactive_since"] or now) if inactive else None

        if t.get("x_m") is not None and t.get("y_m") is not None:
            tm["last_position_ts"] = now

        coupler = t.get("coupler_lock")
        if coupler == "UNLOCKED" and prev.get("coupler_lock") == "LOCKED":
            tm["coupler_unlock_ts"] = now
        if coupler == "LOCKED" and prev.get("coupler_lock") == "UNLOCKED":
            tm["coupler_cycled_at"] = now

        # R22 ground-press test: bucket grounded under pressure, held for the rule's hold time.
        tm["ground_press_since"] = (tm["ground_press_since"] or now) if ground_press else None
        if ground_press and (now - tm["ground_press_since"]).total_seconds() >= ground_press_hold_s:
            tm["ground_press_ok_at"] = now

    def fresh_proximity_msg(self, now: datetime, fresh_s: float) -> dict | None:
        """The proximity frame to trust right now: a fresh `cv` frame with frame_ok, else a
        fresh `sim` one, else None."""
        for source in ("cv", "sim"):
            entry = self.proximity.get(source)
            if entry is None or (now - entry["at"]).total_seconds() > fresh_s:
                continue
            if entry["msg"].get("frame_ok"):
                return entry["msg"]
        return None

    def proximity_now(self, now: datetime, fresh_s: float) -> tuple[float | None, str]:
        """(distance_m, status). UNKNOWN when a channel was expected or seen but nothing
        fresh and usable is there; UNAVAILABLE when none is fitted.
        `math.inf` = a good frame with no person in view; `None` = can't tell (I1)."""
        msg = self.fresh_proximity_msg(now, fresh_s)
        if msg is not None:
            dist = msg.get("min_dist_m")
            return (math.inf if dist is None else dist), "OK"
        if self.has_rear_camera or self.proximity:
            return None, "UNKNOWN"
        return None, "UNAVAILABLE"

    def sensor_health(self, now: datetime, policy: dict) -> dict:
        def known(name: str) -> str:
            return "UNKNOWN" if self.sig(name) is None else "OK"

        pos_age = self.age_s("last_position_ts", now)
        position = (
            "OK"
            if known("x_m") == "OK"
            and pos_age is not None
            and pos_age <= policy["position_stale_s"]
            else "UNKNOWN"
        )
        data_ok = self.timers["last_frame_at"] is not None and not self.data_stale
        return {
            "seat": known("seat_occupied"),
            "seatbelt": known("seatbelt"),
            "position": position,
            "proximity": self.proximity_now(now, policy["proximity_fresh_s"])[1],
            "data": "OK" if data_ok else "UNKNOWN",
        }
