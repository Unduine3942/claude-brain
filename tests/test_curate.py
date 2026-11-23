"""Tests for the headless curation call.

The two properties that matter are **isolation** (a curation call must not be
able to trigger our own capture, or the loop never terminates) and **failing
soft** (curation is an enhancement; when it cannot run, the structured note the
hooks already wrote has to stand on its own).

The subprocess is faked throughout. Spawning a real `claude -p` from a unit
test would be slow, non-deterministic, and would spend tokens.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

import pytest

from claude_brain.core import curate as C
from claude_brain.core.scrub import REDACTION


@dataclass
class FakeSession:
    session_id: str = "abc-123"
    date: str = "2026-05-01"
    summary: str = "set up the tunnel\nfix the buffering"
    notes: str | None = None
    file_count: int = 2
    tool_count: int = 9


@dataclass
class FakeFile:
    file_path: str


@dataclass
class FakeCompleted:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@pytest.fixture
def spawned(monkeypatch):
    """Capture the command and environment instead of running anything."""
    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return FakeCompleted(stdout=json.dumps({"result": "## What was done\n\nThe work."}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(C, "claude_executable", lambda: "C:/fake/claude.exe")
    return calls


# --------------------------------------------------------------------------
# Isolation: the loop must not be able to close
# --------------------------------------------------------------------------


def test_the_isolation_flags_are_passed(spawned):
    """--no-session-persistence leaves no transcript for a sweep to ingest;
    --restricted removes Bash, PowerShell and the REPL."""
    C.curate_session(FakeSession())

    command = spawned[0]["command"]
    for flag in ("--no-session-persistence", "--restricted"):
        assert flag in command, f"{flag} is missing"


def test_bare_is_not_passed(spawned):
    """--bare skips hooks, which is what was wanted, but its help text lists
    keychain reads among what it skips - and that is where the OAuth
    credentials live. Measured: it exits 1 with "Not logged in". The
    environment guard does the same job and is testable."""
    C.curate_session(FakeSession())
    assert "--bare" not in spawned[0]["command"]


def test_the_guard_variable_is_set_in_the_child(spawned):
    """The primary recursion barrier, since --bare cannot be used."""
    C.curate_session(FakeSession())

    assert spawned[0]["env"][C.GUARD_ENV] == "1"


def test_the_hook_refuses_to_run_under_the_guard(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "hooks" / "brain_hook.py"
    import os

    result = subprocess.run(
        [sys.executable, str(script), "sweep"],
        input=b"{}",
        capture_output=True,
        env={**os.environ, C.GUARD_ENV: "1"},
    )

    assert result.returncode == 0
    assert result.stdout == b""


def test_the_prompt_is_not_passed_through_a_shell(spawned):
    """A prompt is user text; `shell=True` would make it executable."""
    C.curate_session(FakeSession())
    assert spawned[0].get("shell") in (None, False)


def test_json_output_is_requested(spawned):
    C.curate_session(FakeSession())
    command = spawned[0]["command"]
    assert "--output-format" in command
    assert command[command.index("--output-format") + 1] == "json"


# --------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------


def test_the_prompt_carries_the_session_s_own_material():
    prompt = C.build_prompt(FakeSession(), [FakeFile("C:/a.py")])

    assert "2026-05-01" in prompt
    assert "set up the tunnel" in prompt
    assert "C:/a.py" in prompt
    assert "Tool calls: 9" in prompt


def test_the_prompt_asks_for_the_sections_the_note_needs():
    prompt = C.build_prompt(FakeSession())
    for heading in ("## What was done", "## Why it was done that way", "## What to know next time"):
        assert heading in prompt


def test_the_prompt_forbids_invention():
    prompt = C.build_prompt(FakeSession())
    assert "Do not invent" in prompt


def test_the_prompt_is_scrubbed():
    """This is the one place captured text leaves the machine."""
    session = FakeSession(summary="the root password is Hunter2Hunter2 for the box")

    prompt = C.build_prompt(session)

    assert "Hunter2Hunter2" not in prompt
    assert REDACTION in prompt


def test_the_prompt_is_capped():
    session = FakeSession(summary="x" * 100_000)
    assert len(C.build_prompt(session)) <= 24_000


def test_a_long_file_list_is_truncated():
    files = [FakeFile(f"C:/f{i}.py") for i in range(80)]
    prompt = C.build_prompt(FakeSession(), files)
    assert "and 40 more" in prompt


def test_a_digest_is_included_when_given():
    prompt = C.build_prompt(FakeSession(), None, digest_text="the day's tasks")
    assert "the day's tasks" in prompt


# --------------------------------------------------------------------------
# Failing soft
# --------------------------------------------------------------------------


def test_a_successful_call_returns_the_prose(spawned):
    result = C.curate_session(FakeSession())

    assert result.ok
    assert result.status == "curated"
    assert "## What was done" in result.text


def test_a_missing_claude_cli_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(C, "claude_executable", lambda: None)

    result = C.curate_session(FakeSession())

    assert not result.ok
    assert result.status == "unavailable"
    assert "not found" in result.detail


def test_a_non_zero_exit_is_reported(monkeypatch):
    monkeypatch.setattr(C, "claude_executable", lambda: "claude")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: FakeCompleted(returncode=1, stderr="boom\nrate limited")
    )

    result = C.curate_session(FakeSession())

    assert result.status == "failed"
    assert "rate limited" in result.detail


def test_a_timeout_is_reported(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("claude", 5)

    monkeypatch.setattr(C, "claude_executable", lambda: "claude")
    monkeypatch.setattr(subprocess, "run", timeout)

    result = C.curate_session(FakeSession(), timeout=5)

    assert result.status == "failed"
    assert "timed out" in result.detail


def test_an_os_error_is_reported(monkeypatch):
    monkeypatch.setattr(C, "claude_executable", lambda: "claude")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no such file"))
    )

    assert C.curate_session(FakeSession()).status == "failed"


def test_empty_output_is_a_failure_not_an_empty_note(monkeypatch):
    monkeypatch.setattr(C, "claude_executable", lambda: "claude")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(stdout="   "))

    result = C.curate_session(FakeSession())

    assert not result.ok
    assert result.status == "failed"


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stdout",
    [
        '{"result": "the prose"}',
        '{"text": "the prose"}',
        '{"response": "the prose"}',
        '{"content": [{"type": "text", "text": "the prose"}]}',
        '"the prose"',
        "the prose",
    ],
)
def test_text_is_extracted_from_each_envelope_shape(stdout):
    """The envelope's shape is not ours to depend on."""
    assert C._extract_text(stdout) == "the prose"


def test_unparseable_output_falls_back_to_the_raw_text():
    assert C._extract_text("{not json but readable") == "{not json but readable"


def test_blank_output_extracts_to_nothing():
    assert C._extract_text("") == ""
    assert C._extract_text("   \n ") == ""


# --------------------------------------------------------------------------
# Locating the CLI
# --------------------------------------------------------------------------


def test_the_user_local_install_is_found_when_not_on_path(monkeypatch, tmp_path):
    """Hooks routinely inherit a PATH without ~/.local/bin."""
    import shutil
    from pathlib import Path

    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "claude.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    found = C.claude_executable()

    assert found and found.endswith("claude.exe")


def test_path_wins_when_claude_is_on_it(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: "C:/tools/claude.exe")
    assert C.claude_executable() == "C:/tools/claude.exe"


# --------------------------------------------------------------------------
# The curated note
# --------------------------------------------------------------------------


def test_curated_prose_replaces_the_prompt_list_in_the_note(tmp_path):
    from claude_brain.core import vault_writer as V

    result = V.write_session_note(
        tmp_path,
        "Demo",
        FakeSession(),
        [FakeFile("C:/a.py")],
        curated="## What was done\n\nRewrote the ingest path.",
    )

    text = result.path.read_text(encoding="utf-8")
    assert "Rewrote the ingest path." in text
    assert "set up the tunnel" not in text, "the prose replaces the prompt dump"
    assert "`C:/a.py`" in text, "the file list is still useful"
    assert "brain/curated" in text, "so the note is findable as curated"


def test_stdin_is_closed_for_the_child(spawned):
    """The prompt is an argument, so nothing is sent on stdin - but an
    inherited pipe that never closes makes `claude` wait for input it will
    never get and then exit non-zero. That is how the first real call failed."""
    C.curate_session(FakeSession())
    assert spawned[0]["stdin"] == subprocess.DEVNULL
