# Shikiru Implementation Plan

Source of truth: [`docs/design`](../docs/design/README.md). Progress: [`todo.md`](todo.md).

**Rules for every task**:
- Follow 07-engineering §2.
- `uv run poe check` must be green.
- Conventional commits; refactorings in separate commits.
- Tick the task and its ACs here and in todo.md.
- Unit tests only for `domain/` logic and parsers. Everything else gets integration tests (private tmux socket, tmp git repos, fake agent).
- Keep coverage ≥ 80% from Phase 1 onward.

**Phase order delivers the core first**:
- Phases 0–3 end in a working end-to-end pipeline (E1, E10).
- Phases 4–6 add resilience, the full operator surfaces and onboarding.

---

## Phase 0 — Foundation

### Task 1: Project scaffold and quality gate
Refs: 02 §3, 07 §1–2.
- AC1: `pyproject.toml` (uv, Python ≥ 3.14, `shikiru` script, entry-point groups `shikiru.agents` and `shikiru.widgets`); package tree as in 02 §3 with empty `__init__.py` files only where needed.
- AC2: ruff (ALL with a documented ignore list), basedpyright strict, vulture, deptry, pytest with the markers `e2e` and `integration`, pytest-timeout, coverage `fail_under = 80`, and a poe `check` task running them in order.
- AC3: `shikiru --version` works. `tests/integration/test_cli_version.py` passes.
- AC4: A `SubprocessRunner` with timeouts and typed `CommandFailed` errors, plus a `ShikiruError` hierarchy mapped to exit codes (06 §1).

## Phase 1 — Domain and persistence

### Task 2: Configuration models and loader
Refs: 05 §1.
- AC1: pydantic `Settings` covering every key in 05 §1 with `extra="forbid"`; packaged `defaults.toml`.
- AC2: Merge order defaults < user < project < `--set` (values parsed as TOML); validation errors include the file and key path.
- AC3: `config show [--json]` shows the source of each key. `config set` edits the project file with tomlkit and keeps comments.
- AC4: Rung parsing `agent:model:effort`; per-complexity ladder resolution.
- Tests: unit (merge, overrides, ladder resolution); integration (CLI `config show/set` on a tmp project).

### Task 3: Domain model and transitions
Refs: 03 §1–3, §9, §11.
- AC1: `Task`, `TaskStatus`, `Step`, `Hold`, and a transition table as data. Illegal transitions raise errors.
- AC2: `pick_rung()` (overrides, complexity, rung index, the agent-change flag) and the move-up rules (§3, §8.2).
- AC3: `pick_ready()`, with ordering and the dependency, slot, resource and quota gates, plus `explain()` (§11).
- AC4: Escalation option sets for each reason (§9).
- Tests: unit, table-driven.

### Task 4: Report models
Refs: 05 §4.
- AC1: All report models with their cross-field validators. `schema_for(kind)` exports JSON Schema.
- AC2: `shikiru report --file F` resolves `current_turn`, validates with `report.kind` and writes atomically. Exit codes 0/2/3.
- Tests: unit (validators); integration (report CLI in a tmp turn dir).

### Task 5: State store
Refs: 05 §2.
- AC1: `Database` (WAL, `busy_timeout`, `user_version` migrations) and the schema `001_initial.sql`.
- AC2: Repositories `TaskRepo`, `RunRepo`, `TurnRepo`, `EscalationRepo`, `EventLog`, `CommandQueue`, `UsageRepo`/quotas, and the engine heartbeat. Task ids are allocated atomically.
- AC3: `StateReader` with `on_change` driven by `PRAGMA data_version`.
- Tests: integration against a real SQLite file, including concurrent reader and writer processes.

## Phase 2 — Execution substrate

### Task 6: tmux client, session and layout
Refs: 06 §5.1–5.2, 07 §3.
- AC1: A typed `Tmux` client. Every call uses argv and `SHIKIRU_TMUX_SOCKET` when set. Exact targets use `=`.
- AC2: `SessionView`: create the session with the name rules and collision detection, set the session options and the `pane-died` hook, and store `SHIKIRU_PROJECT_DIR`.
- AC3: `LayoutBuilder` builds windows from the layout tree and returns `{role: pane_id}`. Window lifecycle keeps the `shikiru` window invariant.
- AC4: Pane helpers: `respawn` with env, `paste_line` (load-buffer, paste-buffer, delay, Enter), `send_keys`, `capture_visible`, `set_title`, `is_alive`.
- AC5: Protection bindings installed idempotently (06 §5.1). `tmux-event pane-died` records the event.
- Tests: integration on a private socket: layout geometry, titles, the window invariant, paste reaching `cat`, respawn on death, bindings present.

### Task 7: Workspace manager
Refs: 03 §6, 07 §3.
- AC1: Task root mirroring for monorepo and multi-repo, including the symlinked top-level entries. Worktree per repo with `shikiru/T-n-<slug>`. Submodules initialised.
- AC2: Cache cloning (clone, symlink, none) with CoW detection, `cp -c` on macOS and `--reflink=always` on Linux, and a warning fallback.
- AC3: `GitFacts` (log, diffstat, status, tracker changes) and cleanup.
- AC4: `.git/info/exclude` gets `.shikiru/` idempotently.
- Tests: integration with real repos (monorepo and multi-repo), an ignored cache dir cloned, facts correct.

### Task 8: Agent contracts, signal sources and hook plumbing
Refs: 04 §1.
- AC1: `contracts.py` exactly as in 04 §1 (capabilities, `SignalSource`, `QuotaProbe`, `HeadlessAgent`), with docstrings. `AgentRegistry` loads entry points and reports broken plugins.
- AC2: Agent-agnostic sources `ReportFileSource` and `PaneSource` (screen hash with digits masked plus process-tree CPU → `Busy`/`Quiescent`; `PaneDied`).
- AC3: `shikiru hook <role>`: flock append of trimmed JSON, no-op without `SHIKIRU_RUN_DIR`, p95 < 100 ms (measured in a test). `HookFileSource` tails from an offset through an adapter mapping.
- AC4: `UsageCalculator` (tokens × `models` prices, context %) and `QuotaMonitor` (probe cadence, stores `quotas`).
- AC5: Fake agent plugin in `tests/fixtures/fake_agent` with `hooks` and `plain` modes and scripted behaviours (07 §3).
- Tests: integration (fake agent in a private tmux pane: hooks arrive, report written, offsets resume, quiescence detected for the plain mode); unit (usage calculation).

### Task 9: Claude Code adapter
Refs: 04 §2.
- AC1: `launch_argv` and `launch_env` for new and resume sessions, `--settings` with hooks only (user settings untouched), auth env handling.
- AC2: `parse_hook` for every mapped event; `StopFailure` error-kind mapping.
- AC3: `telemetry` from the transcript (usage deduplicated by `message.id`, context used); `TranscriptSource` failure mapping from `isApiErrorMessage` entries, including `resets <time> (<tz>)` parsing; `ClaudeQuotaProbe` from `rate_limit_event`.
- AC4: `ask()` headless with a JSON schema.
- Tests: unit parsers against recorded fixtures (seeded from the verified payloads in 04 §2); integration argv snapshot.

### Task 10: Codex adapter
Refs: 04 §3.
- AC1: `launch_argv` and `launch_env` (resume, `-c` hooks, trust, `--add-dir` for the run dir and git common dirs, auth env).
- AC2: `parse_hook`; rollout discovery when `transcript_path` is null; `TranscriptSource` for the rollout.
- AC3: `telemetry` from `token_count` records (tokens, context used); `CodexQuotaProbe` from `rate_limits`; `error` events mapped to failures.
- AC4: `ask()` via `codex exec --output-schema`.
- Tests: unit parsers against recorded rollout fixtures; integration argv snapshot.

### Task 11: Generic `tui` adapter
Refs: 04 §1.2–1.3.
- AC1: `GenericTuiAgent` built purely from config (`command`, `args`, `model_args`, `effort_args`, keys, commands) with no capabilities.
- AC2: Engine degradation paths from 04 §1.2 (turn end by report plus quiescence, submission by `Busy`, handoff instead of resume, Helper `classify_screen` for failures and dialogs).
- Tests: integration with the fake agent in `plain` mode through the full turn protocol; E2E E12.

### Task 12: Doctor and agent probe
Refs: 04 §5, 06 §1.
- AC1: `doctor` checks tmux, git, Python, agent binaries and versions, login state, config validity, plugins, CoW support, the DB and `gh`/`docker` availability, as a Rich checklist or `--json`.
- AC2: `doctor --probe` runs the 5 contract checks per agent in a scratch repo on a private socket and saves recorded fixtures.
- Tests: integration with the fake agent; E2E E11 with real agents.

## Phase 3 — Orchestration core (first end-to-end)

### Task 13: Prompt renderer and templates
Refs: 03 §2, §4.
- AC1: `PromptRenderer` (Jinja2, StrictUndefined) with packaged templates `triage`, `plan`, `implement`, `rework`, `consult`, `verify`, `replan`, `conflicts`, `continue`, `handoff`, `helper/*`; overrides from `.shikiru/prompts/`.
- AC2: Every template includes the request, fenced earlier reports, the trackers, the idempotency sentence and the fixed report instruction. `verify` embeds `facts.md`.
- Tests: integration snapshot rendering for each template; override precedence.

### Task 14: Session driver (turn protocol)
Refs: 03 §4–5.
- AC1: `SessionDriver.run_turn` implements steps 1–9 of 03 §4 (ensure session, compact, inject, confirm submission, await signals, nudge, Helper rebuild hook point).
- AC2: Launch and resume in the task window pane with the env from 03 §5; `SessionStarted` stored; injected lines recorded.
- AC3: Telemetry polled every `refresh_seconds` into `runs`/`quotas`; pane titles updated.
- Tests: integration with fake agent scripts `accept`, `no-report`→nudge→report, `no-report`×3→rebuilt through a stub HeadlessAgent, and slow submission→Enter retry.

### Task 15: Task flow and steps
Refs: 03 §1–3.
- AC1: `TaskFlow` with the step classes Triage, Plan, Implement, Consult, Verify and Replan, following the review loop pseudo-code, including split children, `new_tasks`, `scope_change` events and replan outcomes.
- AC2: Triage is read-only. Unneeded worktrees are removed. A split parent releases its resources and completes when its children are terminal.
- Tests: integration flows with the fake agent: implement-accept, changes_requested×2→accept, needs_decision→consult→accept, split with dependencies, plan with enqueue, replan decompose and restate.

### Task 16: Integration (merge and PR)
Refs: 03 §7.
- AC1: Integration lock, rebase per repo, conflict hand-off turn and re-verify, round limit.
- AC2: Pre-check for all repos, ff-only merge or `update-ref`, optional push, the `integration` hold on dirty overlap with a timed retry.
- AC3: PR mode with `gh`, the `pr_open` hold and polling.
- AC4: Cleanup and the `task_done` summary.
- Tests: integration with real repos: clean ff, tracker conflict resolved by the fake agent, dirty-main hold, multi-repo pre-check failure, PR through a fake `gh`.

### Task 17: Engine daemon and scheduler
Refs: 02 §1, 03 §11.
- AC1: `shikiru engine`: flock, heartbeat, command processing loop (250 ms), scheduler ticks, one supervised coroutine per active task, graceful stop.
- AC2: `start` (detached spawn, session ensure, banner, progress) and `stop [--force]`.
- AC3: Slots, resource gate via psutil, quota gate, `explain`.
- AC4: Dispatch creates the task window (layout, titles, system and shell panes); terminal tasks close it, keeping the `shikiru` window invariant.
- Tests: integration: two tasks with `max_parallel = 1` run serially and with 2 in parallel; priority order; dependency blocking; stop and start leave state consistent (no crash recovery yet).

### Task 18: Operator service and core CLI
Refs: 06 §1, §4.
- AC1: `OperatorService` use cases: submit, queue, tasks, show, prioritize, cancel, status, events.
- AC2: Command acknowledgement with a spinner; offline application of queue-only commands; `--json` on every command.
- AC3: Rich rendering (panels, tables, OSC 8 links) with NO_COLOR and non-TTY handling.
- AC4: `init --yes`: non-interactive detection of repos, agents, trackers and caches; writes config; git exclude.
- Tests: integration CLI tests on a tmp project with the fake agent, covering human and JSON output.

### Task 19: Comic renderables, system pane core and E2E baseline
Refs: 06 §5.3, §7, 07 §5.
- AC1: `shikiru.comic` Rich renderables `TaskStrip` (all modes, states, loops, stamps, responsive widths), `SpeechBubble`, `SplashPanel` and `Banner`, used by `show`, `status` and `start`.
- AC2: `shikiru dash` Textual app with the widget contract, `StateReader` wiring, page composition, and the `strips` and `queue` widgets. `shikiru idle` panel.
- AC3: E2E E1 (monorepo) and E10 (multi-repo) pass with real Claude and Codex.
- Tests: Rich render snapshots of strips at 3 widths and every panel state; Textual `run_test` snapshots for the widgets; the E2E scenarios.

## Phase 4 — Resilience, escalation, intervention

### Task 20: Failure handling and model ladder
Refs: 03 §8.1–8.2.
- AC1: `TurnFailed` handling for each kind (quota wait and resume, next-rung policy, backoff for unavailable, context compact then handoff, auth escalation).
- AC2: Ladder move-up on rejections and unavailability; same-agent rung change resumes the session with the new model; agent change starts a handoff session; `rung` command.
- Tests: integration with fake agent scripts quota(resets_at), overload×3, context, auth; recorded real payloads drive the parsers.

### Task 21: Helper (Joshu)
Refs: 04 §4.
- AC1: `Helper` calls `rebuild_report`, `classify_failure` and `classify_screen` with templates, result models, validation and the helper ladder.
- Tests: integration with a stub HeadlessAgent; an E2E smoke test of each call against the real cheapest rung.

### Task 22: Stall detection and pane death
Refs: 03 §8.3, §8.5.
- AC1: Stall timers (tool-aware), `AttentionNeeded` trigger, screen classification actions limited to `safe_keys`, a limit of 2 stall recoveries per turn.
- AC2: `pane-died` → resume up to 2 times → next rung → `agent_crash` escalation with the kept pane output.
- Tests: integration with fake agent scripts stall, dialog and crash; E2E E7.

### Task 23: Escalations
Refs: 03 §9.
- AC1: Packet builder (headline, timeline, attempts, evidence, question, options with ★), written to a file and the DB. Slot released. Red borders.
- AC2: `escalations`, `resolve` with every option; re-queue with precedence.
- Tests: integration for each option; packet completeness snapshot.

### Task 24: Operator intervention
Refs: 03 §10.
- AC1: `say`, `peek`, `sessions`, `pause [--now]`, `resume`, `retry [--step]`.
- AC2: Manual intervention detection (hooks: unrecognised prompt; no hooks: `Busy` with no turn in flight), the `intervention` hold scoped to the affected task only, its clearing rules, `✋ operator` flag and `OPERATOR` stamp; palette "Intervene" action.
- Tests: integration with two parallel fake-agent tasks proving isolation (hold, pause and resume affect only the named task); E2E E2, E8 and E13.

### Task 25: Engine restart recovery
Refs: 03 §8.4.
- AC1: `Reconciler` rebuilds the session and windows, re-subscribes live panes or resumes sessions, consumes an existing report or re-injects the pointer, restarts integration idempotently.
- Tests: integration with SIGKILL of the engine at each step boundary using the fake agent; E2E E6.

## Phase 5 — Operator surfaces

### Task 26: Dashboard widgets
Refs: 06 §5.3.
- AC1: Widgets `project`, `escalations` (splash panels), `usage` (transcript-derived tokens and cost, quota bars with countdown and observation age), `resources` (host and instance process tree), `docker` (task attribution) and `dialogue` (speech-bubble feed), each registered as an entry point and configured through `dashboard.system`.
- Tests: Textual snapshots with seeded state; resources and docker samplers integration-tested (Docker test skipped when unavailable).

### Task 27: Command palette
Refs: 06 §5.4.
- AC1: Palette inside `dash` (Ctrl+P) and `shikiru palette` popup (`prefix + Space`) with every action in 06 §5.4, including the escalation resolution form.
- Tests: Textual `run_test` interaction tests for submit, prioritize and resolve.

### Task 28: Notifications
Refs: 06 §6.
- AC1: tmux message and status flag, OSC 777/9 passthrough, terminal-notifier / notify-send fallback, `notify.command` with event JSON; filtered by `notify.events`.
- Tests: integration with a capturing command and a private tmux socket.

### Task 29: Attach and ls (local and remote)
Refs: 06 §5.5.
- AC1: `ls [--host]`, `attach [--host] [--project]` with an inline picker, `switch-client` inside tmux, and the quoted SSH command.
- Tests: integration for picker selection and argv construction; E2E E9 (ssh localhost).

### Task 30: MCP server
Refs: 06 §3.
- AC1: FastMCP stdio server with every tool and resource in 06 §3; `wait_for_events` long-poll.
- Tests: integration through an in-memory MCP client session; E2E E5 with a real AI Operator resolving an escalation.

### Task 31: Usage and events surfaces
Refs: 06 §1.
- AC1: `usage` by agent, role and task; `events --follow` NDJSON; `status` with quotas and holds.
- Tests: integration CLI output (human and JSON).

## Phase 6 — Onboarding and completion

### Task 32: Interactive init wizard
Refs: 06 §2.
- AC1: Inline Textual wizard with the 7 steps and detected defaults (repos, agents and login, model caches, trackers, skill prefix, caches with CoW check, orchestration). Ends with doctor and offers the probe.
- Tests: Textual `run_test` walkthrough on a tmp multi-repo project.

### Task 33: Comic design polish and plain mode
Refs: 06 §7.
- AC1: Page composition with gutters, header seal, "Next up" thumbnails and margin notes; active-panel animation (screen tone, speed-line edge); kanji only as decorative marks next to English labels; state never conveyed by colour alone.
- AC2: `style = "plain"` renders the same structure without kanji, speed lines or screen tones, using ASCII-safe boxes.
- Tests: render snapshots for both styles at 3 widths.

### Task 34: Full E2E suite and release readiness
Refs: 07 §5.
- AC1: E3, E4 and E5 pass alongside the earlier scenarios (E1, E2, E6–E13).
- AC2: Coverage ≥ 80% on the non-E2E suite. All linters clean. README quick start verified on a fresh machine profile.
- AC3: `docs/design` matches the implementation (differences fixed in code or docs).
