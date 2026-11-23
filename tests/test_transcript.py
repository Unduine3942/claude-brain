"""Tests for reading transcripts."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_brain.core.transcript import (
    discover_transcripts,
    is_sidechain_path,
    read_transcript,
)

# --------------------------------------------------------------------------
# Sidechain exclusion. A bare rglob swept these in, which is how 819 of 882
# rows became subagent and workflow noise.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        r"C:\p\C--x\abc\subagents\agent-a1b2.jsonl",
        r"C:\p\C--x\abc\subagents\workflows\wf_123\agent-a1.jsonl",
        r"C:\p\C--x\wf_6c33d7d8-108\abc.jsonl",
        r"C:\p\C--x\abc\tool-results\r.jsonl",
        r"C:\p\C--x\agent-abe842160067bfed4.jsonl",
    ],
)
def test_sidechain_paths_are_detected(raw):
    assert is_sidechain_path(Path(raw))


@pytest.mark.parametrize(
    "raw",
    [
        r"C:\p\C--Users-jdoe-Desktop-Movie-Server\6c971d85-6b6a.jsonl",
        r"C:\p\C--Users-jdoe\17ce9588-df23.jsonl",
    ],
)
def test_real_session_paths_are_kept(raw):
    assert not is_sidechain_path(Path(raw))


def test_discover_excludes_sidechains(make_transcript, tmp_path):
    make_transcript("aaaa1111-0000-0000-0000-000000000000")
    make_transcript("agent-deadbeef", sidechain=True)
    root = tmp_path / "projects"

    found = discover_transcripts(root)

    assert len(found) == 1
    assert found[0].stem == "aaaa1111-0000-0000-0000-000000000000"


def test_discover_missing_root_is_empty(tmp_path):
    assert discover_transcripts(tmp_path / "nope") == []


# --------------------------------------------------------------------------
# Field extraction
# --------------------------------------------------------------------------


def test_reads_identity_and_span(make_transcript):
    path = make_transcript(
        cwd=r"C:\Users\test\Desktop\Demo",
        timestamps=("2026-05-01T10:00:00.000Z", "2026-06-02T12:00:00.000Z"),
    )

    summary = read_transcript(path)

    assert summary.session_id == "11111111-2222-3333-4444-555555555555"
    assert summary.cwd == r"C:\Users\test\Desktop\Demo"
    assert summary.started_at == "2026-05-01T10:00:00.000Z"
    assert summary.ended_at == "2026-06-02T12:00:00.000Z"
    assert summary.date == "2026-05-01"


def test_accepts_snake_case_session_id(make_transcript):
    """Different record types use different casings; missing one loses the id."""
    path = make_transcript(snake_case_id=True)
    assert read_transcript(path).session_id == "11111111-2222-3333-4444-555555555555"


def test_counts_tool_uses_and_collects_edited_files(make_transcript):
    path = make_transcript(edits=(r"C:\a.py", r"C:\b.py"))

    summary = read_transcript(path)

    assert summary.tool_count == 2
    assert summary.edited_files == (r"C:\a.py", r"C:\b.py")
    assert "Edit" in summary.tool_names


def test_duplicate_edits_recorded_once(make_transcript):
    path = make_transcript(edits=(r"C:\a.py", r"C:\a.py"))
    assert read_transcript(path).edited_files == (r"C:\a.py",)


def test_user_prompts_are_captured(make_transcript):
    path = make_transcript(prompts=("first thing", "second thing"))
    assert read_transcript(path).user_messages == ("first thing", "second thing")


@pytest.mark.parametrize(
    "noise",
    [
        "<local-command-caveat>Caveat: the messages below were generated</local-command-caveat>",
        "<command-name>/effort</command-name>",
        "<task-notification><task-id>abc</task-id></task-notification>",
        "This session is being continued from a previous conversation that ran out of context.",
        "Previous session summary:\n# Session: 2026-01-01",
        "Base directory for this skill is /x",
    ],
)
def test_harness_noise_is_filtered(make_transcript, noise):
    """These appear as `user` records but were never typed by the user."""
    path = make_transcript(prompts=("real prompt", noise))
    assert read_transcript(path).user_messages == ("real prompt",)


def test_compact_summary_record_is_skipped(make_transcript):
    path = make_transcript(
        prompts=("real prompt",),
        extra_records=(
            {
                "type": "user",
                "isCompactSummary": True,
                "message": {"role": "user", "content": [{"type": "text", "text": "summary"}]},
            },
        ),
    )
    assert read_transcript(path).user_messages == ("real prompt",)


def test_sidechain_records_are_counted_not_merged(make_transcript):
    path = make_transcript(
        prompts=("mine",),
        extra_records=(
            {
                "type": "user",
                "isSidechain": True,
                "message": {"role": "user", "content": [{"type": "text", "text": "subagent"}]},
            },
        ),
    )

    summary = read_transcript(path)

    assert summary.user_messages == ("mine",)
    assert summary.sidechain_records == 1


def test_injected_summary_is_counted_never_trusted(make_transcript):
    """The injected block is another session's digest. Count it, never use it."""
    path = make_transcript(
        prompts=("mine",),
        extra_records=(
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_success",
                    "content": "## Session Summary\n**Date:** 2020-01-01\n**Project:** other",
                },
            },
        ),
    )

    summary = read_transcript(path)

    assert summary.injected_summaries == 1
    assert summary.date != "2020-01-01", "injected date must not become the session's date"
    assert "Session Summary" not in " ".join(summary.user_messages)


def test_compaction_boundary_counted(make_transcript):
    path = make_transcript(extra_records=({"type": "system", "compactMetadata": {"preTokens": 1}},))
    assert read_transcript(path).compactions == 1


# --------------------------------------------------------------------------
# Robustness: a damaged transcript must never break ingest.
# --------------------------------------------------------------------------


def test_malformed_lines_counted_and_skipped(make_transcript):
    path = make_transcript(prompts=("ok",), malformed=3)

    summary = read_transcript(path)

    assert summary.malformed_lines == 3
    assert summary.user_messages == ("ok",)


def test_missing_file_returns_empty_summary(tmp_path):
    summary = read_transcript(tmp_path / "absent.jsonl")
    assert summary.is_empty or summary.tool_count == 0


def test_unknown_record_types_are_tolerated(make_transcript):
    """Forward compatibility: new record types must not break the reader."""
    path = make_transcript(
        prompts=("ok",),
        extra_records=({"type": "some-future-record", "payload": {"x": 1}},),
    )

    summary = read_transcript(path)

    assert summary.user_messages == ("ok",)
    assert "some-future-record" in summary.record_counts


def test_date_falls_back_to_mtime_without_timestamps(make_transcript):
    path = make_transcript(prompts=("ok",), timestamps=("",))
    assert len(read_transcript(path).date) == 10


# --------------------------------------------------------------------------
# Session metadata Claude Code records itself. For sessions driven entirely by
# a slash command there is no user prose, so these are the only description.
# --------------------------------------------------------------------------


def test_captures_ai_title_agent_name_and_last_prompt(make_transcript):
    path = make_transcript(
        prompts=(),
        extra_records=(
            {"type": "ai-title", "aiTitle": "System performance optimization"},
            {"type": "agent-name", "agentName": "jellyfin-remote-streaming-setup"},
            {"type": "last-prompt", "lastPrompt": "run a full system scan"},
        ),
    )

    summary = read_transcript(path)

    assert summary.ai_title == "System performance optimization"
    assert summary.agent_name == "jellyfin-remote-streaming-setup"
    assert summary.last_prompt == "run a full system scan"


def test_pasted_content_wrapper_is_filtered(make_transcript):
    path = make_transcript(
        prompts=("real prompt", '<pasted_content id="79ab">blob</pasted_content>')
    )
    assert read_transcript(path).user_messages == ("real prompt",)
