"""No raw frame for > 3 s -> DATA_STALE; recovery clears it (plan.md Phase 2 item 4, HLD §4.2).

Keep in sync with `contracts/rules.yaml` `policy.data_stale_s` (Phase 3 loads that file for
the rule engine; this module doesn't depend on it to stay decoupled from Phase 3).
"""

from datetime import datetime, timedelta

STALE_AFTER_S = 3


class StaleTracker:
    """One instance shared across machines; state keyed by machine_id."""

    def __init__(self, stale_after_s: float = STALE_AFTER_S):
        self._threshold = timedelta(seconds=stale_after_s)
        self._last_received: dict[str, datetime] = {}
        self._stale: set[str] = set()

    def mark_received(self, machine_id: str, now: datetime) -> str | None:
        """Call on every raw frame. Returns 'DATA_RESTORED' if this frame ends a stale
        period, else None."""
        self._last_received[machine_id] = now
        if machine_id in self._stale:
            self._stale.discard(machine_id)
            return "DATA_RESTORED"
        return None

    def check(self, machine_id: str, now: datetime) -> str | None:
        """Call on a periodic tick (independent of frame arrival). Returns 'DATA_STALE'
        the first time the gap is confirmed, else None (including on every later tick
        while already stale — one event per stale period, not a repeat every tick)."""
        last = self._last_received.get(machine_id)
        if last is None or machine_id in self._stale:
            return None
        if now - last > self._threshold:
            self._stale.add(machine_id)
            return "DATA_STALE"
        return None

    def is_stale(self, machine_id: str) -> bool:
        return machine_id in self._stale
