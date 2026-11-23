"""SQLite connection factory.

Two details here are easy to get wrong and both were wrong before:

* **Pragma order.** ``journal_mode`` persists in the database file, but
  ``synchronous`` is *per connection* and defaults to ``FULL``. Setting
  ``synchronous`` first matters because switching into WAL is itself a write, so
  at ``FULL`` SQLite fsyncs for its own transition. It must then be set on every
  writer — including the migration connection, which on a new database is the
  first thing to touch the file.
* **Readers should be read-only.** Opening with ``mode=ro`` plus a busy timeout
  means a reader never blocks during a write transaction, which is the point of
  WAL in the first place.

The database deliberately lives in ``%LOCALAPPDATA%``, never inside an Obsidian
vault: vaults are commonly synced, and a sync client rewriting pages underneath
an open SQLite file corrupts it without even needing a conflict.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from claude_brain.config import APP_DIR, CONSOLIDATIONS_DIR, DB_PATH, LOG_DIR

__all__ = [
    "apply_write_pragmas",
    "backup_database",
    "get_connection",
    "get_readonly_connection",
]

_BUSY_TIMEOUT_MS = 5000


def apply_write_pragmas(conn: sqlite3.Connection) -> None:
    """Pragmas every writing connection needs, in the order that matters."""
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")


def _ensure_dirs() -> None:
    for directory in (APP_DIR, LOG_DIR, CONSOLIDATIONS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a writable connection.

    ``db_path`` exists so tests and the CLI can target another database; the
    previous signature hardcoded the global path and could not be redirected.
    """
    if db_path is None:
        _ensure_dirs()
        db_path = DB_PATH

    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    apply_write_pragmas(conn)
    return conn


def get_readonly_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a read-only connection that never blocks on writers.

    Falls back to a writable connection when the file does not exist yet, since
    ``mode=ro`` refuses to create one.
    """
    target = Path(db_path) if db_path is not None else DB_PATH
    if not target.exists():
        return get_connection(target)

    conn = sqlite3.connect(
        f"file:{target.as_posix()}?mode=ro", uri=True, timeout=30, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    return conn


def backup_database(destination: Path | str, db_path: Path | str | None = None) -> Path:
    """Copy the database safely using SQLite's online backup API.

    Never file-copy a live WAL database: the ``.db`` alone omits committed data
    that exists only in the ``-wal``, which can make a healthy database look
    corrupt. Deleting a database must likewise take its ``-wal``/``-shm`` with it.
    """
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)

    source = get_connection(db_path)
    try:
        backup = sqlite3.connect(str(target))
        try:
            with backup:
                source.backup(backup)
        finally:
            backup.close()
    finally:
        source.close()
    return target
