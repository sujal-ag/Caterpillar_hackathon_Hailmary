"""Stub `ml_runtime` (contracts/ml_runtime.md) until P1 ships the real package. Test-only:
tests put `tests/stubs` on sys.path. Deterministic, dependency-free; model "files" are JSON.

EtaModel file:     {"version": str, "factor": float}
AnomalyModel file: {"version": str, "family": str, "threshold_top3pct": float}
"""

import json


class EtaModel:
    def __init__(self, version: str, factor: float):
        self.version, self.factor = version, factor

    @classmethod
    def load(cls, path: str) -> "EtaModel":
        d = json.loads(open(path).read())  # noqa: SIM115 - tiny stub
        return cls(d["version"], float(d.get("factor", 1.0)))

    def predict(self, feats: dict) -> dict:
        rate = feats["operator_rate_median"] * feats["fill_factor"]
        p50 = feats["planned_quantity"] / rate * 60 * self.factor
        return {
            "p50_min": p50,
            "p90_min": 1.25 * p50,
            "drivers": [
                {"feature": f"soil_type={feats['soil_type']}", "effect_pct": 12.0},
                {"feature": "operator_rate_trend", "effect_pct": -4.0},
                {"feature": f"heat_index_c={feats['heat_index_c']}", "effect_pct": 5.0},
            ],
        }


class AnomalyModel:
    def __init__(self, version: str, family: str, threshold: float):
        self.version, self.family, self.threshold_top3pct = version, family, threshold

    @classmethod
    def load(cls, path: str) -> "AnomalyModel":
        d = json.loads(open(path).read())  # noqa: SIM115 - tiny stub
        return cls(d["version"], d["family"], float(d["threshold_top3pct"]))

    def score(self, window: dict, baseline: dict) -> dict:
        zs = {k[2:]: v for k, v in window.items() if k.startswith("z_") and v is not None}
        top = sorted(zs, key=lambda k: -abs(zs[k]))[:3]
        return {
            "score": min(1.0, max((abs(v) for v in zs.values()), default=0.0) / 6),
            "top_features": [
                {
                    "feature": k,
                    "value": window.get(k),
                    "baseline": baseline.get(k, {}).get("median"),
                    "z": zs[k],
                }
                for k in top
            ],
        }
