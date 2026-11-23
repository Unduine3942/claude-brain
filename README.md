# Claude Brain

Durable, queryable memory across Claude Code sessions.

Claude Code deletes its own session transcripts (the default retention is 30 days), so the
record of how you arrived at a decision disappears along with it. Claude Brain captures each
session automatically, stores it in SQLite, and writes curated notes into a per-project
Obsidian vault — so the reasoning survives, and Claude can look it up next time.

> **Why this matters, measured:** of the 872 transcripts this tool had records for, **871 no
> longer existed on disk.** The brain was the only surviving copy.

## Status

Under active hardening. See `Sessions/` for design history and `Decisions.md` for the decision log.

## How it works

```
         +- SessionStart  -> reconciliation sweep + inject this project's prior context
hooks ---+- SessionEnd    -> fast-path capture
         +- PreCompact    -> capture before compaction discards detail
                     |
                     v
              one idempotent ingest
                     |
          +----------+----------+
          v                     v
   SQLite + FTS5          Obsidian vault
   (%LOCALAPPDATA%)       per-project notes
          |
          v
    `brain` CLI  --  skills so Claude can query past decisions
```

Hooks are **triggers, not the source of truth**. `SessionEnd` is unreliable: it cannot block,
shares a 1.5 s budget, never fires when plugin-scoped
([#33458](https://github.com/anthropics/claude-code/issues/33458)), and misses roughly half of
headless sessions ([#98075](https://github.com/anthropics/claude-code/issues/98075)). So ingest
is idempotent and re-runnable, and a `SessionStart` sweep picks up whatever the last session
missed.

## Install

```bash
git clone https://github.com/Unduine3942/claude-brain
cd claude-brain
pip install -e ".[dev]"

brain hooks  --install   # register automatic capture in ~/.claude/settings.json
brain skills --install   # let Claude query the store mid-session
brain backfill           # import any historical session digests
brain doctor             # confirm all of the above
```

Requires Python 3.10+. The runtime is stdlib-only; the GUI needs tkinter.

`brain hooks --install` is idempotent, backs up `settings.json`, and leaves
other tools' hooks on the same events alone. `--uninstall` removes only its own
entries.

## Usage

```bash
brain recall "<topic>" --project <name>   # what did we decide about X?
brain search "<query>" [--kind note]      # full-text across sessions, digests, notes
brain save --latest --curate              # write a curated note for this session
brain ingest [--force]                    # run the sweep manually; the hooks call this
brain reindex                             # rebuild the search index
brain repair [--apply]                    # report, then fix, bad rows
brain doctor                              # store, vault, hook and skill health
```

The tkinter dashboard: `python -m claude_brain` (or `launch.bat` on Windows).

### What search covers

| Kind | Source | Weight |
|------|--------|--------|
| `note` | curated vault notes | highest — where conclusions live |
| `session` | one session's prompts, files and tool count | reliable for *what*, thin on *why* |
| `digest` | the plugin's per-project-per-day summaries | breadth and dates |

## Design notes

- **The database lives outside the vault**, in `%LOCALAPPDATA%\ClaudeBrain\`. Vaults are often
  synced, and a sync client rewriting pages beneath an open SQLite file corrupts it without even
  needing a conflict.
- **Secrets are scrubbed before anything is written.** Transcripts routinely contain pasted
  credentials; fail closed rather than storing raw text.
- **Notes use YAML frontmatter and Obsidian Bases**, not Dataview (last release April 2025), and
  never inline `key:: value` fields.
- **Generated regions of a note are fenced** with `%% %%` markers. Anything below the end marker
  is never touched, and a hand-edited region causes that note to be skipped rather than
  overwritten.

## Prior art

[`daaain/claude-code-log`](https://github.com/daaain/claude-code-log) (MIT) was the reference for
Claude Code's JSONL schema. Its Pydantic models were **not** vendored, in the end: ingest needs
about eight fields, vendoring would add a runtime dependency and 80 KB to re-sync, and an
exhaustive schema *rejects* record types Claude Code adds later. `core/transcript.py` is a
tolerant reader instead — validated against a real 8.7 MB transcript with 14 record types and
both `sessionId` casings. See `Decisions.md`.

## License

MIT - see [LICENSE](LICENSE).
