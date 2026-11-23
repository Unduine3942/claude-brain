"""The ``brain`` command line.

Everything the GUI could do, without needing a display — which is the whole
point: the hooks, the skills and CI all reach the store through this entry
point, and none of them can open a tkinter window.

Exit codes are meaningful, because hooks and CI read them: ``0`` success, ``1``
nothing found or a recoverable problem, ``2`` a usage error (argparse's own
convention), ``3`` the store is unusable.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from claude_brain import __version__

log = logging.getLogger("claude_brain.cli")

__all__ = ["build_parser", "main"]

EXIT_OK = 0
EXIT_EMPTY = 1
EXIT_USAGE = 2
EXIT_BROKEN = 3

_MAX_EXCERPT = 150


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------


def _out(text: str = "") -> None:
    """Print without dying on a Windows console that cannot encode the text.

    cp1252 is still the default on this platform, so an em dash or an arrow in a
    stored summary raises UnicodeEncodeError and takes the whole command with
    it. Losing a glyph is always better than losing the output.
    """
    stream = sys.stdout
    try:
        print(text, file=stream)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        print(text.encode(encoding, errors="replace").decode(encoding), file=stream)


def _emit(payload: object, *, as_json: bool) -> None:
    if as_json:
        _out(json.dumps(payload, indent=2, default=str))


def _hit_lines(hit, *, verbose: bool) -> list[str]:
    head = f"  [{hit.kind:7}] {(hit.project or '-')[:20]:<22} {hit.date or '-':<10}  {hit.title}"
    lines = [head]
    if verbose and hit.excerpt:
        lines.append(f"            {hit.excerpt[:_MAX_EXCERPT]}")
    return lines


# --------------------------------------------------------------------------
# Store access
# --------------------------------------------------------------------------


def _open(*, read_only: bool = False):
    """Open the store, running migrations. Returns ``(repos, conn)``.

    Read-only callers get a connection that cannot block a concurrent writer,
    which matters because a hook may be sweeping while a skill is searching.
    """
    from claude_brain.db import migrations
    from claude_brain.db.connection import get_connection, get_readonly_connection
    from claude_brain.db.repos import Repos

    if read_only:
        conn = get_readonly_connection()
        return Repos(conn), conn

    conn = get_connection()
    migrations.run(conn)
    return Repos(conn), conn


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_ingest(args) -> int:
    from claude_brain.core.ingest import ingest_all, relink_unlinked

    repos, conn = _open()
    try:
        result = ingest_all(
            repos,
            force=args.force,
            write_vault=not args.no_vault,
        )
        relinked = relink_unlinked(repos) if args.relink else 0
    finally:
        conn.close()

    if args.json:
        _emit({"ingest": result.__dict__, "relinked": relinked}, as_json=True)
        return EXIT_OK

    _out(str(result))
    if relinked:
        _out(f"relinked {relinked} previously unlinked session(s)")
    for message in result.messages:
        _out(f"  {message}")
    return EXIT_OK


def cmd_backfill(args) -> int:
    from claude_brain.core.digests import backfill_digests
    from claude_brain.core.search import reindex

    repos, conn = _open()
    try:
        result = backfill_digests(repos, force=args.force)
        indexed = reindex(conn) if (result.imported or result.updated) else 0
    finally:
        conn.close()

    if args.json:
        _emit({"backfill": result.__dict__, "indexed": indexed}, as_json=True)
        return EXIT_OK

    _out(str(result))
    for message in result.errors:
        _out(f"  error: {message}")
    return EXIT_OK


def cmd_search(args) -> int:
    from claude_brain.core.search import search

    _, conn = _open(read_only=True)
    try:
        hits = search(
            conn,
            args.query,
            project=args.project,
            kind=args.kind,
            limit=args.limit,
        )
    finally:
        conn.close()

    if args.json:
        _emit([hit.__dict__ for hit in hits], as_json=True)
        return EXIT_OK if hits else EXIT_EMPTY

    if not hits:
        _out(f"nothing found for {args.query!r}")
        return EXIT_EMPTY
    _out(f"{len(hits)} result(s) for {args.query!r}:")
    for hit in hits:
        for line in _hit_lines(hit, verbose=not args.quiet):
            _out(line)
    return EXIT_OK


def cmd_recall(args) -> int:
    """Decision-oriented lookup. This is what the ``brain-recall`` skill runs."""
    from claude_brain.core.search import recall

    _, conn = _open(read_only=True)
    try:
        hits = recall(conn, args.topic, project=args.project, limit=args.limit)
    finally:
        conn.close()

    if args.json:
        _emit([hit.__dict__ for hit in hits], as_json=True)
        return EXIT_OK if hits else EXIT_EMPTY

    if not hits:
        scope = f" in {args.project}" if args.project else ""
        _out(f"no prior context for {args.topic!r}{scope}")
        return EXIT_EMPTY

    _out(f"Prior context for {args.topic!r}:")
    for hit in hits:
        _out("")
        _out(f"  {hit.date or '(undated)'} - {hit.project or 'unlinked'} [{hit.kind}]")
        _out(f"  {hit.title}")
        if hit.excerpt:
            _out(f"      {hit.excerpt[:300]}")
    return EXIT_OK


def cmd_save(args) -> int:
    """Write or refresh vault notes, optionally with curated prose.

    ``--curate`` is the repeatable form of writing a session note by hand: it
    spawns an isolated `claude -p` call to write up what was decided, which is
    the part a prompt list cannot carry.
    """
    from claude_brain.core.ingest import ingest_all, ingest_transcript
    from claude_brain.core.transcript import discover_transcripts

    repos, conn = _open()
    try:
        target: Path | None = None
        if args.path:
            target = Path(args.path)
            if not target.is_file():
                _out(f"no such transcript: {target}")
                return EXIT_EMPTY
        elif args.latest:
            transcripts = discover_transcripts()
            if not transcripts:
                _out("no transcripts found")
                return EXIT_EMPTY
            target = transcripts[0]  # discover_transcripts is newest-first

        if target is None:
            result = ingest_all(repos, force=args.force, write_vault=True)
            _out(str(result))
            if args.curate:
                _out("--curate works on one session; pass --latest or --path")
            return EXIT_OK

        status, _, _ = ingest_transcript(repos, target, force=True, write_vault=True)
        _out(f"{target.name}: {status}")
        if not args.curate:
            return EXIT_OK
        return _curate_one(repos, target)
    finally:
        conn.close()


def _curate_one(repos, transcript: Path) -> int:
    """Replace one session note's managed body with curated prose."""
    from claude_brain.core.curate import curate_session
    from claude_brain.core.transcript import read_transcript
    from claude_brain.core.vault_writer import write_session_note

    summary = read_transcript(transcript)
    session = repos.sessions.find_by_id(summary.session_id)
    if session is None:
        _out("that session is not in the store")
        return EXIT_EMPTY

    project = repos.projects.find_by_id(session.project_id) if session.project_id else None
    if project is None:
        _out("that session has no project, so there is no vault to write to")
        return EXIT_EMPTY

    files = repos.session_files.find_by_session(session.session_id)
    _out("curating (an isolated `claude -p` call; this takes a moment)...")
    curated = curate_session(session, files)

    if not curated.ok:
        _out(f"curation {curated.status}: {curated.detail}")
        _out("the structured note is already written and stands on its own.")
        return EXIT_EMPTY

    note = write_session_note(project.path, project.name, session, files, curated=curated.text)
    _out(f"{note.status}: {note.path}")
    return EXIT_OK if note.changed else EXIT_EMPTY


def cmd_reindex(args) -> int:
    from claude_brain.core.search import reindex

    _, conn = _open()
    try:
        count = reindex(conn)
    finally:
        conn.close()
    _out(f"indexed {count} row(s)")
    return EXIT_OK


def cmd_repair(args) -> int:
    from claude_brain.config import APP_DIR
    from claude_brain.core.repair import apply_repair, plan_repair

    _, conn = _open()
    try:
        plan = plan_repair(conn)
        _out(plan.describe())
        if not plan.applicable:
            return EXIT_OK
        if not args.apply:
            _out("")
            _out("dry run. re-run with --apply to make these changes.")
            return EXIT_OK

        import time

        backup = APP_DIR / "backups" / f"pre_repair_{time.strftime('%Y%m%d_%H%M%S')}.db"
        report = apply_repair(conn, plan=plan, backup_to=backup)
        _out("")
        _out(str(report))
        return EXIT_OK
    finally:
        conn.close()


def cmd_hooks(args) -> int:
    """Register, remove or report the capture hooks."""
    from claude_brain.core import hooks

    if args.uninstall:
        removed = hooks.uninstall_hooks()
        _out(f"removed {removed} hook block(s)")
        return EXIT_OK

    if args.install:
        state = hooks.status()
        if not state.script_exists:
            _out(f"error: hook script not found at {state.script}")
            return EXIT_BROKEN
        _, after = hooks.install_hooks()
        _out("capture hooks registered:")
        _out(after.describe())
        _out("")
        _out("Start a new session in a registered project to see it take effect.")
        return EXIT_OK if after.ok else EXIT_EMPTY

    state = hooks.status()
    _out(state.describe())
    if not state.ok:
        _out("")
        _out("run `brain hooks --install` to register them")
        return EXIT_EMPTY
    return EXIT_OK


def cmd_skills(args) -> int:
    """Install, remove or report the brain skills."""
    from claude_brain.core import skills

    if args.uninstall:
        _out(f"removed {skills.uninstall_skills()} skill(s)")
        return EXIT_OK

    if args.install:
        installed, problems = skills.install_skills()
        for name in installed:
            _out(f"installed {name}")
        for problem in problems:
            _out(f"  problem: {problem}")
        state = skills.status()
        _out("")
        _out(state.describe())
        return EXIT_OK if state.ok else EXIT_EMPTY

    state = skills.status()
    _out(state.describe())
    if not state.ok:
        _out("")
        _out("run `brain skills --install` to install them")
        return EXIT_EMPTY
    return EXIT_OK


def cmd_doctor(args) -> int:
    """Report on the health of the store, the vaults and the hooks."""
    from claude_brain.config import DB_PATH
    from claude_brain.core.digests import discover_digests
    from claude_brain.core.repair import plan_repair
    from claude_brain.core.transcript import discover_transcripts
    from claude_brain.db.migrations import runner

    problems: list[str] = []
    _, conn = _open()
    try:
        counts = {
            name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in (
                "projects",
                "sessions",
                "session_files",
                "day_digests",
                "cached_files",
                "search_index",
            )
        }
        unlinked_sessions = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE project_id IS NULL"
        ).fetchone()[0]
        unlinked_digests = conn.execute(
            "SELECT COUNT(*) FROM day_digests WHERE project_id IS NULL"
        ).fetchone()[0]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        applied = sorted(runner.applied(conn))
        drift = runner.verify(conn)
        plan = plan_repair(conn)
        transcripts = len(discover_transcripts())
        digests = len(discover_digests())

        _out(f"claude-brain {__version__}")
        _out(f"database   {DB_PATH}")
        _out(f"           integrity_check: {integrity}")
        _out(f"migrations {applied}")
        _out("")
        _out("rows")
        for name, count in counts.items():
            _out(f"  {name:16} {count:>6}")
        _out(f"  {'unlinked sessions':16} {unlinked_sessions:>6}")
        _out(f"  {'unlinked digests':16} {unlinked_digests:>6}")
        _out("")
        _out("on disk")
        _out(f"  {'transcripts':16} {transcripts:>6}")
        _out(f"  {'digest files':16} {digests:>6}")

        if integrity != "ok":
            problems.append(f"integrity_check returned {integrity!r}")
        if drift:
            problems.extend(drift)
        if plan.applicable:
            problems.append(f"{plan.total_rows} row(s) need repair - run `brain repair`")
        if counts["search_index"] == 0 and counts["sessions"]:
            problems.append("search index is empty - run `brain reindex`")

        problems.extend(_hook_problems())
        problems.extend(_skill_problems())

        _out("")
        if problems:
            _out(f"{len(problems)} problem(s):")
            for problem in problems:
                _out(f"  - {problem}")
        else:
            _out("no problems found")
    finally:
        conn.close()

    return EXIT_OK if not problems else EXIT_EMPTY


def _hook_problems() -> list[str]:
    """Check that the capture hooks are actually registered.

    Automatic capture is the whole premise, and a hook that is missing fails
    silently and invisibly - exactly the state this project spent two months in.
    """
    from claude_brain.core import hooks

    return list(hooks.status().problems)


def _skill_problems() -> list[str]:
    """Check the skills are installed and model-visible.

    A skill without a description is omitted from the list the model sees,
    so it can be present on disk and still never be chosen.
    """
    from claude_brain.core import skills

    return list(skills.status().problems)


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brain",
        description="Durable memory for Claude Code sessions.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="log what is happening to stderr",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_ingest = sub.add_parser("ingest", help="sweep session transcripts into the store")
    p_ingest.add_argument("--force", action="store_true", help="re-read unchanged files")
    p_ingest.add_argument("--no-vault", action="store_true", help="skip writing vault notes")
    p_ingest.add_argument(
        "--relink",
        action="store_true",
        help="re-attempt project resolution for unlinked sessions",
    )
    p_ingest.add_argument("--json", action="store_true", help="machine-readable output")
    p_ingest.set_defaults(func=cmd_ingest)

    p_backfill = sub.add_parser("backfill", help="import ~/.claude/sessions/*.tmp digests")
    p_backfill.add_argument("--force", action="store_true", help="re-read unchanged files")
    p_backfill.add_argument("--json", action="store_true", help="machine-readable output")
    p_backfill.set_defaults(func=cmd_backfill)

    p_search = sub.add_parser("search", help="full-text search the store")
    p_search.add_argument("query", help="free text; wrap in quotes for a phrase")
    p_search.add_argument("--project", help="limit to one project name or path")
    p_search.add_argument(
        "--kind",
        choices=("session", "digest", "note"),
        help="limit to one kind of record",
    )
    p_search.add_argument("--limit", type=int, default=20)
    p_search.add_argument("-q", "--quiet", action="store_true", help="titles only")
    p_search.add_argument("--json", action="store_true", help="machine-readable output")
    p_search.set_defaults(func=cmd_search)

    p_recall = sub.add_parser("recall", help="what did we decide about X?")
    p_recall.add_argument("topic")
    p_recall.add_argument("--project", help="limit to one project name or path")
    p_recall.add_argument("--limit", type=int, default=6)
    p_recall.add_argument("--json", action="store_true", help="machine-readable output")
    p_recall.set_defaults(func=cmd_recall)

    p_save = sub.add_parser("save", help="write or refresh vault notes")
    group = p_save.add_mutually_exclusive_group()
    group.add_argument("--path", help="one transcript to save")
    group.add_argument("--latest", action="store_true", help="the most recent transcript")
    p_save.add_argument("--force", action="store_true", help="re-read unchanged files")
    p_save.add_argument(
        "--curate",
        action="store_true",
        help="also write prose via an isolated `claude -p` call",
    )
    p_save.set_defaults(func=cmd_save)

    p_reindex = sub.add_parser("reindex", help="rebuild the full-text search index")
    p_reindex.set_defaults(func=cmd_reindex)

    p_repair = sub.add_parser("repair", help="report, and optionally fix, bad rows")
    p_repair.add_argument(
        "--apply",
        action="store_true",
        help="make the changes (a backup is taken first)",
    )
    p_repair.set_defaults(func=cmd_repair)

    p_hooks = sub.add_parser("hooks", help="register or remove the capture hooks")
    hook_group = p_hooks.add_mutually_exclusive_group()
    hook_group.add_argument(
        "--install", action="store_true", help="register them in ~/.claude/settings.json"
    )
    hook_group.add_argument("--uninstall", action="store_true", help="remove only our entries")
    p_hooks.set_defaults(func=cmd_hooks)

    p_skills = sub.add_parser("skills", help="install or remove the brain skills")
    skill_group = p_skills.add_mutually_exclusive_group()
    skill_group.add_argument(
        "--install", action="store_true", help="copy them into ~/.claude/skills"
    )
    skill_group.add_argument("--uninstall", action="store_true", help="remove them")
    p_skills.set_defaults(func=cmd_skills)

    p_doctor = sub.add_parser("doctor", help="check the store, vaults and hooks")
    p_doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_USAGE

    try:
        return args.func(args)
    except KeyboardInterrupt:
        return EXIT_USAGE
    except Exception as exc:
        log.exception("command failed")
        _out(f"error: {exc}")
        return EXIT_BROKEN


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
