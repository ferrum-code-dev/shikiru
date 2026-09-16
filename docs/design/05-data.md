# 05 · Data

## 1. Configuration

**Sources**, from lowest to highest precedence, deep-merged:
1. Packaged `config/defaults.toml`
2. `~/.config/shikiru/config.toml` (optional user-wide file: agents, models, notify)
3. `<project>/.shikiru/config.toml`
4. CLI `--set key.path=value` (repeatable, value parsed as TOML), on every command that reads config (`start`, `submit`, `doctor`, …)

`shikiru config show [--json]` prints the effective config and the source of each key. `shikiru config set key=value` edits the project file with `tomlkit`, which keeps comments.
Validation uses pydantic models (`extra="forbid"`), so errors name the file and the key path.

A complete project file, as written by `shikiru init` with defaults:

```toml
[project]
name = "ferrum"                     # tmux session "[shikiru] ferrum"; default = folder name
trackers = ["tasks/plan.md", "tasks/todo.md"]   # given to Leader/Implementer as the task sources of truth

[[repos]]
path = "."                          # relative to project root; "." = monorepo
base = "main"                       # omit = current branch at task start
caches = ["node_modules", "**/node_modules", ".venv", "target", "build/.cache"]
remote = "origin"

[orchestration]
max_parallel = 2
max_iterations = 5
consult_limit = 3
integration = "merge"               # merge | pr
push = false
keep_branches = false
cache_mode = "clone"                # clone | symlink | none
budget_usd = 0                      # 0 = no budget escalation

[orchestration.resources]
max_cpu_pct = 85
min_free_ram_gb = 4

[orchestration.timeouts]            # seconds
launch = 90
stall = 1200
tool = 7200
max_quota_wait = 28800
integration_dirty_wait = 1800

[orchestration.review]
accept_minor_issues = false

[orchestration.on_quota]
policy = "wait"                     # wait | next

[agents.claude]
plugin = "claude"                   # entry point name
command = "claude"
args = ["--permission-mode", "auto"]
prompt_prefix = "/using-agent-skills "
auth = "subscription"               # subscription | api_key
api_key_env = "ANTHROPIC_API_KEY"

[agents.codex]
plugin = "codex"
command = "codex"
args = ["--approve-for-me"]
prompt_prefix = "$using-agent-skills "
auth = "subscription"
compact_below_free_pct = 60

[roles.leader]
ladder = [
  { agent = "claude", model = "opus", effort = "high" },
  { agent = "codex", model = "gpt-5.6-sol", effort = "high" },
]

[roles.implementer]
escalate_after = 2                  # rejected verifications on a rung before moving up
ladder = [
  { agent = "codex", model = "gpt-5.6-luna", effort = "xhigh" },
  { agent = "codex", model = "gpt-5.6-sol", effort = "high" },
  { agent = "claude", model = "opus", effort = "high" },
]

[roles.implementer.complexity.high] # optional per-complexity ladders (low | normal | high)
ladder = [
  { agent = "codex", model = "gpt-5.6-sol", effort = "high" },
  { agent = "claude", model = "opus", effort = "xhigh" },
]

[roles.helper]
ladder = [
  { agent = "claude", model = "haiku", effort = "low" },
  { agent = "codex", model = "gpt-5.6-mini", effort = "low" },
]

[quota]
probe_interval = 900                # seconds; active QuotaProbe cadence while an agent has live runs (0 = off)

[models."gpt-5.6-luna"]             # packaged defaults exist for built-in models; override when prices change
context_window = 400000
input = 0.0                         # USD per 1M tokens
cache_read = 0.0
cache_write = 0.0
output = 0.0

[dashboard]
refresh_seconds = 2
style = "comic"                     # comic | plain (06-operator §7)

[dashboard.layout]                  # tree: split = "h" (side by side) | "v" (stacked); size = % of parent
split = "h"
panes = [
  { pane = "system", size = 30 },
  { split = "v", panes = [ { pane = "leader" }, { pane = "implementer" }, { pane = "shell", size = 20 } ] },
]

[dashboard.system]
widgets = ["project", "strips", "escalations", "queue", "dialogue", "usage", "resources", "docker"]

[dashboard.titles]                  # str.format fields: 06-operator §5.2
window = "{task_id} {title:.24}"
leader = "長 Leader · {agent}:{model}/{effort} · iter {iteration} · ctx {context_free}% · ${cost:.2f} {flags}"
implementer = "員 Implementer · {agent}:{model}/{effort} · iter {iteration} · ctx {context_free}% · ${cost:.2f} {flags}"
system = "Shikiru {project} · {active}/{max_parallel} active · {queued} queued"
shell = "Shell · {cwd}"

[notify]
desktop = true                      # OSC 777/9 through tmux passthrough, and terminal-notifier / notify-send when present
command = ""                        # optional; receives event JSON on stdin (escalation, task_done, scope_change, quota)
events = ["escalation", "task_done", "scope_change", "quota"]
```

Every override flag on `submit` works for that task only:
- `--mode implement|plan|split`
- `--complexity`
- `--priority`
- `--after T-3` (repeatable)
- `--repos a,b`
- `--leader a:m:e` / `--implementer a:m:e`
- `--budget 20`

## 2. State DB (`.shikiru/state.db`)

- SQLite in WAL mode with `busy_timeout = 5000`.
- `PRAGMA user_version` drives migrations in `store/migrations/NNN_*.sql`.
- Readers open short read transactions and detect changes cheaply with `PRAGMA data_version`.
- All timestamps are UTC ISO-8601 text.

```sql
CREATE TABLE tasks (
  id TEXT PRIMARY KEY,               -- T-<n>
  seq INTEGER UNIQUE NOT NULL,
  parent_id TEXT REFERENCES tasks(id),
  origin TEXT NOT NULL,              -- operator | split | leader | replan
  title TEXT NOT NULL, request TEXT NOT NULL,
  mode TEXT NOT NULL,                -- triage | implement | plan | split
  complexity TEXT NOT NULL DEFAULT 'normal',
  priority INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL, step TEXT, holds TEXT NOT NULL DEFAULT '[]',   -- JSON array of {kind, until?, reason}
  iteration INTEGER NOT NULL DEFAULT 0,
  repos TEXT NOT NULL DEFAULT '[]',  -- JSON [{path, base, base_sha, branch}]
  overrides TEXT NOT NULL DEFAULT '{}', -- JSON {leader?, implementer?, budget_usd?, rung_index{role:n}}
  slot INTEGER, window_id TEXT, pr_url TEXT,
  summary TEXT,                      -- final summary for task_done
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE TABLE task_deps (task_id TEXT NOT NULL REFERENCES tasks(id), depends_on TEXT NOT NULL REFERENCES tasks(id),
  PRIMARY KEY (task_id, depends_on));

CREATE TABLE runs (                  -- one agent session per task+role (+ agent switch)
  id INTEGER PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id), role TEXT NOT NULL,
  agent TEXT NOT NULL, model TEXT NOT NULL, effort TEXT,
  session_id TEXT, transcript_path TEXT, pane_id TEXT,
  hooks_offset INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,              -- starting | live | exited | closed
  input_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0, context_used_tokens INTEGER,   -- cost and context % derived via models table
  started_at TEXT NOT NULL, ended_at TEXT
);
CREATE TABLE turns (
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
  task_id TEXT NOT NULL, step TEXT NOT NULL, iteration INTEGER NOT NULL,
  dir TEXT NOT NULL,
  status TEXT NOT NULL,              -- injected | completed | failed | aborted
  outcome TEXT,                      -- e.g. accept / changes_requested / completed
  rebuilt INTEGER NOT NULL DEFAULT 0, nudges INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL, ended_at TEXT
);
CREATE TABLE quotas (                -- latest QuotaProbe / transcript observation per agent window
  agent TEXT NOT NULL, window TEXT NOT NULL, used_pct REAL NOT NULL, resets_at TEXT, observed_at TEXT NOT NULL,
  PRIMARY KEY (agent, window)
);
CREATE TABLE escalations (
  id TEXT PRIMARY KEY,               -- E-<n>
  task_id TEXT NOT NULL REFERENCES tasks(id), step TEXT, reason TEXT NOT NULL,
  headline TEXT NOT NULL, packet_path TEXT NOT NULL,
  options TEXT NOT NULL,             -- JSON [{key, label, consequences, recommended}]
  status TEXT NOT NULL,              -- open | resolved
  resolution TEXT,                   -- JSON {option, text?, rung?, by}
  created_at TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE events (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, task_id TEXT, kind TEXT NOT NULL, message TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE commands (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, name TEXT NOT NULL, args TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending | done | rejected
  result TEXT, handled_at TEXT
);
CREATE TABLE engine (id INTEGER PRIMARY KEY CHECK (id = 1), pid INTEGER, started_at TEXT, heartbeat_at TEXT, tmux_session TEXT);
```

**Event kinds**:
- `task_queued`, `task_started`, `step_started`, `turn_injected`, `turn_completed`, `turn_failed`
- `verdict`, `rung_changed`, `hold_set`, `hold_cleared`, `scope_change`
- `escalation`, `escalation_resolved`
- `integration_started`, `task_done`, `task_cancelled`
- `intervention`, `operator_message`
- `engine_started`, `engine_stopped`

The engine heartbeat is refreshed every 5 s. The CLI and dashboard show `engine: down` when it is older than 15 s.

## 3. Run files

```
runs/T-12/
  leader/ implementer/            # one dir per role (a switch to another agent adds a suffix, e.g. implementer.2/)
    hooks.jsonl  current_turn -> ../turns/05-verify-i2
  turns/NN-<step>-i<iteration>/
    prompt.md  report.schema.json  report.json  facts.md (verify)  helper.json (if rebuilt)
  messages/NNN.md                 # `say` messages
  escalations/E-3.md
```

## 4. Report schemas (`domain/reports.py`, pydantic → JSON Schema)

```python
class Subtask(BaseModel):     title: str; request: str; priority: int = 0; depends_on: list[int] = []  # indexes within list
class NewTask(BaseModel):     title: str; request: str; priority: int = 0; depends_on: list[str] = []  # existing T-ids
class Commit(BaseModel):      repo: str; sha: str; subject: str
class TestRun(BaseModel):     name: str; result: Literal["passed", "failed", "skipped"]; details: str = ""
class Issue(BaseModel):       severity: Literal["blocker", "major", "minor"]; description: str; location: str = ""
class Option(BaseModel):      label: str; consequences: str; recommended: bool = False
class EscalationRequest(BaseModel): question: str; options: list[Option]

class TriageReport(BaseModel):
    title: str                                        # ≤ 60 chars
    mode: Literal["implement", "plan", "split"]
    complexity: Literal["low", "normal", "high"]
    repos: list[str]                                  # subset of configured repo paths
    acceptance_criteria: list[str]
    references: list[str]                             # plan anchors / files
    subtasks: list[Subtask] = []                      # mode == split
    notes: str = ""

class ImplementationReport(BaseModel):
    status: Literal["completed", "needs_decision", "blocked"]
    summary: str
    commits: list[Commit]
    tests: list[TestRun]
    trackers_updated: bool
    risks: list[str] = []
    question: str = ""                                # required unless completed

class PlanReport(BaseModel):
    summary: str
    commits: list[Commit]
    new_tasks: list[NewTask] = []
    enqueue: bool = False

class Verdict(BaseModel):
    decision: Literal["accept", "changes_requested", "escalate"]
    summary: str
    issues: list[Issue] = []                          # empty when accept (unless accept_minor_issues)
    residual_risks: list[str] = []
    new_tasks: list[NewTask] = []
    scope_changes: list[str] = []
    answer: str = ""                                  # consult turns
    escalation: EscalationRequest | None = None       # required when escalate

class ReplanDecision(BaseModel):
    decision: Literal["decompose", "restate", "escalate"]
    rationale: str
    subtasks: list[Subtask] = []
    restated_request: str = ""
    escalation: EscalationRequest | None = None
```

Rules that the schema can't express (for example "`question` is required unless `completed`") are pydantic `model_validator`s. `shikiru report` prints their messages on exit code 2.
