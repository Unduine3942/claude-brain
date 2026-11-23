"""Parse CLAUDE.md vault files into structured dicts for display."""

import re
from pathlib import Path


def parse_claude_md(path: str) -> dict:
    """Return structured sections from a CLAUDE.md file.

    Returns:
        {
            "priorities": list[str],
            "purpose": str,
            "stack": str,
            "behavioral_prefs": list[str],
            "raw": str,
        }
    """
    p = Path(path)
    if not p.exists():
        return _empty()

    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return _empty()

    return {
        "priorities": _extract_list(text, "Current Priorities"),
        "purpose": _extract_kv(text, "Purpose"),
        "stack": _extract_kv(text, "Stack"),
        "behavioral_prefs": _extract_list(text, "Behavioral Preferences"),
        "raw": text,
    }


def parse_todo_md(path: str) -> list[dict]:
    """Parse TODO.md into a list of task dicts with checked/text fields."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    tasks = []
    for line in lines:
        m = re.match(r"^\s*-\s+\[([ xX])\]\s+(.+)$", line)
        if m:
            tasks.append({"checked": m.group(1).lower() == "x", "text": m.group(2).strip()})
    return tasks


def write_todo_md(path: str, tasks: list[dict]) -> None:
    """Write updated task list back to TODO.md, preserving non-task lines."""
    p = Path(path)
    if not p.exists():
        return
    try:
        original = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    task_iter = iter(tasks)
    result = []
    for line in original:
        m = re.match(r"^\s*-\s+\[([ xX])\]\s+(.+)$", line)
        if m:
            try:
                task = next(task_iter)
                mark = "x" if task["checked"] else " "
                result.append(f"- [{mark}] {task['text']}")
            except StopIteration:
                result.append(line)
        else:
            result.append(line)
    p.write_text("\n".join(result) + "\n", encoding="utf-8")


# ---- helpers ---------------------------------------------------------------


def _empty() -> dict:
    return {"priorities": [], "purpose": "", "stack": "", "behavioral_prefs": [], "raw": ""}


def _section_text(text: str, heading: str) -> str:
    """Return the raw text block under a ## heading (until next ## or EOF)."""
    pattern = re.compile(
        r"^##\s+" + re.escape(heading) + r"\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        return ""
    start = m.end()
    next_heading = re.search(r"^##\s+", text[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(text)
    return text[start:end].strip()


def _extract_list(text: str, heading: str) -> list[str]:
    """Extract numbered or bulleted list items from a section."""
    block = _section_text(text, heading)
    items = []
    for line in block.splitlines():
        m = re.match(r"^\s*(?:\d+\.|[-*+])\s+(.+)$", line)
        if m:
            item = m.group(1).strip()
            if item:
                items.append(item)
    return items


def _extract_kv(text: str, key: str) -> str:
    """Extract value from a **Key**: value line anywhere in the file."""
    m = re.search(
        r"^\*\*" + re.escape(key) + r"\*\*\s*:\s*(.+)$",
        text,
        re.IGNORECASE | re.MULTILINE,
    )
    if m:
        val = m.group(1).strip()
        if val and val != "(describe what this project does and why it exists)":
            return val
    return ""
