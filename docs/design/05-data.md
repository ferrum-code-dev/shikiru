# 05 · Data

## 1. Configuration

**Sources**, merged deeply, lowest first:
1. packaged `config/defaults.toml`
2. `.shikiru/config.toml`
3. `--set key.path=value` (value parsed as TOML)

`shikiru --json doctor` prints a `DoctorReport` with overall `ok` and named diagnostic checks; it does not dump effective settings. `--project`, `--json` and `--set` are global CLI options and precede the command. Core keys are validated by pydantic (`extra="forbid"`). Each `[agents.<name>.options]` table is a plugin-owned extension point validated by that plugin's factory at `start` and `doctor`; a plugin may define agent-specific access options when its provider requires them. Core Shikiru has no provider-access configuration or credential handling. For the current Claude and Codex plugins, Operators configure provider access through agent-native files and environment, which those plugins pass through to their child processes.

The full configuration reference (the wizard writes the values the operator selects; `init --yes` writes detected project values and editable model placeholders):

Plugin option tables remain owned by their agent factory. The optional billing declaration is configured only under `[agents.<name>.options]`, for example `billing_mode = "subscription"`; the Claude and Codex factories validate `api`, `subscription` and `unknown`, and normalize an explicit value with source `operator_config`. Without a plugin option or trustworthy native report the mode and source remain unknown. No core billing/access table or credential setting is added.

Task 93 leaves the ordered `[dashboard].widgets` setting and its shipped default unchanged. Home placement is an optional widget capability declared as `placement: WidgetPlacement` in `dashboard.contract`, not a new TOML layout option. Built-ins declare their Focus identity, task-list, attention, project-usage, resource, activity, detailed-usage, diagnostics or workflow placement; a plugin that omits placement remains reachable in Extensions. Missing or broken configured entries, and unsupported placements, fail validation with the configured widget name.

```toml
[project]
name = "ferrum"                                 # tmux session "[shikiru] ferrum"; default: folder name
trackers = ["tasks/plan.md", "tasks/todo.md"]

[repo]                                          # the project root is the repository root (D20)
base = "main"                                   # omit = branch checked out at task start
caches = ["node_modules", "**/node_modules", ".venv", "target"]
remote = "origin"                               # push target when integration = "pr"

[sessions]
ssh_timeout_seconds = 30                        # maximum wait for remote `ls`

[orchestration]
max_parallel = 2
max_iterations = 5
integration = "merge"                           # merge | pr
poll_seconds = 2
shutdown_seconds = 15                           # orderly stop bound before the engine is ended by force (03 §7)
dispatch_budget_seconds = 10                    # total wait of one tick for task roots it starts (fetch, copy)

[orchestration.resources]
max_cpu_pct = 85
min_free_ram_gb = 4
sample_seconds = 2
cpu_window_seconds = 30

[orchestration.timeouts]                        # seconds
stall = 1200                                    # no stream event while no tool runs
tool = 7200                                     # no stream event while a tool runs
max_quota_wait = 28800
integration_wait = 1800
pr_wait = 604800                                # an open PR unmerged this long escalates
helper = 300                                    # maximum duration for helper turns
cache_copy = 600                                # one `repo.caches` copy-on-write copy
git_command = 120                               # one git call (status, worktree add/remove, rebase, merge)

[orchestration.faults]                          # a failed tick is retried with backoff (02 §1)
backoff_initial_seconds = 2
backoff_max_seconds = 60
unexpected_fault_limit = 10                     # consecutive unexpected faults that stop the engine
stop_notify_seconds = 30                        # time allowed to deliver the final notification
repeat_notify_seconds = 300                     # one `engine_fault` event per fault per this long

[orchestration.network]                         # remote calls of integration = "pr"
fetch_timeout_seconds = 30                      # a base fetch (task root build, merged-base check)
poll_attempts = 8                               # `gh pr view` tries per poll before it escalates
pr_poll_seconds = 300                           # between two polls of an open PR
pr_close_attempts = 5                           # `gh pr close` tries per engine start for a cancelled task

[escalation]
tried_lines = 8                                  # lines of the packet's What was tried digest (03 §8)

[agents.claude]                                 # table name = plugin entry point
[agents.codex]

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

# `roles.helper.ladder` must contain exactly one rung until helper fallback is implemented.

[dashboard]
widgets = ["header", "escalations", "strips", "queue", "dialogue", "usage", "resources", "docker", "disk", "faults"]
animations = true                               # false renders static frames (06 §6.5)
docker_refresh_seconds = 5
docker_timeout_seconds = 5
docker_scope = "project"                        # "project" counts containers in the project root or its worktrees (06 §5.4); "all" counts every container
disk_refresh_seconds = 60                       # Disk section free-space sample cadence (06 §5.4)
disk_size_refresh_seconds = 300                 # how often the Disk section re-walks the project's .shikiru folder for its size (06 §5.4)
disk_warn_free_pct = 10                         # free space below this percentage is amber
disk_alert_free_pct = 5                         # free space below this percentage is red
repo_status_cache_seconds = 10
timeline_page_size = 20                         # task evidence rows returned per page, maximum 49
timeline_scan_rows = 256                        # normalized signal rows scanned per role and page, maximum 1024
timeline_refresh_seconds = 1                    # latest task evidence page refresh interval
recent_task_limit = 30                          # most recent terminal tasks the Home Recent list can page through (History is unlimited); at least recent_task_window
recent_task_window = 3                          # Recent tasks shown at once
last_words_chars = 4000                         # safety cap on the stored last agent message shown in Now
now_max_lines = 6                               # lines Now wraps the last message over; ends with … only when cut
now_compact_lines = 2                           # the same in the compact and tiny layouts
results_commits = 3                             # commits the task Results list before "+N more"
action_confirmation_seconds = 5                 # how long the ✓ after a queued Message, Pause, Resume or Cancel stays visible
layout = { split = "v", panes = [
  { pane = "leader", size = 40 },
  { pane = "implementer", size = 40 },
  { pane = "shell", size = 20 },
] }

[dashboard.motion]
refresh_hz = 4                                  # at most 4
stamp_flash_milliseconds = 400
escalation_pulse_hz = 1
task_event_limit = 50                           # events read per task for the motion state
merge_event_limit = 20

[dashboard.titles]                              # fields: 06 §5.3
window = "{task_id} {title:.24}"
leader = "{role_label} · {agent}:{model}/{effort} · iter {iteration} · ctx {context} · {cost} {flags_text}"
implementer = "{role_label} · {agent}:{model}/{effort} · iter {iteration} · ctx {context} · {cost} {flags_text}"
system = "Shikiru · {project} · {active}/{max_parallel} active · {queued} queued"
shell = "Project folder · {cwd}"
context_warn_pct = 40                           # agent pane-title context turns amber from this whole percentage (06 §5.3)
context_alert_pct = 60                          # ... dusty rose from this one; must be above context_warn_pct

[dashboard.gauges]                              # subscription-limit (quota) gauges: 06 §5.4, §6.1
quota_warn_pct = 50                             # amber from this whole percentage
quota_alert_pct = 90                            # dusty rose from this one; must be above quota_warn_pct

[notify]
command = ""                                    # optional; event JSON on stdin for escalation and task_done
timeout_seconds = 30                            # the hook is killed (with its process group) after this

[logging]
engine_log_max_bytes = 10485760                 # rotate `logs/engine.log` at this size
engine_log_backups = 3                          # rotated files kept (`engine.log.1` ...)

[retention]                                     # finished history kept; see §3 Growth
interval_seconds = 3600                         # one pruning pass per this long
finished_task_days = 30                         # run directory and events of a done or cancelled task
event_days = 90                                 # engine-level events (not fault records)
command_days = 30                               # answered commands

[doctor]
probe_timeout = 180                             # seconds per `doctor --probe` live turn
probe_stream_tail_lines = 20                    # stream lines shown when a probe fails
```

Projects without an explicit `dashboard.layout` inherit this packaged task-window layout. Existing
project files that declare a layout keep that override unchanged; remove or edit the override to
adopt the packaged default. This creates Leader, Implementer and Project folder panes only; Home
runs in the separate `dashboard` window and the default task window has no system pane. No
configuration migration rewrites an operator's layout.

Packaged Leader and Implementer title defaults use readable role words and `flags_text`. Explicit
project templates that use `glyph` or glyph-prefixed `flags` keep those fields; Shikiru does not
rewrite project configuration.

Older project files may still set `stream_active_seconds`; it is accepted for configuration
compatibility but deprecated and no longer affects live-agent motion. Activity is based on a
recorded current turn, engine liveness, holds and diagnosed stall deadlines, while signal age
continues to report the last recorded observation.

**`submit` flags**, applying to that task only: `--mode implement|plan`, `--complexity low|normal|high` (omitted: triage decides), `--priority N`, `--after T-n` (repeatable), `--leader a:m:e`, `--implementer a:m:e`.

**Protocol constants** are named module constants, not configuration:

| Constant | Value |
|---|---|
| `NUDGE_LIMIT` | 2 |
| `CONSULT_LIMIT` | 3 |
| `CONFLICT_ROUNDS` | 2 |
| `MAX_DISPATCH_FAILURES` | 3 |
| Stall retries per turn | 2 |
| `CLASSIFY_CONFIDENCE` | 0.8 |
| Unavailable backoff | 30 s / 2 min / 5 min |
| `QUOTA_PROBE` | 15 min |
| `INTEGRATION_RETRY` | 2 min |
| Command poll | 250 ms |
| Dashboard change poll | 500 ms |
| `TURN_STOP_GRACE` | 10 s |
| `GIT_STOP_GRACE` | 10 s |
| `START_TIME_TOLERANCE_SECONDS` | 10 s |
| `STDOUT_DRAIN_TIMEOUT` / `STDERR_DRAIN_TIMEOUT` | 2 s / 2 s (03 §4.2) |
| `STDERR_KEPT_BYTES` | 1 MiB of a process's stderr (04 §2) |
| `TASK_TITLE_MAX_LENGTH` | 60 characters |
| `BUSY_TIMEOUT_MS` | 5000 ms (§2) |
| Escalation evidence | 60 view lines per role |
| Command ack wait | 10 s |
| `QUOTA_MARGIN` | 60 s |
| Dialogue length | 20 lines |
| MCP `wait_for_events` max timeout | 300 s |
| Dashboard motion cap | 4 Hz |

The dashboard keeps this refresh cadence when `animations = false` and under `NO_COLOR` or `TERM=dumb`; those settings change presentation, not countdown or durable-state freshness. Motion work is limited to current projections, ages, countdowns and bounded normalized signal tails. Timeline pages keep their own interval. Report/facts files are cached by their file identities, escalation packet text by file identity, and parsed packet evidence by packet content, so an unrelated database revision does not reread or reparse those files on every motion tick. Layout limits remain named UI constants; no dashboard breakpoint setting is exposed.

**Operational intervals** use the defaults below and can be set in the project config:

| Setting | Default |
|---|---|
| Resource sample cadence / CPU averaging window | 2 s / 30 s |
| Header git data cache | 10 s |
| Docker sample cadence / command timeout | 5 s / 5 s |
| Disk free-space cadence (`dashboard.disk_refresh_seconds`) / `.shikiru` size cadence (`disk_size_refresh_seconds`) / free-space thresholds (`disk_warn_free_pct`, `disk_alert_free_pct`) | 60 s / 300 s / amber below 10 % free, red below 5 % free |
| Notification hook timeout (`notify.timeout_seconds`) / informational queue | 30 s / 64 events (urgent events are never dropped, 06 §5.7) |
| Engine shutdown bound (`orchestration.shutdown_seconds`) | 15 s: the time SIGTERM, SIGINT or `shikiru stop` get to end turns and release the engine lock before the engine is ended by force (03 §7) |
| Failed-tick retry backoff (`orchestration.faults`) | 2 s doubling to 60 s; 10 consecutive unexpected faults stop the engine; final notification 30 s; one `engine_fault` event per fault per 300 s (`repeat_notify_seconds`) |

## 2. State DB (`.shikiru/state.db`)

- SQLite WAL mode, `busy_timeout = 5000`.
- Migrations tracked by `PRAGMA user_version`. An open of an outdated database reads the version and applies each pending migration inside one immediate transaction, so two processes opening it at once (engine and CLI after an upgrade) apply every migration exactly once; an up-to-date database is opened without taking the write lock. A migration rewrites stored JSON when a model gains a required field (e.g. `repo.base_ref` defaults to the stored `base`, the ref tasks of that era started from), so models stay strict. `idx_events_kind` (migration 006) bounds the `command_applied` replay lookup (03 §7) to the events of one kind; `notification_cursor` (migration 007) holds the single row of the delivered-up-to event id (06 §5.7); `usage_reported` (migration 008) distinguishes new unreported runs, measured reports and legacy rows with unknown provenance. Migration 009 adds `runs.billing_mode` and `runs.billing_source`, both `TEXT NOT NULL DEFAULT 'unknown'`; existing rows therefore remain `unknown`/`unknown`. The engine's `RunStore` writes the normalized declaration once for its run/session; readers do not apply current plugin settings to historical rows. These fields are additive and do not replace `cost_basis`.
- Readers use short read transactions and `PRAGMA data_version` to detect changes.
- Timestamps are UTC ISO-8601.

```sql
CREATE TABLE sequences (name TEXT PRIMARY KEY, value INTEGER NOT NULL);   -- task, escalation, message

CREATE TABLE tasks (
  id TEXT PRIMARY KEY, seq INTEGER UNIQUE NOT NULL,
  parent_id TEXT REFERENCES tasks(id), origin TEXT NOT NULL,
  title TEXT NOT NULL, request TEXT NOT NULL,
  mode TEXT, complexity TEXT NOT NULL DEFAULT 'normal', complexity_explicit INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL, step TEXT, status_reason TEXT,
  holds TEXT NOT NULL DEFAULT '[]',               -- ["paused","intervention"]
  iteration INTEGER NOT NULL DEFAULT 0,
  counters TEXT NOT NULL DEFAULT '{}',            -- rejections_on_rung, consults, conflict_rounds; a turn's transition writes only what it changed, an operator rung wins (03 §3)
  rung_index TEXT NOT NULL DEFAULT '{}',          -- {role: n}; never overwritten from a stale snapshot (03 §3)
  overrides TEXT NOT NULL DEFAULT '{}',           -- {role: {agent, model, effort}}
  repo TEXT,                                      -- {base, base_ref, base_sha, branch}; null until the task root exists
  slot INTEGER, window_id TEXT, panes TEXT NOT NULL DEFAULT '{}',
  wait_reason TEXT, requeued_at TEXT, summary TEXT,
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE TABLE task_deps (task_id TEXT NOT NULL REFERENCES tasks(id), depends_on TEXT NOT NULL REFERENCES tasks(id),
  PRIMARY KEY (task_id, depends_on));

CREATE TABLE runs (                               -- one agent session per task role
  id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), role TEXT NOT NULL,
  agent TEXT NOT NULL, model TEXT NOT NULL, effort TEXT,
  session_id TEXT, resume_command TEXT, last_message TEXT,
  status TEXT NOT NULL,                           -- live | taken_over | closed
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL, cost_basis TEXT NOT NULL DEFAULT 'unknown', context_used_pct REAL,
  usage_reported INTEGER,                         -- 0 = not yet reported, 1 = reported, NULL = legacy provenance unknown
  billing_mode TEXT NOT NULL DEFAULT 'unknown', billing_source TEXT NOT NULL DEFAULT 'unknown',
  started_at TEXT NOT NULL, ended_at TEXT
);

CREATE TABLE agent_blocks (agent TEXT PRIMARY KEY, until TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE quotas (agent TEXT NOT NULL,                  -- only windows an agent reported (04 §1)
  window TEXT NOT NULL, used_pct REAL NOT NULL, resets_at TEXT,
  observed_at TEXT NOT NULL, PRIMARY KEY (agent, window));

CREATE TABLE escalations (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), step TEXT, reason TEXT NOT NULL,
  headline TEXT NOT NULL, packet_path TEXT NOT NULL, options TEXT NOT NULL,
  status TEXT NOT NULL,                           -- open | resolved
  resolution TEXT, created_at TEXT NOT NULL, resolved_at TEXT
);

CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, task_id TEXT, kind TEXT NOT NULL,
  message TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}');

CREATE TABLE notification_cursor (id INTEGER PRIMARY KEY CHECK (id = 1), event_id INTEGER NOT NULL);

CREATE TABLE commands (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',        -- pending | done | rejected
  result TEXT);
```

**Usage records** are agent session totals from `UsageReported` (04 §1). A newly started run has `usage_reported = 0`; a received report overwrites its cumulative token, cost and context fields and sets the marker to `1`. Migration 008 leaves pre-existing rows `NULL`. A positive legacy token field is evidence that the old recorder wrote the usage row, so its token values are retained and labelled legacy recorded; all-zero legacy token fields without that evidence remain unknown, never measured zero. Cost is independent of token provenance: a legacy non-null amount with a known billed or API-equivalent basis remains a priced legacy record, including `$0.00`; a missing or unknown basis remains unknown. A stored legacy context percentage remains visible as legacy reported context. A quota report does not mark token usage as reported.

A new completed-turn `usage_delta` records a known cost basis only when both cumulative amounts are present, use the same known basis, and do not decrease. Otherwise its turn cost and basis are unknown, while recorded token deltas remain available. Legacy numeric turn-cost deltas without basis metadata keep an unknown basis; readers never infer it from the current run's cumulative cost.

Dashboard usage has two explicit scopes: all recorded project runs or the selected task's runs. Per-agent token totals combine explicit reports with positive legacy token evidence and show explicit, legacy and unknown run coverage. Billed and API-equivalent costs are summed separately from recorded priced amounts; each subtotal carries its priced-run count and legacy-record count, while every run without priced monetary evidence counts toward unknown-cost coverage. That generic monetary count stays independent of billing mode: a subscription run with no task amount has unknown cost, but the declaration does not classify it as missing API charge coverage. Its task session shows the declaration and source beside the amount. A zero amount from any recorded priced run remains `$0.00`; an absent subtotal remains unknown. A subscription declaration does not turn an absent per-task amount into zero, and a separately reported billed amount remains priced even for a subscription session. No time period or project spend is inferred. Task detail keeps each session's billing declaration and last reported context beside the session status; legacy and closed-session values are labelled as recorded context, not current. Quota windows retain the plugin's `observed_at` and optional reported reset, so their age and reset countdown derive from those timestamps.

Dashboard resource samples retain the sampler's actual observation time. The sampler consumes a minimal classified-root projection containing only verified process identities, split into Shikiru and Agents roots; it does not inspect provider or executable names. Host CPU is the configured CPU-window average, and host used RAM and total RAM come from the same `psutil.virtual_memory()` observation. Shikiru CPU/RSS covers the verified engine, dashboard process, pane marked `@shk_home=1` regardless of its window name, exact recorded task-role panes and a configured task `system` pane only when its recorded window and explicit current-project `dash --task <id>` start command agree. Shell leaves and custom operator panes are excluded, including restored `LayoutGuard` system shells. Agents CPU/RSS covers live headless turns from their role `turn.pid` identity and exact task-role panes whose matching run is taken over. Both scopes include descendants, deduplicate by PID/start identity, and normalize CPU to host logical-CPU capacity; a process first seen by a sampler (no CPU baseline) is credited its lifetime-average CPU (`cpu_times` over its age, floored at 0.1 s) instead of zero, so short-lived children are counted; the compatible aggregate instance fields equal their sum when both scopes are classified. A legacy aggregate without classified roots keeps its aggregate fields but leaves split attribution unknown. Missing roots and an empty process tree are not measured zero. A failed sample shows its error and keeps the last complete timestamped observation as stale; with no prior observation it is failed. Docker records the successful `docker stats` observation time, reports an empty result separately from an unavailable CLI or daemon, and preserves the last successful result as stale after a later failed sample. Diagnostics reads open engine fault streaks from the recorded `engine_fault` and `engine_recovered` events.

The Focus summary reuses these same readings as three groups: Host CPU/used-RAM, Shikiru CPU/RSS excluding agents, and Agents CPU/RSS for this project. Host RAM is used/total against the sampled host total; Shikiru and Agents show process RSS labelled `RSS` with no denominator, not host-used RAM or a per-process limit. The compact line preserves the sample's timestamp/age and state; it adds no sampler, does not add host and process measurements together, and does not refresh expensive values from the motion callback. Its meaning and compact display are specified in [Compact Operator TUI](../tui-focus.md#8-architecture-and-configured-plugins).

The engine uptime projection reads liveness and verified process identity from `EngineLock`, associates the current holder with `engine_started` evidence, and calculates elapsed time with injected `Clock`. New `engine_started` and `engine_stopped` events carry `engine_pid` and `engine_process_started_at`, identifying the exact lock-holding process across restarts. A held lock without matching recorded evidence means running with unknown uptime. A stopped engine never advances; a prior duration is shown only when the matching stop is recorded. Event IDs order lifecycle evidence when fake or system timestamps tie. Missing, future or inconsistent lifecycle events remain unknown. No heartbeat or uptime table is added.

The project usage summary reads persisted run/session rows across current and finished task histories through `StateReader.list_all_runs()`. Token totals distinguish explicit reports, positive legacy evidence and unknown sessions. Reported `billed` amounts form actual-charge totals; `api_equivalent` amounts remain separate estimates. Missing API-charge coverage counts only runs whose declared billing mode is `api`; subscription runs without a reported task charge are not missing API charges, and unknown-mode runs retain separate unknown coverage. Unknown-mode coverage is grouped by persisted source: `operator_config` is an explicit unknown choice, `provider_reported` is a provider-reported unknown, and `unknown` means the declaration was unavailable or not recorded, without identifying its historical cause. Billed amounts from any declared mode remain in the all-mode billed subtotal; only API-mode billed amounts enter the API-only subtotal. Project totals imply no date range or account-period spend. Subscription quota windows retain the agent-reported scope and observation timestamps; without recorded account identities, count declared subscription agents rather than accounts. The dashboard view contract is `RecordedUsageView`, with project and task scope, explicit token, monetary and billing-source coverage counts, separate cost bases and sorted declared subscription-agent names. See Focus §3.

**`EventKind`** (in `domain.events`; `events.data` carries typed fields):
- `task_queued`, `task_started`, `dispatch_failed` (attempt, error), `step_started`
- `turn_started`, `turn_completed` (agent, model, effort, run ID, outcome, summary, question, usage delta), `turn_failed`, `turn_stopped` (step, role, agent, model, effort and run ID: a started turn that ended without a result because a hold, a take over, a cancellation or an engine stop interrupted it, so the latest turn event of a task never claims a turn that no longer runs). New turn-scoped events also carry `turn`, the recorded turn-directory name; older rows may omit it. Older turn events may lack run ID and effort, so compact history only attributes a delta when the retained identity is complete.
- `nudge`, `helper_action`, `rung_changed`, `compacted`
- `quota_blocked`, `quota_cleared`
- `verdict` (turn, role and iteration when known), `scope_change`
- `hold_set`, `hold_cleared`, `taken_over`, `handed_back`, `takeover_lost` (`role`; the taken-over pane was lost with the tmux server or killed, so the role is handed back; notified), `operator_message` (`role`, `path`, `text`)
- `escalation`, `escalation_resolved` (`resolution`, and `text` for an answer)
- `integration_started`, `integration_waiting` (`paths`: the dirty checkout paths a local merge waits on; notified), `pr_opened` (url, branch, commits), `pr_withdrawn` (url, state: the PR of a cancelled task was closed or found not open)
- `task_retried`, `task_done`, `task_cancelled`
- `pane_restored`, `engine_started` and `engine_stopped` (`engine_pid`, `engine_process_started_at`; `reason` is also recorded when the engine stopped on a fault), `engine_fault` (`scope`, error, recoverable, repeat), `engine_recovered` (`scope`; the fault streak of that scope and task ended)

**Commands** are pydantic models in `domain.commands`, stored as `kind` plus a JSON `payload`:
- `Submit` (the command queue allocates its task id from `sequences` when appending)
- `Say`, `HandBack`, `TakeOver`, `Cancel`, `Retry` and `Resolve` carry the queue's row id as `command_id` when the engine reads them (`IdentifiedCommand`), and the event recording their effect repeats it, so a replay recognises the effect (03 §7)
- `Prioritize`, `Pause`, `Resume`, `Cancel`, `Retry`, `SetRung`, `Say`, `TakeOver`, `HandBack`, `Resolve`, `Stop`

`StateReader.command_history(limit, offset, status)` reads one newest-first page of command rows with the stored `pending`, `done` or `rejected` status and returned result. Each page is limited to 50 rows and may filter by stored status; its total count and `has_more` support continuation without returning unbounded history. `StateReader.command_status(command_id)` reads one row, and `StateReader.pending_command(command)` finds a matching pending payload. For `Submit`, the comparison ignores only `task_id`, which `CommandQueue` allocates when it appends the command; request, title, mode, complexity, priority, dependencies and rungs must still match. These reads do not write or acknowledge command rows. `OperatorQueries.command_history(expanded=False)` exposes the three newest rows; expanded reads are also paginated and capped at 50 rows per page. The operator-facing labels are **Pending**, **Applied** (`done`) and **Rejected**. **Sending** is local form state and is never stored. An acknowledgement wait timeout leaves a row `pending`; the row does not record whether its client first saw a stopped engine or elapsed acknowledgement wait. The dashboard shows current engine liveness alongside recovered pending rows without inferring that submission-time context.

`StateReader.task_timeline_events(task_id, limit, before, kinds, role, turn)` reads one bounded newest-first SQL page ordered by `(ts, id)` and continues with that same composite key. Role and turn filters use event JSON fields; newly emitted failure and verdict events record known execution role and iteration so they participate in those filters. `OperatorQueries.task_timeline()` merges that page with bounded reverse reads of each task role's normalized `view.jsonl`; page cursors retain the SQL key and each file's byte position together with the active filters. Each file scan is capped by `dashboard.timeline_scan_rows`, including when filters reject every recent row, and the cursor advances through skipped rows so older matches remain reachable. The timeline preserves engine event IDs and each file's append order; UTC timestamps order available evidence, then engine-before-agent and stable role/position keys break ties. An unknown or missing signal timestamp sorts after available timestamps without changing its per-file order. Missing, unreadable or changed sources are represented as evidence errors without hiding other roles or SQL task state; malformed or timestamp-less rows and a last JSONL row without its terminating newline also remain explicit evidence.

## 3. Run files

See 02 §5. `report.kind` names the report model of the turn. `helper.json` exists only for rebuilt reports. The single `TurnMonitor` consumes agent stdout and writes normalized signals to `view.jsonl`, one JSON object per line with `ts`, `turn`, `signal` type and its fields. `ToolFinished` rows may carry additive `outcome` and `exit_code` facts; old rows and plugins without reliable facts omit them. `shikiru view`, peek and the task evidence query read this recorded file; the dashboard does not subscribe to stdout or parse provider-specific records. Timeline reads run away from the UI thread, include only bounded reverse pages, and leave an incomplete trailing JSONL object visible until a later read observes its completed row.

**Growth** is bounded by retention (`[retention]`, `engine/retention.py`), run by the engine at most once per `interval_seconds` and never before it has reconciled and answered its pending commands. A task that is `done` or `cancelled` for more than `finished_task_days` loses its run directory (prompts, reports, raw streams, `view.jsonl`, transcripts) and its events, unless something unresolved still needs them: an open escalation (its packet and evidence), a worktree kept for unintegrated work (the task root still exists), a hold, or, for a cancelled task, a pull request still open. Historical task detail remains available after ordinary window and worktree cleanup while these retained run files and events exist; it shows recorded task status/reason and available reports, verdicts, integration events and git facts. If retention later removes evidence, the task row remains and missing evidence is shown explicitly. Engine-level events older than `event_days` and answered commands older than `command_days` go too; a pending command, the fault records (`engine_fault`, `engine_recovered`: `status` derives the open streaks from them), every event the notification cursor (06 §5.7) has not covered and the newest event and command row always stay, so ids are never reused. Run directories go before their events, so an interrupted pass is repeated. Task rows, escalations and run rows (usage totals) are kept, and the SQLite file does not shrink (freed pages are reused). `shikiru status` reports the number and total size of the run directories. The engine log is bounded separately: it rotates by size (`[logging]`) and keeps a fixed number of rotated files.

Task evidence export reads the selected task row, every page of its stored relationships, all retained validated reports and verdicts, every retained `facts.md`, persisted role sessions/rungs and task-scoped usage, plus the complete available task timeline. It uses the Task 83 timeline query and source cursors until there are no older rows; the export order is newest first and each event ID and role-file position remains recorded. SQL reads stay on the dashboard's owning thread. Report/facts and normalized-signal files and the final local write run off the UI loop.

Each source is read through its own query or file read. Relationship and timeline pagination exhaust those sources, but the export does not hold one SQLite snapshot or filesystem snapshot across the entire artifact. Sources can change between reads; the export records known unavailable or malformed evidence but does not claim a globally coherent snapshot.

The JSON format is `shikiru.task-evidence`, version `1`; Markdown carries the same metadata and renders each document section in its own dynamically sized JSON fence. Both include task ID, timezone-aware export time, request, recorded outcome/reasons, task and rung metadata, relationships, validated reports/verdicts, recorded facts, timeline, session usage provenance, task-scoped aggregate usage and `unavailable_sections`. Relationship and timeline page exhaustion is recorded separately from unavailable or incomplete source evidence. Unknown token/cost/context values are JSON `null`; reported zero tokens and known priced legacy zero costs remain zero. Billed and API-equivalent cost subtotals stay separate, with unknown run coverage. Relative destinations resolve from the project root; absolute destinations are used as entered. The selected parent directory must already exist, `~` is a literal path character, and no extension is appended. A destination is never overwritten without the explicit Replace choice. Publication uses a same-directory temporary file and an atomic no-replace or replace operation, so a failed write does not leave a partial destination.

Normalized signal excerpts are excluded by default and added only when requested. An engine-only export does not include detail signal tails. Missing reports/facts/sessions/timeline and unreadable, malformed, incomplete or missing normalized signal sources remain in `unavailable_sections` or in the recorded timeline evidence. The export contains only retained Shikiru evidence; it is not a full agent transcript and does not recreate a removed worktree.

## 4. Report models (`domain.reports`, pydantic → JSON Schema)

```python
class NewTask(BaseModel):      title: str; request: str; priority: int = 0
                               depends_on: list[str] = []   # existing "T-n" or "#k" = k-th task of the same list, 1-based; `shikiru report` rejects an out-of-range "#k"
class Commit(BaseModel):       sha: str; subject: str
class TestRun(BaseModel):      name: str; result: Literal["passed", "failed", "skipped"]; details: str = ""
class Issue(BaseModel):        severity: Literal["blocker", "major", "minor"]; description: str; location: str = ""
class Option(BaseModel):       label: str; consequences: str; recommended: bool = False; needs_input: bool = False   # needs_input: an ask for the Operator to provide something; old packets parse unchanged
class EscalationRequest(BaseModel): question: str; options: list[Option]      # exactly one recommended

class TriageReport(BaseModel):
    title: str                                     # ≤ 60 chars
    mode: Literal["implement", "plan"]
    complexity: Literal["low", "normal", "high"]
    acceptance_criteria: list[str]
    references: list[str]

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

Thirteenth review: `ESCALATION_RESOLVED` and `OPERATOR_MESSAGE` events carry the Operator's `text` in their data (read by the Decisions fold, which reads the latest 60 events of the verdict, escalation, resolution, scope, rung, message, take-over, hand-back, hold, retry, cancel and PR kinds); the task timeline has an internal `agent` category that drops bookkeeping signals for Agent activity; the `Events` and `Agent activity` folds have fixed meanings ([spec](../tui-feedback-thirteenth-review.md)). Fourteenth review ([spec](../tui-feedback-fourteenth-review.md)): the task workflow projection also reads the task's `task_queued`, `step_started`, `turn_started` and `verdict` events by kind (newest 200, merged with the 40-event detail window by event id) so stage states do not depend on that window, and the Decisions log is derived by `domain.decisions.derive_decisions` from the newest 60 events of its kinds (verdict, escalation and resolution, scope change, rung change, Operator message, take-over, hand-back, hold, retry, cancel, PR and done); no new storage.
