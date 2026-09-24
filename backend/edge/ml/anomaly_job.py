"""Anomaly job (HLD §4.5, §7.1; plan.md Phase 6 item 3; contracts/ml_features.md).

On every closed hourly window: build the frozen feature dict (plus a robust z-score of each
metric vs the operator's own windows in the previous 14 days: median / MAD), score it with
the machine family's model, store `anomaly_score` / `anomaly_flagged` / top features on the
window (+ outbox), and hand {window_id, score, threshold_top3pct} to the engine, where R23
(INFO, visual only) decides. Flagged windows with no `review_label` are the review queue.
No model -> nothing is scored and R23 stays UNKNOWN (plan.md §5.6).
"""

import statistics
from datetime import datetime, timedelta

from sqlmodel import Session, select

from common.db import repo
from common.db.models import EnvironmentObs, IdleEpisode, TelemetryWindow
from common.timeutil import to_site_iso

METRICS = (
    "idle_ratio",
    "fuel_per_productive_h",
    "fuel_per_load",
    "loads_per_productive_h",
    "passes_per_h",
    "seatbelt_unfastened_running_min",
    "long_idle_count",
    "elevated_rpm_idle_min",
    "mean_engine_load_pct",
    "hyd_oil_temp_over_ambient_c",
    "alert_count",
)
BASELINE_DAYS = 14  # HLD §4.5/§7.1: the operator's own 14-day baseline
MIN_BASELINE_N = 3  # fewer windows than this: no z (a z from 1–2 points is noise)
MAD_TO_SD = 1.4826  # robust z = (x - median) / (1.4826 · MAD)


def _ambient(session: Session, site_id: str, at: str) -> float | None:
    row = session.exec(
        select(EnvironmentObs)
        .where(
            EnvironmentObs.site_id == site_id,
            EnvironmentObs.ts <= at,
            EnvironmentObs.temp_c != None,  # noqa: E711 - SQL expression
        )
        .order_by(EnvironmentObs.ts.desc())
    ).first()
    return row.temp_c if row else None


def metrics(session: Session, w: TelemetryWindow, site_id: str) -> dict:
    """The numeric `ml_features.md` metrics for one stored window."""
    hours = (
        datetime.fromisoformat(w.window_end) - datetime.fromisoformat(w.window_start)
    ).total_seconds() / 3600
    elevated = session.exec(
        select(IdleEpisode).where(
            IdleEpisode.machine_id == w.machine_id,
            IdleEpisode.start_ts >= w.window_start,
            IdleEpisode.start_ts < w.window_end,
            IdleEpisode.elevated_rpm == True,  # noqa: E712 - SQL expression
        )
    ).all()
    ambient = _ambient(session, site_id, w.window_end)
    return {
        "idle_ratio": w.idle_ratio,
        "fuel_per_productive_h": w.fuel_per_productive_h,
        "fuel_per_load": w.fuel_per_load,
        "loads_per_productive_h": w.loads_per_productive_h,
        "passes_per_h": (w.pass_count / hours) if w.pass_count is not None and hours > 0 else None,
        "seatbelt_unfastened_running_min": w.seatbelt_unfastened_running_min,
        "long_idle_count": w.idle_long_count,
        "elevated_rpm_idle_min": sum(e.duration_min or 0 for e in elevated),
        "mean_engine_load_pct": w.mean_engine_load_pct,
        "hyd_oil_temp_over_ambient_c": (w.max_hyd_oil_temp_c - ambient)
        if w.max_hyd_oil_temp_c is not None and ambient is not None
        else None,
        "alert_count": w.alert_count,
    }


def baseline(session: Session, w: TelemetryWindow, site_id: str) -> dict:
    """{metric: {median, mad, n}} over the operator's windows in the 14 days before `w`."""
    since = to_site_iso(datetime.fromisoformat(w.window_start) - timedelta(days=BASELINE_DAYS))
    rows = session.exec(
        select(TelemetryWindow).where(
            TelemetryWindow.operator_id == w.operator_id,
            TelemetryWindow.window_start >= since,
            TelemetryWindow.window_start < w.window_start,
        )
    ).all()
    per = [metrics(session, r, site_id) for r in rows]
    out = {}
    for m in METRICS:
        vals = [p[m] for p in per if p[m] is not None]
        if not vals:
            continue
        med = statistics.median(vals)
        out[m] = {
            "median": med,
            "mad": statistics.median(abs(v - med) for v in vals),
            "n": len(vals),
        }
    return out


def features(
    session: Session, w: TelemetryWindow, site_id: str, task_type, power_mode
) -> tuple[dict, dict]:
    """(window feature dict incl. z_<metric>, baseline) per contracts/ml_features.md."""
    feats = metrics(session, w, site_id)
    base = baseline(session, w, site_id) if w.operator_id else {}
    for m in METRICS:
        b, v = base.get(m), feats[m]
        ok = b is not None and v is not None and b["n"] >= MIN_BASELINE_N and b["mad"] > 0
        feats[f"z_{m}"] = (v - b["median"]) / (MAD_TO_SD * b["mad"]) if ok else None
    feats["task_type"], feats["power_mode"] = task_type, power_mode
    return feats, base


def store(session: Session, window_id: str, result: dict) -> dict:
    row = session.get(TelemetryWindow, window_id)
    row.anomaly_score = result["score"]
    row.anomaly_flagged = result["score"] >= result["threshold_top3pct"]
    row.anomaly_top_features = result["top_features"]
    session.add(row)
    data = row.model_dump()
    repo.enqueue_sync(session, "telemetry_window", window_id, "UPSERT", data)
    return data


def review_queue(session: Session) -> list[TelemetryWindow]:
    """Flagged windows no one has labelled yet (supervisor review, HLD §4.15; D11)."""
    return session.exec(
        select(TelemetryWindow)
        .where(
            TelemetryWindow.anomaly_flagged == True,  # noqa: E712 - SQL expression
            TelemetryWindow.review_label == None,  # noqa: E711 - SQL expression
        )
        .order_by(TelemetryWindow.window_start.desc())
    ).all()
