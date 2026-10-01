# 02 · Architecture

## 1. Processes

```
 Operator (human)           Operator (tool / AI)
   │ tmux attach               │ shikiru … --json │ shikiru mcp
   ▼                           ▼
┌──────────────── tmux session "[shikiru] <project>" ──────────────────────────────┐
│ window "shikiru": dash --home │ shell                                            │
│ window per task:  dash --task │ view leader │ view implementer │ shell           │
│                   (a view pane runs the agent's TUI while taken over)            │
└──────────────▲──────────────────────────────▲──────────────────▲─────────────────┘
               │ read state, append commands  │ tmux             │ tail runs/T/<role>/view.jsonl
          ┌────┴───────┐                ┌─────┴──────────────────┴────────────┐
          │ state.db   │◀─── state ─────│ engine daemon (asyncio, 1/project)  │
          │ SQLite WAL │─── commands ──▶│                                     │
          └────────────┘                └─────┬─────────────────▲─────────────┘
                                              │ spawn per turn  │ stream (stdout JSONL), exit
                                        ┌─────▼─────────────────┴─────┐
                                        │ agent CLI, headless          │── shikiru report ──▶ .shikiru/runs
                                        └──────────────────────────────┘
```

| Process | Started by | Writes |
|---|---|---|
| `shikiru engine` | `shikiru start` (detached) | All state tables |
| `shikiru dash`, `shikiru palette` | engine (panes), tmux popup | `commands` rows only |
| `shikiru <cmd>`, `shikiru mcp` | Operator | `commands` rows only; the `sequences` row when allocating a task id on submit |
| Agent turn processes | engine (child processes, one per running turn) | The task worktree; the agent's own session files |
| `shikiru report` | agent turn processes | Files under `.shikiru/runs` only |
| `shikiru view` | engine (role panes) | nothing |
| Helper calls | engine (subprocess) | nothing |

**Engine liveness and exclusivity** come from one mechanism: an exclusive `flock` on `.shikiru/engine.lock`, held for the engine's lifetime.
- Clients check liveness with a non-blocking lock attempt.
- No heartbeat or pid table exists.

**Engine faults.** One `TickGuard` wraps each scheduling tick (session check, startup reconciliation until it has run once (its steps and tasks isolated, 03 §7), `LayoutGuard.restore`, `Scheduler.run_once`, dispatch, ensuring every unheld running task has a live flow, 03 §7). A second `TickGuard` wraps each pass of the command loop (reading the pending commands, dispatching them, acknowledging them): a database fault there is retried on the same backoff, the command stays pending and is replayed (03 §7), and a handler failure still rejects only its own command with the cause. A third `TickGuard` (`engine loop`) wraps the loop's own reads and writes (the events that wake the scheduler and the notification cursor scan): a database fault there is retried on the same backoff and never stops the engine, nor does a database that is locked while the engine starts (the start sequence retries on the same guard). Recording a fault or its recovery is itself a database write that can fail: `FaultReporter` then logs it and leaves the record owed, so the next failure (or success, for a recovery) records it, and a locked database never turns a recoverable fault into an engine stop. Nothing else in the loop catches faults.
- *Recoverable*: `ShikiruError` (a failed tmux, git or `gh` call, a packet or config error), `OSError` and a SQLite `OperationalError` (database locked or full), but not an `InvariantError`. The tick is logged with its traceback and retried on a bounded backoff (`orchestration.faults`: `backoff_initial_seconds` doubling up to `backoff_max_seconds`), for as long as the fault lasts. Commands keep being processed meanwhile. Every fault is logged; it is recorded as an `engine_fault` event (notified, 06 §5.7) once per scope, task and error class, and again, with `data.repeat` counting its occurrences since the last record, only after `repeat_notify_seconds`. A persistent fault is thus re-notified at that bounded interval, a flapping one cannot flood the log, and a new error class is recorded at once. The backoff is capped iteratively, so it cannot overflow however long a fault lasts. A recovery is logged and, when the streak had been recorded, recorded as an `engine_recovered` event (scope and task id; never notified). A suppressed repeat after a recovery records nothing, so a flapping fault cannot flood recoveries either. A streak is *open* from its `engine_fault` until the matching `engine_recovered`; `status` and the dashboard derive the open streaks (first and last record, summed `repeat`, recoverable flag) from these two event kinds (06 §1). A fault event without a `scope` (a one-off cleanup or packet write failure) is not a streak.
- *Per-task faults*: `LayoutGuard.restore`, escalation restoration and startup reconciliation (03 §7) isolate each task, so a task whose window or pane cannot be restored is recorded as an `engine_fault` with its task id and skipped; the other tasks, scheduling and dispatch go on within the same tick. Such a fault does not count toward the tick's backoff or `unexpected_fault_limit`; it is re-recorded every `repeat_notify_seconds` for as long as it lasts, with `data.repeat` counting the ticks it failed.
- *Tmux loss*: each tick first checks the session; when the tmux server or session is gone, the engine recreates it (home window included, running `shikiru dash --home` without any `--set` overrides the engine was not given), after which `LayoutGuard` rebuilds every active task's window and view panes. Headless turn processes are not children of tmux and keep running. Decorations (titles, colours) are cosmetic: a tmux failure while setting them, a timeout included, never kills a turn, escalates or fails a flow or a command; a streak of failures is logged once.
- *Unexpected*: any other exception, and an `InvariantError` (a `ShikiruError` the engine raises when its own invariant does not hold: a task that vanished mid-operation, a running task with no step, a window it has no record of), is a programming error. It is logged with its traceback, recorded as an `engine_fault` event with `recoverable: false` (so the Operator is notified at the first one) and retried the same way; `unexpected_fault_limit` of them in a row end the engine.
- *Unrecoverable stop*: an unexpected-fault limit or any exception escaping the loop. The engine logs it, appends `engine_stopped` with `data.reason`, delivers that event to `notify.command` (within `stop_notify_seconds`) and exits non-zero. A requested stop records no reason and notifies nobody. An external supervisor restarts the engine (06 §5.8).

## 2. Layers and components

Dependencies point downward only. Each layer is an import-linter `layers` contract. `bootstrap` is the only composition root and may import anything.

```
bootstrap                                   build_engine(), build_operator(), build_dashboard()
───────────────────────────────────────────
cli · mcp · dashboard · agents.testing     interfaces (agents.testing is imported only by tests)
operator                                    queries, commands, event feed
engine                                      daemon, handlers, scheduler, flow, sessions, turns, integration, escalations
workspace · tmux · prompts · helper · ui · telemetry · agents.registry
store
domain
config · agents.contracts · agents.sdk
errors                                      lowest; no internal imports
process                                     imports errors
clock                                       no internal imports
───────────────────────────────────────────
plugins.agents.*  → agents.contracts, agents.sdk, process, clock, errors only; loaded by agents.registry from bootstrap
plugins.widgets.* → dashboard.contract, ui only (all data arrives as view models through WidgetContext, 06 §5.4); enforced as an exact whitelist
```

| Package | Responsibility | Key types |
|---|---|---|
| `errors` | Error hierarchy with exit codes | `ShikiruError`, `ConfigError`, … |
| `process` | The only subprocess gateway: argv lists, timeouts, typed errors from `errors`, async; `spawn` for long-running processes with stdin closed, line-streamed stdout and terminate; every `run` leads its own process group, which is SIGKILLed on a timeout or cancellation so no descendant (hook, MCP server) leaks; `run` can also stop gracefully on cancel, which mutating git calls do, so a cancelled call leaves neither a lock nor a running hook | `SubprocessRunner`, `CommandFailed` |
| `clock` | Injectable time for every timer | `Clock`, `SystemClock` |
| `config` | Merge defaults < project file < `--set`, validate core settings. Plugin `options` stay untyped here | `Settings`, `load_settings()` |
| `agents.contracts` | Agent plugin contract (04 §1) | `Agent`, `Turn`, `TurnSpec`, `HeadlessAgent`, signals, `Usage`, `QuotaWindow`, `Rung` |
| `agents.sdk` | Plugin building blocks | `TurnParser`, `StreamingTurn`, `ResetTimeParser` |
| `agents.registry` | Loads `AgentFactory` entry points plus factories registered by `bootstrap` (tests register the fake agent the same way through its own test-only package entry point) | `AgentRegistry` |
| `agents.testing` | Contract test kit | `AgentContractSuite` |
| `domain` | Pure rules, no I/O | `Task`, `Status`, `Step`, `Hold`, `Counters`, `EventKind`, command models, report models, `next_step()`, `pick_rung()`, `recovery.decide()`, `scheduling.pick_ready()`, `escalation.options_for()`, `activity.is_stalled()` |
| `store` | SQLite schema, migrations, stores per aggregate, read-only reader | `Database`, `TaskStore`, `RunStore`, `EscalationStore`, `QuotaStore`, `EventLog`, `CommandQueue`, `StateReader` |
| `workspace` | Task roots, worktrees, caches, git facts, pull requests through `gh` | `TaskRootBuilder`, `CacheCloner`, `GitFacts`, `Git`, `GitHub` |
| `tmux` | tmux client; session, windows, layout; pane decorations; pane respawn and liveness | `Tmux`, `SessionView`, `LayoutBuilder`, `PaneDecorator` |
| `prompts` | Jinja2 templates (packaged, overridable) | `PromptRenderer` |
| `helper` | Joshu calls | `Helper` |
| `ui` | Visual language and Rich renderables shared by CLI, dashboard and view | `ui.tokens`, `ui.glyphs`, `TaskStrip`, `SpeechBubble`, `SplashPanel`, `Gauge`, `StreamLine` |
| `telemetry` | Host/process and Docker sampling | `ResourceSampler`, `DockerSampler` |
| `engine` | Orchestration (below) | — |
| `operator` | Use cases for interfaces; project detection shared by `init --yes` and the wizard | `OperatorQueries`, `OperatorCommands`, `EventFeed`, `detectors` |
| `cli` / `mcp` / `dashboard` | Typer app (incl. `shikiru view`, the read-only live view of a role's turns); FastMCP stdio server; Textual apps and widget contract | `app`, `build_server()`, `DashboardApp`, `PaletteApp`, `DashboardWidget`, `DashboardViews` |

**Engine components**, each with one responsibility:

| Component | Responsibility |
|---|---|
| `Engine` | Main loop: poll commands → dispatch to handlers, tick the scheduler, supervise one coroutine per active task. Nothing else; `EngineLock` makes it a singleton per project |
| `TickGuard` | The single fault barrier around one engine tick: a recoverable fault becomes an `engine_fault` event, too many stop the engine (06 §5.7) |
| `FaultReporter` | Logs every fault and records the rate-limited `engine_fault` events (above) |
| `TaskSupervisor` | Starts, cancels and awaits the one `TaskFlow` coroutine of each active task; finishes terminal tasks through `TaskFinisher` |
| `TaskFinisher` | The terminal cleanup of a task: sessions, worktree, pull request and window (03 §1.2, §6) |
| `handlers/*` | One `CommandHandler` per command model, found through a registry `dict[type[Command], CommandHandler]` |
| `Scheduler` | Ready set, gates, slot assignment; persists `tasks.wait_reason`. Dependency-cancelled and repeated dispatch failures escalate through `DependencyEscalator` and `DispatchFailureEscalator` |
| `TaskFlow` | The sequencing layer: starts a task at its first step, runs step after step through the `StepRunner` until `integrate` or a stop, hands the `integrating` state to `IntegrationTurns` and escalates an unexpected error as `engine_error`. It holds no turn, recovery or persistence logic of its own |
| `StepRunner` | The boundary every turn shares, ordinary or integration: runs a step's turn through the `TurnLoop`, settles its report with the `AcceptanceGuard`, then applies `domain.steps.next_step` (transition or escalation). Also the escalation of a scope change the protocol rejects |
| `TurnLoop` | Runs one step's turn and its recoveries until a report, a hold or an escalation (`TurnsCompleted` or `TurnsStopped`): hold checks, context, session selection, resume of a submitted report, the turn itself |
| `AcceptanceGuard` | Records a completed turn and turns an `accept` or `supersede` verdict over a dirty worktree into a `changes_requested` verdict, so uncommitted work never ends a task's work (03 §2-3) |
| `IntegrationTurns` | The agent turns of integration: conflict, consult and post-rebase verify, run through the `StepRunner`; escalates blocked integration |
| `TurnContextPreparer` | Builds each turn's `TurnRequest` (template, prompt context, facts) from report history, operator answers and the `NextTurn` request recovery made |
| `TurnRecovery` | Carries out the `domain.recovery.decide` action for a failed turn: retries, back-off, quota blocks and waits, rung moves. Its `RecoveryStep` tells the loop what to do next, including whether the next turn continues or hands off; it never escalates |
| `EscalationFiler` | Escalates a task: gathers the evidence (default question and attempts, report history, workspace facts, pane tail), then persists the escalated state through the `TransitionWriter` and files it with the `EscalationRaiser` |
| `EvidenceCollector` | Reads the report history, workspace facts and view-log tail an escalation packet shows |
| `TransitionWriter` | The single place a `domain` `Transition` becomes stored task state, step and scope-change events and a refreshed title; every escalation persists through it |
| `TaskFacts` | Collects and renders a task worktree's git facts for prompts, escalation evidence and the acceptance check |
| `ReportHistory` | Reads and selects the reports recorded for a task's turns |
| `AgentPool` | Builds and caches one headless `Agent` per name |
| `SessionManager` | Decides the session a role's next turn runs on: new, resume (also with a new model), or new with `handoff`; stores `session_id` and the resume command on the run |
| `TurnProcesses` | The `turn.pid` record of each running turn, and ending the turn processes a dead engine left behind (03 §7) |
| `TurnMonitor` | The single consumer of one `Turn.signals()`: persists session facts (session id, usage, quota, last message), appends the role's view log, updates pane decorations, and stops the turn when `domain.activity.is_stalled` says so |
| `TurnRunner` | One turn: turn dir, queued operator messages, `start_turn`, await the monitor, report validation, nudge turns, Helper rebuild and failure classification. Returns a `TurnResult`; never escalates |
| `Integrator` | Rebase and conflict hand-off, then delegates to an `IntegrationStrategy`: `LocalMerge` or `PullRequest` |
| `EscalationRaiser` | Builds the packet and records the escalation |
| `TaskTitles` / `CosmeticDecorator` | Pane and window titles and state decorations; the decorator swallows tmux failures so cosmetics never fail an engine path (06 §5.3) |
| `Reconciler` | At engine start: restores windows, ends orphaned turn processes, re-runs in-flight steps |
| `LayoutGuard` | Keeps windows and panes matching the DB (recreates killed or dead panes; a dead taken-over pane is handed back); a task it cannot restore is recorded and skipped |
| `SessionGuard` | Recreates the project's tmux session when the tmux server or session is lost (06 §5.1) |
| `Takeover` | Take over and hand back one task role (03 §9) |
| `Notifier` | tmux message plus `notify.command` on escalation and task completion |
| `PullRequestClosing` | One supervised task closing the open pull requests of cancelled tasks off the command loop, with retry (03 §6) |
| `NotificationDelivery` | Bounded queue and one supervised task delivering notifications off the command loop (06 §5.7) |

**Enforced rules** (by tool, not convention):
- import-linter: the layers above; the engine boundary (a `forbidden` contract: `engine` may not import `agents.registry`, `agents.sdk` or `agents.testing`, so of the agents packages it imports only `agents.contracts`; it is not a whitelist of every package the engine may import); the widget plugin boundary (a denylist that names every other top-level package and every `dashboard` module except `contract`, so only `dashboard.contract` and `ui` remain importable); the agent plugin boundary; and an `independence` contract so `plugins.agents.claude` and `plugins.agents.codex` never import each other (they share code only through `agents.sdk`);
- the widget, agent plugin and engine boundaries are denylists, so a new package would pass silently: `tests/integration/test_import_contract_completeness.py` fails when a top-level module under `src/shikiru`, a module under `shikiru.agents` (agent plugins and engine), a module under `shikiru.dashboard` or `shikiru.plugins` (widgets), is neither on the contract's forbidden list nor in the test's explicitly allowed set, forcing a decision when one is added;
- ruff `banned-api`: `sqlite3` only in `store`, `subprocess`/`asyncio.create_subprocess_*` only in `process`, and the sleep, wall-clock, monotonic, CPU-time and performance-counter entry points (`time.sleep`, `asyncio.sleep`, `time.time`, `time.monotonic`, `time.perf_counter`, `time.process_time`, `time.thread_time` and their `_ns` forms, `datetime.now`, `utcnow`, `today`) only in `clock`. Per-file exceptions: `process.py` and `clock.py` (own the boundary), `tests/support/waiting.py` (the one place tests poll), the store test helpers and `tests/integration/test_store_database.py`, and the E2E results file name (`tests/e2e/conftest.py`, `tests/integration/test_e2e_results.py`). `store` has no file exemption: its eight `import sqlite3` lines carry a line-level `# noqa: TID251`, so time and subprocess use inside `store` is still rejected;
- the comment check (`poe comments`, 07 §2.8);
- the literal check (`poe literals`, 07 §2.5).

**Review conventions** (no tool checks them): nothing outside `plugins/agents/*` names an agent CLI, stream or session-log format, or quota source, since import-linter cannot see semantic coupling; and event-loop time (`loop.time()`) is not used outside `clock`.

**Documented exceptions to "time only via `Clock`"** (`asyncio.wait_for` and `asyncio.timeout` are not on the `banned-api` list, because tests use them everywhere to bound real waits and a per-line exemption of that many sites would hide real violations). In `src` they bound one real I/O wait with a real deadline and are never a schedulable timer: `process.py` (graceful-stop grace and call timeouts), `engine/notification_delivery.py` (`deliver_final`: the one `notify.command` run at shutdown, when the loop is over), `operator/probe.py` and `agents/testing/contract_suite.py` (a live agent turn bound in the doctor probe and the contract suite). A new use in `src` outside this list is a review finding.

## 3. Project structure

```
pyproject.toml
src/shikiru/
  bootstrap.py  process.py  clock.py  errors.py  paths.py  private.py  view_log.py  __main__.py
  config/       settings.py loader.py defaults.toml
  domain/       tasks.py steps.py ladder.py recovery.py scheduling.py escalation.py activity.py commands.py events.py reports.py runs.py fencing.py faults.py motion.py
  store/        database.py migrations/ tasks.py runs.py escalations.py quotas.py events.py commands.py reader.py retention.py notification_cursor.py
  agents/       contracts.py registry.py
    sdk/        turn.py reset_time.py
    testing/    contract_suite.py resume.py
  workspace/    roots.py base_refs.py caches.py facts.py git.py github.py messages.py reports.py
  tmux/         client.py session.py layout.py decorator.py
  prompts/      renderer.py templates/
  helper/       helper.py models.py
  ui/           tokens.py age.py size.py glyphs.py status.py strip.py bubbles.py splash.py gauge.py stream.py
  telemetry/    resources.py docker.py disk.py
  engine/       daemon.py tick_guard.py fault_reporter.py session_guard.py supervisor.py lifecycle.py scheduler.py dependency.py agents.py sessions.py
                flow.py step_runner.py turn_loop.py acceptance.py turn_context.py turn_execution.py recovery.py
                monitor.py turns.py turn_processes.py
                escalation_filing.py evidence.py escalations.py transitions.py task_facts.py history.py events.py
                takeover.py integration.py integration_turns.py reconcile.py layout_guard.py titles.py cosmetic_decorator.py
                notifier.py notification_delivery.py notification_feed.py pull_request_closing.py root_preparation.py retention.py
    handlers/   base.py tasks.py operator.py escalations.py engine.py
  operator/     queries.py commands.py services.py events.py strip_views.py sessions.py lifecycle.py detectors.py probe.py doctor.py plugin_checks.py
  cli/          app.py context.py report.py view.py render.py
  mcp/          server.py
  dashboard/    app.py contract.py services.py views.py palette.py wizard.py activity.py motion.py session_picker.py
  plugins/
    agents/claude/  agent.py turn.py headless.py options.py context.py hook.py
    agents/codex/   agent.py turn.py headless.py options.py home.py quota.py usage.py sandbox.py sandbox_probe.py
    widgets/        header.py strips.py escalations.py queue.py dialogue.py usage.py resources.py docker.py
tests/  unit/ integration/ e2e/ support/ (tmux fixture, fake clock, wait_until) fixtures/ (sample-project, agent samples)
  fake_agent/   test-only package (uv dev dependency) with its own `shikiru.agents` entry point `fake`
```

`init --yes` and the interactive wizard share `operator/detectors.py`: base branch, agents, trackers, caches.

## 4. Plugins

Registered through entry points in `pyproject.toml`:

```toml
[project.entry-points."shikiru.agents"]      # target: AgentFactory (04 §1)
claude = "shikiru.plugins.agents.claude.agent:create"
codex  = "shikiru.plugins.agents.codex.agent:create"
[project.entry-points."shikiru.widgets"]     # target: DashboardWidget class (06 §5.4)
strips = "shikiru.plugins.widgets.strips:StripsWidget"
# … header, escalations, queue, dialogue, usage, resources, docker
```

A plugin that is missing or broken fails `start` and `doctor` with its name and error.

## 5. Runtime layout on disk

```
<project>/.shikiru/                 # added to the repository's .git/info/exclude
  config.toml  prompts/ (optional overrides)  state.db  engine.lock  logs/engine.log (+ .1 .. .N rotated)
  worktrees/T-12/                   # the task's worktree
  runs/T-12/
    leader/  implementer/           # current_turn symlink, view.jsonl (view log), turn.pid while a turn runs
    turns/NN-<step>-i<iteration>/   prompt.md report.schema.json report.kind report.json facts.md helper.json
                                    <role>.stream.jsonl <role>.stderr.log (raw agent output)  messages/ (delivered)
    messages/<role>/NNN-c<id>.md   # operator messages queued for the role's next turn
    escalations/E-3.md
  probe/repo/  probe/run/           # scratch repository and run dir reused by `doctor --probe`
```

**Permissions** (`shikiru.private`): every directory Shikiru creates under `.shikiru/` is `0700`, and every file the engine or a client writes there (the `config.toml` written by `init`, state DB with its WAL and SHM, engine log, prompts, reports, packets, queued messages, `view.jsonl`, raw streams) is created owner-only, so the state, the agents' transcripts and the operator's requests are not readable by other users of the host. Directory creation tolerates a concurrent creator of the same path (`mkdir` with `exist_ok`, no check-then-create). Files that agent processes or git create inside those directories follow the process umask; the `0700` directories keep them private. Starting the engine tightens an existing `.shikiru/` (directories to `0700`, group and other access removed from files, symlinks untouched) and logs how many paths it changed; task worktrees hold the project's own files and only the `worktrees/` directory itself is tightened. Nothing outside `.shikiru/`, and never the project repository, is chmod-ed. The tmux socket is tmux's own default (`/tmp/tmux-<uid>/`, created `0700` by tmux), so it needs nothing from Shikiru.
