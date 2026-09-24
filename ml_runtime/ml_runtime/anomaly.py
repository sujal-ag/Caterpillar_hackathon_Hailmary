"""AnomalyModel — thin inference wrapper matching contracts/ml_runtime.md.

Internal .joblib bundle format (P1-owned; not part of any frozen contract):
    {
        "version": str,
        "family": "EXCAVATOR" | "WHEEL_LOADER",
        "threshold_top3pct": float,
        "feature_names": list[str],            # z_<metric> names + any categorical names;
                                                 # order matches how build_features.py /
                                                 # train_models.py built the training matrix
        "categorical_maps": dict[str, dict],
        "fuel_per_load_default_median": float,  # imputes null fuel_per_load (§6.13 guard)
        "model": <fitted sklearn.ensemble.IsolationForest>,
    }

DESIGN NOTE: the model is trained and scored on Z-SCORES vs the operator's own
baseline, not raw metric values. "Anomalous" only means something relative to what
is normal for THIS operator — a veteran's normal idle_ratio can be a novice's
outlier — so feature_names are expected to be prefixed "z_<metric>" (e.g.
"z_idle_ratio") for every numeric metric, and categorical features (task_type,
power_mode) pass through unprefixed via categorical_maps. This must match how
build_features.py computed the training set, or serving-time scores will not mean
what training-time scores meant.

Training code that produces this bundle lives in the separate ML training repo.
"""

from __future__ import annotations

import joblib
import numpy as np


def _is_missing(v) -> bool:
    """True for None or NaN (float('nan') != float('nan'), the standard trick) —
    pandas represents missing numeric values as NaN, not None, so both must be
    treated as missing or a NaN silently reaches IsolationForest and crashes it
    (confirmed by a real crash while testing this against pandas-sourced data)."""
    return v is None or (isinstance(v, float) and v != v)


class AnomalyModel:
    def __init__(self, version: str, family: str, threshold_top3pct: float,
                 feature_names: list[str], categorical_maps: dict,
                 fuel_per_load_default_median: float, model):
        self.version = version
        self.family = family
        self.threshold_top3pct = threshold_top3pct
        self._feature_names = feature_names
        self._categorical_maps = categorical_maps
        self._fuel_per_load_default_median = fuel_per_load_default_median
        self._model = model

    @classmethod
    def load(cls, path: str) -> "AnomalyModel":
        try:
            bundle = joblib.load(path)
        except Exception as exc:
            raise RuntimeError(f"Failed to load anomaly model from {path}: {exc}") from exc

        required = {
            "version", "family", "threshold_top3pct", "feature_names",
            "categorical_maps", "fuel_per_load_default_median", "model",
        }
        missing = required - bundle.keys()
        if missing:
            raise ValueError(f"Anomaly model bundle at {path} is missing keys: {missing}")

        return cls(
            version=bundle["version"],
            family=bundle["family"],
            threshold_top3pct=bundle["threshold_top3pct"],
            feature_names=bundle["feature_names"],
            categorical_maps=bundle["categorical_maps"],
            fuel_per_load_default_median=bundle["fuel_per_load_default_median"],
            model=bundle["model"],
        )

    def _vectorize(self, window: dict, baseline: dict) -> tuple[np.ndarray, dict]:
        """Reads z-scores DIRECTLY from `window` (keys "z_<metric>") — the real
        backend's anomaly_job.py already precomputes these upstream before calling
        score(), exactly like backend/tests/stubs/ml_runtime's stub does. `baseline`
        is used ONLY to populate the "baseline" display value in the returned
        top_features explanation, never to (re)compute the z itself — recomputing
        it here would silently diverge from what anomaly_job.py actually sends."""
        row = []
        z_scores = {}
        for name in self._feature_names:
            if name in self._categorical_maps:
                value = window.get(name)
                cat_map = self._categorical_maps[name]
                row.append(cat_map.get(value, cat_map.get("__unknown__", -1)))
                continue
            metric = name[2:] if name.startswith("z_") else name
            z = window.get(f"z_{metric}")
            row.append(0.0 if _is_missing(z) else float(z))
            if not _is_missing(z):
                z_scores[metric] = {
                    "value": window.get(metric),
                    "baseline": baseline.get(metric, {}).get("median"),
                    "z": round(float(z), 2),
                }
        return np.array(row, dtype=float).reshape(1, -1), z_scores

    def score(self, window: dict, baseline: dict) -> dict:
        x, z_scores = self._vectorize(window, baseline)
        raw_score = -self._model.score_samples(x)[0]  # higher = more anomalous
        normalized = float(np.clip(raw_score / (self.threshold_top3pct or 1e-9), 0, 1))

        top_features = sorted(
            ({"feature": k, **v} for k, v in z_scores.items()),
            key=lambda d: abs(d["z"]),
            reverse=True,
        )[:3]

        return {"score": round(normalized, 3), "top_features": top_features}
