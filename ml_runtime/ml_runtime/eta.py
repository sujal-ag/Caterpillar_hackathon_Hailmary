"""EtaModel — thin inference wrapper matching contracts/ml_runtime.md.

See ../README.md for the internal .joblib bundle format this expects.
Training code that produces that bundle lives in the separate ML training repo.
"""

from __future__ import annotations

import joblib
import numpy as np


class EtaModel:
    def __init__(self, version: str, feature_names: list[str], categorical_maps: dict,
                 model_p50, model_p90):
        self.version = version
        self._feature_names = feature_names
        self._categorical_maps = categorical_maps
        self._model_p50 = model_p50
        self._model_p90 = model_p90

    @classmethod
    def load(cls, path: str) -> "EtaModel":
        try:
            bundle = joblib.load(path)
        except Exception as exc:
            raise RuntimeError(f"Failed to load ETA model from {path}: {exc}") from exc

        required = {"version", "feature_names", "categorical_maps", "model_p50", "model_p90"}
        missing = required - bundle.keys()
        if missing:
            raise ValueError(f"ETA model bundle at {path} is missing keys: {missing}")

        return cls(
            version=bundle["version"],
            feature_names=bundle["feature_names"],
            categorical_maps=bundle["categorical_maps"],
            model_p50=bundle["model_p50"],
            model_p90=bundle["model_p90"],
        )

    def _vectorize(self, feats: dict) -> np.ndarray:
        row = []
        for name in self._feature_names:
            value = feats.get(name)
            if name in self._categorical_maps:
                cat_map = self._categorical_maps[name]
                row.append(cat_map.get(value, cat_map.get("__unknown__", -1)))
            else:
                row.append(np.nan if value is None else float(value))
        return np.array(row, dtype=float).reshape(1, -1)

    def predict(self, feats: dict) -> dict:
        x = self._vectorize(feats)
        log_p50 = float(self._model_p50.predict(x)[0])
        log_p90 = float(self._model_p90.predict(x)[0])
        p50_min = float(np.expm1(log_p50))
        p90_min = max(float(np.expm1(log_p90)), p50_min)  # monotonicity guard

        return {
            "p50_min": round(p50_min, 1),
            "p90_min": round(p90_min, 1),
            "drivers": self._top_drivers(x),
        }

    def _top_drivers(self, x: np.ndarray) -> list[dict]:
        try:
            contrib = self._model_p50.predict(x, pred_contrib=True)[0]
            pairs = list(zip(self._feature_names, contrib[:-1]))  # last entry is the bias term
            pairs.sort(key=lambda p: abs(p[1]), reverse=True)
            return [
                {"feature": name, "effect_pct": round(float(np.expm1(val)) * 100, 1)}
                for name, val in pairs[:3]
            ]
        except Exception:
            return []
