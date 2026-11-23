"""Tests for the versioned migration runner."""

from __future__ import annotations

import contextlib
import sqlite3

import pytest

from claude_brain.db import migrations
from claude_brain.db.migrations import runner


@contextlib.contextmanager
def _fresh():
    """An empty in-memory database, closed on the way out.

    Closed explicitly rather than left to the garbage collector: an open
    connection is a ResourceWarning here and a held file lock as soon as any of
    these tests moves to a real file, which on Windows breaks tmp_path cleanup.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------


def test_applies_all_migrations_in_order():
    with _fresh() as conn:
        applied = migrations.run(conn)
        assert applied == sorted(applied)
        assert applied[0] == 1


def test_rerun_is_a_noop():
    with _fresh() as conn:
        migrations.run(conn)
        assert migrations.run(conn) == []


def test_expected_tables_exist():
    with _fresh() as conn:
        migrations.run(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "_schema_version",
            "projects",
            "sessions",
            "session_files",
            "vaults",
            "day_digests",
            "cached_files",
            "imported_paths",
            "app_settings",
        } <= tables


def test_version_table_records_filename_and_checksum():
    with _fresh() as conn:
        migrations.run(conn)
        rows = conn.execute("SELECT version, filename, checksum FROM _schema_version").fetchall()
        assert rows
        for row in rows:
            assert row["filename"].endswith(".sql")
            assert len(row["checksum"]) == 64, "expected a sha256 hex digest"


def test_settings_are_seeded():
    with _fresh() as conn:
        migrations.run(conn)
        value = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'projects_root'"
        ).fetchone()
        assert value is not None


def test_pragmas_applied_to_the_migration_connection():
    """`synchronous` is per-connection and defaults to FULL, so the migration
    connection must set it too - on a new database it is the first writer."""
    with _fresh() as conn:
        migrations.run(conn)
        assert int(conn.execute("PRAGMA synchronous").fetchone()[0]) == 1  # NORMAL


# --------------------------------------------------------------------------
# Discovery and integrity
# --------------------------------------------------------------------------


def test_discover_rejects_duplicate_version_numbers(tmp_path):
    """Two branches both adding 007_ cannot see each other until both land."""
    (tmp_path / "007_alpha.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "007_beta.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(runner.MigrationError, match="duplicate migration version 007"):
        runner.discover(tmp_path)


def test_discover_ignores_unexpected_filenames(tmp_path):
    (tmp_path / "001_ok.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "notes.sql").write_text("SELECT 1;", encoding="utf-8")
    assert [m.filename for m in runner.discover(tmp_path)] == ["001_ok.sql"]


def test_verify_detects_a_migration_edited_after_it_was_applied(tmp_path):
    path = tmp_path / "001_thing.sql"
    path.write_text("CREATE TABLE t (x);", encoding="utf-8")
    with _fresh() as conn:
        runner.run(conn, tmp_path)
        assert runner.verify(conn, tmp_path) == []

        path.write_text("CREATE TABLE t (x, y);", encoding="utf-8")
        warnings = runner.verify(conn, tmp_path)

        assert len(warnings) == 1
        assert "modified after it was applied" in warnings[0]


def test_failed_migration_is_not_recorded_as_applied(tmp_path):
    (tmp_path / "001_bad.sql").write_text("CREATE TABLE ok (x);", encoding="utf-8")
    (tmp_path / "002_broken.sql").write_text("THIS IS NOT SQL;", encoding="utf-8")
    with _fresh() as conn:
        with pytest.raises(runner.MigrationError):
            runner.run(conn, tmp_path)

        applied = runner.applied(conn)
        assert 1 in applied, "the good migration should have committed"
        assert 2 not in applied, "a failed migration must not be marked applied"


def test_legacy_database_gains_is_master_column():
    """A database created before `is_master` existed must still migrate.

    The table is created with the full historical shape minus `is_master`,
    because that is the only legacy state that ever existed. Note the trap this
    guards: `001_baseline.sql` indexes `sessions(date)`, so a pre-existing
    `sessions` table missing any indexed column makes the baseline fail with
    `no such column` - which is why legacy column fixups run *before* the
    numbered migrations.
    """
    with _fresh() as conn:
        conn.execute(
            """CREATE TABLE sessions (
                   session_id TEXT PRIMARY KEY,
                   project_id TEXT,
                   source_dir TEXT,
                   date       TEXT,
                   summary    TEXT,
                   file_count INTEGER DEFAULT 0,
                   tool_count INTEGER DEFAULT 0,
                   notes      TEXT,
                   raw_path   TEXT,
                   parsed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        conn.commit()
        assert "is_master" not in {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}

        migrations.run(conn)

        columns = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        assert "is_master" in columns


# --------------------------------------------------------------------------
# Migration 004: the session_files natural key
# --------------------------------------------------------------------------


def test_duplicate_session_files_are_collapsed_and_then_prevented(tmp_path):
    """file_id is a fresh uuid4, so INSERT OR IGNORE deduped nothing before."""
    with _fresh() as conn:
        only_baseline = runner.discover()[0]
        conn.executescript(only_baseline.sql)
        conn.executemany(
            "INSERT INTO session_files (file_id, session_id, file_path) VALUES (?, ?, ?)",
            [("f1", "s1", "C:/x.py"), ("f2", "s1", "C:/x.py")],
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM session_files").fetchone()[0] == 2

        migrations.run(conn)

        assert conn.execute("SELECT COUNT(*) FROM session_files").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO session_files (file_id, session_id, file_path) VALUES (?, ?, ?)",
                ("f3", "s1", "C:/x.py"),
            )
