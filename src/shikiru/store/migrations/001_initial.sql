CREATE TABLE sequences (name TEXT PRIMARY KEY, value INTEGER NOT NULL);

CREATE TABLE tasks (
  id TEXT PRIMARY KEY, seq INTEGER UNIQUE NOT NULL,
  parent_id TEXT REFERENCES tasks(id), origin TEXT NOT NULL,
  title TEXT NOT NULL, request TEXT NOT NULL,
  mode TEXT, complexity TEXT NOT NULL DEFAULT 'normal', priority INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL, step TEXT, status_reason TEXT,
  holds TEXT NOT NULL DEFAULT '[]',
  iteration INTEGER NOT NULL DEFAULT 0,
  counters TEXT NOT NULL DEFAULT '{}',
  rung_index TEXT NOT NULL DEFAULT '{}',
  overrides TEXT NOT NULL DEFAULT '{}',
  repo TEXT,
  slot INTEGER, window_id TEXT, panes TEXT NOT NULL DEFAULT '{}',
  wait_reason TEXT, requeued_at TEXT, summary TEXT,
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);

CREATE INDEX idx_tasks_status ON tasks(status);

CREATE TABLE task_deps (task_id TEXT NOT NULL REFERENCES tasks(id), depends_on TEXT NOT NULL REFERENCES tasks(id),
  PRIMARY KEY (task_id, depends_on));

CREATE INDEX idx_task_deps_depends_on ON task_deps(depends_on);

CREATE TABLE runs (
  id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), role TEXT NOT NULL,
  agent TEXT NOT NULL, model TEXT NOT NULL, effort TEXT,
  session_id TEXT, resume_command TEXT,
  agent_state TEXT NOT NULL DEFAULT '{}', last_message TEXT,
  status TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL, cost_basis TEXT, context_used_pct REAL,
  started_at TEXT NOT NULL, ended_at TEXT
);

CREATE INDEX idx_runs_task_id ON runs(task_id);

CREATE TABLE agent_blocks (agent TEXT PRIMARY KEY, until TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE quotas (agent TEXT NOT NULL, window TEXT NOT NULL, used_pct REAL NOT NULL, resets_at TEXT,
  observed_at TEXT NOT NULL, PRIMARY KEY (agent, window));

CREATE TABLE escalations (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), step TEXT, reason TEXT NOT NULL,
  headline TEXT NOT NULL, packet_path TEXT NOT NULL, options TEXT NOT NULL,
  status TEXT NOT NULL,
  resolution TEXT, created_at TEXT NOT NULL, resolved_at TEXT
);

CREATE INDEX idx_escalations_status ON escalations(status);

CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, task_id TEXT, kind TEXT NOT NULL,
  message TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}');

CREATE INDEX idx_events_task_id ON events(task_id);

CREATE TABLE commands (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  result TEXT, handled_at TEXT);

CREATE INDEX idx_commands_status ON commands(status);
