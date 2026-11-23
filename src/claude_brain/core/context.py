"""Build the project-scoped context injected at ``SessionStart``.

This is the one place the brain stops being an archive and starts being useful:
it answers "what was I doing in this project last time" before the first
question is asked, and it tells the model that ``brain recall`` exists.

It is deliberately **scoped by ``cwd``**. The plugin this replaces picked the
newest ``.tmp`` anywhere under the sessions directory with no project filter, so
it routinely injected another project's digest - which is the root cause of the
cross-contaminated summaries this store spent two months accumulating.

Kept short on purpose: it is spent from every session's context window, so it
pays for itself only if it is a few hundred characters of the right thing.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = ["build_session_context"]

_MAX_SESSIONS = 3
_MAX_NOTES = 4
_MAX_TITLE = 110
_MAX_TOTAL = 1800


def _truncate(text: str | None, limit: int = _MAX_TITLE) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned[: limit - 1] + "…" if len(cleaned) > limit else cleaned


def _project_for(conn: sqlite3.Connection, cwd: str | None) -> sqlite3.Row | None:
    """The registered project for ``cwd``, or the nearest enclosing one.

    An exact match is preferred. The walk upwards exists because a session is
    often started in a subdirectory of the project - ``repo/backend`` is still
    the ``repo`` project, and refusing to answer there would make the injection
    useless exactly when a monorepo needs it most.
    """
    if not cwd:
        return None

    row = conn.execute(
        "SELECT project_id, name, path FROM projects WHERE path = ? COLLATE NOCASE",
        (cwd,),
    ).fetchone()
    if row:
        return row

    try:
        here = Path(cwd).resolve()
    except OSError:  # pragma: no cover - unresolvable path
        return None

    candidates = [
        (parent, row)
        for row in conn.execute("SELECT project_id, name, path FROM projects").fetchall()
        if row["path"]
        for parent in [Path(row["path"])]
        if _is_within(here, parent)
    ]
    if not candidates:
        return None
    # Deepest enclosing project wins, so a nested project beats its parent.
    return max(candidates, key=lambda pair: len(pair[0].parts))[1]


def _is_container(path: Path) -> bool:
    r"""Whether ``path`` is too broad to lend its history to what it contains.

    Capture is opt-out, so a session started in the home directory registers it
    as a project - correctly, work did happen there. But as an *enclosing*
    project the home directory is nearly as broad as the drive root: every
    scratch directory under it would inherit its history, which is the
    cross-project leak this module exists to remove.

    Both still match **exactly**; they just do not adopt their children. In
    testing, a throwaway directory under ``AppData\Local\Temp`` was handed the
    home project's sessions, which is what this prevents.
    """
    if len(path.parts) <= 1:
        return True
    try:
        return path.resolve() in {Path.home().resolve(), *Path.home().resolve().parents}
    except OSError:  # pragma: no cover - unresolvable path
        return False


def _is_within(child: Path, parent: Path) -> bool:
    """Whether ``child`` sits under ``parent``, excluding container directories."""
    if _is_container(parent):
        return False
    try:
        child.relative_to(parent)
    except (ValueError, OSError):
        return False
    return True


def build_session_context(
    conn: sqlite3.Connection,
    cwd: str | None,
    *,
    exclude_session_id: str | None = None,
) -> str:
    """Return the text to inject, or ``""`` when there is nothing worth saying.

    ``exclude_session_id`` drops the session being started, which otherwise
    appears as its own "previous work" on a resume.
    """
    project = _project_for(conn, cwd)
    if project is None:
        return ""

    lines: list[str] = []
    try:
        sessions = conn.execute(
            """SELECT date, summary FROM sessions
               WHERE project_id = ?
                 AND COALESCE(session_id, '') <> COALESCE(?, '')
                 AND TRIM(COALESCE(summary, '')) NOT IN ('', '(no summary)')
               ORDER BY date DESC, parsed_at DESC
               LIMIT ?""",
            (project["project_id"], exclude_session_id, _MAX_SESSIONS),
        ).fetchall()

        # Only the two hand-written digests carry a **Topic:** line, so fall
        # back to the first task - otherwise this section never appears for the
        # 162 generated ones, which is almost all of the history.
        digests = conn.execute(
            """SELECT date,
                      COALESCE(NULLIF(TRIM(COALESCE(topic, '')), ''), tasks, '') AS topic
               FROM day_digests
               WHERE project_id = ?
                 AND TRIM(COALESCE(topic, '') || COALESCE(tasks, '')) <> ''
               ORDER BY date DESC LIMIT ?""",
            (project["project_id"], _MAX_SESSIONS),
        ).fetchall()

        notes = conn.execute(
            """SELECT date, title FROM search_index
               WHERE project_id = ? AND kind = 'note'
               ORDER BY COALESCE(date, '') DESC LIMIT ?""",
            (project["project_id"], _MAX_NOTES),
        ).fetchall()

        totals = conn.execute(
            """SELECT
                   (SELECT COUNT(*) FROM sessions    WHERE project_id = ?) AS sessions,
                   (SELECT COUNT(*) FROM day_digests WHERE project_id = ?) AS digests""",
            (project["project_id"], project["project_id"]),
        ).fetchone()
    except sqlite3.Error as exc:  # pragma: no cover - partially migrated store
        log.warning("context query failed: %s", exc)
        return ""

    if not (sessions or digests or notes):
        return ""

    name = project["name"] or Path(project["path"]).name
    lines.append(f"## Claude Brain - prior context for {name}")
    lines.append("")

    if sessions:
        lines.append("Recent sessions:")
        lines.extend(f"- {row['date']}: {_truncate(row['summary'])}" for row in sessions)
        lines.append("")

    if digests:
        lines.append("Recent day digests:")
        lines.extend(
            f"- {row['date']}: {_truncate(row['topic'].splitlines()[0])}" for row in digests
        )
        lines.append("")

    if notes:
        lines.append("Curated notes in this project's vault:")
        lines.extend(
            f"- {row['title']}" + (f" ({row['date']})" if row["date"] else "") for row in notes
        )
        lines.append("")

    span = f"{totals['sessions']} session(s), {totals['digests']} day digest(s)"
    lines.append(
        f"The brain holds {span} for this project. "
        f'Run `brain recall "<topic>" --project {name}` for the detail behind any '
        "of the above, or `brain search` across every project. This context is "
        "scoped to this working directory - it is not another project's history."
    )

    text = "\n".join(lines)
    return text if len(text) <= _MAX_TOTAL else text[: _MAX_TOTAL - 3] + "..."
