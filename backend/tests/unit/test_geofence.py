"""Phase 5 gate (engine level): geofence enter/exit with hysteresis, R14, pin removal, and
the position-unknown pause (HLD §4.12; plan.md Phase 5 item 6)."""

from common.timeutil import parse_iso
from edge.geo import geofence
from tests.engine_support import Run, inline, make_engine

PIN = {
    "pin_id": "PIN-1",
    "type": "WORKER_ZONE",
    "geometry": {"type": "Point", "coordinates": [200.0, 92.7]},
    "radius_m": 10.0,
    "line_clearance_m": None,
    "status": "ACTIVE",
    "deleted": False,
}


def frames_at(xs, drop_position_from=None):
    """One IDLE frame per second, x_m from `xs` (the edge of PIN is x = 190)."""
    lines = [
        ln
        for ln in inline([{"at": 0, "mode": "IDLE"}], duration_s=len(xs))
        if "/raw" in ln["topic"]
    ]
    for i, (ln, x) in enumerate(zip(lines, xs, strict=False)):
        sens = ln["payload"]["sens"]
        sens["x_m"] = x
        if drop_position_from is not None and i >= drop_position_from:
            sens.pop("x_m"), sens.pop("y_m")
    return lines[: len(xs)]


def engine_with_pin(lines):
    e = make_engine()
    e.set_hazards([PIN], parse_iso(lines[0]["payload"]["ts"]))
    return e


def test_outside_by_point_and_polygon():
    assert geofence.outside_by(PIN, 195, 92.7) == -5
    square = {
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
        }
    }
    assert geofence.outside_by(square, 5, 5) == 0
    assert geofence.outside_by(square, 13, 5) == 3


def test_edge_oscillation_enters_once_and_exits_past_hysteresis():
    # the zone edge is x = 190 (inside above it); jitter ±1 m around it, then leave
    xs = [185, 185, 189.5, 190.5, 189.5, 191, 189, 190.5, 188.1, 187]
    lines = frames_at(xs)
    run = Run(engine_with_pin(lines), lines)
    enters = run.events("GEOFENCE_ENTER")
    exits = run.events("GEOFENCE_EXIT")
    assert [t for t, _ in enters] == [3]  # first frame inside (190.5)
    assert [t for t, _ in exits] == [9]  # 187 = 3 m outside > 2 m hysteresis
    assert enters[0][1].payload["pin_id"] == "PIN-1"
    raised = run.alerts("R14", "RAISED")
    assert len(raised) == 1 and raised[0][0] == 3
    assert raised[0][2]["slots"] == {"hazard_type": "WORKER_ZONE"}
    assert raised[0][2]["message_key"] == "nudge.geofence.enter.WORKER_ZONE"
    assert [t for t, *_ in run.alerts("R14", "CLEARED")] == [9]


def test_removed_pin_exits_the_zone_and_clears_r14():
    lines = frames_at([195, 195, 195])
    e = engine_with_pin(lines)
    run = Run(e, lines)
    assert run.alerts("R14", "RAISED")
    out = e.set_hazards([], parse_iso(lines[-1]["payload"]["ts"]))
    assert [ev.type for ev in out.events] == ["GEOFENCE_EXIT"]
    assert out.events[0].payload["reason"] == "PIN_REMOVED"
    assert [a for a, al in out.alerts if al["rule_id"] == "R14"] == ["CLEARED"]
    assert e.ctx.zones_inside == {}


def test_pin_dropped_on_the_machine_enters_immediately():
    lines = frames_at([195, 195])
    e = make_engine()
    Run(e, lines)
    out = e.set_hazards([PIN], parse_iso(lines[-1]["payload"]["ts"]))
    assert [ev.type for ev in out.events] == ["GEOFENCE_ENTER"]
    assert [a for a, al in out.alerts if al["rule_id"] == "R14"] == ["RAISED"]


def test_position_unknown_pauses_geofence_visibly():
    # inside at t=1, then 12 s with no x/y while the machine (really) drives out
    lines = frames_at([195, 195] + [300] * 12, drop_position_from=2)
    run = Run(engine_with_pin(lines), lines)
    assert len(run.events("GEOFENCE_ENTER")) == 1
    assert run.events("GEOFENCE_EXIT") == []  # paused, not guessed
    assert run.alerts("R14", "CLEARED") == []  # UNKNOWN holds the alert (I1)
    assert run.engine.state["sensor_health"]["position"] == "UNKNOWN"
    assert run.engine.state["class"] != "PRODUCTIVE"
