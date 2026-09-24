"""Micro-lessons + Replay (HLD §4.11; plan.md Phase 6 items 4–5).

Assign when a `lesson_trigger` rule is RAISED for an operator and it is CRITICAL or the same
rule fired ≥ 2× in 7 days for that operator (the new alert is persisted before it's
published, so it counts). One open assignment per (operator, lesson): a repeat only updates
its reason. Lesson choice: one whose `trigger_rule_ids` has the rule (machine family or ANY),
else the rule subject's generic lesson, else nothing (logged).

Replay: an R03 raise (unsafe exit) becomes a `scenario` whose state vector is that alert's
exact inputs; P3's `replay_templates[R03]` placeholders are filled from it. Text only ever
comes from P3's template, never generated.

Deliverable (I5, server-side): never while the machine is running. The operator's ACTIVE
shift machine must report engine OFF; with no ACTIVE shift (shift ended) it is deliverable.
Unknown or stale engine state = not deliverable.
"""

import json
import logging
import string
from datetime import datetime, timedelta

from jsonschema import Draft202012Validator
from sqlmodel import Session, func, select

from common.db import repo
from common.db.models import Lesson, LessonAssignment, SafetyAlert, Scenario, Shift
from common.ids import uuid7
from common.log import jlog
from common.timeutil import to_site_iso

log = logging.getLogger("edge.lessons")
REPEAT_WINDOW = timedelta(days=7)  # HLD §4.11: same rule ≥ 2× in 7 days
REPEAT_MIN = 2


def load_templates(path, schema_path) -> dict:
    """replay_templates from P3's lesson.v1 catalogue; {} (Replay off) if missing/invalid."""
    try:
        doc = json.loads(path.read_text())
        errors = list(Draft202012Validator(json.loads(schema_path.read_text())).iter_errors(doc))
        if errors:
            raise ValueError(errors[0].message)
    except (OSError, ValueError) as exc:
        jlog(log, logging.ERROR, "lesson_catalogue_invalid", path=str(path), error=repr(exc))
        return {}
    return doc["replay_templates"]


class _Keep(dict):
    def __missing__(self, key):
        return "{" + key + "}"  # unknown placeholder stays visible, never guessed


def fill(template: str, values: dict) -> str:
    safe = {k: (", ".join(map(str, v)) if isinstance(v, list) else v) for k, v in values.items()}
    return string.Formatter().vformat(template, (), _Keep(safe))


def pick_lesson(session: Session, rule, family: str) -> Lesson | None:
    lessons = session.exec(select(Lesson)).all()
    for les in lessons:
        if rule.id in (les.trigger_rule_ids or []) and les.machine_family in (family, "ANY"):
            return les
    return next((les for les in lessons if les.generic_for_subject == rule.subject), None)


def on_alert_raised(
    session: Session, alert: dict, rule, family: str, templates: dict, now: datetime
) -> dict | None:
    """Unit of work for one RAISED alert. Returns the assignment dict, or None."""
    op = alert.get("operator_id")
    if not rule.lesson_trigger or op is None:
        return None
    count = session.exec(
        select(func.count())
        .select_from(SafetyAlert)
        .where(
            SafetyAlert.operator_id == op,
            SafetyAlert.rule_id == rule.id,
            SafetyAlert.ts >= to_site_iso(now - REPEAT_WINDOW),
        )
    ).one()
    if rule.level != "CRITICAL" and count < REPEAT_MIN:
        return None
    lesson = pick_lesson(session, rule, family)
    if lesson is None:
        jlog(log, logging.WARNING, "no_lesson_for_rule", rule_id=rule.id, family=family)
        return None
    reason = f"{rule.id} ×{count} in 7 days" if count >= REPEAT_MIN else f"{rule.id} critical"

    scenario_id = None
    tpl = templates.get(rule.id)
    if tpl is not None:
        snap = alert.get("state_snapshot") or {}
        vector = {**snap.get("inputs", {}), **(alert.get("slots") or {})}
        scenario = Scenario(
            scenario_id=uuid7(),
            source_incident_id=None,
            state_vector=vector,
            prompt=fill(tpl["prompt"], vector),
            options=[fill(o, vector) for o in tpl["options"]],
            correct_option=tpl["correct_option"],
            explanation=fill(tpl["explanation"], vector),
            doc_reference=tpl.get("doc_reference"),
        )
        session.add(scenario)
        scenario_id = scenario.scenario_id

    row = session.exec(
        select(LessonAssignment).where(
            LessonAssignment.operator_id == op,
            LessonAssignment.lesson_id == lesson.lesson_id,
            LessonAssignment.completed_at == None,  # noqa: E711 - SQL expression
        )
    ).first()
    if row is None:
        row = LessonAssignment(
            assignment_id=uuid7(),
            operator_id=op,
            lesson_id=lesson.lesson_id,
            assigned_at=to_site_iso(now),
        )
    row.trigger_event_id, row.reason = alert["alert_id"], reason
    row.scenario_id = scenario_id or row.scenario_id
    session.add(row)
    data = row.model_dump()
    repo.enqueue_sync(session, "lesson_assignment", row.assignment_id, "UPSERT", data)  # P2
    return data


def machine_of(session: Session, operator_id: str) -> str | None:
    shift = session.exec(
        select(Shift).where(Shift.operator_id == operator_id, Shift.status == "ACTIVE")
    ).first()
    return shift.machine_id if shift else None


def deliverable(rt, machine_id: str | None) -> bool:
    """I5: only when the operator's machine is known OFF, or their shift has ended."""
    if machine_id is None:
        return True
    runner = rt.runners.get(machine_id)
    return runner is not None and runner.engine.ctx.sig("engine_state") == "OFF"
