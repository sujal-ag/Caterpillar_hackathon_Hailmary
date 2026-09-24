"""ML model loading and serving (plan.md §5.6, Phase 6 item 1; contracts/ml_runtime.md).

P1 ships the `ml_runtime` package; the backend only loads its models and calls them. A model
is the active `model_registry` row for its name, stored at `MODELS_DIR/{name}/{version}` and
checked against the row's sha256 before `load()`. Names: `eta`, `anomaly_EXCAVATOR`,
`anomaly_WHEEL_LOADER` (one anomaly model per family, HLD §4.5).

Any failure (package missing, no row, missing file, sha mismatch, load/predict raising or
returning the wrong shape) is logged and leaves that model UNAVAILABLE: ETA falls back to the
generic estimate and anomaly scoring is skipped, while the rules keep running. Models load
once at start; hot-swap is cut (24 h plan) — register a new version and restart.
"""

import hashlib
import importlib
import logging
from pathlib import Path

from sqlmodel import Session, select

from common.db.models import ModelRegistry
from common.log import jlog

log = logging.getLogger("edge.ml")
FAMILIES = ("EXCAVATOR", "WHEEL_LOADER")
_current: "MlAdapter | None" = None


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MlAdapter:
    def __init__(self, models_dir: str, db):
        self.models_dir, self.db = Path(models_dir), db
        self.eta = None
        self.anomaly: dict[str, object] = {}  # family -> AnomalyModel
        self.errors: dict[str, str] = {}

    def load(self) -> "MlAdapter":
        global _current
        try:
            runtime = importlib.import_module("ml_runtime")
        except ImportError as exc:
            self.errors["ml_runtime"] = f"package not installed: {exc}"
            jlog(log, logging.WARNING, "ml_unavailable", reason=self.errors["ml_runtime"])
        else:
            self.eta = self._load("eta", runtime.EtaModel)
            for fam in FAMILIES:
                model = self._load(f"anomaly_{fam}", runtime.AnomalyModel)
                if model is not None:
                    self.anomaly[fam] = model
        _current = self
        return self

    def _load(self, name: str, cls):
        with Session(self.db) as session:
            row = session.exec(
                select(ModelRegistry).where(
                    ModelRegistry.model_name == name,
                    ModelRegistry.active == True,  # noqa: E712 - SQL expression
                )
            ).first()
        if row is None:
            self.errors[name] = "no active model_registry row"
            return None
        path = self.models_dir / name / row.version
        try:
            if not path.is_file():
                raise FileNotFoundError(f"{path} missing")
            if sha256_of(path) != row.sha256:
                raise ValueError(f"sha256 mismatch for {path}")
            model = cls.load(str(path))
        except Exception as exc:  # noqa: BLE001 - any bad model file -> fallback, never a crash
            self.errors[name] = repr(exc)
            jlog(
                log,
                logging.ERROR,
                "model_rejected",
                model=name,
                version=row.version,
                error=repr(exc),
            )
            return None
        jlog(log, logging.INFO, "model_loaded", model=name, version=row.version)
        return model

    def predict_eta(self, feats: dict) -> dict | None:
        if self.eta is None:
            return None
        try:
            out = self.eta.predict(feats)
            p50, p90 = float(out["p50_min"]), float(out["p90_min"])
            drivers = [
                {"feature": str(d["feature"]), "effect_pct": float(d["effect_pct"])}
                for d in out.get("drivers", [])[:3]
            ]
        except Exception as exc:  # noqa: BLE001 - a bad prediction falls back to generic
            jlog(log, logging.ERROR, "eta_predict_failed", error=repr(exc))
            return None
        if not (p50 > 0 and p90 >= p50):
            jlog(log, logging.ERROR, "eta_predict_invalid", p50=p50, p90=p90)
            return None
        return {
            "p50_min": p50,
            "p90_min": p90,
            "drivers": drivers,
            "model_version": self.eta.version,
        }

    def score_anomaly(self, family: str, window: dict, baseline: dict) -> dict | None:
        model = self.anomaly.get(family)
        if model is None:
            return None
        try:
            out = model.score(window, baseline)
            score = float(out["score"])
        except Exception as exc:  # noqa: BLE001 - no score beats a crash; rules keep running
            jlog(log, logging.ERROR, "anomaly_score_failed", family=family, error=repr(exc))
            return None
        return {
            "score": score,
            "threshold_top3pct": float(model.threshold_top3pct),
            "top_features": list(out.get("top_features", []))[:3],
            "model_version": model.version,
        }

    def status(self) -> dict:
        return {
            "eta": "LOCAL" if self.eta is not None else "UNAVAILABLE",
            "anomaly": "LOCAL" if self.anomaly else "UNAVAILABLE",
        }

    def versions(self) -> dict:
        return {
            "eta": getattr(self.eta, "version", None),
            **{f"anomaly_{f}": getattr(m, "version", None) for f, m in self.anomaly.items()},
            "errors": dict(self.errors),
        }


def status() -> dict:
    """Model availability for `health` / `/system/status` (UNAVAILABLE until loaded)."""
    if _current is None:
        return {"eta": "UNAVAILABLE", "anomaly": "UNAVAILABLE"}
    return _current.status()
