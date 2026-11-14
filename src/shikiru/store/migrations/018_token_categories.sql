-- Token categories: uncached input, cache reads, cache writes and output are mutually exclusive,
-- and a NULL category means the provider did not report it (never a zero).
-- Historical counters are retained as ambiguous evidence and excluded from normalized totals.

ALTER TABLE runs RENAME TO runs_legacy;

CREATE TABLE runs (
  id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), role TEXT NOT NULL,
  agent TEXT NOT NULL, model TEXT NOT NULL, effort TEXT,
  session_id TEXT, resume_command TEXT, last_message TEXT,
  status TEXT NOT NULL,
  uncached_input_tokens INTEGER, output_tokens INTEGER,
  cache_read_tokens INTEGER, cache_write_tokens INTEGER,
  cost_usd REAL, cost_basis TEXT NOT NULL DEFAULT 'unknown', context_used_pct REAL,
  started_at TEXT NOT NULL, ended_at TEXT,
  usage_reported INTEGER,
  usage_accounting TEXT NOT NULL DEFAULT 'categories',
  billing_mode TEXT NOT NULL DEFAULT 'unknown',
  billing_source TEXT NOT NULL DEFAULT 'unknown'
);

INSERT INTO runs (
  id, task_id, role, agent, model, effort, session_id, resume_command, last_message,
  status,
  uncached_input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
  cost_usd, cost_basis, context_used_pct, started_at, ended_at,
  usage_reported, usage_accounting, billing_mode, billing_source
)
SELECT
  id, task_id, role, agent, model, effort, session_id, resume_command, last_message,
  status,
  NULL, NULL, NULL, NULL,
  cost_usd, cost_basis, context_used_pct, started_at, ended_at,
  usage_reported, 'legacy', billing_mode, billing_source
FROM runs_legacy;

CREATE TABLE legacy_run_usage (
  run_id INTEGER PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  usage_reported INTEGER,
  input_tokens INTEGER,
  uncached_input_tokens INTEGER,
  output_tokens INTEGER,
  cache_read_tokens INTEGER,
  cache_write_tokens INTEGER
);

INSERT INTO legacy_run_usage (
  run_id, source, usage_reported, input_tokens, uncached_input_tokens,
  output_tokens, cache_read_tokens, cache_write_tokens
)
SELECT id, 'pre_category_columns', usage_reported, input_tokens, NULL,
       output_tokens, cache_read_tokens, cache_write_tokens
FROM runs_legacy;

DROP TABLE runs_legacy;

CREATE INDEX idx_runs_task_id ON runs(task_id);
