-- Ingest dedupe cache.
--
-- `imported_paths` records that a path was *ever* seen, which permanently blocks
-- re-import even when the row it created has been deleted (917 entries against
-- 872 sessions on the live database). This table tracks mtime and size instead,
-- so a transcript that has grown since it was last read is re-ingested, and a
-- re-run that finds nothing changed is cheap.

CREATE TABLE IF NOT EXISTS cached_files (
    path         TEXT PRIMARY KEY,
    mtime        REAL NOT NULL,
    size         INTEGER NOT NULL,
    session_id   TEXT,
    ingested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cached_files_session ON cached_files(session_id);
