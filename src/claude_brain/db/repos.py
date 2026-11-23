"""Repository container, importable without a display.

This lived in :mod:`claude_brain.app`, which imports tkinter at module scope, so
nothing could reach it from a hook or a CLI. Keeping it here is what makes the
ingest path headless.
"""

from __future__ import annotations

import contextlib
import sqlite3

from claude_brain.db.repositories import (
    CachedFileRepository,
    ConsolidationRepository,
    DayDigestRepository,
    ProjectRepository,
    SessionFileRepository,
    SessionRepository,
    SettingsRepository,
    VaultRepository,
)

__all__ = ["Repos", "open_repos"]


class Repos:
    """Every repository over one connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.settings = SettingsRepository(conn)
        self.projects = ProjectRepository(conn)
        self.vaults = VaultRepository(conn)
        self.sessions = SessionRepository(conn)
        self.session_files = SessionFileRepository(conn)
        self.consolidations = ConsolidationRepository(conn)
        self.cached_files = CachedFileRepository(conn)
        self.digests = DayDigestRepository(conn)

    @contextlib.contextmanager
    def transaction(self):
        """Group several writes into one commit.

        Every repository mutator commits individually, which is fine for a
        single row but means a multi-step write has no atomicity. Use this when
        a partial result would be wrong - e.g. a session and its file rows,
        where committing the session alone permanently loses the file list.
        """
        try:
            self.conn.execute("BEGIN")
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self) -> None:
        with contextlib.suppress(sqlite3.Error):
            self.conn.close()


def open_repos(conn: sqlite3.Connection | None = None) -> Repos:
    """Open repositories, running migrations first.

    Pass ``conn`` to reuse an existing connection (tests use an in-memory one).
    """
    from claude_brain.db import migrations
    from claude_brain.db.connection import get_connection

    connection = conn or get_connection()
    migrations.run(connection)
    return Repos(connection)
