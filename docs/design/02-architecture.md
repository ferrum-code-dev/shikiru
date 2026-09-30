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

**Engine faults.** One `TickGuard` wraps each scheduling tick (session check, startup reconciliation until it has succeeded once, `LayoutGuard.restore`, `Scheduler.run_once`, dispatch, ensuring every unheld running task has a live flow, 03 §7). Nothing else in the loop catches tick faults.
- *Recoverable*: `ShikiruError` (a failed tmux, git or `gh` call, a packet or config error) and `OSError`. The tick is logged with its traceback and retried on a bounded backoff (`orchestration.faults`: `backoff_initial_seconds` doubling up to `backoff_max_seconds`), for as long as the fault lasts. Commands keep being processed meanwhile. The first fault of a streak is recorded as an `engine_fault` event (notified, 06 §5.7); a recovery is logged.
- *Tmux loss*: each tick first checks the session; when the tmux server or session is gone, the engine recreates it (home window included, running `shikiru dash --home` without any `--set` overrides the engine was not given), after which `LayoutGuard` rebuilds every active task's window and view panes. Headless turn processes are not children of tmux and keep running. Decorations (titles, colours) are cosmetic: a tmux failure while setting them is logged and never fails a flow or a command.
- *Unexpected*: any other exception is a programming error. It is logged with its traceback, recorded as an `engine_fault` event with `recoverable: false` (so the Operator is notified at the first one) and retried the same way; `unexpected_fault_limit` of them in a row end the engine.
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
plugins.widgets.* → dashboard.contract, ui only (all data arrives as view models through WidgetContext, 06 §5.4)
```

| Package | Responsibility | Key types |
|---|---|---|
| `errors` | Error hierarchy with exit codes | `ShikiruError`, `ConfigError`, … |
| `process` | The only subprocess gateway: argv lists, timeouts, typed errors from `errors`, async; `spawn` for long-running processes with stdin closed, line-streamed stdout and terminate; `run` can own a process group (hooks) or stop gracefully on cancel (mutating git) | `SubprocessRunner`, `CommandFailed` |
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
| `Engine` | Main loop: poll commands → dispatch to handlers, tick the scheduler, supervise one coroutine per active task. Nothing else |
| `handlers/*` | One `CommandHandler` per command model, found through a registry `dict[type[Command], CommandHandler]` |
| `Scheduler` | Ready set, gates, slot assignment; persists `tasks.wait_reason` |
| `TaskFlow` | The sequencing layer: runs a task's steps (`triage`, `plan`, `implement`, `consult`, `verify`, `integrate`), calls `domain.steps.next_step` at every turn boundary and hands integration turns the same boundary (hold check, dirty-worktree rejection of an accepting verdict, transition or escalation) that ordinary steps use |
| `TurnContextPreparer` | Builds each turn's `TurnRequest` (template, prompt context, facts) and remembers whether recovery wants the next turn continued or handed off |
| `TurnRecovery` | Carries out the `domain.recovery.decide` action for a failed turn: retries, back-off, quota blocks and waits, rung moves. It returns what the flow does next and never escalates |
| `EscalationFiler` | Gathers an escalation's evidence (default question and attempts, report history, workspace facts, pane tail) and files it with the `EscalationRaiser` |
| `TransitionWriter` | The single place a `domain` `Transition` becomes stored task state, step and scope-change events and a refreshed title |
| `TaskFacts` | Collects and renders a task worktree's git facts for prompts, escalation evidence and the acceptance check |
| `SessionManager` | Decides the session a role's next turn runs on: new, resume (also with a new model), or new with `handoff`; stores `session_id` and the resume command on the run |
| `TurnMonitor` | The single consumer of one `Turn.signals()`: persists session facts (session id, usage, quota, last message), appends the role's view log, updates pane decorations, and stops the turn when `domain.activity.is_stalled` says so |
| `TurnRunner` | One turn: turn dir, queued operator messages, `start_turn`, await the monitor, report validation, nudge turns, Helper rebuild and failure classification. Returns a `TurnResult`; never escalates |
| `Integrator` | Rebase and conflict hand-off, then delegates to an `IntegrationStrategy`: `LocalMerge` or `PullRequest` |
| `EscalationRaiser` | Builds the packet and records the escalation |
| `Reconciler` | At engine start: restores windows, ends orphaned turn processes, re-runs in-flight steps |
| `LayoutGuard` | Keeps windows and panes matching the DB (recreates killed or dead panes; a dead taken-over pane is handed back) |
| `Takeover` | Take over and hand back one task role (03 §9) |
| `Notifier` | tmux message plus `notify.command` on escalation and task completion |
| `NotificationDelivery` | Bounded queue and one supervised task delivering notifications off the command loop (06 §5.7) |

**Enforced rules** (by tool, not convention):
- import-linter: the layers above; the engine boundary (a `forbidden` contract: `engine` may not import `agents.registry`, `agents.sdk` or `agents.testing`, so of the agents packages it imports only `agents.contracts`; it is not a whitelist of every package the engine may import); the widget plugin boundary; the agent plugin boundary; and an `independence` contract so `plugins.agents.claude` and `plugins.agents.codex` never import each other (they share code only through `agents.sdk`);
- the agent plugin boundary and the engine boundary are denylists, so a new package would pass silently: `tests/integration/test_import_contract_completeness.py` fails when a top-level module under `src/shikiru`, or a module under `shikiru.agents`, is neither on the contract's forbidden list nor in the test's explicitly allowed set, forcing a decision when one is added;
- ruff `banned-api`: `sqlite3` only in `store`, `subprocess`/`asyncio.create_subprocess_*` only in `process`, and the sleep, wall-clock, monotonic, CPU-time and performance-counter entry points (`time.sleep`, `asyncio.sleep`, `time.time`, `time.monotonic`, `time.perf_counter`, `time.process_time`, `time.thread_time` and their `_ns` forms, `datetime.now`, `utcnow`, `today`) only in `clock`. Per-file exceptions: `process.py` and `clock.py` (own the boundary), `tests/support/waiting.py` (the one place tests poll), the store test helpers and `tests/integration/test_store_database.py`, and the E2E results file name (`tests/e2e/conftest.py`, `tests/integration/test_e2e_results.py`). `store` has no file exemption: its eight `import sqlite3` lines carry a line-level `# noqa: TID251`, so time and subprocess use inside `store` is still rejected;
- the comment check (`poe comments`, 07 §2.8).

**Review conventions** (no tool checks them): nothing outside `plugins/agents/*` names an agent CLI, stream or session-log format, or quota source, since import-linter cannot see semantic coupling; and event-loop time (`loop.time()`) is not used outside `clock`.

## 3. Project structure

```
pyproject.toml
src/shikiru/
  bootstrap.py  process.py  clock.py  errors.py  paths.py  view_log.py  __main__.py
  config/       settings.py loader.py defaults.toml
  domain/       tasks.py steps.py ladder.py recovery.py scheduling.py escalation.py activity.py commands.py events.py reports.py runs.py fencing.py
  store/        database.py migrations/ tasks.py runs.py escalations.py quotas.py events.py commands.py reader.py
  agents/       contracts.py registry.py
    sdk/        turn.py reset_time.py
    testing/    contract_suite.py resume.py
  workspace/    roots.py caches.py facts.py git.py github.py messages.py reports.py
  tmux/         client.py session.py layout.py decorator.py
  prompts/      renderer.py templates/
  helper/       helper.py models.py
  ui/           tokens.py glyphs.py status.py strip.py bubbles.py splash.py gauge.py stream.py
  telemetry/    resources.py docker.py
  engine/       daemon.py supervisor.py lifecycle.py scheduler.py dependency.py flow.py agents.py sessions.py monitor.py turns.py
                turn_context.py turn_execution.py recovery.py escalation_filing.py transitions.py task_facts.py
                takeover.py integration.py integration_turns.py escalations.py reconcile.py layout_guard.py titles.py history.py events.py notifier.py notification_delivery.py
    handlers/   base.py tasks.py operator.py escalations.py engine.py
  operator/     queries.py commands.py services.py events.py strip_views.py lifecycle.py detectors.py probe.py doctor.py plugin_checks.py
  cli/          app.py context.py report.py view.py render.py
  mcp/          server.py
  dashboard/    app.py contract.py services.py views.py palette.py wizard.py
  plugins/
    agents/claude/  agent.py turn.py headless.py options.py
    agents/codex/   agent.py turn.py headless.py options.py home.py quota.py usage.py
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

**Permissions** (`shikiru.private`): every directory Shikiru creates under `.shikiru/` is `0700`, and every file the engine or a client writes there (state DB with its WAL and SHM, engine log, prompts, reports, packets, queued messages, `view.jsonl`, raw streams) is created owner-only, so the state, the agents' transcripts and the operator's requests are not readable by other users of the host. Files that agent processes or git create inside those directories follow the process umask; the `0700` directories keep them private. Starting the engine tightens an existing `.shikiru/` (directories to `0700`, group and other access removed from files, symlinks untouched) and logs how many paths it changed; task worktrees hold the project's own files and only the `worktrees/` directory itself is tightened. Nothing outside `.shikiru/`, and never the project repository, is chmod-ed. The tmux socket is tmux's own default (`/tmp/tmux-<uid>/`, created `0700` by tmux), so it needs nothing from Shikiru.
