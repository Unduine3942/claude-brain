"""Installing the brain skills into ``~/.claude/skills``.

The skills are what make the store reachable *during* a session rather than
only after one. They live in the repo so they are version-controlled and
publishable, and are copied into the user scope on install so they apply in
every project rather than one.

Two details decide whether a skill is usable at all:

* **A skill without a ``description`` is omitted from the model-visible list.**
  It can still be invoked by name, but it will never be chosen on its own, which
  for these two defeats the point. The description is validated here rather than
  trusted.
* **User scope, not project scope.** ``~/.claude/skills`` applies everywhere;
  a copy inside this repo would only ever fire while working on the brain
  itself.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = [
    "SKILL_NAMES",
    "SkillStatus",
    "install_skills",
    "skills_dir",
    "source_dir",
    "status",
    "uninstall_skills",
]

SKILL_NAMES = ("brain-recall", "brain-save")

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.S)
_MIN_DESCRIPTION = 40


def skills_dir() -> Path:
    """Where Claude Code looks for user-scoped skills."""
    return Path.home() / ".claude" / "skills"


def source_dir() -> Path:
    """The repo's ``skills/`` directory, resolved from this file."""
    return (Path(__file__).resolve().parents[3] / "skills").resolve()


def _description(text: str) -> str | None:
    """The ``description`` field, or ``None`` when it is missing or too short.

    Length is checked because the description is the only thing the model sees
    when deciding whether a skill applies; a two-word one is as good as absent.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    found = re.search(r"^description:[ \t]*(.+)$", match.group(1), re.M)
    if not found:
        return None
    value = found.group(1).strip().strip("\"'")
    return value if len(value) >= _MIN_DESCRIPTION else None


@dataclass(frozen=True)
class SkillStatus:
    installed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    without_description: tuple[str, ...] = ()
    source: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.missing and not self.without_description

    @property
    def problems(self) -> tuple[str, ...]:
        found: list[str] = []
        if self.missing:
            found.append(
                "skill not installed: "
                + ", ".join(self.missing)
                + " - run `brain skills --install`"
            )
        if self.without_description:
            found.append(
                "skill has no usable description, so the model will never choose it: "
                + ", ".join(self.without_description)
            )
        return tuple(found)

    def describe(self) -> str:
        lines = [f"skills    {skills_dir()}", f"source    {self.source}"]
        for name in SKILL_NAMES:
            if name in self.without_description:
                state = "INSTALLED but has no usable description"
            elif name in self.installed:
                state = "ok"
            else:
                state = "NOT INSTALLED"
            lines.append(f"  {name:14} {state}")
        return "\n".join(lines)


def status() -> SkillStatus:
    """Report which skills are installed and whether they are model-visible."""
    target = skills_dir()
    installed: list[str] = []
    missing: list[str] = []
    undescribed: list[str] = []

    for name in SKILL_NAMES:
        path = target / name / "SKILL.md"
        if not path.is_file():
            missing.append(name)
            continue
        installed.append(name)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - unreadable skill file
            undescribed.append(name)
            continue
        if not _description(text):
            undescribed.append(name)

    return SkillStatus(
        installed=tuple(installed),
        missing=tuple(missing),
        without_description=tuple(undescribed),
        source=source_dir(),
    )


def install_skills(*, dry_run: bool = False) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Copy the skills into the user scope. Returns ``(installed, problems)``.

    A source skill with no usable description is refused rather than installed:
    it would sit there looking present while never being offered to the model.
    """
    source = source_dir()
    target = skills_dir()
    done: list[str] = []
    problems: list[str] = []

    for name in SKILL_NAMES:
        origin = source / name / "SKILL.md"
        if not origin.is_file():
            problems.append(f"{name}: missing from {source}")
            continue
        if not _description(origin.read_text(encoding="utf-8", errors="replace")):
            problems.append(f"{name}: no usable description, so it would never be chosen")
            continue
        if dry_run:
            done.append(name)
            continue
        destination = target / name
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, destination / "SKILL.md")
        done.append(name)
        log.info("installed skill %s", name)

    return tuple(done), tuple(problems)


def uninstall_skills(*, dry_run: bool = False) -> int:
    """Remove only our skill directories. Returns how many were removed."""
    target = skills_dir()
    removed = 0
    for name in SKILL_NAMES:
        directory = target / name
        if not directory.is_dir():
            continue
        if not dry_run:
            shutil.rmtree(directory)
        removed += 1
    return removed
