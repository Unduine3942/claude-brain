"""Register and remove the capture hooks in ``~/.claude/settings.json``.

Hand-editing that file is how this goes wrong, so it is done here instead:
paths are computed at install time, the edit is idempotent, and it is
removable. The user's other hooks are never touched.

Why the configuration looks the way it does - every detail below is a Windows
or Claude Code behaviour that silently breaks the naive version:

* **Hooks live in ``~/.claude/settings.json``, never in a plugin.** A
  plugin-scoped ``SessionEnd`` never fires at all
  (anthropics/claude-code#33458).
* **Exec form** (``command`` plus ``args``) rather than a shell string. Git Bash
  consumes unquoted backslashes, so ``C:\\Users\\x\\.local\\bin`` becomes
  ``C:Usersx.localbin`` (anthropics/claude-code#21878). Exec form also cannot
  run ``.cmd``/``.bat`` shims, which is why the interpreter is invoked directly
  instead of a wrapper.
* **``pythonw.exe``**, so no console window flashes on every session start. It
  hands the script a cp1252 stdout, which the hook accounts for.
* **The context hook is synchronous; the sweeps are not.** An async hook's
  stdout is never injected, so a context hook that ran async would have nothing
  to contribute. It gets a short timeout so a slow brain cannot stall a session.
* **Matchers are case-sensitive and separated by ``|``**, never commas.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = [
    "HookStatus",
    "build_hook_config",
    "install_hooks",
    "settings_path",
    "status",
    "uninstall_hooks",
]

#: Marker identifying our entries, so removal never touches another tool's hooks.
HOOK_MARKER = "brain_hook.py"

#: Every SessionStart trigger, including `fork`, which was added after the
#: original three and is exactly when prior context is most wanted.
SESSION_START_MATCHER = "startup|resume|clear|compact|fork"

#: Seconds. The context hook blocks session start, so it is capped well below
#: the 600 s default; the sweeps are async and their timeout is not enforced.
CONTEXT_TIMEOUT = 10

EVENTS = ("SessionStart", "SessionEnd", "PreCompact")


def settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _interpreter() -> str:
    """Absolute path to ``pythonw.exe``, in forward slashes.

    Falls back to the running interpreter where ``pythonw`` does not exist -
    every platform except Windows, and a Windows install without it.
    """
    executable = Path(sys.executable)
    windowless = executable.with_name("pythonw.exe")
    chosen = windowless if windowless.is_file() else executable
    return chosen.as_posix()


def _hook_script() -> Path:
    """The repo's ``hooks/brain_hook.py``.

    Resolved from this file rather than looked up on PATH: the installed console
    script is a generated shim whose location moves with the environment, while
    the checkout is a stable path the user controls.
    """
    return (Path(__file__).resolve().parents[3] / "hooks" / "brain_hook.py").resolve()


def _entry(script: str, mode: str, *, is_async: bool, timeout: int | None = None) -> dict:
    entry: dict = {
        "type": "command",
        "command": _interpreter(),
        "args": [script, mode],
    }
    if is_async:
        entry["async"] = True
    if timeout is not None:
        entry["timeout"] = timeout
    return entry


def build_hook_config(script: Path | str | None = None) -> dict:
    """The hook entries this tool owns, keyed by event name."""
    target = Path(script) if script is not None else _hook_script()
    path = target.as_posix()
    return {
        "SessionStart": [
            {
                "matcher": SESSION_START_MATCHER,
                "hooks": [
                    _entry(path, "context", is_async=False, timeout=CONTEXT_TIMEOUT),
                    _entry(path, "sweep", is_async=True),
                ],
            }
        ],
        "SessionEnd": [{"hooks": [_entry(path, "sweep", is_async=True)]}],
        "PreCompact": [{"hooks": [_entry(path, "sweep", is_async=True)]}],
    }


def _is_ours(matcher_block: dict) -> bool:
    return HOOK_MARKER in json.dumps(matcher_block.get("hooks", []))


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} does not contain a JSON object")
    return data


def _save(path: Path, data: dict) -> None:
    """Write settings back, keeping a one-deep backup of what was there.

    This file controls the user's whole Claude Code install; a bad write is
    worth being able to undo by hand.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        shutil.copy2(path, path.with_suffix(".json.brain-backup"))
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


@dataclass(frozen=True)
class HookStatus:
    installed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    script: Path | None = None
    script_exists: bool = False
    interpreter: str = ""
    #: Why settings.json could not be read, if it could not. A missing or
    #: corrupt file needs a different fix from an unregistered event, so the
    #: two are reported separately rather than collapsed into "not registered".
    settings_problem: str | None = None

    @property
    def ok(self) -> bool:
        return not self.missing and self.script_exists and not self.settings_problem

    @property
    def problems(self) -> tuple[str, ...]:
        """Everything wrong, in the order it should be fixed."""
        found: list[str] = []
        if self.settings_problem:
            found.append(self.settings_problem)
        if not self.script_exists:
            found.append(f"hook script missing: {self.script}")
        if self.missing:
            found.append(
                "capture hook not registered for: "
                + ", ".join(self.missing)
                + " - run `brain hooks --install`"
            )
        return tuple(found)

    def describe(self) -> str:
        suffix = f"  ({self.settings_problem})" if self.settings_problem else ""
        lines = [f"settings  {settings_path()}{suffix}"]
        lines.append(f"script    {self.script}{'' if self.script_exists else '  (MISSING)'}")
        lines.append(f"python    {self.interpreter}")
        for event in EVENTS:
            mark = "ok" if event in self.installed else "NOT REGISTERED"
            lines.append(f"  {event:14} {mark}")
        return "\n".join(lines)


def status() -> HookStatus:
    """Report which events currently have our hook registered."""
    path = settings_path()
    settings_problem: str | None = None
    data: dict = {}
    if not path.is_file():
        settings_problem = f"{path} not found - hooks cannot be registered"
    else:
        try:
            data = _load(path)
        except RuntimeError as exc:
            settings_problem = str(exc)
            log.warning("%s", exc)
    hooks = data.get("hooks") or {}
    script = _hook_script()
    installed = tuple(
        event
        for event in EVENTS
        if any(_is_ours(block) for block in hooks.get(event, []) if isinstance(block, dict))
    )
    return HookStatus(
        installed=installed,
        missing=tuple(event for event in EVENTS if event not in installed),
        script=script,
        script_exists=script.is_file(),
        interpreter=_interpreter(),
        settings_problem=settings_problem,
    )


def install_hooks(*, dry_run: bool = False) -> tuple[dict, HookStatus]:
    """Register the capture hooks, replacing any previous entries of ours.

    Returns ``(settings, status_after)``. Other tools' hooks on the same events
    are preserved: only blocks containing our marker are replaced.
    """
    path = settings_path()
    data = _load(path)
    hooks = dict(data.get("hooks") or {})
    config = build_hook_config()

    for event, blocks in config.items():
        existing = [
            block
            for block in hooks.get(event, [])
            if isinstance(block, dict) and not _is_ours(block)
        ]
        hooks[event] = existing + blocks

    updated = {**data, "hooks": hooks}
    if not dry_run:
        _save(path, updated)
        log.info("registered capture hooks for %s", ", ".join(config))
    return updated, status() if not dry_run else HookStatus()


def uninstall_hooks(*, dry_run: bool = False) -> int:
    """Remove only our entries. Returns how many blocks were removed."""
    path = settings_path()
    data = _load(path)
    hooks = dict(data.get("hooks") or {})

    removed = 0
    for event in list(hooks):
        blocks = hooks.get(event) or []
        if not isinstance(blocks, list):
            continue
        kept = [b for b in blocks if not (isinstance(b, dict) and _is_ours(b))]
        removed += len(blocks) - len(kept)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event)

    if removed and not dry_run:
        _save(path, {**data, "hooks": hooks})
        log.info("removed %d hook block(s)", removed)
    return removed
