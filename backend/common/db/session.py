"""Engine + session factory. One engine per process; SQLite gets WAL + the pragmas plan.md
§4 asks for. All writes go through the single DbWriter (writer.py) — this module is also
used directly for reads, which use their own connections via the threadpool (plan.md §4)."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine, func, select

from common.config import Settings
from common.db import models as _models  # noqa: F401  (registers tables on SQLModel.metadata)
from common.timeutil import now_utc, to_site_iso


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if _is_sqlite(url) else {}
    engine = create_engine(url, connect_args=connect_args)
    if _is_sqlite(url):

        @event.listens_for(engine, "connect")
        def _set_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def sqlite_url(path: str | None = None) -> str:
    return f"sqlite:///{path or Settings().edge_db_path}"


_default_engine: Engine | None = None


def get_engine() -> Engine:
    global _default_engine
    if _default_engine is None:
        _default_engine = make_engine(sqlite_url())
    return _default_engine


def create_all(engine: Engine) -> None:
    """Create missing tables and stamp `schema_version`. Refuses a DB built by an older
    schema (create_all can't alter it): delete it and re-run tools/seed.py."""
    _models.SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        current = s.exec(select(func.max(_models.SchemaVersion.version))).one()
        if current is None and s.exec(select(func.count()).select_from(_models.Operator)).one():
            current = 1  # pre-versioning DB (Phase 1-3) that already holds data
        if current is not None and current < _models.SCHEMA_VERSION:
            raise RuntimeError(
                f"DB schema v{current} is older than v{_models.SCHEMA_VERSION}: delete "
                f"{engine.url.database} and re-run tools/seed.py"
            )
        if current is None:
            s.add(
                _models.SchemaVersion(
                    version=_models.SCHEMA_VERSION, applied_at=to_site_iso(now_utc())
                )
            )
            s.commit()


@contextmanager
def get_session(engine: Engine | None = None) -> Iterator[Session]:
    """Read-only convenience session. Writers must go through DbWriter (I7)."""
    with Session(engine or get_engine()) as session:
        yield session
