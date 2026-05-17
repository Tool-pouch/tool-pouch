-- Initial schema. Mirrors the 0.1 SCHEMA constant so existing 0.1
-- databases (which already have these tables via CREATE TABLE IF NOT
-- EXISTS) reach the same shape after migration #001.
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    started_at REAL,
    agent_name TEXT,
    user_input TEXT,
    workspace_id TEXT,
    user_id TEXT,
    agent_version TEXT,
    environment TEXT DEFAULT 'local',
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS results (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    scenario TEXT,
    target_tool TEXT,
    outcome TEXT,
    failure_type TEXT,
    trace TEXT,
    duration_ms INTEGER,
    workspace_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_results_run_id ON results(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_workspace ON runs(workspace_id);
