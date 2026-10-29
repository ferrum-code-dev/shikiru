CREATE TABLE IF NOT EXISTS task_quota_turns (
  id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
  run_id INTEGER NOT NULL REFERENCES runs(id), turn TEXT NOT NULL, role TEXT NOT NULL,
  agent TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, outcome TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_quota_turns_task_id ON task_quota_turns(task_id);
CREATE INDEX IF NOT EXISTS idx_task_quota_turns_agent ON task_quota_turns(agent, started_at);

INSERT INTO task_quota_turns
  (task_id, run_id, turn, role, agent, started_at, ended_at, outcome)
SELECT runs.task_id, runs.id, 'legacy', runs.role, runs.agent, runs.started_at, runs.ended_at,
       'legacy'
FROM runs
WHERE NOT EXISTS (
  SELECT 1 FROM task_quota_turns WHERE task_quota_turns.run_id = runs.id
);

CREATE TABLE IF NOT EXISTS task_quota_samples (
  id INTEGER PRIMARY KEY, turn_id INTEGER NOT NULL REFERENCES task_quota_turns(id),
  window TEXT NOT NULL, used_pct REAL NOT NULL, resets_at TEXT, observed_at TEXT NOT NULL,
  boundary TEXT, time_source TEXT NOT NULL DEFAULT 'collected'
);

CREATE INDEX IF NOT EXISTS idx_task_quota_samples_turn_id ON task_quota_samples(turn_id);
