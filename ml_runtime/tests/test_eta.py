"""Smoke test: trains a throwaway 2-feature LightGBM pair, round-trips through
joblib, and checks EtaModel.load()/predict() returns the exact contract shape.
This is NOT a real model — it only proves the wrapper class works."""

import joblib
import lightgbm as lgb
import numpy as np
import pytest

from ml_runtime import EtaModel


@pytest.fixture
def eta_model_path(tmp_path):
    x = np.random.rand(50, 2)
    y = np.log1p(x[:, 0] * 100 + x[:, 1] * 10)
    ds = lgb.Dataset(x, label=y)
    model_p50 = lgb.train({"objective": "quantile", "alpha": 0.5, "verbosity": -1}, ds, num_boost_round=5)
    model_p90 = lgb.train({"objective": "quantile", "alpha": 0.9, "verbosity": -1}, ds, num_boost_round=5)

    bundle = {
        "version": "test-v0",
        "feature_names": ["planned_quantity", "haul_distance_m"],
        "categorical_maps": {},
        "model_p50": model_p50,
        "model_p90": model_p90,
    }
    path = tmp_path / "eta_test.joblib"
    joblib.dump(bundle, path)
    return str(path)


def test_load_and_predict_shape(eta_model_path):
    model = EtaModel.load(eta_model_path)
    assert model.version == "test-v0"
    result = model.predict({"planned_quantity": 420, "haul_distance_m": 60})
    assert set(result.keys()) == {"p50_min", "p90_min", "drivers"}
    assert result["p90_min"] >= result["p50_min"]
    assert isinstance(result["drivers"], list)
    assert len(result["drivers"]) <= 3


def test_load_missing_file_raises():
    with pytest.raises(RuntimeError):
        EtaModel.load("/nonexistent/path.joblib")
