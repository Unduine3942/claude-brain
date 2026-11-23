"""Claude Code hook entry point for Claude Brain.

Two modes, because the two jobs have opposite requirements:

``context``
    Runs **synchronously** on ``SessionStart`` and prints one JSON object with
    ``hookSpecificOutput.additionalContext``. It must be synchronous: an async
    hook's stdout is never injected, so there would be nothing to inject. It
    therefore only *reads*, through a read-only connection.

``sweep``
    Runs **asynchronously** on ``SessionStart``, ``SessionEnd`` and
    ``PreCompact``. Ingests transcripts and refreshes the search index. Prints
    nothing at all.

Why three events rather than one. ``SessionEnd`` cannot be relied on: it is
unable to block, all ``SessionEnd`` hooks share a 1.5-second budget, and it
fires for only about half of headless sessions. So the sweep is idempotent and
is triggered opportunistically from several places - the ``SessionStart`` sweep
is what makes a missed ``SessionEnd`` harmless, and ``PreCompact`` captures the
transcript while the detail still exists.

Rules this file must never break:

* **Never exit non-zero.** Exit 2 on ``PreToolUse`` blocks the tool call, and
  that mechanism is exactly how another plugin on this machine wedged itself.
  Every failure here is logged and swallowed.
* **``sweep`` prints nothing** - not even to stderr. ``SessionEnd`` shows stderr
  to the user, and ``PreCompact`` stdout is not a safe channel.
* **``context`` prints ASCII-only JSON.** ``pythonw.exe`` hands us a cp1252
  stdout, so an em dash in a stored summary would otherwise raise
  UnicodeEncodeError. ``json.dumps`` escapes non-ASCII by default; that is load
  bearing, not incidental.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

#: Mirrors claude_brain.core.curate.GUARD_ENV. Duplicated as a literal on
#: purpose: this check must work before any project import is attempted.
_GUARD_ENV = "CLAUDE_BRAIN_CURATING"

#: Written next to the database, never inside a synced vault.
_LOG_NAME = "hook.log"
_MAX_LOG_BYTES = 512 * 1024


def _log_path() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    directory = Path(local) / "ClaudeBrain" / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / _LOG_NAME


def _log(message: str) -> None:
    """Append one line. Silent on failure: logging must never break a session."""
    try:
        path = _log_path()
        if path.exists() and path.stat().st_size > _MAX_LOG_BYTES:
            path.replace(path.with_suffix(".log.1"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass


def _read_payload() -> dict:
    """Parse the hook payload from stdin. Returns ``{}`` if unreadable."""
    try:
        raw = sys.stdin.read()
    except Exception as exc:
        _log(f"stdin unreadable: {exc}")
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _log(f"payload is not JSON: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def mode_context(payload: dict) -> None:
    """Emit project-scoped prior context for this working directory."""
    from claude_brain.core.context import build_session_context
    from claude_brain.db.connection import get_readonly_connection

    cwd = payload.get("cwd") or str(Path.cwd())
    conn = get_readonly_connection()
    try:
        text = build_session_context(conn, cwd, exclude_session_id=payload.get("session_id"))
    finally:
        conn.close()

    if not text:
        _log(f"context: nothing for {cwd}")
        return

    # ensure_ascii=True (the default) is what makes this safe on a cp1252 stdout.
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": text,
                }
            }
        )
    )
    sys.stdout.flush()
    _log(f"context: {len(text)} chars for {cwd} ({payload.get('source', '?')})")


def mode_sweep(payload: dict) -> None:
    """Ingest whatever is new. Writes nothing to stdout or stderr."""
    from claude_brain.core.ingest import ingest_all
    from claude_brain.db import migrations
    from claude_brain.db.connection import get_connection
    from claude_brain.db.repos import Repos

    event = payload.get("hook_event_name", "?")
    started = time.perf_counter()

    conn = get_connection()
    try:
        migrations.run(conn)
        result = ingest_all(Repos(conn), write_vault=True)
    finally:
        conn.close()

    elapsed = (time.perf_counter() - started) * 1000
    _log(
        f"sweep[{event}]: scanned={result.scanned} ingested={result.ingested} "
        f"skipped={result.skipped} failed={result.failed} "
        f"projects+={result.projects_registered} in {elapsed:.0f}ms"
    )
    for message in result.messages:
        _log(f"  {message}")


_MODES = {"context": mode_context, "sweep": mode_sweep}


def main(argv: list[str] | None = None) -> int:
    """Always returns 0. See the module docstring for why."""
    args = list(sys.argv[1:] if argv is None else argv)
    mode = args[0] if args else "sweep"

    # Curation spawns `claude -p` to write prose about a session. That call is
    # already isolated by --bare, --no-session-persistence and --restricted, but
    # this is the backstop: if a future release changes what --bare skips, the
    # hook still refuses rather than curating a session that is curating.
    if os.environ.get(_GUARD_ENV):
        _log(f"{mode}: refusing to run inside a curation call")
        return 0

    handler = _MODES.get(mode)
    if handler is None:
        _log(f"unknown mode {mode!r}; expected one of {sorted(_MODES)}")
        return 0

    payload = _read_payload()
    try:
        handler(payload)
    except Exception:
        _log(f"{mode} failed:\n{traceback.format_exc()}")
    return 0


if __name__ == "__main__":
    # The package is normally installed (`pip install -e .`). Fall back to the
    # repo layout so the hook keeps working from a plain checkout.
    try:
        import claude_brain  # noqa: F401
    except ModuleNotFoundError:  # pragma: no cover - uninstalled checkout
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    sys.exit(main())
