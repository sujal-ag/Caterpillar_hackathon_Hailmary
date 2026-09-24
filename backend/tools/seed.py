"""Load data/seed/*.yaml and data/catalogue/diagnostic_codes.yaml into the edge DB.

Idempotent: every row is `session.merge()`d (upsert by primary key), so running this twice
gives identical row counts and field values (plan.md Phase 1 gate). Writes go straight
through a session, not the outbox — master data is CFG, not something the edge pushes to
the cloud as a delta (Phase 9's sync *pulls* roster/tasks from the cloud instead).

`assumptions` in a seed file is metadata for reviewers (plan.md §0 rule 5); it is dropped
before constructing a row except on MachineModel and TaskType, which have a real column for
it (rules.yaml and the ETA model may want to know a threshold is unconfirmed).
"""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import bcrypt
import yaml
from sqlmodel import Session, func, select

from common.config import Settings
from common.db.models import (
    Attachment,
    DiagnosticCode,
    ExitEvent,
    Lesson,
    Machine,
    MachineModel,
    Operator,
    SafetyAlert,
    Shift,
    Site,
    Task,
    TaskType,
)
from common.db.session import create_all, get_engine
from common.ids import uuid7_at
from common.shifts import planned_window, shift_id_for
from common.timeutil import to_site_iso

DATA_DIR = Settings().data_dir  # DATA_DIR env var (infra/.env.example)
SEED_DIR = DATA_DIR / "seed"
CATALOGUE_DIR = DATA_DIR / "catalogue"


def _load(dir_: Path, name: str):
    return yaml.safe_load((dir_ / name).read_text())


def _without(row: dict, *keys: str) -> dict:
    return {k: v for k, v in row.items() if k not in keys}


def seed_site(session: Session) -> None:
    session.merge(Site(**_without(_load(SEED_DIR, "site.yaml"), "assumptions")))


def seed_machine_models(session: Session) -> None:
    for row in _load(SEED_DIR, "machine_models.yaml"):
        session.merge(MachineModel(**row))  # keeps `assumptions` (real column, D9)


def seed_attachments(session: Session) -> None:
    for row in _load(SEED_DIR, "attachments.yaml"):
        session.merge(Attachment(**_without(row, "assumptions")))


def seed_machines(session: Session) -> None:
    for row in _load(SEED_DIR, "machines.yaml"):
        session.merge(Machine(**_without(row, "assumptions")))


def seed_operators(session: Session) -> None:
    for row in _load(SEED_DIR, "operators.yaml"):
        row = _without(row, "assumptions")
        pin = row.pop("seed_pin")
        row["pin_hash"] = bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()
        session.merge(Operator(**row))


def seed_task_types(session: Session) -> None:
    for row in _load(SEED_DIR, "task_types.yaml"):
        session.merge(TaskType(**row))  # keeps `assumptions`


def seed_diagnostic_codes(session: Session) -> None:
    for row in _load(CATALOGUE_DIR, "diagnostic_codes.yaml"):
        session.merge(DiagnosticCode(**row))


def seed_shifts_and_demo_tasks(
    session: Session, site_tz: ZoneInfo, now: datetime, settings: Settings
) -> None:
    """PLANNED shifts from shifts_demo.yaml for `now`'s site-local date (window =
    SHIFT_START/SHIFT_END), and the demo tasks attached to them, scheduled as offsets from
    the shift's planned start. Everything is relative to `now`, so a seed at 00:30 or a
    reset on another day still gives a working demo."""
    today = now.astimezone(site_tz).date()
    start, end = planned_window(today, settings.shift_start, settings.shift_end, site_tz)

    # Insert-if-absent, not merge: EDGE_SEED_ON_START re-seeds on every container start and
    # must not reset a shift or task that is already in progress (delete the DB to reset).
    for row in _load(SEED_DIR, "shifts_demo.yaml"):
        shift_id = shift_id_for(row["machine_id"], today)
        if session.get(Shift, shift_id) is not None:
            continue
        session.add(
            Shift(
                shift_id=shift_id,
                operator_id=row["operator_id"],
                machine_id=row["machine_id"],
                site_id="SITE-PUN-01",
                planned_start=to_site_iso(start),
                planned_end=to_site_iso(end),
                status="PLANNED",
            )
        )
    session.flush()

    for row in _load(SEED_DIR, "tasks_demo.yaml"):
        row = dict(row)
        suffix = row.pop("task_id_suffix")
        start_off, end_off = row.pop("start_after_shift_min"), row.pop("end_after_shift_min")
        row["task_id"] = f"TASK-DEMO-{today:%Y%m%d}-{row['machine_id']}-{suffix}"
        if session.get(Task, row["task_id"]) is not None:
            continue
        shift = session.get(Shift, shift_id_for(row["machine_id"], today))
        base = datetime.fromisoformat(shift.planned_start) if shift else start
        row["shift_id"] = shift_id_for(row["machine_id"], today)
        row["scheduled_start"] = to_site_iso(base + timedelta(minutes=start_off))
        row["scheduled_end"] = to_site_iso(base + timedelta(minutes=end_off))
        session.add(Task(**row))


def seed_demo_history(session: Session, now: datetime) -> None:
    """One earlier unsafe exit for OP1001 on EXC001, two days before `now`: an R03 alert
    (cleared) and its UNSAFE exit_event (corrected). The live demo exit is then the second
    offence in 7 days, so the lesson reads "R03 ×2 in 7 days" (HLD §3.2, §12 3:15). Skipped
    if OP1001 already has an R03 alert in the last 7 days. Demo data: no outbox rows."""
    week_ago = to_site_iso(now - timedelta(days=7))
    exists = session.exec(
        select(SafetyAlert).where(
            SafetyAlert.operator_id == "OP1001",
            SafetyAlert.rule_id == "R03",
            SafetyAlert.ts >= week_ago,
        )
    ).first()
    if exists is not None:
        return
    at = now - timedelta(days=2)
    failed = ["ENGINE_RUNNING", "HYD_UNLOCKED", "IMPLEMENT_RAISED"]
    session.add(
        ExitEvent(
            exit_id=uuid7_at(at),
            machine_id="EXC001",
            operator_id="OP1001",
            ts=to_site_iso(at),
            trigger="STRONG",
            state_at_intent="UNSAFE",
            failed_checks=failed,
            corrected=True,
            time_to_correct_s=14,
        )
    )
    session.add(
        SafetyAlert(
            alert_id=uuid7_at(at),
            ts=to_site_iso(at),
            machine_id="EXC001",
            operator_id="OP1001",
            rule_id="R03",
            level="CRITICAL",
            subject="EXIT",
            state_snapshot={
                "inputs": {
                    "bucket_height_m": 2.1,
                    "hyd_lockout": "UNLOCKED",
                    "exit_state": "UNSAFE",
                },
                "thresholds": {},
                "seeded": "demo history (tools/seed.py)",
            },
            message_key="nudge.exit_guard.before_exiting",
            slots={"failed_checks": failed},
            channels=["VISUAL", "AUDIO", "VIBRATION"],
            active=False,
            cleared_at=to_site_iso(at + timedelta(seconds=14)),
        )
    )


def seed_lessons(session: Session, path: Path) -> None:
    """Lesson catalogue rows from lesson.v1 (P3's lessons.json; a stub until then)."""
    if not path.exists():
        print(f"no lesson catalogue at {path}: lessons disabled", file=sys.stderr)
        return
    doc = json.loads(path.read_text())
    for row in doc["lessons"]:
        session.merge(Lesson(**row))


TABLES = [
    Site,
    MachineModel,
    Attachment,
    Machine,
    Operator,
    TaskType,
    DiagnosticCode,
    Shift,
    Task,
    Lesson,
    SafetyAlert,
    ExitEvent,
]


def run(engine=None, now: datetime | None = None, settings: Settings | None = None) -> dict:
    """`now` (default: wall clock) anchors the demo shift, tasks and history; the runtime
    passes its own clock so tests and a demo reset can seed at any time."""
    engine = engine or get_engine()
    settings = settings or Settings()
    now = now or datetime.now(UTC)
    create_all(engine)
    site_tz = ZoneInfo(_load(SEED_DIR, "site.yaml")["timezone"])

    with Session(engine) as session:
        seed_site(session)
        session.commit()
        seed_machine_models(session)
        session.commit()
        seed_attachments(session)
        session.commit()
        seed_machines(session)
        session.commit()
        seed_operators(session)
        session.commit()
        seed_task_types(session)
        session.commit()
        seed_diagnostic_codes(session)
        session.commit()
        seed_lessons(session, settings.lessons_path)
        session.commit()
        seed_shifts_and_demo_tasks(session, site_tz, now, settings)
        session.commit()
        seed_demo_history(session, now)
        session.commit()

        return {
            t.__tablename__: session.exec(select(func.count()).select_from(t)).one() for t in TABLES
        }


def main() -> None:
    counts = run()
    for name, n in counts.items():
        print(f"{name}: {n}", file=sys.stderr)


if __name__ == "__main__":
    main()
