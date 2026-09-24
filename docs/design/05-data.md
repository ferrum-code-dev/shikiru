# 05 · Data

## 1. Configuration

**Sources**, merged deeply, lowest first:
1. packaged `config/defaults.toml`
2. `.shikiru/config.toml`
3. `--set key.path=value` (value parsed as TOML)

`doctor --json` prints the merged result. Core keys are validated by pydantic (`extra="forbid"`). Each `[agents.<name>.options]` table is validated by that plugin's factory at `start` and `doctor`.

The full reference (this is also what `init` writes):

```toml
[project]
name = "ferrum"                                 # tmux session "[shikiru] ferrum"; default: folder name
trackers = ["tasks/plan.md", "tasks/todo.md"]

[repo]                                          # the project root is the repository root (D20)
base = "main"                                   # omit = branch checked out at task start
caches = ["node_modules", "**/node_modules", ".venv", "target"]
remote = "origin"                               # used by integration = "pr"

[orchestration]
max_parallel = 2
max_iterations = 5
integration = "merge"                           # merge | pr
poll_seconds = 2

[orchestration.resources]
max_cpu_pct = 85
min_free_ram_gb = 4

[orchestration.timeouts]                        # seconds
launch = 90
stall = 1200
tool = 7200
max_quota_wait = 28800
integration_wait = 1800

[agents.claude]                                 # table name = plugin entry point
prompt_prefix = "/using-agent-skills "
[agents.claude.options]                         # plugin-owned (04 §4)
auth = "subscription"

[agents.codex]
prompt_prefix = "$using-agent-skills "
[agents.codex.options]                          # plugin-owned (04 §5)
auth = "subscription"
compact_below_free_pct = 60

[roles.leader]
ladder = [
  { agent = "claude", model = "opus", effort = "high" },
  { agent = "codex", model = "gpt-5.6-sol", effort = "high" },
]

[roles.implementer]
escalate_after = 2
ladder = [
  { agent = "codex", model = "gpt-5.6-luna", effort = "xhigh" },
  { agent = "codex", model = "gpt-5.6-sol", effort = "high" },
  { agent = "claude", model = "opus", effort = "high" },
]
[roles.implementer.complexity.high]
ladder = [
  { agent = "codex", model = "gpt-5.6-sol", effort = "high" },
  { agent = "claude", model = "opus", effort = "xhigh" },
]

[roles.helper]
ladder = [ { agent = "claude", model = "haiku", effort = "low" } ]

[dashboard]
animations = true
widgets = ["header", "escalations", "strips", "queue", "dialogue", "usage", "resources", "docker"]
layout = { split = "h", panes = [
  { pane = "system", size = 30 },
  { split = "v", panes = [ { pane = "leader" }, { pane = "implementer" }, { pane = "shell", size = 20 } ] },
] }

[dashboard.titles]                              # fields: 06 §5.3
window = "{task_id} {title:.24}"
leader = "{glyph} Leader · {agent}:{model}/{effort} · iter {iteration} · ctx {context}% · ${cost:.2f} {flags}"
implementer = "{glyph} Implementer · {agent}:{model}/{effort} · iter {iteration} · ctx {context}% · ${cost:.2f} {flags}"
system = "Shikiru · {project} · {active}/{max_parallel} active · {queued} queued"
shell = "Shell · {cwd}"

[notify]
command = ""                                    # optional; event JSON on stdin for escalation and task_done
```

**`submit` flags**, applying to that task only: `--mode implement|plan|split`, `--complexity low|normal|high`, `--priority N`, `--after T-n` (repeatable), `--leader a:m:e`, `--implementer a:m:e`.

**Protocol constants** are named module constants, not configuration:

| Constant | Value |
|---|---|
| `INJECT_DELAY` | 150 ms |
| `SUBMIT_CONFIRM` | 15 s |
| `BEFORE_TURN_WAIT` | 10 min |
| `NUDGE_LIMIT` | 2 |
| `CONSULT_LIMIT` | 3 |
| `CONFLICT_ROUNDS` | 2 |
| Relaunches per turn | 2 |
| Stall recoveries per turn | 2 |
| Unavailable backoff | 30 s / 2 min / 5 min |
| `QUOTA_PROBE` | 15 min |
| `INTEGRATION_RETRY` | 2 min |
| `PR_POLL` | 5 min |
| Command poll | 250 ms |
| Dashboard change poll | 500 ms |
| Session exit grace | 10 s |
| Escalation evidence | 60 pane lines |
| Command ack wait | 10 s |
| `QUOTA_MARGIN` | 60 s |
| `QUOTA_POLL` | 60 s |
| `CPU_WINDOW` | 30 s |
| Git data cache (header) | 10 s |
| Resource / Docker sampling | 2 s / 5 s |
| Dialogue length | 20 lines |
| MCP `wait_for_events` max timeout | 300 s |
| Dashboard motion cap | 4 Hz |

## 2. State DB (`.shikiru/state.db`)

- SQLite WAL mode, `busy_timeout = 5000`.
- Migrations tracked by `PRAGMA user_version`.
- Readers use short read transactions and `PRAGMA data_version` to detect changes.
- Timestamps are UTC ISO-8601.

```sql
CREATE TABLE sequences (name TEXT PRIMARY KEY, value INTEGER NOT NULL);   -- task, escalation, message

CREATE TABLE tasks (
  id TEXT PRIMARY KEY, seq INTEGER UNIQUE NOT NULL,
  parent_id TEXT REFERENCES tasks(id), origin TEXT NOT NULL,
  title TEXT NOT NULL, request TEXT NOT NULL,
  mode TEXT, complexity TEXT NOT NULL DEFAULT 'normal', priority INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL, step TEXT, status_reason TEXT,
  holds TEXT NOT NULL DEFAULT '[]',               -- ["paused","intervention"]
  iteration INTEGER NOT NULL DEFAULT 0,
  counters TEXT NOT NULL DEFAULT '{}',            -- rejections_on_rung, consults, conflict_rounds
  rung_index TEXT NOT NULL DEFAULT '{}',          -- {role: n}
  overrides TEXT NOT NULL DEFAULT '{}',           -- {role: {agent, model, effort}}
  repo TEXT,                                      -- {base, base_sha, branch}; null until the task root exists
  slot INTEGER, window_id TEXT, panes TEXT NOT NULL DEFAULT '{}',
  wait_reason TEXT, requeued_at TEXT, summary TEXT,
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE TABLE task_deps (task_id TEXT NOT NULL REFERENCES tasks(id), depends_on TEXT NOT NULL REFERENCES tasks(id),
  PRIMARY KEY (task_id, depends_on));

CREATE TABLE runs (                               -- one agent session per task role
  id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), role TEXT NOT NULL,
  agent TEXT NOT NULL, model TEXT NOT NULL, effort TEXT,
  session_id TEXT, resume_command TEXT,
  agent_state TEXT NOT NULL DEFAULT '{}', last_message TEXT,
  status TEXT NOT NULL,                           -- live | exited | closed
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL, cost_basis TEXT, context_used_pct REAL,
  started_at TEXT NOT NULL, ended_at TEXT
);

CREATE TABLE agent_blocks (agent TEXT PRIMARY KEY, until TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE quotas (agent TEXT NOT NULL, window TEXT NOT NULL, used_pct REAL NOT NULL, resets_at TEXT,
  observed_at TEXT NOT NULL, PRIMARY KEY (agent, window));

CREATE TABLE escalations (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), step TEXT, reason TEXT NOT NULL,
  headline TEXT NOT NULL, packet_path TEXT NOT NULL, options TEXT NOT NULL,
  status TEXT NOT NULL,                           -- open | resolved
  resolution TEXT, created_at TEXT NOT NULL, resolved_at TEXT
);

CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, task_id TEXT, kind TEXT NOT NULL,
  message TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}');

CREATE TABLE commands (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',        -- pending | done | rejected
  result TEXT, handled_at TEXT);
```

**`EventKind`** (in `domain.events`; `events.data` carries typed fields):
- `task_queued`, `task_started`, `step_started`
- `turn_started`, `turn_completed` (agent, model, outcome, summary, question, usage delta), `turn_failed`
- `nudge`, `helper_action`, `rung_changed`
- `quota_blocked`, `quota_cleared`
- `verdict`, `scope_change`
- `hold_set`, `hold_cleared`, `intervention`, `operator_message`
- `escalation`, `escalation_resolved`
- `integration_started`, `pr_opened`
- `task_done`, `task_cancelled`
- `pane_restored`, `engine_started`, `engine_stopped`

**Commands** are pydantic models in `domain.commands`, stored as `kind` plus a JSON `payload`:
- `Submit` (carries the task id allocated from `sequences` at insert)
- `Prioritize`, `Pause`, `Resume`, `Cancel`, `Retry`, `SetRung`, `Say`, `Resolve`, `Stop`

## 3. Run files

See 02 §5. `report.kind` names the report model of the turn. `helper.json` exists only for rebuilt reports.

## 4. Report models (`domain.reports`, pydantic → JSON Schema)

```python
class NewTask(BaseModel):      title: str; request: str; priority: int = 0
                               depends_on: list[str] = []   # existing "T-n" or "#k" = k-th task of the same list, 1-based; `shikiru report` rejects an out-of-range "#k"
class Commit(BaseModel):       repo: str; sha: str; subject: str
class TestRun(BaseModel):      name: str; result: Literal["passed", "failed", "skipped"]; details: str = ""
class Issue(BaseModel):        severity: Literal["blocker", "major", "minor"]; description: str; location: str = ""
class Option(BaseModel):       label: str; consequences: str; recommended: bool = False
class EscalationRequest(BaseModel): question: str; options: list[Option]      # exactly one recommended

class TriageReport(BaseModel):
    title: str                                     # ≤ 60 chars
    mode: Literal["implement", "plan", "split"]
    complexity: Literal["low", "normal", "high"]
    acceptance_criteria: list[str]
    references: list[str]
    subtasks: list[NewTask] = []                   # required iff mode == split

class ImplementationReport(BaseModel):
    status: Literal["completed", "needs_decision", "blocked"]
    summary: str
    commits: list[Commit]
    tests: list[TestRun]
    trackers_updated: bool
    risks: list[str] = []
    question: str = ""                             # required unless completed

class PlanReport(BaseModel):
    summary: str
    commits: list[Commit]
    new_tasks: list[NewTask] = []
    scope_changes: list[str] = []

class ConsultAnswer(BaseModel):
    decision: Literal["answer", "escalate"]
    answer: str = ""                               # required iff answer
    escalation: EscalationRequest | None = None    # required iff escalate

class Verdict(BaseModel):
    decision: Literal["accept", "changes_requested", "escalate", "supersede"]
    summary: str
    issues: list[Issue] = []                       # required non-empty iff changes_requested; empty iff accept
    residual_risks: list[str] = []
    new_tasks: list[NewTask] = []                  # required non-empty iff supersede
    scope_changes: list[str] = []
    escalation: EscalationRequest | None = None    # required iff escalate
```

Every "required iff" rule is a `model_validator`. `shikiru report` prints the violations and exits with code 2.
