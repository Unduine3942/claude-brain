"""Tests for full-text search, recall and the vault-note collector."""

from __future__ import annotations

import pytest

from claude_brain.core.scrub import REDACTION
from claude_brain.core.search import (
    collect_notes,
    escape_match,
    recall,
    reindex,
    search,
)


def _project(conn, project_id, name, path):
    conn.execute(
        "INSERT INTO projects (project_id, name, path) VALUES (?, ?, ?)",
        (project_id, name, str(path)),
    )
    conn.commit()


def _session(conn, session_id, *, project_id=None, summary="", notes=None, date="2026-05-01"):
    conn.execute(
        """INSERT INTO sessions
           (session_id, project_id, source_dir, date, summary, file_count,
            tool_count, notes, raw_path, is_master)
           VALUES (?, ?, 'd', ?, ?, 0, 0, ?, NULL, 0)""",
        (session_id, project_id, date, summary, notes),
    )
    conn.commit()


def _digest(conn, digest_id, *, project_id=None, topic=None, tasks=None, curated=0):
    conn.execute(
        """INSERT INTO day_digests
           (digest_id, project_id, date, project_name, worktree, topic, tasks,
            source_path, source_mtime, is_curated)
           VALUES (?, ?, '2026-05-02', 'P', 'C:/P', ?, ?, ?, 1.0, ?)""",
        (digest_id, project_id, topic, tasks, f"C:/tmp/{digest_id}.tmp", curated),
    )
    conn.commit()


# --------------------------------------------------------------------------
# Escaping: user input must never be read as FTS5 syntax
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("quick sync", '"quick" "sync"'),
        ('"quick sync"', '"quick sync"'),
        ("prefil*", '"prefil"*'),
        ("a AND b", '"a" "AND" "b"'),
        ("NEAR(x y)", '"NEAR" "x" "y"'),
        ('"; DROP TABLE x; --', '"DROP" "TABLE" "x"'),
        ("  ", ""),
        ("***", ""),
        ("c++", '"c"'),
    ],
)
def test_escape_match(raw, expected):
    assert escape_match(raw) == expected


def test_escape_match_caps_the_term_count():
    assert escape_match(" ".join(f"t{i}" for i in range(40))).count('"') == 32


def test_a_syntax_only_query_returns_nothing_rather_than_raising(conn):
    reindex(conn)
    assert search(conn, "((((") == []
    assert search(conn, "") == []


# --------------------------------------------------------------------------
# Indexing
# --------------------------------------------------------------------------


def test_reindex_covers_all_three_kinds(conn, tmp_path):
    root = tmp_path / "Proj"
    (root / "Sessions").mkdir(parents=True)
    (root / "Decisions.md").write_text("# Decisions\n\nUse WAL.", encoding="utf-8")
    (root / "Sessions" / "2026-05-01_thing.md").write_text("# Thing\n\nbody", encoding="utf-8")
    _project(conn, "p1", "Proj", root)
    _session(conn, "s1", project_id="p1", summary="did a thing")
    _digest(conn, "d1", project_id="p1", topic="a topic")

    assert reindex(conn) == 4

    kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM search_index GROUP BY kind"))
    assert kinds == {"session": 1, "digest": 1, "note": 2}


def test_reindex_is_a_full_rebuild_not_an_append(conn):
    _session(conn, "s1", summary="alpha")
    assert reindex(conn) == 1
    assert reindex(conn) == 1, "a second rebuild must not double the rows"


def test_reindex_reflects_a_deletion(conn):
    """The reason it is a rebuild: a trigger-maintained index can go stale."""
    _session(conn, "s1", summary="alpha")
    reindex(conn)
    conn.execute("DELETE FROM sessions WHERE session_id = 's1'")
    conn.commit()

    assert reindex(conn) == 0
    assert search(conn, "alpha") == []


def test_reindex_without_the_table_is_reported_not_raised(conn):
    conn.execute("DROP TABLE search_index")
    conn.commit()
    assert reindex(conn) == 0


# --------------------------------------------------------------------------
# Searching
# --------------------------------------------------------------------------


def test_finds_a_session_by_its_summary(conn):
    _session(conn, "s1", summary="set up the jellyfin tunnel")
    reindex(conn)

    hits = search(conn, "jellyfin")

    assert len(hits) == 1
    assert hits[0].kind == "session"
    assert hits[0].ref_id == "s1"


def test_finds_a_digest_by_a_task_buried_in_its_body(conn):
    _digest(conn, "d1", tasks="fixed the mousewheel binding\nrenamed the panel")
    reindex(conn)

    hits = search(conn, "mousewheel")

    assert len(hits) == 1
    assert "mousewheel" in hits[0].excerpt.lower()


def test_a_title_hit_outranks_a_body_hit(conn):
    _session(conn, "body", summary="unrelated", notes="we discussed qsv at length")
    _session(conn, "title", summary="qsv transcoding")
    reindex(conn)

    hits = search(conn, "qsv")

    assert [h.ref_id for h in hits] == ["title", "body"]


def test_project_filter_accepts_a_name_or_a_path(conn, tmp_path):
    _project(conn, "p1", "Alpha", tmp_path / "Alpha")
    _project(conn, "p2", "Beta", tmp_path / "Beta")
    _session(conn, "s1", project_id="p1", summary="shared word")
    _session(conn, "s2", project_id="p2", summary="shared word")
    reindex(conn)

    assert len(search(conn, "shared")) == 2
    assert [h.ref_id for h in search(conn, "shared", project="Alpha")] == ["s1"]
    assert [h.ref_id for h in search(conn, "shared", project=str(tmp_path / "Beta"))] == ["s2"]


def test_project_name_is_itself_searchable(conn, tmp_path):
    _project(conn, "p1", "Translator_OSM", tmp_path / "t")
    _session(conn, "s1", project_id="p1", summary="no keyword here")
    reindex(conn)

    assert [h.ref_id for h in search(conn, "Translator_OSM")] == ["s1"]


def test_kind_filter(conn):
    _session(conn, "s1", summary="overlap")
    _digest(conn, "d1", topic="overlap")
    reindex(conn)

    assert len(search(conn, "overlap")) == 2
    assert [h.kind for h in search(conn, "overlap", kind="digest")] == ["digest"]


def test_the_word_session_does_not_match_every_row(conn):
    """`kind` is UNINDEXED precisely so this cannot happen."""
    _session(conn, "s1", summary="nothing relevant")
    _digest(conn, "d1", topic="nothing relevant")
    reindex(conn)

    assert search(conn, "session") == []


def test_limit_is_honoured(conn):
    for i in range(5):
        _session(conn, f"s{i}", summary="common term")
    reindex(conn)

    assert len(search(conn, "common", limit=2)) == 2


def test_prefix_search(conn):
    _session(conn, "s1", summary="prefill is the bottleneck")
    reindex(conn)

    assert search(conn, "prefil*")
    assert not search(conn, "prefil"), "without the star it is an exact term"


def test_a_session_with_no_summary_still_reports_a_title(conn):
    _session(conn, "s1", summary="", notes="only the notes mention qsv")
    reindex(conn)

    assert search(conn, "qsv")[0].title == "(no summary)"


# --------------------------------------------------------------------------
# Vault notes: where the conclusions actually live
# --------------------------------------------------------------------------


def test_notes_are_collected_from_the_vault(conn, tmp_path):
    root = tmp_path / "Proj"
    (root / "Sessions").mkdir(parents=True)
    (root / "Sessions" / "2026-09-29_bakeoff.md").write_text(
        "---\ntitle: Model Bake-Off\ncreated: 2026-09-29\n---\n\nCold prefill is 67 tok/s.",
        encoding="utf-8",
    )
    _project(conn, "p1", "Proj", root)

    rows = collect_notes(conn)

    assert len(rows) == 1
    kind, ref, _project_id, date, project, title, body = rows[0]
    assert kind == "note"
    assert ref.endswith("2026-09-29_bakeoff.md")
    assert project == "Proj"
    assert date == "2026-09-29"
    assert title == "Model Bake-Off"
    assert "67 tok/s" in body


def test_note_title_falls_back_to_a_heading_then_the_filename(conn, tmp_path):
    root = tmp_path / "Proj"
    (root / "Sessions").mkdir(parents=True)
    (root / "Sessions" / "a.md").write_text("# A Heading\n\nbody", encoding="utf-8")
    (root / "Sessions" / "no_heading_here.md").write_text("body only", encoding="utf-8")
    _project(conn, "p1", "Proj", root)

    titles = {row[5] for row in collect_notes(conn)}

    assert titles == {"A Heading", "no heading here"}


def test_index_and_empty_notes_are_skipped(conn, tmp_path):
    root = tmp_path / "Proj"
    (root / "Sessions").mkdir(parents=True)
    (root / "Sessions" / "Index.md").write_text("# Index\n\n| a |", encoding="utf-8")
    (root / "Sessions" / "blank.md").write_text("   \n", encoding="utf-8")
    (root / "Sessions" / "real.md").write_text("# Real\n\nbody", encoding="utf-8")
    _project(conn, "p1", "Proj", root)

    assert [row[5] for row in collect_notes(conn)] == ["Real"]


def test_a_project_whose_directory_is_gone_is_skipped(conn, tmp_path):
    _project(conn, "p1", "Gone", tmp_path / "does-not-exist")
    assert collect_notes(conn) == []


def test_a_credential_quoted_in_a_note_is_scrubbed(conn, tmp_path):
    """Vault notes are hand-written and may quote something pasted into chat."""
    root = tmp_path / "Proj"
    root.mkdir()
    (root / "Decisions.md").write_text(
        "# Decisions\n\nRoot password Hunter2Hunter2 for the box.", encoding="utf-8"
    )
    _project(conn, "p1", "Proj", root)

    body = collect_notes(conn)[0][6]

    assert "Hunter2Hunter2" not in body
    assert REDACTION in body


def test_searching_finds_the_conclusion_in_a_note(conn, tmp_path):
    """The point of indexing notes: a session row holds the prompts, the note
    holds the answer."""
    root = tmp_path / "Proj"
    (root / "Sessions").mkdir(parents=True)
    (root / "Sessions" / "2026-09-29_x.md").write_text(
        "# Bake-Off\n\nCached prefill is 4568 tok/s, a 68x difference.", encoding="utf-8"
    )
    _project(conn, "p1", "Proj", root)
    _session(conn, "s1", project_id="p1", summary="benchmark the models")
    reindex(conn)

    hits = search(conn, "prefill", project="Proj")

    assert hits and hits[0].kind == "note"
    assert "4568" in hits[0].excerpt


# --------------------------------------------------------------------------
# Recall
# --------------------------------------------------------------------------


def test_recall_puts_a_curated_note_first(conn):
    _digest(conn, "generated", tasks="we talked about caching", curated=0)
    _digest(conn, "handwritten", topic="caching", tasks="caching decision", curated=1)
    reindex(conn)

    hits = recall(conn, "caching", limit=5)

    assert hits[0].ref_id == "handwritten"


def test_recall_collapses_a_session_and_digest_from_the_same_day(conn, tmp_path):
    _project(conn, "p1", "Proj", tmp_path)
    _session(conn, "s1", project_id="p1", summary="the qsv work", date="2026-05-02")
    _digest(conn, "d1", project_id="p1", topic="the qsv work")
    reindex(conn)

    assert len(search(conn, "qsv")) == 2
    assert len(recall(conn, "qsv")) == 1, "same project and day, shown once"


def test_recall_on_an_unknown_topic_is_empty_not_an_error(conn):
    reindex(conn)
    assert recall(conn, "nothing at all like this") == []


def test_recall_respects_the_project_filter(conn, tmp_path):
    _project(conn, "p1", "Alpha", tmp_path / "a")
    _project(conn, "p2", "Beta", tmp_path / "b")
    _session(conn, "s1", project_id="p1", summary="tunnel setup")
    _session(conn, "s2", project_id="p2", summary="tunnel setup")
    reindex(conn)

    hits = recall(conn, "tunnel", project="Beta")

    assert [h.ref_id for h in hits] == ["s2"]
