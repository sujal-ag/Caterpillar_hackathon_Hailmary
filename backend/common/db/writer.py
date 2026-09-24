"""Single writer task (plan.md §4, §6): every write to the edge DB goes through one
thread with one connection, serialised by a queue. This is what "no `database is locked`"
means for SQLite — there is never a second writer to collide with. Reads use their own
sessions (session.py) and are unaffected.

A unit of work is `Callable[[Session], T]`. It runs inside one transaction: if it returns
normally the transaction commits; if it raises, nothing it wrote is persisted (I7 — an
outbox row inserted in the same unit of work as its entity either both land or neither does).

Telemetry (`submit_telemetry`) is fire-and-forget and batched: rows queue up and are bulk
inserted together, either when `batch_max` is reached or after `batch_window_s` of
inactivity. It is not part of any transaction with other writes and never touches the
outbox (I8).
"""

import contextlib
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future, InvalidStateError
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session

from common.db.models import TelemetrySample

_SENTINEL = object()

UnitOfWork = Callable[[Session], Any]


class DbWriter:
    def __init__(self, engine: Engine, batch_window_s: float = 1.0, batch_max: int = 200):
        self._engine = engine
        self._batch_window_s = batch_window_s
        self._batch_max = batch_max
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="db-writer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread is None:
            return
        self._queue.put(_SENTINEL)
        self._thread.join(timeout=timeout)
        self._thread = None

    def submit(self, fn: UnitOfWork) -> Future:
        """Enqueue a unit of work; returns a Future (thread-safe, usable from sync or async
        code — async callers `await asyncio.wrap_future(writer.submit(fn))`)."""
        fut: Future = Future()
        self._queue.put(("uow", fn, fut))
        return fut

    def submit_telemetry(self, values: dict) -> None:
        """Fire-and-forget: batched, never enters the outbox (I8)."""
        self._queue.put(("telemetry", values))

    def _run(self) -> None:
        pending: list[dict] = []
        while True:
            try:
                item = self._queue.get(timeout=self._batch_window_s)
            except queue.Empty:
                self._flush_telemetry(pending)
                pending = []
                continue

            if item is _SENTINEL:
                self._flush_telemetry(pending)
                return

            kind = item[0]
            if kind == "telemetry":
                pending.append(item[1])
                if len(pending) >= self._batch_max:
                    self._flush_telemetry(pending)
                    pending = []
                continue

            # A unit of work: flush pending telemetry first (rough ordering), then run it
            # in its own transaction.
            self._flush_telemetry(pending)
            pending = []
            _, fn, fut = item
            try:
                with Session(self._engine) as session:
                    result = fn(session)
                    session.commit()
                ok, value = True, result
            except BaseException as exc:  # noqa: BLE001 - must reach the caller, whatever it is
                ok, value = False, exc
            # A caller that stopped waiting (cancelled request, shutdown) cancels its future;
            # setting it then raises, which used to kill this thread and every later write.
            with contextlib.suppress(InvalidStateError):
                fut.set_result(value) if ok else fut.set_exception(value)

    def _flush_telemetry(self, rows: list[dict]) -> None:
        if not rows:
            return
        with Session(self._engine) as session:
            session.bulk_insert_mappings(TelemetrySample, rows)
            session.commit()
