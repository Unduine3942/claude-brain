-- Stop session_files from accepting duplicates.
--
-- file_id is a fresh uuid4 on every insert, so the existing
-- `INSERT OR IGNORE` never actually deduped: ingesting the same transcript
-- twice produced two rows for the same (session_id, file_path).
--
-- Existing duplicates are collapsed first, keeping the lowest rowid, because a
-- UNIQUE index cannot be created over duplicate data.

DELETE FROM session_files
WHERE rowid NOT IN (
    SELECT MIN(rowid) FROM session_files GROUP BY session_id, file_path
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_session_files_natural
    ON session_files(session_id, file_path);
