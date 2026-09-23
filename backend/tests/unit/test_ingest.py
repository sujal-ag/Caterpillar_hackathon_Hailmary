"""Phase 2 gate: SPN mapping, debounce, staleness, env formulas, idle buckets, rollup
arithmetic, DTC lifecycle, retention (plan.md Phase 2 verification)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from sqlmodel import Session, select

from common.db.models import TelemetrySample
from common.db.session import make_engine, sqlite_url
from common.spn import SPN_FIELDS
from common.timeutil import to_site_iso
from edge.ingest import dtc as dtc_mod
from edge.ingest import env, idle, normalise, retention, rollup
from edge.ingest.events import Seq, event_type_for_switch, make_event
from edge.ingest.stale import StaleTracker
from edge.ingest.switches import Debouncer

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ROOT = Path(__file__).resolve().parents[3]


def frame(**over) -> dict:
    base = {
        "schema": "raw.v1",
        "ts": "2026-10-14T10:42:17.000+05:30",
        "site_id": "SITE-PUN-01",
        "machine_id": "EXC001",
        "can": {},
        "sens": {},
        "sim_label": None,
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- normalise


def test_validate_raw_accepts_good_frame_and_rejects_malformed():
    assert normalise.validate_raw(frame()) == []
    assert normalise.validate_raw({"schema": "raw.v1"}) != []  # missing ts/site/machine
    assert normalise.validate_raw(frame(schema="raw.v0")) != []
    assert normalise.validate_raw(frame(ts="not-a-time")) != []


def test_every_spn_maps_and_missing_key_is_unknown():
    values = {
        "190": 1010,
        "92": 12,
        "183": 2.1,
        "250": 15240.6,
        "96": 58.4,
        "1761": 71.0,
        "247": 1524.62,
        "110": 86,
        "100": 290,
        "1638": 62,
        "168": 27.1,
        "84": 0.0,
        "1856": "UNFASTENED",
        "70": "ON",
        "523": 0,
    }
    full = normalise.normalise(frame(can=values))
    for spn, field in SPN_FIELDS.items():
        assert full[field] == values[spn], field

    empty = normalise.normalise(frame(can={}))
    for field in SPN_FIELDS.values():
        assert empty[field] is None, field  # I1: missing -> UNKNOWN, not defaulted


def test_normalise_derives_grounded_and_activity():
    sens = {
        "engine_state": "RUNNING",
        "bucket_height_m": 0.1,
        "swing_rate_deg_s": 0.0,
        "hyd_pump_press_kpa": 1000,
        "joystick_active": False,
        "travel_speed_kmh": 0.0,
    }
    row = normalise.normalise(frame(sens=sens))
    assert row["implement_grounded"] is True
    assert row["machine_activity"] == "IDLE"

    sens2 = {**sens, "swing_rate_deg_s": 30.0, "bucket_height_m": 3.0}
    row2 = normalise.normalise(frame(sens=sens2))
    assert row2["implement_grounded"] is False
    assert row2["machine_activity"] == "WORKING"

    row_off = normalise.normalise(frame(sens={"engine_state": "OFF"}))
    assert row_off["machine_activity"] == "OFF"
    assert row_off["implement_grounded"] is None  # bucket height unknown -> unknown, not False

    row_unknown = normalise.normalise(frame(sens={}))
    assert row_unknown["machine_activity"] is None  # I1


def test_normalise_against_real_replay_fixture():
    lines = [json.loads(s) for s in (FIXTURES / "replay_10min.jsonl").read_text().splitlines()]
    raw = [ln["payload"] for ln in lines if ln["topic"].endswith("/raw")]
    activities = []
    for r in raw:
        assert normalise.validate_raw(r) == []
        activities.append(normalise.normalise(r)["machine_activity"])
    assert activities[0] in ("WORKING", "IDLE")
    assert (
        activities[300:316] == ["IDLE"] * 16
    )  # belt-off-to-engine-off stretch: waiting, not working
    assert activities[316] == "OFF"  # engine off at the moment of correction
    assert activities[-1] == "WORKING"  # back to the truck-loading cycle


# --------------------------------------------------------------------------- switches


def test_debounce_flap_yields_exactly_one_change_at_first_transition():
    d = Debouncer()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    value, changed = d.apply("seatbelt", "FASTENED", t0)
    assert (value, changed) == ("FASTENED", False)  # baseline, not a change

    changes = []
    for i in range(1, 11):  # flips every 100 ms for 1 s
        now = t0 + timedelta(milliseconds=100 * i)
        new = "UNFASTENED" if i % 2 else "FASTENED"
        value, changed = d.apply("seatbelt", new, now)
        if changed:
            changes.append((i, value))
    assert changes == [(1, "UNFASTENED")]  # exactly one, at the very first flip, no delay


def test_debounce_accepts_next_change_after_window_clears():
    d = Debouncer()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    d.apply("door_open", False, t0)
    d.apply("door_open", True, t0 + timedelta(milliseconds=10))
    _, changed_late = d.apply("door_open", False, t0 + timedelta(milliseconds=600))
    assert changed_late is True


def test_event_type_map_and_default_severity():
    assert event_type_for_switch("seatbelt", "UNFASTENED") == "SEATBELT_UNFASTENED"
    assert event_type_for_switch("engine_state", "CRANKING") is None  # no HLD event for it
    seq = Seq()
    e1 = make_event(
        machine_id="EXC001",
        ts="2026-01-01T00:00:00+05:30",
        type_="DOOR_OPEN",
        seq=seq.next("EXC001"),
        source="ingest",
    )
    e2 = make_event(
        machine_id="EXC001",
        ts="2026-01-01T00:00:01+05:30",
        type_="DATA_STALE",
        seq=seq.next("EXC001"),
        source="ingest",
    )
    assert (e1.severity, e2.severity) == ("INFO", "WARNING")
    assert (e1.seq, e2.seq) == (0, 1)


# --------------------------------------------------------------------------- stale


def test_stale_after_3s_and_clears_on_resume():
    tracker = StaleTracker()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    tracker.mark_received("EXC001", t0)
    assert tracker.check("EXC001", t0 + timedelta(seconds=2)) is None
    assert tracker.is_stale("EXC001") is False
    assert tracker.check("EXC001", t0 + timedelta(seconds=3.5)) == "DATA_STALE"
    assert tracker.is_stale("EXC001") is True
    assert tracker.check("EXC001", t0 + timedelta(seconds=3.6)) is None  # no repeat
    assert tracker.mark_received("EXC001", t0 + timedelta(seconds=3.6)) == "DATA_RESTORED"
    assert tracker.is_stale("EXC001") is False


# --------------------------------------------------------------------------- env


@pytest.mark.parametrize(
    ("temp_c", "rh_pct", "dew", "hi", "wbgt", "level"),
    [
        (22.0, 95.0, 21.16, 22.74, 26.26, "NONE"),
        (32.0, 60.0, 23.26, 37.07, 33.26, "EXTREME_CAUTION"),
        (36.0, 40.0, 20.27, 39.14, 33.66, "DANGER"),
    ],
)
def test_env_formulas_match_hand_computed_values(temp_c, rh_pct, dew, hi, wbgt, level):
    assert env.dew_point_c(temp_c, rh_pct) == pytest.approx(dew, abs=0.01)
    hi_c = env.heat_index_c(temp_c, rh_pct)
    assert hi_c == pytest.approx(hi, abs=0.01)
    assert env.wbgt_est_c(temp_c, rh_pct) == pytest.approx(wbgt, abs=0.01)
    assert env.heat_level(hi_c) == level


def test_heat_index_matches_noaa_reference_table():
    # Classic NOAA example: 90F/70% RH -> ~106F.
    hi_f = env.heat_index_c((90 - 32) * 5 / 9, 70) * 9 / 5 + 32
    assert hi_f == pytest.approx(106, abs=1)


def test_build_environment_obs_missing_inputs_stay_unknown():
    obs = env.build_environment_obs({"ts": "x", "site_id": "SITE-PUN-01", "source": "SIM"})
    assert obs["dew_point_c"] is None and obs["heat_index_c"] is None and obs["wbgt_est_c"] is None


def test_current_env_priority_never_lets_forecast_clobber_live():
    cur = env.CurrentEnv()
    cur.update("SITE-PUN-01", {"source": "MACHINE_SENSOR", "temp_c": 30})
    cur.update("SITE-PUN-01", {"source": "FORECAST_CACHE", "temp_c": 99})
    assert cur.get("SITE-PUN-01")["temp_c"] == 30
    cur.update("SITE-PUN-01", {"source": "MANUAL", "temp_c": 31})
    assert cur.get("SITE-PUN-01")["temp_c"] == 31  # equal rank, newer manual wins


# --------------------------------------------------------------------------- idle


IST = ZoneInfo("Asia/Kolkata")  # +05:30 has a half-hour component — a UTC-midnight
# datetime is NOT hour-aligned in site-local time. Build test times directly in IST.


def _ts_seq(start: datetime, n: int, step_s: float = 1.0):
    return [to_site_iso(start + timedelta(seconds=step_s * i)) for i in range(n)]


def test_idle_episode_buckets_and_rollup_counts():
    tracker = idle.IdleTracker(low_idle_rpm=1000)
    acc = rollup.RollupAccumulator("EXC001")
    t = datetime(2026, 1, 1, 6, 0, 0, tzinfo=IST)
    episodes = []

    def tick(running, active, ts):
        _, ep = tracker.update(
            running=running,
            active=active,
            seatbelt="FASTENED",
            engine_rpm=1000 if not active else 1500,
            fuel_used_total_l=None,
            ts=ts,
        )
        acc.add_sample(
            {
                "ts": ts,
                "operator_id": "OP1001",
                "machine_activity": "WORKING" if active else "IDLE",
                "fuel_used_total_l": None,
                "load_count": None,
                "pass_count": None,
                "engine_load_pct": None,
                "hyd_oil_temp_c": None,
                "seatbelt": "FASTENED",
                "engine_hours": None,
                "payload_kg": None,
            }
        )
        return ep

    for pause_min in (2, 4, 7, 12):
        for ts in _ts_seq(t, int(pause_min * 60)):
            ep = tick(True, False, ts)
            if ep:
                episodes.append(ep)
        t = t + timedelta(seconds=pause_min * 60)
        ep = tick(True, True, to_site_iso(t))  # one active tick closes the episode
        if ep:
            episodes.append(ep)
        t += timedelta(seconds=61)  # a minute of work between pauses

    assert [round(e["duration_min"]) for e in episodes] == [2, 4, 7, 12]
    assert [e["bucket"] for e in episodes] == ["PAUSE", "SHORT", "MEDIUM", "LONG"]

    for ep in episodes:
        acc.add_idle_episode(ep)
    # force-read the still-open hour by closing it
    _, hour = acc.close(to_site_iso(t))
    assert hour["idling_time_min"] == pytest.approx(2 + 4 + 7 + 12, abs=0.05)
    assert (hour["idle_short_count"], hour["idle_medium_count"], hour["idle_long_count"]) == (
        1,
        1,
        1,
    )


def test_idle_episode_never_created_under_30s():
    tracker = idle.IdleTracker(low_idle_rpm=1000)
    t = datetime(2026, 1, 1, 6, 0, 0, tzinfo=IST)
    for ts in _ts_seq(t, 20):  # 20 s of inactivity, never crosses the 30 s threshold
        event, ep = tracker.update(
            running=True,
            active=False,
            seatbelt="FASTENED",
            engine_rpm=1000,
            fuel_used_total_l=None,
            ts=ts,
        )
        assert event is None
    event, ep = tracker.update(
        running=True,
        active=True,
        seatbelt="FASTENED",
        engine_rpm=1500,
        fuel_used_total_l=None,
        ts=to_site_iso(t + timedelta(seconds=21)),
    )
    assert (event, ep) == (None, None)  # a hiccup, never logged


def test_idle_elevated_rpm_and_fuel_and_belt_off_tracking():
    tracker = idle.IdleTracker(low_idle_rpm=1000)
    t = datetime(2026, 1, 1, 6, 0, 0, tzinfo=IST)
    fuel = 100.0
    ep = None
    for i in range(200):
        ts = to_site_iso(t + timedelta(seconds=i))
        fuel += 3.0 / 3600  # 3 L/h idle burn
        _, ep_now = tracker.update(
            running=True,
            active=False,
            seatbelt="UNFASTENED",
            engine_rpm=1400,
            fuel_used_total_l=fuel,
            ts=ts,
        )
        ep = ep_now or ep
    _, ep = tracker.update(
        running=True,
        active=True,
        seatbelt="UNFASTENED",
        engine_rpm=1700,
        fuel_used_total_l=fuel,
        ts=to_site_iso(t + timedelta(seconds=200)),
    )
    assert ep["elevated_rpm"] is True  # 1400 > 1000 + 300
    assert ep["fuel_l"] == pytest.approx(200 * 3.0 / 3600, abs=0.01)
    # 199, not 200: the first tick of a run has dt=0 (nothing to measure from yet).
    assert ep["seatbelt_off_min"] == pytest.approx(199 / 60, abs=0.01)


# --------------------------------------------------------------------------- rollup


def _idle_hour_samples(start: datetime, minutes: int, fuel_lph: float):
    fuel = 0.0
    out = []
    for i in range(minutes * 6):  # every 10 s
        ts = start + timedelta(seconds=10 * i)
        fuel += fuel_lph * 10 / 3600
        out.append(
            {
                "ts": to_site_iso(ts),
                "operator_id": "OP1001",
                "machine_activity": "IDLE",
                "fuel_used_total_l": round(fuel, 6),
                "load_count": 0,
                "pass_count": 0,
                "engine_load_pct": 12,
                "hyd_oil_temp_c": 60,
                "seatbelt": "FASTENED",
                "engine_hours": 100.0,
                "payload_kg": 0,
            }
        )
    return out


def test_rollup_fuel_used_l_for_an_idle_hour_and_null_fuel_per_load():
    acc = rollup.RollupAccumulator("EXC001")
    start = datetime(2026, 1, 1, 6, 0, 0, tzinfo=IST)
    for s in _idle_hour_samples(start, 60, fuel_lph=2.0):
        acc.add_sample(s)
    # one sample in the next hour forces the close
    _, hour = acc.add_sample(
        {
            **_idle_hour_samples(start, 1, 2.0)[0],
            "ts": to_site_iso(start + timedelta(hours=1, seconds=1)),
        }
    )
    assert hour["fuel_used_l"] == pytest.approx(2.0, abs=0.05)
    assert hour["fuel_per_load"] is None  # loads == 0


def test_rollup_fuel_per_load_null_below_three_loads_the_197_artefact_guard():
    acc = rollup.RollupAccumulator("EXC001")
    start = datetime(2026, 1, 1, 6, 0, 0, tzinfo=IST)
    samples = _idle_hour_samples(start, 55, fuel_lph=2.0)
    # 5 minutes of work with 2 loads completed (the HLD §6.13 "3.8 L / 2 loads" artefact)
    work_start = start + timedelta(minutes=55)
    for i in range(30):
        ts = work_start + timedelta(seconds=10 * i)
        samples.append(
            {
                "ts": to_site_iso(ts),
                "operator_id": "OP1001",
                "machine_activity": "WORKING",
                "fuel_used_total_l": samples[-1]["fuel_used_total_l"] + 12.0 * 10 / 3600,
                "load_count": 1 if i < 15 else 2,
                "pass_count": i,
                "engine_load_pct": 60,
                "hyd_oil_temp_c": 65,
                "seatbelt": "FASTENED",
                "engine_hours": 100.1,
                "payload_kg": 2000 if i in (14, 29) else 0,
            }
        )
    for s in samples:
        acc.add_sample(s)
    _, hour = acc.close(to_site_iso(start + timedelta(hours=1)))
    assert hour["load_cycles"] == 2
    assert hour["fuel_per_load"] is None  # loads < 3 (§6.13 guard)


def test_rollup_fuel_per_load_populated_at_three_or_more_loads():
    acc = rollup.RollupAccumulator("EXC001")
    start = datetime(2026, 1, 1, 6, 0, 0, tzinfo=IST)
    fuel = 0.0
    for i in range(180):  # 45 samples per load -> 0,1,2,3 over the hour
        ts = start + timedelta(seconds=10 * i)
        fuel += 12.0 * 10 / 3600
        acc.add_sample(
            {
                "ts": to_site_iso(ts),
                "operator_id": "OP1001",
                "machine_activity": "WORKING",
                "fuel_used_total_l": round(fuel, 6),
                "load_count": min(i // 45, 3),
                "pass_count": i,
                "engine_load_pct": 60,
                "hyd_oil_temp_c": 65,
                "seatbelt": "FASTENED",
                "engine_hours": 100.2,
                "payload_kg": 0,
            }
        )
    _, hour = acc.close(to_site_iso(start + timedelta(hours=1)))
    assert hour["load_cycles"] == 3
    assert hour["fuel_per_load"] == pytest.approx(hour["fuel_work_l"] / 3, abs=1e-3)
    assert hour["fuel_work_l"] > 0 and hour["fuel_idle_l"] == 0


# --------------------------------------------------------------------------- dtc


def _catalogue():
    rows = yaml.safe_load((ROOT / "data" / "catalogue" / "diagnostic_codes.yaml").read_text())
    return dtc_mod.build_catalogue_index(rows)


def test_dtc_lifecycle_active_repeat_then_cleared():
    tracker = dtc_mod.DtcTracker()
    catalogue = _catalogue()
    active = {
        "spn": 1638,
        "fmi": 16,
        "oc": 1,
        "cat_code": None,
        "active": True,
        "machine_id": "EXC001",
        "ts": "2026-01-01T00:00:00+05:30",
    }

    occ, evt = tracker.process(active, catalogue, current_telemetry={"hyd_oil_temp_c": 96}, seq=0)
    assert evt["type"] == "DTC_ACTIVE" and evt["severity"] == "CAUTION"  # MONITOR -> CAUTION
    assert occ["freeze_frame"] == {"hyd_oil_temp_c": 96}

    occ2, evt2 = tracker.process(
        {**active, "oc": 2, "ts": "2026-01-01T00:00:05+05:30"}, catalogue, None, seq=1
    )
    assert evt2 is None and occ2["occurrence_count"] == 2  # repeat: update, no new event

    occ3, evt3 = tracker.process(
        {**active, "active": False, "ts": "2026-01-01T00:01:00+05:30"}, catalogue, None, seq=2
    )
    assert evt3["type"] == "DTC_CLEARED"
    assert occ3["active"] is False


def test_dtc_unknown_code_is_undocumented_not_guessed():
    tracker = dtc_mod.DtcTracker()
    frame_ = {
        "spn": 9999,
        "fmi": 1,
        "oc": 1,
        "cat_code": None,
        "active": True,
        "machine_id": "EXC001",
        "ts": "2026-01-01T00:00:00+05:30",
    }
    _, evt = tracker.process(frame_, catalogue={}, current_telemetry=None, seq=0)
    assert evt["payload"]["action_class"] == "UNDOCUMENTED"
    assert evt["severity"] == "WARNING"


def test_dtc_stop_code_from_seed_catalogue_is_critical():
    tracker = dtc_mod.DtcTracker()
    catalogue = _catalogue()
    frame_ = {
        "spn": 110,
        "fmi": 0,
        "oc": 1,
        "cat_code": None,
        "active": True,
        "machine_id": "EXC001",
        "ts": "2026-01-01T00:00:00+05:30",
    }
    _, evt = tracker.process(frame_, catalogue, None, seq=0)
    assert evt["payload"]["action_class"] == "STOP"
    assert evt["severity"] == "CRITICAL"


# --------------------------------------------------------------------------- retention


def test_purge_old_samples_deletes_only_beyond_retention(tmp_path):
    from tools import seed

    eng = make_engine(sqlite_url(str(tmp_path / "edge.db")))
    seed.run(eng)
    now = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)
    with Session(eng) as s:
        old = TelemetrySample(ts=to_site_iso(now - timedelta(days=8)), machine_id="EXC001")
        recent = TelemetrySample(ts=to_site_iso(now - timedelta(days=1)), machine_id="EXC001")
        s.add(old)
        s.add(recent)
        s.commit()

    with Session(eng) as s:
        deleted = retention.purge_old_samples(s, now=now)
    assert deleted == 1
    with Session(eng) as s:
        remaining = s.exec(select(TelemetrySample)).all()
    assert len(remaining) == 1
    assert remaining[0].ts == to_site_iso(now - timedelta(days=1))
