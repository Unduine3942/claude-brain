-- Full-text search across sessions and day digests in one index.
--
-- Deliberately a PLAIN fts5 table, not `content=''`. A contentless index cannot
-- return column text, which also means snippet() and highlight() return NULL --
-- so a contentless design has to keep its own second copy of the text purely to
-- build excerpts from. The whole database is ~1 MB; paying FTS5's own copy is
-- cheaper than hand-rolling what snippet() already does correctly.
--
-- `kind`, `ref_id`, `project_id` and `date` are UNINDEXED: they are filters and
-- join keys, not search terms. Leaving `kind` indexed would make the word
-- "session" match every row in the table.

CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    kind       UNINDEXED,   -- 'session' | 'digest'
    ref_id     UNINDEXED,   -- sessions.session_id | day_digests.digest_id
    project_id UNINDEXED,
    date       UNINDEXED,
    project,                -- project name, searchable: "what did I do in Overture"
    title,                  -- session summary, or digest topic
    body,                   -- tasks, notes and touched file paths
    tokenize = 'unicode61 remove_diacritics 2'
);
