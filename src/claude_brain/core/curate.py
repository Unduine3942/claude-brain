"""Turning a captured session into prose worth reading, by asking Claude.

The hooks always write the structured record; this is the other half of the
hybrid. A generated note lists the prompts and the files touched, which is a
good index and a poor memory - the part worth keeping is *what was decided and
why*, and only a model that has read the session can write that.

Isolation is the whole design problem. A curation call started from a hook is
itself a session, which fires the hooks, which curate, which start a session.
Two independent barriers break that loop:

:data:`GUARD_ENV`
    Set in the child's environment. The hook entry point checks it before doing
    anything and returns immediately, so the spawned call's hooks are inert.
    The child's own children inherit it too, so the guard holds at any depth.
``--no-session-persistence``
    The spawned call writes no transcript, so even if a hook did run there
    would be nothing for it to ingest.

``--restricted`` is also passed. It is not a recursion barrier - it removes
Bash, PowerShell and the REPL, because this call reads text and writes text and
has no business running commands on the way through.

**``--bare`` is deliberately not passed**, although it was the original design.
It skips hooks, which is exactly what was wanted, but its own help text lists
*keychain reads* among the things it skips - and that is where the OAuth
credentials live. Measured on this machine: ``claude -p … --bare`` exits 1 with
"Not logged in · Please run /login", with or without an explicit
``--settings``. The environment guard does the same job and, unlike a flag
whose behaviour is not ours to control, it is directly testable.

Everything sent is scrubbed first. The curation prompt is the one place where
captured text leaves this machine.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = [
    "GUARD_ENV",
    "CurationResult",
    "build_prompt",
    "claude_executable",
    "curate_session",
]

#: Set in the spawned process's environment; the hook refuses to run with it.
GUARD_ENV = "CLAUDE_BRAIN_CURATING"

_TIMEOUT_SECONDS = 180
_MAX_PROMPT_CHARS = 24_000
_MAX_FILES = 40

_INSTRUCTIONS = """\
You are writing a durable session note for a developer's own knowledge vault.
They will read it months from now, with none of today's context.

Write GitHub-flavoured Markdown, 150-400 words, with these sections and no
preamble:

## What was done
## Why it was done that way
## What to know next time

Rules:
- Be specific. Name the files, commands, numbers and error messages involved.
- Record decisions and their reasoning, especially anything that was tried and
  rejected - that is the part that is expensive to rediscover.
- Say plainly when the record is thin rather than padding it out.
- Do not invent anything that is not in the material below.
- Do not repeat these instructions, and do not add a title; the note has one.
"""


def claude_executable() -> str | None:
    """Locate the ``claude`` CLI, or ``None`` if it is not installed.

    The user-local install is checked explicitly because hooks and services
    routinely inherit a PATH that does not include ``~/.local/bin``.
    """
    found = shutil.which("claude")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "claude"
    for suffix in ("", ".exe", ".cmd"):
        probe = candidate.with_name(candidate.name + suffix)
        if probe.is_file():
            return str(probe)
    return None


@dataclass(frozen=True)
class CurationResult:
    text: str = ""
    status: str = "skipped"  # curated | skipped | unavailable | failed
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "curated" and bool(self.text.strip())


def build_prompt(session, files: list | None = None, digest_text: str | None = None) -> str:
    """Assemble the curation prompt. Everything in it is scrubbed."""
    from claude_brain.core.scrub import load_deny_literals, scrub

    literals = load_deny_literals()

    def clean(value: str | None) -> str:
        return scrub(value or "", literals=literals).text

    parts = [_INSTRUCTIONS, "", "---", "", f"Session date: {session.date}"]
    if session.summary:
        parts += ["", "What the developer asked, in order:", "", clean(session.summary)]
    if files:
        shown = [item.file_path for item in files[:_MAX_FILES]]
        parts += ["", "Files changed:", ""]
        parts += [f"- {clean(path)}" for path in shown]
        if len(files) > _MAX_FILES:
            parts.append(f"- (and {len(files) - _MAX_FILES} more)")
    if session.notes:
        parts += ["", "Notes already recorded:", "", clean(session.notes)]
    if digest_text:
        parts += ["", "That day's digest:", "", clean(digest_text)]
    parts += ["", f"Tool calls: {session.tool_count}. Files changed: {session.file_count}."]

    prompt = "\n".join(parts)
    return prompt[:_MAX_PROMPT_CHARS]


def curate_session(
    session,
    files: list | None = None,
    *,
    digest_text: str | None = None,
    timeout: int = _TIMEOUT_SECONDS,
    executable: str | None = None,
) -> CurationResult:
    """Ask Claude for a prose write-up of one session.

    Never raises. Curation is an enhancement: when it cannot run, the
    structured note the hooks already wrote stands on its own.
    """
    binary = executable or claude_executable()
    if not binary:
        return CurationResult(status="unavailable", detail="the claude CLI was not found")

    prompt = build_prompt(session, files, digest_text)
    command = [
        binary,
        "-p",
        prompt,
        "--no-session-persistence",
        "--restricted",
        "--output-format",
        "json",
    ]

    environment = {**os.environ, GUARD_ENV: "1"}
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
            # The prompt is an argument, so there is nothing to send on stdin -
            # but an inherited pipe that never closes makes `claude` wait three
            # seconds for input it will never get and then exit non-zero.
            # Measured: this is why the first real curation call failed.
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CurationResult(status="failed", detail=f"timed out after {timeout}s")
    except OSError as exc:
        return CurationResult(status="failed", detail=str(exc))

    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        return CurationResult(
            status="failed",
            detail=f"exit {completed.returncode}: {detail[-1] if detail else 'no output'}",
        )

    text = _extract_text(completed.stdout)
    if not text:
        return CurationResult(status="failed", detail="no usable text in the response")
    return CurationResult(text=text, status="curated")


def _extract_text(stdout: str) -> str:
    """Pull the message out of ``--output-format json``, tolerating plain text.

    The envelope's shape is not ours to depend on, so a few known key names are
    tried and anything unparseable falls back to the raw output.
    """
    raw = (stdout or "").strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, dict):
        for key in ("result", "text", "response", "content", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list):
                blocks = [
                    block.get("text", "")
                    for block in value
                    if isinstance(block, dict) and block.get("type") in (None, "text")
                ]
                joined = "\n".join(part for part in blocks if part).strip()
                if joined:
                    return joined
    return raw
