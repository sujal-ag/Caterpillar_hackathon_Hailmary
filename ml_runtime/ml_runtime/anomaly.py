"""AnomalyModel — thin inference wrapper matching contracts/ml_runtime.md.

See ../README.md for the internal .joblib bundle format this expects.
Training code that produces that bundle lives in the separate ML training repo.
"""

from __future__ import annotations

import joblib
import numpy as np


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
        row = []
        z_scores = {}
        for name in self._feature_names:
            value = window.get(name)
            if name == "fuel_per_load" and value is None:
                value = self._fuel_per_load_default_median
            if name in self._categorical_maps:
                cat_map = self._categorical_maps[name]
                row.append(cat_map.get(value, cat_map.get("__unknown__", -1)))
                continue
            numeric_value = 0.0 if value is None else float(value)
            row.append(numeric_value)
            base = baseline.get(name)
            if base and base.get("mad", 0) > 0:
                z = 0.6745 * (numeric_value - base["median"]) / base["mad"]
                z_scores[name] = {
                    "value": numeric_value,
                    "baseline": base["median"],
                    "z": round(z, 2),
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
