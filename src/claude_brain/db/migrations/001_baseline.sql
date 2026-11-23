-- Claude Brain database schema

CREATE TABLE IF NOT EXISTS projects (
    project_id   TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    path         TEXT NOT NULL UNIQUE,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_scanned TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vaults (
    vault_id     TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    vault_path   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'none',  -- none | exists
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    project_id   TEXT,
    source_dir   TEXT,
    date         TEXT,
    summary      TEXT,
    file_count   INTEGER DEFAULT 0,
    tool_count   INTEGER DEFAULT 0,
    notes        TEXT,
    raw_path     TEXT,
    is_master    INTEGER DEFAULT 0,
    parsed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS session_files (
    file_id      TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS consolidations (
    consolidation_id TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    session_ids      TEXT NOT NULL,  -- JSON array
    markdown_path    TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Tracks every .jsonl raw_path ever imported; survives session deletion/summarization
CREATE TABLE IF NOT EXISTS imported_paths (
    raw_path  TEXT PRIMARY KEY,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sessions_project    ON sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_sessions_date       ON sessions(date);
CREATE INDEX IF NOT EXISTS idx_session_files_sess  ON session_files(session_id);
CREATE INDEX IF NOT EXISTS idx_vaults_project      ON vaults(project_id);
