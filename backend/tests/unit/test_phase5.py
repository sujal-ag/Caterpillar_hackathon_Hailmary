"""Phase 5 gate (API level): auth + shift binding, shift lifecycle + handover note, readiness
(R20, I10), incidents (multipart, snapshot, links, create_hazard), hazards (retained topic,
confirm/resolve/delete, expiry), Operator B driving into Operator A's pin, env one-taps.
Same harness as test_api.py: real app, MemoryBus, fixture-driven clock."""

import asyncio
import json
from datetime import datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from common.db.models import HazardPin, Incident, Shift, SyncQueue
from common.shifts import shift_id_for
from common.timeutil import SITE_TZ, parse_iso, to_site_iso
from edge import hazards
from edge.bus import MemoryBus
from edge.main import create_app
from tests.engine_support import load
from tests.unit.test_api import SECRET, Clock, auth, feed, login, settings, upto, validator

V = {n: validator(n) for n in ("readiness.v1", "incident.v1", "hazards.v1", "event.v1")}
HAZ_TOPIC = "cat/SITE-PUN-01/hazards"


def valid(schema, payload):
    errors = [e.message for e in V[schema].iter_errors(payload)]
    assert not errors, (schema, errors)
    return True


@pytest.fixture
def env(tmp_path):
    lines = load("drive_into_zone")
    bus, clock = MemoryBus(), Clock(lines[0]["payload"]["ts"])
    app = create_app(settings(tmp_path, media_dir=str(tmp_path / "media")), bus=bus, clock=clock)
    with TestClient(app) as client:
        yield client, bus, clock, lines


def rt_of(client):
    return client.app.state.rt


def rows(client, model, *where):
    with Session(rt_of(client).db) as s:
        return s.exec(select(model).where(*where)).all()


def outbox(client, entity, entity_id):
    return rows(client, SyncQueue, SyncQueue.entity == entity, SyncQueue.entity_id == entity_id)


def settle(client):
    """Let the runtime's hazard consumer apply the latest retained list to every engine."""
    rt = rt_of(client)

    async def drain():
        while not rt.hazard_queue.empty():
            await asyncio.sleep(0.005)
        for r in rt.runners.values():  # a no-op call runs after anything queued before it
            await r.call(lambda e, now: None)

    client.portal.call(drain)


def claims(token):
    return jwt.decode(token, SECRET, algorithms=["HS256"], options={"verify_exp": False})


# --- auth + shift binding (user request: SHIFT_START/END, exp never born expired) ----------


def app_at(tmp_path, local: datetime, **over):
    clock = Clock(to_site_iso(local))
    app = create_app(settings(tmp_path, **over), bus=MemoryBus(), clock=clock)
    return app, clock


def test_login_binds_the_seeded_shift_and_expires_2h_after_planned_end(tmp_path):
    today = datetime.now(SITE_TZ).date()  # the seed plans today's shifts
    app, _ = app_at(
        tmp_path, datetime.combine(today, datetime.min.time(), SITE_TZ) + timedelta(hours=8)
    )
    with TestClient(app) as client:
        r = client.post(
            "/auth/login", json={"badge_id": "EMP1001", "pin": "1234", "machine_id": "EXC001"}
        )
        body = r.json()
        assert body["shift"]["shift_id"] == shift_id_for("EXC001", today)
        c = claims(body["token"])
        assert c["shift_id"] == body["shift"]["shift_id"]
        planned_end = parse_iso(body["shift"]["planned_end"])
        assert planned_end.astimezone(SITE_TZ).hour == 17  # SHIFT_END default
        # 08:00 + 12 h TTL = 20:00 > 17:00 + 2 h = 19:00 -> the TTL wins
        assert parse_iso(body["expires_at"]) == parse_iso(
            to_site_iso(datetime.combine(today, datetime.min.time(), SITE_TZ) + timedelta(hours=20))
        )


def test_login_at_2100_after_the_shift_is_not_born_expired(tmp_path):
    today = datetime.now(SITE_TZ).date()
    at_21 = datetime.combine(today, datetime.min.time(), SITE_TZ) + timedelta(hours=21)
    app, _ = app_at(tmp_path, at_21, jwt_ttl_s=60)
    with TestClient(app) as client:
        r = client.post(
            "/auth/login", json={"badge_id": "EMP1001", "pin": "1234", "machine_id": "EXC001"}
        )
        body = r.json()
        assert body["shift"]["status"] == "PLANNED"  # today's seeded shift, planned 07-17
        # planned_end + 2 h = 19:00 is already past; now + TTL wins
        assert parse_iso(body["expires_at"]) == at_21 + timedelta(seconds=60)
        tok = body["token"]
        assert client.get("/shift/current", headers=auth(tok)).status_code == 200
        r = client.post("/shift/start", json={}, headers=auth(tok))
        assert r.status_code == 200, r.text
        assert r.json()["shift"]["status"] == "ACTIVE"


def test_login_at_0030_creates_the_new_dates_shift_on_start(tmp_path):
    tomorrow = datetime.now(SITE_TZ).date() + timedelta(days=1)
    at_0030 = datetime.combine(tomorrow, datetime.min.time(), SITE_TZ) + timedelta(minutes=30)
    app, _ = app_at(tmp_path, at_0030, shift_start="19:00", shift_end="07:00")
    with TestClient(app) as client:
        r = client.post(
            "/auth/login", json={"badge_id": "EMP1001", "pin": "1234", "machine_id": "WL001"}
        )
        body = r.json()
        assert body["shift"] is None  # the seed plans EXC001/EXC002 only: none for WL001
        assert claims(body["token"])["shift_id"] is None
        assert parse_iso(body["expires_at"]) > at_0030
        tok = body["token"]
        r = client.post("/shift/start", json={}, headers=auth(tok))
        assert r.status_code == 200, r.text
        shift = r.json()["shift"]
        assert shift["shift_id"] == shift_id_for("WL001", tomorrow)
        assert shift["status"] == "ACTIVE" and shift["operator_id"] == "OP1001"
        # overnight window from SHIFT_START/SHIFT_END: 19:00 that date -> 07:00 the next day
        assert parse_iso(shift["planned_start"]).astimezone(SITE_TZ).hour == 19
        assert parse_iso(shift["planned_end"]) - parse_iso(shift["planned_start"]) == timedelta(
            hours=12
        )
        # a fresh login now binds to the ACTIVE shift
        again = login_body(client, "EMP1001", "1234", "WL001")
        assert again["shift"]["shift_id"] == shift["shift_id"]
        assert client.app.state.rt.runners["WL001"].engine.ctx.shift_id == shift["shift_id"]


def login_body(client, badge, pin, machine=None):
    r = client.post("/auth/login", json={"badge_id": badge, "pin": pin, "machine_id": machine})
    assert r.status_code == 200, r.text
    return r.json()


def test_auth_roles_and_unknown_badge(env):
    client, *_ = env
    assert client.post("/auth/login", json={"badge_id": "WHO?", "pin": "0000"}).status_code == 401
    assert (
        client.post("/auth/login", json={"badge_id": "EMP1001", "pin": "9999"}).status_code == 401
    )
    op = login(client, "EMP1001", "1234", "EXC001")
    assert client.delete("/hazards/x", headers=auth(op)).status_code == 403  # supervisor route
    with pytest.raises(Exception):  # noqa: B017 - starlette raises on the 4403 close
        with client.websocket_connect(f"/ws/site?token={op}") as ws:
            ws.receive_json()


def test_seed_assigns_operator_b_to_exc002(env):
    client, _, clock, _ = env
    today = clock.now.astimezone(SITE_TZ).date()  # the seed is anchored to the runtime clock
    (row,) = rows(client, Shift, Shift.shift_id == shift_id_for("EXC002", today))
    assert row.operator_id == "OP1002" and row.status == "PLANNED"


# --- shift lifecycle ---------------------------------------------------------------------


def test_shift_start_end_handover(env):
    client, bus, clock, lines = env
    feed(client, bus, clock, upto(lines, 3))
    a = login(client, "EMP1001", "1234", "EXC001")
    r = client.post("/shift/start", json={}, headers=auth(a))
    assert r.status_code == 200, r.text
    shift = r.json()["shift"]
    assert shift["engine_hours_start"] == pytest.approx(1524.62, abs=0.01)
    assert client.post("/shift/start", json={}, headers=auth(a)).status_code == 200  # idempotent
    engine = rt_of(client).runners["EXC001"].engine
    assert (engine.ctx.operator_id, engine.ctx.shift_id) == ("OP1001", shift["shift_id"])
    assert outbox(client, "shift", shift["shift_id"])

    b = login(client, "EMP1002", "5678", "EXC001")
    assert client.post("/shift/start", json={}, headers=auth(b)).status_code == 409
    assert client.post("/shift/end", json={}, headers=auth(b)).status_code == 403

    r = client.post("/shift/end", json={"handover_note": "Left track tension low"}, headers=auth(a))
    assert r.status_code == 200 and r.json()["shift"]["status"] == "CLOSED"
    assert engine.ctx.operator_id is None and engine.ctx.shift_id is None
    cur = client.get("/shift/current", headers=auth(b)).json()
    assert cur["previous_handover_note"] == "Left track tension low" and cur["as_of"]
    assert client.post("/shift/start", json={}, headers=auth(b)).status_code == 409  # closed today


# --- readiness ---------------------------------------------------------------------------

CHECK = dict(sleep_last_24h_h=8, sleep_last_48h_h=16, feel_score=4, rt_mean_ms=300, rt_lapses=0)


def test_readiness_d3_yellow_and_contract(env):
    client, *_ = env
    op = login(client, "EMP1001", "1234", "EXC001")
    r = client.post(
        "/readiness",
        json={**CHECK, "sleep_last_24h_h": 5, "sleep_last_48h_h": 11},
        headers=auth(op),
    )
    body = r.json()
    assert r.status_code == 200 and valid("readiness.v1", body)
    assert (body["score"], body["rating"]) == (70, "YELLOW")
    assert body["baseline_source"] == "POPULATION_DEFAULT"
    assert "readiness.reason.no_personal_baseline" in body["reasons"]
    r = client.post(
        "/readiness",
        json={**CHECK, "sleep_last_24h_h": 5, "sleep_last_48h_h": 14},
        headers=auth(op),
    )
    assert (r.json()["score"], r.json()["rating"]) == (85, "GREEN")


def test_personal_baseline_after_five_checks(env):
    client, *_ = env
    op = login(client, "EMP1001", "1234", "EXC001")
    for _ in range(5):
        client.post("/readiness", json={**CHECK, "rt_mean_ms": 250}, headers=auth(op))
    body = client.post("/readiness", json={**CHECK, "rt_mean_ms": 330}, headers=auth(op)).json()
    assert body["baseline_source"] == "PERSONAL"
    assert body["rt_delta_vs_baseline_pct"] == 32.0
    assert body["reasons"] == ["readiness.reason.rt_slower_30"]


def test_red_readiness_raises_r20_at_shift_start_and_never_blocks(env):
    client, bus, clock, lines = env
    feed(client, bus, clock, upto(lines, 2))
    op = login(client, "EMP1001", "1234", "EXC001")
    red = {**CHECK, "sleep_last_24h_h": 3, "rt_lapses": 3, "feel_score": 1}  # 100-30-30-15 = 25
    body = client.post("/readiness", json=red, headers=auth(op)).json()
    assert body["rating"] == "RED"
    (q,) = outbox(client, "readiness_check", body["check_id"])
    assert q.priority == 0  # RED -> P0
    engine = rt_of(client).runners["EXC001"].engine
    assert not [a for a in engine.alerts.active_records() if a["rule_id"] == "R20"]  # no shift yet

    r = client.post("/shift/start", json={}, headers=auth(op))  # I10: RED never blocks
    assert r.status_code == 200
    assert r.json()["shift"]["readiness_check_id"] == body["check_id"]
    (r20,) = [a for a in engine.alerts.active_records() if a["rule_id"] == "R20"]
    assert r20["escalated"] is True and r20["level"] == "WARNING"
    assert outbox(client, "safety_alert", r20["alert_id"])[0].priority == 0
    assert engine.state["readiness"] == "RED"
    for path in ("/shift/current", "/hazards"):
        assert client.get(path, headers=auth(op)).status_code == 200

    client.post("/shift/end", json={}, headers=auth(op))
    assert not [a for a in engine.alerts.active_records() if a["rule_id"] == "R20"]  # clears


# --- hazards -----------------------------------------------------------------------------


def worker_zone(x, y, radius=5):
    return {
        "type": "WORKER_ZONE",
        "geometry": {"type": "Point", "coordinates": [x, y]},
        "radius_m": radius,
    }


def test_hazard_crud_retained_list_and_validation(env):
    client, bus, *_ = env
    op = login(client, "EMP1001", "1234", "EXC001")
    b = login(client, "EMP1002", "5678", "EXC002")
    sup = login(client, "SUP001", "9999")
    r = client.post("/hazards", json=worker_zone(10, 10), headers=auth(op))
    assert r.status_code == 201, r.text
    pin = r.json()
    assert pin["version"] == 1 and pin["expires_at"] and pin["created_by"] == "OP1001"
    assert outbox(client, "hazard_pin", pin["pin_id"])[0].priority == 0

    retained = bus.retained[HAZ_TOPIC]
    assert valid("hazards.v1", retained) and [p["pin_id"] for p in retained["pins"]] == [
        pin["pin_id"]
    ]
    q = asyncio.Queue()
    bus.subscribe(HAZ_TOPIC, q)  # a new subscriber gets the full list at once
    _, payload = q.get_nowait()
    assert payload["pins"][0]["version"] == 1

    line = {"type": "OVERHEAD_LINE", "geometry": {"type": "Point", "coordinates": [50, 50]}}
    assert client.post("/hazards", json=line, headers=auth(op)).status_code == 422  # no clearance
    assert (
        client.post("/hazards", json={**line, "line_clearance_m": 8}, headers=auth(op)).status_code
        == 201
    )
    assert (
        client.post("/hazards", json=worker_zone(0, 0, radius=60), headers=auth(op)).status_code
        == 422
    )
    bad = {"type": "TRENCH", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1]]]}}
    assert client.post("/hazards", json=bad, headers=auth(op)).status_code == 422

    got = client.get("/hazards?bbox=0,0,20,20", headers=auth(op)).json()
    assert [p["pin_id"] for p in got["pins"]] == [pin["pin_id"]] and got["as_of"]
    assert client.get("/hazards?bbox=nope", headers=auth(op)).status_code == 422

    r = client.patch(f"/hazards/{pin['pin_id']}", json={"action": "CONFIRM"}, headers=auth(b))
    assert r.json()["confirmations"] == 1 and r.json()["version"] == 2
    assert (
        client.patch(
            f"/hazards/{pin['pin_id']}", json={"action": "RESOLVE"}, headers=auth(b)
        ).status_code
        == 403
    )
    r = client.patch(f"/hazards/{pin['pin_id']}", json={"action": "RESOLVE"}, headers=auth(op))
    assert r.json()["status"] == "RESOLVED" and r.json()["version"] == 3
    assert pin["pin_id"] not in [p["pin_id"] for p in bus.retained[HAZ_TOPIC]["pins"]]
    assert (
        client.patch(
            f"/hazards/{pin['pin_id']}", json={"action": "CONFIRM"}, headers=auth(op)
        ).status_code
        == 404
    )

    other = bus.retained[HAZ_TOPIC]["pins"][0]["pin_id"]
    r = client.delete(f"/hazards/{other}", headers=auth(sup))
    assert r.json()["deleted"] is True
    assert bus.retained[HAZ_TOPIC]["pins"] == []
    assert [q.op for q in outbox(client, "hazard_pin", other)][-1] == "DELETE"


def test_worker_zone_expires_after_24h(env):
    client, bus, clock, _ = env
    op = login(client, "EMP1001", "1234", "EXC001")
    zone = client.post("/hazards", json=worker_zone(10, 10), headers=auth(op)).json()
    line = client.post(
        "/hazards",
        json={
            "type": "OVERHEAD_LINE",
            "geometry": {"type": "Point", "coordinates": [50, 50]},
            "line_clearance_m": 8,
        },
        headers=auth(op),
    ).json()
    assert line["expires_at"] is None  # lines don't expire
    rt = rt_of(client)
    assert client.portal.call(hazards.expire, rt) == []
    clock.now += timedelta(hours=25)
    assert client.portal.call(hazards.expire, rt) == [zone["pin_id"]]
    (row,) = rows(client, HazardPin, HazardPin.pin_id == zone["pin_id"])
    assert row.deleted is True and row.version == 2  # tombstone kept in the DB
    assert [p["pin_id"] for p in bus.retained[HAZ_TOPIC]["pins"]] == [line["pin_id"]]
    assert [q.op for q in outbox(client, "hazard_pin", zone["pin_id"])] == ["UPSERT", "DELETE"]


def test_operator_b_drives_into_operator_a_zone(env):
    """HLD §12 3:45–4:05: A pins a worker zone; B's machine (EXC002) drives in -> R14 on B."""
    client, bus, clock, lines = env
    raw = [ln for ln in lines if ln["topic"].endswith("/raw")]
    target = raw[40]["payload"]["sens"]  # a point on the drive path, ~35 m from the start
    a = login(client, "EMP1001", "1234", "EXC001")
    pin = client.post(
        "/hazards", json=worker_zone(target["x_m"], target["y_m"]), headers=auth(a)
    ).json()
    settle(client)

    b_lines = []
    for ln in lines:
        ln = json.loads(json.dumps(ln))
        if ln["topic"].endswith("/raw"):
            ln["topic"] = ln["topic"].replace("EXC001", "EXC002")
            ln["payload"]["machine_id"] = "EXC002"
        b_lines.append(ln)
    feed(client, bus, clock, b_lines[:44])
    b_engine = rt_of(client).runners["EXC002"].engine
    (r14,) = [al for al in b_engine.alerts.active_records() if al["rule_id"] == "R14"]
    enter_ts = parse_iso(r14["ts"])
    t_inside = min(
        parse_iso(ln["payload"]["ts"])
        for ln in b_lines
        if ln["topic"].endswith("/raw")
        and (
            (ln["payload"]["sens"]["x_m"] - target["x_m"]) ** 2
            + (ln["payload"]["sens"]["y_m"] - target["y_m"]) ** 2
        )
        ** 0.5
        <= 5
    )
    assert enter_ts - t_inside <= timedelta(seconds=2)
    assert r14["slots"] == {"hazard_type": "WORKER_ZONE"}
    assert r14["state_snapshot"]["inputs"]["pin_id"] == pin["pin_id"]
    a_engine = rt_of(client).runners["EXC001"].engine
    assert not [al for al in a_engine.alerts.active_records() if al["rule_id"] == "R14"]


# --- incidents ---------------------------------------------------------------------------


def post_incident(client, token, meta, voice=True, photos=2, photo_type="image/jpeg"):
    files = [("incident", (None, json.dumps(meta)))]
    if voice:
        files.append(("voice", ("note.webm", b"OggS-voice", "audio/webm")))
    files += [("photos", (f"p{i}.jpg", b"\xff\xd8photo", photo_type)) for i in range(photos)]
    return client.post("/incidents", files=files, headers=auth(token))


NEAR_MISS = {"type": "NEAR_MISS", "category": "PERSON_PROXIMITY", "severity_self": 2}


def test_incident_with_media_snapshot_links_and_hazard(env, tmp_path):
    client, bus, clock, lines = env
    feed(client, bus, clock, upto(lines, 10))
    op = login(client, "EMP1001", "1234", "EXC001")
    client.post("/shift/start", json={}, headers=auth(op))
    rt = rt_of(client)
    alert_ids = [a["alert_id"] for a in rt.runners["EXC001"].engine.alerts.active_records()]

    r = post_incident(client, op, {**NEAR_MISS, "create_hazard": {"type": "WORKER_ZONE"}})
    assert r.status_code == 201, r.text
    body = r.json()
    inc = body["incident"]
    assert valid("incident.v1", inc)
    assert inc["voice_note_path"].endswith("voice.webm") and len(inc["photo_paths"]) == 2
    media = tmp_path / "media"
    assert (media / inc["voice_note_path"]).read_bytes() == b"OggS-voice"
    assert all((media / p).exists() for p in inc["photo_paths"])
    assert inc["operator_id"] == "OP1001" and inc["reporter_id"] == "OP1001"
    assert inc["x_m"] is not None and inc["state_snapshot"]["state"]["machine_id"] == "EXC001"
    assert inc["state_snapshot"]["last_60s"]["activity"] is not None
    assert set(alert_ids) <= set(inc["linked_alert_ids"])
    assert outbox(client, "incident", inc["incident_id"])[0].priority == 0

    pin = body["hazard"]
    assert inc["hazard_pin_id"] == pin["pin_id"] and body["hazard_error"] is None
    assert pin["geometry"]["coordinates"] == [inc["x_m"], inc["y_m"]] and pin["radius_m"] == 10
    assert outbox(client, "hazard_pin", pin["pin_id"])[0].priority == 0
    assert pin["pin_id"] in [p["pin_id"] for p in bus.retained[HAZ_TOPIC]["pins"]]


def test_incident_rejects_bad_media_and_keeps_report_without_position(env, tmp_path):
    client, bus, clock, lines = env
    op = login(client, "EMP1001", "1234", "EXC001")
    assert post_incident(client, op, NEAR_MISS, photos=4).status_code == 422
    assert post_incident(client, op, NEAR_MISS, photo_type="application/pdf").status_code == 415
    assert post_incident(client, op, {**NEAR_MISS, "severity_self": 9}).status_code == 422
    assert rows(client, Incident) == []
    assert not any((tmp_path / "media").glob("*/*"))  # nothing left behind

    # no telemetry yet: position UNKNOWN -> report saved, no pin, and it says why
    r = post_incident(
        client, op, {**NEAR_MISS, "create_hazard": {"type": "WORKER_ZONE"}}, voice=False, photos=0
    )
    assert r.status_code == 201
    assert r.json()["hazard"] is None and "position unknown" in r.json()["hazard_error"]
    assert r.json()["incident"]["x_m"] is None


# --- env one-taps --------------------------------------------------------------------------


def test_ground_tap_makes_steps_wet_without_freezing_weather(env):
    client, bus, clock, lines = env
    feed(client, bus, clock, upto(lines, 1))
    op = login(client, "EMP1001", "1234", "EXC001")
    r = client.post("/env/ground", json={"ground_condition": "WET"}, headers=auth(op))
    assert r.status_code == 200 and r.json()["source"] == "MANUAL"
    settle_env(client, bus, clock, r.json())
    engine = rt_of(client).runners["EXC001"].engine
    assert engine.ctx.env["ground_condition"] == "WET"
    sim = {**lines[0]["payload"], "temp_c": 31.0, "ground_condition": "DRY"}
    settle_env(client, bus, clock, sim)
    assert engine.ctx.env["temp_c"] == 31.0  # SIM weather still applies
    assert engine.ctx.env["ground_condition"] == "WET"  # MANUAL ground outranks SIM

    r = client.post("/env/manual", json={"temp_c": 38, "rh_pct": 60}, headers=auth(op))
    settle_env(client, bus, clock, r.json())
    assert engine.ctx.env["temp_c"] == 38 and engine.ctx.env["heat_index_c"] is not None
    assert (
        client.post("/env/manual", json={"temp_c": 38, "rh_pct": 160}, headers=auth(op)).status_code
        == 422
    )


def settle_env(client, bus, clock, frame):
    """Wait until every engine processed this env frame (already published by the API for
    the MANUAL ones; the SIM one is published here)."""
    rt = rt_of(client)
    if frame["source"] == "SIM":
        feed(client, bus, clock, [{"topic": "cat/SITE-PUN-01/env", "payload": frame}])
    else:
        settle(client)
    assert all(r.engine.ctx.env is not None for r in rt.runners.values())
