"""Tests for the context injected at SessionStart.

The property these all defend is *scoping*. Injecting another project's history
is the bug that produced this store's cross-contaminated summaries, so a test
that only checks "some context came back" would miss the whole point.
"""

from __future__ import annotations

from pathlib import Path

from claude_brain.core.context import build_session_context
from claude_brain.core.search import reindex


def _project(conn, project_id, name, path):
    conn.execute(
        "INSERT INTO projects (project_id, name, path) VALUES (?, ?, ?)",
        (project_id, name, str(path)),
    )
    conn.commit()


def _session(conn, session_id, project_id, summary, date="2026-05-01"):
    conn.execute(
        """INSERT INTO sessions
           (session_id, project_id, source_dir, date, summary, file_count,
            tool_count, notes, raw_path, is_master)
           VALUES (?, ?, 'd', ?, ?, 0, 0, NULL, NULL, 0)""",
        (session_id, project_id, date, summary),
    )
    conn.commit()


def _digest(conn, digest_id, project_id, *, topic=None, tasks=None, date="2026-05-02"):
    conn.execute(
        """INSERT INTO day_digests
           (digest_id, project_id, date, project_name, worktree, topic, tasks,
            source_path, source_mtime, is_curated)
           VALUES (?, ?, ?, 'P', 'C:/P', ?, ?, ?, 1.0, 0)""",
        (digest_id, project_id, date, topic, tasks, f"C:/t/{digest_id}.tmp"),
    )
    conn.commit()


# --------------------------------------------------------------------------
# Scoping
# --------------------------------------------------------------------------


def test_an_unregistered_directory_gets_nothing(conn, tmp_path):
    _project(conn, "p1", "Alpha", tmp_path / "Alpha")
    _session(conn, "s1", "p1", "alpha work")

    assert build_session_context(conn, str(tmp_path / "Beta")) == ""


def test_only_this_project_s_history_is_returned(conn, tmp_path):
    _project(conn, "p1", "Alpha", tmp_path / "Alpha")
    _project(conn, "p2", "Beta", tmp_path / "Beta")
    _session(conn, "s1", "p1", "ALPHA-ONLY work")
    _session(conn, "s2", "p2", "BETA-ONLY work")

    text = build_session_context(conn, str(tmp_path / "Alpha"))

    assert "ALPHA-ONLY" in text
    assert "BETA-ONLY" not in text, "this is the cross-project leak being prevented"


def test_a_subdirectory_resolves_to_its_project(conn, tmp_path):
    """A session started in repo/backend is still the repo project."""
    root = tmp_path / "Repo"
    (root / "backend").mkdir(parents=True)
    _project(conn, "p1", "Repo", root)
    _session(conn, "s1", "p1", "repo work")

    assert "repo work" in build_session_context(conn, str(root / "backend"))


def test_the_deepest_enclosing_project_wins(conn, tmp_path):
    outer = tmp_path / "Mono"
    inner = outer / "packages" / "api"
    inner.mkdir(parents=True)
    _project(conn, "p1", "Mono", outer)
    _project(conn, "p2", "Api", inner)
    _session(conn, "s1", "p1", "OUTER work")
    _session(conn, "s2", "p2", "INNER work")

    text = build_session_context(conn, str(inner / "src"))

    assert "INNER work" in text
    assert "OUTER work" not in text


def test_a_drive_root_project_does_not_adopt_the_whole_drive(conn, tmp_path):
    r"""One real digest recorded a session started at ``C:\``."""
    _project(conn, "root", "unknown", Path(tmp_path.anchor))
    _session(conn, "s1", "root", "work at the root")

    assert build_session_context(conn, str(tmp_path / "anything")) == ""


def test_the_home_directory_does_not_adopt_its_children(conn, tmp_path):
    """Capture is opt-out, so home gets registered - but as an enclosing
    project it is nearly as broad as the drive root."""
    _project(conn, "home", "jdoe", Path.home())
    _session(conn, "s1", "home", "work in the home directory")

    scratch = Path.home() / "some-scratch-dir-that-need-not-exist"
    assert build_session_context(conn, str(scratch)) == ""


def test_the_home_directory_still_matches_exactly(conn):
    _project(conn, "home", "jdoe", Path.home())
    _session(conn, "s1", "home", "work in the home directory")

    assert "work in the home directory" in build_session_context(conn, str(Path.home()))


def test_the_project_is_named_so_the_scope_is_visible(conn, tmp_path):
    _project(conn, "p1", "Translator_OSM", tmp_path / "t")
    _session(conn, "s1", "p1", "something")

    text = build_session_context(conn, str(tmp_path / "t"))

    assert "prior context for Translator_OSM" in text
    assert "not another project's history" in text


# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------


def test_a_project_with_no_history_gets_nothing(conn, tmp_path):
    _project(conn, "p1", "Empty", tmp_path / "Empty")
    assert build_session_context(conn, str(tmp_path / "Empty")) == ""


def test_placeholder_summaries_are_not_offered_as_context(conn, tmp_path):
    _project(conn, "p1", "Alpha", tmp_path / "Alpha")
    _session(conn, "s1", "p1", "(no summary)")
    _session(conn, "s2", "p1", "")

    assert build_session_context(conn, str(tmp_path / "Alpha")) == ""


def test_the_session_being_started_is_excluded(conn, tmp_path):
    """On a resume it would otherwise appear as its own previous work."""
    _project(conn, "p1", "Alpha", tmp_path / "Alpha")
    _session(conn, "current", "p1", "THIS session")
    _session(conn, "older", "p1", "EARLIER session", date="2026-04-01")

    text = build_session_context(conn, str(tmp_path / "Alpha"), exclude_session_id="current")

    assert "EARLIER session" in text
    assert "THIS session" not in text


def test_newest_first(conn, tmp_path):
    _project(conn, "p1", "Alpha", tmp_path / "Alpha")
    _session(conn, "old", "p1", "the older one", date="2026-01-01")
    _session(conn, "new", "p1", "the newer one", date="2026-09-01")

    text = build_session_context(conn, str(tmp_path / "Alpha"))

    assert text.index("the newer one") < text.index("the older one")


def test_a_generated_digest_falls_back_to_its_first_task(conn, tmp_path):
    """Only 2 of 164 real digests have a **Topic:** line, so keying the section
    on topic alone would hide almost all of the history."""
    _project(conn, "p1", "Alpha", tmp_path / "Alpha")
    _digest(conn, "d1", "p1", tasks="rewrote the exporter\nfixed the bbox widget")

    text = build_session_context(conn, str(tmp_path / "Alpha"))

    assert "rewrote the exporter" in text
    assert "fixed the bbox widget" not in text, "only the first line"


def test_a_topic_is_preferred_over_the_task_list(conn, tmp_path):
    _project(conn, "p1", "Alpha", tmp_path / "Alpha")
    _digest(conn, "d1", "p1", topic="THE TOPIC", tasks="a task line")

    assert "THE TOPIC" in build_session_context(conn, str(tmp_path / "Alpha"))


def test_vault_notes_are_listed(conn, tmp_path):
    root = tmp_path / "Alpha"
    (root / "Sessions").mkdir(parents=True)
    (root / "Sessions" / "2026-09-29_bakeoff.md").write_text("# Bake-Off\n\nbody", encoding="utf-8")
    _project(conn, "p1", "Alpha", root)
    reindex(conn)

    text = build_session_context(conn, str(root))

    assert "Bake-Off" in text
    assert "brain recall" in text, "the injection must teach that the tool exists"


def test_long_text_is_truncated(conn, tmp_path):
    _project(conn, "p1", "Alpha", tmp_path / "Alpha")
    for i in range(10):
        _session(conn, f"s{i}", "p1", "x" * 4000, date=f"2026-0{i % 9 + 1}-01")

    text = build_session_context(conn, str(tmp_path / "Alpha"))

    assert len(text) <= 1800, "it is spent from every session's context window"


def test_no_cwd_is_not_an_error(conn):
    assert build_session_context(conn, None) == ""
    assert build_session_context(conn, "") == ""


def test_a_partially_migrated_store_degrades_quietly(conn, tmp_path):
    """The hook must never be the reason a session fails to start."""
    _project(conn, "p1", "Alpha", tmp_path / "Alpha")
    _session(conn, "s1", "p1", "work")
    conn.execute("DROP TABLE day_digests")
    conn.commit()

    assert build_session_context(conn, str(tmp_path / "Alpha")) == ""
