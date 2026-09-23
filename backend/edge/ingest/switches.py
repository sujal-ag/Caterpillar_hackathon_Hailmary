"""Leading-edge debounce for discrete switches (plan.md Phase 2 item 2, D2).

D2: a classic trailing debounce (wait 500 ms, then accept) adds latency to every switch
change, which fights the <300 ms nudge target. Leading-edge instead: accept a change the
instant it's seen, then ignore further changes of that same switch for 500 ms. Zero added
latency on the change that matters; flapping right after it is suppressed.
"""

from datetime import datetime, timedelta

DEBOUNCE_MS = 500

# Switches that carry a debounced, event-worthy discrete state (HLD §6.9).
SWITCH_NAMES = (
    "seatbelt",
    "seat_occupied",
    "door_open",
    "hyd_lockout",
    "parking_brake",
    "coupler_lock",
    "engine_state",
)


class Debouncer:
    """One instance per machine. `state[name] = (accepted_value, accepted_at, last_attempt_at)`."""

    def __init__(self, window_ms: int = DEBOUNCE_MS):
        self._window = timedelta(milliseconds=window_ms)
        # accepted_at is None until the first real *change* — the debounce window
        # protects a change from flapping, it must not delay the first change itself.
        # last_attempt_at is the window's reference point: it refreshes on every
        # suppressed flap, so a burst of flapping faster than the window never lets a
        # second change through (matches D2's demo case: 100 ms flapping for a full
        # second still yields exactly one accepted change, not one every 500 ms).
        self._state: dict[str, tuple[object, datetime | None, datetime | None]] = {}

    def apply(self, name: str, new_value: object, now: datetime) -> tuple[object, bool]:
        """Returns (value_to_use, changed). `changed` is True only on an accepted
        transition — never on the first observation of a switch (that's a baseline, not
        a change) and never on a flap inside the debounce window."""
        if name not in self._state:
            self._state[name] = (new_value, None, None)
            return new_value, False

        value, accepted_at, last_attempt = self._state[name]
        if new_value == value:
            return value, False  # matches the current stable value, not an attempt

        if accepted_at is not None and now - last_attempt < self._window:
            self._state[name] = (value, accepted_at, now)  # refresh: still flapping
            return value, False

        self._state[name] = (new_value, now, now)
        return new_value, True
