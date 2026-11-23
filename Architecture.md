# Claude_Brain — Architecture

Claude Code forgets everything when a session ends. Claude_Brain captures each
session on the way out, stores it locally, and feeds the relevant part back on
the way in. Nothing leaves the machine.

## Components

### Capture

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `hooks/brain_hook.py` | 184 | The only entry point Claude Code calls. Two modes: `context` and `sweep`. Always exits 0. |
| `core/hooks.py` | 268 | Registers and removes those hooks in `~/.claude/settings.json`. |
| `core/transcript.py` | 368 | Tolerant reader for Claude Code's JSONL. Skips sidechains, follows compaction boundaries. |
| `core/digests.py` | 424 | Parses the plugin's per-project-per-day digest files, both historical shapes. |
| `core/ingest.py` | 415 | Orchestrates one sweep: discover, parse, scrub, persist, write notes, reindex. |
| `core/scrub.py` | 310 | Removes credentials before anything is stored. Structural patterns plus a machine-local literal denylist. |

### Storage

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `db/connection.py` | 104 | WAL, pragma ordering, online-backup API. |
| `db/migrations/` | 6 files | Forward-only numbered SQL, applied by `runner.py`. |
| `db/repositories.py` | 471 | One repository per table; all SQL lives here. |
| `core/repair.py` | 349 | Finds and reverses bad rows — unlinked sessions, container-directory projects, legacy summaries. |

Tables: `projects`, `vaults`, `sessions`, `session_files`, `cached_files`,
`day_digests`, `consolidations`, `imported_paths`, `app_settings`, and the
`search_index` FTS5 table. The store is a single SQLite file under
`%LOCALAPPDATA%\ClaudeBrain\`.

### Retrieval

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `core/search.py` | 378 | FTS5 index over sessions, digests **and** curated vault notes; `search` and `recall`. |
| `core/context.py` | 197 | Builds the ≤1800-char block `SessionStart` injects, keyed on `cwd`. |
| `cli.py` | 591 | Ten commands. The interface the skills, the hooks and a human all use. |
| `skills/brain-recall`, `skills/brain-save` | — | Make the store reachable *during* a session, not only after one. |

### Vault integration

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `core/vault_writer.py` | 557 | Writes managed regions into Obsidian notes without ever losing hand-written text. |
| `core/vault_manager.py` | 195 | Vault discovery and registration. |
| `core/curate.py` | 233 | Spawns an isolated `claude -p` to turn a raw session into a readable note. |
| `resources/vault_templates/` | 4 files | Created once in a new vault, never overwritten. |

## Data Flow

```
Claude Code session
        |
        |  writes ~/.claude/projects/<slug>/<session-id>.jsonl
        |  writes the plugin's per-day digest files
        v
  SessionEnd / PreCompact  ---> brain_hook.py sweep (async, non-blocking)
                                        |
                                        v
                                 core/ingest.py
                      transcript.py + digests.py  (parse)
                                 scrub.py         (redact)
                                 repositories     (persist)
                                 vault_writer.py  (managed regions in the vault)
                                 search.py        (reindex)
                                        |
                                        v
                          %LOCALAPPDATA%\ClaudeBrain\brain.db
                                        |
  SessionStart  <--- brain_hook.py context (sync, 10 s cap) <--- core/context.py
        |
        |  hookSpecificOutput.additionalContext
        v
  the next session starts already knowing
        |
        |  mid-session, on demand
        v
  brain-recall / brain-save skills ---> brain CLI ---> the same store
```

Three properties make this survive Claude Code's hook behaviour:

1. **Ingest is idempotent and re-runnable.** Hooks are opportunistic triggers,
   not a contract. `SessionEnd` cannot block, shares a 1.5 s budget with every
   other hook, and misses roughly half of headless sessions
   ([#98075](https://github.com/anthropics/claude-code/issues/98075)). A missed
   sweep costs nothing because the next `SessionStart` catches up.
2. **Capture never blocks a session.** The sweeps are `async`. Only the context
   hook is synchronous — it has to be, because an async hook's stdout is never
   injected — and it is capped at 10 s.
3. **The hook always exits 0.** A crashing capture hook that broke session
   startup would be worse than no capture at all.

## Key Decisions

See `Decisions.md` for the full log with rationale. The four that shape
everything else:

- **`cwd` is the project key**, not Claude Code's directory slug. The slug
  flattens separators and is ambiguous; `cwd` is exact.
- **Hooks are registered in `~/.claude/settings.json`**, never in a plugin,
  because plugin-scoped `SessionEnd` never fires
  ([#33458](https://github.com/anthropics/claude-code/issues/33458)).
- **Vault writes are confined to `%% brain:begin hash=… %%` regions** and skip
  themselves when the content hash shows a human edited the block.
- **Everything is scrubbed on the way in.** This project found a server root
  password pasted into a chat transcript; redaction is not optional.
