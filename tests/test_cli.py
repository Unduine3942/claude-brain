"""Tests for the ``brain`` command line.

The CLI is the only way the hooks, the skills and CI reach the store, so its
exit codes are part of its contract and are asserted here.
"""

from __future__ import annotations

import json

import pytest

from claude_brain import cli


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point every CLI entry point at a throwaway database."""
    db = tmp_path / "brain.db"
    monkeypatch.setattr("claude_brain.db.connection.DB_PATH", db)
    monkeypatch.setattr("claude_brain.config.DB_PATH", db)
    # The CLI reads ~/.claude for settings, transcripts and digests. Point the
    # whole home elsewhere so no test can see the real machine's history.
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    (home / ".claude" / "sessions").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    return db


def _run(*argv) -> int:
    return cli.main(list(argv))


# --------------------------------------------------------------------------
# Parser contract
# --------------------------------------------------------------------------


def test_every_subcommand_is_wired_to_a_function():
    parser = cli.build_parser()
    actions = [a for a in parser._actions if a.dest == "command"]
    assert actions, "expected a subparser"
    for name, sub in actions[0].choices.items():
        assert sub.get_default("func"), f"{name} has no handler"


def test_no_command_prints_help_and_signals_usage(capsys):
    assert _run() == cli.EXIT_USAGE
    assert "Durable memory" in capsys.readouterr().out


def test_unknown_command_is_an_argparse_error():
    with pytest.raises(SystemExit) as excinfo:
        _run("nonsense")
    assert excinfo.value.code == cli.EXIT_USAGE


def test_version_is_reported(capsys):
    with pytest.raises(SystemExit) as excinfo:
        _run("--version")
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip()


def test_save_path_and_latest_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        _run("save", "--path", "x.jsonl", "--latest")


# --------------------------------------------------------------------------
# Output must survive a Windows console
# --------------------------------------------------------------------------


def test_output_survives_text_the_console_cannot_encode(capsys, monkeypatch):
    """cp1252 is still the default here, so an em dash in a stored summary
    would otherwise take the whole command down with UnicodeEncodeError."""

    class Cp1252Stream:
        encoding = "cp1252"

        def __init__(self):
            self.written = []

        def write(self, text):
            text.encode("cp1252")  # raises on an em dash, like the real console
            self.written.append(text)

        def flush(self):
            pass

    stream = Cp1252Stream()
    monkeypatch.setattr("sys.stdout", stream)

    cli._out("session \u2014 done \u2192 ok")

    assert stream.written, "something must still be printed"
    assert "done" in "".join(stream.written)


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------


def test_doctor_reports_a_clean_empty_store(store, capsys):
    from claude_brain.core import skills

    skills.install_skills()
    (store.parent / "home" / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    event: [{"hooks": [{"command": "py", "args": ["brain_hook.py"]}]}]
                    for event in ("SessionStart", "SessionEnd", "PreCompact")
                }
            }
        ),
        encoding="utf-8",
    )

    code = _run("doctor")
    out = capsys.readouterr().out

    assert code == cli.EXIT_OK
    assert "integrity_check: ok" in out
    assert "no problems found" in out


def test_doctor_flags_uninstalled_skills(store, capsys, tmp_path):
    """A skill with no description is omitted from the model-visible list, so
    it can be present and never chosen. doctor reports both states."""
    (tmp_path / "home" / ".claude" / "settings.json").write_text("{}", encoding="utf-8")

    assert _run("doctor") == cli.EXIT_EMPTY
    assert "skill not installed" in capsys.readouterr().out


def test_doctor_flags_missing_capture_hooks(store, capsys, tmp_path):
    (tmp_path / "home" / ".claude" / "settings.json").write_text("{}", encoding="utf-8")

    code = _run("doctor")
    out = capsys.readouterr().out

    assert code == cli.EXIT_EMPTY
    assert "capture hook not registered" in out
    assert "SessionStart" in out


def test_doctor_flags_an_empty_search_index(store, capsys):
    _run("doctor")  # creates and migrates the database
    from claude_brain.db.connection import get_connection

    conn = get_connection(store)
    conn.execute(
        """INSERT INTO sessions
           (session_id, project_id, source_dir, date, summary, file_count,
            tool_count, notes, raw_path, is_master)
           VALUES ('s1', NULL, 'd', '2026-01-01', 'x', 0, 0, NULL, NULL, 0)"""
    )
    conn.commit()
    conn.close()
    capsys.readouterr()

    assert _run("doctor") == cli.EXIT_EMPTY
    assert "search index is empty" in capsys.readouterr().out


def test_doctor_reports_a_missing_settings_file(store, capsys):
    assert _run("doctor") == cli.EXIT_EMPTY
    assert "settings.json not found" in capsys.readouterr().out


def test_doctor_reports_an_unreadable_settings_file(store, capsys, tmp_path):
    (tmp_path / "home" / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")

    _run("doctor")

    assert "cannot read" in capsys.readouterr().out


# --------------------------------------------------------------------------
# search / recall
# --------------------------------------------------------------------------


def _seed(store):
    from claude_brain.db import migrations
    from claude_brain.db.connection import get_connection

    conn = get_connection(store)
    migrations.run(conn)
    conn.execute("INSERT INTO projects (project_id, name, path) VALUES ('p1','Demo','C:/Demo')")
    conn.execute(
        """INSERT INTO sessions
           (session_id, project_id, source_dir, date, summary, file_count,
            tool_count, notes, raw_path, is_master)
           VALUES ('s1','p1','d','2026-05-01','set up the jellyfin tunnel',0,0,NULL,NULL,0)"""
    )
    conn.execute(
        "INSERT INTO cached_files (path, mtime, size, session_id)"
        " VALUES ('C:/t/s1.jsonl', 1.0, 10, 's1')"
    )
    conn.commit()
    from claude_brain.core.search import reindex

    reindex(conn)
    conn.close()


def test_search_finds_a_seeded_session(store, capsys):
    _seed(store)

    code = _run("search", "jellyfin")
    out = capsys.readouterr().out

    assert code == cli.EXIT_OK
    assert "1 result" in out
    assert "jellyfin tunnel" in out


def test_search_with_no_match_signals_empty(store, capsys):
    _seed(store)

    code = _run("search", "nothinglikethis")

    assert code == cli.EXIT_EMPTY
    assert "nothing found" in capsys.readouterr().out


def test_search_json_is_machine_readable(store, capsys):
    _seed(store)

    _run("search", "jellyfin", "--json")
    payload = json.loads(capsys.readouterr().out)

    assert payload[0]["ref_id"] == "s1"
    assert payload[0]["kind"] == "session"


def test_search_quiet_omits_excerpts(store, capsys):
    _seed(store)

    _run("search", "jellyfin", "-q")
    out = capsys.readouterr().out

    assert len(out.strip().splitlines()) == 2, "header plus one line"


def test_recall_with_no_prior_context_signals_empty(store, capsys):
    _seed(store)

    code = _run("recall", "something unmentioned")

    assert code == cli.EXIT_EMPTY
    assert "no prior context" in capsys.readouterr().out


def test_recall_names_the_project_scope_when_filtered(store, capsys):
    _seed(store)

    _run("recall", "unmentioned", "--project", "Demo")

    assert "in Demo" in capsys.readouterr().out


# --------------------------------------------------------------------------
# ingest / backfill / reindex / repair
# --------------------------------------------------------------------------


def test_ingest_on_an_empty_machine_is_not_an_error(store, capsys, monkeypatch):
    monkeypatch.setattr("claude_brain.core.transcript.discover_transcripts", lambda root=None: [])

    assert _run("ingest", "--no-vault") == cli.EXIT_OK
    assert "scanned=0" in capsys.readouterr().out


def test_backfill_reports_counts(store, capsys, monkeypatch):
    monkeypatch.setattr("claude_brain.core.digests.discover_digests", lambda root=None: [])

    assert _run("backfill") == cli.EXIT_OK
    assert "imported=0" in capsys.readouterr().out


def test_reindex_reports_the_row_count(store, capsys):
    _seed(store)

    assert _run("reindex") == cli.EXIT_OK
    assert "indexed 1 row" in capsys.readouterr().out


def test_repair_defaults_to_a_dry_run(store, capsys):
    from claude_brain.db import migrations
    from claude_brain.db.connection import get_connection

    conn = get_connection(store)
    migrations.run(conn)
    conn.execute(
        """INSERT INTO sessions
           (session_id, project_id, source_dir, date, summary, file_count,
            tool_count, notes, raw_path, is_master)
           VALUES ('agent-a',NULL,'d','2026-01-01','(no summary)',0,0,NULL,
                   'C:/p/x/subagents/agent-a.jsonl',0)"""
    )
    conn.commit()
    conn.close()

    code = _run("repair")
    out = capsys.readouterr().out

    assert code == cli.EXIT_OK
    assert "purge_sidechains" in out
    assert "--apply" in out

    conn = get_connection(store)
    remaining = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    conn.close()
    assert remaining == 1, "a dry run must not delete anything"


def test_repair_on_a_clean_store_says_nothing_to_do(store, capsys):
    _seed(store)

    assert _run("repair") == cli.EXIT_OK
    assert "nothing to do" in capsys.readouterr().out


def test_save_rejects_a_missing_transcript(store, capsys):
    code = _run("save", "--path", str(store.parent / "nope.jsonl"))

    assert code == cli.EXIT_EMPTY
    assert "no such transcript" in capsys.readouterr().out


def test_an_unexpected_failure_reports_rather_than_tracebacks(store, capsys, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("claude_brain.core.search.reindex", boom)

    code = _run("reindex")

    assert code == cli.EXIT_BROKEN
    assert "disk on fire" in capsys.readouterr().out
