"""Phase 0: fixture scripter, replay scheduling, record format, health endpoints."""

import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from cloud.main import app as cloud_app
from edge.main import app as edge_app
from tools.fake_machine import generate
from tools.record import to_line
from tools.replay import retimed, schedule

HERE = Path(__file__).resolve().parents[1]
CONTRACTS = HERE.parent.parent / "contracts" / "schemas"
FIXTURE = HERE / "fixtures" / "replay_10min.jsonl"
SPEC = HERE / "fixtures" / "specs" / "replay_10min.yaml"


def fixture_lines() -> list[dict]:
    return [json.loads(s) for s in FIXTURE.read_text().splitlines()]


def test_fixture_is_regenerated_from_spec():
    # Deterministic and in sync: rerun fake_machine and compare with the committed file.
    assert generate(yaml.safe_load(SPEC.read_text())) == fixture_lines()


def test_fixture_frames_match_contracts():
    validators = {
        p.name.removesuffix(".json"): Draft202012Validator(json.loads(p.read_text()))
        for p in CONTRACTS.glob("*.json")
    }
    for line in fixture_lines():
        p = line["payload"]
        errors = [e.message for e in validators[p["schema"]].iter_errors(p)]
        assert not errors, (line["topic"], p["ts"], errors)
        assert p.get("site_id", "SITE-PUN-01") == "SITE-PUN-01"


def test_fixture_contains_one_unsafe_exit_corrected_in_14s():
    raw = [ln["payload"] for ln in fixture_lines() if ln["topic"] == "cat/SITE-PUN-01/EXC001/raw"]
    assert len(raw) == 600
    labelled = [i for i, p in enumerate(raw) if p["sim_label"] == "UNSAFE_EXIT"]
    assert labelled == list(range(300, 316))
    first = lambda cond: next(i for i, p in enumerate(raw) if cond(p))  # noqa: E731
    belt_off = first(lambda p: p["can"]["1856"] == "UNFASTENED")
    vacated = first(lambda p: p["sens"]["seat_occupied"] is False)
    engine_off = first(lambda p: p["sens"]["engine_state"] == "OFF")
    assert (belt_off, vacated, engine_off) == (300, 302, 316)
    at_intent = raw[vacated]["sens"]
    assert at_intent["bucket_height_m"] == 2.1 and at_intent["hyd_lockout"] == "UNLOCKED"
    # counters only move forward
    for key in ("250", "247"):
        vals = [p["can"][key] for p in raw]
        assert vals == sorted(vals)
    assert raw[-1]["sens"]["pass_count"] > raw[0]["sens"]["pass_count"]


def test_schedule_scales_and_carries_untimed_lines():
    lines = [
        {"topic": "a", "payload": {"ts": "2026-10-14T10:00:00+05:30"}},
        {"topic": "b", "payload": {"no_ts": 1}},
        {"topic": "c", "payload": {"ts": "2026-10-14T10:00:10+05:30"}},
    ]
    assert [o for o, _ in schedule(lines, speed=10)] == [0.0, 0.0, 1.0]


def test_retimed_shifts_ts_only_when_present():
    from datetime import UTC, datetime

    start = datetime(2026, 1, 1, tzinfo=UTC)
    assert retimed({"ts": "x", "v": 1}, 2.5, start) == {
        "ts": "2026-01-01T05:30:02.500+05:30",
        "v": 1,
    }
    assert retimed({"v": 1}, 2.5, start) == {"v": 1}


def test_record_line_is_replayable():
    assert json.loads(to_line("t", b'{"ts": "x"}', True)) == {
        "topic": "t",
        "payload": {"ts": "x"},
        "retain": True,
    }
    assert json.loads(to_line("t", b"\xffnot json", False))["payload"] == "�not json"


def test_health_endpoints_carry_ts():
    for app, name in ((edge_app, "edge-api"), (cloud_app, "cloud-api")):
        body = TestClient(app).get("/health").json()
        assert body["status"] == "ok" and body["service"] == name
        assert body["ts"].endswith("+05:30")  # I6 + D22


def test_openapi_export_is_current():
    from tools.export_openapi import OUT, render

    assert OUT.read_text() == render(), "run: python tools/export_openapi.py"
