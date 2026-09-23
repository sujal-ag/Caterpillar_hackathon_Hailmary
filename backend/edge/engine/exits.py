"""Exit Guard: check set, exit state and the exit lifecycle (plan.md Phase 3 item 4; D1, D19).

A check is named after the unsafe condition: "FAIL" means the condition is present (e.g.
HYD_UNLOCKED: FAIL = hydraulics are unlocked). UNKNOWN counts as FAIL everywhere (I1).

exit_state (D1): UNSAFE if any check other than ENGINE_RUNNING is FAIL/UNKNOWN;
SAFE_ENGINE_ON if only ENGINE_RUNNING is; else SAFE.
"""

from datetime import datetime

from common.ids import uuid7
from common.timeutil import to_site_iso
from edge.engine.context import MachineContext

EXIT_COMPLETE_S = 10  # plan.md Phase 3 item 4: seat vacated >= 10 s -> EXIT_COMPLETED


def _check(unsafe) -> str:
    return "UNKNOWN" if unsafe is None else ("FAIL" if unsafe else "PASS")


def compute_exit_checks(ctx: MachineContext, preds: dict) -> dict:
    m = ctx.model
    pitch, roll = ctx.sig("pitch_deg"), ctx.sig("roll_deg")
    slope = (
        None
        if pitch is None or roll is None
        else abs(pitch) > m["pitch_caution_deg"] or abs(roll) > m["roll_caution_deg"]
    )
    brake = ctx.sig("parking_brake")
    hyd = ctx.sig("hyd_lockout")
    checks = {
        "ENGINE_RUNNING": _check(preds["running"]),
        "HYD_UNLOCKED": _check(None if hyd is None else hyd != "LOCKED"),
        "IMPLEMENT_RAISED": _check(None if preds["grounded"] is None else not preds["grounded"]),
        "MOVING": _check(preds["moving"]),
        "SLOPE": _check(slope),
    }
    if ctx.family == "WHEEL_LOADER":
        checks["PARK_BRAKE_OFF"] = _check(None if brake is None else brake != "ON")
    return checks


def failed(checks: dict) -> list[str]:
    return [name for name, v in checks.items() if v != "PASS"]


def exit_state(checks: dict) -> str:
    bad = failed(checks)
    if any(name != "ENGINE_RUNNING" for name in bad):
        return "UNSAFE"
    return "SAFE_ENGINE_ON" if bad else "SAFE"


class ExitTracker:
    """One per machine. `open` is the exit in progress (None when none), a plain dict so the
    snapshot can persist it; `intent_prev` stops a rehydrated engine re-opening the same
    exit (no duplicate EXIT_ATTEMPT)."""

    PERSIST = ("open", "intent_prev")

    def __init__(self):
        self.open: dict | None = None
        self.intent_prev = False

    def update(
        self, ctx: MachineContext, now: datetime, preds: dict, checks: dict, state: str
    ) -> tuple[list[tuple[str, dict]], dict | None]:
        """Returns (events [(type, payload)], exit_event row to upsert or None)."""
        events: list[tuple[str, dict]] = []
        changed = False
        intent = preds["exit_intent"] is True
        ex = self.open

        if intent and not self.intent_prev and ex is None:
            ex = self.open = {
                "exit_id": uuid7(),
                "started_at": now,
                "state_at_intent": state,
                "failed_checks": failed(checks),
                "corrected": False,
                "time_to_correct_s": None,
                "time_outside_s": None,
                "wet_conditions": preds["wet"] is True,
                "three_point_prompted": False,
                "seat_vacated_at": None,
                "door_opened": False,
                "completed": False,
            }
            events.append(
                (
                    "EXIT_ATTEMPT",
                    {
                        "exit_id": ex["exit_id"],
                        "state": state,
                        "failed_checks": ex["failed_checks"],
                    },
                )
            )
            changed = True
        self.intent_prev = intent

        if ex is not None:
            changed = self._progress(ctx, now, preds, state, events) or changed
        return events, (self._row(ctx, ex) if changed else None)

    def _progress(self, ctx, now, preds, state, events) -> bool:
        ex, changed = self.open, False
        if ctx.sig("door_open"):
            ex["door_opened"] = True
        seat = ctx.sig("seat_occupied")
        if seat is False and ex["seat_vacated_at"] is None:
            ex["seat_vacated_at"] = ctx.timers["seat_vacated_since"]

        if ex["state_at_intent"] == "UNSAFE" and not ex["corrected"] and state != "UNSAFE":
            ex["corrected"] = True
            ex["time_to_correct_s"] = (now - ex["started_at"]).total_seconds()
            changed = True

        vacated_s = ctx.age_s("seat_vacated_since", now)
        if not ex["completed"] and seat is False and vacated_s is not None:
            if vacated_s >= EXIT_COMPLETE_S:
                ex["completed"] = True
                events.append(("EXIT_COMPLETED", {"exit_id": ex["exit_id"]}))
                changed = True

        # R06: first moment the exit is no longer UNSAFE, or at completion if never corrected.
        if preds["wet"] is True and not ex["three_point_prompted"]:
            if state != "UNSAFE" or ex["completed"]:
                ex["three_point_prompted"] = ex["wet_conditions"] = True
                changed = True

        if seat is True and preds["exit_intent"] is not True:
            if ex["completed"]:
                ex["time_outside_s"] = (now - ex["seat_vacated_at"]).total_seconds()
                events.append(
                    ("MOUNT", {"exit_id": ex["exit_id"], "time_outside_s": ex["time_outside_s"]})
                )
                changed = True
            # Not completed: operator shifted in the seat or sat back down within 10 s.
            # The exit_event already written at intent stays (decision 5); just close it.
            self.open = None
        return changed

    @staticmethod
    def _row(ctx: MachineContext, ex: dict) -> dict:
        return {
            "exit_id": ex["exit_id"],
            "machine_id": ctx.machine_id,
            "operator_id": ctx.operator_id,
            "ts": to_site_iso(ex["started_at"]),
            "trigger": "STRONG",  # D19
            "state_at_intent": ex["state_at_intent"],
            "failed_checks": ex["failed_checks"],
            "corrected": ex["corrected"],
            "time_to_correct_s": ex["time_to_correct_s"],
            "time_outside_s": ex["time_outside_s"],
            "wet_conditions": ex["wet_conditions"],
            "three_point_prompted": ex["three_point_prompted"],
        }
