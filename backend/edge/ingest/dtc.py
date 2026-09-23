"""dtc.v1 -> dtc_occurrence + DTC_ACTIVE/CLEARED events (plan.md Phase 2 item 6; HLD §4.9).

The action_class an alert (Phase 3, R16/R17) or the Alarm Explainer (Phase 8) shows always
comes from the catalogue lookup here, never invented (I3) — an unknown code is UNDOCUMENTED,
not guessed at.
"""

from common.ids import uuid7

# Event severity for a newly-active occurrence, by its catalogue action_class. Distinct
# from a rule's alert level (Phase 3) — this is just how loud the raw event log entry is.
SEVERITY_FOR_ACTION_CLASS = {
    "STOP": "CRITICAL",
    "MONITOR": "CAUTION",
    "CONTINUE": "INFO",
    "UNDOCUMENTED": "WARNING",
}


def lookup_action_class(
    catalogue: dict, spn: int | None, fmi: int | None, cat_code: str | None
) -> str:
    """`catalogue` maps (spn, fmi) and cat_code to a DiagnosticCode-shaped dict (built by
    the caller from the `diagnostic_code` table)."""
    row = catalogue.get((spn, fmi)) or (cat_code and catalogue.get(cat_code))
    return row["action_class"] if row else "UNDOCUMENTED"


class DtcTracker:
    """One instance per machine. Tracks currently-active (spn, fmi) occurrences so a
    repeated 'still active' frame updates instead of re-opening."""

    def __init__(self):
        self._open: dict[tuple[int, int], dict] = {}

    def process(
        self, dtc_frame: dict, catalogue: dict, current_telemetry: dict | None, seq: int
    ) -> tuple[dict | None, dict | None]:
        """Returns (dtc_occurrence dict to upsert, event dict to emit) — either may be
        None. No event on a repeated 'still active' update."""
        key = (dtc_frame["spn"], dtc_frame["fmi"])
        ts = dtc_frame["ts"]
        machine_id = dtc_frame["machine_id"]

        if dtc_frame["active"]:
            existing = self._open.get(key)
            if existing is None:
                action_class = lookup_action_class(
                    catalogue, dtc_frame["spn"], dtc_frame["fmi"], dtc_frame.get("cat_code")
                )
                occurrence = {
                    "occurrence_id": uuid7(),
                    "machine_id": machine_id,
                    "code_id": None,
                    "spn": dtc_frame["spn"],
                    "fmi": dtc_frame["fmi"],
                    "occurrence_count": dtc_frame.get("oc") or 1,
                    "first_seen": ts,
                    "last_seen": ts,
                    "active": True,
                    "freeze_frame": current_telemetry,
                }
                self._open[key] = occurrence
                event = {
                    "type": "DTC_ACTIVE",
                    "ts": ts,
                    "machine_id": machine_id,
                    "severity": SEVERITY_FOR_ACTION_CLASS[action_class],
                    "seq": seq,
                    "payload": {
                        "spn": dtc_frame["spn"],
                        "fmi": dtc_frame["fmi"],
                        "action_class": action_class,
                    },
                }
                return dict(occurrence), event

            existing["last_seen"] = ts
            existing["occurrence_count"] = max(
                existing["occurrence_count"], dtc_frame.get("oc") or 1
            )
            return dict(existing), None

        existing = self._open.pop(key, None)
        if existing is None:
            return None, None  # clearing something we never saw active
        existing["active"] = False
        existing["last_seen"] = ts
        event = {
            "type": "DTC_CLEARED",
            "ts": ts,
            "machine_id": machine_id,
            "severity": "INFO",
            "seq": seq,
            "payload": {"spn": dtc_frame["spn"], "fmi": dtc_frame["fmi"]},
        }
        return dict(existing), event


def build_catalogue_index(diagnostic_codes: list[dict]) -> dict:
    """diagnostic_codes: rows from the `diagnostic_code` table (as dicts)."""
    index: dict = {}
    for row in diagnostic_codes:
        if row.get("spn") is not None:
            index[(row["spn"], row.get("fmi"))] = row
        if row.get("cat_code"):
            index[row["cat_code"]] = row
    return index
