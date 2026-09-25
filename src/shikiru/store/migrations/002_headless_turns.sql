DROP INDEX idx_runs_task_id;

ALTER TABLE runs RENAME TO runs_legacy;

CREATE TABLE runs (
  id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), role TEXT NOT NULL,
  agent TEXT NOT NULL, model TEXT NOT NULL, effort TEXT,
  session_id TEXT, resume_command TEXT, last_message TEXT,
  status TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL, cost_basis TEXT NOT NULL DEFAULT 'unknown', context_used_pct REAL,
  started_at TEXT NOT NULL, ended_at TEXT
);

INSERT INTO runs (
  id, task_id, role, agent, model, effort, session_id, resume_command, last_message,
  status,
  input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, cost_basis,
  context_used_pct, started_at, ended_at
)
SELECT
  id, task_id, role, agent, model, effort, session_id, resume_command, last_message,
  CASE WHEN status = 'exited' THEN 'closed' ELSE status END,
  input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd,
  COALESCE(cost_basis, 'unknown'), context_used_pct, started_at, ended_at
FROM runs_legacy;

DROP TABLE runs_legacy;

CREATE INDEX idx_runs_task_id ON runs(task_id);
