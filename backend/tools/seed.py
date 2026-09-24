"""Load data/seed/*.yaml and data/catalogue/diagnostic_codes.yaml into the edge DB.

Idempotent: every row is `session.merge()`d (upsert by primary key), so running this twice
gives identical row counts and field values (plan.md Phase 1 gate). Writes go straight
through a session, not the outbox — master data is CFG, not something the edge pushes to
the cloud as a delta (Phase 9's sync *pulls* roster/tasks from the cloud instead).

`assumptions` in a seed file is metadata for reviewers (plan.md §0 rule 5); it is dropped
before constructing a row except on MachineModel and TaskType, which have a real column for
it (rules.yaml and the ETA model may want to know a threshold is unconfirmed).
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import bcrypt
import yaml
from sqlmodel import Session, func, select

from common.config import Settings
from common.db.models import (
    Attachment,
    DiagnosticCode,
    Machine,
    MachineModel,
    Operator,
    Shift,
    Site,
    Task,
    TaskType,
)
from common.db.session import create_all, get_engine
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


def seed_shifts_and_demo_tasks(session: Session, site_tz: ZoneInfo) -> None:
    """Today's PLANNED shifts from shifts_demo.yaml (window = SHIFT_START/SHIFT_END), plus
    the demo tasks, which reference their machine's shift."""
    settings = Settings()
    tasks_raw = _load(SEED_DIR, "tasks_demo.yaml")
    today = datetime.now(site_tz).date()
    midnight = datetime.combine(today, datetime.min.time(), tzinfo=site_tz)
    start, end = planned_window(today, settings.shift_start, settings.shift_end, site_tz)

    # Insert-if-absent, not merge: EDGE_SEED_ON_START re-seeds on every container start and
    # must not reset a shift that is already ACTIVE/CLOSED (delete the DB to reset the demo).
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

    for row in tasks_raw:
        row = dict(row)
        suffix = row.pop("task_id_suffix")
        start_off, end_off = (
            row.pop("scheduled_start_offset_min"),
            row.pop("scheduled_end_offset_min"),
        )
        row["task_id"] = f"TASK-DEMO-{row['machine_id']}-{suffix}"
        row["shift_id"] = shift_id_for(row["machine_id"], today)
        row["scheduled_start"] = to_site_iso(midnight + timedelta(minutes=start_off))
        row["scheduled_end"] = to_site_iso(midnight + timedelta(minutes=end_off))
        session.merge(Task(**row))


TABLES = [Site, MachineModel, Attachment, Machine, Operator, TaskType, DiagnosticCode, Shift, Task]


def run(engine=None) -> dict[str, int]:
    engine = engine or get_engine()
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
        seed_shifts_and_demo_tasks(session, site_tz)
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
