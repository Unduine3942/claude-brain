# Claude_Brain — Decision Log

Decisions whose reasoning is not visible in the code. Each row names what was
chosen *and what was rejected*, because the rejected option is usually the one
someone will try again later.

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-10-05 | Hooks in `~/.claude/settings.json`, not a plugin | Plugin-scoped `SessionEnd` never fires ([#33458](https://github.com/anthropics/claude-code/issues/33458)). |
| 2026-10-05 | Hooks are opportunistic; ingest is idempotent | `SessionEnd` cannot block, shares a 1.5 s budget, and misses ~50% of headless sessions ([#98075](https://github.com/anthropics/claude-code/issues/98075)). Correctness cannot depend on it. |
| 2026-10-05 | Exec form (`command` + `args`), `pythonw.exe` | Git Bash eats unquoted backslashes in shell form ([#21878](https://github.com/anthropics/claude-code/issues/21878)); `pythonw` means no console flash. |
| 2026-10-05 | Sweeps `async`, context hook synchronous | An async hook's stdout is never injected, so an async context hook would contribute nothing. |
| 2026-10-05 | `cwd` as the project key, not the directory slug | The slug flattens `\`, `/` and `.` to `-`, so it cannot be reversed to a path. 865 of 882 sessions were unlinked under the slug; 11 under `cwd`, all from deleted directories. |
| 2026-10-05 | Tolerant JSONL reader, **not** vendored Pydantic models | See *Prior art* below. |
| 2026-10-05 | Forward-only numbered SQL migrations | `CREATE TABLE IF NOT EXISTS` at startup cannot add a column. Six migrations so far, each independently replayable. |
| 2026-10-05 | `synchronous` before `journal_mode` | Setting `synchronous` after `journal_mode=WAL` silently fails to apply. |
| 2026-10-05 | Plain FTS5 table, not contentless | `snippet()` and `highlight()` return NULL on a contentless table, and the excerpt is most of the value of a search result. |
| 2026-10-05 | Index curated vault notes alongside sessions | A session row holds prompts and filenames; the *conclusion* is in a note. `recall "prefill"` returned nothing until notes were indexed. |
| 2026-10-05 | Full index rebuild, not triggers | A rebuild of the real corpus takes single-digit ms and cannot go stale. Triggers would have to reassemble a body spanning three tables on every row change. |
| 2026-10-05 | Every search term is quoted before `MATCH` | FTS5 treats `"`, `*`, `(`, `:`, `^`, `-` and `AND`/`OR`/`NOT`/`NEAR` as syntax. |
| 2026-10-05 | Vault writes confined to hash-stamped managed regions | See *Vault safety* below. |
| 2026-10-05 | Line-level frontmatter merge, not parse-and-dump | The package is stdlib-only; a partial YAML round trip corrupts nested maps and block scalars. |
| 2026-10-05 | Curation via `claude -p`, guarded by an env var — **no** `--bare` | See *Curation* below. |
| 2026-10-05 | Skills installed to user scope, description length validated | A skill with no `description` is omitted from the model-visible list, so it looks installed and is never chosen. Project scope would only fire inside this repo. |
| 2026-10-05 | Capture everything, opt out per project | A denylist the user maintains beats an allowlist they forget to extend. `default_denylist()` is deliberately empty. |
| 2026-10-05 | Public tool repo, private vaults | The tool is worth sharing; six months of transcripts are not. |
| 2026-10-05 | `windows-latest` is the primary CI leg | Everything platform-specific here is Windows-specific: `pythonw.exe`, `%LOCALAPPDATA%`, a cp1252 console. A Linux-only matrix would pass while the only platform it runs on was broken. |

## Prior art

[`daaain/claude-code-log`](https://github.com/daaain/claude-code-log) (MIT) was
the reference for Claude Code's JSONL schema — compaction boundaries,
sidechains, hook attachments, a dozen easily-missed record types.

Its Pydantic models were **considered and not vendored**. Ingest needs about
eight fields; vendoring would add a runtime dependency and 80 KB to re-sync by
hand; and an exhaustive schema *rejects* record types Claude Code adds later,
which is the opposite of what a capture tool wants. `core/transcript.py` is a
tolerant reader instead, validated against a real 8.7 MB transcript containing
14 record types and both `sessionId` casings.

## Vault safety

The vault holds hand-written knowledge. A writer that can clobber it is worse
than no writer, so three rules hold:

1. Writes happen only between `%% brain:begin hash=… %%` and `%% brain:end %%`.
2. The hash is of the region body. If it does not match, a human edited the
   block and the write is **skipped**, not merged.
3. A begin marker with no end marker aborts the write for that file.

Measured against 20 real vaults / 140 markdown files: one sweep changed one
managed block, created 4 new files, and removed or clobbered nothing. Repeated
forced sweeps settle to zero changes.

The managed block carries **no wall-clock timestamp** — an `auto-updated HH:MM`
line churned `CLAUDE.md` on every sweep, which in a synced vault is a conflict
generator.

## Curation

A raw session is unreadable; a curated note is the thing worth keeping. Curation
runs `claude -p` in a subprocess, which means guarding against a session that
curates itself forever.

- `CLAUDE_BRAIN_CURATING=1` is set in the child's environment and checked by
  `brain_hook.py` before any dispatch. This is the primary barrier.
- `stdin=subprocess.DEVNULL`. Inheriting stdin made the first real call hang
  3 s and exit 1.
- `--bare` was tried and **removed**: it skips keychain reads, so the call
  exits 1 with *"Not logged in · Please run /login"*.
- The prompt is scrubbed and capped at 24,000 characters.

Verified: a curation call produced the hook log line *"sweep: refusing to run
inside a curation call"* and created no transcript.

## Scrubbing calibration

The first scrubber over-redacted real data: `[A-Za-z0-9+/=]{20,}` ate 32 paths
and URLs (`/mnt/c/Users/jdoe/Desktop/Scripting` 104 times) plus 3 CamelCase
class names, because `/` and `=` made a whole path one token.

Now: alphanumeric runs only, a digit **and** a letter required, pure hex
rejected, and a Shannon-entropy floor. `SELFTEST PASS (Python 3.14.0)` and
`password protect the option` were both false positives and both now pass
clean. Operator-specific literals load from
`%LOCALAPPDATA%\ClaudeBrain\deny_literals.txt`, never from repo source.
