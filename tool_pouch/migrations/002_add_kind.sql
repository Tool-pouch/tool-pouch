-- Adds `kind` to runs, distinguishing test runs (the 0.1 default), captured
-- production traces (tool_pouch.wrap), and replay runs.
--
-- Backfill: every existing row pre-0.2 was a test run, so default to 'test'.
-- New writes set kind explicitly.
ALTER TABLE runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'test';

UPDATE runs SET kind = 'test' WHERE kind IS NULL;

CREATE INDEX IF NOT EXISTS idx_runs_kind_started_at
    ON runs(kind, started_at);

CREATE INDEX IF NOT EXISTS idx_runs_kind_agent
    ON runs(kind, agent_name);
