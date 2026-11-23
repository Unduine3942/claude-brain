-- Per-project-per-day session digests, parsed from ~/.claude/sessions/*.tmp
--
-- Deliberately NOT stored as sessions. A .tmp is a daily digest, not a session:
-- there is one per project per day (so multiple sessions merge into one file),
-- a single long session spans several of them, and the file carries no
-- session_id at all. Minting synthetic session rows from these is exactly how
-- the store previously accumulated 882 rows for ~50 real sessions.

CREATE TABLE IF NOT EXISTS day_digests (
    digest_id   TEXT PRIMARY KEY,
    project_id  TEXT,
    date        TEXT NOT NULL,
    project_name TEXT,            -- as recorded in the file, may differ from projects.name
    worktree    TEXT,             -- absolute path from **Worktree:**; the reliable key
    tasks       TEXT,
    files       TEXT,             -- newline-separated
    notes       TEXT,
    tools       TEXT,
    source_path TEXT NOT NULL,
    source_mtime REAL,
    parsed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE SET NULL
);

-- One digest per source file; re-importing updates rather than duplicates.
CREATE UNIQUE INDEX IF NOT EXISTS idx_day_digests_source ON day_digests(source_path);
CREATE INDEX IF NOT EXISTS idx_day_digests_project ON day_digests(project_id);
CREATE INDEX IF NOT EXISTS idx_day_digests_date    ON day_digests(date);
