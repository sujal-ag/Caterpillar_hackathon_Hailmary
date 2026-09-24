"""Phase 6 gate (pure): generic ETA fallback, unplanned-stop minutes and the task chain
(plan.md §5.6, Phase 6 item 2)."""

from datetime import datetime

import pytest

from common.config import Settings
from common.db.models import Task, TaskType
from edge.ml.eta_service import chain, generic_eta, load_soils, stop_minutes

SOILS = load_soils(Settings().data_dir)
TRUCK_LOAD = TaskType(
    task_type_id="TRUCK_LOAD",
    name="Truck loading",
    unit="M3",
    base_rate_low=140,
    base_rate_high=280,
)
CLEANUP = TaskType(task_type_id="CLEANUP", name="Site cleanup", unit="HOURS")


def T(hhmm: str) -> str:
    return f"2026-10-14T{hhmm}:00.000+05:30"


def task(tid="t1", status="SCHEDULED", start="07:15", end="10:30", **kw) -> Task:
    return Task(
        task_id=tid,
        shift_id="SH",
        machine_id="EXC001",
        operator_id="OP1001",
        task_type_id="TRUCK_LOAD",
        planned_quantity=420,
        unit="M3",
        scheduled_start=T(start),
        scheduled_end=T(end),
        status=status,
        **{"soil_type": "CLAY_WET", **kw},
    )


def test_generic_eta_hand_computed():
    # midpoint (140+280)/2 = 210 m³/h; CLAY_WET fill 0.85 -> 178.5 m³/h; +10 % cycle time
    # p50 = 420 / 178.5 × 60 × 1.10 = 155.29 min; p90 = 1.4 × p50 = 217.41 min
    p = generic_eta(task(), TRUCK_LOAD, SOILS)
    assert p["p50_min"] == 155.3 and p["p90_min"] == 217.4
    assert p["model_version"] == "generic" and p["label"] == "Estimate (generic)"
    assert p["drivers"] == [{"feature": "soil_type=CLAY_WET", "effect_pct": 29.4}]  # 1.1/0.85
    assert generic_eta(task(soil_type=None), TRUCK_LOAD, SOILS)["p50_min"] == 120.0  # 420/210
    assert generic_eta(task(), CLEANUP, SOILS) is None  # time-boxed: no prediction


def D(hhmm):
    return datetime.fromisoformat(T(hhmm))


def test_stop_minutes_union_and_open_stop():
    ev = [(T("08:00"), "IGNITION_OFF"), (T("08:12"), "IGNITION_ON")]
    assert stop_minutes(ev, D("07:15"), D("09:00")) == 12
    # operator out 08:05–08:20 overlaps engine off 08:00–08:12 -> union 08:00–08:20 = 20
    both = sorted([*ev, (T("08:05"), "EXIT_COMPLETED"), (T("08:20"), "MOUNT")])
    assert stop_minutes(both, D("07:15"), D("09:00")) == 20
    # still stopped: counts up to now
    assert stop_minutes([(T("08:50"), "IGNITION_OFF")], D("07:15"), D("09:00")) == 10
    # a stop that began before the task started counts only from the start
    assert (
        stop_minutes(
            [(T("07:00"), "IGNITION_OFF"), (T("07:30"), "IGNITION_ON")], D("07:15"), D("09:00")
        )
        == 15
    )


def test_chain_in_progress_stop_and_next_task():
    t1 = task("t1", "IN_PROGRESS", actual_start=T("07:20"))
    t2 = task("t2", start="08:00", end="10:00")
    preds = {"t1": {"p50_min": 100.0}, "t2": {"p50_min": 60.0}}
    out = chain([t1, t2], preds, {"t1": 12.0}, D("08:30"))
    assert out["t1"]["eta"] == T("09:12")  # 07:20 + 100 + 12
    assert out["t2"]["eta"] == T("10:12")  # max(08:00, 09:12) + 60


def test_done_early_pulls_the_next_task_forward():
    t1 = task("t1", "DONE", actual_start=T("07:15"), actual_end=T("08:10"))
    t2 = task("t2", start="07:30", end="10:00")
    out = chain([t1, t2], {"t2": {"p50_min": 60.0}}, {}, D("08:10"))
    assert out["t1"] is None and out["t2"]["eta"] == T("09:10")


@pytest.mark.parametrize("status", ["DONE", "BACKLOG"])
def test_finished_tasks_have_no_prediction(status):
    assert chain([task(status=status)], {"t1": {"p50_min": 5.0}}, {}, D("08:00"))["t1"] is None
