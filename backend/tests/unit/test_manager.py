"""/manager/*: supervisor lists operators, assigns and reassigns tasks (contracts/rest.md)."""

from fastapi.testclient import TestClient

from common.db.models import Shift
from tests.unit.test_api import auth, login
from tests.unit.test_phase5 import outbox, rows
from tests.unit.test_phase6 import AT_0700, TASK_V1, make_app, tasks_of

TODAY, TOMORROW = "2026-10-14", "2026-10-15"


def body(**over):
    return {
        "operator_id": "OP1001",
        "machine_id": "EXC001",
        "task_type_id": "TRENCH",
        "zone_id": "Z-EAST",
        "planned_quantity": 60,
        "soil_type": "LOAM",
        "priority": 1,
        "scheduled_start": f"{TODAY}T11:00:00+05:30",
        "scheduled_end": f"{TODAY}T12:30:00+05:30",
        **over,
    }


def test_roles_and_operator_list(tmp_path):
    app, *_ = make_app(tmp_path, AT_0700)
    with TestClient(app) as client:
        op = login(client, "EMP1001", "1234", "EXC001")
        sup = login(client, "SUP001", "9999")
        for path in ("/manager/operators", "/manager/tasks", "/manager/options"):
            assert client.get(path, headers=auth(op)).status_code == 403
        assert client.post("/manager/tasks", json=body(), headers=auth(op)).status_code == 403

        r = client.get("/manager/operators", headers=auth(sup)).json()
        assert r["as_of"]
        by_id = {o["operator_id"]: o for o in r["operators"]}
        a = by_id["OP1001"]
        assert "pin_hash" not in a and "persona" not in a  # I9
        assert a["shift"]["machine_id"] == "EXC001" and a["tasks_today"]["total"] == 2

        opts = client.get("/manager/options", headers=auth(sup)).json()
        assert {m["machine_id"] for m in opts["machines"]} == {"EXC001", "EXC002", "WL001"}
        assert "LOAM" in opts["soil_types"] and "Z-NORTH-CUT" in opts["zones"]


def test_assign_reaches_operator_and_outbox(tmp_path):
    app, *_ = make_app(tmp_path, AT_0700)
    with TestClient(app) as client:
        op = login(client, "EMP1001", "1234", "EXC001")
        sup = login(client, "SUP001", "9999")
        assert client.post("/shift/start", json={}, headers=auth(op)).status_code == 200
        with client.websocket_connect(f"/ws/live?machine=EXC001&token={op}") as ws:
            ws.receive_json()  # snapshot
            r = client.post("/manager/tasks", json=body(), headers=auth(sup))
            assert r.status_code == 201, r.text
            task = r.json()
            assert not list(TASK_V1.iter_errors(task))
            assert task["unit"] == "M3" and task["shift_id"] == "SH-20261014-EXC001-D"
            for _ in range(50):
                env = ws.receive_json()
                if env["type"] == "eta" and env["data"]["task_id"] == task["task_id"]:
                    break
            else:
                raise AssertionError("no WS eta for the assigned task")

        assert task["task_id"] in [t["task_id"] for t in tasks_of(client, op)]
        (q,) = outbox(client, "task", task["task_id"])[:1]
        assert q.priority == 1  # I7: same unit of work, P1

        day = client.get(f"/manager/tasks?date={TODAY}", headers=auth(sup)).json()["tasks"]
        assert {t["operator_name"] for t in day} == {"Demo Operator"} and len(day) == 3
        detail = client.get("/manager/operators/OP1001", headers=auth(sup)).json()["operator"]
        assert task["task_id"] in [t["task_id"] for t in detail["tasks"]]
        assert {"alerts", "lessons", "shifts", "readiness_history"} <= detail.keys()


def test_assign_rejections(tmp_path):
    app, *_ = make_app(tmp_path, AT_0700)
    with TestClient(app) as client:
        sup = login(client, "SUP001", "9999")

        def post(**over):
            return client.post("/manager/tasks", json=body(**over), headers=auth(sup))

        assert post(operator_id="OP1002").status_code == 409  # EXC001 is OP1001's today
        assert post(operator_id="GUEST0").status_code == 409  # not certified
        assert post(operator_id="SUP001").status_code == 409  # not an operator
        assert post(operator_id="NOPE").status_code == 404
        assert post(machine_id="WL001", operator_id="OP1002").status_code == 409  # family
        assert post(task_type_id="LOAD_CARRY").status_code == 409  # wheel-loader task
        assert post(soil_type="MUD").status_code == 422
        end = f"{TODAY}T10:00:00+05:30"
        assert post(scheduled_end=end).status_code == 422
        assert post(scheduled_start="2026-10-14T11:00:00").status_code == 422  # no offset


def test_new_day_shift_and_reassign(tmp_path):
    app, *_ = make_app(tmp_path, AT_0700)
    with TestClient(app) as client:
        sup = login(client, "SUP001", "9999")
        start, end = f"{TOMORROW}T08:00:00+05:30", f"{TOMORROW}T09:00:00+05:30"
        r = client.post(
            "/manager/tasks", json=body(scheduled_start=start, scheduled_end=end), headers=auth(sup)
        )
        assert r.status_code == 201, r.text
        tid = r.json()["task_id"]
        (s,) = rows(client, Shift, Shift.shift_id == "SH-20261015-EXC001-D")
        assert s.status == "PLANNED" and s.operator_id == "OP1001"
        assert outbox(client, "shift", s.shift_id)

        move = {"operator_id": "OP1002", "machine_id": "EXC002", "scheduled_start": start}
        r = client.patch(f"/manager/tasks/{tid}", json=move, headers=auth(sup))
        assert r.status_code == 200, r.text
        t = r.json()
        assert (t["operator_id"], t["shift_id"]) == ("OP1002", "SH-20261015-EXC002-D")

        assert (
            client.post(
                f"/tasks/{tid}/status", json={"status": "IN_PROGRESS"}, headers=auth(sup)
            ).status_code
            == 200
        )
        r = client.patch(f"/manager/tasks/{tid}", json={"priority": 5}, headers=auth(sup))
        assert r.status_code == 409  # started tasks keep their assignment
