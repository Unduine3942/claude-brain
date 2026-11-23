"""Data access layer — one repository per domain entity."""

import json
import sqlite3
import uuid

from claude_brain.core.models import (
    Consolidation,
    DayDigest,
    Project,
    Session,
    SessionFile,
    Vault,
)


class SettingsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get(self, key: str, default: str = "") -> str:
        row = self._conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()


class ProjectRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def find_all(self) -> list[Project]:
        rows = self._conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
        return [_row_to_project(r) for r in rows]

    def find_by_path(self, path: str) -> Project | None:
        row = self._conn.execute("SELECT * FROM projects WHERE path = ?", (path,)).fetchone()
        return _row_to_project(row) if row else None

    def find_by_id(self, project_id: str) -> Project | None:
        row = self._conn.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        return _row_to_project(row) if row else None

    def upsert(self, name: str, path: str) -> Project:
        existing = self.find_by_path(path)
        if existing:
            return existing
        project_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO projects (project_id, name, path) VALUES (?, ?, ?)",
            (project_id, name, path),
        )
        self._conn.commit()
        return self.find_by_id(project_id)

    def touch(self, project_id: str) -> None:
        self._conn.execute(
            "UPDATE projects SET last_scanned = CURRENT_TIMESTAMP WHERE project_id = ?",
            (project_id,),
        )
        self._conn.commit()

    def delete(self, project_id: str) -> None:
        self._conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
        self._conn.commit()


class VaultRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def find_by_project(self, project_id: str) -> Vault | None:
        row = self._conn.execute(
            "SELECT * FROM vaults WHERE project_id = ?", (project_id,)
        ).fetchone()
        return _row_to_vault(row) if row else None

    def find_all(self) -> list[Vault]:
        rows = self._conn.execute("SELECT * FROM vaults").fetchall()
        return [_row_to_vault(r) for r in rows]

    def upsert(self, project_id: str, vault_path: str, status: str = "exists") -> Vault:
        existing = self.find_by_project(project_id)
        if existing:
            self._conn.execute(
                "UPDATE vaults SET vault_path = ?, status = ? WHERE vault_id = ?",
                (vault_path, status, existing.vault_id),
            )
            self._conn.commit()
            return self.find_by_project(project_id)
        vault_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO vaults (vault_id, project_id, vault_path, status) VALUES (?, ?, ?, ?)",
            (vault_id, project_id, vault_path, status),
        )
        self._conn.commit()
        return self.find_by_project(project_id)

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM vaults WHERE status='exists'").fetchone()
        return row[0]


class SessionRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def find_all(self) -> list[Session]:
        rows = self._conn.execute("SELECT * FROM sessions ORDER BY date DESC").fetchall()
        return [_row_to_session(r) for r in rows]

    def find_by_project(self, project_id: str) -> list[Session]:
        """Sessions for a project, **newest first**.

        The ordering is load-bearing: callers take ``[:5]`` to mean "the five
        most recent". Several previously indexed this list as if it were oldest
        first (``[-1]``, ``[-5:]``), which is why the UI reported a project's
        last session as 2026-06-24 when the latest was 2026-06-30.
        """
        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE project_id = ? ORDER BY date DESC, parsed_at DESC",
            (project_id,),
        ).fetchall()
        return [_row_to_session(r) for r in rows]

    def find_by_project_oldest_first(self, project_id: str) -> list[Session]:
        """The same rows in chronological order, for callers building a timeline."""
        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE project_id = ? ORDER BY date ASC, parsed_at ASC",
            (project_id,),
        ).fetchall()
        return [_row_to_session(r) for r in rows]

    def find_by_id(self, session_id: str) -> Session | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return _row_to_session(row) if row else None

    def exists_by_raw_path(self, raw_path: str) -> bool:
        """Return True if this .jsonl has ever been imported (even if the session was later deleted)."""
        row = self._conn.execute(
            "SELECT 1 FROM imported_paths WHERE raw_path = ?", (raw_path,)
        ).fetchone()
        return row is not None

    def create(self, session: Session, *, commit: bool = True) -> None:
        """Insert or replace a session.

        Pass ``commit=False`` when called inside ``Repos.transaction()``: an
        inner ``commit()`` would end the outer transaction, silently defeating
        its atomicity.
        """
        self._conn.execute(
            """INSERT OR REPLACE INTO sessions
               (session_id, project_id, source_dir, date, summary,
                file_count, tool_count, notes, raw_path, is_master)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session.session_id,
                session.project_id,
                session.source_dir,
                session.date,
                session.summary,
                session.file_count,
                session.tool_count,
                session.notes,
                session.raw_path,
                int(session.is_master),
            ),
        )
        if session.raw_path:
            self._conn.execute(
                "INSERT OR IGNORE INTO imported_paths (raw_path) VALUES (?)",
                (session.raw_path,),
            )
        if commit:
            self._conn.commit()

    def delete_by_ids(self, session_ids: list[str]) -> None:
        if not session_ids:
            return
        placeholders = ",".join("?" * len(session_ids))
        self._conn.execute(
            f"DELETE FROM sessions WHERE session_id IN ({placeholders})",
            session_ids,
        )
        self._conn.commit()

    def link_project(self, session_id: str, project_id: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET project_id = ? WHERE session_id = ?",
            (project_id, session_id),
        )
        self._conn.commit()

    def find_unlinked(self) -> list["Session"]:
        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE project_id IS NULL ORDER BY date DESC"
        ).fetchall()
        return [_row_to_session(r) for r in rows]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
        return row[0]

    def stats(self) -> dict:
        """Return aggregate counts in one query."""
        row = self._conn.execute(
            "SELECT COUNT(*) as total, COALESCE(SUM(tool_count), 0) as tools,"
            " COALESCE(SUM(file_count), 0) as files FROM sessions"
        ).fetchone()
        return {"total": row["total"], "tools": row["tools"], "files": row["files"]}

    def recent(self, limit: int = 5) -> list[Session]:
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_session(r) for r in rows]


class SessionFileRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def find_by_session(self, session_id: str) -> list[SessionFile]:
        rows = self._conn.execute(
            "SELECT * FROM session_files WHERE session_id = ?", (session_id,)
        ).fetchall()
        return [
            SessionFile(file_id=r["file_id"], session_id=r["session_id"], file_path=r["file_path"])
            for r in rows
        ]

    def create_many(self, files: list[SessionFile], *, commit: bool = True) -> None:
        """Insert file rows, ignoring duplicates.

        `INSERT OR IGNORE` only actually dedupes because migration 004 added a
        UNIQUE index on (session_id, file_path); file_id is a fresh uuid4 on
        every call, so before that index the same pair inserted twice.
        """
        self._conn.executemany(
            "INSERT OR IGNORE INTO session_files (file_id, session_id, file_path) VALUES (?, ?, ?)",
            [(f.file_id, f.session_id, f.file_path) for f in files],
        )
        if commit:
            self._conn.commit()


class ConsolidationRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def find_by_project(self, project_id: str) -> list[Consolidation]:
        rows = self._conn.execute(
            "SELECT * FROM consolidations WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        return [_row_to_consolidation(r) for r in rows]

    def create(self, c: Consolidation) -> None:
        self._conn.execute(
            """INSERT INTO consolidations
               (consolidation_id, project_id, session_ids, markdown_path)
               VALUES (?, ?, ?, ?)""",
            (c.consolidation_id, c.project_id, json.dumps(c.session_ids), c.markdown_path),
        )
        self._conn.commit()


# --- row mappers ---


def _row_to_project(r: sqlite3.Row) -> Project:
    return Project(
        project_id=r["project_id"],
        name=r["name"],
        path=r["path"],
        created_at=r["created_at"],
        last_scanned=r["last_scanned"],
    )


def _row_to_vault(r: sqlite3.Row) -> Vault:
    return Vault(
        vault_id=r["vault_id"],
        project_id=r["project_id"],
        vault_path=r["vault_path"],
        status=r["status"],
        created_at=r["created_at"],
    )


def _row_to_session(r: sqlite3.Row) -> Session:
    return Session(
        session_id=r["session_id"],
        project_id=r["project_id"],
        source_dir=r["source_dir"],
        date=r["date"] or "",
        summary=r["summary"] or "",
        file_count=r["file_count"] or 0,
        tool_count=r["tool_count"] or 0,
        notes=r["notes"],
        raw_path=r["raw_path"],
        is_master=bool(r["is_master"]) if r["is_master"] is not None else False,
    )


def _row_to_consolidation(r: sqlite3.Row) -> Consolidation:
    return Consolidation(
        consolidation_id=r["consolidation_id"],
        project_id=r["project_id"],
        session_ids=json.loads(r["session_ids"]),
        markdown_path=r["markdown_path"],
        created_at=r["created_at"],
    )


class CachedFileRepository:
    """Ingest dedupe keyed on (path, mtime, size).

    Supersedes `imported_paths`, which only recorded that a path had *ever* been
    seen. That had two failure modes: a transcript deleted from the store could
    never be re-imported (917 blocking entries against 872 sessions on the live
    database), and a transcript that had *grown* since first read was frozen at
    that first snapshot - which is wrong for any session still in progress.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def is_unchanged(self, path: str, mtime: float, size: int) -> bool:
        row = self._conn.execute(
            "SELECT mtime, size FROM cached_files WHERE path = ?", (path,)
        ).fetchone()
        if row is None:
            return False
        # mtime is a float; compare with tolerance for filesystem granularity.
        return abs(float(row["mtime"]) - mtime) < 0.001 and int(row["size"]) == size

    def remember(self, path: str, mtime: float, size: int, session_id: str | None = None) -> None:
        self._conn.execute(
            """INSERT INTO cached_files (path, mtime, size, session_id, ingested_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(path) DO UPDATE SET
                   mtime = excluded.mtime,
                   size = excluded.size,
                   session_id = excluded.session_id,
                   ingested_at = CURRENT_TIMESTAMP""",
            (path, mtime, size, session_id),
        )
        self._conn.commit()

    def forget(self, path: str) -> None:
        self._conn.execute("DELETE FROM cached_files WHERE path = ?", (path,))
        self._conn.commit()

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM cached_files").fetchone()[0])


def _row_to_digest(row: sqlite3.Row) -> DayDigest:
    keys = row.keys()
    return DayDigest(
        digest_id=row["digest_id"],
        project_id=row["project_id"],
        date=row["date"],
        project_name=row["project_name"],
        worktree=row["worktree"],
        topic=row["topic"] if "topic" in keys else None,
        tasks=row["tasks"],
        files=row["files"],
        tools=row["tools"],
        notes=row["notes"],
        message_count=int(row["message_count"] or 0) if "message_count" in keys else 0,
        source_path=row["source_path"],
        source_mtime=float(row["source_mtime"] or 0.0),
        is_curated=bool(row["is_curated"]) if "is_curated" in keys else False,
    )


class DayDigestRepository:
    """Day digests, keyed on their source file.

    ``source_path`` carries the UNIQUE index rather than ``(worktree, date)``:
    the file is the artifact we actually read, and two of the 164 real files
    have no worktree at all. Re-importing a file that changed updates its row.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def find_by_source(self, source_path: str) -> DayDigest | None:
        row = self._conn.execute(
            "SELECT * FROM day_digests WHERE source_path = ?", (source_path,)
        ).fetchone()
        return _row_to_digest(row) if row else None

    def find_by_project(self, project_id: str, limit: int = 50) -> list[DayDigest]:
        """Newest first — matching SessionRepository.find_by_project."""
        rows = self._conn.execute(
            "SELECT * FROM day_digests WHERE project_id = ? ORDER BY date DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [_row_to_digest(r) for r in rows]

    def is_unchanged(self, source_path: str, mtime: float) -> bool:
        row = self._conn.execute(
            "SELECT source_mtime FROM day_digests WHERE source_path = ?", (source_path,)
        ).fetchone()
        if row is None or row["source_mtime"] is None:
            return False
        return abs(float(row["source_mtime"]) - mtime) < 0.001

    def upsert(self, digest: DayDigest, *, commit: bool = True) -> None:
        self._conn.execute(
            """INSERT INTO day_digests
               (digest_id, project_id, date, project_name, worktree, topic,
                tasks, files, tools, notes, message_count, source_path,
                source_mtime, is_curated, parsed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(source_path) DO UPDATE SET
                   project_id    = excluded.project_id,
                   date          = excluded.date,
                   project_name  = excluded.project_name,
                   worktree      = excluded.worktree,
                   topic         = excluded.topic,
                   tasks         = excluded.tasks,
                   files         = excluded.files,
                   tools         = excluded.tools,
                   notes         = excluded.notes,
                   message_count = excluded.message_count,
                   source_mtime  = excluded.source_mtime,
                   is_curated    = excluded.is_curated,
                   parsed_at     = CURRENT_TIMESTAMP""",
            (
                digest.digest_id,
                digest.project_id,
                digest.date,
                digest.project_name,
                digest.worktree,
                digest.topic,
                digest.tasks,
                digest.files,
                digest.tools,
                digest.notes,
                digest.message_count,
                digest.source_path,
                digest.source_mtime,
                int(digest.is_curated),
            ),
        )
        if commit:
            self._conn.commit()

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM day_digests").fetchone()[0])

    def count_unlinked(self) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM day_digests WHERE project_id IS NULL"
            ).fetchone()[0]
        )
