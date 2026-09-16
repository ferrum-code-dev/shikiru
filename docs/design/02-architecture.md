# 02 · Architecture

## 1. Processes

```
 Operator (human)        Operator (tool / AI)                 Agent CLIs (in panes)
   │ tmux attach            │ shikiru … --json │ shikiru mcp       │ plugin hooks → shikiru hook
   ▼                        ▼                                      │ report → shikiru report
┌──────────────────── tmux session "[shikiru] <project>" ──────────┼───────────────┐
│ window per task: system(shikiru dash) │ leader │ implementer │ shell             │
└───────────────▲──────────────────────────────▲──────────────────┼───────────────┘
                │ reads                         │ tmux commands    │ files (.shikiru/runs/…)
          ┌─────┴──────┐  commands table  ┌─────┴──────────────────▼─────┐
          │ state.db   │◀────────────────▶│ engine daemon (shikiru engine)│
          │ SQLite WAL │  state + events   │ asyncio, one per project      │
          └────────────┘                   └──────────────────────────────┘
```

| Process | Started by | Lifetime | Writes DB |
|---|---|---|---|
| `shikiru engine` | `shikiru start` (detached, `start_new_session`) | Until `shikiru stop` or a crash (the next `start` recovers) | yes (owner of the state) |
| `shikiru dash` | engine, in every system pane | pane lifetime (respawned) | no (read-only) |
| `shikiru <cmd>` / `shikiru mcp` | Operator | command / MCP session | `commands` rows only; reads everything |
| `shikiru hook` / `shikiru report` | agent CLIs | milliseconds | no (files only) |
| Helper calls | engine (subprocess) | one call | no |

Single-writer rule: the engine owns every state table. CLI and MCP append `commands` rows and wait for the engine's acknowledgement (§4 of 06-operator). Submissions and reprioritisations made while the engine is down are applied at its next start. The CLI tells the operator about this.

Engine exclusivity: `fcntl.flock` on `.shikiru/engine.lock`, with the pid written inside. `start` on a running project only ensures the tmux session exists and then reports it.

## 2. Components

Each component has one reason to change. Arrows show dependencies (callers → callees).

```
cli ─┐                    ┌─► workspace ──► git
mcp ─┼─► operator ─► store│
dash ┘        ▲           ├─► turns ──► agents (plugin registry) ──► tmux
              │  engine ──┤              └─► helper
              │           ├─► scheduler
              │           ├─► flow (steps) ──► prompts
              │           └─► integration ──► workspace
config ◄── everything
```

| Package | Responsibility | Key types |
|---|---|---|
| `config` | Load packaged defaults, the user file, the project file and `--set` overrides; validate; run the init wizard | `Settings` (pydantic), `load_settings()`, `Wizard` |
| `domain` | Pure models and rules, no I/O: task states, transitions, ladder selection, scheduling choice, escalation policy | `Task`, `TaskStatus`, `Hold`, `Transition`, `LadderRung`, `pick_rung()`, `pick_ready()` |
| `store` | SQLite schema and migrations, repositories, event append, `data_version` change polling | `Database`, `TaskRepo`, `RunRepo`, `EscalationRepo`, `EventLog`, `CommandQueue`, `UsageRepo` |
| `engine` | Daemon main loop: process commands, reconcile, dispatch tasks into slots, supervise task coroutines | `Engine`, `Reconciler` |
| `engine.scheduler` | Choose ready tasks by DAG, priority, slots and resource gate | `Scheduler`, `ResourceGate` |
| `engine.flow` | Per-task step sequence. Each step is a small class that returns an `Outcome` | `TaskFlow`, `TriageStep`, `ImplementStep`, `VerifyStep`, `ReplanStep`, `PlanStep`, `IntegrateStep` |
| `engine.turns` | Turn protocol over the normalized `AgentSession`: ensure the session is live, inject `before_turn` lines and the pointer, await signals, validate the report, nudge, recover, switch rungs | `SessionDriver`, `TurnResult` |
| `engine.integration` | Rebase, conflict hand-off, fast-forward or PR, serialised by an integration lock | `Integrator`, `LocalMerge`, `PullRequest` |
| `workspace` | Worktrees per task and repo, cache cloning, root mirroring for multi-repo, cleanup, git facts | `WorkspaceManager`, `GitFacts`, `CacheCloner` |
| `prompts` | Render Jinja2 prompt templates (packaged, overridable in `.shikiru/prompts/`) and JSON schemas for reports | `PromptRenderer` |
| `agents` | `contracts` (the only module the engine imports), `registry`, `sdk` (building blocks for plugins), `testing` (the contract test kit), `report` CLI | `InteractiveAgent`, `AgentSession`, `HeadlessAgent`, `AgentSignal`, `Usage`, `QuotaWindow`, `AgentRegistry`, `AgentContractSuite` |
| `plugins/agents/claude`, `plugins/agents/codex` | Agent plugins: launch, signals, usage with their own price tables, quota, failures, headless | `ClaudeAgent`, `CodexAgent` |
| `helper` | Joshu one-shot calls with schemas | `Helper` |
| `tmux` | Thin typed wrapper around the `tmux` binary; session, window and layout building; pane protection | `Tmux`, `SessionView`, `LayoutBuilder` |
| `operator` | Use cases shared by CLI, MCP and dashboard palette: submit, prioritise, pause, cancel, say, peek, resolve, status | `OperatorService` |
| `cli` | Typer app, Rich rendering, `--json` | command modules |
| `mcp` | FastMCP stdio server exposing `OperatorService` | `build_server()` |
| `dashboard` | Textual app, widget contract, palette | `DashboardApp`, `Widget` contract |
| `plugins/dashboard/*` | Built-in widgets | `ProjectWidget`, `StripsWidget`, … |
| `comic` | Signature comic-strip renderables (06-operator §7), used by both the CLI and the dashboard | `TaskStrip`, `SplashPanel`, `SpeechBubble`, `Banner` |
| `notify` | Terminal and OS notifications plus the `notify.command` hook | `Notifier` |
| `telemetry` | Host, process-tree and Docker sampling | `ResourceSampler`, `DockerSampler` |

Anti-god-object rules:
- `Engine` supervises and delegates. It never contains step logic.
- `TaskFlow` only sequences steps through the transition table in `domain`.
- Steps build prompts through `PromptRenderer` and run turns through `SessionDriver`.
- Nothing except `store` issues SQL.
- Nothing except `tmux` spawns `tmux`.
- Nothing except `workspace` and `integration` spawns `git`.
- Nothing outside `plugins/agents/*` knows any agent CLI, transcript format, model name, price or quota source. An import-linter contract enforces that `engine`, `domain`, `operator`, `dashboard` and `cli` import only `shikiru.agents.contracts`.

## 3. Project structure

```
pyproject.toml            # uv project, entry points, ruff/basedpyright/pytest config
src/shikiru/
  __main__.py
  config/      models.py loader.py wizard.py defaults.toml
  domain/      tasks.py transitions.py ladder.py scheduling.py escalation.py reports.py
  store/       database.py migrations/ tasks.py runs.py escalations.py events.py commands.py usage.py
  engine/      daemon.py reconcile.py scheduler.py flow.py steps/ turns.py signals.py integration.py
  workspace/   manager.py caches.py git.py
  prompts/     renderer.py templates/*.md.j2
  agents/      contracts.py registry.py report_cli.py
    sdk/       hooks.py jsonl.py activity.py turn_detection.py reset_time.py prices.py probe.py
    testing/   contract_suite.py
  helper/      helper.py
  tmux/        client.py session.py layout.py protection.py
  operator/    service.py
  cli/         app.py project.py tasks.py escalations.py sessions.py internal.py render.py
  mcp/         server.py
  dashboard/   app.py widget.py palette.py page.py theme.tcss
  comic/       strip.py splash.py bubbles.py banner.py   # Rich renderables shared by CLI and dashboard
  notify/      notifier.py
  telemetry/   resources.py docker.py
  plugins/
    agents/claude/    agent.py session.py signals.py usage.py quota.py headless.py options.py prices.toml
    agents/codex/     agent.py session.py signals.py usage.py quota.py headless.py options.py prices.toml
    dashboard/        project.py strips.py queue.py escalations.py usage.py resources.py docker.py dialogue.py
tests/
  unit/ integration/ e2e/ fixtures/ (sample-project, fake-agent, recorded transcripts)
docs/design/  tasks/
```

## 4. Plugin contracts

Plugins are discovered through `importlib.metadata.entry_points(group=…)`. Built-ins register the same way, in Shikiru's own `pyproject.toml`:

```toml
[project.entry-points."shikiru.agents"]
claude = "shikiru.plugins.agents.claude.agent:ClaudeAgent"
codex  = "shikiru.plugins.agents.codex.agent:CodexAgent"
[project.entry-points."shikiru.widgets"]
strips = "shikiru.plugins.dashboard.strips:StripsWidget"
# … project, queue, escalations, usage, resources, docker, dialogue
```

The agent contracts are in [04-agents.md §1](04-agents.md). The widget contract is in [06-operator.md §5.3](06-operator.md).
A missing or broken plugin is reported by `shikiru doctor` and at `start`. It never fails silently.

## 5. Runtime layout on disk

```
<project>/.shikiru/                     # excluded from git (added to .git/info/exclude of each repo)
  config.toml                           # the only file an operator edits
  prompts/                              # optional template overrides (same names as packaged)
  state.db (+ -wal, -shm)
  engine.lock  logs/engine.log
  worktrees/T-12/<repo-rel-path>/       # per-task mirrors of the project root (03-workflow §6)
  runs/T-12/
    leader/ current_turn  (+ plugin-private files, e.g. hooks.jsonl)
    implementer/ …
    turns/03-implement-i2/ prompt.md  report.json  report.schema.json  facts.md
    escalations/E-3.md
```
