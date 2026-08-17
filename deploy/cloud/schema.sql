CREATE TABLE IF NOT EXISTS robot_tasks (
    id TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS robot_tasks_updated_at_idx ON robot_tasks (updated_at, id);
