"""Tests for the day-digest parser and backfill.

Every fixture here is the shape of a real file in ``~/.claude/sessions``: the
corpus is 164 files, 162 generated from the ECC template and 2 hand-written.
"""

from __future__ import annotations

import pytest

from claude_brain.core.digests import (
    backfill_digests,
    discover_digests,
    parse_digest,
)
from claude_brain.core.scrub import REDACTION

GENERATED = """\
# Session: 2026-04-11
**Date:** 2026-04-11
**Started:** 09:15
**Last Updated:** 11:40
**Project:** Overture
**Branch:** main
**Worktree:** {worktree}

---
<!-- ECC:SUMMARY:START -->
## Session Summary

### Tasks
- add a bbox selector to the dataset widget
- <command-name>/plan</command-name> <command-args>port the exporter</command-args>
- This session is being continued from a previous conversation that ran out of context.

### Files Modified
- {worktree}/main.py
- {worktree}/ui/widgets/bbox.py
- {worktree}/main.py

### Tools Used
Read, Edit, Bash

### Stats
- Total user messages: 12
<!-- ECC:SUMMARY:END -->

### Notes for Next Session
-

### Context to Load
```
[relevant files]
```
"""

CURATED = """\
# Session: 2026-06-05

**Started:** ~ongoing from 2026-05-27
**Last Updated:** 17:53
**Project:** Claude Brain (`{worktree}`)
**Topic:** Dashboard transformation, mousewheel fix

---

## What We Are Building

A desktop GUI over the session store.

## What WORKED

Binding the wheel to the canvas, not the frame.
"""


def _write(tmp_path, name, body, worktree=r"C:\proj\Overture"):
    path = tmp_path / name
    path.write_text(body.format(worktree=worktree), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def test_discover_is_flat_and_ordered(tmp_path):
    _write(tmp_path, "2026-04-11-b-session.tmp", GENERATED)
    _write(tmp_path, "2026-04-10-a-session.tmp", GENERATED)
    (tmp_path / "notes.md").write_text("ignored", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    _write(tmp_path / "sub", "2026-01-01-c-session.tmp", GENERATED)

    names = [p.name for p in discover_digests(tmp_path)]

    assert names == ["2026-04-10-a-session.tmp", "2026-04-11-b-session.tmp"]


def test_discover_missing_directory_is_not_an_error(tmp_path):
    assert discover_digests(tmp_path / "nope") == []


# --------------------------------------------------------------------------
# The generated (ECC template) shape
# --------------------------------------------------------------------------


def test_parses_the_generated_shape(tmp_path):
    digest = parse_digest(_write(tmp_path, "2026-04-11-Overture-session.tmp", GENERATED))

    assert digest.date == "2026-04-11"
    assert digest.project_name == "Overture"
    assert digest.worktree == r"C:\proj\Overture"
    assert digest.message_count == 12
    assert digest.tools == ("Read", "Edit", "Bash")
    assert not digest.is_curated
    assert not digest.is_empty


def test_harness_noise_is_dropped_from_tasks(tmp_path):
    digest = parse_digest(_write(tmp_path, "2026-04-11-x-session.tmp", GENERATED))
    joined = " ".join(digest.tasks)
    assert "add a bbox selector" in joined
    assert "session is being continued" not in joined


def test_slash_command_arguments_are_recovered(tmp_path):
    """The tags are noise but the arguments are the actual request."""
    digest = parse_digest(_write(tmp_path, "2026-04-11-x-session.tmp", GENERATED))
    assert "/plan port the exporter" in digest.tasks


def test_truncated_command_args_still_recover(tmp_path):
    """The plugin cuts every task line at ~200 chars, losing the closing tag."""
    body = GENERATED.replace(
        "<command-args>port the exporter</command-args>",
        "<command-args>port the exporter and keep going until it is cut off",
    )
    digest = parse_digest(_write(tmp_path, "2026-04-11-x-session.tmp", body))
    assert any(t.startswith("/plan port the exporter") for t in digest.tasks)


def test_duplicate_files_are_collapsed(tmp_path):
    digest = parse_digest(_write(tmp_path, "2026-04-11-x-session.tmp", GENERATED))
    assert len(digest.files) == 2, "main.py is listed twice in the source"


def test_template_placeholders_do_not_become_content(tmp_path):
    digest = parse_digest(_write(tmp_path, "2026-04-11-x-session.tmp", GENERATED))
    assert digest.notes is None, "'Notes for Next Session' is the literal '-'"
    assert "[relevant files]" not in (digest.notes or "")


# --------------------------------------------------------------------------
# The curated (hand-written) shape
# --------------------------------------------------------------------------


def test_parses_a_hand_written_note(tmp_path):
    """Two real files have no ECC marker and are the best content in the set."""
    digest = parse_digest(_write(tmp_path, "2026-06-05-claudebrain-session.tmp", CURATED))

    assert digest.is_curated
    assert digest.date == "2026-06-05"
    assert digest.project_name == "Claude Brain", "the opening bracket must not survive"
    assert digest.worktree == r"C:\proj\Overture"
    assert digest.topic and "mousewheel" in digest.topic
    assert digest.notes and "What We Are Building" in digest.notes
    assert not digest.is_empty


def test_date_falls_back_to_the_filename(tmp_path):
    """The two curated files have no **Date:** header. Measured across all 164
    real files, the header and the filename never disagree."""
    body = CURATED.replace("# Session: 2026-06-05\n", "")
    digest = parse_digest(_write(tmp_path, "2026-06-05-thing-session.tmp", body))
    assert digest.date == "2026-06-05"


def test_undateable_file_is_rejected(tmp_path):
    path = tmp_path / "notes-session.tmp"
    path.write_text("**Project:** X\n", encoding="utf-8")
    assert parse_digest(path) is None


def test_a_digest_of_pure_noise_is_still_worth_a_row(tmp_path):
    """Two real digests have nothing but harness text in Tasks, yet still record
    that N messages of work happened in this project with these tools."""
    body = """\
# Session: 2026-06-23
**Date:** 2026-06-23
**Project:** WORK
**Worktree:** C:\\Users\\x\\WORK

<!-- ECC:SUMMARY:START -->
### Tasks
- <system-reminder>noise</system-reminder>

### Tools Used
Bash, Agent

### Stats
- Total user messages: 10
<!-- ECC:SUMMARY:END -->
"""
    digest = parse_digest(_write(tmp_path, "2026-06-23-WORK-session.tmp", body))
    assert digest.tasks == ()
    assert not digest.is_empty
    assert digest.message_count == 10


# --------------------------------------------------------------------------
# Scrubbing is a precondition, not a polish item
# --------------------------------------------------------------------------


def test_a_credential_never_reaches_the_row(tmp_path):
    """One digest in the real corpus contains a plaintext server password."""
    body = GENERATED.replace(
        "- add a bbox selector to the dataset widget",
        "- SSH username root Root password Hunter2Hunter2",
    )
    digest = parse_digest(_write(tmp_path, "2026-04-11-x-session.tmp", body))

    joined = " ".join(digest.tasks)
    assert "Hunter2Hunter2" not in joined
    assert REDACTION in joined
    assert digest.redactions


def test_the_worktree_is_not_scrubbed(tmp_path):
    """It is a filesystem path and the only reliable project key; redacting it
    would unlink the digest from its project."""
    digest = parse_digest(_write(tmp_path, "2026-04-11-x-session.tmp", GENERATED))
    assert digest.worktree == r"C:\proj\Overture"


# --------------------------------------------------------------------------
# Backfill
# --------------------------------------------------------------------------


def test_backfill_imports_and_links_to_a_registered_project(repos, tmp_path):
    worktree = str(tmp_path / "Overture")
    (tmp_path / "Overture").mkdir()
    repos.projects.upsert("Overture", worktree)
    digests = tmp_path / "sessions"
    digests.mkdir()
    _write(digests, "2026-04-11-Overture-session.tmp", GENERATED, worktree=worktree)

    result = backfill_digests(repos, root=digests)

    assert result.imported == 1
    assert result.linked == 1
    assert result.unlinked == 0
    assert repos.digests.count() == 1


def test_backfill_auto_registers_an_unseen_project(repos, tmp_path):
    worktree = tmp_path / "NewProj"
    worktree.mkdir()
    digests = tmp_path / "sessions"
    digests.mkdir()
    _write(digests, "2026-04-11-NewProj-session.tmp", GENERATED, worktree=str(worktree))

    result = backfill_digests(repos, root=digests)

    assert result.projects_registered == 1
    assert repos.projects.find_by_path(str(worktree)) is not None


def test_a_worktree_that_no_longer_exists_stays_unlinked(repos, tmp_path):
    """Six of the 49 real worktrees are gone. The row is still worth keeping."""
    digests = tmp_path / "sessions"
    digests.mkdir()
    _write(digests, "2026-04-11-Gone-session.tmp", GENERATED, worktree=r"D:\deleted\Gone")

    result = backfill_digests(repos, root=digests)

    assert result.imported == 1
    assert result.linked == 0
    assert result.unlinked == 1


def test_backfill_is_idempotent(repos, tmp_path):
    digests = tmp_path / "sessions"
    digests.mkdir()
    _write(digests, "2026-04-11-x-session.tmp", GENERATED)

    first = backfill_digests(repos, root=digests)
    second = backfill_digests(repos, root=digests)

    assert first.imported == 1
    assert second.imported == 0 and second.skipped == 1
    assert repos.digests.count() == 1


def test_a_rewritten_digest_updates_in_place(repos, tmp_path):
    """A digest file is appended to all day, so mtime is the trigger."""
    digests = tmp_path / "sessions"
    digests.mkdir()
    path = _write(digests, "2026-04-11-x-session.tmp", GENERATED)
    backfill_digests(repos, root=digests)

    path.write_text(
        GENERATED.format(worktree=r"C:\proj\Overture").replace(
            "Total user messages: 12", "Total user messages: 30"
        ),
        encoding="utf-8",
    )
    import os

    os.utime(path, (0, 0))  # force a different mtime

    result = backfill_digests(repos, root=digests)

    assert result.updated == 1
    assert repos.digests.count() == 1, "updated, not duplicated"
    assert repos.digests.find_by_source(str(path)).message_count == 30


def test_one_unreadable_file_does_not_stop_the_sweep(repos, tmp_path):
    digests = tmp_path / "sessions"
    digests.mkdir()
    _write(digests, "2026-04-11-good-session.tmp", GENERATED)
    (digests / "2026-04-12-bad-session.tmp").write_text("no date here", encoding="utf-8")

    result = backfill_digests(repos, root=digests)

    assert result.imported == 1
    assert result.skipped == 1


def test_denylisted_worktree_is_not_linked(repos, tmp_path):
    denied = tmp_path / "Private"
    denied.mkdir()
    repos.settings.set("capture_denylist", str(denied))
    digests = tmp_path / "sessions"
    digests.mkdir()
    _write(digests, "2026-04-11-Private-session.tmp", GENERATED, worktree=str(denied))

    result = backfill_digests(repos, root=digests)

    assert result.projects_registered == 0
    assert result.linked == 0


def test_empty_directory_returns_an_empty_result(repos, tmp_path):
    digests = tmp_path / "sessions"
    digests.mkdir()
    assert backfill_digests(repos, root=digests).imported == 0


@pytest.mark.parametrize("field_name", ["tasks", "files", "tools"])
def test_list_fields_round_trip_through_the_database(repos, tmp_path, field_name):
    digests = tmp_path / "sessions"
    digests.mkdir()
    path = _write(digests, "2026-04-11-x-session.tmp", GENERATED)
    backfill_digests(repos, root=digests)

    stored = getattr(repos.digests.find_by_source(str(path)), field_name)
    assert stored and "\n" in stored, "stored newline-separated"
