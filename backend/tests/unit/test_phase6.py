"""Phase 6 gate (API + engine): seeded demo relative to now, lessons (second offence, I5,
Replay), ETA (stub model, fallbacks, unplanned stop, chain), hourly windows + anomaly/R23.
Same harness as test_phase5.py: real app, MemoryBus, fixture-driven clock."""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from common.db.models import LessonAssignment, Task, TelemetryWindow
from common.db.session import create_all, make_engine, sqlite_url
from common.timeutil import SITE_TZ, parse_iso, to_site_iso
from edge.bus import MemoryBus
from edge.main import create_app
from edge.ml import anomaly_job
from tests.engine_support import INITIAL, Run, load
from tests.engine_support import make_engine as make_machine_engine
from tests.unit.test_api import Clock, auth, feed, login, settings, upto, validator
from tests.unit.test_phase5 import outbox, rows, rt_of
from tools.fake_machine import generate
from tools.register_model import register

STUBS = str(Path(__file__).resolve().parents[1] / "stubs")
TASK_V1 = validator("task.v1")
AT_0700 = datetime(2026, 10, 14, 7, 0, tzinfo=SITE_TZ)


def retime(lines, start: datetime):
    delta = start - parse_iso(lines[0]["payload"]["ts"])
    out = []
    for ln in lines:
        ln = json.loads(json.dumps(ln))
        ln["payload"]["ts"] = to_site_iso(parse_iso(ln["payload"]["ts"]) + delta)
        out.append(ln)
    return out


def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        value = pred()
        if value:
            return value
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.01)


def make_app(tmp_path, at: datetime, bus=None, models=(), **over):
    """App seeded at `at`; `models` = [(name, version, json_body)] registered first."""
    db_path = str(tmp_path / "edge.db")
    models_dir = str(tmp_path / "models")
    if models:
        db = make_engine(sqlite_url(db_path))
        create_all(db)
        for name, version, body in models:
            src = tmp_path / f"{name}.json"
            src.write_text(json.dumps(body))
            register(db, models_dir, name, version, src)
    clock = Clock(to_site_iso(at))
    bus = bus or MemoryBus()
    s = settings(tmp_path, media_dir=str(tmp_path / "media"), models_dir=models_dir, **over)
    return create_app(s, bus=bus, clock=clock), bus, clock


def tasks_of(client, token):
    body = client.get("/tasks", headers=auth(token)).json()
    for t in body["tasks"]:
        errors = [e.message for e in TASK_V1.iter_errors(t)]
        assert not errors, errors
    return body["tasks"]


def raw_frame(ts: datetime, machine="EXC001", **sens):
    """One raw.v1 frame (drive_into_zone's first raw line) at `ts`, with `sens` overrides."""
    line = next(ln for ln in load("drive_into_zone") if ln["topic"].endswith("/raw"))
    line = json.loads(json.dumps(line))
    line["topic"] = line["topic"].replace("EXC001", machine)
    line["payload"].update(ts=to_site_iso(ts), machine_id=machine)
    line["payload"]["sens"].update(sens)
    return line


# --- demo data relative to now + lessons + Replay (user request) ---------------------------


def test_seed_at_0030_finds_tasks_and_second_offence(tmp_path):
    at = datetime(2026, 10, 14, 0, 30, tzinfo=SITE_TZ)
    app, bus, clock = make_app(tmp_path, at)
    lines = retime(load("unsafe_exit"), at)
    with TestClient(app) as client:
        op = login(client, "EMP1001", "1234", "EXC001")
        assert client.post("/shift/start", json={}, headers=auth(op)).status_code == 200
        tasks = tasks_of(client, op)
        assert [t["shift_id"] for t in tasks] == ["SH-20261014-EXC001-D"] * 2
        assert [parse_iso(t["scheduled_start"]).astimezone(SITE_TZ).hour for t in tasks] == [7, 13]
        assert all(t["prediction"]["label"] == "Estimate (generic)" for t in tasks)

        with client.websocket_connect(f"/ws/live?machine=EXC001&token={op}") as ws:
            ws.receive_json()  # snapshot
            feed(client, bus, clock, upto(lines, 66))  # the unsafe exit: R03 raised
            rt = rt_of(client)
            (a,) = wait_until(
                lambda: rows(client, LessonAssignment, LessonAssignment.operator_id == "OP1001")
            )
            assert a.reason == "R03 ×2 in 7 days"  # seeded exit 2 days ago + this one
            assert a.lesson_id == "L-SAFE-SHUTDOWN" and a.scenario_id
            assert outbox(client, "lesson_assignment", a.assignment_id)[0].priority == 2

            listed = client.get("/lessons/assigned", headers=auth(op)).json()["assignments"]
            assert [x["deliverable"] for x in listed] == [False]
            assert client.get(f"/lessons/{a.assignment_id}", headers=auth(op)).status_code == 409
            done = {"score": 100, "answers": [0]}
            r = client.post(f"/lessons/{a.assignment_id}/complete", json=done, headers=auth(op))
            assert r.status_code == 409  # I5: server-side, not just UI

            feed(client, bus, clock, [ln for ln in lines if ln not in upto(lines, 66)][:30])
            assert rt.runners["EXC001"].engine.ctx.sig("engine_state") == "OFF"
            for _ in range(500):
                env = ws.receive_json()
                if env["type"] == "lesson" and env["data"]["deliverable"]:
                    break
            else:
                pytest.fail("no WS lesson {deliverable: true} after engine off")
            assert env["data"]["assignment_id"] == a.assignment_id

        body = client.get(f"/lessons/{a.assignment_id}", headers=auth(op)).json()
        replay = body["replay"]
        assert "2.1" in replay["prompt"]  # the real bucket height of *this* exit
        assert "HYD_UNLOCKED" in replay["prompt"] and "IMPLEMENT_RAISED" in replay["prompt"]
        assert replay["state_vector"]["bucket_height_m"] == 2.1
        assert body["assignment"]["started_at"]
        r = client.post(f"/lessons/{a.assignment_id}/complete", json=done, headers=auth(op))
        assert r.status_code == 200 and r.json()["assignment"]["score"] == 100


def test_seed_is_idempotent_and_history_not_duplicated(tmp_path):
    from tools import seed

    db = make_engine(sqlite_url(str(tmp_path / "s.db")))
    now = datetime(2026, 10, 14, 0, 30, tzinfo=SITE_TZ)
    assert seed.run(db, now) == seed.run(db, now)


# --- ETA ---------------------------------------------------------------------------------

ETA_STUB = ("eta", "stub-eta-1", {"version": "stub-eta-1", "factor": 1.0})


def start_shift(client):
    op = login(client, "EMP1001", "1234", "EXC001")
    assert client.post("/shift/start", json={}, headers=auth(op)).status_code == 200
    return op


def test_eta_with_stub_model(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(STUBS)
    app, *_ = make_app(tmp_path, AT_0700, models=[ETA_STUB])
    with TestClient(app) as client:
        op = start_shift(client)
        t1, _ = tasks_of(client, op)
        p = t1["prediction"]
        assert p["model_version"] == "stub-eta-1" and len(p["drivers"]) == 3
        assert p["p90_min"] > p["p50_min"] > 0
        assert p["operator_rate_source"] == "TASK_TYPE" and p["label"] == "Estimate (generic)"
        eta = client.get(f"/tasks/{t1['task_id']}/eta", headers=auth(op)).json()
        assert eta["eta"] == p["eta"] and eta["as_of"]
        status = client.get("/system/status", headers=auth(op)).json()
        assert status["models"]["eta"] == "LOCAL"
        assert status["model_detail"]["eta"] == "stub-eta-1"


def test_operator_history_gives_personal_rate(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(STUBS)
    app, *_ = make_app(tmp_path, AT_0700, models=[ETA_STUB])
    with TestClient(app) as client:
        rt = rt_of(client)
        sid = "SH-20261014-EXC001-D"

        def history(session):
            for i in range(3):  # 3 finished tasks: 420 m³ in 2 h = 210 m³/h
                start = AT_0700 - timedelta(days=i + 1)
                session.add(
                    Task(
                        task_id=f"HIST-{i}",
                        shift_id=sid,
                        machine_id="EXC001",
                        operator_id="OP1001",
                        task_type_id="TRUCK_LOAD",
                        planned_quantity=420,
                        unit="M3",
                        scheduled_start=to_site_iso(start),
                        scheduled_end=to_site_iso(start + timedelta(hours=2)),
                        status="DONE",
                        actual_start=to_site_iso(start),
                        actual_end=to_site_iso(start + timedelta(hours=2)),
                        actual_quantity=420,
                    )
                )

        client.portal.call(rt.write, history)
        op = start_shift(client)
        t1 = next(t for t in tasks_of(client, op) if t["status"] == "SCHEDULED")
        assert t1["prediction"]["operator_rate_source"] == "OPERATOR"
        assert t1["prediction"]["label"] is None
        # stub: 420 / (210 × 0.85 CLAY_WET fill) × 60 = 141.2 min
        assert t1["prediction"]["p50_min"] == pytest.approx(141.2, abs=0.1)


@pytest.mark.parametrize("breakage", ["file_deleted", "sha_tampered", "bad_prediction"])
def test_eta_falls_back_to_generic(tmp_path, monkeypatch, breakage):
    monkeypatch.syspath_prepend(STUBS)
    body = {"version": "stub-eta-1", "factor": -1.0 if breakage == "bad_prediction" else 1.0}
    app, *_ = make_app(tmp_path, AT_0700, models=[("eta", "stub-eta-1", body)])
    path = tmp_path / "models" / "eta" / "stub-eta-1"
    if breakage == "file_deleted":
        path.unlink()
    elif breakage == "sha_tampered":
        path.write_text(json.dumps({"version": "evil", "factor": 1.0}))
    with TestClient(app) as client:
        op = start_shift(client)
        p = tasks_of(client, op)[0]["prediction"]  # no 500, labelled fallback
        assert p["model_version"] == "generic" and p["label"] == "Estimate (generic)"
        assert p["p50_min"] == 155.3  # generic TRUCK_LOAD 420 m³ CLAY_WET (test_eta.py)
        status = client.get("/system/status", headers=auth(op)).json()
        if breakage == "bad_prediction":
            assert status["models"]["eta"] == "LOCAL"  # loaded; the bad output is refused
        else:
            assert status["models"]["eta"] == "UNAVAILABLE"
            assert status["model_detail"]["errors"]["eta"]


def test_unplanned_stop_moves_eta_by_its_length(tmp_path):
    app, bus, clock = make_app(tmp_path, AT_0700)
    with TestClient(app) as client:
        feed(client, bus, clock, [raw_frame(AT_0700, engine_state="RUNNING")])
        op = start_shift(client)
        t1, t2 = tasks_of(client, op)
        with client.websocket_connect(f"/ws/live?machine=EXC001&token={op}") as ws:
            ws.receive_json()
            r = client.post(
                f"/tasks/{t1['task_id']}/status", json={"status": "IN_PROGRESS"}, headers=auth(op)
            )
            before = parse_iso(r.json()["prediction"]["eta"])
            off, on = AT_0700 + timedelta(minutes=1), AT_0700 + timedelta(minutes=13)
            feed(client, bus, clock, [raw_frame(off, engine_state="OFF")])
            feed(client, bus, clock, [raw_frame(on, engine_state="RUNNING")])

            def moved():
                p = client.get(f"/tasks/{t1['task_id']}/eta", headers=auth(op)).json()
                return parse_iso(p["eta"]) if parse_iso(p["eta"]) != before else None

            after = wait_until(moved)
            assert (after - before).total_seconds() / 60 == pytest.approx(12, abs=1)
            for _ in range(500):
                env = ws.receive_json()
                if env["type"] == "eta" and env["data"]["task_id"] == t1["task_id"]:
                    break
            else:
                pytest.fail("no WS eta message")


def test_task_done_early_shifts_the_next_task(tmp_path):
    app, bus, clock = make_app(tmp_path, AT_0700)
    with TestClient(app) as client:
        op = start_shift(client)
        t1, t2 = tasks_of(client, op)

        def squeeze(session):  # task 2 right after task 1, so the chain matters
            t = session.get(Task, t2["task_id"])
            t.scheduled_start = to_site_iso(AT_0700 + timedelta(minutes=30))
            session.add(t)

        client.portal.call(rt_of(client).write, squeeze)
        client.post(
            f"/tasks/{t1['task_id']}/status", json={"status": "IN_PROGRESS"}, headers=auth(op)
        )
        before = tasks_of(client, op)[1]["prediction"]["eta"]
        clock.now = AT_0700 + timedelta(minutes=20)
        r = client.post(
            f"/tasks/{t1['task_id']}/status",
            json={"status": "DONE", "actual_quantity": 420},
            headers=auth(op),
        )
        assert r.json()["prediction"] is None and r.json()["actual_end"]
        after = tasks_of(client, op)[1]["prediction"]["eta"]
        assert parse_iso(after) < parse_iso(before)  # 07:30 + p50 instead of after task 1
        # task 2 = 380 m³ CLAY_WET: 380 / 178.5 × 60 × 1.1 = 140.5 min, from its 07:30 slot
        assert parse_iso(after) == AT_0700 + timedelta(minutes=30 + 140.5)
        assert outbox(client, "task", t1["task_id"])


# --- hourly windows + anomaly (R23) -----------------------------------------------------


def test_hourly_window_closes_with_alert_counts():
    spec = {
        "site_id": "SITE-PUN-01",
        "machine_id": "EXC001",
        "start": "2026-10-14T07:59:30.000+05:30",
        "duration_s": 60,
        "initial": dict(INITIAL),
        "timeline": [{"at": 0, "mode": "IDLE"}],
    }
    lines = [ln for ln in generate(spec) if ln["topic"].endswith("/raw")]
    run = Run(make_machine_engine(), lines)
    windows = [w for _, out, _ in run.steps for w in out.windows]
    assert len(windows) == 1
    w = windows[0]
    assert w["window_start"].startswith("2026-10-14T07:00") and w["window_end"].startswith(
        "2026-10-14T08:00"
    )
    assert isinstance(w["alert_count"], int) and w["window_id"]
    assert any(out.minutes for _, out, _ in run.steps)


ANOMALY_STUB = (
    "anomaly_EXCAVATOR",
    "stub-if-1",
    {"version": "stub-if-1", "family": "EXCAVATOR", "threshold_top3pct": 0.8},
)


def window(start: datetime, fuel: float, **over) -> TelemetryWindow:
    return TelemetryWindow(
        **{
            "window_id": f"W-{start:%m%d%H}",
            "window_start": to_site_iso(start),
            "window_end": to_site_iso(start + timedelta(hours=1)),
            "machine_id": "EXC001",
            "operator_id": "OP1001",
            "fuel_per_productive_h": fuel,
            "idle_ratio": 0.2,
            "productive_min": 48,
            "pass_count": 150,
            "alert_count": 0,
            **over,
        }
    )


def test_anomalous_window_raises_r23_silently_and_is_queued(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(STUBS)
    app, bus, clock = make_app(tmp_path, AT_0700, models=[ANOMALY_STUB])
    with TestClient(app) as client:
        rt = rt_of(client)
        base = [8.0, 8.4, 7.6, 8.2, 7.8, 8.1]  # the operator's normal L/productive h

        def insert(session):
            for i, fuel in enumerate(base):
                session.add(window(AT_0700 - timedelta(days=i + 1), fuel))
            session.add(window(AT_0700 - timedelta(hours=1), 13.0))  # ~1.6× normal

        client.portal.call(rt.write, insert)
        wid = f"W-{AT_0700 - timedelta(hours=1):%m%d%H}"
        stored = client.portal.call(rt.score_window, "EXC001", wid)
        assert stored["anomaly_flagged"] is True and stored["anomaly_score"] >= 0.8
        assert stored["anomaly_top_features"][0]["feature"] == "fuel_per_productive_h"
        (r23,) = [
            a for a in rt.runners["EXC001"].engine.alerts.active_records() if a["rule_id"] == "R23"
        ]
        assert r23["level"] == "INFO" and "AUDIO" not in r23["channels"]  # HLD: no audio
        with Session(rt.db) as s:
            assert [w.window_id for w in anomaly_job.review_queue(s)] == [wid]
        assert [q.priority for q in outbox(client, "telemetry_window", wid)] == [2]

        # a live hour boundary: the window is persisted and scored with no manual call
        feed(client, bus, clock, [raw_frame(AT_0700 + timedelta(minutes=59, seconds=59))])
        feed(client, bus, clock, [raw_frame(AT_0700 + timedelta(hours=1, seconds=1))])
        live = wait_until(
            lambda: [
                w
                for w in rows(
                    client, TelemetryWindow, TelemetryWindow.window_start == to_site_iso(AT_0700)
                )
                if w.anomaly_score is not None
            ]
        )
        assert live[0].alert_count is not None


def test_no_anomaly_model_scores_nothing(tmp_path):
    app, *_ = make_app(tmp_path, AT_0700)
    with TestClient(app) as client:
        rt = rt_of(client)
        client.portal.call(rt.write, lambda s: s.add(window(AT_0700, 30.0)))
        assert client.portal.call(rt.score_window, "EXC001", f"W-{AT_0700:%m%d%H}") is None
        (w,) = rows(client, TelemetryWindow)
        assert w.anomaly_score is None
        assert rt.runners["EXC001"].engine.ctx.anomaly is None  # R23 stays UNKNOWN
        assert (
            client.get("/system/status", headers=auth(login(client, "SUP001", "9999"))).json()[
                "models"
            ]["anomaly"]
            == "UNAVAILABLE"
        )
