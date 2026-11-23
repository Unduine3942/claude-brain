# Claude_Brain — TODO

## Blocked on a human


- [ ] **Open a vault and look at `Sessions.base`.** The schema is verified
      against the installed Obsidian 1.12.7 bundle's own parser, and Bases needs
      1.9+, so it should load — but nobody has seen it render.
- [ ] **Watch the first CI run.** `.github/workflows/ci.yml` has never executed;
      every step has been run locally on Windows instead. The
      `windows-latest` / py3.10 leg is the one to read first — it exercises the
      declared floor.

## Next

- [ ] Add `UserPromptSubmit` as a fourth capture trigger. It fires reliably and
      often; the question is whether per-prompt sweeps are worth the I/O over
      `SessionEnd` + `PreCompact` + `SessionStart`.
- [ ] Expose the store over MCP as well as the CLI. The skills work, but an MCP
      server would make `recall` available to clients that have no shell.
- [ ] Retire `imported_paths`. It records that a path was *ever* seen, which
      permanently blocks re-import even after the row it created is deleted.
      `cached_files` (mtime + size) supersedes it; 68 legacy rows remain.
- [ ] Cross-project recall in the `SessionStart` block. Context is deliberately
      scoped to `cwd`; a second section for "related work elsewhere" would need
      a relevance signal better than a name match.
- [ ] Semantic search to complement FTS5. Only worth it if a local embedding
      model can be run without adding a runtime dependency — the stdlib-only
      constraint is load-bearing for a tool that installs into a hook.

## Known gaps

- [ ] 12 sessions remain unlinked (2026-10-05), all from directories that no
      longer exist. `brain repair` reports them and leaves them alone, which is
      correct — there is no path left to link them to — but it means the count
      never reaches zero.
- [ ] The tkinter dashboard has no view of digests or search; it predates both.
- [ ] `core/consolidator.py` and `core/summarizer.py` are pre-rewrite code that
      still works but no longer matches how ingest is structured.
