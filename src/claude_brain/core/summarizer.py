"""Compact session summarizer — merges many sessions into one master record."""

import uuid
from datetime import datetime

from claude_brain.core.models import Session, SessionFile


def build_master_session(
    project_id: str,
    sessions: list[Session],
    files_by_session: dict[str, list[SessionFile]],
) -> Session:
    """Return a new master Session that compactly represents all provided sessions."""
    if not sessions:
        raise ValueError("No sessions to summarize.")

    sorted_sessions = sorted(sessions, key=lambda s: s.date or "")
    date_start = sorted_sessions[0].date or "?"
    date_end = sorted_sessions[-1].date or "?"
    total_files = sum(s.file_count for s in sorted_sessions)
    total_tools = sum(s.tool_count for s in sorted_sessions)

    # One line per session — keeps token cost proportional to session count
    lines: list[str] = [
        f"[MASTER — {len(sorted_sessions)} sessions · {date_start} → {date_end}]",
        "",
    ]
    for s in sorted_sessions:
        first = s.summary.splitlines()[0][:80] if s.summary else "—"
        marker = "⊞" if s.is_master else "·"
        lines.append(f"{marker} {s.date or '?'}: {first}")

    # Deduplicated file list (capped to keep size down)
    all_files: dict[str, int] = {}
    for s in sorted_sessions:
        for sf in files_by_session.get(s.session_id, []):
            all_files[sf.file_path] = all_files.get(sf.file_path, 0) + 1
    top_files = sorted(all_files, key=lambda f: -all_files[f])[:20]
    if top_files:
        lines += ["", "Files touched:"]
        lines += [f"  {fp}" for fp in top_files]

    # Carry-forward notes from the most recent session
    last_notes = next((s.notes for s in reversed(sorted_sessions) if s.notes), None)

    summary = "\n".join(lines)
    return Session(
        session_id=str(uuid.uuid4()),
        project_id=project_id,
        source_dir=sorted_sessions[-1].source_dir,
        date=datetime.now().strftime("%Y-%m-%d"),
        summary=summary,
        file_count=total_files,
        tool_count=total_tools,
        notes=last_notes,
        raw_path=None,
        is_master=True,
    )
