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
- [x] `next_step(task, outcome)` covering every outcome in 03 §2–3, including final round and supersede.
- [x] `pick_rung(task, role, settings)` and move-up rules; `recovery.decide(result, counters, policy)` for every row of 03 §7; counter resets (03 §1.1).
- [x] `activity.is_stalled` and `activity.is_operator_prompt` (03 §4.2, §9).
- [x] `scheduling.pick_ready(tasks, gates)` with ordering and `wait_reason`; `escalation.options_for(reason)` per 03 §8.
- Tests: table-driven unit tests, one row per documented rule.

### Task 4: Report models and `shikiru report`
Refs: 05 §4, 03 §4.3.
- [x] All report models with "required iff" validators; `schema_for(kind)`.
- [x] `shikiru report --file F` (`cli/report.py`): resolves `$SHIKIRU_RUN_DIR/$SHIKIRU_ROLE/current_turn`, validates by `report.kind`, atomic write, exit codes 0/2/3.
- Tests: unit for validators; integration for the CLI in a tmp run dir.

### Task 5: State store
Refs: 05 §2.
- [x] `Database` (WAL, busy timeout, `user_version` migrations), `001_initial.sql`.
- [x] `TaskStore`, `RunStore`, `EscalationStore`, `QuotaStore` (incl. `agent_blocks`), `EventLog`, `CommandQueue` (typed commands; `Submit` allocates the task id from `sequences`).
- [x] `StateReader` with `on_change` via `PRAGMA data_version`.
- Tests: integration on a real file, including a writer and a reader in separate processes.

## Phase 2 — Substrate

### Task 6: tmux
Refs: 06 §5.1–5.3.
- [x] `Tmux` client (argv, exact `=` targets, socket override for tests).
- [x] `SessionView`: name rules and collision check, session options, home window, task window create/close.
- [x] `LayoutBuilder` from the layout tree → `{role: pane_id}`; `TmuxPane` implementing `PaneHandle` plus `paste_line` and `send_keys`; `PaneDecorator` (`@shk_*` pane and window options, border and status formats, titles from templates); conditional `prefix + Space` binding.
- Tests: integration on `tmux_server`: geometry, paste reaches `cat`, decorations render, binding keeps its default outside Shikiru sessions.

### Task 7: Workspace
Refs: 03 §5.
- [x] `TaskRootBuilder`: the task root is one idempotent worktree of the project repository on `shikiru/T-n-<slug>` (D20).
- [x] `CacheCloner`: CoW clone of matching ignored paths, a single warning when unsupported.
- [x] `GitFacts` (log, diffstat, status, trackers changed); `.git/info/exclude`; cleanup.
- Tests: integration with real repos.

### Task 8: Agent contract, registry, SDK
Refs: 04 §1–2.
- [x] `agents/contracts.py` exactly per 04 §1 with docstrings.
- [x] `AgentRegistry`: loads `AgentFactory` entry points (and factories registered by `bootstrap`), reports missing or broken plugins and option errors by name.
- [x] SDK: `HookRecorder` + `shikiru hook` (flock, no-op outside Shikiru), `JsonlTail`, `ResetTimeParser`, `PriceTable`.
- Tests: unit for `ResetTimeParser` and `PriceTable`; integration for hook append/tail with concurrent writers.

### Task 9: Fake agent and contract test kit
Refs: 04 §3, 07 §3.
- [x] `AgentContractSuite` with offline and live checks.
- [x] Fake agent as a test-only package `tests/fake_agent` (uv dev dependency, own `shikiru.agents` entry point): scripted REPL using `HookRecorder`, headless `ask` via script; passes the suite on `tmux_server`.

### Task 10: Prompts and Helper
Refs: 03 §2, §4.3; 04 §6.
- [x] `PromptRenderer` with packaged templates `triage`, `plan`, `implement`, `rework`, `consult`, `verify`, `conflicts`, `continue`, `handoff`; `.shikiru/prompts/` overrides.
- [x] `Helper.rebuild_report` and `Helper.classify_screen` over `headless()` with validated results.
- Tests: property tests (request and report instruction present, fenced reports, `StrictUndefined` raises); Helper against the fake agent's headless script.

### Task 11: UI core and dashboard shell
Refs: 06 §5.4, §6.1–6.2.
- [x] `ui.tokens`, `ui.glyphs` (width-safety unit test), `ui.TaskStrip` (modes, panel states, stamps, two widths, `NO_COLOR`).
- [x] `DashboardApp` (`dash --home | --task`), `DashboardWidget` contract with `WidgetContext` services, widgets `header`, `strips`, `queue`.
- Tests: property tests for strips; two goldens per mode; Textual `run_test` for widgets over a seeded DB.

## Phase 3 — Pipeline on the fake agent

### Task 12: Session manager and monitor
Refs: 03 §4.1–4.2, §9.
- [x] `SessionManager.ensure` for every row of 03 §4.1; `resume_command` stored; exit on task end.
- [x] `SessionMonitor`: single consumer, fan-out queue (with `PaneDecorator` subscribed), usage/snapshot/last message persisted, pane death, stall and attention via `domain.activity`, intervention hold via `domain.activity`; launch timeout → `Stalled`.
- Tests: integration with fake agent scripts (resume, attach after restart, rung change, crash, stall, typed prompt → hold on that task only).

### Task 13: Turn runner
Refs: 03 §4.3.
- [x] Turn dir and `current_turn`, `before_turn` lines, pointer injection with recorded injected set, submit confirmation with one Enter retry, await, nudges, Helper rebuild (`helper.json`), `TurnResult` variants.
- Tests: integration: accept; no report → nudge → report; no report ×3 → rebuilt; unconfirmed submit → retry → `Stalled`.

### Task 14: Task flow
Refs: 03 §2–3.
- [x] Step functions triage, plan, implement, consult, verify with `next_step`; final round; supersede with dependents repointed; plan with `new_tasks`; `scope_change` events; `turn_completed` with summary and question; persisted counters and `rung_index`.
- [x] `EscalationRaiser`: packet with all sections and exactly one ★ option; `escalated` status.
- [x] Titles and window decorations (03 §4.2, 06 §5.3): pane titles and window titles from `dashboard.titles`, and window `@shk_state_color`/`@shk_state_glyph`, updated on change of step, iteration, status or holds.
- Tests: integration flows ending at step `integrate` or an escalation: implement-accept; changes_requested → accept; needs_decision → consult → accept; consult limit; final round → `iterations_exhausted`; plan with new tasks; supersede; packet property test; titles and window state read back from `tmux_server`.

### Task 15: Recovery
Refs: 03 §7.
- [x] `TaskFlow` applies `recovery.decide` actions: agent quota blocks with `continue` after the block, backoff, next rung (same-agent resume, other-agent handoff), relaunch, context handoff, stall → `classify_screen` actions (Leader question → `decision_required`).
- [x] Tests: integration with fake failures for each 03 §7 row using `FakeClock`.

### Task 16: Integration (local merge)
Refs: 03 §6.
- [x] Integration lock; rebase of the task branch onto base; conflict turn → verify turn with the loop rules of 03 §6; `CONFLICT_ROUNDS`.
- [x] `LocalMerge`: pre-check, ff-only or `update-ref`, dirty-overlap retry and `integration_blocked`; cleanup; `task_done` with summary.
- Tests: integration with real repos: clean ff, forced conflict resolved by the fake agent, dirty main checkout wait then success, base moved past the branch blocks.

### Task 17: Engine daemon, scheduler, handlers
Refs: 02 §1–2, 03 §9–10, 05 §2.
- [x] `Engine`: flock liveness, command loop, supervision of task coroutines, `engine.log`.
- [x] `Scheduler`: ready set, slot (previous slot preferred, slot released on escalation)/resources/quota gates, `wait_reason`, dispatch creating the task window; `dependency_cancelled`.
- [x] `ResourceSampler` (host and instance process tree) and `QuotaPoller`.
- [x] Handlers for every command model (`Submit` … `Stop`), including cancellation semantics (03 §4.3) and resolution options (03 §8).
- [x] `LayoutGuard` restoring killed or dead panes; `Notifier` (tmux message and `notify.command`).
- [x] Tests: integration with the in-process engine: priority order, parallelism, dependencies, resource gate via configured thresholds (0% / 100%), quota gate via `agent_blocks` rows, each handler, resolution options with counter resets, pane restoration, quota polling with the fake agent.

### Task 18: Operator services, CLI and MCP
Refs: 06 §1–2, §4.
- [x] `OperatorQueries`, `OperatorCommands` (ack or queued), `EventFeed`.
- [x] `init --yes` (with `operator/detectors.py`), `start`, `stop`, `submit`, `status [T-n] [--watch]`, with Rich output via `ui` and `--json`; `start`/`stop` spawn and stop the detached engine. No other command: the rest of 06 §2 is `ls`/`attach` (Task 28) and `doctor` (Task 21).
- [x] FastMCP stdio server with every tool, typed inputs and outputs, `wait_for_events` long poll. Mutating operator actions ship here, not as CLI verbs, so they are reachable before the palette exists.
- [x] End-to-end with the fake agent through real processes: `init --yes` → `start` → `submit` → task `done`, commit on `base`, task window closed.
- [x] Tests: integration CLI tests (human and JSON), MCP through an in-memory client, plus the fake end-to-end.

## Phase 4 — Real agents

### Task 19: Claude Code plugin
Refs: 04 §4.
- [x] Factory and options; launch/resume/attach; hook signals; transcript usage and failures; quota probe with cache; keys; `resume_command`; `headless`; `doctor`.
- [x] Offline contract checks with committed samples; live contract checks pass.

### Task 20: Codex plugin
Refs: 04 §5.
- [x] Factory and options; launch/resume/attach (`-c` hooks, trust, `--add-dir`); hook signals; rollout discovery, usage, failures, quota; compaction `before_turn`; keys; `resume_command`; `headless`; `doctor`.
- [x] Offline contract checks with committed samples; live contract checks pass.

### Task 21: Doctor and first real E2E
Refs: 06 §2, 07 §5.
- [x] `doctor` (tmux, git, repository root, config, plugins and their `doctor()`, CoW, DB) and `doctor --probe`.
- [x] E2E harness (`poe e2e`, fixture project, cheap rungs, results file).
- [x] E2E E1 and E2 pass (one repository per project, D20).

## Phase 5 — Resilience and operator completeness

### Task 22: Engine restart recovery
Refs: 03 §7.
- [ ] `Reconciler`: rebuild windows, attach or resume sessions, re-run current steps idempotently, integration restart.
- [ ] Integration test: kill the in-process engine at each step boundary with the fake agent. E2E E5 passes.

### Task 23: Pull request integration
Refs: 03 §6.
- [ ] `PullRequest` strategy: push, `gh pr create`, slot release with `wait_reason`, polling, merged → done, closed → `pr_closed`.
- Tests: integration with `gh` faked on PATH.

### Task 24: Parallel, escalation and intervention E2E
Refs: 07 §5.
- [ ] E2E E3, E4 and E6 pass.

## Phase 6 — Operator surfaces and onboarding

### Task 25: Remaining widgets and renderables
Refs: 06 §5.4, §6.3–6.4.
- [ ] `ui.SpeechBubble`, `ui.SplashPanel`, `ui.Gauge`; widgets `escalations`, `dialogue`, `usage`, `resources`, `docker` (hidden without Docker); `DockerSampler`.
- Tests: Textual `run_test` behaviour over seeded state; samplers against real psutil and Docker (skipped when absent).

### Task 26: Palette
Refs: 06 §5.5.
- [ ] `PaletteApp` with every action including the escalation resolution form and intervene; tmux popup and `Ctrl+P` in `dash`.
- Tests: Textual `run_test` for submit, prioritize, resolve, intervene.

### Task 27: Motion
Refs: 06 §6.5.
- [ ] `frame(state, now)` for spinner/freeze/stall, stamp flash, escalation pulse, ticking counters; 4 Hz cap; `animations = false`.
- Tests: unit tests of `frame` with `FakeClock`.

### Task 28: Sessions list and attach
Refs: 06 §5.6.
- [ ] `SessionLister` rows: session, project dir, engine state, running/queued/escalated counts, read without writing to the other project.
- [ ] `ls [--host H]` (human and `--json`) and `attach [NAME] [--host H]` with the inline picker, `switch-client` inside tmux, quoted SSH command.
- Tests: integration on `tmux_server` with two projects; SSH argv construction.

### Task 29: Init wizard
Refs: 06 §3.
- [ ] Inline Textual wizard over `operator/detectors.py` with the 7 steps; writes config, excludes `.shikiru/`, runs doctor.
- Tests: Textual `run_test` walkthrough on a tmp repository project, and rejection of a non-repository root.

### Task 30: Release readiness
- [ ] All E2E scenarios pass in one `poe e2e` run; coverage ≥ 80%; `poe check` clean.
- [ ] Scripted clean-environment check: fresh `HOME`, `uv tool install .`, README quick start.
- [ ] Walk every section of `docs/design`; code and docs agree, and each difference is fixed in one or the other.
