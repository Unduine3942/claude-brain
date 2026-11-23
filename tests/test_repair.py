"""Tests for the one-off data repair.

The store is the only surviving copy of nearly all its history, so these tests
care as much about what is *kept* as what is deleted.
"""

from __future__ import annotations

import pytest

from claude_brain.core.repair import UNTRUSTED_MARKER, apply_repair, plan_repair


def _session(conn, session_id, *, raw_path=None, summary="real summary", notes=None, master=0):
    conn.execute(
        """INSERT INTO sessions
           (session_id, project_id, source_dir, date, summary, file_count,
            tool_count, notes, raw_path, is_master)
           VALUES (?, NULL, 'd', '2026-01-01', ?, 0, 0, ?, ?, ?)""",
        (session_id, summary, notes, raw_path, master),
    )
    conn.commit()


# --------------------------------------------------------------------------
# Dry run
# --------------------------------------------------------------------------


def test_plan_is_read_only(conn):
    _session(
        conn, "agent-aaa", raw_path=r"C:\p\x\subagents\agent-aaa.jsonl", summary="(no summary)"
    )
    before = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    plan = plan_repair(conn)

    assert plan.total_rows > 0
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == before


def test_plan_on_a_clean_database_is_empty(conn):
    assert plan_repair(conn).applicable == ()


def test_describe_mentions_each_applicable_action(conn):
    _session(
        conn, "agent-aaa", raw_path=r"C:\p\x\subagents\agent-aaa.jsonl", summary="(no summary)"
    )
    text = plan_repair(conn).describe()
    assert "purge_sidechains" in text
    assert "DELETE" in text


# --------------------------------------------------------------------------
# What gets purged
# --------------------------------------------------------------------------


def test_sidechain_rows_are_purged(conn):
    _session(
        conn, "agent-aaa", raw_path=r"C:\p\x\subagents\agent-aaa.jsonl", summary="(no summary)"
    )
    _session(conn, "wf-one", raw_path=r"C:\p\wf_123\b.jsonl", summary="(no summary)")

    apply_repair(conn)

    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_orphan_session_files_are_purged(conn):
    """session_files cascades only when the caller enabled foreign_keys."""
    conn.execute("PRAGMA foreign_keys=OFF")
    _session(conn, "s1")
    conn.execute(
        "INSERT INTO session_files (file_id, session_id, file_path) VALUES ('f1','ghost','C:/x.py')"
    )
    conn.commit()

    apply_repair(conn)

    assert conn.execute("SELECT COUNT(*) FROM session_files").fetchone()[0] == 0


def test_orphan_imported_paths_are_unblocked(conn):
    conn.execute("INSERT INTO imported_paths (raw_path) VALUES ('C:/gone.jsonl')")
    conn.commit()

    apply_repair(conn)

    assert conn.execute("SELECT COUNT(*) FROM imported_paths").fetchone()[0] == 0


# --------------------------------------------------------------------------
# What is deliberately kept
# --------------------------------------------------------------------------


def test_dangling_row_with_a_summary_is_kept(conn):
    """Its transcript is gone, so this row is the only record it happened."""
    _session(conn, "s1", raw_path=r"C:\gone\abc.jsonl", summary="did the thing")

    apply_repair(conn)

    kept = conn.execute("SELECT summary, notes FROM sessions WHERE session_id='s1'").fetchone()
    assert kept is not None
    assert kept["summary"] == "did the thing"
    assert UNTRUSTED_MARKER in kept["notes"], "kept but flagged as misattributed"


def test_master_sessions_are_kept(conn):
    _session(conn, "m1", raw_path=None, summary="consolidated", master=1)
    apply_repair(conn)
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE is_master=1").fetchone()[0] == 1


def test_plan_reports_what_it_keeps(conn):
    _session(conn, "s1", raw_path=r"C:\gone\abc.jsonl", summary="did the thing")
    plan = plan_repair(conn)
    assert any("only surviving record" in label for label in plan.kept)
    assert "Deliberately kept" in plan.describe()


# --------------------------------------------------------------------------
# Field cleanups
# --------------------------------------------------------------------------


def test_placeholder_notes_are_cleared(conn):
    _session(conn, "s1", raw_path=r"C:\gone\a.jsonl", notes="-")
    apply_repair(conn)
    assert conn.execute("SELECT notes FROM sessions WHERE session_id='s1'").fetchone()[0] != "-"


def test_vault_path_is_normalised_to_the_project_root(conn):
    conn.execute("INSERT INTO projects (project_id, name, path) VALUES ('p1','Demo','C:/Demo')")
    conn.execute(
        "INSERT INTO vaults (vault_id, project_id, vault_path, status) "
        "VALUES ('v1','p1','C:/Demo/.obsidian','exists')"
    )
    conn.commit()

    apply_repair(conn)

    assert (
        conn.execute("SELECT vault_path FROM vaults WHERE vault_id='v1'").fetchone()[0] == "C:/Demo"
    )


def test_untrusted_flag_is_not_applied_twice(conn):
    _session(conn, "s1", raw_path=r"C:\gone\a.jsonl", summary="x")
    apply_repair(conn)
    apply_repair(conn)
    notes = conn.execute("SELECT notes FROM sessions WHERE session_id='s1'").fetchone()[0]
    assert notes.count(UNTRUSTED_MARKER) == 1


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------


def test_repair_is_idempotent(conn):
    _session(conn, "agent-a", raw_path=r"C:\p\x\subagents\agent-a.jsonl", summary="(no summary)")
    _session(conn, "s1", raw_path=r"C:\gone\a.jsonl", notes="-")

    first = apply_repair(conn)
    second = apply_repair(conn)

    assert first.rows_changed > 0
    assert second.rows_changed == 0
    assert plan_repair(conn).applicable == ()


def test_allow_destructive_false_skips_deletions(conn):
    _session(conn, "agent-a", raw_path=r"C:\p\x\subagents\agent-a.jsonl", summary="(no summary)")
    _session(conn, "s1", raw_path=r"C:\gone\a.jsonl", notes="-")

    apply_repair(conn, allow_destructive=False)

    assert (
        conn.execute("SELECT COUNT(*) FROM sessions WHERE session_id='agent-a'").fetchone()[0] == 1
    )
    assert conn.execute("SELECT notes FROM sessions WHERE session_id='s1'").fetchone()[0] != "-"


def test_report_counts_sessions_before_and_after(conn):
    _session(conn, "agent-a", raw_path=r"C:\p\x\subagents\agent-a.jsonl", summary="(no summary)")
    report = apply_repair(conn)
    assert report.sessions_before == 1
    assert report.sessions_after == 0
    assert "sessions 1 -> 0" in str(report)


def test_new_pipeline_summaries_are_not_flagged_as_untrusted(conn):
    """Only LEGACY captures are suspect.

    A session ingested by the current pipeline is recorded in cached_files and
    its summary comes from its own prompts. Flagging those would mislabel good
    data and would re-fire on every future session.
    """
    _session(conn, "legacy", raw_path=r"C:\gone\a.jsonl", summary="old misattributed text")
    _session(conn, "fresh", raw_path=r"C:\live\b.jsonl", summary="what I actually asked")
    conn.execute(
        "INSERT INTO cached_files (path, mtime, size, session_id) VALUES (?,?,?,?)",
        (r"C:\live\b.jsonl", 1.0, 10, "fresh"),
    )
    conn.commit()

    apply_repair(conn)

    rows = dict(conn.execute("SELECT session_id, COALESCE(notes,'') FROM sessions").fetchall())
    assert UNTRUSTED_MARKER in rows["legacy"]
    assert UNTRUSTED_MARKER not in rows["fresh"], "a fresh summary must not be flagged"


def test_repair_settles_after_ingest(conn):
    """Regression: the flag predicate used to re-fire on every new session."""
    _session(conn, "fresh", raw_path=r"C:\live\b.jsonl", summary="real prompt")
    conn.execute(
        "INSERT INTO cached_files (path, mtime, size, session_id) VALUES (?,?,?,?)",
        (r"C:\live\b.jsonl", 1.0, 10, "fresh"),
    )
    conn.commit()

    apply_repair(conn)
    assert plan_repair(conn).applicable == (), "repair must settle, not re-fire"


def _project(conn, project_id, name, path):
    conn.execute(
        "INSERT INTO projects (project_id, name, path) VALUES (?, ?, ?)",
        (project_id, name, path),
    )
    conn.commit()


def test_a_project_registered_at_a_drive_root_is_unregistered(conn):
    r"""A project at ``C:\`` encloses every other project, so one such row leaks
    its history into every enclosing-project lookup on the drive."""
    _project(conn, "r", "unknown", "C:\\")
    _project(conn, "g", "Real", r"C:\p\Real")
    _session(conn, "s1", raw_path=r"C:\live\a.jsonl", summary="work at the root")
    conn.execute("UPDATE sessions SET project_id = 'r' WHERE session_id = 's1'")
    conn.commit()

    apply_repair(conn)

    assert [r["path"] for r in conn.execute("SELECT path FROM projects")] == [r"C:\p\Real"]
    kept = conn.execute("SELECT project_id, summary FROM sessions WHERE session_id='s1'").fetchone()
    assert kept is not None, "the session row must survive"
    assert kept["project_id"] is None, "unlinked is the honest state"
    assert kept["summary"] == "work at the root"


@pytest.mark.parametrize("path", ["C:\\", "C:", "D:/", "/", "", "   "])
def test_every_root_shape_is_caught(conn, path):
    _project(conn, "r", "x", path)
    apply_repair(conn)
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


@pytest.mark.parametrize(
    "path", [r"C:\p", r"C:\Users\x\Desktop\Proj", "/home/x/proj", "C:/Users/x/Proj"]
)
def test_a_real_project_path_is_left_alone(conn, path):
    _project(conn, "g", "x", path)
    apply_repair(conn)
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
