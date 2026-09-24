"""Write helpers. Each one runs as a single DbWriter unit of work: the entity row and its
outbox row (if the entity is syncable) go in with `session.add`, and both land together or
neither does when the caller does `writer.submit(lambda s: repo.insert_incident(s, row))`.

Priority table is plan.md Phase 1 item 2. `telemetry_sample` and `telemetry_minute`'s sibling
`telemetry_window` are the only time-series entities here — `telemetry_sample` itself is
never syncable (I8) and is written via `DbWriter.submit_telemetry`, not through this module.
"""

from sqlmodel import Session, select

from common.db.models import (
    DtcOccurrence,
    EngineSnapshot,
    ExitEvent,
    IdleEpisode,
    MachineEvent,
    MachineStateSnapshot,
    SafetyAlert,
    SyncQueue,
    TelemetryMinute,
    TelemetryWindow,
)
from common.timeutil import now_utc, to_site_iso

_PRIORITY_P0 = {"incident", "hazard_pin"}
_PRIORITY_P1 = {"machine_event", "dtc_occurrence", "task", "shift", "machine_state_snapshot"}
_PRIORITY_P2 = {
    "telemetry_window",
    "idle_episode",
    "operator_scorecard",
    "lesson_assignment",
    "walkaround",
}
_PRIORITY_P3 = {"telemetry_minute"}
_CONDITIONAL = {"safety_alert", "readiness_check", "exit_event"}
NOT_SYNCABLE = {"telemetry_sample"}


def priority_for(entity: str, payload: dict) -> int:
    """plan.md Phase 1 item 2 priority table. `payload` decides the three conditional
    entities: a CRITICAL/escalated alert, a RED readiness check, and an UNSAFE exit are P0;
    otherwise they're P1."""
    if entity in _PRIORITY_P0:
        return 0
    if entity == "safety_alert":
        return 0 if payload.get("level") == "CRITICAL" or payload.get("escalated") else 1
    if entity == "readiness_check":
        return 0 if payload.get("rating") == "RED" else 1
    if entity == "exit_event":
        return 0 if payload.get("state_at_intent") == "UNSAFE" else 1
    if entity in _PRIORITY_P1:
        return 1
    if entity in _PRIORITY_P2:
        return 2
    if entity in _PRIORITY_P3:
        return 3
    if entity in NOT_SYNCABLE:
        raise ValueError(f"{entity} is not syncable (I8) — do not enqueue it")
    raise ValueError(f"{entity} is not in the sync priority table (plan.md Phase 1 item 2)")


def enqueue_sync(
    session: Session, entity: str, entity_id: str, op: str, payload: dict
) -> SyncQueue:
    row = SyncQueue(
        entity=entity,
        entity_id=entity_id,
        op=op,
        payload=payload,
        priority=priority_for(entity, payload),
        created_at=to_site_iso(now_utc()),
    )
    session.add(row)
    return row


def insert_incident(session: Session, incident) -> None:
    session.add(incident)
    enqueue_sync(session, "incident", incident.incident_id, "UPSERT", incident.model_dump())


def upsert_machine_state_snapshot(session: Session, machine_id: str, payload: dict) -> None:
    """D8: one current-state row per machine; at most one PENDING outbox row per machine,
    with a newer write replacing its payload instead of queuing another row."""
    now = to_site_iso(now_utc())
    row = session.get(MachineStateSnapshot, machine_id)
    if row is None:
        row = MachineStateSnapshot(machine_id=machine_id, payload=payload, updated_at=now)
    else:
        row.payload, row.updated_at = payload, now
    session.add(row)

    pending = session.exec(
        select(SyncQueue).where(
            SyncQueue.entity == "machine_state_snapshot",
            SyncQueue.entity_id == machine_id,
            SyncQueue.status == "PENDING",
        )
    ).first()
    if pending is not None:
        pending.payload = payload
        session.add(pending)
    else:
        enqueue_sync(session, "machine_state_snapshot", machine_id, "UPSERT", payload)


# --- Phase 3 engine writes: entity + outbox row in the same unit of work (I7) ---------------


def insert_machine_event(session: Session, event: MachineEvent) -> None:
    # Add a copy: the caller keeps publishing its object after this session commits, and a
    # committed instance is expired (its fields read back empty outside the session).
    row = event.model_dump()
    session.add(MachineEvent(**row))
    enqueue_sync(session, "machine_event", event.event_id, "UPSERT", row)


def upsert_safety_alert(session: Session, alert: dict) -> None:
    session.merge(SafetyAlert(**alert))
    enqueue_sync(session, "safety_alert", alert["alert_id"], "UPSERT", alert)


def upsert_exit_event(session: Session, row: dict) -> None:
    session.merge(ExitEvent(**row))
    enqueue_sync(session, "exit_event", row["exit_id"], "UPSERT", row)


def upsert_dtc_occurrence(session: Session, row: dict) -> None:
    session.merge(DtcOccurrence(**row))
    enqueue_sync(session, "dtc_occurrence", row["occurrence_id"], "UPSERT", row)


def insert_idle_episode(session: Session, row: dict) -> None:
    session.merge(IdleEpisode(**row))
    enqueue_sync(session, "idle_episode", row["episode_id"], "UPSERT", row)


def upsert_engine_snapshot(session: Session, machine_id: str, payload: dict) -> None:
    """Crash-recovery state (D18). Edge-local, never synced."""
    session.merge(
        EngineSnapshot(machine_id=machine_id, payload=payload, updated_at=to_site_iso(now_utc()))
    )


def insert_telemetry_minute(session: Session, row: dict) -> None:
    session.add(TelemetryMinute(**row))
    entity_id = f"{row['machine_id']}@{row['window_start']}"
    enqueue_sync(session, "telemetry_minute", entity_id, "UPSERT", row)  # P3


def upsert_telemetry_window(session: Session, row: dict) -> None:
    session.merge(TelemetryWindow(**row))
    enqueue_sync(session, "telemetry_window", row["window_id"], "UPSERT", row)  # P2
