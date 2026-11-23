"""Tests for skill installation.

The failure these guard against is silent: a skill with no ``description`` is
omitted from the list the model sees, so it sits on disk looking installed and
is never chosen.
"""

from __future__ import annotations

import pytest

from claude_brain.core import skills


@pytest.fixture
def home(tmp_path, monkeypatch):
    target = tmp_path / "home"
    (target / ".claude").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: target))
    return target


# --------------------------------------------------------------------------
# The shipped skills
# --------------------------------------------------------------------------


def test_both_skills_exist_in_the_repo():
    for name in skills.SKILL_NAMES:
        assert (skills.source_dir() / name / "SKILL.md").is_file(), f"{name} is missing"


@pytest.mark.parametrize("name", skills.SKILL_NAMES)
def test_every_shipped_skill_has_a_usable_description(name):
    """Without one it is invocable by name but never offered to the model,
    which for these two defeats the point."""
    text = (skills.source_dir() / name / "SKILL.md").read_text(encoding="utf-8")
    description = skills._description(text)

    assert description, f"{name} has no usable description"
    assert len(description) >= 40


@pytest.mark.parametrize("name", skills.SKILL_NAMES)
def test_every_shipped_skill_says_when_to_use_it(name):
    """A description that only says what a skill *is* gives the model nothing
    to match a request against."""
    text = (skills.source_dir() / name / "SKILL.md").read_text(encoding="utf-8")
    assert "Use when" in skills._description(text)


@pytest.mark.parametrize("name", skills.SKILL_NAMES)
def test_every_shipped_skill_names_the_command_it_wraps(name):
    text = (skills.source_dir() / name / "SKILL.md").read_text(encoding="utf-8")
    assert "brain " in text, f"{name} never names a brain command"


# --------------------------------------------------------------------------
# Description parsing
# --------------------------------------------------------------------------


def test_a_description_is_read_from_the_frontmatter():
    text = "---\nname: x\ndescription: " + "a" * 50 + "\n---\n\nbody\n"
    assert skills._description(text) == "a" * 50


@pytest.mark.parametrize(
    "text",
    [
        "no frontmatter at all",
        "---\nname: x\n---\n\nbody\n",
        "---\nname: x\ndescription: too short\n---\n\nbody\n",
        "---\nname: x\ndescription:\n---\n\nbody\n",
    ],
)
def test_an_unusable_description_is_none(text):
    assert skills._description(text) is None


def test_quotes_are_stripped_from_the_description():
    text = '---\ndescription: "' + "a" * 50 + '"\n---\n\nbody\n'
    assert skills._description(text) == "a" * 50


# --------------------------------------------------------------------------
# Installing
# --------------------------------------------------------------------------


def test_install_copies_both_skills(home):
    installed, problems = skills.install_skills()

    assert set(installed) == set(skills.SKILL_NAMES)
    assert problems == ()
    for name in skills.SKILL_NAMES:
        assert (home / ".claude" / "skills" / name / "SKILL.md").is_file()


def test_install_is_idempotent(home):
    skills.install_skills()
    first = (home / ".claude" / "skills" / "brain-recall" / "SKILL.md").read_text(encoding="utf-8")

    skills.install_skills()

    assert (home / ".claude" / "skills" / "brain-recall" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == first


def test_install_refuses_a_skill_with_no_description(home, tmp_path, monkeypatch):
    """It would look installed and never be offered, which is worse than
    absent because nothing reports it."""
    fake = tmp_path / "src-skills"
    (fake / "brain-recall").mkdir(parents=True)
    (fake / "brain-recall" / "SKILL.md").write_text("---\nname: x\n---\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(skills, "source_dir", lambda: fake)

    installed, problems = skills.install_skills()

    assert "brain-recall" not in installed
    assert any("no usable description" in problem for problem in problems)
    assert not (home / ".claude" / "skills" / "brain-recall").exists()


def test_a_missing_source_skill_is_reported(home, tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "source_dir", lambda: tmp_path / "absent")

    installed, problems = skills.install_skills()

    assert installed == ()
    assert len(problems) == len(skills.SKILL_NAMES)


def test_dry_run_writes_nothing(home):
    installed, _ = skills.install_skills(dry_run=True)

    assert set(installed) == set(skills.SKILL_NAMES)
    assert not (home / ".claude" / "skills" / "brain-recall").exists()


def test_install_targets_the_user_scope(home):
    """Project scope would only fire while working on the brain itself."""
    skills.install_skills()
    assert skills.skills_dir() == home / ".claude" / "skills"


# --------------------------------------------------------------------------
# Status and removal
# --------------------------------------------------------------------------


def test_status_on_a_clean_machine(home):
    state = skills.status()

    assert not state.ok
    assert set(state.missing) == set(skills.SKILL_NAMES)
    assert any("not installed" in problem for problem in state.problems)


def test_status_after_installing(home):
    skills.install_skills()
    state = skills.status()

    assert state.ok
    assert state.problems == ()
    assert set(state.installed) == set(skills.SKILL_NAMES)


def test_status_flags_an_installed_skill_with_no_description(home):
    skills.install_skills()
    path = home / ".claude" / "skills" / "brain-save" / "SKILL.md"
    path.write_text("---\nname: brain-save\n---\n\nbody\n", encoding="utf-8")

    state = skills.status()

    assert not state.ok
    assert state.without_description == ("brain-save",)
    assert any("never choose it" in problem for problem in state.problems)
    assert "has no usable description" in state.describe()


def test_describe_names_every_skill(home):
    text = skills.status().describe()
    for name in skills.SKILL_NAMES:
        assert name in text


def test_uninstall_removes_both(home):
    skills.install_skills()

    assert skills.uninstall_skills() == 2
    assert skills.status().missing == skills.SKILL_NAMES


def test_uninstall_on_a_clean_machine_is_a_noop(home):
    assert skills.uninstall_skills() == 0


def test_uninstall_leaves_other_skills_alone(home):
    other = home / ".claude" / "skills" / "someone-elses-skill"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("---\nname: theirs\n---\n", encoding="utf-8")
    skills.install_skills()

    skills.uninstall_skills()

    assert (other / "SKILL.md").is_file(), "only our directories are removed"
