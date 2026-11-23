"""One-off repair of data the old ingest produced.

Dry-run by default: :func:`plan_repair` only counts, and :func:`apply_repair`
takes a backup before touching anything. The store is the *only* surviving copy
of nearly all its history — 871 of 872 recorded transcripts no longer exist on
disk — so deletion is deliberately narrow.

What is purged, and what is deliberately kept:

* **Purged: sidechain rows.** Subagent and ephemeral workflow transcripts swept
  in by a bare ``rglob``. All of them have no usable summary, so nothing is lost.
* **Kept: dangling rows that still carry a summary.** Their transcript is gone,
  so this row is the only record that the work happened. Their summary text is
  *misattributed* — it came from the injected-digest bug and describes some
  other session — so they are flagged rather than trusted or deleted.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = ["RepairAction", "RepairPlan", "apply_repair", "plan_repair"]

#: Marker appended to notes whose summary provenance cannot be trusted.
UNTRUSTED_MARKER = "[summary provenance unreliable: captured before the injected-digest fix]"

_SIDECHAIN_PREDICATE = """
    is_master = 0
    AND (
        COALESCE(raw_path, '') LIKE '%subagents%'
        OR COALESCE(raw_path, '') LIKE '%wf_%'
        OR session_id LIKE 'agent-%'
    )
"""


@dataclass(frozen=True)
class RepairAction:
    """One repair step: how many rows it affects and the statements to run."""

    name: str
    description: str
    count: int
    statements: tuple[tuple[str, tuple], ...] = ()
    destructive: bool = False

    @property
    def applicable(self) -> bool:
        return self.count > 0


@dataclass(frozen=True)
class RepairPlan:
    actions: tuple[RepairAction, ...] = ()
    kept: dict[str, int] = field(default_factory=dict)

    @property
    def applicable(self) -> tuple[RepairAction, ...]:
        return tuple(action for action in self.actions if action.applicable)

    @property
    def total_rows(self) -> int:
        return sum(action.count for action in self.applicable)

    @property
    def destructive_rows(self) -> int:
        return sum(a.count for a in self.applicable if a.destructive)

    def describe(self) -> str:
        lines = ["Repair plan:"]
        if not self.applicable:
            lines.append("  nothing to do")
        for action in self.applicable:
            flag = "DELETE" if action.destructive else "update"
            # ASCII only: this prints to a Windows console that mangles en/em dashes.
            lines.append(f"  [{flag}] {action.count:>5}  {action.name} - {action.description}")
        if self.kept:
            lines.append("Deliberately kept:")
            for label, count in self.kept.items():
                lines.append(f"          {count:>5}  {label}")
        return "\n".join(lines)


def _scalar(conn: sqlite3.Connection, sql: str, params: Sequence = ()) -> int:
    try:
        row = conn.execute(sql, tuple(params)).fetchone()
    except sqlite3.Error as exc:  # pragma: no cover - missing table on an old DB
        log.warning("repair count failed (%s): %s", sql.strip()[:60], exc)
        return 0
    return int(row[0]) if row else 0


def plan_repair(conn: sqlite3.Connection) -> RepairPlan:
    """Inspect the database and return the planned repairs. Read-only."""
    actions: list[RepairAction] = []

    sidechains = _scalar(conn, f"SELECT COUNT(*) FROM sessions WHERE {_SIDECHAIN_PREDICATE}")
    actions.append(
        RepairAction(
            name="purge_sidechains",
            description="subagent/workflow transcripts stored as sessions",
            count=sidechains,
            statements=((f"DELETE FROM sessions WHERE {_SIDECHAIN_PREDICATE}", ()),),
            destructive=True,
        )
    )

    placeholder_notes = _scalar(
        conn, "SELECT COUNT(*) FROM sessions WHERE TRIM(COALESCE(notes, '')) = '-'"
    )
    actions.append(
        RepairAction(
            name="clear_placeholder_notes",
            description="notes that are the literal '-' from the digest template",
            count=placeholder_notes,
            statements=(
                ("UPDATE sessions SET notes = NULL WHERE TRIM(COALESCE(notes, '')) = '-'", ()),
            ),
        )
    )

    # Only LEGACY captures are suspect. A session ingested by the current
    # pipeline is always recorded in cached_files, and its summary is derived
    # from its own prompts — flagging those would mislabel good data, and
    # would re-fire on every future session.
    _LEGACY_SUMMARY = """
        is_master = 0
        AND TRIM(COALESCE(summary, '')) NOT IN ('', '(no summary)')
        AND COALESCE(notes, '') NOT LIKE ?
        AND NOT EXISTS (
            SELECT 1 FROM cached_files cf WHERE cf.session_id = sessions.session_id
        )
    """
    untrusted = _scalar(
        conn,
        f"SELECT COUNT(*) FROM sessions WHERE {_LEGACY_SUMMARY}",
        (f"%{UNTRUSTED_MARKER}%",),
    )
    actions.append(
        RepairAction(
            name="flag_untrusted_summaries",
            description="mark legacy summaries captured from another session's digest",
            count=untrusted,
            statements=(
                (
                    f"""UPDATE sessions
                        SET notes = TRIM(COALESCE(notes, '') || ' ' || ?)
                        WHERE {_LEGACY_SUMMARY}""",
                    (UNTRUSTED_MARKER, f"%{UNTRUSTED_MARKER}%"),
                ),
            ),
        )
    )

    orphan_paths = _scalar(
        conn,
        """SELECT COUNT(*) FROM imported_paths ip
           WHERE NOT EXISTS (SELECT 1 FROM sessions s WHERE s.raw_path = ip.raw_path)""",
    )
    actions.append(
        RepairAction(
            name="unblock_imported_paths",
            description="paths blocking re-import with no session row behind them",
            count=orphan_paths,
            statements=(
                (
                    """DELETE FROM imported_paths
                       WHERE NOT EXISTS (
                           SELECT 1 FROM sessions s WHERE s.raw_path = imported_paths.raw_path
                       )""",
                    (),
                ),
            ),
            destructive=True,
        )
    )

    # Defensive: session_files cascades on delete, but only when the *caller's*
    # connection has `PRAGMA foreign_keys = ON`. Repair must not depend on that.
    orphan_files = _scalar(
        conn,
        """SELECT COUNT(*) FROM session_files sf
           WHERE NOT EXISTS (
               SELECT 1 FROM sessions s WHERE s.session_id = sf.session_id
           )""",
    )
    actions.append(
        RepairAction(
            name="purge_orphan_session_files",
            description="file rows whose session no longer exists",
            count=orphan_files,
            statements=(
                (
                    """DELETE FROM session_files
                       WHERE NOT EXISTS (
                           SELECT 1 FROM sessions s
                           WHERE s.session_id = session_files.session_id
                       )""",
                    (),
                ),
            ),
            destructive=True,
        )
    )

    # A filesystem root is not a project. One real digest records a session
    # started at `C:\`, and the auto-registration that followed produced a
    # project whose directory contains every other project - so every
    # enclosing-project lookup on the drive matched it. Deleting the project
    # row sets its sessions and digests back to project_id NULL (ON DELETE SET
    # NULL), which is the honest state: that work has no project.
    root_projects = _scalar(
        conn,
        """SELECT COUNT(*) FROM projects
           WHERE TRIM(COALESCE(path, '')) = ''
              OR path IN ('/', '\')
              OR path GLOB '[A-Za-z]:'
              OR path GLOB '[A-Za-z]:[\\/]'""",
    )
    actions.append(
        RepairAction(
            name="unregister_filesystem_roots",
            description="projects registered at a drive root, which enclose every other project",
            count=root_projects,
            statements=(
                (
                    """DELETE FROM projects
                       WHERE TRIM(COALESCE(path, '')) = ''
                          OR path IN ('/', '\')
                          OR path GLOB '[A-Za-z]:'
                          OR path GLOB '[A-Za-z]:[\\/]'""",
                    (),
                ),
            ),
            destructive=True,
        )
    )

    bad_vaults = _scalar(conn, "SELECT COUNT(*) FROM vaults WHERE vault_path LIKE '%.obsidian'")
    actions.append(
        RepairAction(
            name="normalise_vault_paths",
            description="vault_path holding the .obsidian subdirectory, not the project root",
            count=bad_vaults,
            statements=(
                (
                    """UPDATE vaults
                       SET vault_path = (
                           SELECT p.path FROM projects p WHERE p.project_id = vaults.project_id
                       )
                       WHERE vault_path LIKE '%.obsidian'
                         AND EXISTS (
                             SELECT 1 FROM projects p WHERE p.project_id = vaults.project_id
                         )""",
                    (),
                ),
            ),
        )
    )

    kept = {
        "dangling rows with a summary (only surviving record)": _scalar(
            conn,
            f"""SELECT COUNT(*) FROM sessions
                WHERE is_master = 0
                  AND NOT ({_SIDECHAIN_PREDICATE})
                  AND TRIM(COALESCE(summary, '')) NOT IN ('', '(no summary)')
                  AND NOT EXISTS (
                      SELECT 1 FROM cached_files cf
                      WHERE cf.session_id = sessions.session_id
                  )""",
        ),
        "master/consolidated sessions": _scalar(
            conn, "SELECT COUNT(*) FROM sessions WHERE is_master = 1"
        ),
    }

    return RepairPlan(actions=tuple(actions), kept=kept)


@dataclass(frozen=True)
class RepairReport:
    applied: tuple[str, ...] = ()
    rows_changed: int = 0
    backup: Path | None = None
    sessions_before: int = 0
    sessions_after: int = 0

    def __str__(self) -> str:
        return (
            f"applied={len(self.applied)} rows_changed={self.rows_changed} "
            f"sessions {self.sessions_before} -> {self.sessions_after}"
            + (f" backup={self.backup}" if self.backup else "")
        )


def apply_repair(
    conn: sqlite3.Connection,
    *,
    plan: RepairPlan | None = None,
    backup_to: Path | str | None = None,
    allow_destructive: bool = True,
) -> RepairReport:
    """Apply the repair plan inside a single transaction.

    ``backup_to`` is strongly recommended for the live database; it uses the
    online backup API rather than a file copy. Set ``allow_destructive=False``
    to run only the non-deleting steps.
    """
    repair_plan = plan or plan_repair(conn)
    before = _scalar(conn, "SELECT COUNT(*) FROM sessions")

    backup_path: Path | None = None
    if backup_to is not None:
        from claude_brain.db.connection import backup_database

        backup_path = backup_database(backup_to)
        log.info("repair backup written to %s", backup_path)

    applied: list[str] = []
    changed = 0
    try:
        conn.execute("BEGIN")
        for action in repair_plan.applicable:
            if action.destructive and not allow_destructive:
                continue
            for sql, params in action.statements:
                cursor = conn.execute(sql, params)
                changed += max(cursor.rowcount, 0)
            applied.append(action.name)
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise

    after = _scalar(conn, "SELECT COUNT(*) FROM sessions")
    return RepairReport(
        applied=tuple(applied),
        rows_changed=changed,
        backup=backup_path,
        sessions_before=before,
        sessions_after=after,
    )
