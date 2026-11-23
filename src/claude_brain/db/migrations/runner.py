"""Versioned, checksummed schema migrations.

The previous implementation ran one ``schema.sql`` through ``executescript`` on
every launch with no version tracking at all (``PRAGMA user_version`` was 0), so
there was no record of what had been applied and no way to detect drift.

Two properties earn their keep over the usual ``user_version`` counter:

* **Checksums.** Each applied migration's SHA-256 is recorded, so editing a file
  that has already run is detectable. ``user_version`` cannot see that.
* **Duplicate-number detection.** Two branches that both add ``007_`` cannot see
  each other while developed in parallel, and the clash only becomes visible
  once both land. Without this check one of them is silently skipped forever on
  any database that already applied the other.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = ["Migration", "MigrationError", "applied", "discover", "run", "verify"]

MIGRATIONS_DIR = Path(__file__).parent
_FILENAME_RE = re.compile(r"^(\d{3,})_([A-Za-z0-9_.-]+)\.sql$")

_VERSION_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS _schema_version (
    version    INTEGER PRIMARY KEY,
    filename   TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    checksum   TEXT NOT NULL
)
"""


class MigrationError(RuntimeError):
    """Raised for a problem that must stop startup rather than be worked around."""


@dataclass(frozen=True)
class Migration:
    version: int
    filename: str
    path: Path
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover(directory: Path | None = None) -> list[Migration]:
    """Load migrations in version order, rejecting duplicate version numbers."""
    target = directory or MIGRATIONS_DIR
    found: dict[int, Migration] = {}

    for path in sorted(target.glob("*.sql")):
        match = _FILENAME_RE.match(path.name)
        if not match:
            log.warning("ignoring migration with unexpected name: %s", path.name)
            continue
        version = int(match.group(1))
        if version in found:
            raise MigrationError(
                f"duplicate migration version {version:03d}: "
                f"{found[version].filename} and {path.name}. "
                "Renumber one of them — otherwise whichever is applied first "
                "permanently masks the other."
            )
        found[version] = Migration(
            version=version,
            filename=path.name,
            path=path,
            sql=path.read_text(encoding="utf-8"),
        )

    return [found[version] for version in sorted(found)]


def applied(conn: sqlite3.Connection) -> dict[int, str]:
    """Map of applied version -> recorded checksum."""
    conn.execute(_VERSION_TABLE_DDL)
    rows = conn.execute("SELECT version, checksum FROM _schema_version").fetchall()
    return {int(row[0]): str(row[1]) for row in rows}


def verify(conn: sqlite3.Connection, directory: Path | None = None) -> list[str]:
    """Report migrations whose file changed after being applied."""
    recorded = applied(conn)
    warnings: list[str] = []
    for migration in discover(directory):
        expected = recorded.get(migration.version)
        if expected is not None and expected != migration.checksum:
            warnings.append(
                f"{migration.filename} was modified after it was applied "
                f"(recorded {expected[:12]}, now {migration.checksum[:12]})"
            )
    return warnings


def _ensure_legacy_columns(conn: sqlite3.Connection) -> None:
    """Add columns that predate the migration system.

    SQLite has no ``ADD COLUMN IF NOT EXISTS``, and a database created before
    ``is_master`` existed would otherwise fail the baseline. Kept deliberately
    small: new columns belong in a numbered migration.
    """
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    except sqlite3.Error:
        return
    if columns and "is_master" not in columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN is_master INTEGER DEFAULT 0")
        log.info("added legacy column sessions.is_master")


def run(conn: sqlite3.Connection, directory: Path | None = None) -> list[int]:
    """Apply every pending migration. Returns the versions newly applied.

    Each migration runs inside its own transaction and is recorded in the same
    transaction, so a failure cannot leave a migration half-applied but marked
    as done.
    """
    from claude_brain.db.connection import apply_write_pragmas

    # The migration connection is often the first to touch a new database, so
    # without this the whole chain would run at SQLite's defaults.
    apply_write_pragmas(conn)

    conn.execute(_VERSION_TABLE_DDL)
    conn.commit()
    _ensure_legacy_columns(conn)
    conn.commit()

    already = applied(conn)
    newly: list[int] = []

    for migration in discover(directory):
        if migration.version in already:
            continue
        try:
            conn.execute("BEGIN")
            conn.executescript(migration.sql)
            conn.execute(
                "INSERT INTO _schema_version (version, filename, checksum) VALUES (?, ?, ?)",
                (migration.version, migration.filename, migration.checksum),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise MigrationError(f"migration {migration.filename} failed: {exc}") from exc
        newly.append(migration.version)
        log.info("applied migration %s", migration.filename)

    for warning in verify(conn, directory):
        log.warning("schema drift: %s", warning)

    _seed_settings(conn)
    return newly


def _seed_settings(conn: sqlite3.Connection) -> None:
    """Seed default settings, if this migration set created a place to put them.

    Guarded because ``run()`` accepts an arbitrary migration directory (tests
    use one), and seeding must not be the step that fails when the baseline
    schema was not part of that set.
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_settings'"
    ).fetchone()
    if not exists:
        return

    from claude_brain.config import PROJECTS_ROOT_DEFAULT

    defaults = {"projects_root": str(PROJECTS_ROOT_DEFAULT)}
    for key, value in defaults.items():
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
