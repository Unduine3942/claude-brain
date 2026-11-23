"""Tests for hook registration and the hook entry point.

Every assertion about the generated config encodes a Windows or Claude Code
behaviour that silently breaks the naive version - a shell-form command gets
its backslashes eaten, an async context hook contributes nothing, a plugin
scoped SessionEnd never fires at all. Silent failure is the whole hazard here,
so the shape of the config is tested rather than assumed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from claude_brain.core import hooks

HOOK_SCRIPT = Path(__file__).resolve().parents[1] / "hooks" / "brain_hook.py"


@pytest.fixture
def home(tmp_path, monkeypatch):
    target = tmp_path / "home"
    (target / ".claude").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: target))
    return target


# --------------------------------------------------------------------------
# The generated configuration
# --------------------------------------------------------------------------


def test_all_three_events_are_configured():
    assert set(hooks.build_hook_config()) == {"SessionStart", "SessionEnd", "PreCompact"}


def test_exec_form_is_used_everywhere():
    """Shell form would have its backslashes eaten by Git Bash, turning
    C:\\Users\\x\\.local\\bin into C:Usersx.localbin (claude-code#21878)."""
    config = hooks.build_hook_config()
    for blocks in config.values():
        for block in blocks:
            for entry in block["hooks"]:
                assert entry["type"] == "command"
                assert isinstance(entry["args"], list), "args means exec form"
                assert entry["args"], "the script path must be passed as an argument"


def test_paths_are_absolute_and_forward_slashed():
    entry = hooks.build_hook_config()["SessionEnd"][0]["hooks"][0]
    assert "\\" not in entry["command"]
    assert "\\" not in entry["args"][0]
    assert Path(entry["args"][0]).is_absolute()


def test_the_interpreter_is_windowless_where_one_exists():
    """pythonw.exe keeps a console window from flashing on every session."""
    entry = hooks.build_hook_config()["SessionEnd"][0]["hooks"][0]
    expected = Path(sys.executable).with_name("pythonw.exe")
    if expected.is_file():
        assert entry["command"].endswith("pythonw.exe")
    else:  # pragma: no cover - non-Windows
        assert entry["command"] == Path(sys.executable).as_posix()


def test_the_context_hook_is_synchronous_and_the_sweeps_are_not():
    """An async hook's stdout is never injected, so an async context hook would
    have nothing to contribute."""
    config = hooks.build_hook_config()
    start = config["SessionStart"][0]["hooks"]

    context = next(e for e in start if e["args"][-1] == "context")
    sweep = next(e for e in start if e["args"][-1] == "sweep")

    assert "async" not in context
    assert context["timeout"] == hooks.CONTEXT_TIMEOUT, "it blocks session start"
    assert sweep["async"] is True

    for event in ("SessionEnd", "PreCompact"):
        assert config[event][0]["hooks"][0]["async"] is True


def test_session_start_matcher_covers_every_trigger():
    matcher = hooks.build_hook_config()["SessionStart"][0]["matcher"]
    assert "," not in matcher, "matchers are pipe-separated, not comma-separated"
    assert set(matcher.split("|")) == {"startup", "resume", "clear", "compact", "fork"}


def test_the_sweep_events_take_no_matcher():
    """Only SessionStart has trigger sources to discriminate."""
    for event in ("SessionEnd", "PreCompact"):
        assert "matcher" not in hooks.build_hook_config()[event][0]


# --------------------------------------------------------------------------
# Installing and removing
# --------------------------------------------------------------------------


def test_install_creates_the_entries(home):
    hooks.install_hooks()

    data = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert set(data["hooks"]) >= {"SessionStart", "SessionEnd", "PreCompact"}
    assert hooks.status().missing == ()


def test_install_preserves_another_tool_s_hooks(home):
    """The only thing that must never happen to this file."""
    settings = home / ".claude" / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "model": "opus",
                "hooks": {
                    "PreToolUse": [{"matcher": "*", "hooks": [{"command": "observe.sh"}]}],
                    "SessionEnd": [{"hooks": [{"command": "their-script.sh"}]}],
                },
            }
        ),
        encoding="utf-8",
    )

    hooks.install_hooks()
    data = json.loads(settings.read_text(encoding="utf-8"))

    assert data["model"] == "opus", "unrelated settings survive"
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "observe.sh"
    commands = json.dumps(data["hooks"]["SessionEnd"])
    assert "their-script.sh" in commands, "their SessionEnd hook survives"
    assert hooks.HOOK_MARKER in commands, "ours was added alongside"


def test_install_is_idempotent(home):
    hooks.install_hooks()
    first = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    hooks.install_hooks()
    second = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))

    assert first == second, "re-installing must not stack duplicate blocks"


def test_install_backs_up_what_was_there(home):
    settings = home / ".claude" / "settings.json"
    settings.write_text('{"model": "opus"}', encoding="utf-8")

    hooks.install_hooks()

    backup = settings.with_suffix(".json.brain-backup")
    assert backup.is_file()
    assert json.loads(backup.read_text(encoding="utf-8")) == {"model": "opus"}


def test_install_works_when_there_is_no_settings_file(home):
    (home / ".claude" / "settings.json").unlink(missing_ok=True)

    hooks.install_hooks()

    assert hooks.status().ok


def test_uninstall_removes_only_our_entries(home):
    settings = home / ".claude" / "settings.json"
    settings.write_text(
        json.dumps({"hooks": {"SessionEnd": [{"hooks": [{"command": "theirs.sh"}]}]}}),
        encoding="utf-8",
    )
    hooks.install_hooks()

    removed = hooks.uninstall_hooks()
    data = json.loads(settings.read_text(encoding="utf-8"))

    assert removed == 3
    assert "theirs.sh" in json.dumps(data["hooks"]["SessionEnd"])
    assert hooks.HOOK_MARKER not in json.dumps(data["hooks"])


def test_uninstall_drops_an_event_it_emptied(home):
    hooks.install_hooks()
    hooks.uninstall_hooks()

    data = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "PreCompact" not in data.get("hooks", {})


def test_uninstall_on_a_clean_machine_is_a_noop(home):
    assert hooks.uninstall_hooks() == 0


def test_dry_run_changes_nothing(home):
    hooks.install_hooks(dry_run=True)
    assert not (home / ".claude" / "settings.json").is_file() or hooks.status().missing


# --------------------------------------------------------------------------
# Status reporting
# --------------------------------------------------------------------------


def test_status_reports_a_missing_settings_file(home):
    (home / ".claude" / "settings.json").unlink(missing_ok=True)

    state = hooks.status()

    assert not state.ok
    assert state.settings_problem and "not found" in state.settings_problem
    assert any("not found" in problem for problem in state.problems)


def test_status_reports_a_corrupt_settings_file(home):
    (home / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")

    state = hooks.status()

    assert not state.ok
    assert state.settings_problem and "cannot read" in state.settings_problem


def test_status_distinguishes_partial_registration(home):
    hooks.install_hooks()
    settings = home / ".claude" / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8"))
    del data["hooks"]["PreCompact"]
    settings.write_text(json.dumps(data), encoding="utf-8")

    state = hooks.status()

    assert state.installed == ("SessionStart", "SessionEnd")
    assert state.missing == ("PreCompact",)
    assert "PreCompact" in state.describe()


def test_describe_names_every_event(home):
    text = hooks.status().describe()
    for event in hooks.EVENTS:
        assert event in text


# --------------------------------------------------------------------------
# The hook entry point itself
# --------------------------------------------------------------------------


def _run_hook(mode, payload, *, env_home=None):
    env = None
    if env_home is not None:
        import os

        env = {**os.environ, "USERPROFILE": str(env_home), "HOME": str(env_home)}
    return subprocess.run(
        [sys.executable, str(HOOK_SCRIPT), mode],
        input=json.dumps(payload).encode(),
        capture_output=True,
        env=env,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"hook_event_name": "SessionStart"},
        {"cwd": "C:\\definitely\\not\\a\\project"},
    ],
)
def test_the_hook_never_exits_non_zero(payload):
    """Exit 2 on PreToolUse blocks the tool call; another plugin on this
    machine wedged itself exactly that way."""
    assert _run_hook("context", payload).returncode == 0


def test_garbage_on_stdin_is_survivable():
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT), "context"],
        input=b"this is not json at all",
        capture_output=True,
    )
    assert result.returncode == 0


def test_an_unknown_mode_is_not_fatal():
    assert _run_hook("no-such-mode", {}).returncode == 0


def test_context_output_is_ascii_only_json():
    """pythonw hands the script a cp1252 stdout, so an em dash in a stored
    summary would otherwise raise UnicodeEncodeError."""
    result = _run_hook("context", {"hook_event_name": "SessionStart", "cwd": str(Path.cwd())})

    assert result.returncode == 0
    if not result.stdout:
        pytest.skip("no registered project for the test's working directory")
    text = result.stdout.decode("ascii")  # raises if any byte is non-ASCII
    payload = json.loads(text)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert payload["hookSpecificOutput"]["additionalContext"]


def test_an_unregistered_directory_produces_no_output():
    result = _run_hook("context", {"cwd": "C:\\definitely\\not\\a\\project"})
    assert result.stdout == b""
