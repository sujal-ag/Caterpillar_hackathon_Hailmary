"""Switch transitions -> machine_event rows (plan.md Phase 2 item 3; HLD §6.9)."""

from common.db.models import MachineEvent
from common.ids import uuid7

# {switch name: {new_value: event type}}. A value with no entry (e.g. engine_state
# "CRANKING") produces no event — HLD's type enum has nothing for it.
EVENT_TYPE_MAP: dict[str, dict[object, str]] = {
    "seatbelt": {"FASTENED": "SEATBELT_FASTENED", "UNFASTENED": "SEATBELT_UNFASTENED"},
    "seat_occupied": {True: "SEAT_OCCUPIED", False: "SEAT_VACATED"},
    "door_open": {True: "DOOR_OPEN", False: "DOOR_CLOSED"},
    "hyd_lockout": {"LOCKED": "HYD_LOCK", "UNLOCKED": "HYD_UNLOCK"},
    "parking_brake": {"ON": "PARK_BRAKE_ON", "OFF": "PARK_BRAKE_OFF"},
    "coupler_lock": {"LOCKED": "COUPLER_LOCK", "UNLOCKED": "COUPLER_UNLOCK"},
    "engine_state": {"RUNNING": "IGNITION_ON", "OFF": "IGNITION_OFF"},
}

# Plain state-transition logging, not a rule outcome — INFO unless noted otherwise.
DEFAULT_SEVERITY = "INFO"
SEVERITY_OVERRIDES = {"DATA_STALE": "WARNING"}


class Seq:
    """Monotonic per-machine event sequence (HLD §6.9 `seq`)."""

    def __init__(self):
        self._next: dict[str, int] = {}

    def next(self, machine_id: str) -> int:
        n = self._next.get(machine_id, 0)
        self._next[machine_id] = n + 1
        return n


def event_type_for_switch(name: str, new_value: object) -> str | None:
    return EVENT_TYPE_MAP.get(name, {}).get(new_value)


def make_event(
    *,
    machine_id: str,
    ts: str,
    type_: str,
    seq: int,
    source: str,
    severity: str | None = None,
    operator_id: str | None = None,
    shift_id: str | None = None,
    rule_id: str | None = None,
    payload: dict | None = None,
) -> MachineEvent:
    return MachineEvent(
        event_id=uuid7(),
        ts=ts,
        machine_id=machine_id,
        operator_id=operator_id,
        shift_id=shift_id,
        type=type_,
        severity=severity or SEVERITY_OVERRIDES.get(type_, DEFAULT_SEVERITY),
        rule_id=rule_id,
        payload=payload,
        seq=seq,
        source=source,
    )
