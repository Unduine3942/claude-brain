"""Merge multiple sessions into a single context markdown document."""

import uuid
from datetime import datetime

from claude_brain.config import CONSOLIDATIONS_DIR
from claude_brain.core.models import Consolidation, Session, SessionFile


def consolidate(
    project_name: str,
    project_id: str,
    sessions: list[Session],
    files_by_session: dict[str, list[SessionFile]],
) -> Consolidation:
    """Generate a consolidated markdown file from a list of sessions."""
    if not sessions:
        raise ValueError("No sessions to consolidate.")

    md = _build_markdown(project_name, sessions, files_by_session)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{_slugify(project_name)}_{timestamp}.md"
    CONSOLIDATIONS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CONSOLIDATIONS_DIR / filename
    out_path.write_text(md, encoding="utf-8")

    return Consolidation(
        consolidation_id=str(uuid.uuid4()),
        project_id=project_id,
        session_ids=tuple(s.session_id for s in sessions),
        markdown_path=str(out_path),
        created_at=datetime.now().isoformat(),
    )


def _build_markdown(
    project_name: str,
    sessions: list[Session],
    files_by_session: dict[str, list[SessionFile]],
) -> str:
    sorted_sessions = sorted(sessions, key=lambda s: s.date or "")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = [
        f"# {project_name} — Consolidated Session Context",
        "",
        f"_Generated: {now} | Sessions: {len(sessions)}_",
        "",
        "---",
        "",
        "## Purpose",
        "",
        "Inject this file at the start of a new Claude Code session to restore full project history.",
        "",
        "---",
        "",
        "## Session Timeline",
        "",
    ]

    all_files: set[str] = set()
    all_notes: list[str] = []

    for s in sorted_sessions:
        lines.append(f"### {s.date or 'Unknown Date'}")
        lines.append("")
        if s.summary:
            lines.append("**Tasks:**")
            for task in s.summary.splitlines():
                if task.strip():
                    lines.append(f"- {task.strip()}")
        lines.append("")
        lines.append(f"**Stats:** {s.file_count} files modified | {s.tool_count} tool uses")
        lines.append("")

        session_files = files_by_session.get(s.session_id, [])
        if session_files:
            lines.append("**Files Modified:**")
            for f in session_files:
                lines.append(f"- `{f.file_path}`")
                all_files.add(f.file_path)
            lines.append("")

        if s.notes:
            all_notes.append(f"- [{s.date}] {s.notes}")

        lines.append("---")
        lines.append("")

    # Summary section
    lines.append("## All Files Ever Modified")
    lines.append("")
    for fp in sorted(all_files):
        lines.append(f"- `{fp}`")
    lines.append("")

    if all_notes:
        lines.append("## Carry-Forward Notes")
        lines.append("")
        for note in all_notes:
            lines.append(note)
        lines.append("")

    lines.append("## Respin Instructions")
    lines.append("")
    lines.append("1. Open a new Claude Code session in the project directory")
    # `claude --context` does not exist and never has. Naming the file in the
    # first message is what actually works.
    lines.append(f'2. Ask it to read this file: "read {CONSOLIDATIONS_DIR} and continue"')
    lines.append("3. Or paste the Session Timeline section above as your first message")
    lines.append("")

    return "\n".join(lines)


def _slugify(name: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9_-]", "_", name).lower()
