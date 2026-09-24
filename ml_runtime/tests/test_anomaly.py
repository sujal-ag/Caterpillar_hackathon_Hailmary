"""Smoke test: trains a throwaway IsolationForest, round-trips through joblib,
and checks AnomalyModel.load()/score() returns the exact contract shape."""

import joblib
import numpy as np
import pytest
from sklearn.ensemble import IsolationForest

from ml_runtime import AnomalyModel


@pytest.fixture
def anomaly_model_path(tmp_path):
    x = np.random.rand(100, 2)
    clf = IsolationForest(n_estimators=50, contamination=0.03, random_state=0).fit(x)

    bundle = {
        "version": "test-v0",
        "family": "EXCAVATOR",
        "threshold_top3pct": 0.5,
        "feature_names": ["idle_ratio", "fuel_per_productive_h"],
        "categorical_maps": {},
        "fuel_per_load_default_median": 0.4,
        "model": clf,
    }
    path = tmp_path / "anomaly_test.joblib"
    joblib.dump(bundle, path)
    return str(path)


def test_load_and_score_shape(anomaly_model_path):
    model = AnomalyModel.load(anomaly_model_path)
    assert model.family == "EXCAVATOR"
    window = {"idle_ratio": 0.8, "fuel_per_productive_h": 15.0}
    baseline = {
        "idle_ratio": {"median": 0.3, "mad": 0.05, "n": 20},
        "fuel_per_productive_h": {"median": 9.0, "mad": 1.0, "n": 20},
    }
    result = model.score(window, baseline)
    assert set(result.keys()) == {"score", "top_features"}
    assert 0 <= result["score"] <= 1
    assert len(result["top_features"]) <= 3


def test_load_missing_file_raises():
    with pytest.raises(RuntimeError):
        AnomalyModel.load("/nonexistent/path.joblib")
