"""ETA feature dict (HLD §7.2; contracts/ml_features.md — names and units are frozen, D15).

Built from SQLite at inference time. `operator_rate_median` follows the cold-start ladder
(HLD §7.2): the operator's last 10 finished tasks of this type (>= 3 needed) -> the same
experience level's tasks -> the task-type base-rate midpoint; `operator_rate_source` says
which one was used.
"""

import statistics
from datetime import datetime

from sqlmodel import Session, select

from common.db.models import Attachment, Machine, Operator, ReadinessCheck, Shift, Task, TaskType
from common.timeutil import SITE_TZ, to_site_iso

LAST_N = 10  # HLD §7.2: rolling median over the last 10 tasks of the type
MIN_TASKS = 3  # HLD §7.2 cold start: < 3 tasks -> cohort


def fill_factor(soils: dict, soil_type: str | None) -> tuple[float, float]:
    """(fill factor midpoint, cycle-time factor) for a task.v1 soil type (HLD §6.7)."""
    s = soils.get(soil_type or "")
    if s is None:
        return 1.0, 1.0
    return (s["fill_factor_low"] + s["fill_factor_high"]) / 2, s.get("cycle_time_factor", 1.0)


def task_rate(t: Task) -> float | None:
    """Achieved rate in `unit`/h for a finished task, or None if it can't be measured."""
    if not (t.actual_start and t.actual_end and t.actual_quantity):
        return None
    hours = (
        datetime.fromisoformat(t.actual_end) - datetime.fromisoformat(t.actual_start)
    ).total_seconds() / 3600
    return t.actual_quantity / hours if hours > 0 else None


def _median_rate(session: Session, where) -> float | None:
    rows = session.exec(
        select(Task).where(Task.status == "DONE", *where).order_by(Task.actual_end.desc())
    ).all()
    rates = [r for r in (task_rate(t) for t in rows) if r is not None][:LAST_N]
    return statistics.median(rates) if len(rates) >= MIN_TASKS else None


def operator_rate(session: Session, task: Task, op: Operator | None, tt: TaskType):
    same_type = Task.task_type_id == task.task_type_id
    rate = _median_rate(session, [same_type, Task.operator_id == task.operator_id])
    if rate is not None:
        return rate, "OPERATOR"
    if op is not None and op.experience_level:
        cohort = select(Operator.operator_id).where(
            Operator.experience_level == op.experience_level
        )
        rate = _median_rate(session, [same_type, Task.operator_id.in_(cohort)])
        if rate is not None:
            return rate, "COHORT"
    if tt.base_rate_low is not None and tt.base_rate_high is not None:
        return (tt.base_rate_low + tt.base_rate_high) / 2, "TASK_TYPE"
    return None, "TASK_TYPE"


def eta_features(
    session: Session, task: Task, soils: dict, env: dict | None, power_mode, now: datetime
) -> dict:
    tt = session.get(TaskType, task.task_type_id)
    op = session.get(Operator, task.operator_id)
    machine = session.get(Machine, task.machine_id)
    att = session.get(Attachment, machine.current_attachment_id) if machine else None
    shift = session.get(Shift, task.shift_id)
    rate, source = operator_rate(session, task, op, tt)
    ff, _ = fill_factor(soils, task.soil_type)
    midnight = datetime.combine(now.astimezone(SITE_TZ).date(), datetime.min.time(), SITE_TZ)
    check = session.exec(
        select(ReadinessCheck)
        .where(
            ReadinessCheck.operator_id == task.operator_id,
            ReadinessCheck.ts >= to_site_iso(midnight),
        )
        .order_by(ReadinessCheck.ts.desc())
    ).first()
    start = datetime.fromisoformat(task.actual_start or task.scheduled_start).astimezone(SITE_TZ)
    shift_start = shift and (shift.actual_start or shift.planned_start)
    env = env or {}
    return {
        "task_type": task.task_type_id,
        "planned_quantity": task.planned_quantity,
        "unit": task.unit,
        "soil_type": task.soil_type,
        "fill_factor": ff,
        "material_density_t_m3": task.material_density_t_m3,
        "haul_distance_m": task.haul_distance_m,
        "truck_capacity_total_t": (task.trucks_assigned or 0) * (task.truck_capacity_t or 0)
        if task.trucks_assigned and task.truck_capacity_t
        else None,
        "machine_model": machine.model_id if machine else None,
        "attachment_capacity_m3": att.capacity_m3 if att else None,
        "power_mode": power_mode,
        "operator_rate_median": rate,
        "operator_rate_source": source,
        "experience_years": op.experience_years if op else None,
        "readiness_score": check.score if check else None,
        "rain_mm_h": env.get("precip_mm_h"),
        "temp_c": env.get("temp_c"),
        "heat_index_c": env.get("heat_index_c"),
        "start_hour": start.hour,
        "hours_into_shift": (start - datetime.fromisoformat(shift_start)).total_seconds() / 3600
        if shift_start
        else None,
        "day_of_week": start.weekday(),
    }
