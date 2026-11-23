"""Tests for vault writing.

The governing property is that a generated write can never lose hand-written
text, so most of these assert what *survives* rather than what is produced.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from claude_brain.core import vault_writer as V


@dataclass
class FakeSession:
    session_id: str = "11111111-2222-3333-4444-555555555555"
    date: str = "2026-05-01"
    summary: str = "set up the jellyfin tunnel"
    notes: str | None = None
    file_count: int = 2
    tool_count: int = 9


@dataclass
class FakeFile:
    file_path: str


# --------------------------------------------------------------------------
# Managed regions
# --------------------------------------------------------------------------


def test_render_and_split_round_trip():
    block = V.render_managed("generated body")
    region = V.split_managed(block)

    assert region is not None
    assert region.body.strip() == "generated body"
    assert not region.hand_edited


def test_an_edited_region_is_detected():
    block = V.render_managed("generated body")
    tampered = block.replace("generated body", "a human wrote this")

    region = V.split_managed(tampered)

    assert region is not None
    assert region.hand_edited


def test_a_region_without_a_hash_is_not_treated_as_edited():
    """Blocks written before hashing must still accept their first update."""
    region = V.split_managed("%% brain:begin %%\nold content\n%% brain:end %%")
    assert region is not None and not region.hand_edited


def test_a_begin_marker_with_no_end_is_not_fatal():
    """The previous implementation called str.index on the end marker and
    raised ValueError here."""
    assert V.split_managed("%% brain:begin %%\ncontent but no end") is None


def test_text_outside_the_markers_is_preserved():
    original = "# Title\n\n" + V.render_managed("old") + "\n\nMy own notes below.\n"

    updated = V._replace_managed(original, "new")

    assert updated is not None
    assert updated.startswith("# Title")
    assert "My own notes below." in updated
    assert "new" in updated and "old" not in updated


def test_a_hand_edited_region_is_skipped_not_overwritten():
    original = V.render_managed("generated").replace("generated", "edited by hand")
    assert V._replace_managed(original, "new content") is None


def test_the_legacy_html_markers_are_replaced_in_place():
    """So an existing CLAUDE.md is updated rather than gaining a second block."""
    original = f"# Doc\n\n{V._LEGACY_START}\nold activity\n{V._LEGACY_END}\n\nMine.\n"

    updated = V._replace_managed(original, "new activity")

    assert updated is not None
    assert V._LEGACY_START not in updated
    assert "new activity" in updated
    assert "Mine." in updated
    assert updated.count("brain:begin") == 1


# --------------------------------------------------------------------------
# Frontmatter
# --------------------------------------------------------------------------


def test_frontmatter_is_added_when_absent():
    result = V.merge_frontmatter("# Title\n\nbody\n", {"type": "session"})
    assert result.startswith("---\ntype: session\n---\n")
    assert "# Title" in result


def test_our_keys_are_replaced_in_place():
    existing = "---\ntype: old\ndate: 2026-01-01\n---\n\nbody\n"

    result = V.merge_frontmatter(existing, {"type": "session"})

    assert "type: session" in result
    assert "type: old" not in result
    assert "date: 2026-01-01" in result, "untouched keys keep their value"
    assert result.index("type:") < result.index("date:"), "ordering is preserved"


def test_unknown_keys_survive_verbatim():
    """Nothing is parsed and re-serialised: a partial YAML round trip is how a
    nested map or block scalar gets silently flattened."""
    existing = (
        "---\n"
        "aliases:\n"
        "  - one\n"
        "  - two\n"
        "cssclasses: [wide]\n"
        "description: >\n"
        "  a folded\n"
        "  block scalar\n"
        "---\n\nbody\n"
    )

    result = V.merge_frontmatter(existing, {"type": "session"})

    for line in ("aliases:", "  - one", "cssclasses: [wide]", "description: >", "  a folded"):
        assert line in result, f"{line!r} must survive untouched"


def test_new_keys_are_appended():
    result = V.merge_frontmatter("---\nexisting: 1\n---\n\nbody\n", {"added": "yes"})
    assert "existing: 1" in result and "added: yes" in result


def test_none_values_are_not_written():
    result = V.merge_frontmatter("---\na: 1\n---\n\nbody\n", {"b": None})
    assert "b:" not in result


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (["one", "two"], "[one, two]"),
        (True, "true"),
        (False, "false"),
        (7, "7"),
        ("plain text", "plain text"),
        ("has: a colon", '"has: a colon"'),
        ("[bracketed]", '"[bracketed]"'),
        ("#hash", '"#hash"'),
    ],
)
def test_value_rendering(value, expected):
    assert V._render_value(value) == expected


def test_reading_one_value_back():
    text = "---\nsession_id: abc-123\nother: x\n---\n\nbody\n"
    assert V.read_frontmatter_value(text, "session_id") == "abc-123"
    assert V.read_frontmatter_value(text, "missing") is None
    assert V.read_frontmatter_value("no frontmatter", "session_id") is None


# --------------------------------------------------------------------------
# Session notes
# --------------------------------------------------------------------------


def test_a_note_is_written_with_frontmatter_and_a_managed_region(tmp_path):
    result = V.write_session_note(tmp_path, "Movie_Server", FakeSession(), [FakeFile("C:/a.py")])

    assert result.status == "written"
    text = result.path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "type: session" in text
    assert "project: Movie_Server" in text
    assert "brain:begin" in text and "brain:end" in text
    assert "jellyfin tunnel" in text
    assert "`C:/a.py`" in text


def test_the_filename_is_the_date_and_a_slug(tmp_path):
    result = V.write_session_note(tmp_path, "P", FakeSession())
    assert result.path.name == "2026-05-01_set_up_the_jellyfin_tunnel.md"


def test_two_sessions_on_one_day_both_get_notes(tmp_path):
    """The previous implementation returned early on a filename collision,
    silently dropping the second session's note."""
    first = V.write_session_note(tmp_path, "P", FakeSession(session_id="aaaa1111"))
    second = V.write_session_note(tmp_path, "P", FakeSession(session_id="bbbb2222"))

    assert first.path != second.path
    assert first.status == "written" and second.status == "written"
    assert len(list((tmp_path / "Sessions").glob("*.md"))) == 2


def test_rewriting_the_same_session_reuses_its_note(tmp_path):
    session = FakeSession()
    first = V.write_session_note(tmp_path, "P", session)
    second = V.write_session_note(tmp_path, "P", session)

    assert first.path == second.path
    assert second.status == "skipped", "identical content is not rewritten"
    assert len(list((tmp_path / "Sessions").glob("*.md"))) == 1


def test_a_changed_session_updates_its_note(tmp_path):
    session = FakeSession()
    V.write_session_note(tmp_path, "P", session)

    session.notes = "a new conclusion"
    result = V.write_session_note(tmp_path, "P", session)

    assert result.status == "updated"
    assert "a new conclusion" in result.path.read_text(encoding="utf-8")


def test_a_hand_edited_note_is_never_overwritten(tmp_path):
    session = FakeSession()
    path = V.write_session_note(tmp_path, "P", session).path
    edited = path.read_text(encoding="utf-8").replace(
        "set up the jellyfin tunnel", "MY OWN WORDS, carefully chosen"
    )
    path.write_text(edited, encoding="utf-8")

    session.notes = "something new the writer would add"
    result = V.write_session_note(tmp_path, "P", session)

    assert result.status == "hand-edited"
    assert "MY OWN WORDS" in path.read_text(encoding="utf-8")
    assert "something new" not in path.read_text(encoding="utf-8")


def test_prose_a_user_appended_below_the_region_survives(tmp_path):
    session = FakeSession()
    path = V.write_session_note(tmp_path, "P", session).path
    path.write_text(
        path.read_text(encoding="utf-8") + "\n## My follow-up\n\nStill to do: rotate the key.\n",
        encoding="utf-8",
    )

    session.notes = "regenerated"
    result = V.write_session_note(tmp_path, "P", session)

    text = path.read_text(encoding="utf-8")
    assert result.status == "updated"
    assert "Still to do: rotate the key." in text
    assert "regenerated" in text


def test_a_long_file_list_is_truncated(tmp_path):
    files = [FakeFile(f"C:/f{i}.py") for i in range(60)]
    result = V.write_session_note(tmp_path, "P", FakeSession(), files)

    text = result.path.read_text(encoding="utf-8")
    assert "and 20 more" in text


def test_a_missing_project_directory_is_not_an_error(tmp_path):
    assert V.write_session_note(tmp_path / "nope", "P", FakeSession()).status == "skipped"


def test_a_session_with_no_summary_still_gets_a_note(tmp_path):
    result = V.write_session_note(tmp_path, "P", FakeSession(summary=""))
    assert result.status == "written"
    assert result.path.name.startswith("2026-05-01")


# --------------------------------------------------------------------------
# The Sessions base
# --------------------------------------------------------------------------


def test_the_base_is_written_once(tmp_path):
    first = V.write_sessions_base(tmp_path)
    second = V.write_sessions_base(tmp_path)

    assert first.status == "written"
    assert second.status == "skipped", "the user may customise the view"


def test_the_base_is_valid_yaml_with_the_expected_keys(tmp_path):
    V.write_sessions_base(tmp_path)
    text = (tmp_path / "Sessions" / "Sessions.base").read_text(encoding="utf-8")

    assert 'file.inFolder("Sessions")' in text
    assert "views:" in text and "type: table" in text
    assert "direction: DESC" in text
    # `note.` addresses frontmatter, `file.` addresses file properties.
    assert "note.date" in text and "file.name" in text


def test_the_base_columns_match_what_notes_actually_write(tmp_path):
    """A column naming a key the writer never emits renders an empty column."""
    V.write_sessions_base(tmp_path)
    base = (tmp_path / "Sessions" / "Sessions.base").read_text(encoding="utf-8")
    note = V.write_session_note(tmp_path, "P", FakeSession()).path.read_text(encoding="utf-8")

    for column in ("date", "project", "files_changed", "tool_calls"):
        assert f"note.{column}" in base
        assert f"{column}:" in note


def test_sort_uses_the_canonical_property_key(tmp_path):
    """Obsidian accepts `column` as a legacy alias but serialises `property`.
    Emitting the alias would be silently rewritten the first time the view is
    edited in the app."""
    V.write_sessions_base(tmp_path)
    text = (tmp_path / "Sessions" / "Sessions.base").read_text(encoding="utf-8")

    assert "- property: note.date" in text
    assert "column:" not in text


def test_a_view_declares_both_keys_obsidian_requires(tmp_path):
    """The parser throws on a view missing `name` or `type`, which renders the
    whole Base as an error rather than degrading."""
    V.write_sessions_base(tmp_path)
    text = (tmp_path / "Sessions" / "Sessions.base").read_text(encoding="utf-8")

    assert "name: Sessions" in text
    assert "type: table" in text


def test_only_keys_obsidian_recognises_appear_at_the_top_level(tmp_path):
    V.write_sessions_base(tmp_path)
    text = (tmp_path / "Sessions" / "Sessions.base").read_text(encoding="utf-8")
    recognised = {
        "views",
        "filters",
        "display",
        "properties",
        "formulas",
        "newItemFolder",
        "newItemTemplate",
    }
    top_level = {
        line.split(":", 1)[0] for line in text.splitlines() if line and not line[0].isspace()
    }

    assert top_level <= recognised, f"unrecognised: {top_level - recognised}"


def test_a_base_we_generated_earlier_is_upgraded(tmp_path):
    """A superseded rendering is provably unedited, so a template correction
    can reach it. This is what keeps 45 live vaults from carrying the alias."""
    target = tmp_path / "Sessions" / "Sessions.base"
    target.parent.mkdir(parents=True)
    target.write_text(V._SUPERSEDED_BASES[0], encoding="utf-8")

    result = V.write_sessions_base(tmp_path)

    assert result.status == "written"
    assert "- property: note.date" in target.read_text(encoding="utf-8")


def test_a_customised_base_is_never_upgraded(tmp_path):
    customised = V._SUPERSEDED_BASES[0] + "\n  - type: cards\n    name: Cards\n"
    target = tmp_path / "Sessions" / "Sessions.base"
    target.parent.mkdir(parents=True)
    target.write_text(customised, encoding="utf-8")

    result = V.write_sessions_base(tmp_path)

    assert result.status == "skipped"
    assert target.read_text(encoding="utf-8") == customised


# --------------------------------------------------------------------------
# CLAUDE.md
# --------------------------------------------------------------------------


def test_claude_md_is_created_when_missing(tmp_path):
    """The previous implementation returned early instead, which is why 11 of
    17 projects never got one."""
    result = V.refresh_claude_md(tmp_path, "Demo", [FakeSession()])

    assert result.status in {"written", "updated"}
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Demo - AI Context" in text
    assert "Recent Activity" in text


def test_claude_md_keeps_the_user_s_own_sections(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text(
        "# Demo\n\n## Key Constraints\n\n- The viewer can install nothing.\n", encoding="utf-8"
    )

    V.refresh_claude_md(tmp_path, "Demo", [FakeSession()])
    text = path.read_text(encoding="utf-8")

    assert "## Key Constraints" in text
    assert "The viewer can install nothing." in text
    assert "Recent Activity" in text


def test_refreshing_twice_does_not_stack_blocks(tmp_path):
    V.refresh_claude_md(tmp_path, "Demo", [FakeSession()])
    V.refresh_claude_md(tmp_path, "Demo", [FakeSession()])

    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.count("brain:begin") == 1
    assert text.count("Recent Activity") == 1


def test_hot_files_are_listed(tmp_path):
    V.refresh_claude_md(tmp_path, "Demo", [FakeSession()], ["C:/a.py", "C:/b.py"])
    assert "`C:/a.py`" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")


def test_a_hand_edited_activity_block_is_skipped(tmp_path):
    V.refresh_claude_md(tmp_path, "Demo", [FakeSession()])
    path = tmp_path / "CLAUDE.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("Recent Activity", "MY HEADING"), encoding="utf-8"
    )

    result = V.refresh_claude_md(tmp_path, "Demo", [FakeSession()])

    assert result.status == "hand-edited"
    assert "MY HEADING" in path.read_text(encoding="utf-8")


def test_an_empty_session_list_is_handled(tmp_path):
    V.refresh_claude_md(tmp_path, "Demo", [])
    assert "Sessions tracked**: 0" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Provisioning
# --------------------------------------------------------------------------


def test_ensure_vault_creates_the_expected_layout(tmp_path):
    created = V.ensure_vault(tmp_path, "Demo")

    names = {path.name for path in created}
    assert {"app.json", "appearance.json", "Sessions.base"} <= names
    assert (tmp_path / "Sessions").is_dir()
    assert (tmp_path / "Decisions.md").is_file()


def test_ensure_vault_never_clobbers_obsidian_settings(tmp_path):
    """app.json and appearance.json are the user's editor settings. The previous
    implementation rewrote both on every vault refresh."""
    obsidian = tmp_path / ".obsidian"
    obsidian.mkdir()
    (obsidian / "appearance.json").write_text('{"theme": "moonstone"}', encoding="utf-8")

    V.ensure_vault(tmp_path, "Demo")

    assert (obsidian / "appearance.json").read_text(encoding="utf-8") == '{"theme": "moonstone"}'


def test_ensure_vault_never_clobbers_starter_notes(tmp_path):
    (tmp_path / "Decisions.md").write_text("# My own decisions\n", encoding="utf-8")

    V.ensure_vault(tmp_path, "Demo")

    assert (tmp_path / "Decisions.md").read_text(encoding="utf-8") == "# My own decisions\n"


def test_ensure_vault_is_idempotent(tmp_path):
    V.ensure_vault(tmp_path, "Demo")
    assert V.ensure_vault(tmp_path, "Demo") == []


def test_ensure_vault_on_a_missing_directory(tmp_path):
    assert V.ensure_vault(tmp_path / "nope", "Demo") == []


# --------------------------------------------------------------------------
# Packaged templates
# --------------------------------------------------------------------------


def test_the_packaged_templates_are_used(tmp_path):
    """resources/vault_templates was an empty directory, so git did not track
    it and _load_templates always fell through to the inline defaults."""
    from claude_brain.config import VAULT_TEMPLATES_DIR

    assert VAULT_TEMPLATES_DIR.is_dir(), "the templates must ship with the package"
    assert list(VAULT_TEMPLATES_DIR.glob("*.md")), "and must not be empty"

    notes = V._starter_notes("Demo")

    assert "Decisions.md" in notes
    assert "{{project}}" not in notes["Decisions.md"], "the placeholder is substituted"
    assert "Demo" in notes["Decisions.md"]


def test_every_template_carries_frontmatter_the_base_can_read(tmp_path):
    from claude_brain.config import VAULT_TEMPLATES_DIR

    for template in VAULT_TEMPLATES_DIR.glob("*.md"):
        text = template.read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{template.name} has no frontmatter"
        assert V.read_frontmatter_value(text, "type"), f"{template.name} has no type"
        assert "{{project}}" in text, f"{template.name} never names the project"


def test_the_inline_fallback_still_works_without_the_templates(tmp_path, monkeypatch):
    """A bare checkout with no resources directory must still provision."""
    monkeypatch.setattr("claude_brain.config.VAULT_TEMPLATES_DIR", tmp_path / "absent")

    notes = V._starter_notes("Demo")

    assert "Decisions.md" in notes
    assert "Demo" in notes["Decisions.md"]


def test_the_activity_block_carries_no_wall_clock_timestamp(tmp_path):
    """A timestamp made the block differ on every pass, so each sweep rewrote
    CLAUDE.md in a vault Obsidian Sync is watching."""
    V.refresh_claude_md(tmp_path, "Demo", [FakeSession()])
    first = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    result = V.refresh_claude_md(tmp_path, "Demo", [FakeSession()])

    assert result.status == "skipped", "an unchanged block must not be rewritten"
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == first
    assert "auto-updated" not in first


def test_the_activity_block_does_change_when_the_history_does(tmp_path):
    V.refresh_claude_md(tmp_path, "Demo", [FakeSession()])

    newer = FakeSession(session_id="bbbb", date="2026-06-01", summary="newer work")
    result = V.refresh_claude_md(tmp_path, "Demo", [newer, FakeSession()])

    assert result.status == "updated"
    assert "newer work" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
