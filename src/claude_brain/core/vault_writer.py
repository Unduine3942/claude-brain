"""Writing Obsidian vault artifacts without destroying what the user wrote.

The vault is the half of this system a person actually reads and edits, so the
governing rule is that a generated write must never be able to lose hand-written
text. Three mechanisms enforce that:

**Managed regions.** Generated prose lives between ``%% brain:begin %%`` and
``%% brain:end %%``. The begin marker records a hash of what was generated, so a
later write can tell whether the region still holds its own output. If it does
not, the note was hand-edited and is **skipped**, not overwritten. Anything
outside the markers is never touched. ``%% %%`` is Obsidian's comment syntax, so
the markers are invisible in reading view.

**Line-level frontmatter merge.** Our keys are replaced line by line and every
other line is kept verbatim. Nothing is parsed and re-serialised, because that
is how a round trip through an incomplete YAML implementation silently rewrites
nested maps, block scalars and quoting. The package is stdlib-only and a partial
YAML parser is worse than none.

**Create-only writes for configuration.** ``.obsidian/app.json`` and
``appearance.json`` are the user's editor settings. The previous implementation
rewrote both on every vault refresh, discarding whatever they had changed.

Indexing is an Obsidian **Base** rather than a generated markdown table.
``update_sessions_index`` built a table whose summary column was each note's
first non-``#`` line - which, for any note with frontmatter, is the ``---``
delimiter, so every row read ``---``. It also never escaped ``|``, so one
summary containing a pipe broke the whole table. A Base is a core feature
reading the frontmatter directly, and cannot rot that way.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = [
    "MANAGED_BEGIN_RE",
    "ManagedRegion",
    "WriteResult",
    "ensure_vault",
    "merge_frontmatter",
    "refresh_claude_md",
    "render_managed",
    "split_managed",
    "write_session_note",
    "write_sessions_base",
]

#: ``[ \t]`` throughout, never ``\s``: ``\s`` includes newlines, so a greedy
#: ``\s*$`` swallows the line break after the marker. The text after the region
#: then comes back one newline short, and rewriting an unchanged note reports a
#: change on every single pass.
MANAGED_BEGIN_RE = re.compile(
    r"^%%[ \t]*brain:begin(?:[ \t]+hash=([0-9a-f]{12}))?[ \t]*%%[ \t]*$", re.M
)
MANAGED_END_RE = re.compile(r"^%%[ \t]*brain:end[ \t]*%%[ \t]*$", re.M)

#: Legacy HTML-comment markers from the first implementation. Still recognised
#: so existing CLAUDE.md files are updated in place rather than gaining a second
#: block below the first.
_LEGACY_START = "<!-- CB:ACTIVITY:START -->"
_LEGACY_END = "<!-- CB:ACTIVITY:END -->"

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.S)
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_WORDS = 5
_MAX_NOTE_FILES = 40


def _hash(text: str) -> str:
    """Short digest of a managed region's body, ignoring surrounding whitespace."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------
# Managed regions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ManagedRegion:
    """A located ``%% brain:begin %% … %% brain:end %%`` block."""

    before: str
    body: str
    after: str
    recorded_hash: str | None

    @property
    def hand_edited(self) -> bool:
        """Whether the body no longer matches the hash recorded when written.

        An absent hash means the block predates hashing, which is treated as
        *not* edited so the first write after an upgrade can still land.
        """
        if self.recorded_hash is None:
            return False
        return _hash(self.body) != self.recorded_hash


def split_managed(text: str) -> ManagedRegion | None:
    """Locate the managed region, or ``None`` if the note has none.

    A begin marker with no end marker returns ``None`` rather than raising: the
    note is then left alone and a fresh block is appended, which is safer than
    guessing where a truncated region was meant to stop. The previous
    implementation called ``str.index`` on the end marker and raised
    ``ValueError`` in exactly this case.
    """
    begin = MANAGED_BEGIN_RE.search(text)
    if not begin:
        return None
    end = MANAGED_END_RE.search(text, begin.end())
    if not end:
        log.warning("managed region has no end marker; leaving the note alone")
        return None
    return ManagedRegion(
        before=text[: begin.start()],
        body=text[begin.end() : end.start()],
        after=text[end.end() :],
        recorded_hash=begin.group(1),
    )


def render_managed(body: str) -> str:
    """Wrap generated content in markers carrying its hash."""
    inner = body.strip("\n")
    return f"%% brain:begin hash={_hash(inner)} %%\n{inner}\n%% brain:end %%"


def _normalised(text: str) -> str:
    """Exactly one trailing newline, so an unchanged note compares equal."""
    return text.rstrip("\n") + "\n"


def _replace_managed(existing: str, body: str) -> str | None:
    """Existing text with its managed region replaced, or ``None`` to skip.

    ``None`` means the region was hand-edited and must survive untouched.
    """
    block = render_managed(body)
    region = split_managed(existing)

    if region is None:
        if _LEGACY_START in existing and _LEGACY_END in existing:
            start = existing.index(_LEGACY_START)
            end = existing.index(_LEGACY_END) + len(_LEGACY_END)
            return existing[:start] + block + existing[end:]
        separator = "" if not existing.strip() else "\n\n"
        return existing.rstrip() + separator + block + "\n"

    if region.hand_edited:
        return None
    return region.before + block + region.after


# --------------------------------------------------------------------------
# Frontmatter
# --------------------------------------------------------------------------


def merge_frontmatter(existing: str, updates: dict[str, object]) -> str:
    """Return ``existing`` with ``updates`` applied to its YAML frontmatter.

    Merged **line by line**: a key we own replaces its line in place, a key we
    do not own is copied verbatim, and new keys are appended. The original line
    ordering is kept so a diff shows only what changed.

    Deliberately not a parse-and-dump round trip. Serialising frontmatter back
    out requires a complete YAML implementation; with a partial one, a user's
    nested map or block scalar is silently flattened or corrupted.
    """
    rendered = {key: _render_value(value) for key, value in updates.items() if value is not None}

    match = _FRONTMATTER_RE.match(existing)
    if not match:
        block = "\n".join(f"{key}: {value}" for key, value in rendered.items())
        body = existing.lstrip("\n")
        return f"---\n{block}\n---\n\n{body}" if block else existing

    seen: set[str] = set()
    lines: list[str] = []
    for line in match.group(1).splitlines():
        key_match = re.match(r"^([A-Za-z0-9_-]+):", line)
        key = key_match.group(1) if key_match else None
        if key and key in rendered:
            lines.append(f"{key}: {rendered[key]}")
            seen.add(key)
        else:
            lines.append(line)

    lines.extend(f"{key}: {value}" for key, value in rendered.items() if key not in seen)
    return "---\n" + "\n".join(lines) + "\n---\n" + existing[match.end() :]


def _render_value(value: object) -> str:
    """Scalar or flow-sequence YAML for the few shapes we write."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value if str(item).strip()]
        return "[" + ", ".join(items) + "]"
    text = str(value).strip()
    # Quote anything YAML would otherwise read as structure.
    if text and (text[0] in "[{&*!|>%@`'\"#" or ": " in text or text.endswith(":")):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def read_frontmatter_value(text: str, key: str) -> str | None:
    """One scalar value out of a note's frontmatter, without parsing all of it."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    found = re.search(rf"^{re.escape(key)}:[ \t]*(.*)$", match.group(1), re.M)
    return found.group(1).strip().strip("\"'") or None if found else None


# --------------------------------------------------------------------------
# Session notes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteResult:
    path: Path | None = None
    status: str = "skipped"  # written | updated | skipped | hand-edited

    @property
    def changed(self) -> bool:
        return self.status in {"written", "updated"}


def _slug(text: str) -> str:
    words = _SLUG_STRIP_RE.sub(" ", (text or "").lower()).split()
    return "_".join(words[:_MAX_SLUG_WORDS])


def _note_path(sessions_dir: Path, date: str, summary: str, session_id: str) -> Path | None:
    """A stable filename for this session, distinct from other sessions'.

    The first candidate is ``<date>_<slug>.md``. Two sessions on one day whose
    summaries begin with the same words produce the same candidate, and the
    previous implementation returned early on that collision - silently dropping
    the second session's note. The session id disambiguates, and because it is
    derived from the id rather than a counter the name stays stable across runs.
    """
    slug = _slug(summary)
    base = f"{date}_{slug}" if slug else date
    candidates = [sessions_dir / f"{base}.md", sessions_dir / f"{base}_{session_id[:8]}.md"]

    for candidate in candidates:
        if not candidate.is_file():
            return candidate
        owner = read_frontmatter_value(
            candidate.read_text(encoding="utf-8", errors="replace"), "session_id"
        )
        if owner in (None, session_id):
            return candidate
    return None


def _session_body(project_name: str, session, files: list, curated: str | None = None) -> str:
    """The managed body of a session note.

    ``curated`` is prose written by :mod:`claude_brain.core.curate`. When it
    is present it replaces the prompt list, because a write-up of what was
    decided is worth more than a transcript of what was typed - the prompts
    remain searchable in the database either way.
    """
    lines = [f"# Session {(session.date or 'unknown')[:10]}", ""]

    if curated and curated.strip():
        lines += [curated.strip(), ""]
    elif session.summary:
        lines += ["## What was asked", ""]
        lines += [f"- {line.strip()}" for line in session.summary.splitlines() if line.strip()]
        lines.append("")

    if files:
        lines += ["## Files touched", ""]
        lines += [f"- `{item.file_path}`" for item in files[:_MAX_NOTE_FILES]]
        if len(files) > _MAX_NOTE_FILES:
            lines.append(f"- _and {len(files) - _MAX_NOTE_FILES} more_")
        lines.append("")

    if session.notes:
        lines += ["## Notes", "", session.notes.strip(), ""]

    lines += [
        "---",
        "",
        f"_{session.tool_count} tool call(s), {session.file_count} file(s) changed._",
        f'_Run `brain recall "<topic>" --project {project_name}` for related work._',
    ]
    return "\n".join(lines)


def write_session_note(
    project_path: str | Path,
    project_name: str,
    session,
    files: list | None = None,
    *,
    curated: str | None = None,
) -> WriteResult:
    """Write or refresh one session's note. Never overwrites hand-edited prose."""
    root = Path(project_path)
    if not root.is_dir():
        return WriteResult(status="skipped")

    sessions_dir = root / "Sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    date = (session.date or "unknown")[:10]
    path = _note_path(sessions_dir, date, session.summary or "session", session.session_id)
    if path is None:
        log.warning("no free filename for session %s in %s", session.session_id, sessions_dir)
        return WriteResult(status="skipped")

    existing = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    merged = _replace_managed(existing, _session_body(project_name, session, files or [], curated))
    if merged is None:
        log.info("skipping hand-edited note %s", path.name)
        return WriteResult(path=path, status="hand-edited")

    merged = merge_frontmatter(
        merged,
        {
            "type": "session",
            "project": project_name,
            "date": date,
            "session_id": session.session_id,
            "files_changed": session.file_count,
            "tool_calls": session.tool_count,
            "tags": ["brain/session", "brain/curated"] if curated else ["brain/session"],
        },
    )

    merged = _normalised(merged)
    status = "updated" if existing else "written"
    if merged == existing:
        return WriteResult(path=path, status="skipped")
    path.write_text(merged, encoding="utf-8")
    return WriteResult(path=path, status=status)


# --------------------------------------------------------------------------
# The Sessions base
# --------------------------------------------------------------------------

#: Obsidian Bases. Checked against the installed Obsidian 1.12.7 bundle rather
#: than written from memory, because a malformed Base renders as an error in a
#: live synced vault:
#:
#: * Recognised top-level keys are `views`, `filters`, `display`, `properties`,
#:   `formulas`, `newItemFolder` and `newItemTemplate`. Anything else is kept
#:   but ignored.
#: * A filter object takes exactly one of `and`/`or`/`not`, each an array.
#: * `properties` values recognise only `displayName`.
#: * A view requires both `name` and `type`, and `direction` must be exactly
#:   `ASC` or `DESC`.
#: * `sort` entries take **`property`**. `column` is accepted as a legacy alias
#:   - the parser does `k.column && (k.property = k.column)` - but the app
#:   itself serialises `property`, so that is what we emit.
#: * `note.` addresses a frontmatter property, `file.` a file property.
#:
#: Written once and then left alone, because the user may customise the view.
_SESSIONS_BASE = """\
filters:
  and:
    - file.inFolder("Sessions")
    - note.type == "session"

properties:
  note.date:
    displayName: Date
  note.project:
    displayName: Project
  note.files_changed:
    displayName: Files
  note.tool_calls:
    displayName: Tools

views:
  - type: table
    name: Sessions
    order:
      - file.name
      - note.date
      - note.files_changed
      - note.tool_calls
    sort:
      - property: note.date
        direction: DESC
"""

#: Renderings this module generated before a template correction, byte for byte.
#: A file still matching one of these is ours and provably unedited, so the
#: correction can reach it; anything else is treated as the user's view and left
#: alone. Same contract as the managed regions: only rewrite what we can prove
#: nobody has touched.
_SUPERSEDED_BASES = (_SESSIONS_BASE.replace("- property: note.date", "- column: note.date"),)


def write_sessions_base(project_path: str | Path) -> WriteResult:
    """Create ``Sessions/Sessions.base``, or upgrade an untouched one of ours.

    Create-only in effect: a Base is a view the user is expected to adjust, and
    the data it reads lives in each note's frontmatter, so there is nothing to
    refresh. The sole exception is a file that still byte-matches a rendering
    this module generated earlier - see :data:`_SUPERSEDED_BASES`.
    """
    target = Path(project_path) / "Sessions" / "Sessions.base"
    if target.is_file():
        try:
            existing = target.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover - unreadable base file
            log.warning("cannot read %s: %s", target, exc)
            return WriteResult(path=target, status="skipped")
        if existing not in _SUPERSEDED_BASES:
            return WriteResult(path=target, status="skipped")
        target.write_text(_SESSIONS_BASE, encoding="utf-8")
        log.info("upgraded the Sessions base at %s", target)
        return WriteResult(path=target, status="written")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_SESSIONS_BASE, encoding="utf-8")
    return WriteResult(path=target, status="written")


# --------------------------------------------------------------------------
# CLAUDE.md
# --------------------------------------------------------------------------


def _activity_body(project_name: str, sessions: list, hot_files: list[str]) -> str:
    """The activity block.

    Carries **no wall-clock timestamp**. One was there, and it meant the block's
    text differed on every single pass, so each sweep rewrote ``CLAUDE.md`` in a
    vault that Obsidian Sync is watching - measured as the only file churning on
    an otherwise no-op re-sweep. "Last session" is the date that actually
    matters, and it changes only when the content does.
    """
    last = max((s.date or "" for s in sessions), default="")[:10] or "unknown"

    lines = [
        "## Recent Activity",
        "",
        f"- **Sessions tracked**: {len(sessions)}",
        f"- **Last session**: {last}",
        "",
    ]
    if hot_files:
        lines += ["**Most active files:**", ""]
        lines += [f"- `{path}`" for path in hot_files[:10]]
        lines.append("")
    if sessions:
        lines += ["**Recent work:**", ""]
        for session in sessions[:3]:
            first = next(
                (line.strip() for line in (session.summary or "").splitlines() if line.strip()),
                None,
            )
            if first:
                lines.append(f"- [{session.date or '?'}] {first[:80]}")
        lines.append("")
    lines.append(f'_`brain recall "<topic>" --project {project_name}` searches this history._')
    return "\n".join(lines)


def refresh_claude_md(
    project_path: str | Path,
    project_name: str,
    sessions: list,
    hot_files: list[str] | None = None,
) -> WriteResult:
    """Refresh the managed activity block in ``CLAUDE.md``.

    Creates the file when it is missing. The previous implementation returned
    early instead, which is why 11 of 17 projects never got one.
    """
    path = Path(project_path) / "CLAUDE.md"
    if not Path(project_path).is_dir():
        return WriteResult(status="skipped")

    existing = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    if not existing.strip():
        existing = f"# {project_name} - AI Context\n"

    merged = _replace_managed(existing, _activity_body(project_name, sessions, hot_files or []))
    if merged is None:
        return WriteResult(path=path, status="hand-edited")
    merged = _normalised(merged)
    if merged == existing:
        return WriteResult(path=path, status="skipped")

    status = "updated" if path.is_file() else "written"
    path.write_text(merged, encoding="utf-8")
    return WriteResult(path=path, status=status)


# --------------------------------------------------------------------------
# Vault provisioning
# --------------------------------------------------------------------------

_APP_JSON = {"alwaysUpdateLinks": True, "newLinkFormat": "relative", "useMarkdownLinks": False}
_APPEARANCE_JSON = {"accentColor": "#00D5FF", "theme": "obsidian"}


def ensure_vault(project_path: str | Path, project_name: str) -> list[Path]:
    """Provision the vault, creating only what is missing. Returns new files.

    Every write here is create-only. ``app.json`` and ``appearance.json`` are
    the user's own editor settings; the previous implementation rewrote both
    unconditionally on every refresh, discarding anything they had changed.
    """
    root = Path(project_path)
    if not root.is_dir():
        return []

    created: list[Path] = []
    obsidian = root / ".obsidian"
    obsidian.mkdir(parents=True, exist_ok=True)

    for name, payload in (("app.json", _APP_JSON), ("appearance.json", _APPEARANCE_JSON)):
        target = obsidian / name
        if not target.is_file():
            target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            created.append(target)

    (root / "Sessions").mkdir(exist_ok=True)
    base = write_sessions_base(root)
    if base.changed and base.path:
        created.append(base.path)

    for name, body in _starter_notes(project_name).items():
        target = root / name
        if not target.is_file():
            target.write_text(body, encoding="utf-8")
            created.append(target)

    return created


def _starter_notes(project_name: str) -> dict[str, str]:
    """Starter documents, from packaged templates where they exist.

    ``resources/vault_templates`` is consulted first so the set can be changed
    without a code edit; the inline fallbacks keep a bare checkout working.
    """
    from claude_brain.config import VAULT_TEMPLATES_DIR

    notes: dict[str, str] = {}
    if VAULT_TEMPLATES_DIR.is_dir():
        for template in sorted(VAULT_TEMPLATES_DIR.glob("*.md")):
            notes[template.name] = template.read_text(encoding="utf-8").replace(
                "{{project}}", project_name
            )
    if notes:
        return notes

    return {
        "Decisions.md": (
            f"---\ntype: decisions\nproject: {project_name}\n---\n\n"
            f"# {project_name} - Decision Log\n\n"
            "One entry per decision that would be expensive to revisit.\n\n"
            "## Template\n\n"
            "**Decision:** what was chosen\n"
            "**Date:** YYYY-MM-DD\n"
            "**Why:** the reasoning, including what was rejected\n"
            "**Revisit if:** the condition that would change the answer\n"
        ),
        "Architecture.md": (
            f"---\ntype: architecture\nproject: {project_name}\n---\n\n"
            f"# {project_name} - Architecture\n\n"
            "What the pieces are and how they fit together.\n"
        ),
        "TODO.md": (
            f"---\ntype: todo\nproject: {project_name}\n---\n\n# {project_name} - TODO\n\n- [ ] \n"
        ),
    }
