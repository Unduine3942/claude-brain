"""Reading Claude Code session transcripts (``~/.claude/projects/**/*.jsonl``).

Replaces the field-scraping that derived a session's date and summary from an
injected ``## Session Summary`` block. That block is the *previous* session's
digest, injected at SessionStart and often from a different project, so every
value taken from it was wrong.

Everything here comes from the session's own records instead:

================  =========================================================
``session_id``    the transcript's own id, accepting both ``sessionId`` and
                  ``session_id`` — different record types use each casing
``cwd``           the absolute project path; the reliable project key, where
                  the enclosing directory name is a lossy slug
``started_at``    ``min``/``max`` of record timestamps, i.e. the real span
``user_messages`` the session's own prompts, with harness noise filtered
``edited_files``  ``file_path`` from this session's own edit tool calls
================  =========================================================

Design note: parsing is *tolerant*, not exhaustive. A real transcript carries
at least 14 record types and three quarters of its records hold no message at
all, so unknown types are skipped rather than modelled. That makes the reader
forward-compatible with record types Claude Code adds later, which an
exhaustive schema would reject.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = [
    "TranscriptSummary",
    "read_transcript",
    "discover_transcripts",
    "is_sidechain_path",
    "is_noise",
    "path_to_claude_slug",
]

#: Tool names whose invocation means a file was modified.
_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "StrReplace"})

#: Keys a tool call may use to name the file it touched.
_PATH_KEYS = ("file_path", "notebook_path", "path", "filePath")

#: Path fragments marking a transcript that is NOT a session in its own right.
#: ``rglob`` previously swept these in, which is how 819 of 882 rows became
#: subagent sidechains and ephemeral workflow runs.
_SIDECHAIN_PARTS = ("subagents", "tool-results")
_SIDECHAIN_PREFIXES = ("wf_", "agent-")

#: Harness-generated text that appears as a ``user`` record but was never typed
#: by the user. Matched case-insensitively against the start of the message.
_NOISE_PREFIXES = (
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<task-notification>",
    "<system-reminder>",
    "<user-prompt-submit-hook>",
    "<pasted_content",
    "caveat: the messages below were generated",
    "this session is being continued from a previous conversation",
    "previous session summary:",
    "base directory for this skill",
    "[request interrupted by user",
    "your task is to create a detailed summary",
)

_MAX_USER_MESSAGES = 40
_MAX_EDITED_FILES = 200
_MAX_MESSAGE_CHARS = 500


@dataclass(frozen=True)
class TranscriptSummary:
    """Facts derived from a transcript's own records. Immutable."""

    path: Path
    session_id: str | None = None
    cwd: str | None = None
    git_branch: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    user_messages: tuple[str, ...] = ()
    edited_files: tuple[str, ...] = ()
    #: Claude Code's own generated session title, e.g. "System performance
    #: optimization and bloatware removal". Often a better one-line description
    #: than the raw prompts, and present even in sessions driven entirely by a
    #: slash command, where there is no user prose at all.
    ai_title: str | None = None
    agent_name: str | None = None
    last_prompt: str | None = None
    tool_count: int = 0
    tool_names: tuple[str, ...] = ()
    record_counts: dict[str, int] = field(default_factory=dict)
    malformed_lines: int = 0
    sidechain_records: int = 0
    compactions: int = 0
    injected_summaries: int = 0

    @property
    def date(self) -> str:
        """Start date as ``YYYY-MM-DD``, falling back to the file's mtime."""
        if self.started_at:
            return self.started_at[:10]
        try:
            from datetime import datetime

            return datetime.fromtimestamp(self.path.stat().st_mtime).strftime("%Y-%m-%d")
        except (OSError, ValueError):  # pragma: no cover - unreadable file
            return ""

    @property
    def is_empty(self) -> bool:
        """True when the transcript yielded nothing worth storing.

        Deliberately not based on ``session_id``: that falls back to the file
        stem, so it is always set and would make this property always false —
        storing empty and garbage files as real sessions, which is how the
        previous implementation accumulated hundreds of junk rows.
        """
        if not self.record_counts:
            return True
        return not self.user_messages and self.tool_count == 0 and not self.started_at


def path_to_claude_slug(project_path: str) -> str:
    r"""The ``~/.claude/projects`` directory name Claude Code derives from a path.

    ``C:\Users\x\Desktop\Scripting_Projects\Claude_Brain`` becomes
    ``c--users-x-desktop-scripting-projects-claude-brain``.

    Lossy, and knowing that is the point: every non-alphanumeric character
    collapses to ``-``, so a sibling directory and a subdirectory can produce
    the same slug. It is a *fallback* for records with no ``cwd``, never the
    primary key - prefix-matching on it is what used to collapse
    ``Translator_OSM`` and ``Translator_OSM_Offline`` into one project.
    """
    path = Path(project_path)
    drive = path.drive.rstrip(":")
    rest = [segment.replace("_", "-") for segment in path.parts[1:]]
    return (drive + "--" + "-".join(rest)).lower()


def is_sidechain_path(path: Path) -> bool:
    """Whether this transcript belongs to a subagent or workflow, not a session.

    Judged on the path alone so it can be applied during discovery, before any
    file is opened.
    """
    parts = path.parts
    if any(part in _SIDECHAIN_PARTS for part in parts):
        return True
    if any(part.startswith("workflows") for part in parts):
        return True
    return path.stem.startswith(_SIDECHAIN_PREFIXES) or any(
        part.startswith(_SIDECHAIN_PREFIXES) for part in parts
    )


def discover_transcripts(root: Path | None = None) -> list[Path]:
    """Return real session transcripts under ``root``, newest first.

    Excludes subagent and workflow sidechains. The previous implementation used
    a bare ``rglob`` and treated every nested ``.jsonl`` as a session.
    """
    if root is None:
        from claude_brain.config import claude_projects_dir

        root = claude_projects_dir()
    if not root.exists():
        return []

    found = [p for p in root.rglob("*.jsonl") if not is_sidechain_path(p)]

    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:  # pragma: no cover
            return 0.0

    return sorted(found, key=_mtime, reverse=True)


def _iter_records(path: Path) -> Iterator[dict | None]:
    """Yield parsed records; ``None`` marks a line that failed to parse.

    Streams line by line: transcripts reach tens of megabytes, and reading one
    whole file into memory to find a handful of fields is wasteful.
    """
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("cannot open transcript %s: %s", path, exc)
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                yield None
                continue
            yield record if isinstance(record, dict) else None


def _first(record: dict, *keys: str) -> str | None:
    """First present, non-empty string value among ``keys``."""
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _content_blocks(record: dict) -> list:
    message = record.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, list):
        return content
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


def is_noise(text: str) -> bool:
    """True when the text is harness-generated, not something the user typed.

    Public because the digest parser needs the same filter: the ECC ``Tasks``
    lists are built from raw user records and carry the same injected noise.
    """
    lowered = text.lstrip().lower()
    return any(lowered.startswith(prefix) for prefix in _NOISE_PREFIXES)


def _extract_user_text(record: dict) -> str | None:
    """Readable text of a genuine user turn, or ``None`` if it is harness noise."""
    if record.get("isCompactSummary") or record.get("isMeta"):
        return None

    parts = [
        block.get("text", "")
        for block in _content_blocks(record)
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    text = " ".join(part.strip() for part in parts if part).strip()
    if not text or is_noise(text):
        return None
    return " ".join(text.split())[:_MAX_MESSAGE_CHARS]


def _extract_tool_uses(record: dict) -> Iterator[tuple[str, dict]]:
    for block in _content_blocks(record):
        if isinstance(block, dict) and block.get("type") == "tool_use":
            name = block.get("name")
            payload = block.get("input")
            yield (
                name if isinstance(name, str) else "?",
                payload if isinstance(payload, dict) else {},
            )


def read_transcript(path: Path) -> TranscriptSummary:
    """Summarize one transcript from its own records.

    Never raises for malformed content: a damaged line is counted and skipped,
    because an unreadable transcript must not be able to break ingest.
    """
    session_id: str | None = None
    cwd: str | None = None
    branch: str | None = None
    timestamps: list[str] = []
    messages: list[str] = []
    files: list[str] = []
    tool_names: list[str] = []
    counts: Counter[str] = Counter()
    ai_title: str | None = None
    agent_name: str | None = None
    last_prompt: str | None = None
    malformed = sidechains = compactions = injected = 0

    for record in _iter_records(path):
        if record is None:
            malformed += 1
            continue

        rtype = record.get("type") or "<none>"
        counts[str(rtype)] += 1

        if record.get("isSidechain") is True:
            sidechains += 1
            continue

        session_id = session_id or _first(record, "sessionId", "session_id")
        cwd = cwd or _first(record, "cwd")
        branch = branch or _first(record, "gitBranch", "git_branch")

        stamp = _first(record, "timestamp")
        if stamp:
            timestamps.append(stamp)

        if rtype == "user":
            text = _extract_user_text(record)
            if text and len(messages) < _MAX_USER_MESSAGES:
                messages.append(text)

        elif rtype == "assistant":
            for name, payload in _extract_tool_uses(record):
                tool_names.append(name)
                if name in _EDIT_TOOLS and len(files) < _MAX_EDITED_FILES:
                    target = _first(payload, *_PATH_KEYS)
                    if target and target not in files:
                        files.append(target)

        elif rtype == "ai-title":
            ai_title = ai_title or _first(record, "aiTitle", "ai_title")

        elif rtype == "agent-name":
            agent_name = agent_name or _first(record, "agentName", "agent_name")

        elif rtype == "last-prompt":
            last_prompt = last_prompt or _first(record, "lastPrompt", "last_prompt")

        elif rtype == "system" and record.get("compactMetadata"):
            compactions += 1

        elif rtype == "attachment":
            attachment = record.get("attachment")
            if isinstance(attachment, dict):
                content = attachment.get("content")
                if isinstance(content, str) and "## Session Summary" in content:
                    # Counted, never trusted: this is another session's digest.
                    injected += 1

    return TranscriptSummary(
        path=path,
        session_id=session_id or (path.stem or None),
        cwd=cwd,
        git_branch=branch,
        started_at=min(timestamps) if timestamps else None,
        ended_at=max(timestamps) if timestamps else None,
        user_messages=tuple(messages),
        edited_files=tuple(files),
        ai_title=ai_title,
        agent_name=agent_name,
        last_prompt=(" ".join(last_prompt.split())[:_MAX_MESSAGE_CHARS] if last_prompt else None),
        tool_count=len(tool_names),
        tool_names=tuple(sorted(set(tool_names))),
        record_counts=dict(counts),
        malformed_lines=malformed,
        sidechain_records=sidechains,
        compactions=compactions,
        injected_summaries=injected,
    )
