"""Register a model file for the edge (plan.md §5.6; contracts/ml_runtime.md). Copies it to
MODELS_DIR/{name}/{version}, records its sha256 in `model_registry` and makes it the active
version of `name`. Restart edge-api to load it (hot-swap is cut).

    EDGE_DB_PATH=data/edge.db .venv/bin/python tools/register_model.py eta 2026.10.13 eta.joblib
Names: eta, anomaly_EXCAVATOR, anomaly_WHEEL_LOADER.
"""

import argparse
import shutil
import sys
from pathlib import Path

from sqlmodel import Session, select

from common.config import Settings
from common.db.models import ModelRegistry
from common.db.session import create_all, make_engine, sqlite_url
from common.timeutil import now_utc, to_site_iso
from edge.ml.adapter import sha256_of


def register(db, models_dir: str, name: str, version: str, src: Path) -> str:
    dest = Path(models_dir) / name / version
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    sha = sha256_of(dest)
    with Session(db) as session:
        for row in session.exec(select(ModelRegistry).where(ModelRegistry.model_name == name)):
            row.active = False
            session.add(row)
        session.merge(
            ModelRegistry(
                model_name=name,
                version=version,
                sha256=sha,
                trained_at=to_site_iso(now_utc()),
                active=True,
            )
        )
        session.commit()
    return sha


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name")
    ap.add_argument("version")
    ap.add_argument("file", type=Path)
    args = ap.parse_args()
    s = Settings()
    db = make_engine(sqlite_url(s.edge_db_path))
    create_all(db)
    sha = register(db, s.models_dir, args.name, args.version, args.file)
    print(f"registered {args.name} {args.version} sha256={sha}", file=sys.stderr)


if __name__ == "__main__":
    main()
