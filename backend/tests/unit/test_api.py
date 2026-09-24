"""Phase 4 gate (unit level): REST + WebSocket bridge + nudges on the real app, with the
in-memory bus and a clock driven by fixture timestamps (plan.md Phase 4 verification)."""

import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlmodel import Session, select
from starlette.websockets import WebSocketDisconnect

from common.config import Settings
from common.db.models import EnvironmentObs, SafetyAlert
from common.timeutil import parse_iso
from edge.bus import MemoryBus
from edge.main import create_app
from tests.engine_support import SCENARIOS, load

SCHEMAS = Path(__file__).resolve().parents[3] / "contracts" / "schemas"
SECRET = "test-secret-" + "x" * 32


def validator(name):
    return Draft202012Validator(json.loads((SCHEMAS / f"{name}.json").read_text()))


V = {
    n: validator(n)
    for n in (
        "ws_envelope.v1",
        "state.v1",
        "alert.v1",
        "nudge.v1",
        "telemetry.v1",
        "event.v1",
        "hazards.v1",
        "sync_status.v1",
    )
}


def valid(schema: str, payload: dict) -> bool:
    errors = [e.message for e in V[schema].iter_errors(payload)]
    assert not errors, (schema, errors)
    return True


class Clock:
    def __init__(self, ts: str):
        self.now = parse_iso(ts)

    def __call__(self):
        return self.now


def settings(tmp_path, **over) -> Settings:
    base = dict(
        edge_db_path=str(tmp_path / "edge.db"),
        edge_seed_on_start=True,
        edge_jwt_secret=SECRET,
        sim_mode="replay",
        engine_tick_s=3600,  # deterministic: the fixture clock drives every evaluation
        sync_status_interval_s=3600,
    )
    return Settings(**{**base, **over})


@pytest.fixture
def app_env(tmp_path):
    lines = load("unsafe_exit")
    bus, clock = MemoryBus(), Clock(lines[0]["payload"]["ts"])
    app = create_app(settings(tmp_path), bus=bus, clock=clock)
    with TestClient(app) as client:
        yield client, bus, clock, lines


def login(client, badge, pin, machine=None) -> str:
    r = client.post("/auth/login", json={"badge_id": badge, "pin": pin, "machine_id": machine})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def feed(client, bus, clock, lines):
    """Publish fixture lines onto the in-memory bus, one at a time, waiting until the
    machine's runner has processed each one."""
    rt = client.app.state.rt
    for line in lines:
        clock.now = parse_iso(line["payload"]["ts"])
        runner = rt.runners[line["topic"].split("/")[2]] if "/env" not in line["topic"] else None
        runners = [runner] if runner else list(rt.runners.values())
        before = [r.handled for r in runners]
        client.portal.call(bus.publish, line["topic"], line["payload"])
        deadline = time.monotonic() + 5
        while any(r.handled == b for r, b in zip(runners, before, strict=True)):
            assert time.monotonic() < deadline, f"runner stuck on {line['topic']}"
            time.sleep(0.001)


def upto(lines, clock_s: float):
    t0 = parse_iso(lines[0]["payload"]["ts"])
    return [ln for ln in lines if (parse_iso(ln["payload"]["ts"]) - t0).total_seconds() <= clock_s]


def after(lines, clock_s: float):
    t0 = parse_iso(lines[0]["payload"]["ts"])
    return [ln for ln in lines if (parse_iso(ln["payload"]["ts"]) - t0).total_seconds() > clock_s]


def receive_until(ws, pred, limit=500):
    seen = []
    for _ in range(limit):
        env = ws.receive_json()
        valid("ws_envelope.v1", env)
        seen.append(env)
        if pred(env):
            return env, seen
    raise AssertionError(f"not seen in {limit} messages: {[e['type'] for e in seen]}")


# --- auth -----------------------------------------------------------------------------------


def test_login_roles_and_binding(app_env):
    client, *_ = app_env
    assert (
        client.post("/auth/login", json={"badge_id": "EMP1001", "pin": "0000"}).status_code == 401
    )
    assert client.post("/auth/login", json={"badge_id": "NOPE", "pin": "1234"}).status_code == 401
    op = login(client, "EMP1001", "1234", "EXC001")
    admin = login(client, "SUP001", "9999")
    assert client.get("/sim/scenarios").status_code == 401
    assert client.get("/sim/scenarios", headers=auth(op)).status_code == 403
    r = client.get("/sim/scenarios", headers=auth(admin))
    assert r.json() == {
        "mode": "replay",
        "scenarios": sorted(p.stem for p in SCENARIOS.glob("*.jsonl")),
    }
    # an operator token bound to EXC001 cannot read another machine
    assert client.get("/state/current?machine=WL001", headers=auth(op)).status_code == 403
    assert client.get("/state/current?machine=EXC001", headers=auth(op)).status_code == 200
    status = client.get("/system/status", headers=auth(op)).json()
    assert status["auth_disabled"] is False and set(status["machines"]) == {
        "EXC001",
        "EXC002",
        "WL001",
    }
    assert {k: status["models"][k] for k in ("eta", "anomaly")} == {
        "eta": "UNAVAILABLE",
        "anomaly": "UNAVAILABLE",
    }


def test_missing_jwt_secret_refuses_to_start(tmp_path):
    app = create_app(settings(tmp_path, edge_jwt_secret=""), bus=MemoryBus())
    with pytest.raises(RuntimeError, match="EDGE_JWT_SECRET"), TestClient(app):
        pass


def test_auth_disabled_is_admin_and_reported(tmp_path):
    app = create_app(settings(tmp_path, edge_jwt_secret="", auth_disabled=True), bus=MemoryBus())
    with TestClient(app) as client:
        assert client.get("/sim/scenarios").status_code == 200
        assert client.get("/system/status").json()["auth_disabled"] is True


def test_edge_machine_ids_narrow_the_engines(tmp_path):
    app = create_app(settings(tmp_path, edge_machine_ids="EXC001"), bus=MemoryBus())
    with TestClient(app) as client:
        assert list(client.app.state.rt.runners) == ["EXC001"]


# --- the CP1 path: unsafe exit -> nudge on the socket, why, ack, reconnect -----------------


def test_unsafe_exit_over_websocket(app_env):
    client, bus, clock, lines = app_env
    op = login(client, "EMP1001", "1234", "EXC001")
    with client.websocket_connect(f"/ws/live?machine=EXC001&token={op}") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and valid("ws_envelope.v1", snap)
        data = snap["data"]
        assert valid("state.v1", data["state"]) and valid("hazards.v1", data["hazards"])
        assert valid("sync_status.v1", data["sync"]) and data["as_of"]

        feed(client, bus, clock, upto(lines, 66))
        nudge, seen = receive_until(
            ws, lambda e: e["type"] == "nudge" and e["data"]["rule_id"] == "R03"
        )
        n = nudge["data"]
        assert valid("nudge.v1", n)
        assert n["display"] == "FULLSCREEN" and n["tone_pattern"] == "tone_critical"
        assert n["audio_clip"] == "exit_guard_before_exiting"
        assert n["checklist"] == [
            "IMPLEMENT_RAISED",
            "HYD_UNLOCKED",
            "ENGINE_RUNNING",
            "MOVING",
            "SLOPE",
        ]  # rules.yaml checklist_order, excavator
        for env in seen:
            schema = {
                "state": "state.v1",
                "alert": "alert.v1",
                "event": "event.v1",
                "telemetry": "telemetry.v1",
                "nudge": "nudge.v1",
            }.get(env["type"])
            if schema:
                valid(schema, env["data"])

    # /why for the R03 alert: rule id, inputs, thresholds
    alert_id = n["alert_id"]
    why = client.get(f"/alerts/{alert_id}/why", headers=auth(op)).json()
    assert why["rule_id"] == "R03" and why["active"]
    assert why["inputs"]["exit_state"] == "UNSAFE" and why["inputs"]["hyd_lockout"] == "UNLOCKED"
    assert why["thresholds"]["grounded_max_m"] == 0.3

    # ack != clear
    acked = client.post(f"/alerts/{alert_id}/ack", headers=auth(op)).json()
    assert acked["acknowledged_at"] and acked["ack_by"] == "OP1001" and acked["active"]

    # reconnect mid-scenario: the snapshot is the current truth
    with client.websocket_connect(f"/ws/live?machine=EXC001&token={op}") as ws:
        snap = ws.receive_json()["data"]
        assert snap["state"]["exit_state"] == "UNSAFE" and snap["state"]["class"] == "UNSAFE"
        (r03,) = [a for a in snap["alerts"] if a["rule_id"] == "R03"]
        assert r03["acknowledged_at"] and r03["active"]  # FULLSCREEN stays until cleared

        feed(client, bus, clock, after(lines, 66))
        receive_until(ws, lambda e: e["type"] == "state" and e["data"]["exit_state"] == "SAFE")

    assert client.post(f"/alerts/{alert_id}/ack", headers=auth(op)).status_code == 409
    assert client.post("/alerts/nope/ack", headers=auth(op)).status_code == 404
    with Session(client.app.state.rt.db) as s:
        row = s.get(SafetyAlert, alert_id)
        assert row.acknowledged_at and not row.active and row.cleared_at


def test_three_point_contact_voice_wins_the_audio_budget(app_env):
    client, bus, clock, lines = app_env
    op = login(client, "EMP1001", "1234", "EXC001")
    with client.websocket_connect(f"/ws/live?machine=EXC001&token={op}") as ws:
        ws.receive_json()
        feed(client, bus, clock, upto(lines, 72))
        _, seen = receive_until(
            ws, lambda e: e["type"] == "nudge" and e["data"]["rule_id"] == "R04"
        )
    nudges = {e["data"]["rule_id"]: e["data"] for e in seen if e["type"] == "nudge"}
    assert nudges["R06"]["audio_clip"] == "exit_three_point_contact"  # audio_priority: 1
    assert nudges["R04"]["audio_clip"] is None and nudges["R04"]["tone_pattern"] is None
    assert "AUDIO" not in nudges["R04"]["channels"]


def test_ws_auth_and_site_socket(app_env):
    client, *_ = app_env
    op = login(client, "EMP1001", "1234", "EXC001")
    sup = login(client, "SUP001", "9999")
    for url, code in (
        ("/ws/live?machine=EXC001&token=bad", 4401),
        (f"/ws/live?machine=NOPE&token={op}", 4404),
        (f"/ws/live?machine=WL001&token={op}", 4403),
        (f"/ws/site?token={op}", 4403),
    ):
        with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect(url) as ws:
            ws.receive_json()
        assert exc.value.code == code, url
    with client.websocket_connect(f"/ws/site?token={sup}") as ws:
        snaps = [ws.receive_json() for _ in client.app.state.rt.runners]
        assert {s["data"]["state"]["machine_id"] for s in snaps} == set(client.app.state.rt.runners)


# --- sim control ---------------------------------------------------------------------------


def test_replay_mode_plays_onto_the_bus(app_env):
    client, *_ = app_env
    admin = login(client, "SUP001", "9999")
    rt = client.app.state.rt
    assert (
        client.post("/sim/scenario", json={"name": "nope"}, headers=auth(admin)).status_code == 404
    )
    before = rt.runners["WL001"].handled
    r = client.post(
        "/sim/scenario",
        json={"name": "reset", "machine_id": "WL001", "speed": 1000},
        headers=auth(admin),
    )
    assert r.status_code == 200 and r.json()["ok"] and r.json()["name"] == "reset"
    deadline = time.monotonic() + 5
    while rt.runners["WL001"].handled < before + 30:  # frames re-targeted at WL001
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert client.get("/system/status", headers=auth(admin)).json()["sim"]["running"] == "reset"
    with Session(rt.db) as s:  # the scenario's env frame is stored once, site-level
        assert s.exec(select(EnvironmentObs)).first() is not None


def test_proxy_mode_forwards_to_the_simulator(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.url.path == "/scenarios":
            return httpx.Response(200, json={"scenarios": ["reset", "unsafe_exit"]})
        return httpx.Response(
            200,
            json={"ok": True, "name": "unsafe_exit", "started_at": "2026-10-14T07:00:00.000+05:30"},
        )

    app = create_app(
        settings(tmp_path, sim_mode="proxy", sim_control_url="http://sim.test"), bus=MemoryBus()
    )
    with TestClient(app) as client:
        client.app.state.rt.sim_transport = httpx.MockTransport(handler)
        admin = login(client, "SUP001", "9999")
        assert client.get("/sim/scenarios", headers=auth(admin)).json()["scenarios"] == [
            "reset",
            "unsafe_exit",
        ]
        r = client.post(
            "/sim/scenario",
            json={"name": "unsafe_exit", "machine_id": "EXC001"},
            headers=auth(admin),
        )
        assert r.json()["ok"]
    assert calls[-1][:2] == ("POST", "/scenario")
    assert json.loads(calls[-1][2]) == {"name": "unsafe_exit", "machine_id": "EXC001"}


def test_sim_stop_ends_playback_and_hold(app_env):
    client, *_ = app_env
    admin = login(client, "SUP001", "9999")
    rt = client.app.state.rt
    client.post("/sim/scenario", json={"name": "reset", "speed": 1000}, headers=auth(admin))
    assert rt.replayer.current == "reset"
    r = client.post("/sim/stop", headers=auth(admin))
    assert r.json() == {"ok": True, "stopped": "reset"}
    assert rt.replayer.current is None and rt.replayer.task is None
    runner = rt.runners["EXC001"]
    deadline = time.monotonic() + 5
    while not runner.queue.empty():  # frames published before the stop still drain
        assert time.monotonic() < deadline
        time.sleep(0.01)
    time.sleep(0.1)
    before = runner.handled
    time.sleep(1.5)  # hold frames would arrive at the fixture's 1 s spacing
    assert runner.handled == before
    op = login(client, "EMP1001", "1234")
    assert client.post("/sim/stop", headers=auth(op)).status_code == 403


def test_sim_stop_in_proxy_mode_is_a_conflict(tmp_path):
    app = create_app(settings(tmp_path, sim_mode="proxy"), bus=MemoryBus())
    with TestClient(app) as client:
        admin = login(client, "SUP001", "9999")
        assert client.post("/sim/stop", headers=auth(admin)).status_code == 409


def test_ws_tokens_are_redacted_from_logs():
    import logging

    from common.log import RedactSecrets

    record = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        __file__,
        1,
        '%s - "WebSocket %s" [accepted]',
        ("1.2.3.4:5", "/ws/live?machine=EXC001&token=eyJhbGciOi.abc.def"),
        None,
    )
    RedactSecrets().filter(record)
    assert (
        record.getMessage()
        == '1.2.3.4:5 - "WebSocket /ws/live?machine=EXC001&token=***" [accepted]'
    )
