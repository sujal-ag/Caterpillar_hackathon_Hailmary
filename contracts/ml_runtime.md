# `ml_runtime` package interface — v1

Agreed by: P1 ☑

P1 ships a pip-installable package `ml_runtime`. The backend only loads it and calls it (plan.md §5.6). Model files are loaded from `MODELS_DIR` through `model_registry` (sha256 verified).

```python
class EtaModel:
    version: str
    @classmethod
    def load(cls, path: str) -> "EtaModel": ...
    def predict(self, feats: dict) -> dict:
        # -> {"p50_min": float, "p90_min": float,
        #     "drivers": [{"feature": str, "effect_pct": float}]}   # top 3

class AnomalyModel:
    version: str
    family: str                       # EXCAVATOR | WHEEL_LOADER
    threshold_top3pct: float          # score at/above which a window is "top 3%"
    @classmethod
    def load(cls, path: str) -> "AnomalyModel": ...
    def score(self, window: dict, baseline: dict) -> dict:
        # -> {"score": 0..1, "top_features": [{"feature","value","baseline","z"}]}
```

- Feature dict keys are exactly the ones in `ml_features.md`, with the units given there.
- Library versions: the backend pins the **same** scikit-learn / lightgbm / joblib versions as P1's training env (D24). P1 fills this in: `scikit-learn==1.5.2 lightgbm==4.5.0 joblib==1.4.2 python==3.11`.
- `load()` must fail loudly on a corrupt or incompatible file. The backend then keeps the previous model.

Backend fallbacks (always available, no P1 code needed):
- ETA → `generic_eta()` = task-type base-rate midpoint × soil fill factor (HLD §6.7). `p90 = 1.4 × p50`. `model_version: "generic"`, label `"Estimate (generic)"`.
- Anomaly → no score. `health.models.anomaly = "UNAVAILABLE"`. Rules keep running.

Until P1 delivers, a stub in `backend/tests/stubs/ml_runtime/` implements this interface (Phase 6, test-only).

## Hand-off (Phase 6)
1. `pip install` P1's `ml_runtime` into the edge image (pinned libs, D24).
2. Register each model file (copies it to `MODELS_DIR/{name}/{version}`, records sha256, marks it active):
   `EDGE_DB_PATH=… .venv/bin/python tools/register_model.py <name> <version> <file>`
   Names: `eta`, `anomaly_EXCAVATOR`, `anomaly_WHEEL_LOADER`.
3. Restart edge-api (hot-swap is cut). `/system/status` → `models` (LOCAL/UNAVAILABLE) and `model_detail` (versions, and why a model was rejected).
