"""Immutable domain models."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    path: str
    created_at: str
    last_scanned: str | None = None


@dataclass(frozen=True)
class Vault:
    vault_id: str
    project_id: str
    vault_path: str
    status: str  # 'none' | 'exists'
    created_at: str


@dataclass(frozen=True)
class Session:
    session_id: str
    project_id: str | None
    source_dir: str | None
    date: str
    summary: str
    file_count: int
    tool_count: int
    notes: str | None
    raw_path: str | None
    is_master: bool = False


@dataclass(frozen=True)
class SessionFile:
    file_id: str
    session_id: str
    file_path: str


@dataclass(frozen=True)
class Consolidation:
    consolidation_id: str
    project_id: str
    session_ids: tuple
    markdown_path: str
    created_at: str


@dataclass(frozen=True)
class DayDigest:
    """A per-project-per-day digest parsed from ``~/.claude/sessions/*.tmp``.

    Deliberately not a :class:`Session`: a digest merges every session a project
    had that day, one long session spans several digests, and the file carries
    no session id at all.
    """

    digest_id: str
    project_id: str | None
    date: str
    project_name: str | None
    worktree: str | None
    topic: str | None
    tasks: str | None
    files: str | None
    tools: str | None
    notes: str | None
    message_count: int
    source_path: str
    source_mtime: float
    is_curated: bool = False
