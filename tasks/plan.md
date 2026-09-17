# Shikiru Implementation Plan

Source of truth: [`docs/design`](../docs/design/README.md). Progress: [`todo.md`](todo.md).

**Rules**:
- Follow `CLAUDE.md`/`AGENTS.md` and 07 §2–3.
- Work on one task at a time.
- `uv run poe check` must be green before every commit.
- Tick each AC here and the task in `todo.md` when done.
- An AC naming an E2E scenario is ticked only with a passing `tests/e2e/results/*.json` committed.

**Order**:
- Phases 0–3 build the whole pipeline against the fake agent.
- Phase 4 makes it real (E1, E2).
- Phases 5–6 complete resilience, operator surfaces and onboarding.

---

## Phase 0 — Foundation

### Task 1: Scaffold, quality gate, test support
Refs: 02 §2–3, 07 §1–3.
- [x] `pyproject.toml`: uv, Python ≥ 3.14, `shikiru` script, entry-point groups; package tree per 02 §3 (only modules this task needs).
- [x] `poe check` runs ruff format/lint (with `banned-api` for `sqlite3`, `subprocess`, sleeps), basedpyright strict, import-linter layers (02 §2), vulture and pytest with coverage `fail_under = 80` (subprocess patch). `poe e2e` runs `-m e2e`.
- [x] `process.SubprocessRunner` (async, argv, timeout, `CommandFailed`), `clock.Clock` + `SystemClock`, `errors.ShikiruError` hierarchy with exit codes (06 §2), `bootstrap.py` stub.
- [x] `tests/support`: `tmux_server` fixture (`-f /dev/null`, fixed size, `/bin/sh`, unique socket), `wait_until`, `FakeClock`.
- [x] `shikiru --version`; an integration test proves the `tmux_server` fixture is isolated from the user's server.

## Phase 1 — Core model

### Task 2: Configuration
Refs: 05 §1.
- [x] `Settings` models for every core key (`extra="forbid"`), `agents.<name>.options` untyped; packaged `defaults.toml`.
- [x] Merge defaults < project < `--set` (TOML-parsed values); errors name the file and key path.
- [x] `Rung` parsing `agent:model:effort`; implementer ladder resolution by complexity with fallback.
- Tests: unit for merge, overrides and ladder resolution.

### Task 3: Domain rules
Refs: 03 §1–3, §7–8, §10; 05 §2.
- [x] `Task`, `Status`, `Step`, `Mode`, `Hold`, `Counters`, `EventKind`, command models.
- [x] `next_step(task, outcome)` covering every outcome in 03 §2–3, including final round, supersede and split.
- [x] `pick_rung(task, role, settings)` and move-up rules; `recovery.decide(result, counters, policy)` for every row of 03 §7; counter resets (03 §1.1).
- [x] `activity.is_stalled` and `activity.is_operator_prompt` (03 §4.2, §9).
- [x] `scheduling.pick_ready(tasks, gates)` with ordering and `wait_reason`; `escalation.options_for(reason)` per 03 §8.
- Tests: table-driven unit tests, one row per documented rule.

### Task 4: Report models and `shikiru report`
Refs: 05 §4, 03 §4.3.
- [ ] All report models with "required iff" validators; `schema_for(kind)`.
- [ ] `shikiru report --file F` (`cli/report.py`): resolves `$SHIKIRU_RUN_DIR/$SHIKIRU_ROLE/current_turn`, validates by `report.kind`, atomic write, exit codes 0/2/3.
- Tests: unit for validators; integration for the CLI in a tmp run dir.

### Task 5: State store
Refs: 05 §2.
- [ ] `Database` (WAL, busy timeout, `user_version` migrations), `001_initial.sql`.
- [ ] `TaskStore`, `RunStore`, `EscalationStore`, `QuotaStore` (incl. `agent_blocks`), `EventLog`, `CommandQueue` (typed commands; `Submit` allocates the task id from `sequences`).
- [ ] `StateReader` with `on_change` via `PRAGMA data_version`.
- Tests: integration on a real file, including a writer and a reader in separate processes.

## Phase 2 — Substrate

### Task 6: tmux
Refs: 06 §5.1–5.3.
- [ ] `Tmux` client (argv, exact `=` targets, socket override for tests).
- [ ] `SessionView`: name rules and collision check, session options, home window, task window create/close.
- [ ] `LayoutBuilder` from the layout tree → `{role: pane_id}`; `TmuxPane` implementing `PaneHandle` plus `paste_line` and `send_keys`; `PaneDecorator` (`@shk_*` pane and window options, border and status formats, titles from templates); conditional `prefix + Space` binding.
- Tests: integration on `tmux_server`: geometry, paste reaches `cat`, decorations render, binding keeps its default outside Shikiru sessions.

### Task 7: Workspace
Refs: 03 §5.
- [ ] `TaskRootBuilder`: monorepo and multi-repo roots, symlinked top-level entries, idempotent worktrees on `shikiru/T-n-<slug>`.
- [ ] `CacheCloner`: CoW clone of matching ignored paths, a single warning when unsupported.
- [ ] `GitFacts` (log, diffstat, status, trackers changed, repos ahead of base); `.git/info/exclude`; cleanup.
- Tests: integration with real repos for both topologies.

### Task 8: Agent contract, registry, SDK
Refs: 04 §1–2.
- [ ] `agents/contracts.py` exactly per 04 §1 with docstrings.
- [ ] `AgentRegistry`: loads `AgentFactory` entry points (and factories registered by `bootstrap`), reports missing or broken plugins and option errors by name.
- [ ] SDK: `HookRecorder` + `shikiru hook` (flock, no-op outside Shikiru), `JsonlTail`, `ResetTimeParser`, `PriceTable`.
- Tests: unit for `ResetTimeParser` and `PriceTable`; integration for hook append/tail with concurrent writers.

### Task 9: Fake agent and contract test kit
Refs: 04 §3, 07 §3.
- [ ] `AgentContractSuite` with offline and live checks.
- [ ] Fake agent as a test-only package `tests/fake_agent` (uv dev dependency, own `shikiru.agents` entry point): scripted REPL using `HookRecorder`, headless `ask` via script; passes the suite on `tmux_server`.

### Task 10: Prompts and Helper
Refs: 03 §2, §4.3; 04 §6.
- [ ] `PromptRenderer` with packaged templates `triage`, `plan`, `implement`, `rework`, `consult`, `verify`, `conflicts`, `continue`, `handoff`; `.shikiru/prompts/` overrides.
- [ ] `Helper.rebuild_report` and `Helper.classify_screen` over `headless()` with validated results.
- Tests: property tests (request and report instruction present, fenced reports, `StrictUndefined` raises); Helper against the fake agent's headless script.

### Task 11: UI core and dashboard shell
Refs: 06 §5.4, §6.1–6.2.
- [ ] `ui.tokens`, `ui.glyphs` (width-safety unit test), `ui.TaskStrip` (modes, panel states, stamps, two widths, `NO_COLOR`).
- [ ] `DashboardApp` (`dash --home | --task`), `DashboardWidget` contract with `WidgetContext` services, widgets `header`, `strips`, `queue`.
- Tests: property tests for strips; two goldens per mode; Textual `run_test` for widgets over a seeded DB.

## Phase 3 — Pipeline on the fake agent

### Task 12: Session manager and monitor
Refs: 03 §4.1–4.2, §9.
- [ ] `SessionManager.ensure` for every row of 03 §4.1; `resume_command` stored; exit on task end.
- [ ] `SessionMonitor`: single consumer, fan-out queue (with `PaneDecorator` subscribed), usage/snapshot/last message persisted, pane death, stall and attention via `domain.activity`, intervention hold via `domain.activity`; launch timeout → `Stalled`.
- Tests: integration with fake agent scripts (resume, attach after restart, rung change, crash, stall, typed prompt → hold on that task only).

### Task 13: Turn runner
Refs: 03 §4.3.
- [ ] Turn dir and `current_turn`, `before_turn` lines, pointer injection with recorded injected set, submit confirmation with one Enter retry, await, nudges, Helper rebuild (`helper.json`), `TurnResult` variants.
- Tests: integration: accept; no report → nudge → report; no report ×3 → rebuilt; unconfirmed submit → retry → `Stalled`.

### Task 14: Task flow
Refs: 03 §2–3.
- [ ] Step functions triage, plan, implement, consult, verify with `next_step`; final round; supersede and split with dependents repointed; plan with `new_tasks`; `scope_change` events; `turn_completed` with summary and question; persisted counters and `rung_index`.
- [ ] `EscalationRaiser`: packet with all sections and exactly one ★ option; `escalated` status.
- Tests: integration flows ending at step `integrate` or an escalation: implement-accept; changes_requested → accept; needs_decision → consult → accept; consult limit; final round → `iterations_exhausted`; split with dependents; plan with new tasks; supersede; packet property test.

### Task 15: Recovery
Refs: 03 §7.
- [ ] `TaskFlow` applies `recovery.decide` actions: agent quota blocks with `continue` after the block, backoff, next rung (same-agent resume, other-agent handoff), relaunch, context handoff, stall → `classify_screen` actions (Leader question → `decision_required`).
- Tests: integration with fake failures for each 03 §7 row using `FakeClock`.

### Task 16: Integration (local merge)
Refs: 03 §6.
- [ ] Integration lock; rebase of repos ahead of base; conflict turn → verify turn with the loop rules of 03 §6; `CONFLICT_ROUNDS`.
- [ ] `LocalMerge`: pre-check, ff-only or `update-ref`, dirty-overlap retry and `integration_blocked`; cleanup; `task_done` with summary.
- Tests: integration with real repos: clean ff, forced conflict resolved by the fake agent, dirty main checkout wait then success, multi-repo pre-check failure.

### Task 17: Engine daemon, scheduler, handlers
Refs: 02 §1–2, 03 §9–10, 05 §2.
- [ ] `Engine`: flock liveness, command loop, supervision of task coroutines, `engine.log`.
- [ ] `Scheduler`: ready set, slot (previous slot preferred, slot released on escalation)/resources/quota gates, `wait_reason`, dispatch creating the task window; `dependency_cancelled`.
- [ ] `ResourceSampler` (host and instance process tree) and `QuotaPoller`.
- [ ] Handlers for every command model (`Submit` … `Stop`), including cancellation semantics (03 §4.3) and resolution options (03 §8).
- [ ] `LayoutGuard` restoring killed or dead panes; `Notifier` (tmux message and `notify.command`).
- Tests: integration with the in-process engine: priority order, parallelism, dependencies, resource gate via configured thresholds (0% / 100%), quota gate via `agent_blocks` rows, each handler, resolution options with counter resets, pane restoration, quota polling with the fake agent.

### Task 18: Operator services and CLI
Refs: 06 §1–2.
- [ ] `OperatorQueries`, `OperatorCommands` (ack or queued), `EventFeed`.
- [ ] All CLI commands from 06 §2 except `ls`/`attach`/`mcp`/`doctor`, with Rich output via `ui` and `--json`; `init --yes` with `operator/detectors.py`; `start`/`stop` spawning the detached engine.
- [ ] End-to-end with the fake agent through real processes: `init --yes` → `start` → `submit` → task `done`, commit on `base`, task window closed.
- Tests: integration CLI tests (human and JSON) plus the fake end-to-end.

## Phase 4 — Real agents

### Task 19: Claude Code plugin
Refs: 04 §4.
- [ ] Factory and options; launch/resume/attach; hook signals; transcript usage and failures; quota probe with cache; keys; `resume_command`; `headless`; `doctor`.
- [ ] Offline contract checks with committed samples; live contract checks pass.

### Task 20: Codex plugin
Refs: 04 §5.
- [ ] Factory and options; launch/resume/attach (`-c` hooks, trust, `--add-dir`); hook signals; rollout discovery, usage, failures, quota; compaction `before_turn`; keys; `resume_command`; `headless`; `doctor`.
- [ ] Offline contract checks with committed samples; live contract checks pass.

### Task 21: Doctor and first real E2E
Refs: 06 §2, 07 §5.
- [ ] `doctor` (tmux, git, config, plugins and their `doctor()`, CoW, DB) and `doctor --probe`.
- [ ] E2E harness (`poe e2e`, fixture project, cheap rungs, results file).
- [ ] E2E E1 (monorepo and multi-repo) and E2 pass.

## Phase 5 — Resilience and operator completeness

### Task 22: Engine restart recovery
Refs: 03 §7.
- [ ] `Reconciler`: rebuild windows, attach or resume sessions, re-run current steps idempotently, integration restart.
- [ ] Integration test: kill the in-process engine at each step boundary with the fake agent. E2E E5 passes.

### Task 23: Pull request integration
Refs: 03 §6.
- [ ] `PullRequest` strategy: push, `gh pr create`, slot release with `wait_reason`, polling, merged → done, closed → `pr_closed`.
- Tests: integration with `gh` faked on PATH.

### Task 24: MCP server
Refs: 06 §4.
- [ ] FastMCP stdio server with every tool, typed inputs and outputs, `wait_for_events` long poll.
- [ ] Integration test through an in-memory MCP client; E2E E4 passes.

### Task 25: Parallel and intervention E2E
Refs: 07 §5.
- [ ] E2E E3 and E6 pass.

## Phase 6 — Operator surfaces and onboarding

### Task 26: Remaining widgets and renderables
Refs: 06 §5.4, §6.3–6.4.
- [ ] `ui.SpeechBubble`, `ui.SplashPanel`, `ui.Gauge`; widgets `escalations`, `dialogue`, `usage`, `resources`, `docker` (hidden without Docker); `DockerSampler`.
- Tests: Textual `run_test` behaviour over seeded state; samplers against real psutil and Docker (skipped when absent).

### Task 27: Palette
Refs: 06 §5.5.
- [ ] `PaletteApp` with every action including the escalation resolution form and intervene; tmux popup and `Ctrl+P` in `dash`.
- Tests: Textual `run_test` for submit, prioritize, resolve, intervene.

### Task 28: Motion
Refs: 06 §6.5.
- [ ] `frame(state, now)` for spinner/freeze/stall, stamp flash, escalation pulse, ticking counters; 4 Hz cap; `animations = false`.
- Tests: unit tests of `frame` with `FakeClock`.

### Task 29: Sessions list and attach
Refs: 06 §5.6.
- [ ] `ls [--host]`, `attach [--host] [--project]` with the inline picker, `switch-client` inside tmux, quoted SSH command.
- Tests: integration on `tmux_server`; SSH argv construction.

### Task 30: Init wizard
Refs: 06 §3.
- [ ] Inline Textual wizard over `operator/detectors.py` with the 7 steps; writes config, excludes `.shikiru/`, runs doctor.
- Tests: Textual `run_test` walkthrough on tmp mono- and multi-repo projects.

### Task 31: Release readiness
- [ ] All E2E scenarios pass in one `poe e2e` run; coverage ≥ 80%; `poe check` clean.
- [ ] Scripted clean-environment check: fresh `HOME`, `uv tool install .`, README quick start.
- [ ] Walk every section of `docs/design`; code and docs agree, and each difference is fixed in one or the other.
