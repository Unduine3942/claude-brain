"""Talking to the Obsidian application: its registry, launching it, and
reading patterns out of a project's session history.

**Writing vault files belongs to** :mod:`claude_brain.core.vault_writer`. The
split matters: everything here touches the user's Obsidian installation or
reads, while the writer owns the rules that keep a generated write from losing
hand-written text.
"""

import contextlib
import json
import os
import secrets
import subprocess
import time
import urllib.parse
from pathlib import Path

_OBSIDIAN_REGISTRY = Path(os.environ.get("APPDATA", "~")) / "obsidian" / "obsidian.json"


def vault_path_for(project_path: str) -> Path:
    """The ``.obsidian`` directory that marks a project as a vault.

    Note what this is *not*: the vault path stored in the database, which is the
    project root. Confusing the two put ``…\\.obsidian`` into two ``vaults``
    rows, and a repair action now normalises them.
    """
    return Path(project_path) / ".obsidian"


def vault_exists(project_path: str) -> bool:
    return vault_path_for(project_path).exists()


def register_vault(project_path: str, project_name: str) -> str:
    """Add the project to Obsidian's own vault registry. Returns the vault id.

    Idempotent by path, so re-registering an already-known vault returns its
    existing id rather than creating a duplicate entry.
    """
    registry_path = _OBSIDIAN_REGISTRY
    registry_path.parent.mkdir(parents=True, exist_ok=True)

    registry: dict = {}
    if registry_path.exists():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            registry = json.loads(registry_path.read_text(encoding="utf-8"))

    vaults = registry.get("vaults", {})
    absolute = str(Path(project_path).resolve())
    for vault_id, data in vaults.items():
        if data.get("path") == absolute:
            return vault_id

    vault_id = secrets.token_hex(8)
    vaults[vault_id] = {"path": absolute, "ts": int(time.time() * 1000), "open": True}
    registry["vaults"] = vaults
    registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    return vault_id


def open_in_obsidian(project_path: str, project_name: str = "") -> bool:
    """Register vault and open it in Obsidian. Returns True if launched."""
    name = project_name or Path(project_path).name
    register_vault(project_path, name)
    uri = f"obsidian://open?vault={urllib.parse.quote(name, safe='')}"
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "", uri],
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0,
        )
        return True
    except Exception:
        return False


def open_in_explorer(project_path: str) -> None:
    """Fallback: open vault folder in Windows Explorer."""
    with contextlib.suppress(Exception):
        subprocess.Popen(
            ["explorer", str(Path(project_path))],
            creationflags=subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0,
        )


_ACTIVITY_START = "<!-- CB:ACTIVITY:START -->"
_ACTIVITY_END = "<!-- CB:ACTIVITY:END -->"


def scan_patterns(sessions: list) -> dict:
    """Return a dict of pattern insights across a list of sessions."""
    import re
    from collections import Counter

    keyword_counter: Counter = Counter()
    _stop = {
        "the",
        "a",
        "an",
        "to",
        "of",
        "in",
        "and",
        "for",
        "with",
        "is",
        "are",
        "was",
        "on",
        "at",
        "by",
        "from",
        "it",
        "this",
        "that",
        "be",
        "as",
        "or",
        # Session boilerplate
        "session",
        "summary",
        "conversation",
        "context",
        "continued",
        "previous",
        "below",
        "being",
        "were",
        "have",
        "has",
        "had",
        "been",
        "will",
        "would",
        "could",
        "should",
        "which",
        "when",
        "where",
        "what",
        "how",
        "also",
        "then",
        "into",
        "not",
        "but",
        "used",
        "using",
        "new",
        "added",
        "updated",
        "created",
        "user",
        "file",
        "files",
        "code",
        "task",
        "tasks",
        "note",
        "notes",
        "current",
        "work",
        "working",
        "make",
        "made",
        "tool",
        "tools",
        "based",
        "after",
        "before",
    }

    for s in sessions:
        if s.summary:
            words = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]{3,}\b", s.summary.lower())
            keyword_counter.update(w for w in words if w not in _stop)

    # hot_files requires session_files data, passed in as flat list on session objects
    # We use session.file_count as a proxy; callers can pre-aggregate file paths
    recurring_keywords = [kw for kw, count in keyword_counter.most_common(15) if count >= 2]

    return {
        "recurring_keywords": recurring_keywords,
        "session_count": len(sessions),
        "date_range": (
            min((s.date or "" for s in sessions), default=""),
            max((s.date or "" for s in sessions), default=""),
        ),
    }
