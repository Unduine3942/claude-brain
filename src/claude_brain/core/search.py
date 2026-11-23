"""Full-text search and decision recall over the store.

The index is rebuilt wholesale rather than maintained by triggers. Ingest is the
only writer, it is already idempotent, and a full rebuild of the real corpus
(73 sessions + 164 digests) takes single-digit milliseconds — while triggers
would have to reassemble a ``body`` that spans three tables on every row
change. A rebuild cannot go stale, which is the failure mode that matters: a
search that silently misses the session saved a minute ago.

User input never reaches ``MATCH`` unquoted. FTS5 treats ``"``, ``*``, ``(``,
``:``, ``^``, ``-``, ``AND``/``OR``/``NOT``/``NEAR`` as syntax, so an unescaped
query is at best a confusing error and at worst matches the wrong thing.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = [
    "SearchHit",
    "collect_notes",
    "escape_match",
    "recall",
    "reindex",
    "search",
]

#: bm25 weight per indexed column, in declaration order. Unindexed columns still
#: take a slot. A hit in the title outranks one buried in a task list.
_BM25_WEIGHTS = (0.0, 0.0, 0.0, 0.0, 2.0, 8.0, 1.0)

#: Curated markdown at a vault's root, in the order it is worth reading.
_VAULT_ROOT_NOTES = ("Decisions.md", "CLAUDE.md", "Architecture.md", "TODO.md", "README.md")

_FRONTMATTER_TITLE_RE = re.compile(r"^title:\s*(.+)$", re.M)
_FRONTMATTER_DATE_RE = re.compile(r"^(?:created|date):\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", re.M)
_HEADING_RE = re.compile(r"^#{1,2}\s+(.+?)\s*$", re.M)
_LEADING_DATE_RE = re.compile(r"^([0-9]{4}-[0-9]{2}-[0-9]{2})")

#: Per-note ceiling. The largest real vault note is ~8 KB; this is headroom,
#: not a target, and it keeps one runaway file from dominating the index.
_MAX_NOTE_CHARS = 60_000

_TOKEN_RE = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*\*?", re.UNICODE)
_MAX_TERMS = 16
_EXCERPT_TOKENS = 18


@dataclass(frozen=True)
class SearchHit:
    kind: str
    ref_id: str
    project: str | None
    date: str | None
    title: str
    excerpt: str
    score: float

    def __str__(self) -> str:
        where = f"{self.project or '-'} {self.date or '-'}"
        return f"[{self.kind}] {where}  {self.title}"


def escape_match(query: str) -> str:
    """Turn free text into a safe FTS5 ``MATCH`` expression.

    Every term is quoted, so FTS5 syntax in the input is treated as literal
    text. Two conveniences survive because they are what people actually type:

    * A fully quoted input is kept as one phrase — ``"quick sync"`` searches the
      words adjacent, not merely both present.
    * A trailing ``*`` keeps its prefix meaning (``prefil*`` finds *prefill*).
      FTS5 only accepts ``*`` outside the quotes, so it is re-attached there.
    """
    text = query.strip()
    if not text:
        return ""

    stripped = text.strip('"')
    if text.startswith('"') and text.endswith('"') and len(text) > 1 and stripped:
        return '"' + stripped.replace('"', "") + '"'

    terms: list[str] = []
    for match in _TOKEN_RE.finditer(text):
        token = match.group(0)  # brain-scan: allow - a variable named token, not one
        prefix = token.endswith("*")
        bare = token.rstrip("*").replace('"', "")
        if not bare:
            continue
        terms.append(f'"{bare}"*' if prefix else f'"{bare}"')
        if len(terms) >= _MAX_TERMS:
            break
    return " ".join(terms)


def _digest_body(row: sqlite3.Row) -> str:
    parts = [row["tasks"], row["notes"], row["files"], row["tools"]]
    return "\n".join(p for p in parts if p)


def _note_title(text: str, path: Path) -> str:
    """Frontmatter ``title``, then the first ATX heading, then the filename."""
    front = _FRONTMATTER_TITLE_RE.search(text)
    if front:
        return front.group(1).strip().strip("\"'")
    heading = _HEADING_RE.search(text)
    if heading:
        return heading.group(1).strip()
    return path.stem.replace("_", " ")


def _note_date(text: str, path: Path) -> str | None:
    front = _FRONTMATTER_DATE_RE.search(text)
    if front:
        return front.group(1).strip()
    stamp = _LEADING_DATE_RE.match(path.name)
    return stamp.group(1) if stamp else None


def collect_notes(conn: sqlite3.Connection) -> list[tuple]:
    """Index rows for the curated markdown in every registered project's vault.

    This is where the knowledge actually is. A session row holds the prompts and
    the files touched; the *conclusion* — "prefill is 52-202x faster cached" —
    is written in a vault note, and a search that cannot find it is not a brain.

    Scrubbed on the way in, like every other captured field: a vault note is
    hand-written and may quote a credential that was pasted into the chat.
    """
    from claude_brain.core.scrub import load_deny_literals, scrub

    literals = load_deny_literals()
    rows: list[tuple] = []

    for project in conn.execute("SELECT project_id, name, path FROM projects").fetchall():
        root = Path(project["path"])
        if not root.is_dir():
            continue
        candidates = [root / name for name in _VAULT_ROOT_NOTES]
        notes_dir = root / "Sessions"
        if notes_dir.is_dir():
            candidates.extend(sorted(notes_dir.glob("*.md")))

        for path in candidates:
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:  # pragma: no cover - unreadable vault file
                log.warning("cannot read vault note %s: %s", path, exc)
                continue
            if not text.strip() or path.name.lower() == "index.md":
                continue
            rows.append(
                (
                    "note",
                    str(path),
                    project["project_id"],
                    _note_date(text, path),
                    project["name"] or "",
                    _note_title(text, path),
                    scrub(text[:_MAX_NOTE_CHARS], literals=literals).text,
                )
            )

    return rows


def reindex(conn: sqlite3.Connection) -> int:
    """Rebuild the whole search index. Returns the number of rows indexed."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'search_index'").fetchone():
        log.warning("search_index is missing; run migrations first")
        return 0

    sessions = conn.execute(
        """SELECT s.session_id, s.project_id, s.date,
                  COALESCE(p.name, '')    AS project,
                  COALESCE(s.summary, '') AS title,
                  COALESCE(s.notes, '')   AS notes,
                  COALESCE(
                      (SELECT GROUP_CONCAT(f.file_path, char(10))
                       FROM session_files f WHERE f.session_id = s.session_id),
                      ''
                  ) AS files
           FROM sessions s
           LEFT JOIN projects p ON p.project_id = s.project_id"""
    ).fetchall()

    digests = conn.execute(
        """SELECT d.digest_id, d.project_id, d.date,
                  COALESCE(p.name, d.project_name, '') AS project,
                  COALESCE(d.topic, '') AS topic,
                  d.tasks, d.notes, d.files, d.tools
           FROM day_digests d
           LEFT JOIN projects p ON p.project_id = d.project_id"""
    ).fetchall()

    rows = [
        (
            "session",
            row["session_id"],
            row["project_id"],
            row["date"],
            row["project"],
            row["title"],
            "\n".join(p for p in (row["notes"], row["files"]) if p),
        )
        for row in sessions
    ]
    rows += [
        (
            "digest",
            row["digest_id"],
            row["project_id"],
            row["date"],
            row["project"],
            # A digest has no summary of its own; its topic, or failing that its
            # first task line, is the closest thing to a title.
            row["topic"] or _first_line(row["tasks"]),
            _digest_body(row),
        )
        for row in digests
    ]
    rows += collect_notes(conn)

    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM search_index")
        conn.executemany(
            """INSERT INTO search_index
               (kind, ref_id, project_id, date, project, title, body)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise

    log.info("search index rebuilt: %d rows", len(rows))
    return len(rows)


def _first_line(text: str | None, limit: int = 120) -> str:
    if not text:
        return ""
    return text.splitlines()[0][:limit]


def _excerpt(row: sqlite3.Row) -> str:
    """Prefer FTS5's own snippet; fall back to the head of the body.

    snippet() can still come back empty when the match was in the title only.
    """
    for key in ("snip_body", "snip_title"):
        value = row[key]
        if value and value.strip():
            return " ".join(value.split())
    return _first_line(row["body"], 160)


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    project: str | None = None,
    kind: str | None = None,
    limit: int = 20,
) -> list[SearchHit]:
    """Search sessions and digests. ``project`` matches a name or a path.

    Returns an empty list for an empty or all-punctuation query rather than
    raising: this is called from a CLI and from a skill.
    """
    expression = escape_match(query)
    if not expression:
        return []

    where = ["search_index MATCH ?"]
    params: list[object] = [expression]
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if project:
        where.append(
            """project_id IN (
                   SELECT project_id FROM projects
                   WHERE name = ? COLLATE NOCASE OR path = ? COLLATE NOCASE
               )"""
        )
        params.extend([project, project])

    weights = ", ".join(str(w) for w in _BM25_WEIGHTS)
    sql = f"""
        SELECT kind, ref_id, project, date, title, body,
               snippet(search_index, 6, '', '', '...', {_EXCERPT_TOKENS}) AS snip_body,
               snippet(search_index, 5, '', '', '...', {_EXCERPT_TOKENS}) AS snip_title,
               bm25(search_index, {weights}) AS score
        FROM search_index
        WHERE {" AND ".join(where)}
        ORDER BY score
        LIMIT ?
    """
    params.append(max(1, limit))

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        # A malformed MATCH should never crash a lookup; escape_match makes this
        # unlikely, but FTS5 rejects some inputs outright (e.g. only a prefix).
        log.warning("search failed for %r: %s", query, exc)
        return []

    return [
        SearchHit(
            kind=row["kind"],
            ref_id=row["ref_id"],
            project=row["project"] or None,
            date=row["date"],
            title=_first_line(row["title"]) or "(no summary)",
            excerpt=_excerpt(row),
            # bm25 returns a negative number, best first. Flip it so a bigger
            # score reads as a better match everywhere outside this module.
            score=-float(row["score"]),
        )
        for row in rows
    ]


def recall(
    conn: sqlite3.Connection,
    topic: str,
    *,
    project: str | None = None,
    limit: int = 8,
) -> list[SearchHit]:
    """Decision-oriented lookup: fewer, richer hits than :func:`search`.

    Curated notes come first when they match at all. Two of the digests are
    hand-written session write-ups and a third of the sessions carry real notes;
    those answer "what did we decide about X" far better than a task list does.
    """
    hits = search(conn, topic, project=project, limit=limit * 3)
    if not hits:
        return []

    curated = {
        row[0] for row in conn.execute("SELECT digest_id FROM day_digests WHERE is_curated = 1")
    }
    ordered = sorted(
        hits,
        key=lambda hit: (hit.ref_id not in curated, -hit.score),
    )
    return _dedupe(ordered)[:limit]


def _dedupe(hits: Sequence[SearchHit]) -> list[SearchHit]:
    """Collapse a session and the digest covering the same project and day.

    They describe the same work from two angles, and showing both twice in a
    short recall list wastes the space that a different day could use.
    """
    seen: set[tuple[str | None, str | None]] = set()
    out: list[SearchHit] = []
    for hit in hits:
        key = (hit.project, hit.date)
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out
