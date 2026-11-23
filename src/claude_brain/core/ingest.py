"""The single session-ingest implementation.

Previously there were four: one in ``app.py`` that ran only when the tkinter app
launched, and three inside UI widgets, each with a weaker project matcher and a
bare ``except: pass``. The launch-time copy marked every transcript imported but
wrote no vault artifacts, while the copies that *did* write notes only ever saw
transcripts that appeared while the window was open. The result was 865 of 882
rows unlinked and most projects with no notes at all.

Three rules this module enforces:

1. **Projects are matched on ``cwd``**, the absolute path recorded in the
   transcript. Slug matching could not distinguish ``Translator_OSM`` from
   ``Translator_OSM_Offline`` and failed outright for anything outside the
   configured projects root.
2. **Everything is scrubbed before it is stored.** Transcripts contain pasted
   credentials; see :mod:`claude_brain.core.scrub`.
3. **Ingest is idempotent and re-runnable**, because hooks are unreliable
   triggers. Re-running must be cheap and must not duplicate rows.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from claude_brain.core.models import Project, Session, SessionFile
from claude_brain.core.scrub import scrub
from claude_brain.core.transcript import (
    TranscriptSummary,
    discover_transcripts,
    read_transcript,
)

log = logging.getLogger(__name__)

__all__ = [
    "DENYLIST_SETTING",
    "IngestResult",
    "build_session",
    "default_denylist",
    "ingest_all",
    "ingest_transcript",
    "is_denied",
    "is_registerable",
    "load_denylist",
    "resolve_project",
]

#: ``app_settings`` key holding newline-separated path prefixes to never capture.
DENYLIST_SETTING = "capture_denylist"

_SUMMARY_MAX_LINES = 10


def default_denylist() -> tuple[str, ...]:
    """Paths never captured when the user has set no denylist of their own.

    Deliberately **empty**: capture is opt-out, so anything here is an opt-out
    this tool invented rather than one the user chose.

    ``%TEMP%`` was tried as a default and removed. The case for it is real - a
    throwaway session in a temp directory registers a directory that will not
    exist tomorrow, and later sweeps keep re-registering it from the transcript
    that outlives it. But the rule is too broad to be a default: it also
    excludes every legitimate scratch workspace, and it silently excluded this
    project's own test suite, whose ``tmp_path`` lives under ``%TEMP%``. A rule
    that surprising belongs in a user's setting, not in the defaults.
    """
    return ()


@dataclass(frozen=True)
class IngestResult:
    """Outcome of a sweep. Immutable; counts never overlap."""

    scanned: int = 0
    ingested: int = 0
    skipped: int = 0
    failed: int = 0
    projects_registered: int = 0
    notes_written: int = 0
    messages: tuple[str, ...] = ()

    def __str__(self) -> str:
        return (
            f"scanned={self.scanned} ingested={self.ingested} "
            f"skipped={self.skipped} failed={self.failed} "
            f"projects+={self.projects_registered} notes={self.notes_written}"
        )


def load_denylist(repos) -> tuple[str, ...]:
    """Path prefixes excluded from capture, lower-cased for comparison."""
    try:
        raw = repos.settings.get(DENYLIST_SETTING, "")
    except Exception:  # pragma: no cover - settings table missing
        return default_denylist()
    entries = [line.strip() for line in str(raw).splitlines() if line.strip()]
    return tuple(e.lower() for e in entries) or default_denylist()


def is_denied(path: str | None, denylist: Sequence[str]) -> bool:
    if not path:
        return False
    lowered = path.replace("/", "\\").lower()
    return any(lowered.startswith(entry.replace("/", "\\")) for entry in denylist)


def is_registerable(path: str | None) -> bool:
    r"""Whether a path is plausible as a project root.

    A filesystem root is not a project. One real digest records a session
    started at ``C:\``, and registering that produced a project whose
    directory *contains every other project* - which then matched every
    enclosing-project lookup on the drive. That is precisely the
    cross-project contamination this rewrite exists to remove, so it is
    refused at the source rather than worked around downstream.
    """
    if not path:
        return False
    try:
        parts = Path(path).parts
    except (OSError, ValueError):
        return False
    return len(parts) > 1


def resolve_project(
    repos,
    summary: TranscriptSummary,
    *,
    denylist: Sequence[str] = (),
    auto_register: bool = True,
) -> tuple[Project | None, bool]:
    """Find — or register — the project a transcript belongs to.

    Returns ``(project, newly_registered)``. ``cwd`` is authoritative; the slug
    fallback exists only for records that genuinely lack it.
    """
    cwd = summary.cwd
    if is_denied(cwd, denylist):
        return None, False

    if cwd:
        existing = repos.projects.find_by_path(cwd)
        if existing:
            return existing, False
        if auto_register and is_registerable(cwd) and Path(cwd).is_dir():
            name = Path(cwd).name or cwd
            created = repos.projects.upsert(name, cwd)
            log.info("registered project %s -> %s", name, cwd)
            return created, True
        # cwd recorded but the directory is gone: fall through to the slug.

    return _resolve_by_slug(repos, summary), False


def _resolve_by_slug(repos, summary: TranscriptSummary) -> Project | None:
    """Last-resort match on the enclosing directory name.

    Lossy — Claude Code replaces every non-alphanumeric character, so a sibling
    and a subdirectory are indistinguishable. Only used when ``cwd`` is absent,
    and it requires an exact slug match rather than the old prefix test that
    collapsed ``Translator_OSM_Offline`` into ``Translator``.
    """
    from claude_brain.core.transcript import path_to_claude_slug

    source_dir = summary.path.parent.name.lower()
    if not source_dir:
        return None
    for project in repos.projects.find_all():
        if project.path and path_to_claude_slug(project.path) == source_dir:
            return project
    return None


def _build_summary_text(summary: TranscriptSummary) -> str:
    """The session's own words, scrubbed — one line per real user prompt.

    Sessions launched entirely by a slash command have no user prose at all, so
    rather than storing a useless placeholder we fall back to the metadata
    Claude Code already records: its generated ``ai-title`` first (often a
    better one-line description than the raw prompts), then the session's
    agent name, then its last prompt.
    """
    lines = [scrub(message).text for message in summary.user_messages[:_SUMMARY_MAX_LINES]]
    lines = [line for line in lines if line]
    if lines:
        return "\n".join(lines)

    for fallback in (summary.ai_title, summary.agent_name, summary.last_prompt):
        if fallback:
            return scrub(fallback).text

    return "(no user prompts recorded)"


def build_session(
    summary: TranscriptSummary,
    project_id: str | None,
) -> tuple[Session, list[SessionFile]]:
    """Map a transcript summary onto the storage models.

    Every text field passes through the scrubber here, so no caller can forget.
    """
    files = [scrub(path).text for path in summary.edited_files]
    files = [f for f in files if f]

    notes_source = (
        f"span {summary.started_at} -> {summary.ended_at}"
        if summary.started_at and summary.ended_at
        else None
    )

    session = Session(
        session_id=summary.session_id or summary.path.stem,
        project_id=project_id,
        source_dir=summary.path.parent.name,
        date=summary.date,
        summary=_build_summary_text(summary),
        file_count=len(files),
        tool_count=summary.tool_count,
        notes=notes_source,
        raw_path=str(summary.path),
    )
    session_files = [
        SessionFile(file_id=str(uuid.uuid4()), session_id=session.session_id, file_path=path)
        for path in files
    ]
    return session, session_files


def ingest_transcript(
    repos,
    path: Path,
    *,
    force: bool = False,
    write_vault: bool = True,
    denylist: Sequence[str] = (),
) -> tuple[str, bool, bool]:
    """Ingest one transcript.

    Returns ``(status, project_newly_registered, vault_note_written)``.
    Status is one of ``ingested``, ``skipped``, ``denied``, ``empty``, ``failed``.

    The third value is reported separately from the status because a note
    whose managed region was hand-edited is deliberately **not** written,
    even though the session itself was ingested.
    """
    raw_path = str(path)
    try:
        stat = path.stat()
        mtime, size = stat.st_mtime, stat.st_size
    except OSError:
        return "failed", False, False

    # Dedupe on (path, mtime, size) rather than "seen before". A transcript that
    # has grown since it was last read must be re-ingested, which matters for a
    # session still in progress; the old flag froze it at the first snapshot.
    if not force and repos.cached_files.is_unchanged(raw_path, mtime, size):
        return "skipped", False, False

    summary = read_transcript(path)
    if summary.is_empty:
        repos.cached_files.remember(raw_path, mtime, size, None)
        return "empty", False, False

    project, registered = resolve_project(repos, summary, denylist=denylist)
    if project is None and is_denied(summary.cwd, denylist):
        return "denied", False, False

    session, files = build_session(summary, project.project_id if project else None)

    # One transaction: committing the session without its file rows would
    # permanently lose the file list, since the path is then marked ingested.
    with repos.transaction():
        repos.sessions.create(session, commit=False)
        if files:
            repos.session_files.create_many(files, commit=False)
    repos.cached_files.remember(raw_path, mtime, size, session.session_id)

    wrote_note = False
    if project is not None:
        repos.projects.touch(project.project_id)
        if write_vault:
            wrote_note = _write_vault_artifacts(repos, project, session, files)

    return "ingested", registered, wrote_note


def _write_vault_artifacts(repos, project: Project, session: Session, files: list) -> bool:
    """Write the session note and refresh the CLAUDE.md activity block.

    The old launch-time ingest skipped this entirely, which is why 11 projects
    had no ``CLAUDE.md`` and only 5 auto-notes existed. Failures are logged, not
    raised: a vault problem must not lose the database row.

    A note whose managed region was hand-edited is reported as not written,
    because it was not - the sweep leaves the person's prose alone.
    """
    from claude_brain.core import vault_writer

    try:
        note = vault_writer.write_session_note(project.path, project.name, session, files)
        recent = repos.sessions.find_by_project(project.project_id)[:5]
        vault_writer.refresh_claude_md(project.path, project.name, recent, [])
        vault_writer.write_sessions_base(project.path)
    except (OSError, ValueError, KeyError) as exc:
        log.warning("vault write failed for %s: %s", project.name, exc)
        return False
    return note.changed


def ingest_all(
    repos,
    *,
    root: Path | None = None,
    paths: Iterable[Path] | None = None,
    force: bool = False,
    write_vault: bool = True,
    update_search: bool = True,
) -> IngestResult:
    """Sweep every real session transcript. Safe to re-run at any time.

    This is what the hooks call. It is deliberately tolerant: one bad transcript
    is counted and skipped rather than aborting the sweep.
    """
    denylist = load_denylist(repos)
    targets = list(paths) if paths is not None else discover_transcripts(root)

    counts = {"ingested": 0, "skipped": 0, "denied": 0, "empty": 0, "failed": 0}
    registered = 0
    notes = 0
    messages: list[str] = []

    for path in targets:
        try:
            status, new_project, wrote_note = ingest_transcript(
                repos, path, force=force, write_vault=write_vault, denylist=denylist
            )
        except Exception as exc:
            log.warning("ingest failed for %s: %s", path, exc)
            counts["failed"] += 1
            messages.append(f"failed: {path.name}: {exc}")
            continue

        counts[status] = counts.get(status, 0) + 1
        registered += int(new_project)
        notes += int(wrote_note)

    # Only when a row actually changed: a sweep that finds nothing new should
    # stay cheap, but a stale index is the failure mode that matters here - a
    # search that silently misses the session saved a minute ago.
    if update_search and (counts["ingested"] or registered):
        from claude_brain.core.search import reindex

        try:
            reindex(repos.conn)
        except Exception as exc:
            log.warning("search reindex failed: %s", exc)
            messages.append(f"reindex failed: {exc}")

    return IngestResult(
        scanned=len(targets),
        ingested=counts["ingested"],
        skipped=counts["skipped"] + counts["denied"] + counts["empty"],
        failed=counts["failed"],
        projects_registered=registered,
        notes_written=notes if write_vault else 0,
        messages=tuple(messages),
    )


def relink_unlinked(repos) -> int:
    """Re-attempt project resolution for sessions with no project.

    Where the transcript still exists, it is re-read so the authoritative
    ``cwd`` is used rather than the slug heuristic that produced the orphans.

    Where it does not — which is the common case, since Claude Code deletes
    transcripts and the store is the only surviving copy — fall back to an
    **exact** match on the stored ``source_dir`` slug. That is still lossy
    (Claude Code replaces every non-alphanumeric character, so a sibling and a
    subdirectory are indistinguishable) but an exact comparison cannot produce
    the sibling collisions the old prefix test did.
    """
    from claude_brain.core.transcript import path_to_claude_slug

    denylist = load_denylist(repos)
    slug_index = {
        path_to_claude_slug(project.path): project
        for project in repos.projects.find_all()
        if project.path
    }

    linked = 0
    for session in repos.sessions.find_unlinked():
        project = None

        if session.raw_path and Path(session.raw_path).exists():
            summary = read_transcript(Path(session.raw_path))
            project, _ = resolve_project(repos, summary, denylist=denylist)
        elif session.source_dir:
            project = slug_index.get(session.source_dir.lower())
            if project is not None and is_denied(project.path, denylist):
                project = None

        if project is not None:
            repos.sessions.link_project(session.session_id, project.project_id)
            linked += 1
    return linked
