---
name: brain-save
description: Write a durable, curated note about the current session into Claude Brain and the project's Obsidian vault — what was done, why, and what the next session needs to know. Use when the user asks to save, record, or write up this session, when a session reached a decision worth keeping, or at the end of substantial work. Capture is already automatic; this adds the reasoning that a prompt list cannot carry.
---

# Save this session to Claude Brain

Capture is **already automatic** — hooks on `SessionStart`, `SessionEnd` and
`PreCompact` write the structured record whether or not anyone asks. This skill
is for the part automation cannot produce: a write-up of what was decided and
why, which is the only part that is expensive to reconstruct later.

So the first thing to establish is whether anything needs doing at all.

## Decide what is actually wanted

**The user asked to save the session.** Run the curated save:

```bash
brain save --latest --curate
```

That re-ingests the newest transcript, spawns an isolated `claude -p` call to
write the prose, and replaces the managed region of the session's vault note.
It takes under a minute. Report the path it printed.

**A decision was reached that belongs in the project's long-term record.** A
session note is the wrong home for it — it will be one note among hundreds.
Append it to the project's `Decisions.md` instead, in the format that file
already uses:

```markdown
**Decision:** what was chosen
**Date:** 2026-10-05
**Why:** the reasoning, including what was considered and rejected
**Revisit if:** the condition that would change the answer
```

Both can be right. A long session often warrants the note *and* one decision
entry.

**The user just wants the record up to date.** `brain ingest` is enough, and
the hooks have almost certainly done it already. Say so rather than running
something redundant.

## Writing the note yourself

If `--curate` is unavailable or the user wants you to write it, you have the
context the spawned call would have to infer. Write it directly into the
project's `Sessions/<date>_<slug>.md` and keep the structure the writer uses:

- YAML frontmatter with `type: session`, `project`, `date`, `session_id`, `tags`
- the body inside `%% brain:begin hash=… %%` / `%% brain:end %%`
- sections: `## What was done`, `## Why it was done that way`,
  `## What to know next time`

**Do not hand-edit inside the markers and expect it to survive.** The hash in
the begin marker is how the writer detects a hand edit; once it no longer
matches, the note is skipped on every future write — which protects your prose,
but also means automatic refreshes stop for that note. Write below
`%% brain:end %%` if you want both.

## What makes a note worth keeping

- **Name things.** Files, commands, numbers, exact error text. "Fixed the
  buffering" is worthless in six months; "the viewer must pin quality to
  maximum, because ABR was choosing a bitrate above the tunnel's throughput" is
  not.
- **Record what was rejected and why.** This is the single highest-value thing
  in the vault, and the only part that cannot be recovered from a diff.
- **Say when the record is thin.** An honest "no reasoning was captured" is
  more useful than invented rationale, and far less harmful.
- **Never write a secret into the vault.** The store scrubs what it captures,
  but anything you type goes in verbatim. Vault files are git-committed and
  synced.

## Afterwards

`brain doctor` confirms the store is healthy and capture is wired up. If the
user wants to see the note rendered, it is an ordinary file in their vault —
`brain search "<topic>"` will find it once the index refreshes, which the next
sweep does automatically.
