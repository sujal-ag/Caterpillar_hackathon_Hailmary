"""Task ETAs (HLD §4.6, §7.2; plan.md §5.6, Phase 6 item 2). No live progress (HLD decision):
ETAs are re-estimated only when an input changes — shift start, a task status change,
rain starting/stopping, a readiness update, or an unplanned stop.

Per machine, over its current shift's tasks in scheduled order:
- DONE / BACKLOG tasks are not predicted; a DONE task's `actual_end` feeds the chain.
- IN_PROGRESS: eta = actual_start + p50 + unplanned-stop minutes since actual_start.
- next tasks: eta_i = max(scheduled_start_i, eta_{i-1}) + p50_i.
p50/p90 come from P1's model through the adapter, else `generic_eta` (labelled).

Unplanned stop = union of engine-off (IGNITION_OFF -> ON) and operator-out
(EXIT_COMPLETED -> MOUNT) intervals; an open stop counts up to now.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from sqlmodel import Session, select

from common.db import repo
from common.db.models import MachineEvent, Task, TaskType
from common.log import jlog
from common.shifts import current_shift
from common.timeutil import SITE_TZ, to_site_iso
from edge.ml.features import eta_features, fill_factor

log = logging.getLogger("edge.ml.eta")
P90_FACTOR = 1.4  # plan.md §5.6 generic fallback: p90 = 1.4 × p50
LABELS = {"COHORT": "Estimate (cohort)", "TASK_TYPE": "Estimate (generic)"}
STOP_EVENTS = {"IGNITION_OFF", "IGNITION_ON", "EXIT_COMPLETED", "MOUNT"}
NOT_PREDICTED = {"DONE", "BACKLOG"}


def load_soils(data_dir: Path) -> dict:
    return yaml.safe_load((data_dir / "seed" / "soil_types.yaml").read_text())["soils"]


def generic_eta(task: Task, tt: TaskType, soils: dict) -> dict | None:
    """plan.md §5.6: task-type base-rate midpoint × soil fill factor; p90 = 1.4 × p50.
    None for task types with no rate (CLEANUP: time-boxed, HLD §6.7 "no prediction")."""
    if tt.base_rate_low is None or tt.base_rate_high is None:
        return None
    ff, ctf = fill_factor(soils, task.soil_type)
    rate = (tt.base_rate_low + tt.base_rate_high) / 2 * ff
    p50 = task.planned_quantity / rate * 60 * ctf
    drivers = []
    if task.soil_type and (ff, ctf) != (1.0, 1.0):
        drivers.append(
            {"feature": f"soil_type={task.soil_type}", "effect_pct": round((ctf / ff - 1) * 100, 1)}
        )
    return {
        "p50_min": round(p50, 1),
        "p90_min": round(P90_FACTOR * p50, 1),
        "drivers": drivers,
        "model_version": "generic",
        "label": "Estimate (generic)",
        "operator_rate_source": "TASK_TYPE",
    }


def predict(adapter, feats: dict, task: Task, tt: TaskType, soils: dict) -> dict | None:
    model = adapter.predict_eta(feats) if feats["operator_rate_median"] else None
    if model is None:
        return generic_eta(task, tt, soils)
    source = feats["operator_rate_source"]
    return {
        **model,
        "p50_min": round(model["p50_min"], 1),
        "p90_min": round(model["p90_min"], 1),
        "label": LABELS.get(source),
        "operator_rate_source": source,
    }


def stop_minutes(events: list[tuple[str, str]], since: datetime, now: datetime) -> float:
    """Minutes stopped (engine off OR operator out) in [since, now]. `events` = sorted
    (ts, type); state before `since` is taken from the events themselves."""
    off = out = False
    stopped_at: datetime | None = None
    total = 0.0
    for ts, type_ in events:
        t = max(datetime.fromisoformat(ts), since)
        was = off or out
        off = {"IGNITION_OFF": True, "IGNITION_ON": False}.get(type_, off)
        out = {"EXIT_COMPLETED": True, "MOUNT": False}.get(type_, out)
        if not was and (off or out):
            stopped_at = t
        elif was and not (off or out) and stopped_at is not None:
            total += (t - stopped_at).total_seconds()
            stopped_at = None
    if stopped_at is not None:
        total += max((now - stopped_at).total_seconds(), 0)
    return total / 60


def chain(tasks: list[Task], preds: dict, stops: dict, now: datetime) -> dict:
    """task_id -> prediction dict with `eta` (or None)."""
    out, prev = {}, None
    for t in tasks:
        if t.status in NOT_PREDICTED:
            out[t.task_id] = None
            if t.status == "DONE" and t.actual_end:
                prev = datetime.fromisoformat(t.actual_end)
            continue
        p = preds.get(t.task_id)
        if p is None:
            out[t.task_id] = None
            prev = datetime.fromisoformat(t.scheduled_end)  # time-boxed task keeps its slot
            continue
        if t.status == "IN_PROGRESS" and t.actual_start:
            start = datetime.fromisoformat(t.actual_start)
            eta = start + timedelta(minutes=p["p50_min"] + stops.get(t.task_id, 0.0))
        else:
            start = datetime.fromisoformat(t.scheduled_start)
            if prev is not None:
                start = max(start, prev)
            eta = start + timedelta(minutes=p["p50_min"])
        out[t.task_id] = {**p, "eta": to_site_iso(eta)}
        prev = eta
    return out


def prediction_of(t: Task) -> dict | None:
    if t.pred_eta is None:
        return None
    return {
        "p50_min": t.pred_duration_p50_min,
        "p90_min": t.pred_duration_p90_min,
        "eta": t.pred_eta,
        "drivers": t.pred_drivers or [],
        "model_version": t.model_version,
        "label": t.pred_label,
        "operator_rate_source": t.pred_rate_source,
    }


def task_v1(t: Task) -> dict:
    d = t.model_dump(
        exclude={
            "pred_duration_p50_min",
            "pred_duration_p90_min",
            "pred_eta",
            "pred_drivers",
            "model_version",
            "pred_label",
            "pred_rate_source",
        }
    )
    return {"schema": "task.v1", **d, "prediction": prediction_of(t)}


def shift_tasks(session: Session, machine_id: str, now: datetime) -> list[Task]:
    shift = current_shift(session, machine_id, now, SITE_TZ)
    if shift is None:
        return []
    return list(
        session.exec(
            select(Task).where(Task.shift_id == shift.shift_id).order_by(Task.scheduled_start)
        ).all()
    )


def _compute(rt, machine_id: str, now: datetime, env, power_mode) -> dict:
    with Session(rt.db) as session:
        tasks = shift_tasks(session, machine_id, now)
        preds, stops = {}, {}
        for t in tasks:
            if t.status in NOT_PREDICTED:
                continue
            tt = session.get(TaskType, t.task_type_id)
            feats = eta_features(session, t, rt.soils, env, power_mode, now)
            preds[t.task_id] = predict(rt.ml, feats, t, tt, rt.soils)
            if t.status == "IN_PROGRESS" and t.actual_start:
                rows = session.exec(
                    select(MachineEvent.ts, MachineEvent.type)
                    .where(
                        MachineEvent.machine_id == machine_id,
                        MachineEvent.type.in_(STOP_EVENTS),
                    )
                    .order_by(MachineEvent.ts)
                ).all()
                stops[t.task_id] = stop_minutes(
                    [(ts, ty) for ts, ty in rows if ts <= to_site_iso(now)],
                    datetime.fromisoformat(t.actual_start),
                    now,
                )
        return {"tasks": [t.task_id for t in tasks], "result": chain(tasks, preds, stops, now)}


def _store(session: Session, result: dict) -> list[tuple[str, dict | None]]:
    changed = []
    for task_id, p in result.items():
        t = session.get(Task, task_id)
        new = (
            (None,) * 7
            if p is None
            else (
                p["p50_min"],
                p["p90_min"],
                p["eta"],
                p["drivers"],
                p["model_version"],
                p.get("label"),
                p.get("operator_rate_source"),
            )
        )
        old = (
            t.pred_duration_p50_min,
            t.pred_duration_p90_min,
            t.pred_eta,
            t.pred_drivers,
            t.model_version,
            t.pred_label,
            t.pred_rate_source,
        )
        if new == old:
            continue
        (
            t.pred_duration_p50_min,
            t.pred_duration_p90_min,
            t.pred_eta,
            t.pred_drivers,
            t.model_version,
            t.pred_label,
            t.pred_rate_source,
        ) = new
        session.add(t)
        repo.enqueue_sync(session, "task", task_id, "UPSERT", t.model_dump())
        changed.append((task_id, prediction_of(t)))
    return changed


async def recompute(rt, machine_id: str, reason: str) -> list[tuple[str, dict | None]]:
    """Re-estimate one machine's shift; push WS `eta` for every prediction that changed."""
    engine = rt.runners[machine_id].engine
    now, env, power_mode = rt.clock(), engine.ctx.env, engine.ctx.sig("power_mode")
    computed = await asyncio.to_thread(_compute, rt, machine_id, now, env, power_mode)
    changed = await rt.write(lambda s: _store(s, computed["result"]))
    for task_id, pred in changed:
        await rt.sink.publish(
            machine_id, "eta", {"ts": to_site_iso(now), "task_id": task_id, "prediction": pred}
        )
    if changed:
        jlog(
            log,
            logging.INFO,
            "eta_updated",
            machine_id=machine_id,
            reason=reason,
            tasks=len(changed),
        )
    return changed


def current_eta(rt, machine_id: str) -> dict | None:
    """{task_id, prediction} for the snapshot: the IN_PROGRESS task, else the next one."""
    with Session(rt.db) as session:
        tasks = shift_tasks(session, machine_id, rt.clock())
    for status in ("IN_PROGRESS", "SCHEDULED", "PAUSED"):
        for t in tasks:
            if t.status == status:
                return {"task_id": t.task_id, "prediction": prediction_of(t)}
    return None
