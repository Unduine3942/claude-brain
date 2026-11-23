"""Tests for the single ingest implementation."""

from __future__ import annotations

from claude_brain.core import ingest as I

FAKE_SECRET = "Qx7!tempPassw0rd#42"


# --------------------------------------------------------------------------
# Project resolution. This is the fix for 865 of 882 rows being unlinked.
# --------------------------------------------------------------------------


def test_resolves_existing_project_by_cwd(repos, make_transcript, tmp_path):
    workdir = tmp_path / "Demo"
    workdir.mkdir()
    project = repos.projects.upsert("Demo", str(workdir))
    path = make_transcript(cwd=str(workdir))

    from claude_brain.core.transcript import read_transcript

    resolved, registered = I.resolve_project(repos, read_transcript(path))

    assert resolved is not None
    assert resolved.project_id == project.project_id
    assert not registered


def test_sibling_projects_do_not_collide(repos, make_transcript, tmp_path):
    """The headline matching bug: slug prefixes collapsed siblings.

    `Translator_OSM` and `Translator_OSM_Offline` both resolved to `Translator`,
    because `_` becomes `-` in the slug and a sibling is then indistinguishable
    from a subdirectory.
    """
    base = tmp_path / "Scripting_Projects"
    paths = {}
    for name in ("Translator", "Translator_OSM", "Translator_OSM_Offline"):
        directory = base / name
        directory.mkdir(parents=True)
        paths[name] = repos.projects.upsert(name, str(directory)).project_id

    from claude_brain.core.transcript import read_transcript

    resolved_ids = {}
    for name in ("Translator", "Translator_OSM", "Translator_OSM_Offline"):
        transcript = make_transcript(
            name=f"{name}-0000-0000-0000-000000000000",
            cwd=str(base / name),
            subdir=f"C--{name}",
        )
        project, _ = I.resolve_project(repos, read_transcript(transcript))
        assert project is not None, name
        resolved_ids[name] = project.project_id

    assert len(set(resolved_ids.values())) == 3, f"projects collapsed: {resolved_ids}"
    for name, project_id in resolved_ids.items():
        assert project_id == paths[name], f"{name} resolved to the wrong project"


def test_auto_registers_unknown_project(repos, make_transcript, tmp_path):
    workdir = tmp_path / "BrandNew"
    workdir.mkdir()
    path = make_transcript(cwd=str(workdir))

    from claude_brain.core.transcript import read_transcript

    project, registered = I.resolve_project(repos, read_transcript(path))

    assert registered
    assert project is not None
    assert project.name == "BrandNew"
    assert repos.projects.find_by_path(str(workdir)) is not None


def test_auto_register_skipped_when_directory_is_gone(repos, make_transcript, tmp_path):
    path = make_transcript(cwd=str(tmp_path / "deleted-project"))

    from claude_brain.core.transcript import read_transcript

    project, registered = I.resolve_project(repos, read_transcript(path))

    assert not registered
    assert project is None


def test_denylist_blocks_capture(repos, make_transcript, tmp_path):
    workdir = tmp_path / "WORK" / "Secret"
    workdir.mkdir(parents=True)
    path = make_transcript(cwd=str(workdir))
    deny = (str(tmp_path / "WORK").lower(),)

    from claude_brain.core.transcript import read_transcript

    project, registered = I.resolve_project(repos, read_transcript(path), denylist=deny)

    assert project is None
    assert not registered
    assert I.ingest_transcript(repos, path, denylist=deny)[0] == "denied"


def test_load_denylist_reads_setting(repos):
    repos.settings.set(I.DENYLIST_SETTING, "C:\\WORK\nC:\\Secret\n")
    assert I.load_denylist(repos) == ("c:\\work", "c:\\secret")


# --------------------------------------------------------------------------
# Storage, scrubbing and idempotency
# --------------------------------------------------------------------------


def test_ingest_stores_session_and_files(repos, make_transcript, tmp_path):
    workdir = tmp_path / "Demo"
    workdir.mkdir()
    path = make_transcript(
        cwd=str(workdir), prompts=("build the thing",), edits=(r"C:\x.py", r"C:\y.py")
    )

    status, _, _ = I.ingest_transcript(repos, path, write_vault=False)

    assert status == "ingested"
    stored = repos.sessions.find_by_id("11111111-2222-3333-4444-555555555555")
    assert stored is not None
    assert stored.file_count == 2
    assert stored.tool_count == 2
    assert "build the thing" in stored.summary
    assert len(repos.session_files.find_by_session(stored.session_id)) == 2


def test_secrets_are_scrubbed_before_storage(repos, make_transcript, tmp_path, monkeypatch):
    """Nothing reaches SQLite unscrubbed. This is the Phase 0 gate."""
    monkeypatch.setattr(
        "claude_brain.core.scrub.load_deny_literals", lambda path=None: (FAKE_SECRET,)
    )
    workdir = tmp_path / "Demo"
    workdir.mkdir()
    path = make_transcript(cwd=str(workdir), prompts=(f"the root password is {FAKE_SECRET}",))

    I.ingest_transcript(repos, path, write_vault=False)

    stored = repos.sessions.find_by_id("11111111-2222-3333-4444-555555555555")
    assert FAKE_SECRET not in (stored.summary or "")


def test_reingest_is_skipped(repos, make_transcript, tmp_path):
    workdir = tmp_path / "Demo"
    workdir.mkdir()
    path = make_transcript(cwd=str(workdir))

    assert I.ingest_transcript(repos, path, write_vault=False)[0] == "ingested"
    assert I.ingest_transcript(repos, path, write_vault=False)[0] == "skipped"


def test_force_reingests_without_duplicating_files(repos, make_transcript, tmp_path):
    workdir = tmp_path / "Demo"
    workdir.mkdir()
    path = make_transcript(cwd=str(workdir), edits=(r"C:\x.py",))

    I.ingest_transcript(repos, path, write_vault=False)
    I.ingest_transcript(repos, path, force=True, write_vault=False)

    files = repos.session_files.find_by_session("11111111-2222-3333-4444-555555555555")
    assert len(files) == 1, f"duplicate session_files rows: {len(files)}"


def test_ingest_touches_project_last_scanned(repos, make_transcript, tmp_path):
    """`touch()` had zero call sites, so last_scanned was NULL on 15 of 17 rows."""
    workdir = tmp_path / "Demo"
    workdir.mkdir()
    project = repos.projects.upsert("Demo", str(workdir))
    assert project.last_scanned is None

    I.ingest_transcript(repos, make_transcript(cwd=str(workdir)), write_vault=False)

    assert repos.projects.find_by_id(project.project_id).last_scanned is not None


def test_empty_transcript_is_not_stored(repos, make_transcript, tmp_path):
    path = make_transcript(prompts=(), timestamps=("",))
    path.write_text("", encoding="utf-8")
    assert I.ingest_transcript(repos, path, write_vault=False)[0] == "empty"


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------


def test_sweep_excludes_sidechains(repos, make_transcript, tmp_path):
    workdir = tmp_path / "Demo"
    workdir.mkdir()
    make_transcript("aaaa1111-0000-0000-0000-000000000000", cwd=str(workdir))
    make_transcript("agent-deadbeef", cwd=str(workdir), sidechain=True)

    result = I.ingest_all(repos, root=tmp_path / "projects", write_vault=False)

    assert result.scanned == 1
    assert result.ingested == 1
    assert repos.sessions.count() == 1


def test_sweep_is_idempotent(repos, make_transcript, tmp_path):
    workdir = tmp_path / "Demo"
    workdir.mkdir()
    make_transcript(cwd=str(workdir))
    root = tmp_path / "projects"

    first = I.ingest_all(repos, root=root, write_vault=False)
    second = I.ingest_all(repos, root=root, write_vault=False)

    assert first.ingested == 1
    assert second.ingested == 0
    assert second.skipped == 1
    assert repos.sessions.count() == 1


def test_sweep_survives_a_broken_transcript(repos, make_transcript, tmp_path, monkeypatch):
    """One bad file must not abort the sweep."""
    workdir = tmp_path / "Demo"
    workdir.mkdir()
    make_transcript("aaaa1111-0000-0000-0000-000000000000", cwd=str(workdir))
    make_transcript("bbbb2222-0000-0000-0000-000000000000", cwd=str(workdir))

    real = I.read_transcript
    calls = {"n": 0}

    def flaky(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real(path)

    monkeypatch.setattr(I, "read_transcript", flaky)
    result = I.ingest_all(repos, root=tmp_path / "projects", write_vault=False)

    assert result.failed == 1
    assert result.ingested == 1
    assert any("boom" in m for m in result.messages)


def test_sweep_counts_new_project_registrations(repos, make_transcript, tmp_path):
    for name in ("One", "Two"):
        directory = tmp_path / name
        directory.mkdir()
        make_transcript(
            f"{name}-0000-0000-0000-000000000000", cwd=str(directory), subdir=f"C--{name}"
        )

    result = I.ingest_all(repos, root=tmp_path / "projects", write_vault=False)

    assert result.projects_registered == 2
    assert len(repos.projects.find_all()) == 2


def test_relink_uses_cwd_not_the_slug(repos, make_transcript, tmp_path):
    workdir = tmp_path / "Demo"
    workdir.mkdir()
    path = make_transcript(cwd=str(workdir))

    # Ingest with auto-registration suppressed so the row lands unlinked.
    from claude_brain.core.transcript import read_transcript

    session, _ = I.build_session(read_transcript(path), None)
    repos.sessions.create(session)
    assert len(repos.sessions.find_unlinked()) == 1

    repos.projects.upsert("Demo", str(workdir))
    assert I.relink_unlinked(repos) == 1
    assert repos.sessions.find_unlinked() == []


def test_ingest_result_str_is_readable():
    text = str(I.IngestResult(scanned=3, ingested=2, skipped=1))
    assert "scanned=3" in text and "ingested=2" in text


# --------------------------------------------------------------------------
# Summary fallbacks
# --------------------------------------------------------------------------


def test_summary_falls_back_to_ai_title(repos, make_transcript, tmp_path):
    """A slash-command session has no user prose; ai-title is the description."""
    workdir = tmp_path / "Demo"
    workdir.mkdir()
    path = make_transcript(
        cwd=str(workdir),
        prompts=("<command-message>everything-claude-code:deep-research</command-message>",),
        extra_records=({"type": "ai-title", "aiTitle": "Cheapest flights research"},),
    )

    I.ingest_transcript(repos, path, write_vault=False)

    stored = repos.sessions.find_by_id("11111111-2222-3333-4444-555555555555")
    assert stored.summary == "Cheapest flights research"


def test_summary_fallback_order_prefers_ai_title(repos, make_transcript, tmp_path):
    workdir = tmp_path / "Demo"
    workdir.mkdir()
    path = make_transcript(
        cwd=str(workdir),
        prompts=(),
        extra_records=(
            {"type": "ai-title", "aiTitle": "the title"},
            {"type": "agent-name", "agentName": "the-agent"},
            {"type": "last-prompt", "lastPrompt": "the prompt"},
        ),
    )

    I.ingest_transcript(repos, path, write_vault=False)

    stored = repos.sessions.find_by_id("11111111-2222-3333-4444-555555555555")
    assert stored.summary == "the title"


def test_real_prompts_win_over_ai_title(repos, make_transcript, tmp_path):
    workdir = tmp_path / "Demo"
    workdir.mkdir()
    path = make_transcript(
        cwd=str(workdir),
        prompts=("what I actually asked",),
        extra_records=({"type": "ai-title", "aiTitle": "generated title"},),
    )

    I.ingest_transcript(repos, path, write_vault=False)

    stored = repos.sessions.find_by_id("11111111-2222-3333-4444-555555555555")
    assert stored.summary == "what I actually asked"


def test_the_default_denylist_is_empty_because_capture_is_opt_out(repos):
    """%TEMP% was tried as a default and removed: it also excluded every
    legitimate scratch workspace, including this suite's own tmp_path."""
    from claude_brain.core.ingest import default_denylist, load_denylist

    assert default_denylist() == ()
    repos.settings.set("capture_denylist", "")
    assert load_denylist(repos) == ()


def test_an_explicit_denylist_is_the_whole_list(repos, tmp_path):
    from claude_brain.core.ingest import is_denied, load_denylist

    repos.settings.set("capture_denylist", str(tmp_path / "Private"))
    denylist = load_denylist(repos)

    assert is_denied(str(tmp_path / "Private" / "x"), denylist)
    assert not is_denied(str(tmp_path / "Public" / "x"), denylist)


def test_a_denylisted_directory_is_never_registered(repos, make_transcript, tmp_path):
    private = tmp_path / "Private"
    private.mkdir()
    repos.settings.set("capture_denylist", str(private))
    path = make_transcript("aaaaaaaa-0000-0000-0000-000000000000", cwd=str(private))

    result = I.ingest_all(repos, paths=[path], write_vault=False)

    assert result.projects_registered == 0
    assert repos.projects.find_by_path(str(private)) is None


# --------------------------------------------------------------------------
# Vault artifacts. The old launch ingest wrote none of these.
# --------------------------------------------------------------------------


def test_a_sweep_writes_the_vault_artifacts(repos, make_transcript, tmp_path):
    workdir = tmp_path / "Demo"
    workdir.mkdir()
    path = make_transcript(cwd=str(workdir), prompts=("do the thing",), edits=("C:/a.py",))

    result = I.ingest_all(repos, paths=[path], write_vault=True)

    assert result.notes_written == 1
    assert (workdir / "CLAUDE.md").is_file()
    assert (workdir / "Sessions" / "Sessions.base").is_file()
    assert list((workdir / "Sessions").glob("*.md")), "a session note was written"


def test_sweeping_twice_does_not_rewrite_the_note(repos, make_transcript, tmp_path):
    """Regression: a trailing-newline difference made every pass look like a
    change, which would churn an Obsidian-synced vault on every session."""
    workdir = tmp_path / "Demo"
    workdir.mkdir()
    path = make_transcript(cwd=str(workdir), prompts=("do the thing",))

    I.ingest_all(repos, paths=[path], write_vault=True)
    note = next((workdir / "Sessions").glob("*.md"))
    before = note.read_text(encoding="utf-8")

    I.ingest_all(repos, paths=[path], force=True, write_vault=True)

    assert note.read_text(encoding="utf-8") == before


def test_a_hand_edited_note_survives_a_sweep(repos, make_transcript, tmp_path):
    workdir = tmp_path / "Demo"
    workdir.mkdir()
    path = make_transcript(cwd=str(workdir), prompts=("do the thing",))
    I.ingest_all(repos, paths=[path], write_vault=True)

    note = next((workdir / "Sessions").glob("*.md"))
    note.write_text(
        note.read_text(encoding="utf-8").replace("do the thing", "WHAT I ACTUALLY MEANT"),
        encoding="utf-8",
    )

    result = I.ingest_all(repos, paths=[path], force=True, write_vault=True)

    assert "WHAT I ACTUALLY MEANT" in note.read_text(encoding="utf-8")
    assert result.notes_written == 0, "a skipped note is not a written note"
