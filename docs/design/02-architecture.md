# 02 · Architecture

## 1. Processes

```
 Operator (human)           Operator (tool / AI)                      Agent CLIs (in panes)
   │ tmux attach               │ shikiru … --json │ shikiru mcp            │ plugin hooks → shikiru hook
   ▼                           ▼                                           │ shikiru report
┌──────────────── tmux session "[shikiru] <project>" ───────────────────────┼──────────┐
│ window "shikiru": dash --home │ shell                                     │          │
│ window per task:  dash --task │ leader │ implementer │ shell              │          │
└──────────────▲───────────────────────────────▲────────────────────────────┼──────────┘
               │ read state, append commands    │ tmux                      │ files (.shikiru/runs)
          ┌────┴───────┐                   ┌────┴───────────────────────────▼──┐
          │ state.db   │◀─── state ───────│ engine daemon (asyncio, 1/project)  │
          │ SQLite WAL │──── commands ───▶│                                     │
          └────────────┘                   └────────────────────────────────────┘
```

| Process | Started by | Writes |
|---|---|---|
| `shikiru engine` | `shikiru start` (detached) | All state tables |
| `shikiru dash`, `shikiru palette` | engine (panes), tmux popup | `commands` rows only |
| `shikiru <cmd>`, `shikiru mcp` | Operator | `commands` rows only; the `sequences` row when allocating a task id on submit |
| `shikiru hook`, `shikiru report` | agent CLIs | Files under `.shikiru/runs` only |
| Helper and quota probes | engine / plugins (subprocess) | nothing |

**Engine liveness and exclusivity** come from one mechanism: an exclusive `flock` on `.shikiru/engine.lock`, held for the engine's lifetime.
- Clients check liveness with a non-blocking lock attempt.
- No heartbeat or pid table exists.

## 2. Layers and components

Dependencies point downward only. Each layer is an import-linter `layers` contract. `bootstrap` is the only composition root and may import anything.

```
bootstrap                                   build_engine(), build_operator(), build_dashboard()
───────────────────────────────────────────
cli · mcp · dashboard                       interfaces
operator                                    queries, commands, event feed
engine                                      daemon, handlers, scheduler, flow, sessions, turns, integration, escalations
workspace · tmux · prompts · helper · ui · telemetry
store
domain
config · agents.contracts · process · clock lowest; no internal imports
───────────────────────────────────────────
plugins.agents.*  → agents.contracts, agents.sdk, process only; loaded by agents.registry from bootstrap
plugins.widgets.* → dashboard.contract, ui, store.reader only
```

| Package | Responsibility | Key types |
|---|---|---|
| `process` | The only subprocess gateway: argv lists, timeouts, typed errors, async | `SubprocessRunner`, `CommandFailed` |
| `clock` | Injectable time for every timer | `Clock`, `SystemClock` |
| `config` | Merge defaults < project file < `--set`, validate core settings. Plugin `options` stay untyped here | `Settings`, `load_settings()` |
| `agents.contracts` | Agent plugin contract (04 §1) | `InteractiveAgent`, `AgentSession`, `HeadlessAgent`, signals, `Usage`, `QuotaWindow`, `Rung` |
| `agents.registry` / `sdk` / `testing` | Entry-point loading; plugin building blocks; contract test kit | `AgentRegistry`, `HookRecorder`, `JsonlTail`, `ResetTimeParser`, `PriceTable`, `AgentContractSuite` |
| `domain` | Pure rules, no I/O | `Task`, `Status`, `Step`, `Hold`, `Counters`, `EventKind`, command models, report models, `next_step()`, `pick_rung()`, `recovery.decide()`, `scheduling.pick_ready()`, `escalation.options_for()` |
| `store` | SQLite schema, migrations, stores per aggregate, read-only reader | `Database`, `TaskStore`, `RunStore`, `EscalationStore`, `QuotaStore`, `EventLog`, `CommandQueue`, `StateReader` |
| `workspace` | Task roots, worktrees, caches, git facts | `TaskRootBuilder`, `CacheCloner`, `GitFacts`, `Git` |
| `tmux` | tmux client; session, windows, layout, pane decorations; the `PaneHandle` implementation | `Tmux`, `SessionView`, `LayoutBuilder`, `TmuxPane` |
| `prompts` | Jinja2 templates (packaged, overridable) | `PromptRenderer` |
| `helper` | Joshu calls | `Helper` |
| `ui` | Visual language and Rich renderables shared by CLI and dashboard | `Tokens`, `Glyphs`, `TaskStrip`, `SpeechBubble`, `SplashPanel`, `Gauge` |
| `telemetry` | Host/process and Docker sampling | `ResourceSampler`, `DockerSampler` |
| `engine` | Orchestration (below) | — |
| `operator` | Use cases for interfaces | `OperatorQueries`, `OperatorCommands`, `EventFeed` |
| `cli` / `mcp` / `dashboard` | Typer app; FastMCP stdio server; Textual apps and widget contract | `app`, `build_server()`, `DashboardApp`, `PaletteApp`, `DashboardWidget` |

**Engine components**, each with one responsibility:

| Component | Responsibility |
|---|---|
| `Engine` | Main loop: poll commands → dispatch to handlers, tick the scheduler, supervise one coroutine per active task. Nothing else |
| `handlers/*` | One `CommandHandler` per command model, found through a registry `dict[type[Command], CommandHandler]` |
| `Scheduler` | Ready set, gates, slot assignment; persists `tasks.wait_reason` |
| `TaskFlow` | Runs a task's steps as async functions (`triage`, `plan`, `implement`, `consult`, `verify`, `integrate`) and applies `domain` transitions and recovery actions |
| `SessionManager` | Ensures a live `AgentSession` per task role: launch, resume, attach, handoff, exit |
| `SessionMonitor` | The single consumer of `AgentSession.signals()`. Fans signals out to subscribers. Persists usage, last message and snapshot. Detects intervention, stall and pane death. Updates pane decorations |
| `TurnRunner` | One turn: turn dir, `before_turn` lines, pointer injection, submission confirmation, await, report validation, nudges, Helper rebuild. Returns a `TurnResult`; never escalates |
| `Integrator` | Rebase and conflict hand-off, then delegates to an `IntegrationStrategy`: `LocalMerge` or `PullRequest` |
| `EscalationRaiser` | Builds the packet and records the escalation |
| `Reconciler` | Restores sessions, windows and in-flight steps at engine start |
| `LayoutGuard` | Keeps windows and panes matching the DB (recreates killed or dead panes) |
| `Notifier` | tmux message plus `notify.command` on escalation and task completion |

**Enforced rules** (lint, not convention):
- import-linter layers as above;
- ruff `banned-api`: `sqlite3` only in `store`, `subprocess`/`asyncio.create_subprocess_*` only in `process`, `time.sleep`/`asyncio.sleep` only through `clock`;
- nothing outside `plugins/agents/*` names an agent CLI, transcript format, model price or quota source.

## 3. Project structure

```
pyproject.toml
src/shikiru/
  bootstrap.py  process.py  clock.py  errors.py  __main__.py
  config/       settings.py loader.py defaults.toml
  domain/       tasks.py steps.py ladder.py recovery.py scheduling.py escalation.py commands.py events.py reports.py
  store/        database.py migrations/ tasks.py runs.py escalations.py quotas.py events.py commands.py reader.py
  agents/       contracts.py registry.py hook_cli.py report_cli.py
    sdk/        hooks.py jsonl.py reset_time.py prices.py
    testing/    contract_suite.py
  workspace/    roots.py caches.py facts.py git.py
  tmux/         client.py session.py layout.py pane.py
  prompts/      renderer.py templates/
  helper/       helper.py templates/
  ui/           tokens.py glyphs.py strip.py bubbles.py splash.py gauge.py
  telemetry/    resources.py docker.py
  engine/       daemon.py scheduler.py flow.py sessions.py monitor.py turns.py integration.py escalations.py reconcile.py layout_guard.py notifier.py
    handlers/   tasks.py sessions.py escalations.py engine.py
  operator/     queries.py commands.py events.py
  cli/          app.py project.py tasks.py escalations.py sessions.py diagnostics.py render.py
  mcp/          server.py
  dashboard/    app.py contract.py palette.py detectors.py wizard.py
  plugins/
    agents/claude/  agent.py session.py signals.py usage.py quota.py headless.py options.py prices.toml
    agents/codex/   agent.py session.py signals.py usage.py quota.py headless.py options.py prices.toml
    widgets/        header.py strips.py escalations.py queue.py dialogue.py usage.py resources.py docker.py
tests/  unit/ integration/ e2e/ support/ (tmux fixture, fake clock, wait_until) fixtures/ (sample-project, fake agent, agent samples)
```

`init --yes` and the interactive wizard share `dashboard/detectors.py`: repos, agents, trackers, caches.

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
<project>/.shikiru/                 # added to .git/info/exclude of every repo
  config.toml  prompts/ (optional overrides)  state.db  engine.lock  logs/engine.log
  worktrees/T-12/<repo path>/
  runs/T-12/
    leader/  implementer/           # current_turn symlink + plugin-private files (e.g. hooks.jsonl)
    turns/NN-<step>-i<iteration>/   prompt.md report.schema.json report.kind report.json facts.md helper.json
    messages/NNN.md
    escalations/E-3.md
```
