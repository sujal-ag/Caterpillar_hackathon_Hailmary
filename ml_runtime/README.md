# ml_runtime

Thin inference wrapper the backend `pip install`s and calls directly (no HTTP
layer — see contracts/ml_runtime.md and contracts/ml_features.md). This
package contains ONLY inference code: loading a trained model file and
running predict()/score(). It intentionally does NOT contain:

- the data simulator
- training scripts
- feature-engineering pipelines used to build training data
- the actual trained .joblib model files (those are produced by the separate
  training repo and dropped into `models/` in the runtime repo before a demo)

## Internal .joblib bundle format (P1-owned; not part of any frozen contract)

EtaModel expects a joblib file containing a dict:
    {
        "version": str,
        "feature_names": list[str],           # order matches contracts/ml_features.md ETA table
        "categorical_maps": dict[str, dict],   # {feature_name: {category_value: int_index}}
        "model_p50": <fitted LightGBM Booster, quantile alpha=0.5>,
        "model_p90": <fitted LightGBM Booster, quantile alpha=0.9>,
    }

AnomalyModel expects a joblib file containing a dict:
    {
        "version": str,
        "family": "EXCAVATOR" | "WHEEL_LOADER",
        "threshold_top3pct": float,
        "feature_names": list[str],            # order matches contracts/ml_features.md Anomaly table
        "categorical_maps": dict[str, dict],
        "fuel_per_load_default_median": float, # imputes null fuel_per_load (§6.13 guard)
        "model": <fitted sklearn.ensemble.IsolationForest>,
    }

The training repo must produce bundles in exactly this shape.
