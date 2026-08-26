"""SQLite connection handling.

Every caller gets its own connection. That is what makes the reveal-consumption
test meaningful: 50 concurrent consumers really do contend for the same write
lock rather than serialising behind one shared handle.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from ..config import get_settings

BUSY_TIMEOUT_MS = 10_000

P = ParamSpec("P")
T = TypeVar("T")


def connect(path: Path | None = None) -> sqlite3.Connection:
    settings = get_settings()
    connection = sqlite3.connect(
        path or settings.database_path,
        isolation_level=None,  # explicit transaction control; see `immediate()`
        check_same_thread=False,
        timeout=BUSY_TIMEOUT_MS / 1000,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return connection


@contextmanager
def connection(path: Path | None = None):
    handle = connect(path)
    try:
        yield handle
    finally:
        handle.close()


@contextmanager
def immediate(handle: sqlite3.Connection):
    """Run a write transaction that takes the reserved lock up front.

    A deferred transaction would only escalate to a write lock at the first
    mutation, which allows two readers to both observe `state = 'READY'` before
    either writes. `BEGIN IMMEDIATE` serialises the whole read-modify-write.
    """
    handle.execute("BEGIN IMMEDIATE")
    try:
        yield handle
    except BaseException:
        handle.execute("ROLLBACK")
        raise
    handle.execute("COMMIT")


def initialise(path: Path | None = None) -> None:
    schema = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")
    with connection(path) as handle:
        handle.executescript(schema)


async def run(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Execute a blocking db function on a worker thread with a fresh connection."""

    def runner() -> T:
        with connection() as handle:
            return function(handle, *args, **kwargs)

    return await asyncio.to_thread(runner)
