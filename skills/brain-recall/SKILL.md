---
name: brain-recall
description: Search this machine's Claude Code history for prior work on a topic — past decisions, what was tried and rejected, curated session notes and day digests. Use when the user asks "what did we decide about X", "have we done this before", "why is it built this way", "what was the problem with Y", or when about to re-make a decision in a project with history. Also use before proposing an approach in an unfamiliar project, to check it was not already ruled out.
---

# Recall prior work from Claude Brain

Claude Brain is a local store of this machine's Claude Code history: session
records, the daily digests the ECC plugin writes, and the curated notes in each
project's Obsidian vault. It covers work back to 2026-03-30 across ~45 projects,
and it is the **only surviving copy** of most of it — Claude Code deletes
transcripts, and 871 of the 872 it has records for are already gone from disk.

## When to reach for this

- The user asks what was decided, tried, or rejected.
- You are about to propose an approach in a project you have no context for.
- Something looks like it was built oddly and you want to know why before
  changing it.
- The user refers to past work as if you should know it ("the buffering fix",
  "the prefill finding").

## How to use it

Decision-oriented lookup, few rich results. Prefer this:

```bash
brain recall "<topic>" --project <ProjectName>
```

Broader full-text search across every project and record type:

```bash
brain search "<query>" [--project <name>] [--kind session|digest|note] [--limit N]
```

Both accept `--json` when you want to process the output rather than read it.

### Reading the results

Each hit says what kind of record it is, and that tells you how much to trust it:

| Kind | What it is | Weight |
|------|-----------|--------|
| `note` | A curated vault note, often written by a model that read the session, sometimes written by hand | **Highest.** This is where conclusions and reasoning live. |
| `session` | One session's record: its prompts, the files it changed, its tool count | Reliable for *what happened*, thin on *why*. |
| `digest` | A per-project-per-day summary from the ECC plugin | Useful for dates and breadth; its task lists are raw prompts. |

### Query syntax

- Terms are ANDed: `brain recall "qsv transcoding"` wants both words.
- Quote the whole thing for a phrase: `brain search '"quick sync"'`.
- Trailing `*` is a prefix match: `brain search "prefil*"` finds *prefill*.
- Everything else is treated as literal text, so punctuation is safe to type.

## Important

- **Some older summaries are untrustworthy and say so.** Sessions captured
  before 2026-10-05 carry a note marked `summary provenance unreliable`: a bug
  injected *another* session's digest, often from a different project, and that
  text was stored as the summary. If a hit carries that marker, treat its
  summary as unattributed and rely on its date, project and file list instead.
- **Absence is not evidence.** A project with no hits may simply predate
  capture or have been worked on outside Claude Code. Say "the brain has
  nothing on this" rather than "this was never done".
- **Report what you found, not that you searched.** Quote the relevant line and
  name its date and project so the user can judge it.
- If nothing matches, try one broader query before concluding. `brain recall`
  deliberately returns few results; `brain search --limit 30` returns many.

## If the command is missing

`brain` is a console script from the `claude-brain` package. If it is not on
PATH, the tool is installed at
the folder you cloned it into and
`python -m claude_brain.cli` is equivalent. `brain doctor` reports the health of
the store and whether automatic capture is wired up.
