"""Parse ``~/.claude/sessions/*.tmp`` day digests into ``day_digests``.

These files are the only record left of six months of work: of the 872
transcripts the store had rows for, exactly **one** still existed on disk, while
all 164 digests are intact. They are written by the ECC plugin, one per project
per day, and they are *not* sessions — a long session spans several of them, a
day's several sessions merge into one, and no file carries a session id. They
are modelled separately for exactly that reason.

Two shapes occur in the real corpus:

* **Generated** (162 files) — the ``ECC:SUMMARY`` template: a header block of
  ``**Key:** value`` lines followed by ``### Tasks`` / ``### Files Modified`` /
  ``### Tools Used`` / ``### Stats``.
* **Curated** (2 files) — hand-written prose with a ``**Topic:**`` line and no
  ECC marker at all. They are the densest, most useful notes in the directory,
  so they are parsed on a fallback path rather than skipped, and flagged
  ``is_curated`` so recall can rank them first.

Every captured field passes through :mod:`claude_brain.core.scrub` before it
reaches SQLite. This is not optional: one digest in this corpus contains a
plaintext server root password.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from claude_brain.core.models import DayDigest
from claude_brain.core.scrub import load_deny_literals, scrub
from claude_brain.core.transcript import is_noise

log = logging.getLogger(__name__)

__all__ = [
    "BackfillResult",
    "DigestFile",
    "backfill_digests",
    "discover_digests",
    "parse_digest",
]


_HEADER_RE = re.compile(r"^\*\*([^*:]+):\*\*\s*(.*?)\s*$", re.M)
_SECTION_RE = re.compile(r"^###\s+(.+?)\s*$", re.M)
_BULLET_RE = re.compile(r"^[-*]\s+(.*\S)\s*$", re.M)
_HEADING_DATE_RE = re.compile(r"^#\s*Session:\s*(\d{4}-\d{2}-\d{2})", re.M)
_FILENAME_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_MESSAGE_COUNT_RE = re.compile(r"Total user messages:\s*(\d+)")
#: A slash-command invocation. The surrounding tags are harness noise, but the
#: arguments are what the user actually asked for, so they are recovered rather
#: than discarded with the rest of the line. The closing ``</command-args>`` is
#: optional because the plugin truncates every task line at ~200 characters, so
#: a long argument loses its closing tag.
_COMMAND_RE = re.compile(
    r"<command-name>\s*/?([^<>\s]+)\s*</command-name>"
    r"|<command-args>(.*?)(?:</command-args>|$)"
)
#: A path inside parentheses or backticks on the **Project:** line of a curated
#: note, e.g. ``**Project:** Claude Brain (`C:\\...\\Claude_Brain`)``.
_INLINE_PATH_RE = re.compile(r"[(`]\s*([A-Za-z]:[\\/][^)`\n]+?)\s*[)`]")
_ECC_MARKER = "ECC:SUMMARY"

#: Placeholders the template leaves behind when a section was never filled in.
_PLACEHOLDERS = frozenset({"", "-", "[relevant files]", "none", "n/a"})

_MAX_ITEMS = 60
_MAX_FIELD_CHARS = 8000


@dataclass(frozen=True)
class DigestFile:
    """One parsed digest. Plain data — no database identity yet."""

    path: Path
    date: str
    project_name: str | None = None
    worktree: str | None = None
    topic: str | None = None
    tasks: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    notes: str | None = None
    message_count: int = 0
    is_curated: bool = False
    redactions: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Nothing worth a row.

        ``tools`` and ``message_count`` count: two real digests consist entirely
        of harness noise in their task list, but still record that N messages of
        work happened in this project on this date using these tools. That is a
        thin row, not an empty one, and these files are irreplaceable.
        """
        return not (
            self.tasks or self.files or self.notes or self.topic or self.tools or self.message_count
        )


@dataclass(frozen=True)
class BackfillResult:
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    linked: int = 0
    unlinked: int = 0
    projects_registered: int = 0
    redactions: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)

    def __str__(self) -> str:
        return (
            f"imported={self.imported} updated={self.updated} skipped={self.skipped} "
            f"failed={self.failed} linked={self.linked} unlinked={self.unlinked} "
            f"projects_registered={self.projects_registered} redactions={self.redactions}"
        )


def discover_digests(root: Path | None = None) -> list[Path]:
    """Digest files, oldest first. Flat glob: the directory has no subdirectories."""
    from claude_brain.config import claude_sessions_dir

    directory = root or claude_sessions_dir()
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.tmp"), key=lambda p: p.name)


def _headers(text: str) -> dict[str, str]:
    """``**Key:** value`` lines, lower-cased keys.

    First occurrence wins, so a ``**Date:**`` inside quoted body text cannot
    override the real one in the header block.
    """
    found: dict[str, str] = {}
    for match in _HEADER_RE.finditer(text):
        key = match.group(1).strip().lower()
        if key not in found:
            found[key] = match.group(2).strip()
    return found


def _sections(text: str) -> dict[str, str]:
    """``### Heading`` -> body text, lower-cased headings."""
    matches = list(_SECTION_RE.finditer(text))
    out: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        out[match.group(1).strip().lower()] = text[match.end() : end].strip()
    return out


def _recover_command(item: str) -> str | None:
    """Turn a slash-command task line into what the user actually asked.

    ``<command-name>/plan</command-name> <command-args>rewrite the ingest</…>``
    is noise by prefix, but the arguments are the request itself. Rendered as
    ``/plan rewrite the ingest`` so it reads like the invocation.
    """
    name, args = None, None
    for match in _COMMAND_RE.finditer(item):
        name = name or (match.group(1) or "").strip() or None
        args = args or (match.group(2) or "").strip() or None
    if not args:
        return None
    return " ".join(f"/{name} {args}".split()) if name else " ".join(args.split())


def _bullets(body: str | None, *, drop_noise: bool = False) -> tuple[str, ...]:
    if not body:
        return ()
    items: list[str] = []
    for match in _BULLET_RE.finditer(body):
        item = " ".join(match.group(1).split())
        if item.lower() in _PLACEHOLDERS:
            continue
        if drop_noise and is_noise(item):
            recovered = _recover_command(item)
            if not recovered:
                continue
            item = recovered
        if item not in items:
            items.append(item)
        if len(items) >= _MAX_ITEMS:
            break
    return tuple(items)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip().strip("`").strip()
    return None if stripped.lower() in _PLACEHOLDERS else stripped


def _resolve_date(text: str, path: Path) -> str | None:
    """Prefer the explicit header, then the heading, then the filename.

    Measured across all 164 real files: the header and the filename never
    disagree, so the filename is a safe fallback for the two that lack a header.
    """
    heading = _HEADING_DATE_RE.search(text)
    filename = _FILENAME_DATE_RE.match(path.name)
    candidates = (
        _headers(text).get("date"),
        heading.group(1) if heading else None,
        filename.group(1) if filename else None,
    )
    for candidate in candidates:
        if candidate and re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate.strip()):
            return candidate.strip()
    return None


def _curated_body(text: str) -> str | None:
    """Everything after the header block of a hand-written note.

    The prose *is* the value of these files, so it is kept whole rather than
    reduced to bullets. The ``---`` rule that closes the header block is the
    split point; without one, fall back to the first ``##`` heading.
    """
    marker = text.find("\n---")
    if marker == -1:
        heading = re.search(r"^##\s", text, re.M)
        marker = heading.start() if heading else -1
    if marker == -1:
        return None
    body = text[marker:].lstrip("-\n \t")
    return body.strip() or None


def parse_digest(path: Path, *, literals: Iterable[str] | None = None) -> DigestFile | None:
    """Parse one digest file. Returns ``None`` only if it is unusable.

    Tolerant by design: a file missing its header block still yields a row if a
    date can be recovered, because these files are irreplaceable.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("cannot read digest %s: %s", path.name, exc)
        return None

    date = _resolve_date(text, path)
    if not date:
        log.warning("digest %s has no recoverable date", path.name)
        return None

    deny = tuple(literals) if literals is not None else load_deny_literals()
    redactions: list[str] = []

    def protect(value: str | None) -> str | None:
        if value is None:
            return None
        result = scrub(value, literals=deny)
        redactions.extend(result.redactions)
        return result.text or None

    headers = _headers(text)
    sections = _sections(text)
    is_curated = _ECC_MARKER not in text

    project_raw = headers.get("project")
    worktree = _clean(headers.get("worktree"))
    project_name = _clean(project_raw)

    if project_raw and not worktree:
        # Curated notes write the path inline: `Name (C:\path\to\project)`.
        # The match can begin at the inner backtick of ``Name (`C:\...`)``, so
        # strip the opening bracket the name would otherwise keep.
        inline = _INLINE_PATH_RE.search(project_raw)
        if inline:
            worktree = inline.group(1).strip()
            name = project_raw[: inline.start()].strip().rstrip("([{`").strip()
            project_name = _clean(name) or project_name

    tasks = _bullets(sections.get("tasks"), drop_noise=True)
    files = _bullets(sections.get("files modified"))
    tools = tuple(
        part.strip()
        for part in (sections.get("tools used") or "").replace("\n", ",").split(",")
        if part.strip() and part.strip().lower() not in _PLACEHOLDERS
    )

    notes_body = sections.get("notes for next session")
    notes = _curated_body(text) if is_curated else _clean(notes_body)

    count_match = _MESSAGE_COUNT_RE.search(sections.get("stats", "") or text)

    return DigestFile(
        path=path,
        date=date,
        project_name=protect(project_name),
        worktree=worktree,  # a filesystem path; scrubbing it would break matching
        topic=protect(_clean(headers.get("topic"))),
        tasks=tuple(protect(t) or "" for t in tasks),
        files=files,
        tools=tools[:_MAX_ITEMS],
        notes=(protect(notes) or None) if notes else None,
        message_count=int(count_match.group(1)) if count_match else 0,
        is_curated=is_curated,
        redactions=tuple(redactions),
    )


def _join(items: Sequence[str]) -> str | None:
    text = "\n".join(item for item in items if item)
    return text[:_MAX_FIELD_CHARS] or None


def _resolve_digest_project(
    repos, digest: DigestFile, *, auto_register: bool
) -> tuple[str | None, bool]:
    """Link the digest to a project by worktree path.

    The worktree is an absolute path recorded by the plugin, so it is the same
    reliable key ``cwd`` is for transcripts — and unlike the digest's
    ``**Project:**`` name, it cannot collide between sibling directories.
    """
    from claude_brain.core.ingest import is_denied, is_registerable, load_denylist

    worktree = digest.worktree
    if not worktree:
        return None, False
    if is_denied(worktree, load_denylist(repos)):
        return None, False

    existing = repos.projects.find_by_path(worktree)
    if existing:
        return existing.project_id, False
    if auto_register and is_registerable(worktree) and Path(worktree).is_dir():
        name = Path(worktree).name or (digest.project_name or worktree)
        created = repos.projects.upsert(name, worktree)
        log.info("registered project %s -> %s", name, worktree)
        return created.project_id, True
    return None, False


def backfill_digests(
    repos,
    *,
    root: Path | None = None,
    force: bool = False,
    auto_register: bool = True,
) -> BackfillResult:
    """Import every digest file. Idempotent: unchanged files are skipped.

    Safe to re-run; a digest whose file has been rewritten since last import is
    updated in place rather than duplicated.
    """
    paths = discover_digests(root)
    if not paths:
        return BackfillResult()

    literals = load_deny_literals()
    imported = updated = skipped = failed = linked = registered = redactions = 0
    errors: list[str] = []

    for path in paths:
        try:
            stat = path.stat()
            source_path = str(path)

            if not force and repos.digests.is_unchanged(source_path, stat.st_mtime):
                skipped += 1
                continue

            parsed = parse_digest(path, literals=literals)
            if parsed is None or parsed.is_empty:
                skipped += 1
                continue

            project_id, newly = _resolve_digest_project(repos, parsed, auto_register=auto_register)
            registered += int(newly)
            linked += int(bool(project_id))
            redactions += len(parsed.redactions)

            existing = repos.digests.find_by_source(source_path)
            repos.digests.upsert(
                DayDigest(
                    digest_id=existing.digest_id if existing else str(uuid.uuid4()),
                    project_id=project_id,
                    date=parsed.date,
                    project_name=parsed.project_name,
                    worktree=parsed.worktree,
                    topic=parsed.topic,
                    tasks=_join(parsed.tasks),
                    files=_join(parsed.files),
                    tools=_join(parsed.tools),
                    notes=parsed.notes,
                    message_count=parsed.message_count,
                    source_path=source_path,
                    source_mtime=stat.st_mtime,
                    is_curated=parsed.is_curated,
                )
            )
            if existing:
                updated += 1
            else:
                imported += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{path.name}: {exc}")
            log.warning("digest backfill failed for %s: %s", path.name, exc)

    return BackfillResult(
        imported=imported,
        updated=updated,
        skipped=skipped,
        failed=failed,
        linked=linked,
        unlinked=repos.digests.count_unlinked(),
        projects_registered=registered,
        redactions=redactions,
        errors=tuple(errors[:20]),
    )
