# Shikiru Implementation Plan

Source of truth: [`docs/design`](../docs/design/README.md). Progress: [`todo.md`](todo.md).

Phase 8 target specification: [Operator Workspace](../docs/tui-workspace.md). Its tasks update the normative design as each slice ships.

Phase 9 target specification: [Compact Operator TUI](../docs/tui-focus.md). It converts the Phase 8 presentation to the approved Focus layout and Flight deck palette while preserving operator behavior.

**Rules**:
- Follow `CLAUDE.md`/`AGENTS.md` and 07 §2–3.
- Work on one task at a time.
- `uv run poe check` must be green before every commit.
- Tick each AC here and the task in `todo.md` when done.
- An AC naming an E2E scenario is ticked only after a passing `poe e2e` run; the ticking commit message states the run date, scenarios and cost.

**Order**:
- Phases 0–3 build the whole pipeline against the fake agent.
- Phase 4 makes it real (E1, E2).
- Phase 4b moves agents to headless turns on resumed sessions with panes as live views (D1 revised, D21, D22, D23). Its tasks are numbered 31–39 so earlier numbers stay stable; do them before Phase 5. Task 38 (code health) runs alongside: its correctness items first, the rest together with the Phase 4b tasks they touch.
- Phases 5–6 complete resilience, operator surfaces and onboarding.
- Phase 7 (Tasks 41–59) remediates the 2026-09-29 review. Task 41 comes first because it makes the gate reliable; then follow the listed order, since high-severity work and operator-control fixes precede the rest.
- Phase 7b (Tasks 60–74) completes the independent-review remediation before Phase 8 relies on its operator-control and work-preservation behaviour.
- Phase 8 (Tasks 75–88) delivers the Operator Workspace: navigation and task actions first, then attention/evidence views, responsive behaviour, exports and final documentation/verification. Follow the listed order and keep refactorings in separate commits.
- Phase 9 (Tasks 89–96) delivers the approved compact TUI. Execute these tasks sequentially, review each completed task before advancing, and keep necessary refactorings in separate commits. Phase 8 completion and older independently open review gates remain historical facts.

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
_Superseded by Phase 4b (D1 revised): `activity.is_operator_prompt` is removed by Task 35._

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
_Superseded by Phase 4b (D1 revised): `paste_line`, `send_keys` and `PaneHandle` are removed by Task 35._

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
_Superseded by Phase 4b (D1 revised): the contract is replaced by Task 32; `HookRecorder`/`shikiru hook` by Task 35; `PriceTable` by Task 31._

### Task 9: Fake agent and contract test kit
Refs: 04 §3, 07 §3.
- [x] `AgentContractSuite` with offline and live checks.
- [x] Fake agent as a test-only package `tests/fake_agent` (uv dev dependency, own `shikiru.agents` entry point): scripted REPL using `HookRecorder`, headless `ask` via script; passes the suite on `tmux_server`.
_Superseded by Phase 4b (D1 revised): the REPL fake agent and live-pane checks are replaced by Task 32._

### Task 10: Prompts and Helper
Refs: 03 §2, §4.3; 04 §6.
- [x] `PromptRenderer` with packaged templates `triage`, `plan`, `implement`, `rework`, `consult`, `verify`, `conflicts`, `continue`, `handoff`; `.shikiru/prompts/` overrides.
- [x] `Helper.rebuild_report` and `Helper.classify_screen` over `headless()` with validated results.
- Tests: property tests (request and report instruction present, fenced reports, `StrictUndefined` raises); Helper against the fake agent's headless script.
_Superseded by Phase 4b (D1 revised): `classify_screen` is replaced by `classify_failure` in Task 35._

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
_Superseded by Phase 4b (D1 revised): launch/attach/exit, pane death, screen stall and typed-prompt intervention are replaced by Task 35._

### Task 13: Turn runner
Refs: 03 §4.3.
- [x] Turn dir and `current_turn`, `before_turn` lines, pointer injection with recorded injected set, submit confirmation with one Enter retry, await, nudges, Helper rebuild (`helper.json`), `TurnResult` variants.
- Tests: integration: accept; no report → nudge → report; no report ×3 → rebuilt; unconfirmed submit → retry → `Stalled`.
_Superseded by Phase 4b (D1 revised): pointer injection, `before_turn` and submit confirmation are replaced by Task 35._

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
_Superseded by Phase 4b (D1 revised): relaunch and `classify_screen` actions are replaced by Task 35._

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
_Superseded by Phase 4b (D1 revised): `QuotaPoller` is removed by Task 35 (quota comes from turn streams)._

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
_Superseded by Phase 4b (D1 revised): the interactive plugin is replaced by Task 33._

### Task 20: Codex plugin
Refs: 04 §5.
- [x] Factory and options; launch/resume/attach (`-c` hooks, trust, `--add-dir`); hook signals; rollout discovery, usage, failures, quota; compaction `before_turn`; keys; `resume_command`; `headless`; `doctor`.
- [x] Offline contract checks with committed samples; live contract checks pass.
_Superseded by Phase 4b (D1 revised): the interactive plugin is replaced by Task 34._

### Task 21: Doctor and first real E2E
Refs: 06 §2, 07 §5.
- [x] `doctor` (tmux, git, repository root, config, plugins and their `doctor()`, CoW, DB) and `doctor --probe`.
- [x] E2E harness (`poe e2e`, fixture project, cheap rungs, results file).
- [x] E2E E1 and E2 pass (one repository per project, D20).
_Superseded by Phase 4b (D1 revised): `doctor --probe` and E1/E2 are re-done on headless turns by Task 37._

## Phase 4b — Headless agents

### Task 31: Usage rules and price-table removal
Refs: 01 D21, 04 §1–2, 05 §1, 06 §5.3–5.4.
- [x] `PriceTable` and both `prices.toml` removed; plugins report `cost_usd = None`, `cost_basis = "unknown"` wherever the agent reports no priced cost, and `context_used_pct = None` without a reliable window.
- [x] Title fields `context` and `cost` are preformatted and render `–` when missing; default title templates per 05 §1.
- Tests: unit for the title fields with and without values; plugin offline checks updated to the new expectations.

### Task 38: Code health from the 2026-09-25 review
Refs: [review-2026-09-25.md](review-2026-09-25.md), AGENTS.md.
Do the fixes marked _After/With Task N_ inside or after those tasks. Findings on code that Phase 4b replaces are out of scope. Close each finding, or record in the review file why it was dropped.
- [x] Correctness first:
  - E2 (a non-`ShikiruError` still stops the daemon)
  - E4 (dispatch failures are silent)
  - E5 (`integration = "pr"` merges locally without saying so)
  - F1 (default widgets crash `dash`)
  - F2 (caches are never cloned: wire them or delete them)
  - C8 (Helper config fails mid-task)
  - C11 (a corrupt report is silently treated as "no report")
  - C12 (`--set` silently turns invalid TOML into a string)
  - T1 (the FakeClock drive pattern runs an extra turn)
  - T2 (ordinary runs overwrite E2E results)
  - T4 (a missing pane counts as alive)
- [x] Design debts:
  - E1 (one escalate sequence)
  - E3 (the consult answer is persisted, not held in memory)
  - E6 (the conflict rebase matches 03 §6)
  - E7 (conflict context is passed explicitly)
  - E10 (one terminal cleanup)
  - E11 (integration summary)
  - E12 (handlers wired in bootstrap)
  - F4, F5, F10 (dashboard views)
  - F6, F7 (plugin checks)
  - F8, C13 (one layout module)
  - F12 (one operator services type)
  - C6 (one reference check)
  - C7 (typed `status_reason`)
  - C9 (step duplication)
  - C10 (drop `Commit.repo`)
  - C17 (`RepoConfig` injected into `TaskRootBuilder`)
- [x] Stale code removed: E13–E24, F11, F13–F23, F25, C2–C5, C14–C16, C18.
- [x] Tests follow AGENTS.md:
  - T3, T6–T12 (one table per rule, no duplicates)
  - T13–T16 (misleading or fake tests)
  - T17, T18 (no stand-ins for our own classes)
  - T19–T25 (trivial, flaky or literal-driven tests)
  - T5 (FakeClock by default)
- [x] With Phase 4b tasks: E8, E9 (Task 35), E25, F9 (Task 36). F24 is closed by Tasks 33–34. F3 and C1 duplicate E5 and F2.
- Tests: every behaviour fix above gets a behaviour test that fails before the fix. The gate stays green, and coverage stays at or above 80% without tests added only for coverage.

### Task 32: Headless contract, SDK, contract kit and fake agent
Refs: 04 §1–3, 02 §2, 07 §3–4.
- [x] `agents/contracts.py` per 04 §1 (`Agent`, `Turn`, `TurnSpec`, signals incl. `Compacted`, `FailureKind.UNKNOWN`, obligation 8), with docstrings.
- [x] `SubprocessRunner.spawn` (stdin closed, stdout lines, stderr captured, terminate with `TURN_STOP_GRACE`); SDK `TurnParser` and `StreamingTurn` (raw stream and stderr copied to the turn dir, obligations 1 and 3 enforced, a process sequence per turn for compaction).
- [x] `AgentContractSuite` offline checks 1–4 replay recorded streams (04 §3); live checks 1–6.
- [x] Fake agent rewritten as a per-turn fake CLI with session resume by id and a REPL `interactive_command`; passes the suite.
- Tests: unit for obligation enforcement over synthetic parsers; integration for `spawn`/`stop` with a real child process.

### Task 33: Claude Code headless plugin
Refs: 04 §4.
- [x] Turn argv (new, resume, model change), Operator-provided access environment pass-through, stream-json parser, provider-reported quota and cost, failure mapping incl. stderr tags, `interactive_command`, `headless`, `doctor`.
- [x] Recorded samples under `tests/fixtures/agents/claude/` exercise native Claude Code protocol behavior: genuine redacted success and report captures; provider-reported quota, list-priced or unknown-price usage, rate-limit and credit-exhaustion failures and an unrecognised model as synthetic scenarios, since they cannot be provoked on demand (fixture README); offline replay passes.

Scope change (2026-09-26): compaction and the full live contract suite moved to Task 39, which replaces the between-turn compaction design with plugin-owned in-turn compaction (D23).

Task 33 verification note (2026-09-25): `uv run poe check` passes with a dedicated `TMUX_TMPDIR` (584 passed, 14 skipped; coverage above the 80% gate). An offline replay now includes a genuine redacted OpenRouter stream. The default sandbox's host-only probe failed with `gaierror`; the same host-only probe with network escalation returned HTTP 200. With the local settings environment loaded only into the Claude subprocess, one short Claude Code 2.1.281 turn on the configured Haiku model emitted `SessionStarted`, `Message`, non-zero `UsageReported`, and `TurnEnded`. This shows the earlier retry-only timeout was caused by the sandbox network restriction; no auth, model, or protocol failure reproduced after escalation. The other genuine sample types, full live contract suite, and `/compact` after context overflow remain open. Overflow was not attempted.

Task 33 live verification follow-up (2026-09-25): the configured OpenRouter host resolved under escalation; a credential-free `HEAD` to its origin returned HTTP 403. The bounded live suite produced three passes (usage, stop/resume, headless ask) and three failures. The report check did not pass even though its captured stream had a success terminal; a sanitized diagnostic did not preserve whether the report artifact was missing or invalid. The other two failures exposed missing prompt imports and scratch-repo setup in the new live-test overrides; those test setup errors were corrected. The model-change test is also invalidly configured because the configured Haiku and Sonnet model IDs are identical, so it now skips in that case. The live low-compaction check now skips when the provider omits `context_used_pct`, as required by D21. After correcting the test setup, a model-memory attempt hit three genuine `api_retry` events with HTTP 429 before the 60-second turn deadline and emitted no terminal result. This is an endpoint rate-limit response, with no evidence of an auth or stream-protocol rejection. The provider does not report a reliable context window, and the current 429 blocks another bounded compaction call, so no genuine `/compact` boundary was captured; context overflow was not attempted. The live report artifact, a real model change, and live compaction remain unverified. Task 33 ACs remain open.

Task 33 live verification follow-up (2026-09-25): after the OpenRouter token was updated, the non-tilde `anthropic/claude-haiku-latest` model identifier returned HTTP 400 with no usage. OpenRouter's current Claude Code guide configures the alias as `~anthropic/claude-haiku-latest`; one bounded, content-free turn using that alias succeeded, started a session, emitted a message, and ended successfully. Its OpenRouter generation record identifies `anthropic/claude-4.5-haiku-20251001` via Amazon Bedrock and bills $0.00786875; Claude Code's own $0.03934375 estimate has `costBasis=unknown`, so it is not used as billed cost. Immediate key/account aggregate deltas were zero; the generation record provided the per-request charge. The first report-artifact attempt was rejected because its repository-derived prompt/context was not isolated; it was later rerun successfully with the audited synthetic scratch payload (see the report verification note below). Previous live calls cannot be itemized against the refreshed key or generation IDs; the activity endpoint requires a management key, which this key does not have. This content-free turn does not verify memory/resume, `/compact`, or overflow. The captured context value is not reliable context-use evidence, as detailed in the audit note below. No separate direct Anthropic API capture or subscription-style quota window is required or inferred for API access. Other live checks and overflow compaction remain open; no Task 33 AC or todo item is complete.

Task 33 report-payload audit (2026-09-25): the live report check uses a clean temporary git repo and run directory. The prompt file contains one fixed `shikiru_command("report", "--file")` invocation against prepared JSON input, forbids other file inspection or commands, and requests only `REPORT_OK` after success. The input JSON is fixed synthetic data; `report.kind` and the generated `ImplementationReport` schema are the only turn files reachable through `current_turn`. Claude's cwd is the empty scratch repo; its sole `--add-dir` is the scratch run directory; `HOME` and `CLAUDE_CONFIG_DIR` point to empty scratch directories. A local-only audit verifies the file manifest, command paths, prompt, schema, and isolation without calling Claude or OpenRouter. The test-only launcher supplies the Operator's native Claude environment; the plugin has no access mode or credential handling.

Task 33 live report verification (2026-09-25): one bounded Claude Code run through OpenRouter's documented Anthropic-compatible API setup, using the tilde Haiku alias and only `Read,Bash` tools, completed the audited first-turn report check. Claude read the prompt file, invoked `shikiru report` with fixed synthetic JSON, produced a schema-valid report, and emitted `TurnEnded`. A redacted normalized replay excerpt is at `tests/fixtures/agents/claude/turn_openrouter_report_live_redacted.jsonl`; it contains no credentials, prompt text, paths, command contents, tool output, or private response text. The current-key usage endpoint measured $0.05804775 before the run, unchanged immediately after, and $0.076317 after delayed settlement, an aggregate key-usage delta of $0.01826925. Claude Code's estimate was $0.069104 with `costBasis=unknown`; it is not treated as billed cost. This satisfies the report-contract live check through this route; it does not require a separate direct Anthropic API key capture. No genuine provider quota-window or error stream was captured, and the remaining live checks and compaction after overflow remain open. Synthetic quota/error scenarios remain synthetic; no Task 33 AC or todo item is complete.

Task 33 access design correction (2026-09-25): Core Shikiru configuration selects agent, model and effort and contains no provider-access configuration or credential handling. Generic agent options remain a plugin-owned extension point; a plugin may define access options where its provider requires them. The current Claude implementation uses Operator-managed native files/environment, passes inherited environment through, and defines no access mode or credential option. Quota signals are emitted whenever Claude reports `rate_limit_event`; list-priced `total_cost_usd` is reported as `api_equivalent` independent of access. HTTP 402 payment/credit exhaustion maps to the generic immediate `AUTH` escalation, so it does not enter the 15-minute unknown-reset quota probe. Offline behavior checks cover turn and headless environment pass-through, emitted quota, provider list-cost accounting and 402 recovery. This supersedes the earlier test-payload audit's credential-filtering expectation. The Codex reconciliation is recorded in the Task 34 access-design correction below. Compaction after overflow and the designated live checks remain unverified; Task 33 remains incomplete and todo stays open.

Task 33 local Claude session audit (2026-09-25): read-only inspection covered 348 local Claude session logs without printing or retaining their private content. Five genuine `compact_boundary` events were present, all marked manual; one minimal redacted excerpt is documented in `tests/fixtures/agents/claude/README.md`. These are transcript events (`compactMetadata`), not Shikiru subprocess stream captures, and none proves `/compact` after context overflow. Ten structured `api_error` events carried HTTP 401; no `rate_limit_event` or structured context/overflow error was found. Free-text context matches were not treated as errors because they could not be distinguished from ordinary discussion. The configured alternate model was identical at audit time, so the model-change check skipped. It now reads distinct Operator-supplied Claude model-class values when available; no live model-change check has been run with distinct values. The genuine stream reports a nominal 200k context window, but the official OpenRouter guide notes Claude Code assumes 200k unless a `[1m]` suffix is used; this value therefore does not establish a reliably provider-reported window. OpenRouter's guide says only its Anthropic first-party provider is guaranteed for Claude Code, while the recorded generation used Amazon Bedrock. No rate-limit event, overflow error, reliable context-use percentage, or overflow compaction is evidenced. Earlier spend predates the refreshed key and cannot be itemized. Task 33 ACs and todo remain open.

### Task 34: Codex headless plugin
Refs: 04 §5.
- [x] `exec` / `exec resume` argv with closed stdin, workspace-write sandbox with the run dir and the git common dir writable (verify persistence across `exec resume`), `--json` parser, error-message JSON mapping, rollout context and `rate_limits`, `interactive_command`, `headless`, `doctor`.
- [x] Compaction per 04 §5 as designed then: `model_auto_compact_token_limit` from `compact_at_pct` and the model's window on every turn, compact-and-rerun on a context failure, `Compacted` from the rollout's `compacted` entry. Superseded by Task 39 (in-turn compaction).
- [x] Recorded samples under `tests/fixtures/agents/codex/` (first turn, resume with model change, usage limit, API key with null `rate_limits`); offline checks pass; live checks pass, including a commit from the worktree. First-turn and resume-with-model-change samples are genuine redacted 0.156.1 captures; the low-threshold compaction sample is a genuine redacted protocol excerpt. The prior live suite and worktree commit check pass. The OpenRouter fixture is a genuine API-key turn with null quota windows through a native custom provider; it is distinct from a direct OpenAI API-key sample. A genuine headless usage-limit capture cannot be provoked on demand; usage-limit and error classification are synthetic offline coverage, documented in the fixture README (accepted 2026-09-26).

Task 34 verification note (2026-09-25): `uv run poe check` passes (696 passed, 28 skipped; coverage exceeds the 80% gate). All six live contract checks pass, as do the isolated worktree model-change persistence check and doctor (8 passed in 139.12s). The later OpenRouter fixture provides a genuine API-key/null-quota sample; a genuine usage-limit sample remains unavailable.

Task 34 OpenRouter custom-provider follow-up (2026-09-25): the same token used for the Claude experiment was loaded at runtime from the ignored local settings backup. OpenRouter's read-only key and model requests reported $0.076317 aggregate usage and listed `openai/gpt-6-luna` with a 1,050,000-token context. A disposable user-level Codex config used `https://openrouter.ai/api/v1`, `wire_api = "responses"`, command-based auth, model `openai/gpt-6-luna`, and low reasoning effort. Codex 0.156.1's read-only model catalog returned that model and context metadata without an unknown-model/fallback warning. At that time, the plugin's now-removed `api_key` mode with `api_key_env = "OPENROUTER_API_KEY"` passed the token to Codex. One content-free turn in an empty scratch Git repository returned `OK` with `SessionStarted`, `Message`, `UsageReported` (12,340 input and 5 output tokens), and `TurnEnded`; it made no tool calls and emitted no quota windows. The final rollout had null primary and secondary `rate_limits`; a redacted OpenRouter-specific fixture is registered for offline replay. This is OpenRouter API-key evidence, not a direct OpenAI API-key sample. Usage was still $0.076317 immediately after the turn and settled at $0.077860 (+$0.001543), well under the experiment's $2 ceiling. The original live signal had `context_used_pct = None` because `thread.started` could arrive before a new session's rollout file existed, and the parser did not retry rollout discovery at finish. `finish()` now resolves the rollout again when it was initially absent; a focused behavior test verifies that a late-created rollout contributes context, quota, and compaction signals. Replay of the recorded final rollout derives 1.237% from its 997,500-token context window. The usage-limit sample remains synthetic; keep the final Task 34 AC and todo item open.

Task 34 access-design correction (2026-09-25): removed Codex's `auth` and `api_key_env` options and all `OPENAI_API_KEY`/`CODEX_API_KEY` rewriting. Turn children inherit the Operator process environment with `TurnSpec.env` overrides; headless and doctor inherit the process environment, including the Operator's `CODEX_HOME`. Offline boundary tests assert a turn-specific `CODEX_HOME` overrides a different process `CODEX_HOME` and provider variables survive unchanged in turn, headless, and doctor child environments. Doctor continues to check the built-in Codex login status, but recognizes a selected custom provider in user-level `$CODEX_HOME/config.toml` and reports its credentials as unverified instead of incorrectly failing against OpenAI login status. With a disposable custom-provider `CODEX_HOME`, the installed CLI's local `codex login status` returned `Not logged in`; the doctor behavior test uses a fake CLI to verify the command is skipped and provider environment is preserved. No provider request was made. HTTP 402 payment/credit exhaustion now maps to `AUTH` and immediate `agent_auth` escalation; a 429 with a reported reset remains `QUOTA` and waits until the reset plus the quota margin. `uv run poe check` passes (592 passed, 14 skipped, 87.67% coverage). The Task 34 AC and todo item remain open because the genuine usage-limit capture is unavailable.

### Task 35: Engine on process-per-turn; screen machinery removed
Refs: 03 §4, §7, 04 §6, 05 §1–2.
- [x] `SessionManager.session_for` per 03 §4.1; `TurnMonitor` (single consumer, run facts, `quotas` from `QuotaReported`, `view.jsonl`, decorations, stall → `stop()`); `TurnRunner` per 03 §4.3 incl. resumed nudge turns and `Helper.classify_failure` with `CLASSIFY_CONFIDENCE`.
- [x] Recovery per 03 §7 (`Stalled` → resumed `continue`, quota block → resumed `continue`); cancellation stops the turn; `turn.pid` written and removed.
- [x] Removed: `InteractiveAgent`/`AgentSession`/`PaneHandle`, keys, pointer injection and submit confirmation, `before_turn` (compaction moves into the plugins, 04 §1 obligation 8), `HookRecorder` and `shikiru hook`, `activity.is_operator_prompt`, `Helper.classify_screen`, `QuotaPoller` and `quota()`, `timeouts.launch`, `compact_below_free_pct`, `runs.agent_state` (migration), `agents.<name>.prompt_prefix` (a slash or `$` skill prefix does not work in headless turns; projects name skills in their own instructions).
- Tests: integration with the fake CLI: accept; a `Compacted` signal becomes a `compacted` event; changes_requested → accept on one resumed session; no report → nudge turn; rebuild; stall stop → resume; unknown failure classified above and below the threshold; model change resumes the session; different agent → handoff.

### Task 36: Pane views, take over / hand back, queued `say`
Refs: 03 §9, 06 §2, §4, §5.1–5.2.
- [x] `shikiru view T-n ROLE` renders `view.jsonl` live (`ui.StreamLine`); role panes run it; `LayoutGuard` respawns dead view panes.
- [x] `TakeOver`/`HandBack` commands, handlers and `Takeover` per 03 §9; MCP `take_over`/`hand_back`; a dead taken-over pane hands back; `taken_over`/`handed_back` events.
- [x] `Say` queues `messages/<role>/NNN.md`, delivered into the role's next turn dir and listed in its prompt.
- Tests: integration on `tmux_server` with the fake agent: the view shows a turn's messages and tool calls; take over stops the turn, holds only that task and runs the REPL; exiting the REPL hands back and the step completes on the same session; a message sent mid-turn appears in the next prompt of that role only.

### Task 37: Probe and real E2E on headless turns
Refs: 04 §3, 06 §2, 07 §5.
- [x] `doctor --probe` runs live check 1 per ladder agent in `.shikiru/probe/`, reporting the stream tail and stderr on failure.
- [x] E2E E1 and E2 (queued delivery, 07 §5) pass.

### Task 39: Proactive context compaction within a turn
Refs: 01 D23, 03 §4.1, §7, 04 §1 obligation 8, §3–5.

Spike (2026-09-26, Claude Code 2.1.282, codex-cli 0.157.1): Claude `-p` never compacts within a turn; a plugin `PostToolUse` hook returning `continue: false` stops the turn before an over-threshold request, and `/compact` plus a continue prompt completed a 12-file turn with six compactions. Codex's native limit compacts within a running turn before the exceeding request.
- [x] Clarify the agent-agnostic requirement: configurable threshold (default 80%), prevention within a running turn, session and task continuity, and exceptional overflow recovery. Distinguish previous between-turn evidence from compliance with this requirement.
- [x] Core owns no context handling: `FailureKind.CONTEXT`, its recovery branch, the handoff on a context failure in `session_for` and `CONTEXT` in the Helper's classification are removed; an unrecovered context failure is the plugin's `TurnFailed(OTHER)`. `Compacted` stays a display-only signal and event.
- [x] Offline contract check 3 per 04 §3: threshold crossing and repeated compaction within one turn continue to one `TurnEnded`; context failure → compaction and rerun → `TurnEnded`; failed recovery → `TurnFailed(OTHER)`.
- [x] Claude compacts within a running turn per 04 §4 through the plugin's `PostToolUse` hook, `/compact` and a continue prompt, independent of the display `context_used_pct`; a resumed turn over the threshold compacts first.
- [x] Codex per 04 §5: `post_tokens` from the first non-zero `token_count`; a model with no known window is reported by doctor instead of silently running unprotected.
- [x] Live contract checks 1–6 pass for both agents in the Operator's configuration; check 6 needs no second Shikiru turn to trigger compaction.
- [x] `uv run poe check` passes; record CLI versions, models and genuine redacted evidence.

Task 39 verification note (2026-09-26): `uv run poe check` passes. Live contract suites ran with `SHIKIRU_E2E=1` in the Operator's native agent configuration and subscriptions (no isolated homes, no copied credentials): Claude Code 2.1.282 on `haiku` (resolves to `claude-haiku-4-5-20251001`) with `low` effort, and `sonnet` for the model change; codex-cli 0.157.1 on `gpt-5.6-luna` with `low` effort, and `gpt-5.6-terra` for the model change. Claude: all seven live checks pass (checks 1–6 plus the report-payload audit). Codex: all eight live checks pass (checks 1–6, doctor with the models' context windows, and worktree commits across `exec resume`); the first run failed checks 1 and 6 because the test spec omitted `SHIKIRU_RUN_DIR`/`SHIKIRU_ROLE`, which the engine always sets; after the fix both passed and the full suite was rerun. Check 6 evidence, one new Shikiru turn each, reading six generated ~39 KB files: Claude at `compact_at_pct = 25` (50,000 of the assumed 200,000 tokens) ran seven processes in one `Turn`; the plugin hook stopped it three times (`terminal_reason: "hook_stopped"`), each `/compact` ran below the limit (pre 48,825 / 50,093 / 50,331 → post 2,685 / 2,812 / 2,888 tokens), and the turn completed with a valid report and its nonce (list-priced estimate $0.30). Codex at `compact_at_pct = 15` (40,800 of 272,000 from the model list) compacted twice inside the running turn (last input 45,264 and 45,831 tokens before each `compacted`, window 258,400) and completed with a valid report and its nonce. Genuine redacted fixtures: `tests/fixtures/agents/claude/turn_hook_*_live_redacted.jsonl` and `tests/fixtures/agents/codex/*midturn_compaction_live_redacted.jsonl` (spike captures, documented in the fixture READMEs). `/compact` on an already overflowed Claude session was not provoked; context-failure recovery stays covered by synthetic offline scenarios.

Task 39 Codex threshold note (2026-09-26): codex-cli 0.157.1 compacts at the next request boundary after its reported usage reaches `model_auto_compact_token_limit`, not before the request that would cross it: live check 6 sent requests of 45,264 and 45,831 input tokens against a 40,800-token limit before its two compactions. 04 §5 now says the headroom above `compact_at_pct` must absorb one request's pending tool output.

### Task 40: Probe every configured rung
Refs: 04 §3, 06 §2.
- [x] `doctor --probe` runs live check 1 on each agent's first rung and a smoke turn ("Hi", must end in `TurnEnded`) on every other distinct rung (agent, model, effort) across the configured ladders, reporting each rung's result and the stream tail and stderr on failure.
- Tests: integration with the fake agent: a ladder with several rungs per agent probes each distinct rung once; a failing rung is reported by agent, model and effort.

## Phase 5 — Resilience and operator completeness

### Task 22: Engine restart recovery
Refs: 03 §7.
- [x] `Reconciler`: rebuild windows, end orphaned turn processes (`turn.pid`), re-run current steps idempotently on resumed sessions, integration restart.
- [x] Integration test: kill the in-process engine at each step boundary with the fake agent. E2E E5 passes.

### Task 23: Pull request integration
Refs: 03 §6.
- [x] `PullRequest` strategy: push, `gh pr create`, slot release with `wait_reason`, polling, merged → done, closed → `pr_closed`.
- Tests: integration with `gh` faked on PATH.

### Task 24: Parallel, escalation and intervention E2E
Refs: 07 §5.
- [x] E2E E3, E4 and E6 pass.

## Phase 6 — Operator surfaces and onboarding

### Task 25: Remaining widgets and renderables
Refs: 06 §5.4, §6.3–6.4.
- [x] `ui.SpeechBubble`, `ui.SplashPanel`, `ui.Gauge`; widgets `escalations`, `dialogue`, `usage`, `resources`, `docker` (hidden without Docker); `DockerSampler`.
- Tests: Textual `run_test` behaviour over seeded state; samplers against real psutil and Docker (skipped when absent).

### Task 26: Palette
Refs: 06 §5.5.
- [x] `PaletteApp` with every action including the escalation resolution form and take over / hand back; tmux popup and `Ctrl+P` in `dash`.
- Tests: Textual `run_test` for submit, prioritize, resolve, take over.

### Task 27: Motion
Refs: 06 §6.5.
- [x] `frame(state, now)` for spinner/freeze/stall, stamp flash, escalation pulse, ticking counters; 4 Hz cap; `animations = false`.
- Tests: unit tests of `frame` with `FakeClock`.

### Task 28: Sessions list and attach
Refs: 06 §5.6.
- [x] `SessionLister` rows: session, project dir, engine state, running/queued/escalated counts, read without writing to the other project.
- [x] `ls [--host H]` (human and `--json`) and `attach [NAME] [--host H]` with the inline picker, `switch-client` inside tmux, quoted SSH command.
- Tests: integration on `tmux_server` with two projects; SSH argv construction.

### Task 29: Init wizard
Refs: 06 §3.
- [x] Inline Textual wizard over `operator/detectors.py` with the 7 steps; writes config, excludes `.shikiru/`, runs doctor.
- [x] Tests: Textual `run_test` walkthrough on a tmp repository project, and rejection of a non-repository root.

### Task 30: Release readiness
- [x] All E2E scenarios pass in one `poe e2e` run; coverage ≥ 80%; `poe check` clean.
- [x] Scripted clean-environment check: fresh `HOME`, `uv tool install .`, README quick start.
- [x] Walk every section of `docs/design`; code and docs agree, and each difference is fixed in one or the other.

## Phase 7 — Review remediation (2026-09-29)

Source: the 2026-09-29 review of `e56832b` (finding ids F01–F23). Every finding was re-checked against the source on 2026-09-29. Tasks 41–59 are ordered by risk: work preservation, operator control and dependent-work correctness first. Each task adds its regression test first, against the real boundary named in it (real SQLite, Git and tmux, the fake agent). Design text changes in the same commit as the code it describes.

Invariants these tasks establish:
1. A task workspace has one owner: automation or operator takeover.
2. A hold blocks the next operation on every execution path, including recovery and integration.
3. `done` implies the intended changes are integrated or explicitly accounted for; cleanup never erases unexplained dirtiness.
4. A dependent's base contains the completed work it depends on, including remote and decomposed work.
5. Durable command, message and escalation state survives interruption between effects.

### Task 41: Quality gate independent of the terminal (F17)
Refs: 07 §2–3.
- [x] `test_usage_panel_labels_api_equivalent_cost_and_hides_incomplete_totals` passes under `TERM=dumb`, a narrow `COLUMNS` and the ordinary terminal (it fails today with `TERM=dumb`): it compares label and value text after normalizing layout whitespace, and the render helper does not inherit terminal settings.
- [x] Other `_plain`-style render helpers in `tests/` are checked for the same dependence.
- [x] `uv run poe check` passes with `TERM=dumb` and with the default environment.
- [x] `test_engine_restart` no longer flakes under load (root cause: engine tests capped `max_cpu_pct` at 100 while the gate is a strict `<` on a windowed average, so a saturated host held every task in `wait_reason=resources`; they now use `CPU_UNCAPPED_PCT`).

### Task 42: Integration never discards uncommitted work (F01)
Refs: 03 §5–6, invariant 3.
- [x] Integration refuses a task worktree with tracked or untracked changes at its shared entry, including the no-op path (branch SHA equals base) and plan tasks, and keeps the worktree.
- [x] Post-rebase acceptance after conflict resolution applies the same check; an accepting Leader with uncommitted changes gets the existing rework path, not a merge.
- [x] A clean no-op task still finishes `done`.
- Tests: integration with real Git: uncommitted tracked and untracked plan output; dirty post-rebase acceptance; clean no-op. Each asserts the task outcome and that the files still exist.

### Task 43: Holds block every execution path (F04)
Refs: 03 §1.2, §7, §9, invariant 2.
- [x] One turn-boundary hold check is used by ordinary steps, integration entry, each integration turn and the reconciler; the task returns without starting the next external effect while `paused` or `intervention` is held.
- [x] Decision recorded in 03 §6: an already-running Git operation may finish, no further step starts.
- [x] A hold set while integration waits for access is honoured before the merge.
- Tests: integration: restart with a paused `INTEGRATING` task does not rebase, run a turn or merge until `resume`; pause at the verify → integrate boundary; pause while waiting for integration access.

### Task 44: One owner for a task workspace during takeover (F03)
Refs: 03 §4.1, §9, invariant 1.
- [x] `resume` on a task with a taken-over role is rejected with an actionable message, or performs the full hand-back; the choice is written in 03 §9.
- [x] `hand_back` releases the intervention hold only when no role is taken over; a partial hand-back keeps it.
- [x] No headless turn starts while any role is taken over, and the original session is resumed after hand-back rather than replaced.
- Tests: integration on `tmux_server` with the fake agent: resume during takeover; take over both roles and hand back one; pause plus takeover. Assert pane and process ownership, run and session identity, holds and the absence of headless turns.

### Task 45: Stale escalation actions cannot resurrect work (F05)
Refs: 03 §8, 06 §2.
- [x] Direct `cancel` and `retry` settle the task's open escalations in the same transaction as the task transition.
- [x] `Resolve` is checked against the task's current state and rejects an escalation that no longer owns it.
- [x] A presentation failure (title refresh) after the durable transition does not turn the acknowledgement into a rejection while leaving the transition applied; the primary state and the acknowledgement agree.
- Tests: integration through the daemon and command queue: escalate → cancel → stale resolve (stays cancelled, rejected); escalate → retry → stale resolve; repeated resolve; title-refresh failure after resolve.

### Task 46: Turn processes own their descendants (F02)
Refs: 03 §4.3, §7, 04 §6.
- [x] `SubprocessRunner.spawn` starts each turn in its own process group; `stop()` signals the group, then kills it after the grace period, and its wait for stderr EOF is bounded.
- [x] Orphan cleanup after a restart signals the recorded group only while the recorded PID identity still matches.
- Tests: integration with a real runner: a descendant that inherits stdout/stderr and outlives or ignores its parent; `stop(grace)` returns within a bound and the descendant is gone; orphan cleanup ends it and never signals a reused PID.

### Task 47: Dependents start from the merged base in PR mode (F06)
Refs: 03 §6, invariant 4.
- [x] Decision written in 03 §6: after a PR is merged, the engine fetches the remote base and creates dependents' worktrees from the verified fetched ref. The operator's checked-out branch and local changes are never reset.
- [x] Dependents are not released until the merged remote base is fetched and verified.
- [x] Squash and rebase merges are handled.
- Tests: integration with a local bare remote and `gh` faked on PATH: merge T-1 remotely (merge commit and squash); T-2's worktree contains T-1's change.

### Task 48: Decomposition delivers tracker changes to children (F07)
Refs: 03 §2–3, §6, invariant 4.
- [x] Design decision first, written in 03 before code: plan children wait until the plan parent's tracker changes are on the base (for example an implicit dependency on the parent's `done`); for `supersede`, the approved replacement tracker entries reach the replacements without integrating the rejected implementation. The dependency cascade on a cancelled parent must not cancel the replacements.
- [x] Scheduling and `apply_scope_change` implement the decision; child worktrees contain the tracker entries the parent committed.
- Tests: integration with real Git: a blocked plan integration keeps its children undispatched, then releasing it dispatches them with the tracker entries present; supersede with committed replacement tracker entries leaves them available to the replacements.

### Task 49: Transitions persist triage results and keep concurrent overrides (F09, F10)
Refs: 03 §2–3, 05 §2.
- [x] A completed triage persists the selected complexity and title with the transition (an explicit operator complexity has the precedence written in 03 §2); the next turn uses the ladder for the persisted complexity.
- [x] A `set_rung` acknowledged while a turn runs is kept when that turn completes and is used by the next invocation; rung-change counter semantics are unchanged.
- Tests: integration: triage `high` → the next turn's rung is the high ladder and the title updates; `SetRung` during a controlled in-flight turn → the following turn uses it.

### Task 50: Dependency graph validation (F11)
Refs: 03 §10.
- [x] `apply_scope_change` validates the resulting graph inside its transaction and rejects self-edges and cycles, including cycles created by repointing existing dependents, with the offending ids; forward sibling references stay allowed.
- [x] A rejected scope change escalates through the existing scope-change protocol path.
- Tests: table-driven domain tests for the graph rule (self-edge, sibling cycle, repoint-induced cycle, valid forward reference); one integration test through the store.

### Task 51: SQLite path is opened exactly (F16)
Refs: 05 §3.
- [x] `Database._connect` builds an escaped absolute file URI and keeps read-only mode.
- Tests: integration: paths with spaces, `#`, `?` and literal `%` sequences; `PRAGMA database_list` reports the requested file, and no other file is created.

### Task 52: A slow notification hook cannot block the engine loop (F08)
Refs: 06 §5.7.
- [x] Notification delivery runs in a supervised bounded queue and task with a shutdown policy; the command loop and scheduling never await the hook.
- [x] A blocked or failing hook is logged, and `stop` and `pause` are acknowledged normally.
- Tests: integration: a hook blocked on a FIFO does not delay `stop`/`pause` acknowledgements or scheduling (`FakeClock`, `wait_until`); a failing hook is logged and delivery continues.

### Task 53: Idempotent command replay (F12)
Refs: 03 §7, 06 §2, invariant 5.
- [x] At-least-once delivery, idempotent by command id, is written in 03 §7. Database effects and the acknowledgement commit together where possible; `say` file writes and hand-back pane/run/hold changes are repeatable. No generic effect-replay framework.
- [x] A restart after the effect but before the acknowledgement neither duplicates guidance, rejects a `submit` whose task exists, nor rejects a hand-back that already took effect.
- Tests: integration with a crash between durable effect and acknowledgement for `say`, `submit` and `hand_back`.

### Task 54: Interrupted turns keep operator messages (F13)
Refs: 03 §4.3, §7, §9.
- [x] After a turn dies without a report (engine kill or process death), delivered messages of that turn are requeued ahead of newer ones before its replacement turn is created.
- Tests: integration with a real SIGKILL of the fake agent after delivery and before its report: the replacement turn's prompt lists the message, in order relative to a newly queued one.

### Task 55: Escalated state always has an actionable escalation (F14)
Refs: 03 §8, invariant 5.
- [x] The escalated task state, its escalation row and its event commit together; the packet is regenerated from durable state when missing, including at reconciliation. Title refresh stays outside the correctness path. Dispatch and dependency escalations follow the same rule.
- Tests: integration: failure after the state write; failure while writing the packet; both end with an open actionable escalation after recovery.

### Task 56: Codex context window belongs to the model (F15)
Refs: 04 §5.
- [x] Design 04 §5 and the implementation associate each observed window with its model and use it only for that model; a rung change to another model drops the old window and uses the new model's cache. Session resume is unchanged.
- [x] The existing test that prescribes the rollout-first precedence is changed with the design.
- Tests: contract-level integration: change model in both directions between models with different windows; the compaction threshold follows the selected model.

### Task 57: TaskFlow responsibilities (F18)
Refs: 02 §2, 07 §1; `refactor:` commits only.
- [x] After Tasks 42, 43 and 49 the shared hold, acceptance and persistence rules each have one implementation used by ordinary steps and integration turns.
- [x] Recovery/quota waiting, prompt-context and escalation-evidence preparation move into focused collaborators wired in `bootstrap.py`. `TaskFlow` stays the sequencing layer; no one-method classes and no workflow framework.
- [x] Behaviour tests are unchanged and green.

### Task 58: Low-severity items (F19, F20, F21, F23, F22)
Refs: 01 §glossary, 02 §2, 07 §1–2.
- [x] F19: one deterministic live-tail scenario in `test_view.py`: start the view, append a partial then complete JSONL row, assert the output before the turn completes; include a terminal or failure outcome.
- [x] F20: import-linter contracts for the stated `plugins.agents.*` import boundary, and `banned-api` entries for wall and monotonic clock calls; or 02 §2 says which rules are lint-enforced and which are review conventions.
- [x] F21: `scripts/release-readiness-smoke.sh` uses a portable temporary directory; it is proven on macOS and on Linux where available. A CI workflow for the non-paid gate is optional and not part of this task.
- [x] F23: 01 §glossary documents slots `0..max_parallel-1` (implementation and tests win), with a matching `SHIKIRU_SLOT` example.
- [x] F22 (design opinion, needs an owner decision, no code churn): either relax the "no other literals" and "no comments" rules in 07 §2 and AGENTS.md to allow local syntax literals and short rationale comments, or record that they stay. Apply a relaxed rule only in code touched by other tasks.

### Task 59: Re-verify and regenerate release evidence
Refs: 07 §5.
- [x] `uv run poe check` passes in a normal and a `TERM=dumb` environment.
- [ ] `poe e2e` E1–E6 pass in one run on the resulting revision (stating date, scenarios and cost); resume, model-change and takeover scenarios included. Met: E1–E6 passed in one bounded run on 2026-09-30/10-01 at `6116446` (31 m 11 s, one automatic E3 rerun; Task 74), with resume (E5) and takeover (E6) as native scenarios. Not met: 07 §5 defines no native model-change scenario, so model change is covered only by plugin and integration tests and, since Task 73, by the live contract checks (Codex passed on 2026-09-30; Claude skipped, no alternate model configured), not by `poe e2e`. Restated 2026-10-01 against `docs/release-readiness-2026-10-01-phase7c.md`: E1–E6 also passed at `e78c057` (21 m 03 s, first attempts, $1.2244) and both live model-change checks passed (Claude with `sonnet`); the criterion stays open for the missing native scenario.
- [ ] The portable clean-install smoke passes; tested CLI versions and the source commit are recorded next to the results. Met on macOS (2026-10-01, `6116446`), with the CLI versions and source commit recorded. Not met: the script is macOS-only and Linux is unverified, so "portable" is not demonstrated; Task 74 states Linux as verified or unverified.
- [x] `docs/release-readiness-*.md` states which findings were fixed, and its claim that every design section agrees with the code is restated after Tasks 41–58.

## Phase 7b — Independent-review remediation (2026-09-30)

Source: three independent read-only reviews of `66aeb3a` (F01–F08, F09–F23, whole-system release readiness), whose verdict was "ready for supervised use only". Every item below was found by a reviewer reading code and, where noted there, reproduced on real SQLite, Git and tmux; each task first writes a failing test that reproduces its item (or shows by reading that the item is not real, and says so), then fixes it. The Phase 7 invariants apply. Owner decisions recorded 2026-09-30: all reviewer items are in scope; F22 — the "no other literals" and "no comments" rules stay, unnecessary comments are removed, and a genuinely needed comment gets an in-place exclusion marker.

### Task 60: The engine survives tick faults and tmux loss
Refs: 02 §1, 01 D8, 06 §5.7.
- [x] A fault in one engine tick (layout restore, scheduling, reconciliation, notification) is logged with its cause and traceback (a tick fault is not attributable to one task, so the task is not named), retried on the next tick with bounded backoff from `defaults.toml`, and never ends the engine. A vanished tmux server or session is recreated (session and windows restored) instead of raising.
- [x] An engine that does stop for an unrecoverable reason records the reason as an event and sends the configured notification; `engine_stopped` carries the reason. 06 documents how to keep the engine running (service-wrapper recipe for launchd and systemd).
- Tests: integration: `tmux kill-server` while a task is running — the engine keeps running, restores the layout and the task continues; a scheduler exception in one tick does not stop later ticks; an unrecoverable stop notifies.

### Task 61: Every running task has a live flow
Refs: 03 §2, §7, §10; invariant 5.
- [x] Each engine tick ensures every `running`/`integrating` task that is not held has a live flow, and starts the missing one; a flow that ended without settling its task (an exception out of `TaskFlow.run`, an `answer` resolution racing the previous flow's tail, a failed escalation) no longer leaves the task `running` and idle until a restart. `supervisor.start_task` never silently skips a task whose old flow is finishing.
- Tests: integration: an `answer` resolved while the old flow is still finishing its title refresh ends with one live flow; a flow killed by an exception is restarted or the task escalated; no duplicate flows for one task.

### Task 62: Escalation packet failures never kill or wedge the engine
Refs: 03 §8; invariant 5.
- [x] A failure writing an escalation packet (unwritable directory, disk full) is handled at every call site (`TaskFlow`, scheduler dispatch and dependency escalations, reconciliation): the engine keeps running, the task remains actionable (the escalation is retried on the next tick or the task stays `escalated` with its row), and the failure is logged and shown as an `engine_error`; a restart does not fail the same way forever.
- [x] A packet restored from durable state carries the recorded reports, facts and turn tail that still exist, not only the headline question.
- Tests: integration: failure after the state write inside the transaction, failure while writing the packet on the flow path, the scheduler path and reconcile; recovery after the failure is removed ends with an open, actionable escalation with its evidence.

### Task 63: `supersede` and cancellation never erase unexplained work
Refs: 03 §2, §6, §8; invariant 3.
- [x] A `supersede` verdict is subject to the same uncommitted-work rule as `accept`: the Leader's dirty worktree is returned as `changes_requested` (or escalated as `uncommitted_work`) before the task is cancelled, so tracker entries and other work reach the replacements' seed or are explicitly accounted for. 03 says so.
- [x] Cancelling an `uncommitted_work` escalation keeps the dirty worktree (and its branch) in place and tells the operator where it is, instead of removing it; the cancel option text states this.
- [x] Ignored plan output is documented as outside the guard, or included in it (say which, tested).
- Tests: integration with real git: supersede with an uncommitted tracker edit; cancel of an `uncommitted_work` escalation keeps the files; the replacement receives the tracker entries.

### Task 64: Operator rung pins survive every transition
Refs: 03 §1.1, §7; F10.
- [x] Every path that writes rung overrides (`changes_requested` at `escalate_after`, recovery rung moves, retry, `set_rung`) changes only the role it moves and preserves overrides set concurrently by the operator; `set_rung` resets `rejections_on_rung` as 03 §1.1 says (or the design is corrected with the reason).
- Tests: integration: a `set_rung` acknowledged while a turn runs survives a verify `changes_requested` that reaches `escalate_after` and a recovery move up a rung; counters follow the documented rule.

### Task 65: Holds cover every remaining execution path
Refs: 03 §6, §10; invariant 2.
- [x] Nudge turns, the scheduler (a queued task with the intervention hold is not dispatched into a slot), `git rebase --continue` after a conflict turn, PR polling and the window between the hold check and process spawn all honour `Task.held`; `INTEGRATION_STARTED` is emitted only after the hold check; a paused task cannot escalate as `integration_blocked` from the lock wait budget. 03 §6 lists these paths.
- Tests: integration: pause during a nudge, during a conflict turn before `rebase --continue`, during PR polling, and just before spawn starts no further process; retrying a taken-over task does not take a slot; the quota-wait hold test fails on the pre-Task-43 code (it currently passes there).

### Task 66: Command replay and crash windows
Refs: 03 §4.3, §7, §9; invariant 5.
- [x] `take_over` is idempotent and crash-atomic (hold, run mark, pane respawn and event recover to a consistent state on replay); replayed `cancel`, `resolve`, `retry`, `set_rung`, `pause` and `take_over` report the effect that already applied instead of a rejection and append no duplicate events; a cleanup failure after a committed cancel or resolution does not turn its acknowledgement into a rejection.
- [x] Message queue writes are crash-safe: `requeue` writes before it unlinks; delivery into a turn directory cannot strand a message when the process dies before `current_turn` is repointed (the next turn finds it).
- Tests: integration with a crash between each effect and the acknowledgement for each command; a real SIGKILL of the fake agent after delivery (the existing turn-process test passes on the pre-Task-54 code and must be replaced by one that fails there).

### Task 67: PR mode is live and current
Refs: 03 §6; invariant 4.
- [x] PR polling has a liveness rule: a conflicted, closed, failing-CI or over-budget PR escalates (with the reason) instead of polling forever; `cancel` closes an open PR of the task; the local branch of a merged PR is removed.
- [x] A task root built after a sibling PR merged, or after the remote base moved, starts from the refreshed base (fetch before building), not from a stale `refs/shikiru/bases/<base>`.
- Tests: integration with real git, a bare remote and the fake `gh`: conflicted PR, closed PR, failing CI, cancel with an open PR, merged-branch cleanup, and a base moved by an unrelated push.

### Task 68: Compatibility and leftovers
Refs: 05 §2, 03 §1.1; F06, F09.
- [x] `RepoState.base_ref` loads from state written before it existed (default or migration), tested with a pre-Task-47 database row.
- [x] `Submit.complexity` is optional: an explicit `--complexity normal` is kept and triage does not replace it; the mode write of a triage report and its transition are one UPDATE.
- [x] The two remaining E2E tests that cap CPU at 100 use the shared gate setting.
- Tests: integration for each.

### Task 69: Process ownership residuals
Refs: 04 §6, 06 §5.7; F02.
- [x] Notification hooks run in their own process group and are killed as a group on timeout, cancellation or shutdown; mutating git calls run by the engine are not SIGKILLed mid-operation on cancel (bounded graceful stop or shield); a turn process that died before its identity was recorded is still cleaned up; process identity compares start times with a tolerance so a clock step on Linux cannot make a live group look dead.
- [x] A descendant that calls `setsid` is documented as outside the guarantee, with the reason.
- Tests: integration with real processes for each; unit test for the identity tolerance.

### Task 70: Trust boundary, permissions and platform support
Refs: 01, 04 §4–5, README.
- [x] The trust boundary is documented in README and the design: Claude runs in `bypassPermissions` by default, Codex gets the shared `.git` as a writable root, worktree isolation is a prompt convention; use only on repositories and hosts the operator trusts.
- [x] State, run and log directories are created private (owner-only); log and run growth is bounded by a documented, configured retention or explicitly documented as unbounded.
- [x] `scripts/release-readiness-smoke.sh` is written for both macOS and GNU/Linux (`script` invocation, `sha256sum`/`shasum`, `PATH` not hard-coded, `TMPDIR`-aware short socket path) and the design states which platforms are verified: macOS yes; Linux only if a run is actually made (say so). No LICENSE is added: that is the owner's choice and is listed in the release notes as open.
- Tests: integration for directory modes; the smoke re-run on macOS.

### Task 71: Comment rule and lint claims (F22 decision, F20)
Refs: 07 §2; owner decision 2026-09-30.
- [x] Unnecessary comments are removed from `src/`, `tests/` and `scripts/`. A mechanical check in `poe check` rejects a comment unless it is a tool directive (`noqa`, `type:`, `pyright:`, `pragma`, shebang, encoding) or carries the documented in-place exclusion marker with a reason; the few comments that truly need to stay carry it. 07 §2 states the rule, the check and the marker.
- [x] The lint claims are exact: the agent-plugin contract is a denylist, so a test fails when a top-level package exists that is neither on the list nor in the allowed set; 02 §2 and 07 §2 name what is enforced by which tool; the `store/**` clock/subprocess exemption is narrowed to the modules that need it; `time.process_time` and `time.thread_time` are banned.
- Tests: unit tests of the comment checker (directive, marker, violation); the package-completeness test.

### Task 72: TaskFlow residuals (F18)
Refs: 02 §2; `refactor:` commits only.
- [x] The dirty-worktree acceptance policy and its constants move out of `TaskFlow` into a focused collaborator; the `escalate` pass-through, the 4-tuple with mutually exclusive slots and `TaskFlow` passing itself to the integration host are removed or replaced by a narrow interface; the handoff/continue flags shared by `TurnContextPreparer` and `TurnRecovery` have one explicit owner; `VIEW_LOG_NAME` replaces the hard-coded `"view.jsonl"`; `escalate_stored` shares its persistence with `TransitionWriter`; the 02 file list is complete.
- [x] Behaviour tests are unchanged and green.

### Task 73: Test gaps and evidence quality
Refs: 07 §3, §5.
- [x] F06/F07 are covered through `bootstrap.py` wiring and the real plan/supersede flow (PR-mode plan children included), not hand-built transitions.
- [x] Live model-change checks assert the emitted `model_auto_compact_token_limit` follows the selected model (Claude and Codex), and were run natively on the resulting revision with the real rollout format; the result is recorded. Met for Codex (run at `b8de3d0`, real rollout parsed, redacted fixture and offline test added, recorded in `docs/release-readiness-2026-09-30.md`). Claude emits no `model_auto_compact_token_limit`; its hook compaction limit (following the `[1m]` suffix) is asserted and the live check passed on `a2b8bad` with `SHIKIRU_CLAUDE_LIVE_CHANGE_MODEL=sonnet`.
- [x] The E2E results artifact records the source commit, CLI versions and start/end timestamps.
- [x] The Task 59 ACs that were ticked with annotations are restated strictly (unticked with a reason where not met).
- [x] Flake hunt: not reproduced in 20 full-suite runs plus `-n 2`, nice'd-load and 12 heavy-load runs of the Task 60-71 files; timing-sensitive tests hardened (the 1 s hook timeout in `test_hook_descendants_die_with_the_hook` and the 2 s timeout in `test_timeout_raises_and_kills_the_process` are now 10 s and 5 s with 30 s waits, and each still fails when the guarded group/timeout kill is removed). The single original failure at `17d1d8f` was never identified, so the cause is unconfirmed.
- Tests: the above.

### Task 74: Final verification and release evidence (Phase 7b)
Refs: 07 §5.
- [x] `uv run poe check` passes in a normal and a `TERM=dumb` environment. Met at `6116446` (894 passed, 15 skipped, 89.62% both) and at `3615b39`; 8 further plain test runs without a failure.
- [x] `poe e2e` E1–E6 pass in one run on the resulting revision (date, scenarios, duration, cost, commit, CLI versions). Met at `6116446`: 7 passed in 31 m 11 s with one automatic rerun of E3 (attempt 0 escalated after a Codex git index-lock denial, cause not established). Per-scenario wall times are not recorded by the artifact, so 07 §5 keeps its estimates.
- [x] The clean-install smoke passes on macOS at the final commit; Linux is stated as verified or unverified. Passed on macOS at `6116446` (the evidence commit changes only documentation); Linux is stated as not verified.
- [x] A new `docs/release-readiness-*.md` (`docs/release-readiness-2026-10-01-phase7b.md`) states which reviewer items were fixed, restates the design-agreement claim after Tasks 60–73 and lists remaining limitations honestly.

### Task 74a: Bounded E2E scenarios
Refs: 07 §5. Owner request 2026-10-01: E2E must check real scenarios but be bounded so agents work for a short time.
- [x] Every real-agent scenario has its own attempt budget sized from measured durations (about 2–3 times the slowest passing run, in minutes, not the current flat 30 min `ATTEMPT_TIMEOUT_SECONDS`), and a whole-invocation budget; no scenario may take 30 minutes per attempt.
- [x] A scenario that makes no progress fails fast: a stall detector ends an attempt when no new event was recorded and no turn process is alive for a few minutes, and the failure names the last event, the task states and the engine log tail.
- [x] The E2E engine configuration shortens waits that only matter when something is wrong (for example `timeouts.integration_wait`, currently 1800 s, to about a minute) so a blocked merge escalates and fails the attempt quickly instead of waiting out its budget.
- [x] The automatic rerun stays at one, but a rerun happens only for the failure classes the design names as agent flakiness (07 §5), and each attempt's diagnostics (events, task states, `git status` of the operator checkout) are written next to the results.
- [x] Root cause of the 2026-09-30 E3 stall is determined from the preserved run files (a live agent edited the operator checkout outside its worktree while LocalMerge waited for a clean checkout): if Shikiru can prevent or detect it (prompt, cwd or writable roots, an early `dirty operator checkout` escalation that names the paths), fix it; otherwise document it as an agent-behaviour limit and make the E2E failure message name it.
- Met by `tests/integration/test_e2e_guards.py` (budget table cut to the invocation, stall rule, stall and timeout failures with diagnostics, rerun classes) and the LocalMerge event tests in `tests/integration/test_engine_integration.py`; budgets in `tests/e2e/budgets.py`, stall detector in `tests/e2e/watchdog.py`, E2E overrides `integration_wait=60` and `max_quota_wait=300`. Not run against real agents (paid): the per-scenario durations in 07 §5 are estimates from the 24 m 52 s total and E3's task timestamps, to be replaced by the next passing `poe e2e`.
- Tests: unit/integration tests of the budget table and the stall detector with `FakeClock`; `poe check` green; measured scenario durations recorded in 07 §5.


## Phase 7c — Final-review remediation (2026-10-01)

Source: the three independent final reviews of `b1c16d0` (F01–F12 with Tasks 60–69, F13–F23 with Tasks 66 and 70–74a, whole-system release readiness). Verdict: ready for supervised use with Claude on macOS only; not ready for unattended use, for Codex in a real repository, or for public distribution. Tasks 74b–74h close the findings the reviewers reproduced or traced to code, then re-verify. Owner scope decision: all reviewer items; the LICENSE stays an owner decision. Each task writes its failing test first against the real boundary. Phase 8 starts after Task 74h.

### Task 74b: Codex sandbox covers the worktree git directory on every turn
Refs: 04 §5, 01 §6.
- [x] The Codex plugin passes the per-worktree git directory (`git rev-parse --absolute-git-dir` of the turn's cwd), the run directory and the shared git common directory as writable roots on every turn, including `exec resume` turns (resume has no `--add-dir`/`-s`; pass the roots as `-c` configuration for that CLI version), so a commit and `shikiru report` work from a worktree outside `$TMPDIR` without a guardian approval. 04 §5, 01 §6 and the README state what works and what is verified; the note that resume keeps the session's sandbox is corrected.
- [x] `shikiru doctor` has a Codex sandbox probe that fails when a worktree gitdir or the run directory is not writable under the configured sandbox (a real, free `codex sandbox`-style check; no model turn), and reports the Codex version.
- [x] The E2E projects live outside `$TMPDIR` (a private directory under the user cache or `$HOME`) so the writable-root behaviour is exercised; the harness records the sandbox settings and the rerun policy no longer treats a sandbox denial as agent flakiness.
- Tests: contract-level argv tests for first and resumed turns; an integration test that commits inside a real worktree under a real Codex sandbox profile outside `$TMPDIR` without model turns, if the CLI exposes a free sandbox command (otherwise a doctor-probe test and a documented live check); live: the existing worktree-commit-survives-resume check on the installed Codex outside `$TMPDIR`.

### Task 74c: Reconcile and the tick fail per item, not per engine
Refs: 02 §1, 03 §7; reviewer findings.
- [x] Reconciliation isolates each step and each task: a failing cleanup (for example `gh pr close` for a cancelled PR task) is logged and recorded as an `engine_fault` and does not stop scheduling, `ensure_flows` or escalation restore; `end_orphans` runs once per engine start, never again on a retry (no kill of a live turn).
- [x] `layout.restore` isolates a failing task window so one bad pane does not starve every other task; a persistent per-task fault is re-notified at a bounded interval (`repeat_notify_seconds`) while it lasts, as `engine_fault` events with the task id and a `repeat` count; the `status` display of the fault streak is Task 74d.
- [x] The command loop (`pending`, handlers, `mark_done`) is inside the fault barrier; environmental database errors (`sqlite3.OperationalError`: locked, disk full) are recoverable faults; internal "vanished/no step" invariant errors (`InvariantError`) are recorded as unexpected faults (`recoverable: false`, with the task where isolated per task), not as environmental.
- [x] The tick guard's backoff cannot overflow (iterative cap) and `engine_fault` events are deduplicated per error class while a fault flaps (rate-limited), without hiding a new class.
- Tests: integration with real SQLite/git/tmux and failing `gh`/tmux/SQLite: queued task still dispatches, unheld running task still resumes, pause/resume then retry of reconcile does not kill a live turn, one bad pane does not block another task, 1100 consecutive faults do not stop the engine, a flapping fault records a bounded number of events.

### Task 74d: Notifications cannot silence an escalation
Refs: 06 §5.7, 03 §8.
- [x] A hung or slow `notify.command` hook does not delay or drop later escalations: escalation and engine-fault notifications have priority over informational ones, the queue never drops an escalation, hook timeouts are short and configured (not 2 h), and a failing hook is recorded once per streak.
- [x] `status` and the dashboard show the engine's current fault streak and the oldest unacknowledged escalation age, so an alive-but-starved engine does not look healthy.
- Tests: integration: blocked hook plus ten informational events plus an escalation: the escalation is delivered after the hook is released and never dropped; fault streak shown in `status`.

### Task 74e: PR mode network work leaves the command loop and tolerates blips
Refs: 03 §6, §10; invariant 4.
- [x] `git fetch`, `gh pr view` and `gh pr close` never run inside the serialised command loop or the scheduling tick for longer than a small bounded time: cancel acknowledges immediately and the PR close is performed off-loop with retry; root builds fetch with a short timeout and a failure is a recoverable dispatch retry that does not count toward `MAX_DISPATCH_FAILURES` for a transient network error.
- [x] A single failed `gh pr view` poll is retried with backoff; a check that is still pending or later goes green does not escalate; only a settled failing state, a conflict, a closed PR or the budget escalates.
- [x] The integration rebase refreshes the base before rebasing, and `retry` after a conflict escalation rebases again instead of only polling.
- Tests: integration with real git, a bare remote and a slow/failing fake `gh`: a slow `gh` does not delay another command, a blip does not escalate, a stale base is refreshed at rebase, retry after conflict rebases.

### Task 74f: Operator-held tasks and task ids survive tmux loss and reuse
Refs: 03 §9, §5; reviewer findings.
- [x] After a tmux server loss or host reboot, a taken-over role is restored as an interactive session or handed back with a visible, notified event; it is never left held with a silent read-only pane.
- [x] A task id or branch that already exists from an earlier state (state reset, kept branch of a cancelled task) is never adopted silently: the root build verifies the branch against `base_sha` and fails with a clear error naming it; `cleanup` never `rmtree`s a root with a dirty worktree whose metadata is missing.
- [x] Turn directory numbers sort correctly past 99 turns; `command_applied` lookups are indexed or bounded; an unwritable packet directory does not re-run git evidence collection on every tick.
- [x] `config.toml` created by `init` is owner-only from creation; `make_private_dir` is race-free.
- Tests: integration on `tmux_server` (kill-server during takeover), real git branch collision, 120 turn dirs ordering, init file modes.

### Task 74g: Lint, docs, smoke and evidence hygiene
Refs: 02 §2–3, 07 §2, §5; F20, F21 residue.
- [x] The widget import contract is exact (add the missing forbidden packages and extend the completeness test) or renamed to state it is a partial denylist; 02 §2 and §3 match the code (tree complete).
- [x] `asyncio.wait_for`/`asyncio.timeout` use is either routed through `Clock` or listed as an explicit, justified exception in 02 §2; the comment checker rejects free prose after `noqa`/`pyright:`/`pragma:` except the documented reason form; the literals rule is described as a review convention and the worst violations the reviewers named (`turn_context.py`, `acceptance.py`, `recovery.py` `"quota"`) become named constants.
- [x] The smoke script cleans its temp dir on the socket-path exit, reports a missing tool, never overwrites a passing same-date artifact, fingerprints the current evidence doc, and has a documented Linux verification recipe; Linux stays "unverified" unless run.
- [x] The E2E artifact records every attempt (failed attempts, costs, durations per scenario) and is keyed by invocation start, not by date; a release summary of the last full passing E2E invocation is committed as `docs/e2e-results.md` so the evidence is versioned (the smoke result stays in the release-readiness document). 07 §5's duration table keeps its estimates until Task 74h, whose measured per-scenario durations replace them.
- [x] `tests/integration/test_git_mutation_cancel.py` leaks a `/bin/sh .git/hooks/reference-transaction` and its blocked `cat <fifo>` after a full-suite run (observed twice, 4+ minutes old, parent 1): the hook survives the graceful stop of git. Find why (the release write races the hook, or the stop signals only git and not its hook children), fix the test so it never leaves a process, and decide whether the engine's graceful git stop must signal git's process group so a hook cannot outlive a cancelled mutating call; document the decision in 03 §7.
- [x] Root-fragile tests (`chmod 0o500`) skip or adapt when running as root; the decomposition test no longer imports helpers from another test module; `evidence.py` logs and reports a failed history load instead of swallowing it.
- Tests: as applicable; `poe check` green.

### Task 74i: Test-value audit against AGENTS.md
Refs: AGENTS.md (Tests, Test value), 07 §3. Owner question 2026-10-01: the suite grew from 779 to about 974 collected tests (+195 in 108 new functions) while coverage stayed near 89%.
- [x] Every test added since `66aeb3a` (`git diff --stat 66aeb3a..HEAD -- tests`) is audited against the AGENTS.md rules: behaviour over implementation, unit tests only for domain rules, models and parsers, integration tests for the rest, one table per rule, no tests of pydantic features, enum or constant listings, trivial wiring or message wording, no private-attribute access, no mocks of own classes. The audit result (kept / merged into a table / removed, with the reason) is recorded in `docs/test-audit-2026-10-01.md`.
- [x] Near-duplicate scenarios are consolidated into one table or one scenario (for example the unwritable-escalation-packet tests across four files, the replay-of-an-acknowledged-command family, the PR-poll and hold families); enum-listing parametrizations (`test_triage_transition_carries_the_triaged_mode(mode)`, `test_only_queued_unheld_tasks_...(hold)`), assertions on error-message wording and tests of test tooling beyond one table each (`test_e2e_guards.py`, `test_e2e_results.py`, `test_comment_rule.py`) are removed or reduced to the rule they protect.
- [x] A test is removed only when another test still fails for the same reverted fix; the audit shows this by mutation or by naming the covering test. The regression power of every Phase 7/7b/7c fix is preserved, coverage stays at least 80% and `poe check` is green.
- Report: collected tests and coverage before and after, and the number of tests merged, removed and kept. Result: 974 -> 943 collected, coverage 89.93% -> 89.93%; see `docs/test-audit-2026-10-01.md`.

### Task 74h: Phase 7c verification and release evidence
Refs: 07 §5.
- [x] `uv run poe check` passes in a normal and a `TERM=dumb` environment, plus repeated suite runs. Met at `e78c057`: 928 passed, 15 skipped, 89.93% in both environments, and 5 further plain runs without a failure.
- [x] `poe e2e` E1–E6 pass in one invocation on the resulting revision with projects outside `$TMPDIR`; measured per-scenario durations and every attempt are recorded and replace the estimates in 07 §5's table and `docs/e2e-results.md`; live model-change checks and the macOS smoke pass at the final commit. Met at `e78c057` on 2026-10-01: 7 passed in 21 m 03 s, every scenario at its first attempt, $1.2244 agent-estimated; Codex and Claude live model-change checks and the Codex worktree-commit check passed; the macOS smoke passed (the evidence commit changes only documentation). Linux stays unverified.
- [x] A new `docs/release-readiness-*.md` restates each reviewer finding, the design-agreement claim and the release claim honestly; Task 59 is restated against it. `docs/release-readiness-2026-10-01-phase7c.md`.
- [ ] A final independent read-only review (three reviewers as in Phase 7b) reports no blocking finding for the claimed use. Run on `a371b41` on 2026-10-01: it confirmed F01–F23 fixed or decided and found a blocker (a task reaches `done` without integrating when the agent leaves its branch) plus the items of Phase 7d. Task 74h is ticked when Task 74p's re-review reports no blocker.


## Phase 7d — Release-blocker remediation (2026-10-01)

Source: the three independent reviews of `a371b41` (finding traceability with fix-reversion mutations, a fresh defect hunt against the five invariants, conventions/tests/evidence). Owner decision (2026-10-01): Phase 7d makes Shikiru production ready for the claimed use: it must work stably and as designed, with no blocking or critical issue left. Severity scale for every review and verification in this phase: **blocker** = work is lost, corrupted, silently not integrated, or an operator command or escalation is lost or contradicted by durable state; **critical** = the engine or a task can hang, spin, leak processes, spend money out of control, or cannot be recovered or stopped by the operator; **major** = a defect with a bounded, visible effect and a documented workaround. Phase 7d ends only when the final review finds no blocker and no critical issue in the supported scope; majors are fixed unless the task says why not, and any remaining limitation is stated in the release claim and is not stability-relevant. The reviewer-found minor defects are therefore fixed in Tasks 74s–74u, not merely recorded. Each task writes its failing test first against the real boundary. Phase 7d is the last remediation phase before the release: there is no Phase 7e. A review that finds a blocker, critical or unjustified major adds a task inside this phase, and Task 74p's review loop repeats until a full round is clean. Phase 8 starts after Task 74p.

### Task 74j: Integration requires the task branch
Refs: 03 §6, invariant 3. Reviewer reproducer: an agent runs `git switch -c feature/x`, commits; facts and acceptance read `HEAD`, the merge uses `shikiru/T-n`, so `LocalMerge.merge` returns no commits and the task is `done` with the work reachable from no integrated branch (detached `HEAD` is worse: cleanup removes the worktree).
- [x] Before facts collection, acceptance, rebase and integration (local merge and PR mode), the engine verifies that the worktree `HEAD` is `refs/heads/<repo.branch>`. A different or detached `HEAD` never reaches `done`: the task escalates (or the agent is nudged first, whichever 03 prescribes for a recoverable agent action) naming the actual branch and the commits at risk, and cleanup never deletes the task branch or the worktree while commits exist outside it.
- [x] 03 §6 states the rule; the agent prompt tells the agent to stay on the task branch.
- Tests: integration with real git and the fake agent: switch to another branch and commit, detach `HEAD`: the task does not reach `done`, nothing is deleted, the commits remain reachable and an escalation is raised; a task that stays on its branch is unaffected; the same in PR mode with the fake `gh`.

### Task 74k: Graceful shutdown on SIGTERM and SIGINT
Refs: 03 §7, 06 §5.8. Reviewer finding: `grep add_signal_handler src` finds nothing, so a plain `kill`, `systemctl stop` or `launchctl bootout` skips `Engine._shutdown`; turn processes run in their own sessions and keep editing worktrees and spending money until the next start.
- [x] The engine process handles SIGTERM and SIGINT by running the same graceful shutdown as `shikiru stop` (stop turns, flush state, release the lock), within a bounded time from `defaults.toml`; a second signal or the bound ends the process after killing the turn process groups.
- [x] 06 §5.8's systemd and launchd recipes are accurate for this behaviour; `shikiru stop` acknowledges only after the engine has released its lock (or documents and bounds the wait), so `stop && start` is safe.
- Tests: integration with a real engine process and the fake agent: SIGTERM while a turn runs leaves no live turn process, no engine lock and consistent state; restart resumes; `stop && start` leaves an engine running.

### Task 74l: Notifications survive a restart; command handler errors are retried
Refs: 06 §5.7, 03 §8, 74c. Reviewer findings: the last-notified event id and the queue are in memory, so an escalation raised just before a crash or kill is never announced; the command loop rejects every non-`ShikiruError` exception permanently, including a transient `sqlite3.OperationalError`.
- [x] The notification cursor is durable (a stored event id or an equivalent derivation); after a restart every escalation or fault event not yet delivered is notified exactly once more, informational events are not replayed, and the existing priority and 30 s hook bounds hold.
- [x] A command handler failing with an environmental database error is retried by the fault barrier and not marked rejected; only domain errors reject.
- Tests: integration: kill the engine between the escalation commit and delivery, restart, the hook receives it once; a locked database during a handler leaves the command pending and it applies afterwards.

### Task 74m: Regression power of the Codex sandbox and reconcile fixes
Refs: 74b, 74c; reviewer mutations. Mutating `_git_dirs_for_turn` to return nothing leaves all 17 offline Codex tests green; removing the `_orphans_ended` guard leaves all integration tests green and the test meant to protect it is vacuous.
- [x] An offline test builds a real linked `git worktree` and asserts the Codex argv for a first and a resumed turn carries its gitdir, the common directory and the run directory as writable roots; the test-only `_worktree_git_dirs` fallback in production code is removed or justified.
- [x] A test that fails when `end_orphans` runs a second time (a reconcile retried after a fault must not end a turn that started after the first attempt); the vacuous test is replaced.
- [x] A turn whose leader exits while a descendant still holds the stdout pipe ends within the bounded drain time, not the stall timeout, or the limitation is written into 03 §7 with the measured bound (decide by cost: the simplest fix wins).
- Tests: mutation check recorded in the report for each (revert the code, see the new test fail).

### Task 74n: Documentation and evidence corrections
Refs: reviewer findings of the conventions review.
- [x] 07 §1 states what the code does (SQLite is called synchronously from the loop with a 5 s busy timeout) or the code moves blocking work to `to_thread`; the simplest consistent choice, with the reasoning. 05 §1's constants table lists the real notify timeout (`notify.timeout_seconds`); 02 §51 names `run_dashboard`; 03 §4.2's Linux claim says "designed for, verified on macOS only".
- [x] tmux ≥ 3.4 and git ≥ 2.45: `doctor` compares the installed versions with the documented floors and fails below them, or the README and 07 §1 restate the floors as "verified on tmux 3.7c, git 2.55.0" (prefer the doctor check if it is a few lines).
- [x] `docs/release-readiness-2026-10-01-phase7c.md` and `docs/test-audit-2026-10-01.md` no longer cite the dangling `05c11fb` and no longer call the test inflation "fixed" (974 to 943 against 779 before; the older files were not audited); F21 (Linux) and the reviewer-found defects not fixed in Phase 7d are listed as limitations.
- [x] The duplicated or config-worthy constants and the git and cache-copy timeouts are handled by Tasks 74r and 74t.
- Tests: as applicable; `poe check` and `poe comments` green.

### Task 74o: Test-value audit of the older suite
Refs: AGENTS.md (Test value), 07 §3. Reviewer sample: `test_prompts_renderer.py` has 47 tests (9 templates x 3 payloads re-test `domain.fencing.fence`, 9 test Jinja `StrictUndefined`, 9 enumerate `PromptName`), `test_ui_glyphs.py` 42 (35 one per glyph), about 41 tests of test tooling, three duplicated `_StubServices` stubs, two duplicated fake-distribution classes, a hand-rolled "advance FakeClock while polling" closure about 16 times, wording asserts through `match=`, an index-name assertion, a monkeypatched own function.
- [x] The audit of Task 74i is extended to the files it did not cover: every file above is reduced to one table per rule, framework and enumeration tests are removed, the duplicated stubs and the FakeClock polling closure become shared helpers under `tests/support`, wording and index-name assertions assert behaviour instead, and `monkeypatch.setattr` of an own function is replaced by the real boundary or removed.
- [x] A test is removed only when another test still fails for the same reverted fix (mutation or named covering test); coverage stays at least 80% and `poe check` is green. The result (collected tests and coverage before and after) extends `docs/test-audit-2026-10-01.md`.

### Task 74q: A turn completion never overwrites operator-owned task state (F10, closed for good)
Refs: F10, 03 §3, 05 (task row). Two earlier attempts preserved the rung pins and overrides but left `counters` (`rejections_on_rung`) and `rung_index` written from the pre-turn snapshot (`store/tasks.py` `apply_transition`), so a `set_rung` acknowledged during a turn can still be undone by that turn's completion (limitation 9). Owner: this must work, not be argued away.
- [x] `apply_transition` writes only the fields the domain decision changed relative to the snapshot it was computed from (or reloads the operator-owned fields inside the same transaction); `rung_index`, the rung overrides, `rejections_on_rung` and every other counter an operator command can change survive a concurrent command, for every transition kind (accept, reject, escalate, rung escalation, completion), and the rule is stated once in 03 §3 and 05.
- [x] When the domain decision itself moves the rung (automatic escalation up the ladder) while the operator has set one during the same turn, the documented winner is the operator's choice and the counter reset by `set_rung` is not re-applied from the snapshot.
- Tests (written first, each failing before the fix): real flow and store, controlled in-flight turn (fake agent with a release gate): `set_rung` during a turn that completes as accept, as reject (counter would increment), as rung escalation, and as escalate: the next turn runs on the operator's rung, `rejections_on_rung` reflects the reset, no field is overwritten; a table over the transition kinds, one assertion set; plus a store-level test that a transition computed from a stale snapshot leaves unrelated changed columns intact. Mutation check recorded in the report.

### Task 74r: The no-literals rule is enforced like the no-comments rule (F22, closed for good)
Refs: F22, AGENTS.md, 07 §2 items 5 and 8, `tests/support/comment_rule.py`. Owner decision (2026-10-01): keep the rules; a genuinely needed exception uses the in-place marker `# keep: <reason>`. The comments rule is now mechanical (`poe comments`); the literals rule is only a review convention, and the code shows it: bare literals remain (`claude/turn.py` `* 100`, `domain/motion.py` `* 2) % 2`, `workspace/git.py` `entry[:2]`, `operator/doctor.py` `hex[:8]`, `process.py` `dup2 … 2`, `indent=2` in four places), tunables are module constants outside `defaults.toml` (`DOCTOR_TIMEOUT_SECONDS` three times and others), and `codex/options.py` splits a name as `"model_auto_compact_" + "token_limit"` to dodge a check, which is exactly the indirection F22 warned about.
- [x] A mechanical check (`poe literals`, in `poe check`, beside `poe comments`; AST-based, same `# keep: <reason>` exclusion) enforces a precisely written rule. The rule is defined in 07 §2 and AGENTS.md and is narrow enough to be honest: numeric literals other than 0, 1, -1 (and slice/index uses the rule names) are allowed only as a module-level named constant, an enum value, a dataclass/pydantic field default that is itself a named configuration default, or in `config/defaults.toml`; string literals are checked where they are protocol values (comparison operands, argv elements of runner calls, `kind=`/`event=`/`type=` arguments; dict keys, subscripts and `.get` arguments are deliberately out of scope after measuring about 400 sites of row and payload keys read next to the SQL or model that defines them, see 07 §2 item 5) and must be named constants; docstrings, f-string text, user-facing messages, log text and type annotations are out of scope. The checker has a table-driven test (violation, allowed form, `# keep:` exclusion, malformed marker).
- [x] Every violation the checker finds in `src/` and `tests/support/` is fixed by a named constant or `defaults.toml` key (operator-relevant tunables only; implementation timings that an operator never needs become one shared named constant, not TOML), or carries `# keep: <reason>` where a name would obscure the code (for example a loop's `* 2`); no splitting or concatenation tricks to avoid the checker (the Codex option name becomes a plain constant); duplicated constants (command poll 0.25, title length 60, `DOCTOR_TIMEOUT_SECONDS`) become one.
- [x] 07 §2 states the rule, its scope and the exclusion in the same words as the checker, replaces the "review convention" wording, and the rationale answers F22 (what the rule buys: one place to read each protocol value and tunable; what it deliberately does not cover); a `# keep:` count is reported by the checker so exceptions stay visible.
- Tests: the checker's table test; `poe check` green; a mutation check (add a bare `* 100` and a string protocol literal, see `poe literals` fail).

### Task 74s: Engine start, tmux and process hygiene (stability)
Refs: 03 §5, §7, §9; reviewer defect hunt items 5, 7, 8 and 11.
- [x] A fault in `_prepare` (reconcile, orphan reaping, layout restore) at engine start never lets a pending command run a flow before reconcile succeeded: commands wait until the engine start sequence completed once, and `end_orphans` can never read the `turn.pid` of a turn the engine itself started; one table test over the start-up fault points.
- [x] A purely cosmetic tmux failure (title, decoration, status line, timeout included, `CommandTimeoutError` as well as `CommandFailed`) never kills a running turn, never escalates and never fails a command; it is logged once per streak. A `take_over` whose pane respawn fails after the commit is reported to the operator as what it is (taken over, pane could not be created, how to recover) and not as "rejected" while the hold stays; a failed respawn is retried or recoverable through the lost-pane path. Implemented as a rollback to a consistent state (the take over is released, `takeover_lost` appended, the command rejected with a retry hint, 03 §9) rather than a kept hold, plus a live-pane tag so a replay never respawns a live pane.
- [x] `_migrate` reads `user_version` and applies the migration inside one immediate transaction, so two processes opening an old database (engine and CLI after an upgrade) never double-apply.
- [x] Every helper subprocess (git, gh, tmux, doctor probes) runs in its own process group and a timeout kills the group, so descendants (hooks, MCP servers) never leak; the turn's `turn.pid` record cannot be missed by a crash between spawn and write (record the group before the turn starts reading, or reap by session/group identity at start), and the context-retry process is recorded at spawn. Implemented with a spawn gate: a turn process blocks until its pid is recorded (03 §4.2), so an engine killed in between leaves a process that never runs the agent.
- [x] A turn stopped by a hold no longer leaves a stale `TURN_STARTED` as the latest turn event (visible state is accurate in `status` and the dashboard); a `take_over` replay does not respawn a live pane.
- Tests: integration with real SQLite/git/tmux and real processes per item (injected faults at the real boundary: a failing reconcile step with a pending `resume`, a hanging tmux title call during a turn, two concurrent opens of a v(N-1) database, a timed-out git call with a hook child, a kill between spawn and pid write); each fails before its fix.

### Task 74t: Bounded resources and configurable timeouts (stability)
Refs: 03 §7, 05, 07; reviewer defect hunt items 4, 9, 10.
- [x] A failed or timed-out cache copy removes its partial destination, and a retry never accepts a half-populated cache; the cache copy and the git rebase/worktree timeouts are `defaults.toml` keys sized for large repositories, a timed-out `git worktree remove` or rebase leaves a state the next cleanup or `_try_rebase` classifies correctly (no permanent "dirty" or phantom conflict), and the cache copy no longer blocks the command loop for its duration (runs off the loop with the existing bounded-wait idiom).
- [x] Memory and scan bounds: `evidence.py` reads only the tail of `view.jsonl`; `SpawnedProcess` keeps a bounded stderr tail; `OperatorMessages._find` no longer scans the whole run directory per `say`; a retention policy for run directories, events and finished command rows (config keys, conservative defaults, never touches a live task's files or evidence of an open escalation) keeps a long-running engine bounded, and `status` reports the size.
- [x] A hung remote (fetch, `gh pr view`) cannot hold the scheduling tick or the command loop longer than a small configured bound per tick regardless of the number of ready tasks (a total per-tick budget, with the rest deferred to the next tick).
- [x] A single-flight notification hook cannot delay an urgent notification by more than a small bound (urgent events use their own delivery slot).
- Tests: integration per item with real git, a slow fake `gh`, a failing `cp`, a large `view.jsonl`, a long-lived engine over many short tasks with the fake agent (retention keeps the run directory count bounded); each fails before its fix.

### Task 74u: Soak and chaos run (stability evidence)
Refs: 07 §5; invariants 1–5.
- [x] A bounded, deterministic soak (`poe soak`, not part of `poe check`; at most about ten minutes) drives the real engine with the fake agent through many tasks (dependencies, plans, rejections, escalations and resolutions, parallel slots, pull-request mode with the fake `gh`) while a chaos driver injects faults at random but seeded points: SIGKILL of the engine, SIGTERM, tmux server loss, SIGKILL of a turn process, a locked database, a failing `gh`/`git` call, a held hook. After each recovery and at the end the five invariants are asserted from durable state and git (no task lost or duplicated, every `done` task integrated or accounted, no live orphan process, no leaked tmux server or temp directory, no pending command or escalation stranded, notifications delivered), and the seed is printed so a failure replays.
- [x] Every defect the soak finds is fixed in the same task or filed as a blocker/critical item that Phase 7d does not close until fixed; the soak result (seed, duration, faults injected, tasks completed) is recorded in the release evidence.
- Tests: the soak itself plus a short smoke variant (seconds) that runs in `poe check`.

### Task 74p: Phase 7d verification, re-review and release evidence
Refs: 07 §5.
- [x] `uv run poe check` passes in a normal and a `TERM=dumb` environment and in repeated runs; the real-agent E2E E1–E6 pass in one invocation on the resulting revision (projects outside `$TMPDIR`), including a scenario or live check for Task 74j when it can be exercised with the cheapest rungs, and `poe soak` passes on two different seeds; the macOS smoke passes at the final commit; measured durations are recorded in `docs/e2e-results.md` and 07 §5.
- [ ] A new `docs/release-readiness-*-phase7d.md` states the release claim, the closed findings, the soak evidence and the remaining limitations, all of which must be non-stability items (Linux and any unverified agent or host configuration are excluded from the claim, no LICENSE/CI/packaging metadata is an owner decision, PR mode against real GitHub is stated as verified only against the fake `gh` unless run for real). Task 59 is restated against it.
- [ ] A first-use walkthrough on a clean environment: build the wheel, install it into a fresh virtual environment, follow only the README (`init`, `doctor`, `start`, `submit`, `status`, `take_over`/`hand_back`, `stop`) on a scratch git repository with the cheapest real rungs for Claude and for Codex, and record every deviation from the README; any step that needs knowledge not in the README is a defect fixed in this phase.
- [ ] The final review is a loop, run by the orchestrator on the final commit with five read-only reviewers on the strongest available model (not the implementers' model), each with a different lens and instructed to try to break the claim rather than confirm it: (1) the five invariants against the soak and chaos evidence, including mutations of the fixes; (2) an operator following the README and the design end to end, including every documented command and failure message; (3) data safety and security (worktree cleanup, secrets, file modes, process and tmux leaks, injection through task text and paths); (4) concurrency, crash windows and resource bounds in the engine, store and plugins; (5) conventions, test value, documentation and evidence honesty. They use the severity scale above and report no blocker and no critical issue for the claimed use, and every major is fixed or justified. If a round finds any, a task in this phase fixes it and the whole round repeats; the loop ends only with a clean round. Then Task 74h's last AC is ticked.


## Phase 8 — Operator Workspace

Specification: [Operator Workspace](../docs/tui-workspace.md), requested 2026-09-30. It defines the target experience; current behaviour changes only as the tasks below ship. Phase 7b comes first. Retain the engine's single-writer model, typed operator commands, agent-independent signals, tmux role views, SSH access, honest usage reporting and semantic comic-strip visual language.

Each task updates the relevant sections of 01–07 and the workspace specification if implementation resolves a detail differently. Add contracts only with a consuming feature. All tests follow 07 §3: observable behaviour against real boundaries, no mocks of Shikiru classes, no tests of framework features or wording, and no new snapshot catalogue.

### Task 75: Workspace design alignment and shared visual shell
Refs: [Workspace §1–3, §13, §15](../docs/tui-workspace.md#13-visual-system), 01 D18, 02 §2/4, 06 §5.4/§6.
- [x] Record the workspace composition decision in 01 and its responsibilities in 02; update 06 to distinguish the home workspace, task detail and secondary views. Link the target specification from the design index and README, clearly distinguishing planned and shipped behaviour.
- [x] Introduce shared background, surface, neutral border, text and focus tokens; apply them to the dashboard shell, palette and evidence modals while preserving role/state meanings, width-safe glyphs and monochrome cues.
- [x] Add the shell's visible focus, contextual footer, help and section expansion/back navigation, using current widgets. Keep Textual/Rich and existing plugin entry points; document placement/order of configured built-ins and reachable third-party Extensions. No configured plugin is silently dropped.
- Tests: Textual integration: navigate/expand/return to a section with keyboard and mouse, preserve focus, reach a configured external widget, and read state/focus without colour. Assert behaviour and width properties, not token inventories or screenshots.

### Task 76: Selectable task browser and search
Refs: Workspace §5/§7/§15, 03 §10, 06 §5.4.
- [x] Add ready-to-render task rows and bounded read queries consumed by an interactive browser. ID, title and state remain accessible; Active, Waiting, History and All membership/order follow §5.1, with queued tasks in scheduler dispatch order.
- [x] Search task ID/title and combine it with filters; show matching count and distinguish an empty project from an empty search result.
- [x] Preserve selected task ID, filter/search and scroll across state updates and row reordering. When selection leaves a filter, retain its detail/context until deliberate navigation; keep the selected visible row in view.
- [x] Update 02's component list, 06's widget contract/navigation and 05 only for introduced settings. Preserve existing widget fields/callbacks; new capabilities are additive and have consumers.
- Tests: Textual over real SQLite: search by title, combine a filter, reorder tasks while selected, complete the selected task, clear search, and load more history without resetting selection. Unit tests only for new domain projection rules.

### Task 77: Task detail and navigation to existing windows
Refs: Workspace §6/§7, 02 §2, 06 §5.1/§5.4/§6.2.
- [x] Render shared task detail from operator queries: situation, expanded strip, latest report/verdict, request/ACs, recorded evidence, workspace/session metadata and actions. Mark reported tests and engine-computed facts with their source; distinguish unavailable evidence.
- [x] `dash --task` opens detail fixed to its task, with task-scoped activity/usage and a compact global attention indicator. Home Enter opens/focuses the selected task detail and Escape restores browser context.
- [x] Open an existing task/role pane through injected navigation callbacks that resolve the current target at activation. A missing or terminal task window produces a readable result without recreating workflow resources.
- [x] Document task-view composition and narrow identity/title requirements in 06; preserve plan/implement, setup, consult, rework and integration meanings.
- Tests: real SQLite/Git reports plus Textual and `tmux_server`: inspect a task's latest verdict, return to the same browser position, switch to its existing window, and inspect a completed task after window/worktree cleanup without recreating them.

### Task 78: Contextual actions and searchable palette
Refs: Workspace §7–8, 03 §9, 06 §5.2/§5.5.
- [x] The shared palette offers searchable actions and task selection by ID/title. Carry the selected task/role from detail and the originating task/role pane from the tmux popup; show editable targets and advanced submission/rung fields.
- [x] Prepare applicable actions and disabled reasons through the operator layer using domain state; revalidate when dispatching and preserve the engine's authoritative rejection. Widgets do not implement workflow eligibility or construct commands.
- [x] Display action timing and consequences: message/rung changes apply to the next turn; pause differs from pause-now; takeover holds the task; resume/hand-back respect remaining restrictions. Cancellation and stop expose scope/work-preservation consequences in the action form.
- [x] Update 03/06 descriptions and 02/06 contracts for the consumed context/navigation callbacks; preserve the small CLI surface and conditional tmux palette binding.
- Tests: Textual and `tmux_server`: message and change rung without typing the task ID, change the target deliberately, open the popup from a role pane, and reject an action whose target state changed. Existing fake-agent boundary tests establish timing.

### Task 79: Durable command feedback and ownership banners
Refs: Workspace §9/§15, 05 §2, 06 §1/§5.5, 03 §7/§9.
- [x] Expose bounded read-only command history/status through `StateReader` and operator queries. Show Sending, Pending, Applied or Rejected with command ID, target and returned result; preserve stored command statuses and distinguish engine-stopped queueing from acknowledgement timeout.
- [x] Track pending outcomes after the palette closes and recover them after dashboard restart, including commands from another dashboard or MCP. A pending form cannot accidentally submit the same action again on refresh or return.
- [x] Applied message feedback describes guidance queued for the role's next turn. Persistent ownership banners name taken-over roles and all remaining holds; hand back and resume issue their own explicit existing commands.
- [x] Document the read API, outcome vocabulary and acknowledgement-versus-delivery distinction in 02/05/06. Add no command writer, acknowledgement path or navigation-state table.
- Tests: real SQLite/fake engine and Textual: command completes after the acknowledgement wait without form edits, multiple pending forms remain guarded after action switches and reopen, pending Submit recovers despite its store-allocated task ID, an older pending row remains inspectable beyond 50 newer rows, narrow command controls stay reachable, dashboard restarts, another client submits, rejection appears, and hand back leaves another ownership/pause hold visible. Assert one applied command effect.

### Task 80: Home overview and attention composition
Refs: Workspace §3–4, 06 §5.4/§5.7, 01 D18/D21.
- [x] Compose the home header, Attention/Running/Waiting summaries, task browser/detail, and recent project decisions/activity. Counts follow the specified definitions and distinguish task categories from slot occupancy.
- [x] Attention groups open escalations, diagnosed stalls and recorded engine faults; informational retries, quota waits and deliberate pauses retain their own meanings. Engine lock liveness stays distinct from fault and signal observations.
- [x] Summaries navigate to their content, decisions stay visible until durable resolution, and a missing packet shows its evidence error while retaining durable escalation information. Global task-window attention routes back to home.
- [x] Update 06 composition/attention rules and 02 data sources; use existing recorded faults and activity rules without a heartbeat, new long-wait policy or external notification system.
- Tests: seeded real state plus fake-agent transitions: paused/PR-waiting tasks and slots count correctly, escalation resolves from another client, unavailable packet retains the decision, recovery does not appear as an open decision, and attention updates without stealing focus.

### Task 81: Escalation resolution with structured evidence
Refs: Workspace §10.1, 03 §8, 06 §5.4–5.5/§6.4.
- [x] Present the question, recommended alternative, consequences and allowed resolution fields before expandable reports, facts, timeline and tails. Keep the complete original packet accessible and preserve agent alternatives versus engine resolution-kind semantics.
- [x] Route the selected escalation through the shared palette with its exact ID; map choices using the validated resolution contract, including answer/rung requirements.
- [x] Refresh forms when another Operator resolves/cancels the task; disable stale submission, preserve entered text for copying and show engine rejections from later races without resubmitting.
- [x] Update 03/06 packet and resolution presentation, keeping escalation state/evidence and existing work-preservation rules authoritative.
- Tests: Textual over real SQLite/fake engine: inspect consequences and full evidence, submit each applicable resolution class, resolve from another client while editing, and handle packet-read failure and the submission race.

### Task 82: Blocker navigation and task families
Refs: Workspace §10.2–10.3, 03 §2/§6/§10, 06 §5.4.
- [x] Show specific dependency/slot/resource/quota/pause/ownership/PR blockers with their available evidence and navigation. Distinguish dashboard resource sample age, scheduler reason, provider quota reset and engine retry/probe time.
- [x] Group children under parents with expand/collapse and filter-matched ancestor context. Show dependencies/dependents separately and superseded replacements only when supported by recorded evidence.
- [x] Keep plan-parent integration separate from child implementation completion; show exact family outcome counts. Show submitted/recorded queue age without inventing current-blocker duration, ETA or guaranteed start position.
- [x] Update 02/06 consumed relationship/blocker view contracts and 03's operator-facing explanations; reuse dispatch/domain rules and existing stored graph relationships.
- Tests: real SQLite/Git/fake-agent decomposition and supersede: follow a blocked dependency, navigate back with selection intact, see active children under a done parent, search a child with ancestor context, and distinguish unknown quota resets and cancelled replacements.

### Task 83: Completion history and task evidence timeline
Refs: Workspace §11, 05 §2–3, 06 §2/§5.4.
- [x] Make historical task detail usable after terminal cleanup; retain recorded cancellation/supersede reasons, reports, decisions and integration outcome without inventing terminal failure states for recovered turn errors.
- [x] Add bounded timeline/evidence queries and filters by role, turn and category. Distinguish engine events from normalized agent signals, preserve source ordering and identify missing timestamps/evidence or an incomplete live JSONL row.
- [x] Live evidence follows until the Operator scrolls away; provide an explicit Follow latest action. Updates and pagination preserve reading position and selected evidence identity.
- [x] Update 02/05/06 query, source-ordering and retained-evidence descriptions. Read recorded files without a second consumer of agent stdout or agent-specific parsing in the UI.
- Tests: real SQLite and recorded normalized files: browse a closed task, page/filter a long timeline, receive a partial then complete live row, preserve selection while new events arrive, and resume following explicitly. Test ordering through public behaviour, not implementation helpers.

### Task 84: Honest usage and secondary diagnostics
Refs: Workspace §12, 01 D21/D23, 04 §1, 06 §5.4.
- [x] Expose project recorded totals and selected-task usage with explicit scope, tokens, cost basis and priced/unknown-run coverage. Separate billed and API-equivalent totals and label partial subtotals; show unknown values distinctly from measured zero.
- [x] Preserve only reliable reported context and quota windows, including reset/observation age. Add no inferred pricing, spend budget, completion percentage, ETA, daily charts or time-period labels unsupported by queries.
- [x] Move host/process-tree resources, Docker and recorded engine-fault evidence into reachable diagnostics. Distinguish empty, unavailable, stale and failed samples; failures leave unrelated task data readable.
- [x] Update 05/06 metric definitions, scope, sampling/observation labels and widget placement; keep expensive reads off motion refreshes.
- Tests: real runs/quotas and samplers: mixed cost bases, partial pricing, unknown/zero values, per-task versus project totals, aged quota observations, unavailable Docker and a failed sample without lost task navigation.

### Task 85: Responsive layouts and accessible refresh behaviour
Refs: Workspace §7/§13–14, 06 §5.4/§6, 07 §3.
- [x] Choose wide browser/detail or compact list/detail from actual pane dimensions. At 48 and 36 columns keep task ID/title, state, hold/blocker and all action/result controls reachable; short panes collapse summaries and keep content scrollable.
- [x] Resizing preserves selection, focus, search and scroll; focused rows/forms remain visible. Footer/help, keyboard and mouse paths operate under `NO_COLOR` and disabled animation with textual state/ownership cues.
- [x] Preserve the signal-driven motion rules and 4 Hz cap; static motion settings still allow state/countdown refresh. No tmux split rearrangement or blank/clipped critical panels is introduced.
- [x] Document named layout limits, compact strip/row forms and terminal support in 05/06/07. Retain the snapshot limit and avoid operator breakpoint settings without a concrete need.
- [x] Tests: Textual size/interaction matrix for wide, 48-column, 36-column, short and very small panes, monochrome and static frames. Assert reachable critical content, stable identity/focus and width bounds; visually inspect representative home/task/form states and record findings.

### Task 86: Markdown and JSON evidence export
Refs: Workspace §11/§15, 02 §2, 05 §3, 06 §5.5.
- [x] Add a read-only operator export use case consumed by task detail/palette, with an injected local file writer. Export request, task outcome/relationships, task/rung metadata, available reports/facts/timeline, session usage provenance and task-scoped aggregate usage; signal excerpts are an explicit inclusion option.
- [x] Markdown and JSON contain a format version, task ID, export time and unavailable sections; preserve recorded source ordering and unknown costs, and escape/fence embedded content. Export does not imply a complete agent transcript or reconstruct cleaned worktrees.
- [x] Destination selection, explicit overwrite choice, success and write failures are visible. Export never queues a workflow command or changes engine state; no recorder, timed playback, HTML renderer or network service is added.
- [x] Document contents, format/version, destination behaviour and retained-evidence limits in 02/05/06 and the workspace specification.
- Tests: integration with real task records/files: parse JSON, verify meaningful Markdown evidence and hostile fenced text boundaries, export after worktree cleanup, mark missing sections, and handle existing/unwritable destinations while database task/event/command state stays unchanged.

### Task 87: Operator documentation and workflow walkthroughs
Refs: Workspace §16, 01–07, README.
- [x] Reconcile 01–07 with the completed workspace: decision log, responsibilities/file list, action timing/ownership, read/export contracts, widget placement/defaults, keyboard navigation, visual/compact forms and testing requirements. Update the design index and workspace implementation status.
- [x] Rewrite README dashboard/navigation examples around shipped home/task views, contextual actions and evidence inspection; document palette access from tmux, pending outcomes, usage provenance and export limits without duplicating the CLI action surface.
- [x] Run and document the eight workspace walkthroughs with the fake agent, including another Operator's escalation resolution, remaining takeover holds, engine-down pending command recovery and terminal history after window cleanup.
- [x] Every published shortcut, example and screenshot/terminal sketch agrees with actual behaviour; planned or unverified capabilities are labelled. Keep the workspace specification and task references internally consistent.
- Verification: inspect real Textual/tmux flows and the produced export artifacts; review documentation links and examples. No tests that pin prose or screenshots.

### Task 88: Workspace release verification and evidence
Refs: Workspace §16, 07 §5.
- [x] `uv run poe check` passes normally and with `TERM=dumb` on the resulting revision, preserving coverage ≥ 80% and architecture boundaries.
- [x] Real-agent E1–E6 pass in one `poe e2e` run on that revision; record date, scenarios, duration, source commit, CLI versions and reported cost or unknown cost as appropriate.
- [x] Clean-install smoke and the documented workspace entry/navigation paths pass on macOS; Linux is reported as verified only after an actual run. Record representative home/task pane sizes, monochrome/static operation and remaining limitations.
- [x] A new `docs/release-readiness-*.md` records Phase 8 delivery, fake-agent walkthrough evidence, final checks and design agreement. Tick Tasks 75–88 only after their own ACs are met; planning this phase does not count as implementation.

## Phase 9 — Compact Operator TUI

Specification: [Compact Operator TUI](../docs/tui-focus.md), approved direction 2026-10-04: option B's task list and inspector with option A's graphite, steel blue, sage and amber palette. Compact project totals, host and instance resources, task agent/usage rows, explicit coverage and subscription-aware presentation replace the overloaded primary workspace. Preserve the services, commands, queries, evidence, plugin boundaries and regressions delivered in Phase 8; do not revert it.

Delivery is sequential: design, billing facts, projections, shared visuals, Home, task detail/actions, responsive compatibility and verification. Each task uses one GPT-6-Luna sub-agent at xhigh effort, with architect review and fixes by that same agent before the next task. Every commit requires a green `uv run poe check` and ends with `Co-Authored-By: Codex <codex@openai.com>`. Tick a task only after every AC is implemented and verified. Inspect and clean only task-owned test processes, private tmux servers/sockets and disposable directories; preserve the user's servers, files and required evidence.

### Task 89: Compact TUI design alignment
Refs: Focus §1–10; 01 D18/D21/D24, 02 §2/4, 04 §1, 05 §1–2, 06 §5–6, 07 §2–3.
- [x] Reconcile the normative design with Focus's approved composition, palette, information hierarchy and keyboard contract. Explicitly identify the Phase 8 presentation sections being superseded and retain its shipped behavioral guarantees and historical evidence.
- [x] Define the smallest additive billing metadata contract, its plugin-owned explicit configuration/reporting, compatible absence behavior, persistence scope and provenance; subscription mode never implies zero cost or an API-equivalent charge.
- [x] Define honest task session/model history and attribution, project scope/coverage, current-engine uptime and resource sample meanings. Existing historical session totals must not be assigned to a model without evidence.
- [x] Preserve widget entry points and callbacks; specify where every configured built-in and external widget remains reachable, including the comic-strip plugin outside the default primary composition.
- [x] Preserve a durable copy of the approved mockup and link it from Focus. Link the new phase/specification from the design index and README, marking not-yet-shipped behavior as planned.
- Verification: review links, design cross-references, task dependencies and migration boundaries. No prose, token-inventory or mockup snapshot tests.

### Task 90: Explicit billing metadata and historical persistence
Refs: Focus §5/§8; 04 §1/4–5, 05 §1–2; Task 89's additive contract.
- [x] Implement normalized subscription/API/unknown billing metadata with explicit source, preserving existing plugin contracts and old plugin behavior. Agent specifics and native access handling remain inside plugins; core never infers a billing mode from model, environment, missing cost or quota.
- [x] Claude and Codex factories validate their explicit optional billing declaration, defaulting to unknown, and expose it through the normalized contract. The fake agent supports the same boundary for integration verification. No credential, account-management or tariff-price feature is introduced.
- [x] Persist the declaration with the run/session it describes through the engine's single writer and a compatible SQLite migration. Old records remain unknown; current settings never retroactively reclassify historical runs. Keep billing mode independent of billed/API-equivalent/unknown monetary evidence.
- [x] Carry stored metadata through existing task/session usage, history and evidence export consumers as compatible additions. Avoid counting a subscription session's absent per-task charge as an unknown API charge or a measured zero.
- [x] Update the relevant design and configuration reference with actual fields and behavior.
- Tests: real SQLite migration and engine/fake-agent sessions demonstrate declared modes, unreported/zero amounts, mixed cost bases, unknown old records, restart persistence and changed configuration without rewriting prior session facts; plugin validation tests cover the real factory boundary.

### Task 91: Compact metrics and agent-history projections
Refs: Focus §3–5/§8; 02 §2, 05 §2–3, 06 §5.4; Tasks 89–90.
- [x] Add consumed project projections for recorded token totals, explicit reported/unknown session coverage, reported API charge coverage, separate API-equivalent evidence and distinct declared subscription agents. Do not invent account identities or sum charges with subscription fees.
- [x] Project engine uptime from current lock liveness, recorded engine-start evidence and injected Clock. A stopped engine never shows an advancing uptime; a previous run duration requires its recorded stop, and absent timing remains unknown.
- [x] Project compact host and instance resources from the existing timestamped sampler; the instance includes its agent/process trees. Preserve empty, unavailable, stale and failed meanings without another sampler or motion-triggered expensive reads.
- [x] Project task agent/usage rows from persisted session lineages, preserving role, agent, model/effort, billing mode/source, observed token/cost coverage and current/previous state. Expose recorded earlier models through bounded existing evidence; show history gaps explicitly and never duplicate cumulative session totals across models.
- [x] Add only compatible widget view fields/capabilities with consumers in the upcoming Home and task views; update the relevant design contracts.
- Tests: real SQLite and FakeClock verify mixed modes and coverage, model/agent changes and closed-task history, no cumulative double-counting, run restart/stop uptime and stale/missing timing/resource observations. Domain unit tables only for genuine new projection invariants.

### Task 92: Shared compact visual language
Refs: Focus §2/§6–7; 06 §6, 07 §2–3; Tasks 89–91.
- [x] Implement the approved graphite/steel blue/sage/amber palette with quiet borders, readable primary/secondary text and dusty rose for errors. Role names and state words/glyphs carry meaning; no mandatory neon role coloring or kanji decoration in primary content.
- [x] Introduce consumed compact project metric, resource and combined agent/usage renderables. Replace vague `partial` labels with explicit coverage and show subscription, actual reported charges, estimates and unknown values distinctly.
- [x] Provide a lightweight textual stage/activity projection for actual implement/plan/setup/consult/rework/integration states, with holds and ownership distinct from signal activity. Preserve existing public strip/renderable contracts for CLI or explicitly configured plugins.
- [x] Apply the shared theme to shell, task view, palette and evidence forms; preserve width-safe glyphs, monochrome cues and the existing signal-driven motion cap. Use shared rendering rather than duplicate Home and task formatting.
- [x] Update visual design sections to actual tokens/renderables without adding theme or breakpoint configuration without an Operator need.
- Tests: behavior-focused Rich/Textual integration verifies meaningful hold/state/coverage rendering and narrow/monochrome readability. No theme token listings or new golden-snapshot catalogue.

### Task 93: Focus Home dashboard and compact project readouts
Refs: Focus §2–4/§6–8; 06 §5.4, 02 §4; Tasks 89–92.
- [x] Replace Home's default section/tab/card/button stack with the compact header, totals/resource lines, actionable attention row and selectable task list plus focused inspector at usable wide sizes. Preserve the approved B/A composition; remove obsolete default shell composition instead of maintaining two default TUIs.
- [x] Show project token/cost coverage, declared subscription summary, current-engine uptime and host/instance CPU/RAM. Do not show a separate agents resource tile, an API-equivalent estimate as spend, or invented subscription/account counts.
- [x] Preserve task ordering/search/filter/history, stable ID selection, reading position and updates. Search/filter controls become contextual keyboard/menu interactions rather than permanent button rows; recent finished tasks and long lists remain reachable.
- [x] Integrate the existing queue/header/escalation/usage/resources plugins as compact primary content when configured. Activity, Docker, faults, command history, detailed usage, explicitly configured strips and external extensions remain reachable through contextual views; every configured plugin is accounted for.
- [x] Preserve honest durable attention, faults, missing packets, engine-stop state and command feedback. Navigation/refresh must never dispatch a workflow command.
- [x] Update configuration/default placement and the affected operator design; preserve existing configuration compatibility with clear validation and no silent plugin loss.
- Tests: Textual over real SQLite verifies Home composition through navigation behavior, changed task order without lost selection, search/history access, decision/fault inspection, stopped engine and configured built-in/external plugin reachability.

### Task 94: Focus task detail and contextual operator control
Refs: Focus §4–6/§8; Workspace §6–11/§15 retained guarantees; 03 §8–9, 06 §5.5; Tasks 89–93.
- [x] Convert the shared inline inspector and `dash --task` view to compact identity/situation, actual stage/activity and one combined usage item per role/session. Display current and previously used agents/models with honest session-level attribution; remove separate bulky Task Usage/Agents panels and the permanent action-button toolbar.
- [x] Keep tokens and declared subscription/API/unknown mode beside agent/model identity; actual API charges and unreported cost remain distinguishable. Details retain input/output/cache, source/coverage, model history and provider-reported quota/reset observations. Subscription fees remain account-level and outside task charges.
- [x] Reuse the existing palette/forms for contextual actions, exposing only applicable operations with timing/consequences, typed command dispatch and durable Pending/Applied/Rejected outcomes. All existing actions, including submit/prioritize/pause/resume/cancel/rung changes/stop/export, remain discoverable.
- [x] Preserve reports/verdicts, request/ACs, facts/signals/timeline, relationships/blockers, retained completed-task evidence and existing task/role-window navigation through on-demand views. Display all ownership/holds and stale escalation rejection without discarding entered guidance.
- [x] Preserve `Enter` inspection, `Escape` return, selected-task context, role context from tmux, keyboard/mouse access and live refresh without unsolicited focus or form changes; update operator documentation for actual behavior.
- Tests: connected Textual/SQLite/git/private-tmux/fake-agent boundaries verify task inspection and retained history, an escalation answer, message delivery timing, takeover/hand-back with another task progressing, engine-down pending command recovery and export. Reuse existing scenario fixtures rather than duplicate a test catalogue.

### Task 95: Responsive compatibility and obsolete presentation cleanup
Refs: Focus §6–10; Workspace §14–16 retained guarantees, 07 §3; Tasks 89–94.
- [x] At representative wide, 48-column, 36-column, short and very small panes, keep task identity/title, critical decision/hold, usage details and action/result controls reachable. Compact Home becomes list then task detail; no compressed unreadable side-by-side inspector.
- [x] Preserve ID selection, search/filter/history, focus, entered forms and reading position across resize, row reordering and state refresh. Monochrome, `TERM=dumb` and disabled animation retain textual state and timely data refresh.
- [x] Verify every configured plugin remains reachable and legacy public fields/callbacks still work. Remove superseded private panels/navigation/styling and unused new code without deleting useful CLI/plugin contracts or preserving a second default shell.
- [x] Render real Textual wide/compact Home, task, usage/subscription, action and stale/error states for visual comparison with the approved mockup; fix density/clipping/contrast findings and retain a concise artifact/evidence record.
- [x] Audit task-owned processes, private tmux sockets and scratch outputs after checks; remove disposable leftovers while retaining review artifacts and diagnostic evidence. Update design support limits and actual known limitations.
- Tests: focused integration size/interaction matrix asserts observable reachability and preservation. Inspect rendered artifacts manually; no framework tests, theme snapshots or coverage-for-coverage additions.

### Task 96: Compact TUI walkthroughs and release verification
Refs: Focus §10; 07 §3/5; Tasks 89–95.
- [x] Reconcile README, normative design/configuration/plugin docs and walkthroughs with shipped compact behavior. Preserve Phase 8's historical verification and independently open review gates; document billing declaration, coverage, subscription/estimate meanings, model attribution and retained evidence limits accurately.
- [x] Execute and record connected fake-agent operator walkthroughs for decision/message/control, takeover with parallel progress, engine-down command recovery and completed-task evidence, plus a seeded usage scenario covering mixed billing, model history, unknown values and stale resources. Record source revision, commands and outcomes.
- [x] Run green `uv run poe check` normally and with `TERM=dumb`, with coverage at least 80%, and the existing clean-install/navigation smoke on macOS. Claim another platform only after an actual run; preserve failed attempt evidence.
- [x] Run the existing bounded real-agent E1–E6 in one invocation on the resulting source, recording date, revision, versions, duration and reported/unknown cost. Do not add speculative paid scenarios; fix product failures and follow the established retry policy.
- [x] Record Phase 9 release evidence, actual visual review sizes/states, walkthrough and gate results, remaining limits and final task-owned process/socket/directory cleanup. Close Tasks 89–96 only after their respective ACs genuinely pass.
- Verification: behavior gates, real-agent existing scenarios, clean-install smoke, visual review, documentation audit and a read-only host hygiene audit with targeted cleanup of owned leftovers.

## Phase 10 — Focus terminal parity correction

Specification: [Focus terminal parity correction](../docs/tui-parity.md). Phase 9 Tasks 89–96 remain accepted and historically complete. The approved screenshot and embedded interaction source are immutable; Phase 10 corrects the real native TUI to that reference. Tasks 97–102 are accepted complete, with Root sign-off at `580368c`; see the [Phase 10 release record](../docs/release-readiness-2026-10-05-phase10.md). The Phase 10 specification takes precedence over broader Phase 9 visual and interaction interpretations while preserving its engine, operator, billing, evidence, plugin and CLI guarantees.

Execute Tasks 98–102 sequentially with architect review between tasks. Each implementation task requires observable native Textual comparison or interaction proof for its scope and a green normal `uv run poe check` before every commit. Do not add wording, style-inventory, framework or screenshot-golden tests. Use existing SQLite/Textual fixture support and `App.export_screenshot()` for manual review as needed; do not add a screenshot dependency, harness or golden catalogue. Preserve source revision and actual terminal geometry in review records. No new paid scenario is authorized or needed for presentation-only changes; Task 102 inherits the accepted Phase 9 E1–E6 evidence unless an engine or agent behavior change establishes a reason to rerun it. Keep Tasks 59, 74h and 74p independently open.

### Task 97: Approved reference parity audit and target specification
Refs: [Parity specification §1–6](../docs/tui-parity.md); Focus §1–10; approved HTML/PNG reference; 02 §2/4, 06 §5–6, 07 §2–3; Tasks 89–96.
- [x] Record the audited application revision and compare the immutable approved terminal composition and embedded interaction handlers with current wide and compact native Textual states. Identify artifact source revisions and provenance limits; do not treat browser-prototype selectors or its palette legend as product controls.
- [x] Write a concrete gap-to-fix matrix naming current and target behavior, evidence, affected existing modules and consumed data facts. Record the missing host RAM used/total observation, timestamped latest-signal projection and conditional actual-stage elapsed source without proposing new sampling, storage or framework infrastructure.
- [x] Define exact terminal hierarchy, working cell geometry, source CSS spacing table and native-cell quantization policy, quiet borders, normal font weights, colors including selected surface `#233340`, status glyphs, task rows and `Recent`, primary data wording and always-present quiet/actionable attention-strip behavior.
- [x] Define keyboard/mouse behavior, contextual action menus, forms and direct routes. Preserve read-only navigation, native input keys, selection/focus/scroll/role context, engine revalidation, durable command outcomes, holds and all existing operation routes.
- [x] Add sequential, compact Tasks 98–102 with specific refs, files/facts, dependencies and review proof. Keep Tasks 98–102 unchecked. Reconcile normative indexes and Phase 9 status without changing historical release facts.
- Verification: inspect the approved PNG and decoded iframe `srcdoc` CSS/JS; inspect native current-session preview and retained Textual exports; review link targets, source/provenance notes, task boundaries and check state. No product changes or prose/style tests.

### Task 98: Reference-aligned Home frame and task list
Refs: parity spec §2–4/6; Focus §2–3/8; 02 §2/4, 06 §5.4/§6; Task 97.
- [x] Compose Home as the approved identity/engine line, concise operations summary, project usage line, Host/Shikiru resource line, always-present quiet-status or actionable-attention strip, decision-first task list with `Recent`, selected inspector and quiet footer. Keep the split by reference-matched width, not a height-only breakpoint. Wrap task titles at any character boundary as in the reference; truncate only when needed to keep a critical control reachable at the minimum pane, with the full title available in inspection.
- [x] Match the 42/58 list/inspector split at the measured wide grid, one separator, unframed normal-weight task rows, `#233340` selection surface with steel outline, and actual completed-task `Recent` group. Preserve bounded queries, search, ordering, selection and configured widget contracts.
- [x] Carry observed host RAM used and total through the existing cached resource snapshot/view from the same single host observation. Preserve sample cadence, timestamp/error/stale handling, process-tree deduplication and scheduler threshold inputs; do not infer from percent/available or sample again. Keep project costs, tokens, subscription-agent counts, branch and uptime evidence-backed and concise.
- [x] Always show exactly one attention strip. In a genuinely healthy, quiet state it reports `✓ No decisions needed`; say `Automation continues` only when the engine is running without a blocking hold or fault. For operator-owned, held, stopped or faulted states, show the actual state without claiming automation continues; an open decision or actionable fault uses one compact direct-action row. Do not repeat its count in the summary or add another attention card.
- Proof: [Task 98 native Home review](../docs/task98-visual-review-2026-10-04.md). Real SQLite/Textual Home behavior covers healthy quiet, operator-owned/held, stopped/faulted and actionable-decision states at 100×35, 120×40, 80×24 and 48×20; 20×8 keeps the selected identity/state and existing keyboard routes reachable. Searched All retains matching completed/cancelled rows across bounded pages. Current color/monochrome native renders compare spacing, row gaps, borders and title wrapping against parity §3. The final implementation gate at `830d34f` passed 1,162 tests with 14 skipped at 90.06% coverage; the final documentation-tree gate at `6423bf0` passed 1,162 with 14 skipped at 90.08%. Earlier attempts and logs remain in the linked Task 98 review. The shared inspector was completed in Task 99 and direct contextual routes in Task 100.

### Task 99: Compact shared task inspector and factual stage/signal rows
Refs: parity spec §2/3/4/6; Focus §4/8; Workspace §6–11/§15 retained guarantees; 03 §2–3/§8–9, 06 §5.4–5; Tasks 97–98.
- [x] Convert the shared Home inspector and `dash --task` to the exact identity, state, milestone, acting-role/stage/elapsed, situation, latest-signal, task-usage, role-row and Evidence order. Use compact wrapping and avoid duplicate section headings or large blank blocks.
- [x] Derive milestones and elapsed/signal details only from workflow transitions, reports and retained observations. Represent bypassed stages, rework, stale/unknown time, missing signals and history gaps truthfully; never invent progress or “just now.” Do not recover structured facts by parsing rendered role or progress text.
- [x] Show `Current` for the role actually acting or owned in the current task state and `Used` for earlier role use. Keep session lineage, previous agent/model identities, session-wide totals and per-turn evidence distinct in Details; preserve ownership and every hold.
- [x] Add only the bounded compatible activity facts consumed by the compact view: latest signal identity/time, and actual active-stage start/elapsed only if existing progress does not expose it. Reuse existing bounded event/timeline and current-role/progress views plus injected `Clock`; preserve missing/truncated/unreadable evidence. Add no stream consumer, store, schema, sampling or persistence.
- Proof: [Task 99 native inspector review](../docs/task99-visual-review-2026-10-04.md). Final implementation source `6706bd36c7b9e576acf83eafa084a67e3fd548bf` has true-color native `App.export_screenshot()` evidence at 100×35/120×40 and normal compact Home list → Enter detail → Escape return plus shared detail/Evidence/Usage routes at 48×20/36×20. Real temporary SQLite/Textual records show six representative Active rows, an open decision, selected Verifying task and Recent Done. Tests cover report/transition identities, re-entry, rework/bypass/truncation, task-level latest signal across role/state/filter changes, honest unknown/future timing, stalled refresh with animations disabled, compact Current/Used billing rows, preserved Views-menu reading position, plan Consult with missing history, actual-step Verify retention and integration Consult ordering. The workflow correction gate passed 1,193 tests with 14 skipped at 90.20% coverage. Test-only readiness commit `3706674` makes the held-task check wait for visible rendered cells; its full gate also passed 1,193 tests with 14 skipped at 90.20%, with serial and four-worker focused runs passing all four sizes. Earlier domain refactor and inspector commits had their own green gates. Detailed source, provenance, geometry, attempt history and bounded hygiene evidence are in the linked native review. Task 99 was accepted before Task 100; Tasks 100–101 were later accepted, and Task 102 records the final connected release evidence.

### Task 100: Contextual actions, direct attention routes and forms
Refs: parity spec §2/5/6; Focus §6; Workspace §6–11/§15; 03 §8–9, 06 §5.4–5; Tasks 97–99.
- [x] Implement cyclic Up/Down task selection, Enter inspection, Ctrl+P contextual actions, `e` Evidence, `u` Usage Details, `?` Help and Escape close/return. Keep mouse selection and direct clicks on the same routes.
- [x] Clicking primary attention selects its affected task and opens the applicable answer form. Project token/billing/subscription summaries open Project Totals; resource summaries open Diagnostics; task inline `u` and `e` links open task Usage Details and Evidence.
- [x] Present a centered quiet contextual action surface with only applicable actions, accessible project-level operations and forms with only relevant Role inputs, guidance and consequences. Opening or navigating never queues a command; submitting still uses existing typed commands and Pending/Applied/Rejected results.
- [x] Preserve native input editing keys, entered guidance, selected task, role context, search, focus, scroll and reading position on Escape, route changes and resize. Engine validation, ownership, holds, stale escalation handling and all existing actions remain authoritative.
- Proof: [Task 100 native interaction review](../docs/task100-visual-review-2026-10-05.md). Connected Home/inspector/palette/attention flows use real SQLite records; captures identify `T-1` / Leader and `T-2` / `E-1`, and all opens/routes leave the command queue empty. Coverage also includes contextual applicability, alternate roles, no-task project actions, optional fault-extension fallback, stopped/no-row attention, native Select editing, and legacy Queue Enter. Message and Answer drafts survive Back→Escape, Message survives Back→Submit→Escape, and an explicit Implementer role is restored. The full gates passed 1,207 / 14 skipped / 90.30% at `3509a24`, 1,208 / 14 / 90.33% at `7971436`, and 1,209 / 14 / 90.33% at `e03e676`.

### Task 101: Responsive and compatibility parity review
Refs: parity spec §1–6; Focus §6–10; Workspace §14–16 retained guarantees; 06 §6, 07 §3; Tasks 97–100.
- [x] Measure the 736-pixel reference frame, monospace glyph advance, line pitch and CSS breakpoints against native cell width/height. Verify one reference composition at measured wide geometry, 120×40 and 80×24/short-wide; then compact Home and detail at 48×20, 36×14/20 and 20×8. Keep identity, decision/hold, task list and routes reachable with cell wrapping/scrolling; preserve selection, search, focus, forms and reading position on resize and refresh. A short pane does not switch to list-only solely because of height.
- [x] Verify `NO_COLOR`, `TERM=dumb`, disabled motion, configured built-in and external plugins, legacy widget callbacks, CLI outputs and tmux role-pane navigation. Do not hide configured extensions, reorder their configured relative order or recreate a second shell.
- [x] Render matched actual native states/populations from real SQLite data with `App.export_screenshot()`. Record exact implementation source revision, reference/native dimensions, artifact paths, CSS-to-cell mapping, quantized spacing and cell-level findings. Resolve each applicable mismatch in §2 and spacing deviations against §3; do not accept a qualitative “direction matches” statement or copy reference sample metrics.
- [x] Update only actual support limits and known gaps. Preserve approved artifacts and existing Phase 8/9 evidence; no screenshot harness, rendering dependency or golden catalogue.
- Proof: [Task 101 measured native review](../docs/task101-visual-review-2026-10-05.md), source `116c571daee451bee57bd576ff1552d55036d5c0`. It records immutable-reference hashes, 101×35 source measurement, actual native line-box deltas, matched color/mono wide/compact/detail/Usage/Evidence/Answer/stale/error captures and truthful SQLite/resource/engine provenance. The normal gate passed 1,215 tests, skipped 14, at 90.40% coverage; targeted `TERM=dumb NO_COLOR=1` CLI/tmux/widget/plugin/motion/navigation checks passed 43 tests. Linux, CJK/ambiguous-width and below-20 support remain unverified. Task 101 was accepted before Task 102's connected final walkthrough and release evidence.

### Task 102: Connected correction walkthroughs and final evidence
Refs: parity spec §6; Focus §9–10; 07 §3/5; Phase 8 release record; Tasks 97–101.
- [x] Reconcile README, normative design indexes, Focus/parity status, plugin/configuration notes and walkthroughs to the shipped correction. Keep Phase 9 acceptance and its visual gap history accurate; retain Tasks 59, 74h and 74p as separate open gates.
- [x] Run connected fake-agent/Textual walkthroughs through Home selection, direct decision answer, task usage/evidence, contextual action form, role takeover/hand-back, command outcomes and compact return/resizing. Record observed results, source revision, actual matched-grid parity review and artifact paths.
- [x] Run normal and `TERM=dumb` full gates, the existing macOS clean-install/navigation smoke, and the final native parity review. Do not claim Linux or another host without a run. Reuse the existing Phase 9 E1–E6 record for a presentation-only change; no speculative paid scenario.
- [x] Record remaining unverified floors and final task-owned process/socket/scratch cleanup. Preserve existing user projects, tmux servers, auth/configuration and all useful prior evidence.
- Proof: [Phase 10 release evidence](../docs/release-readiness-2026-10-05-phase10.md) records source and patch fingerprints, connected walkthrough outcomes, true-color/monochrome native-cell captures including 80×14, full normal and `TERM=dumb` counts, the macOS clean-install/navigation artifacts and same-source retry history, evidence limits and bounded host-hygiene comparison. Root reviewed commit `580368c` and the native preview/XML evidence and signed off Phase 10; Root did not rerun the recorded checks. Tasks 59, 74h and 74p remain independent open gates.

## Real-project TUI feedback — reference review

### Task 103: Revise the Focus Steel HTML reference for operator review
Refs: operator feedback 2026-10-06; [Focus Steel HTML](../docs/design/references/focus-steel-approved.html); Phase 10 historical acceptance.
- [x] Update the HTML identity, checkout, spacing, metrics, recent list, selection hints, popup labels and overlays, stopped uptime and task-detail controls while retaining the original palette.
- [x] Demonstrate project-aware tmux titles and the requested stacked Leader/Implementer/project-folder layout where applicable to the browser reference. Keep task-start focus behavior explicit in the preview.
- [x] Inspect crash, recent-list, popup, selection, tmux and unknown-billing causes without changing application code. Keep billing examples tied to explicit sample declarations.
- [x] Verify the reference interactions and responsive composition, and deliver the HTML for review. Native implementation and operator approval remain subsequent work; this task does not supersede the historical Phase 10 evidence.
- Investigation: the immediate crash is the unguarded screen lookup in `plugins/widgets/usage.py:121`; a captured motion callback can outlive screen attachment. `plugins/widgets/queue.py` caps Recent at one and has no Home deselect path. Task window creation in `tmux/client.py` omits `-d`; the default layout nests the 40/40/20 stack beside a system pane. The separate tmux `PaletteApp` has no dashboard beneath its modal. The source of external window-number/hostname/button chrome remains uncertain. Billing is unknown without an explicit plugin `billing_mode` declaration and on older run rows; new settings do not relabel history. Current resource samples combine Shikiru and agents, so the requested split still needs native implementation; process-group RAM denominators in the preview mean host total RAM.
- Proof: GPT-6-Luna sub-agents at xhigh applied the reference changes and investigated the runtime symptoms. All three product inline scripts passed `node --check`; `git diff --check` passed. Isolated headless Chrome ran 25 interaction, state, color and geometry checks against a decoded `srcdoc` copy with external cosmetic scripts excluded, at 736- and 1000-pixel widths and a 380-pixel compact pane within a 500-pixel viewport. Popup, compact and stacked task-pane captures were visually inspected; the delivered iframe HTML was also rendered at 1000 pixels. Temporary evidence: `/private/tmp/shikiru-reference-review-dashboard.png`, `/private/tmp/shikiru-reference-review-popup.png`, `/private/tmp/shikiru-reference-review-compact.png`, `/private/tmp/shikiru-reference-review-task.png` and `/private/tmp/shikiru-reference-final-*-dom.txt`. The original approved PNG is retained. No application code changed and no commit was created; the Python gate was not run for this HTML/documentation-only review.

### Task 104: Correct the HTML reference after operator review
Refs: operator review of Task 103; [Focus Steel HTML](../docs/design/references/focus-steel-approved.html).
- [x] Restore explicit green Engine running and red Engine stopped beside uptime/grey Last, parenthesize checkout changes, and separate resource groups with only ` · `, preserving the palette.
- [x] Remove the invented tmux pane preview and task-title prefix. Retain the runtime requirements: existing task window titles; home `dashboard`; project-aware terminal/session title; no window switch at dispatch; separate task window with vertically stacked Leader 40%, Implementer 40%, project folder 20%.
- [x] Remove duplicate Enter inspection and its routes; task selection exposes inline details at wide and compact sizes. Remove Evidence's Situation control, TASK USAGE's self-link and ANSWER DECISION's Evidence control, retaining Escape close and the useful dashboard routes.
- [x] Verify the corrected reference, record results and deliver for review without application changes.
- Proof: GPT-6-Luna at xhigh applied the corrections, including the subsequent green-running/red-stopped clarification. Inline script syntax and `git diff --check` passed. Headless Chrome ran 26 checks covering rendered status/checkout colors, exact separator widths, removed routes, inline selection, Escape, retained usage/evidence/answer submission, compact detail access and monochrome behavior. Final runs passed at 736 and 1000 pixels and a 380-pixel compact pane within a 500-pixel viewport. Wide, Usage, Answer, compact and stopped captures were visually reviewed; the compact header uses identity, branch and runtime rows to avoid squeezing the project path. Evidence: `/private/tmp/shikiru-reference-review2-*.png`, `/private/tmp/shikiru-reference-review2-dom-*.txt`. These checks concern only the HTML draft. Native tmux behavior and TUI changes remain pending; no application code or historical PNG changed, no commit was created, and the Python gate was not run for this HTML/documentation-only review.

## Phase 11 — Real-project TUI corrections

Target: the operator-reviewed [Focus Steel HTML](../docs/design/references/focus-steel-approved.html), including Task 104 and the green-running/red-stopped clarification. Tasks 105–112 are the accepted first delivery; the fourth review adds planned Tasks 129–140 under the [fourth-review specification](../docs/tui-feedback-fourth-review.md). The subsequent real-project review extends this phase through Tasks 113–121, including Task 113a, under the [follow-up specification](../docs/tui-feedback-followup.md); new operator requests supersede conflicting prototype row spacing and presentation. Implement the extension in order, one task at a time, with a new GPT-6-Luna xhigh agent per task and root review/fixes by that same agent before continuing. The original PNG and first-delivery release evidence remain historical. The browser's outer selectors, palette legend and sample values are demonstration controls/data, not product features or observations. No task-pane preview belongs in Home. Each commit requires a green `uv run poe check`; preserve the single writer, typed commands, plugin boundaries, ownership, holds, retained evidence and configured extension contracts. Tick new ACs only when implementation and verification pass.

Planning verification: the final template/documentation tree passed `uv run poe check` on 2026-10-06: 1,219 passed, 15 skipped, 90.47% coverage. Log: `/private/tmp/shikiru-template-plan-check-final-20261006.log`. This gate supports the reference/planning commit only; it does not complete any native implementation task below.

### Task 105: Prevent dashboard refresh callbacks from crashing during screen teardown
Refs: reported `NoScreen` at `plugins/widgets/usage.py:121/126`, `dashboard/app.py:1300`; 06 §5.4; 07 §2–3.
- [x] Reproduce the reported crash through the real dashboard with configured Usage and motion/state refreshes while opening, closing or replacing screens. Make already-captured callbacks safe after screen/widget detachment; keep subscription cleanup and avoid broad exception suppression or disabling motion.
- [x] Audit the same lifecycle pattern in the other registered widgets and shared detail/overlay callbacks. Preserve active-screen refresh and stop work for detached widgets, including pending asynchronous refresh results; do not build a new refresh framework.
- [x] Add a focused real Textual/SQLite regression covering repeated navigation/teardown with refreshes in flight, asserting that the dashboard stays alive, active values still update and closed views stop receiving updates. Use existing clock/wait helpers, without sleeps or mocks of our classes.
- Proof: `src/shikiru/ui/screen.py` checks Textual's DOM-to-App `is_attached` predicate and catches only `NoScreen`; state refreshes continue for covered attached widgets, motion remains current-screen-only, and task detail refreshes on resume. `tests/integration/test_dashboard_motion.py` covers repeated stale motion callbacks, a real SQLite-backed Usage result completing after contextual screen teardown, current facts after an inactive detail screen resumes, and a captured Command History watcher called after its screen closes. `uv run poe check` passed: 1,222 passed, 15 skipped, 90.39% coverage; log: `/private/tmp/shikiru-task105-review-check-20261007.log`.

### Task 106: Align dashboard identity, checkout, summaries and engine status with the reference
Refs: updated HTML `top`, `metadata`, `projectUsage`, `attention`; dashboard header/RepoStatus contracts and GitRepoStatusService; 06 §5.4; Tasks 104–105.
- [x] Render `SHIKIRU <project> (<project path>)`, then `<branch> (clean)` in neutral white or `<branch> (+XXX -YYY)` in amber, followed by the actual integration mode such as `· Local merge`. Add the missing minimal project-root projection and cached git added/deleted-line observation through the existing checkout service and SubprocessRunner; ahead/behind commit counts are not line changes. Define tracked/staged/unstaged, untracked and binary-file handling without inventing counts or reporting a dirty checkout as clean. Retain explicit unavailable/stale facts and show a blank line after this header block. Move integration/checkout labels out of Slots and leave a blank line after Slots/running/waiting counts.
- [x] Keep explicit green `Engine running` and red `Engine stopped`. Running uptime comes from the verified current engine identity; when stopped show only grey `Last <previous duration>` for timing, with an honest grey unavailable value if that duration is missing. Remove stopped `Uptime unknown`/red previous-uptime duplication.
- [x] Align recorded tokens, session coverage, reported API charges and subscription summaries to the compact reference row, wrapping coherently at narrow widths. Remove `Decision first` from TASKS and `Engine stopped`/`Automation continues` from the `No decisions needed` row; retain independent actionable decisions, ownership and faults.
- [x] Verify native wide/short/compact color and monochrome renders against matching real SQLite/header/usage states, including clean/dirty checkout, known/missing uptime and stopped/no-decision states. Verify root and diff-stat propagation with real git fixtures covering staged/unstaged, untracked and binary changes; assert meaningful facts and bounded rendering in existing integrations. Inspect spacing/colors visually rather than adding wording or constant-list tests.

Proof: real Git `--numstat -z HEAD` totals include staged and unstaged tracked changes, classify binary entries, and keep untracked path counts separate; fixtures verify rename/deletion and commit divergence does not become line counts. Checkout service integration preserves stale/unavailable samples. Native Textual exports use matching real SQLite and Git fixture roots under `/private/tmp/shikiru-task106-fixtures-final10-{color,mono}/p0`, with local `main`, `Local merge`, dirty `+2/-1`, one binary and two untracked paths, clean and untracked-only zero-line states, running `1h 23m`, stopped known/missing previous duration, and recorded usage `2.7k tokens`, `4/4 sessions`, API billed `$0.42`, unreported `2`, one subscription agent. Exports: `/private/tmp/shikiru-task106-native-final10-color/` and `/private/tmp/shikiru-task106-native-final10-mono/`; their `review-manifest.txt` records source roots and fixture facts. Natural startup is captured at 101×35, 80×14, 48×20 and 20×8. At 101×35 all readouts fit without scrolling. At 80×14 and 48×20, natural startup and explicit page-down captures to checkout, Slots and usage are retained. At 20×8, the native SVG visually shows the complete wrapped path and running engine; the permanent responsive integration asserts natural project-identity visibility and that usage can be brought into the root viewport with `scroll_to_widget`. The retained capture matrix also includes page-down exports to checkout, Slots and usage; its capture-only helpers were removed from the permanent suite. Existing selection/keyboard-route coverage remains green. Color/mono styles were inspected in the exported SVGs. Disposable repositories, scratch and cache were removed; a task-prefix process/socket audit found no task106-specific leftovers. `uv run poe check` passed: 1,227 passed, 15 skipped, 90.40% coverage; log: `/private/tmp/shikiru-task106-check-20261007-attempt12.log`.

### Task 107: Show multiple recent tasks and make selection the single task-detail route
Refs: updated HTML `focus`, selection handlers and footer; `plugins/widgets/queue.py` one-row Recent cap; 06 §5.4; Tasks 105–106.
- [x] Replace the one-row Recent cap with a bounded list from existing history/browser queries, newest first. Show multiple matching completed/cancelled tasks when available, without `depends on ...`/ancestor dependency decoration in Recent rows; keep those relationships available in the appropriate detail/evidence views and preserve full History pagination.
- [x] Selecting a row by click or cyclic Up/Down updates the shared inline task details. Keep those details reachable in both wide split and compact stacked layouts; remove Home's duplicate Enter inspect popup, footer hint and contextual Inspect task entry. Enter may activate the focused row or native inputs/forms, but must not open a second copy of its details. Retain useful task-focused/related-task and external-widget callbacks without duplicating the default Home route.
- [x] Escape closes the active popup first; at Home it clears task selection and the inspector, with `Esc clear` in the footer. Respect native input editing and existing search-clear precedence. Clearing persists across refresh/resize, leaves project actions available and allows Up/Down to select safely from no selection. Add the real tmux detach hint after `Ctrl+P actions`, then Help; respect custom tmux prefixes and offer a working detach action where the footer is clickable.
- [x] Verify at least three retained Recent records, selection/deselection through refresh and resize, search/filter/history behavior, compact detail access, keyboard/mouse equivalence and no command submission from navigation using real SQLite/Textual integrations. Preserve list scroll, entered search and task identity when an overlay closes.

Proof: real SQLite/Textual Home tests cover four Recent records (done and cancelled), newest-first order, dependency-free Recent rows, preserved History pagination, mouse/cyclic keyboard selection, no Enter detail duplication or command submission, search-clear precedence, explicit clear across refresh/resize, and compact 48×20/36×20/20×8 detail reachability. Private tmux integration constructs the binding before any client attaches to a verified project-root pane. The footer resolves inherited global `C-b`, then the session-local override `C-a`; at activation it revalidates an attributable project client and detaches only that client, leaving the unrelated session client attached. The 120×40 Home footer export `/private/tmp/shikiru-task107-corrective-native/task107-wide-footer-120x40-color.svg` shows Select, Clear, Actions, `Ctrl+A d detach`, and Help on one row, also asserted at the Textual boundary. The shared task detail and list refresh after an overlay closes while preserving search and selection. Existing explicit external `TaskPaletteRoute.INSPECT` and related-task routes remain exercised. Native true-color and monochrome exports: `/private/tmp/shikiru-task107-native-review-color/`, `/private/tmp/shikiru-task107-native-review/`, and `/private/tmp/shikiru-task107-corrective-native/`, including 120×40/101×35/80×24 Home and inline detail at 48×20, 36×20 and 20×8. `uv run poe check` passed: 1,232 passed, 15 skipped, 90.24% coverage; log: `/private/tmp/shikiru-task107-followup-check-final.log`.

### Task 108: Keep action and detail popups over the dashboard and remove redundant controls
Refs: updated HTML modal surfaces; `dashboard/palette.py`, `dashboard/detail.py`, tmux palette entry; 06 §5.4–5; Tasks 105–107.
- [x] Present contextual Actions, Answer, Evidence, Usage, Project Totals, Diagnostics and other popup details as bounded, scrollable overlays over the mounted dashboard, with the original surface/border palette and visible dashboard underneath. Route Home's tmux palette shortcut into its already-running DashboardApp and verify both that route and Ctrl+P; a translucent screen over an empty standalone PaletteApp is not an acceptable dashboard overlay. Preserve originating task/role context for entry from separate task panes and keep standalone palette access functional for those callers.
- [x] Use `Esc ×` as the popup close control. Remove `← Situation` from Evidence, `u Details` from TASK USAGE itself, `e evidence` from ANSWER DECISION and `Ctrl+P actions` beside Evidence in task details. Retain the inline dashboard Usage/Evidence entry points, contextual actions and explicit Answer submission.
- [x] Preserve guidance/role drafts, selection, focus and reading position through popup transitions, Escape, refresh and resizing. Keep forms' native editing/Enter semantics, stale-decision validation and Pending/Applied/Rejected feedback; opening or routing never queues a command.
- [x] Exercise normal/contextual/compact popups, Answer submission, cancellation and preserved drafts against real Textual/SQLite/operator boundaries. Capture native overlay composition, including the Home tmux entry, to prove the underlying dashboard is visible rather than accepting background-color assertions alone.

Proof: `uv run poe check` passed with `PYTEST_ADDOPTS="-n 2"`: 1,237 passed, 15 skipped and 90.18% coverage; log: `/private/tmp/shikiru-task108-full-check-final.log`. The project default remains `-n auto`; the final full gate used two workers to keep the process-heavy Textual/tmux integration run repeatable. Earlier development failure logs contain short UI-wait and command timeouts as well as then-unfixed callback errors, so they do not establish that worker pressure alone caused every failure: `/private/tmp/shikiru-task108-last-failed.log` and `/private/tmp/shikiru-task108-last-failed-2.log`. No tests were omitted from the final gate. The isolated live tmux integration attached a real client, sent prefix then Space through the client key table, and verified Actions over the still-running DashboardApp (same pane PID and `TASKS` visible); its pane capture is `/private/tmp/shikiru-task108-native-color/task108-prefix-space-actions-pane.txt`. It then verified Escape and Ctrl+P open/close behavior. The compact 48×20 Actions capture shows the Home `Ctrl+P` and tmux footer beneath the overlay; the wide capture shows the task row and `TASKS`. Native true-color and monochrome wide/compact captures for Actions, Answer, Evidence, Usage, Project Totals, Diagnostics and resource details are in `/private/tmp/shikiru-task108-native-color/` and `/private/tmp/shikiru-task108-native-mono/`.

The 36×20 monochrome Usage render was re-captured after its content and in-bounds close control settled. The close-button integration clicks that control and verifies the same Home task selection and focused widget return; the refreshed capture is `/private/tmp/shikiru-task108-native-color/task108-usage-36x20-mono.svg`.

### Task 109: Preserve tmux focus and correct task panes, home naming and chrome
Refs: `tmux/client.py`, `tmux/session.py`, `tmux/layout.py`, `config/defaults.toml`, layout guard; 06 §5.1–3; Tasks 105–108.
- [x] Create new task windows detached, including their pane splits and ordinary guard restoration, so dispatch does not switch the attached client's current window or pane. Explicit operator navigation/takeover still selects its requested target. Verify from an attached client on the isolated `tmux_server`, through dispatch and recovery; do not touch the operator's other tmux sessions.
- [x] Make the default separate task window a full-width vertical stack: Leader 40%, Implementer 40%, a shell labelled Project folder 20%, with no extra dashboard/system pane consuming part of those percentages. Make guard restoration follow configured layout leaves so it cannot recreate an omitted system pane; custom layouts retain their configured special panes. Keep role stream/takeover and shell cwd semantics. Document that configs without a layout override inherit the new packaged default, while explicit custom layouts remain until the operator changes/removes that override; do not overwrite them. Verify pane geometry within tmux rounding after guard ticks and pane recovery, plus navigation.
- [x] Name the permanent home window `dashboard` so it appears as `0:dashboard`; reuse/rename the identified existing Home idempotently rather than creating a duplicate, and handle restart/rename races without changing unrelated windows. Verify upgrade from an existing shikiru-named Home and repeated restart on the isolated tmux server. Put the project name in the tmux terminal/session title. Keep task `dashboard.titles.window` templates and resulting task window titles unchanged, without an added `shikiru` or project prefix.
- [x] Identify the actual source of the redundant top window-number/hostname/`[z][x]` chrome from a native attached run and remove Shikiru-owned dashboard chrome. Preserve the requested SHIKIRU identity row, useful role-pane labels and unrelated user terminal/tmux settings. Do not invent a Home pane-preview control to represent tmux behavior.
- [x] Update operator/configuration documentation for the changed default, migration choice, Home name and palette/detach behavior. Record a native tmux walkthrough proving focus retention, layout, titles and chrome, in addition to isolated integration checks.

Proof: the attached-client engine integration keeps the same client window/pane through fake-agent dispatch, title refresh and guard recovery; explicit operator navigation still selects the task window. Session integration covers a server-global `base-index=1`, local Home index `0`, global-option preservation, renamed/duplicate Home convergence, restart after Home removal, and a protected index-0 collision that leaves the unrelated window's identity and index unchanged. The real geometry integration loads the packaged layout through config validation instead of mirroring it; pane recovery retains survivor identities/processes and restores measured rows within tmux rounding. A private live `DashboardApp` and fake-pane walkthrough is retained at `/private/tmp/shikiru-task109-native-u21910us/`: at an attached 120×40 client it records Home `0:dashboard`, session title `demo`, global base index `1`/session base index `0`, the Home identity row, unchanged client focus through detached task creation/title refresh/recovery, and explicit task/Home navigation. The 40/40/20 layout measured 15/14/7 content rows both before and after recovery (41.7/38.9/19.4%); Leader `%2` and shell `%4` IDs/processes survived while Implementer `%3` was replaced with `%5`. Pane metadata shows `pane_title=Pavels-Mac-mini.local` and `pane_current_command=Python`; this host label belongs to tmux's pane title, while the Shikiru Home identity is the captured `SHIKIRU demo (...)` line. Home sets `pane-border-status=off`, task role labels retain `top`, and the global tmux status remains at its existing bottom position. This removes the extra Home pane-border chrome without suppressing Home identity or changing global terminal/tmux settings. No Home pane preview was added. The earlier private 120×40 geometry capture at `/private/tmp/shikiru-task109-native-b1t58li3/` is retained as development evidence; it exposed 15/15/6 row rounding before the absolute-row restoration fix.

`UV_CACHE_DIR=/private/tmp/shikiru-task109-uv-cache PYTEST_ADDOPTS="-n 2 --dist=loadfile" uv run poe check` passed all checks: 1,245 passed, 15 skipped, 90.09% coverage in 475.89s, with no tests omitted; the repository default remains `-n auto`. Final log: `/private/tmp/shikiru-task109-review-check-loadfile.log`. Earlier full runs are retained at `/private/tmp/shikiru-task109-review-check-final.log`, `/private/tmp/shikiru-task109-review-check-green.log` and `/private/tmp/shikiru-task109-review-check-single-worker.log`; they first caught the protected Home-index handling, then unrelated existing Textual timing failures. Focused tmux/session validation passed 17 tests at `/private/tmp/shikiru-task109-focused-tmux-check.log`; the isolated timing repro passed at `/private/tmp/shikiru-task109-dashboard-timing-repro.log`. Native captures and facts remain in the walkthrough directory, and the initial failing geometry capture remains available for comparison.

### Task 110: Split host, current Shikiru instance and agent resource measurements
Refs: updated HTML `resourceMetrics`/Diagnostics; `telemetry/resources.py`, dashboard contracts/views and resource widget; 02 §2, 06 §5.4; Tasks 105–109.
- [x] Establish reliable process ownership for the current project's engine/dashboard/view processes and its headless/taken-over agent trees, using recorded turn.pid ProcessIdentity and role ownership/run/pane identities rather than provider-specific command-name guesses. Deduplicate by PID/start identity, exclude other projects, and keep Shikiru and Agents disjoint, including descendants/tools. Supply the missing classified roots to the existing sampler through only a minimal normalized identity projection if needed; document that scope before coding and do not invent measured attribution.
- [x] Reuse one timestamped cached sampling cycle to produce Host CPU/used RAM, Shikiru CPU/RSS excluding agents, and CPU/RSS for all agents belonging to this instance. Keep CPU percentages normalized to host capacity, the injected Clock, configured cadence, disappeared-process handling and existing scheduler thresholds. Preserve any compatible existing aggregate field rather than breaking widget/plugin callers.
- [x] Render `Host CPU X% RAM A/B GiB · Shikiru CPU X% RAM A/B GiB · Agents CPU X% RAM A/B GiB` with only single-space dot separators and coherent responsive wrapping. B is sampled host RAM total for each group; Diagnostics explains process RSS versus host used RAM and that B is not a per-process limit. Preserve unknown/empty, error/stale and timestamp states; never turn absent ownership/failed samples into a measured zero.
- [x] Verify attribution, deduplication, process exit/PID reuse and other-project exclusion with task-owned real process trees via SubprocessRunner and the fake agent, using existing clock/wait helpers. Check the dashboard and Diagnostics show the same cached observations and review native wide/compact layout. Add no independent refresh timer, sampler service or provider coupling.

Task110 evidence: the smallest consumed ownership projection is `ResourceProcessRoots(shikiru, agents)`, each containing only verified `ProcessIdentity` values. Shikiru roots are the lock-verified engine, this DashboardApp process, the `@shk_home=1` pane regardless of window name, exact recorded task-role panes and a configured task `system` pane only when its recorded window and explicit current-project `dash --task <id>` start command agree. Marker-only restored system shells and custom operator panes are excluded. Live headless agent roots come from each matching live run's recorded `turn.pid`, and taken-over roots come from exact task/run/role pane ownership. Descendants require the recorded PID/start identity to remain live; overlap is deduplicated by `(pid, started_at)` with agent ownership taking precedence. Other-project panes are excluded by project task/window ownership. This scope is recorded in docs/design/05-data.md. The existing aggregate `instance_*` fields remain available; unclassified legacy aggregate roots leave split attribution unknown. Host CPU is normalized by logical CPU capacity, host RAM uses one used/total observation, and process RSS uses that same host total as denominator. Home and Diagnostics read the same cached sample and preserve missing, empty, failed and stale states.

Real-process integration coverage verifies headless fake-agent attribution, disjoint Shikiru/Agents trees, overlapping-root deduplication, exited/reused PID identities and exclusion of a different project's private-tmux pane. The same private-tmux boundary proves that a renamed marker-owned Home and a real configured task dashboard are included, while an unmarked `dashboard` window, a marker-only system command, a shell leaf and other-project resources are excluded; the recorded taken-over role pane remains Agents-owned. Home/Diagnostics sample identity is checked against one cached observation. At 48×20, Home naturally shows Host in the available viewport; keyboard and mouse scrolling reveal Shikiru and Agents without displacing task identity, selection or actions. Wide and compact Diagnostics show all groups and the RSS/host-total explanation. Color and monochrome native SVG/PNG evidence is retained in `/private/tmp/shikiru-task110-native-color/` and `/private/tmp/shikiru-task110-native-mono/`; Chrome render log: `/private/tmp/shikiru-task110-chrome-render-final.log`. The in-app browser runtime was unavailable; the existing isolated local Chrome fallback rendered and was visually inspected. Native focused checks: `/private/tmp/shikiru-task110-native-color-final.log` and `/private/tmp/shikiru-task110-native-mono-final.log`.

The initial implementation gate at `0dbbc64` passed with host process enumeration and isolated private-tmux access: 1,250 passed, 15 skipped, 90.15% coverage in 478.46s. Its log remains `/private/tmp/shikiru-task110-check-final-escalated.log`; the earlier sandbox-only retry remains `/private/tmp/shikiru-task110-check-final-green4.log` and is not an acceptance result. After the pane-ownership review correction, `UV_CACHE_DIR=/private/tmp/shikiru-task110-uv-cache PYTEST_ADDOPTS='-n 2 --dist=loadfile' uv run poe check` passed: 1,250 passed, 15 skipped, 90.11% coverage in 476.68s, with no cases omitted and repository `-n auto` unchanged. Corrective gate log: `/private/tmp/shikiru-task110-correction-check-final-3.log`; the focused private-tmux boundary passed in `/private/tmp/shikiru-task110-correction-focused-4.log`. The two earlier corrective attempts are retained in `/private/tmp/shikiru-task110-correction-check-final.log` and `/private/tmp/shikiru-task110-correction-check-final-2.log`; they stopped at the literals and type checks before running pytest.

### Task 111: Make unknown billing modes understandable and preserve declared subscription facts
Refs: operator's `billing modes unknown` report; agent billing capability and persisted runs; 04 §1, 05 §1, Focus billing guarantees; Tasks 105–110.
- [x] Trace explicit plugin `billing_mode` configuration through factory, run creation/resume, persistence and project/task usage views. Reproduce known subscription/API, explicitly configured unknown, unavailable declaration and legacy cases from retained evidence; inspect a supplied real-project config/database read-only if available. Correct dropped or miscounted declarations only where evidence demonstrates a bug.
- [x] Show Subscription for explicit subscription declarations and API for explicit API declarations. Explain unknown modes from persisted mode/source: operator_config with unknown means explicitly set to unknown; unknown mode/source means declaration unavailable or not recorded, without inventing its historical cause. Provide accessible guidance for `[agents.<name>.options].billing_mode`, with unknown-session coverage separate from unreported API charges. Do not infer subscription from authentication, provider, model, quota or missing charges, and do not relabel historical runs from current config.
- [x] Preserve actual billed versus API-equivalent evidence, token/session coverage, recorded zero, missing charge coverage and historical mixed declarations. Subscription fees remain account-level/unrecorded unless reliable evidence says otherwise; no fabricated prices or account identities. Keep summaries compact as in the reference and provenance/setup explanation in Usage/Project Totals/help.
- [x] Verify explicit mode/source propagation and historical retention through real run-store/engine/view integration, including configured unknown, resume/config changes and plugins without the optional capability. Reuse existing domain/parser coverage; add tests only for uncovered declaration/counting behavior. Review native Usage/Project Totals with unknown-mode counts and mixed retained declarations alongside the reference's known-mode examples. Document the evidence-backed cause or unresolved cause and setup guidance; do not claim the operator's account mode or a billing bug fix without real-project evidence.

Task111 evidence: Existing factory and engine/store integration coverage traces explicit API/subscription/operator_config declarations, explicit unknown, optional plugins, new-run persistence, resume and config changes; no propagation defect or dropped declaration was reproduced. Added source-specific unknown-mode counts to project/task Usage views and kept them distinct from API charges not reported. The mixed-history integration case preserves 9 sessions, 2 subscription sessions, 5 unknown-mode sessions (1 `operator_config`, 1 `provider_reported`, 3 source `unknown`), one API charge-unreported session, one API-equivalent estimate ($1.50), and the existing actual billed subtotal ($2.50 across 2 sessions, including an explicitly reported $0.00 API charge). Usage, Project Totals and Help explain each persisted source and setup for new sessions; source fields are never inferred from current configuration. Read-only inspection of the supplied project found no `billing_mode` setting under current Claude/Codex options and 29 retained rows with `unknown`/`unknown`. This cannot establish why historical declarations are unavailable or the operator's account mode, so cause remains unresolved. Native wide/compact color and monochrome evidence is retained under `/private/tmp/shikiru-task111-native-{color,mono}/`; Chrome-rendered PNGs under `/private/tmp/shikiru-task111-native-rendered/` were visually inspected. Final `UV_CACHE_DIR=/private/tmp/shikiru-task111-uv-cache PYTEST_ADDOPTS='-n 2 --dist=loadfile' uv run poe check` passed: 1,250 passed, 15 skipped, 90.10% coverage in 480.64s; log `/private/tmp/shikiru-task111-check-final-escalated.log`.

### Task 112: Verify every reported issue and record native reference alignment
Refs: Tasks 105–111; updated HTML; 07 §3; historical Phase 10 evidence.
- [x] Run a connected fake-agent/operator/tmux walkthrough covering dashboard refresh/teardown, dispatch without window switching, the separate 40/40/20 panes, multiple Recent tasks, selection and Escape, inline compact detail, all popup entry/close routes, Answer submission, detach, resource scopes and billing declarations. Exercise stopped/restarted engine states and retain command/ownership/hold guarantees.
- [x] Compare matched native populations/facts against the current HTML, not its sample values or historical PNG. Record exact source revision, reference hash, measured CSS-to-cell mapping and color/monochrome artifacts at wide, short-wide and compact geometries, including 101×35, 120×40, 80×24/14, 48×20, 36×14/20 and the previously supported 20×8 floor. Resolve reachable-detail, spacing, separator, overlay, palette, label and keyboard gaps rather than accepting a qualitative resemblance; no golden catalogue or screenshot dependency.
- [x] Run normal and `TERM=dumb NO_COLOR=1` full quality gates plus the existing macOS clean-install/attach/navigation smoke. Verify configured built-in/external widgets and custom tmux layouts remain reachable. Real-agent E2E is required only if changed engine/agent behavior warrants it under the existing verification rules; do not claim Linux/other-host support or paid-agent evidence without a run.
- [x] Reconcile normative operator/architecture/configuration/Focus/parity docs and walkthroughs with the delivered behavior, including removing obsolete Enter inspection/default system-pane instructions. Preserve prior acceptance history and original PNG, record each report item's result and any actual remaining limitation, and clean up only task-owned processes/sockets/scratch. Tick this task only when all preceding ACs and the issue checklist have evidence.

Task112 evidence: The [Phase 11 release record](../docs/release-readiness-2026-10-07-phase11.md) records all walkthroughs, the matched fixture and CSS-to-cell matrix, native color/monochrome exports, per-issue outcomes, full gates, smoke, billing/platform limits and ownership cleanup. Implementation revision `b84c5214b62f97b12705219005ffd8545f0effa2`; HTML source SHA-256 `b674503697e4dafaedb97ff2a440baacf9f51f288dda6b37bc7bf05b508a2de4`.

### Reported-issue coverage

| Reported issue or review correction | Implementation / verification |
|---|---|
| Intermittent `NoScreen` and dead dashboard pane | 105, 112 ([release evidence](../docs/release-readiness-2026-10-07-phase11.md)) |
| Token/session/API alignment; identity/path, branch/change parentheses and color; blank rows; Slots labels; TASKS heading | 106, 112 ([release evidence](../docs/release-readiness-2026-10-07-phase11.md)) |
| Green Engine running/red Engine stopped; grey Last when stopped; concise No decisions needed | 106, 112 ([release evidence](../docs/release-readiness-2026-10-07-phase11.md)) |
| Multiple Recent tasks without dependency decoration | 107, 112 ([release evidence](../docs/release-readiness-2026-10-07-phase11.md)) |
| Escape clears selection; remove duplicate Enter inspect; keep inline details accessible; detach hint after actions | 107, 109, 112 ([release evidence](../docs/release-readiness-2026-10-07-phase11.md)) |
| Popups overlap the dashboard; `Esc ×`; remove Situation, Usage self-link, Answer Evidence and task-detail actions duplication | 108, 112 ([release evidence](../docs/release-readiness-2026-10-07-phase11.md)) |
| No dispatch window switch; separate vertical Leader/Implementer/shell 40/40/20; no pane preview | 109, 112 ([release evidence](../docs/release-readiness-2026-10-07-phase11.md)) |
| Remove redundant top chrome; project-aware terminal/session title; `0:dashboard`; preserve task window titles | 109, 112 ([release evidence](../docs/release-readiness-2026-10-07-phase11.md)) |
| Host/Shikiru/Agents CPU and RAM with exact dot separators and truthful instance/agent scopes | 110, 112 ([release evidence](../docs/release-readiness-2026-10-07-phase11.md)) |
| Unknown billing mode versus explicit subscription/API and unreported charges | 111, 112 ([release evidence](../docs/release-readiness-2026-10-07-phase11.md)) |

### Task 113: Keep live agent motion responsive and animate active task status
Refs: follow-up Motion; `domain/motion.py`, `dashboard/app.py` motion loop, `dashboard/motion.py`, `plugins/widgets/queue.py`, detail callbacks; 02 §2, 06 §6.5, 07 §2–3.
- [x] Reproduce and distinguish intentional recent-signal freezing from slow callback/refresh starvation with real current-turn evidence. Keep live, unheld, non-stalled agent indicators cycling through quiet long-running tools; retain honest last-signal ages and stop/static behavior for ended, held, waiting, stalled and stopped-engine cases. Document the refined indicator meaning rather than manufacturing activity.
- [x] Prevent a slow refresh callback from blocking unrelated frame updates or indefinitely extending the configured capped cadence. Reuse Clock, existing caches and lifecycle subscriptions; avoid overlapping callback work, extra polling/sampler timers, unsafe reader threading and detached-result updates.
- [x] Blink the status of tasks currently processed by agents from cached recorded current-turn facts, independently of signal freshness. State words remain readable; disabled animation and monochrome/static mode remain usable, and decisions/holds/waits never acquire a misleading working animation.
- [x] Add only necessary behavior/rule coverage for a quiet live fake-agent turn, blocked refresh, phase progression and pause/stop/detachment. Review native normal/static motion and run the full quality gate; preserve Task105 teardown guarantees.

Proof: the focused real SQLite/Textual/FakeClock cases cover quiet live-tool motion with unchanged recorded signal time, non-overlapping callbacks and unsubscribe before the callback starts, both active status rows, disabled-motion Home crossing the configured tool-stall deadline while the displayed signal age advances, and mounted queue refresh during removal/recomposition followed by later SQLite task additions. Motion ticks retain bounded task-browser reads and active-task projections plus cached current-turn event/tail facts; they do not request full task-detail timeline/evidence. `stream_active_seconds` is no longer in packaged defaults or the motion domain; old explicit values remain accepted as a deprecated compatibility field and no longer control activity. Updated configured-queue native exports in color/monochrome: `/private/tmp/shikiru-task113-native-corrected/`; inspector content is settled before capture. The earlier static-mono loading frame is retained at `/private/tmp/shikiru-task113-native/task113-home-status-static-mono-before-ready-content.svg`. `PYTEST_ADDOPTS='-n 2 --dist=loadfile' UV_CACHE_DIR=/private/tmp/shikiru-task113-uv-cache uv run poe check` passed: 1,260 passed, 15 skipped, 90.11% coverage in 487.06s; log: `/private/tmp/shikiru-task113-corrective-check-final.log`.

### Task 113a: Update the HTML reference for the second review
Refs: operator's request to update the template using a sub-agent; follow-up presentation contract; `docs/design/references/focus-steel-approved.html`; Task113.
- [x] Update the existing HTML through a new GPT-6-Luna xhigh agent, preserving its approved graphite/steel/sage/amber/dusty-rose palette and project identity, branch parentheses, running-green/stopped-red engine states, direct selection and separate task-window semantics.
- [x] Demonstrate live active-status motion with static/reduced-motion and monochrome alternatives, contiguous task rows, fixed Recent heading with an independently scrollable body and truthful overflow cues, capitalized grey terminal statuses, and clear durable command feedback on the footer's right side. Add only enough sample records to exercise overflow; identify preview data as demonstration rather than engine facts.
- [x] Keep the dashboard stationary under centered overlays, place visible `Esc ×` controls outside scrolling bodies, and align resource groups and composed Project Diagnostics. Make advertised Enter resolution open the relevant decision without submitting it; preserve input editing, drafts, keyboard/mouse selection, close and compact reachability. Do not add a Home task-pane preview or change the existing tmux wall clock.
- [x] Inspect the actual HTML in wide/compact color/mono and scrolling/modal states, retain paired review evidence, and run the complete quality gate before committing. Preserve the original PNG and historical first-delivery reference at `f367f4e`; record the revised HTML hash/source and update follow-up references so Tasks114–121 verify the new template rather than the old immutable HTML. Avoid browser test catalogues, permanent harnesses or new dependencies.

Proof: Revised HTML source SHA-256 `9585e8a892bf3811e4f864f562ea41e72f6874e20ceafbb6afcb17b08ea7ff06`. The first-delivery HTML remains at Git `f367f4e` with SHA-256 `b674503697e4dafaedb97ff2a440baacf9f51f288dda6b37bc7bf05b508a2de4`; the original 736-pixel PNG remains unchanged at SHA-256 `3c91f20ee6e96bb2b0f36c8f6baa5b1565fb625eec269974578230e841f5ece3`. Chrome 154.0.8037.98 reviewed the current embedded preview in a private local profile. The review measured two-line task rows with 0-pixel gaps across five active and seven Recent row pairs, grey capitalized terminal states, a fixed Recent heading with a 349-pixel body in a 102-pixel viewport, and truthful top/middle/bottom cues. It verified active status text changes from regular to bold while the four-glyph spinner cycles without changing the preview signal; decision, waiting, paused, Done, Cancelled and stopped states remain static. Static and reduced-motion remove both animations; monochrome retains readable status text. Enter opened E-7/T-42 without submitting, the Esc × header stayed visible, popup underlay/footer geometry did not move, and drafts plus caret selections survived rerender, Escape close and reopen. At 390-pixel compact width there was no horizontal overflow. Updated screenshots: `/private/tmp/task113a-corrective-wide-color.png`, `/private/tmp/task113a-corrective-static-mono.png`, `/private/tmp/task113a-corrective-reduced-motion.png`, `/private/tmp/task113a-corrective-decision-modal.png`, `/private/tmp/task113a-corrective-compact-color.png`, and `/private/tmp/task113a-corrective-compact-footer.png`; assertion/geometry report: `/private/tmp/task113a-corrective-browser-report.txt`. Prototype sample values remain demonstration data, not engine observations. The complete gate `PYTEST_ADDOPTS='-n 2 --dist=loadfile' UV_CACHE_DIR=/private/tmp/task113a-uv-cache uv run poe check` passed: 1,260 passed, 15 skipped, 90.12% coverage in 484.88s; full log: `/private/tmp/shikiru-task113a-corrective-check.log`.

### Task 114: Correct tmux title contrast and agent labels while preserving the clock
Refs: follow-up tmux; [current revised HTML palette](../docs/design/references/focus-steel-approved.html); `tmux/decorator.py`, `tmux/session.py`, `engine/titles.py`, packaged titles; 06 §5.1–3/§6, 05 §1; Tasks113/113a.
- [x] Give Shikiru-owned active/inactive task window and pane title surfaces an explicit contrasting graphite background with readable foregrounds, preserving useful current-window distinction and the approved palette. Verify effective scoped options on an isolated attached tmux client seeded with conflicting inherited styles.
- [x] Remove role/state glyph prefixes from shipped agent pane labels and the pane-border format; retain readable role, model/effort, iteration, context and ownership/hold words. Preserve task window templates, project terminal title, Home index/name and explicit custom configuration; document any custom-template migration choice without rewriting the operator's file.
- [x] Preserve the existing wall clock and useful session/window navigation, global styles/options, other sessions, palette/detach keys and dispatch/recovery focus. The operator questioned the proposed clock removal; no clock replacement was requested.
- [x] Verify active/inactive/readability and title/focus behavior on private tmux, inspect native pane/status captures, update active documentation and pass the full quality gate.

Proof: Packaged Leader/Implementer titles and the copyable TOML example in 05 §1 use word labels and `flags_text`; `glyph` and glyph-prefixed `flags` remain available to explicit templates, which Shikiru never rewrites. The project `titles.window`, project terminal title, Home `0:dashboard`, palette/detach bindings and inherited wall clock remain unchanged. The example TOML parsed and matched both packaged role-title defaults.

The shared-color extraction is commit `c2a9600 refactor: share semantic colors with tmux`; its full gate passed with 1,260 passed, 15 skipped and 90.13% coverage in 487.96s (`/private/tmp/shikiru-task114-refactor-check.log`). The title behavior and integration tests are commit `fb76337 fix: improve tmux task title contrast and labels`; its full gate passed with 1,262 passed, 15 skipped and 90.14% coverage in 487.08s (`/private/tmp/shikiru-task114-fix-check.log`). Both complete `poe check` runs used `uv run --no-project` with the existing locked environment, `PYTEST_ADDOPTS='-n 2 --dist=loadfile'` and an explicit candidate `PYTHONPATH`; direct module paths for each stage are recorded in `/private/tmp/task114-native/refactor-stage-imports.txt` and `feature-stage-imports.txt`. The feature-stage import record also includes a child-process probe; the same candidate `PYTHONPATH` was exported to the gate processes. The 05 TOML example parse check passed.

The real attached-client PTY transcript at `/private/tmp/task114-native/attached-client.typescript` captures both active-window states with pane borders, window-status rows, task labels and the clock/date. Its decoded rows and emitted SGR evidence are in `/private/tmp/task114-native/attached-client-color-evidence.txt`; the effective scoped formats and seeded inherited styles are in `/private/tmp/task114-native/attached-client-facts.txt`. The stream emits primary text `#dce2e9`, inactive graphite `#171d25` and selected graphite `#233340`; each task window keeps the current-window bracket/bold distinction. The facts retain the clock format `%H:%M %d-%b-%y`, visible clock/date, prefix `C-b`, terminal title, Home index/name, detached-creation focus and the unchanged cyan/green global styles with the separate `test` session present.

The initial combined implementation attempt failed the import-layer contract because `tmux` imported `ui.tokens`; that log is preserved at `/private/tmp/shikiru-task114-import-boundary-failure.log` and the palette extraction fixes the boundary. An initial full run recorded one failure in `test_task_evidence_filter_and_reading_position_survive_resize_and_refresh` (1 failed, 1,261 passed, 15 skipped, 90.13%) at `/private/tmp/shikiru-task114-full-check-initial-ui-failure.log`; its isolated rerun passed (1 passed in 1.32s; `/private/tmp/shikiru-task114-ui-isolated-rerun.log`). During Task115, two complete full runs reproduced the same post-refresh visibility failure. A bounded trace showed the unchanged focused timeline input clipped one row below the body viewport while the saved scroll target and focus generation still matched; the focused descendant is now revealed after restoring that saved position. The trace and correction are recorded with Task115 evidence below.

### Task 115: Keep popup underlays stationary and align frames and close controls
Refs: reproduced before/Help SVGs; follow-up popup geometry; [current revised HTML modal CSS](../docs/design/references/focus-steel-approved.html); dashboard modal classes and widget dialogs; 06 §5.4–5; Tasks113–114.
- [x] Remove transparent ModalScreen root gutter effects so the rendered Home shell, identity, resources, task rows and inline inspector stay at identical cells before, during and after overlays. Apply required popup inset to child composition, retaining standalone palette entry from task/role panes.
- [x] Align Actions, Answer, Evidence, Task Usage, Project Totals, Diagnostics, Help, Views, command history and existing widget/packet dialogs with the reference's centered maximum width, surface/steel border and cell-quantized spacing. Use compact consumed styles rather than a new popup framework; remove superseded conflicting layout rules.
- [x] Keep `Esc ×` fully visible at each popup header's right edge outside its body scroll area, operable by mouse and Escape. Preserve native Select Escape precedence, form editing/drafts, selection/focus/reading position, nested close behavior and 20×8 Open/result reachability.
- [x] Verify representative normal/contextual routes on mounted default Home with settled rendered underlay coordinates and actual control containment, including wide, 48×20, 36×14/20 and 20×8. Review paired native artifacts against current HTML; no DOM-presence-only assertions or permanent screenshot catalogue. Pass the full gate.

Source: `b6a15e3`, `ce874d2` and `0aca8f8`. The compositor check compares rendered Home text origins before, during and after Help; a bounded root-gutter reproduction moves them exactly one column and one row (`/private/tmp/shikiru-task115-origin-regression-proof.log`). The Help and Views scroll bodies now size to natural content up to the frame cap; command history limits its body to the measured viewport space so its controls remain visible on resize. The observed task-detail refresh case restores the unchanged focused descendant only when the screen, focus generation and saved scroll target still match.

The final affected route boundary suite passed 238 tests with one existing skip in 118.18s (`/private/tmp/shikiru-task115-natural-final-boundaries-after-scrollfix.log`). Two full runs before the focused-control correction each had one failure in the existing evidence-filter visibility case (1,262 passed, 1 failed, 15 skipped; `/private/tmp/shikiru-task115-full-check-natural-final3.log` and `...final4.log`). The bounded before-fix trace records the clipped input and unchanged saved scroll/focus state at `/private/tmp/shikiru-task115-post-refresh-trace-isolated.log`; the corrected route passed with the same observable visibility assertion. The final full gate passed 1,263 tests with 15 skips at 90.19% coverage in 491.31s (`/private/tmp/shikiru-task115-full-check-natural-final5.log`).

Paired settled Home/Help SVGs, browser-rendered PNGs and source facts are retained under `/private/tmp/shikiru-task115-final-evidence-v6`; the approved HTML SHA-256 `9585e8a892bf3811e4f864f562ea41e72f6874e20ceafbb6afcb17b08ea7ff06` and original PNG SHA-256 `3c91f20ee6e96bb2b0f36c8f6baa5b1565fb625eec269974578230e841f5ece3` remain unchanged.

### Task 116: Make Recent fixed and scrollable with contiguous task rows
Refs: follow-up list; [current revised HTML Recent reference](../docs/design/references/focus-steel-approved.html); `plugins/widgets/queue.py`, task-browser projection, Home layout and existing selection tests; 06 §5.4; Tasks113–115.
- [x] Keep Recent's heading fixed above a bounded independently scrollable Recent body. Show a usable scrollbar or accurate up/down overflow cues when needed; empty/short lists do not advertise unavailable scrolling. Preserve newest-first bounded Recent and full History pagination.
- [x] Preserve one selected task identity across active/Recent sections, cyclic Up/Down, click, search/filter/history, explicit Escape clear, refresh/resize and inline detail. Reuse existing browser queries/order; no workflow writes from navigation or second default inspector.
- [x] Remove extra blank rows between processing, dependency-waiting, slot-waiting and Recent tasks. Render Recent `Done`/`Cancelled` status words in secondary grey with initial capitals; preserve meaningful task icons and Task113 active-status animation.
- [x] Exercise overflowing active/Recent lists and cross-section keyboard/mouse selection at wide, short and compact sizes with real SQLite/Textual. Review fixed heading/cues/contiguous density natively, replace obsolete gap expectations and reconcile prototype-spacing precedence; pass the full gate.

Evidence: native SVGs, full-border Chrome PNGs and source facts are under `/private/tmp/shikiru-task116-visual-review-v5` (`wide-color-120x40` and `compact-mono-36x20`). The wide captures visibly change from Recent T-33…T-30 to T-13…T-10 while the heading stays at viewport cell (2,13), the Recent body scrolls 0→40 and its scrollbar thumb moves to the lower end. The compact captures visibly change from T-33…T-30 to T-13…T-10; the Home main viewport is positioned at the Recent section and remains still while its heading stays at (2,3) and its independently scrollable body moves 0→40. Each `facts.json` records actual visible row text/IDs, viewport regions, source-file SHA-256 values, the runner timeout, the unavailable Chrome exit status and process-group cleanup. Chrome wrote each valid PNG before the owned `SubprocessRunner` process group hit its 15-second timeout; no claim is based on scroll position alone. The affected integration boundary passed 106 tests (`/private/tmp/shikiru-task116-affected-final.log`); the final-source full gate passed 1,268 tests with 15 skips at 90.20% coverage (`/private/tmp/shikiru-task116-review-corrective-full-final-green.log`). Follow-up assertions verify cross-section Up/Down keeps the selected row inside every Home viewport at wide, short-wide and 36×20 compact sizes; all 3 passed (`/private/tmp/shikiru-task116-compact-selection-followup.log`).

The final source-and-test revision `5facfdc682c26baa151e261cfaedddd1c5ce5efa` passed the complete gate: `PYTEST_ADDOPTS='-n 2 --dist=loadfile' UV_CACHE_DIR=/private/tmp/shikiru-task116-final-uv-cache PYTHONPATH=/Users/pavel/Projects/ferrum-code-dev/shikiru/src:/Users/pavel/Projects/ferrum-code-dev/shikiru/tests/fake_agent/src VIRTUAL_ENV=/Users/pavel/Projects/ferrum-code-dev/shikiru/.venv uv run --no-project --active --no-sync poe --executor uv --executor-opt no-sync=true check`. Result: 1,268 passed, 15 skipped, 90.21% coverage in 493.13 seconds; complete log: `/private/tmp/shikiru-task116-full-check-5facfdc.log`.

### Task 117: Align the resource strip and composed Project Diagnostics popup
Refs: follow-up resources; [current revised HTML `resourceMetrics`/`PROJECT DIAGNOSTICS`](../docs/design/references/focus-steel-approved.html); existing resource/Docker/fault projections and context routes; 02 §2, 06 §5.4; Tasks113–116.
- [x] Align the resource line's top rule, spacing, wrapping and Host/Shikiru/Agents groups with the reference and available cells. Preserve exact single-space dot separators, CPU normalization, host-total denominators, disjoint ownership and known/unknown/empty/stale/failed states; compact groups remain reachable without stealing selection/focus.
- [x] Make the Project Diagnostics/resource route compose existing resource, engine-lock/status, open-fault and Docker facts in the reference-aligned popup, with honest unavailable/unconfigured meanings and coherent scope/title. Preserve configured built-in/external diagnostic widget routes and public callbacks.
- [x] Home and popup use the identical cached resource observation and existing diagnostic cadences. Add no sampler, timer, provider lookup, resource attribution heuristic or SQL outside store; failures retain original timestamp/state/error and do not become zero.
- [x] Verify cached identity, diagnostic fact reachability and navigation with real SQLite/process/private-tmux boundaries. Review native wide/compact color/mono strip and popup after Task115's frame correction; pass the full gate.

Verification: the final corrected source-and-test revision passed the complete gate with 1,269 passed, 14 skipped and 90.21% coverage in 513.00 seconds. Command: `PYTEST_ADDOPTS='-n 2 --dist=loadfile' UV_CACHE_DIR=/private/tmp/shikiru-task117-review-final-uv-cache PYTHONPATH=/Users/pavel/Projects/ferrum-code-dev/shikiru/src:/Users/pavel/Projects/ferrum-code-dev/shikiru/tests/fake_agent/src VIRTUAL_ENV=/Users/pavel/Projects/ferrum-code-dev/shikiru/.venv uv run --no-project --active --no-sync poe --executor uv --executor-opt no-sync=true check`; full log: `/private/tmp/shikiru-task117-review-final-full-check-03.log`. A real Home integration test navigates from the selected task list to Resources by keyboard, opens Project Diagnostics with Enter and verifies no command write plus restored selection/focus; a real SQLite/private-tmux test removes a required diagnostic child during resize/state refresh, recomposes it, and verifies updated cached resource and fault facts. Focused result: 3 passed (`/private/tmp/shikiru-task117-review-fixes-focused-final-03.log`). Native mounted Home and Project Diagnostics captures at 120×40 and 48×20 in color and monochrome, including the compact scrolled strip, remain under `/private/tmp/shikiru-task117-native-color-final` and `/private/tmp/shikiru-task117-native-mono-final`; coordinate/fact and PNG metadata are `/private/tmp/shikiru-task117-native-review.json` and `/private/tmp/shikiru-task117-render-facts.json`. The keyboard/lifecycle corrections do not change strip or popup styling. Chrome wrote dimension-matched PNGs before the owned `SubprocessRunner` hit its 15-second timeout and cleaned its process group; its exit status is recorded as unknown, not success.

### Task 118: Remove escalation identity duplication and make Enter resolve truthful
Refs: follow-up attention; [current revised HTML decision/Enter reference](../docs/design/references/focus-steel-approved.html); `engine/escalations.py` stored headline, `ui/compact.py`, escalation widget/palette/detail, Home focus/keys; 03 §8, 06 §5.4–5; Tasks113–117.
- [x] Display a task ID once in attention, detail and resolution summaries, suppressing only its already-present leading headline prefix. Preserve stable escalation IDs, meaningful references to other tasks, stored headlines, retained packet/evidence and engine records.
- [x] Make the advertised Enter answer/resolve route work from normal Home focus and target the displayed/selected decision consistently. Preserve native input/form/selector Enter, avoid duplicate inspection and do not queue a command until explicit submission; mouse and keyboard reach the same form.
- [x] Keep actual packet questions/proposals, typed recovery actions, stale-decision validation, drafts and durable Pending/Applied/Rejected outcomes. Engine-generated packets explain the absence of an agent proposal rather than presenting unavailable alternatives as an actionable choice.
- [x] Verify startup/list-focused Enter, multiple decisions, task-ID normalization and explicit submission/cancellation against real Textual/SQLite/operator boundaries at wide/compact sizes. Inspect native attention and forms and pass the full gate; no message-wording unit tests.

Verification after Root's compact-header correction: the final source-and-test revision passed the complete check gate with 1,277 passed, 14 skipped and 90.22% coverage in 499.03 seconds. Command: `PYTEST_ADDOPTS='-n 2 --dist=loadfile' UV_CACHE_DIR=/private/tmp/shikiru-task118-identity-full-final-uv-cache PYTHONPATH=/Users/pavel/Projects/ferrum-code-dev/shikiru/src:/Users/pavel/Projects/ferrum-code-dev/shikiru/tests/fake_agent/src VIRTUAL_ENV=/Users/pavel/Projects/ferrum-code-dev/shikiru/.venv uv run --no-project --active --no-sync poe --executor uv --executor-opt no-sync=true check`; log: `/private/tmp/shikiru-task118-identity-full-check-final2.log`. The final affected dashboard app and palette integration suites passed 139 tests in 129.07 seconds; log: `/private/tmp/shikiru-task118-identity-boundaries-final.log`. The earlier 162-test Textual/SQLite/private-tmux/engine/parser boundary suite on unchanged backend code remains recorded at `/private/tmp/shikiru-task118-correction-boundaries-final2.log`.

The 120×40 Answer frame sizes to 25 rows; its evidence-error wrapper and content are both 4 rows, with the actions following within the 3-row trailing-gap bound. The wide non-answer resolution view uses the full 38-row viewport for its actual question, recovery choices and missing-packet error. At 48×20, the Answer and resolution forms keep Submit and Back inside the frame. The resolution title now fits the allocated header cells: its full caption remains at wide sizes, while compact shows both `T-1` and `E-1`; at 20×8, Answer and Resolve retain both IDs beside `Esc ×`. The mounted Home integration asserts those rendered IDs while the escalation selector and retry rung hold focus and after a 48×20→20×8 resize; the multi-decision flow asserts the selected escalation remains the header target. The Answer flow still verifies Submit, Back and Escape at 20×8. Corrected compact Resolve and tiny Answer/Resolve color and monochrome captures are under `/private/tmp/shikiru-task118-identity-{color,mono}`; prior valid wide and attention evidence remains under `/private/tmp/shikiru-task118-correction-{color,mono}` and `/private/tmp/shikiru-task118-correction-review`. The resolution fixture has actual stored typed `retry`/`cancel` engine actions and a deliberately missing packet, so its question and agent alternatives are unavailable and the evidence error remains visible; no valid packet facts were fabricated. Compact SVG/PNG dimensions match at 604×538. Each original tiny SVG has a 262×245.2 viewBox; its unchanged SVG is positioned at the top-left at original size on a 600×400 CSS-pixel Chrome canvas, producing a 600×400 PNG with the full outer frame and `Esc ×` visible. Temporary renderer HTML was removed. Chrome wrote each PNG before the owned `SubprocessRunner` reached its 15-second timeout; exit status is unknown, and each owned profile was removed. The approved reference HTML and PNG hashes remain unchanged.

### Task 119: Resolve rebase conflicts through bounded turns and classify continuation failures
Refs: follow-up retained integration evidence; 03 §6/§8, 07 §3/§5; `engine/integration.py`, `integration_turns.py`, existing conflict prompt, escalation filing; Tasks113–118.
- [x] Reproduce true unmerged-path conflicts and a non-content `rebase --continue` failure with real Git and the fake agent. Distinguish actual semantic conflicts from other Git continuation errors; preserve bounded useful failure evidence and classify recovery honestly. Do not assert the inspected project's root cause without evidence or mutate its retained worktree.
- [x] Reuse the existing Implementer conflicts, Leader consult/post-rebase verify and conflict-round bound for automatic semantic resolution. Retry follows current Git/report state instead of repeatedly treating an inappropriate stale completed report as resolved; preserve holds, cancellation/restart behavior, integration locking and the operator checkout.
- [x] On genuine unresolved/exhausted or external Git failure, keep the worktree/rebase and provide clear recorded context and applicable typed recovery actions. Preserve recorded agent EscalationRequest proposals when available, distinct from engine actions; fabricate neither alternatives nor automatic ours/theirs resolution, and do not raise budgets or discard files.
- [x] Verify successful resolution/merge, bounded unresolved outcomes, continuation failure/retry after remedy and retained evidence with meaningful real-Git/fake-agent integration. Update the workflow contract for the demonstrated correction, assess existing E3 verification under §5, and pass the full gate.

Verification: the affected real-Git/fake-agent integration, holds and restart suites passed 56 tests in 59.94 seconds. The complete gate passed with 1,280 passed, 14 skipped and 90.21% coverage in 509.72 seconds. Command: `PYTEST_ADDOPTS='-n 2 --dist=loadfile' UV_CACHE_DIR=/private/tmp/shikiru-task119-full-final2-uv-cache PYTHONPATH=/Users/pavel/Projects/ferrum-code-dev/shikiru/src:/Users/pavel/Projects/ferrum-code-dev/shikiru/tests/fake_agent/src VIRTUAL_ENV=/Users/pavel/Projects/ferrum-code-dev/shikiru/.venv uv run --no-project --active --no-sync poe --executor uv --executor-opt no-sync=true check`; log: `/private/tmp/shikiru-task119-full-check-final2.log`. The gate used a temporary `.venv/lib/python3.14/site-packages/fake_agent` symlink to the repository fake-agent source for basedpyright; the command removed it on exit and its prior absence is restored. An earlier 2026-10-04 E3 pass at `d49791c269ceef683c8a3155593d3f5ec9b5825d` predates this implementation and does not verify non-content continuation classification or remedy/retry behavior; Task 121 later ran the bounded real-agent E3 on source `dd8b49b`, recorded in the [Phase 11 second-review release record](../docs/release-readiness-2026-10-08-phase11-followup.md). The inspected retained project's original Git failure cause remains unknown; it was not mutated.

Root-review recovery corrections are committed at source/test revision `efa6e687f418da03de41dbff1fc1f6487647ad0d`. The affected real-Git/fake-agent integration, hold and engine-restart suites passed 58 tests in 60.31 seconds (`/private/tmp/shikiru-task119-corrective-affected-final.log`); the five focused pending-review, current-attempt retry, bounded restart, multi-commit conflict and non-content remedy/retry scenarios passed in 10.92 seconds (`/private/tmp/shikiru-task119-corrective-boundaries-final.log`). The final revision passed the complete gate: 1,282 passed, 14 skipped, 90.27% coverage in 507.30 seconds. Command: `PYTEST_ADDOPTS='-n 2 --dist=loadfile' UV_CACHE_DIR=/private/tmp/shikiru-task119-corrective-uv-cache PYTHONPATH=/Users/pavel/Projects/ferrum-code-dev/shikiru/src:/Users/pavel/Projects/ferrum-code-dev/shikiru/tests/fake_agent/src VIRTUAL_ENV=/Users/pavel/Projects/ferrum-code-dev/shikiru/.venv uv run --no-project --active --no-sync poe --executor uv --executor-opt no-sync=true check`; log: `/private/tmp/shikiru-task119-corrective-full-check-final2.log`. The temporary basedpyright fake-agent link was absent before and removed after the gate. The earlier E3 evidence predates this correction; Task 121 later ran the bounded E3 on the final implementation source, as recorded in the [Phase 11 second-review release record](../docs/release-readiness-2026-10-08-phase11-followup.md). The inspected retained task was not changed and its original Git failure cause remains unknown.

### Task 120: Move clear durable command feedback into the footer
Refs: follow-up feedback; [current revised HTML footer reference](../docs/design/references/focus-steel-approved.html); `dashboard/app.py` command status/footer, command-history projection and operator command routes; 02 §2, 06 §5.4–5; Tasks113–119.
- [x] Move latest-command feedback from below the task body to the actions footer's right side at usable wide widths; wrap/truncate coherently at compact widths while preserving actions, detach, Help and keyboard order. Remove obsolete separate-row height reservations and keep task list/detail space usable.
- [x] Reuse durable command-history facts to show action/target, Pending timing, Applied effect and Rejected cause clearly. Queued messages are not shown as consumed; engine-down pending commands retain honest delivery meaning, and no feedback is manufactured from navigation.
- [x] Keep full feedback/history reachable by keyboard and mouse when the summary truncates or the 20×8 footer is hidden. Preserve command result updates after popup return/restart and configured widget callbacks.
- [x] Verify real Pending→Applied and Rejected/operator flows, footer reachability and no overlap with controls across resize. Review native wide/compact feedback and pass the full gate; no wording-only or shape tests.

Task120 corrective verification: the dashboard feedback boundary suite passed 11 tests in 7.19 seconds (`/private/tmp/shikiru-task120-corrective-focused5.log`); the final user-route callback teardown variants passed 2 in 1.12 seconds (`/private/tmp/shikiru-task120-lifecycle-final.log`). FakeClock advanced 120 seconds with the project engine stopped and no SQLite version change; feedback retained the recorded UTC queue timestamp, while history retained the full timestamp, engine state and unknown original acknowledgement context. A configured external widget consumed a result as the existing JSON string. `CommandFeedbackEntryView.result` keeps its previous `str | None` type and canonical JSON serialization. The complete gate on the exact corrected source and tests passed 1,288 tests, 14 skipped, 90.26% coverage in 512.87 seconds; log: `/private/tmp/shikiru-task120-full-check-corrective-final.log`. Command: `PYTEST_ADDOPTS='-n 2 --dist=loadfile' UV_CACHE_DIR=/private/tmp/shikiru-task120-full-corrective-final-uv PYTHONPATH=/Users/pavel/Projects/ferrum-code-dev/shikiru/src:/Users/pavel/Projects/ferrum-code-dev/shikiru/tests/fake_agent/src VIRTUAL_ENV=/Users/pavel/Projects/ferrum-code-dev/shikiru/.venv uv run --no-project --active --no-sync poe --executor uv --executor-opt no-sync=true check`. The temporary basedpyright fake-agent link was absent before the run and removed afterward.

Native corrective review used actual Home renders from integration cases and real SQLite facts: Applied `Prioritize T-1` / `priority 4` in color at 120×40; Rejected `Prioritize T-404` and Pending `Stop demo` in monochrome at 48×20 and 36×20; and the 20×8 hidden-footer Home. SVG sources are `/private/tmp/shikiru-task120-corrective-native/` and `/private/tmp/shikiru-task120-corrective-native-pending/`; reviewed PNGs are in the corresponding `*-png/` directories. The wide render shows one rendered-cell gap after Help while the complete effect remains visible. Compact renders keep controls reachable and show the recorded pending timestamp or a truncated rejected cause; command history remains available at 20×8. Google Chrome 154.0.8037.98 wrote the PNGs but timed out at the 15-second subprocess limit; exits are recorded as unknown in the `*-render.log` files. The 20×8 SVG was also placed unchanged at origin `(0,0)` on a 600×400 canvas and inspected from `/private/tmp/shikiru-task120-corrective-tiny-render.log`. The renderer used temporary profiles and `SubprocessRunner` killed timed-out groups; profiles were removed. The approved HTML reference remains SHA-256 `9585e8a892bf3811e4f864f562ea41e72f6874e20ceafbb6afcb17b08ea7ff06`; the reference and PNG baseline were not changed.

Final review correction: command-history results are canonical JSON objects from `StateDashboardViews`, so the display parser now relies on that contract instead of silently replacing invalid JSON with generic feedback; `_command_applied_effect` requires its parsed result argument. The existing footer, history, queue-time, and callback-teardown integration cases passed 6 tests in 3.54 seconds (`/private/tmp/shikiru-task120-focused-final-quality.log`). The exact final source and unchanged tests passed the complete gate: 1,288 passed, 14 skipped, 90.25% coverage in 510.47 seconds; log: `/private/tmp/shikiru-task120-full-check-final-quality.log`. The temporary basedpyright fake-agent link was removed after the gate. This parser-only correction does not alter valid rendering, so the reviewed native captures above remain representative.

### Task 121: Verify the second review end to end and reconcile Phase 11 evidence
Refs: all new reported issues, follow-up specification, Tasks 113–120 and 113a; 07 §3/§5; current revised HTML comparison baseline and preserved first release record.
- [x] Run connected fake-agent/private-tmux/operator walkthroughs for responsive live motion/blink, fixed Recent scrolling/contiguous rows, stationary popup underlays and visible close controls, scoped readable titles/clock, resources/diagnostics, Enter resolution and durable right-side feedback. Include paused/stopped/restarted, explicit custom layouts/widgets and no-focus-steal guarantees.
- [x] Review matching real facts and rendered before/during/after coordinates in color/mono at 120×40, 101×35, 80×24/14, 48×20, 36×20/14 and 20×8. Compare popup/resources and new spacing/Recent/status/footer behavior to the revised Task113a HTML, preserving the earlier reference evidence; do not use prototype sample values, stale captures or injected task-detail renders as production evidence.
- [x] Run complete normal and `TERM=dumb NO_COLOR=1` quality gates and the existing macOS clean-install/attach/navigation smoke on the final implementation revision. Run warranted existing bounded real-agent verification for the changed integration behavior under §5, recording source/versions/budgets/cost and actual failures; no speculative paid scenarios or unrun platform claims.
- [x] Reconcile active design/operator/Focus/parity/config/README with delivered behavior, record every new issue's evidence-backed result and genuine remaining limits in a follow-up release record, and tick only satisfied ACs. Preserve prior acceptance, original PNG, and independently open gates 59/74h/74p. Audit/clean only owned test processes, private sockets, scratch and browser profiles; retain useful evidence and leave the user's project/resources untouched.

Task 121 was accepted by root on 2026-10-08; its verification evidence is recorded in [the second-review release record](../docs/release-readiness-2026-10-08-phase11-followup.md). On source `dd8b49bafad2701fbcd621038c70a7d14c0260f2`, the connected `test_workspace_walkthroughs.py` suite passed 5 tests in 24.75s and the selected mounted Textual/SQLite/private-tmux regression batch passed 34 in 23.95s. The actual Home popup routes passed 17 color and 17 monochrome cases. The full default Home used fresh retained SQLite facts in both modes at all eight requested dimensions; current rendered SVG, Recent scroll, popup and command-history facts are under `/private/tmp/shikiru-task121-native-facts/`, `/private/tmp/shikiru-task121-popup-captures/`, `/private/tmp/shikiru-task121-recent-scroll/` and `/private/tmp/shikiru-task121-command-history/`. A private running engine and scripted fake agent produced a valid persisted `agent_auth` packet with typed Retry/Cancel actions; its settled native Resolve capture is under `/private/tmp/shikiru-task121-engine-packet/`.

The exact-source normal full gate passed 1,288 tests, 14 skipped, 90.26% coverage in 506.44s, exit 0. Its exact command and concise result/provenance are in `/private/tmp/shikiru-task121-check-normal-result.md`; that is not a full output log. The monochrome full gate passed the same count and coverage in 512.38s; full output is `/private/tmp/shikiru-task121-check-mono.log`. The macOS clean-install/attach/navigation smoke passed at this source; result and log are `build/release-readiness/clean-install-20261008T163234Z-52979.json` and `.log`.

The bounded existing real-agent E3 node `tests/e2e/test_real_agents.py::test_real_agents_e3_plans_and_runs_dependent_tasks_in_parallel` passed once in 216.78s on clean source `dd8b49b`; artifact `build/e2e/20261008T163425Z.json`, with null cost meaning unknown. Earlier artifact `build/e2e/20261008T162210Z.json` and diagnostics remain preserved: inherited `PYTEST_ADDOPTS=-k ...` deselected four fixture-project tests, while all tasks completed and no engine fault appeared. The harness's “agent skipped a rule” classification was incorrect for this invocation error. Root authorized one corrected fresh invocation after removing inherited `PYTEST_ADDOPTS`; no further E3 run was made. See the release record for tools, environment, cost and failure provenance.

### Second-review issue coverage

| Reported issue | Implementation / verification |
|---|---|
| Periodically frozen cycling indicator; active agent status blinking | 113, 113a, 121 |
| Cyan/green tmux contrast; agent title glyphs; bottom wall-clock | 114, 121 |
| Popup moves Home one row/column; popup reference geometry; missing visible `Esc ×` | 115, 121 |
| Fixed Recent heading/scroll cues; grey capitalized terminal status; blank task-row gaps | 113a, 116, 121 |
| Resource strip and Project Diagnostics reference alignment | 117, 121 |
| Escalation repeats task ID; Enter resolve does not work | 118, 121 |
| Rebase conflict auto-resolution, repeated continuation, absent structured alternatives | 118, 119, 121 |
| Last command belongs right of actions with clearer meaning | 113a, 120, 121 |

### Third-review follow-up

Scope: [third real-project review](../docs/tui-feedback-third-review.md). Preserve the accepted Tasks 105–121 source/evidence and independently open Tasks 59/74h/74p. Execute Tasks 122–128 sequentially using a fresh GPT-6-Luna xhigh agent per task, root review after completion, and corrections by the same agent. The reported T-35 provider silence remains unexplained; failed report JSON and takeover cancellation are distinct observed facts.

### Task 122: Prevent narrow popup inputs from crashing the dashboard
Refs: third-review resize reproduction; 06 §5.4–5.5, 07 §3; `dashboard/palette.py` Message/Answer fields, shared popup layouts and mounted resize tests.
- [x] Reproduce Home → contextual actions → Message at 120×40 → 16×20 with the exact zero-step `range` exception, then fix the Shikiru layout/rendering boundary so empty and populated popup TextAreas cannot reach invalid zero-width wrapping. Cover other reachable forms sharing that boundary; no dependency patch, blanket exception suppression or speculative version upgrade.
- [x] Preserve drafts, selected task/role, native editing, Select Escape precedence and close/Submit reachability at the supported 20×8 floor, including repeated narrow/wide resizes. Below the floor degrade without crashing, without claiming full usability.
- [x] Extend meaningful mounted real SQLite/Textual resize/form regressions at the failing width and neighboring safe boundary; verify observable content/draft/focus recovery. Pass the complete gate and record exact source/proof plus owned-resource cleanup.

Accepted by root on 2026-10-09. The shared palette TextArea factory sets a named minimum width before mount, protecting all three blank fields. Mounted DashboardApp regressions cover the 17→16→17 boundary, blank forms, populated Message drafts, focus/caret recovery, role selection and Escape precedence, 20×8 controls and restoration to wide size. The complete gate passed with 1,288 passed, 14 skipped and 90.26% coverage in 512.51s; log: `/private/tmp/shikiru-task122-full-check.log`. The temporary `fake_agent` symlink was absent after the gate; task-owned UV caches and test resources were cleaned. Source/tests were unchanged after that gate; only this tracking evidence was added before the final commit.

### Task 123: Retain reliable tool outcomes without changing turn recovery
Refs: retained T-35 episode; 03 §4.2–4.3/§9, 04 §1, 06 evidence contract, 07 §3; `agents/contracts.py`, Codex parser, normalized role logs/timeline and existing monitor/takeover tests.
- [x] Add backward-compatible optional tool outcome/exit-code facts to normalized tool completion when native protocol supplies them. Map Codex command results accurately; unknown/older/other-plugin results remain unknown. Keep engine imports agent-neutral and report validation unchanged.
- [x] Show recorded success/failure and available exit code in existing role views/task evidence without adding raw command/output/prompt/path content. Older logs and third-party contracts remain readable; a failed tool never becomes a terminal `TurnFailed` solely from its exit status.
- [x] Verify native failed-command parser replay and one meaningful real SQLite/Git/private-tmux/fake-agent boundary: failed report command followed by silence remains active until configured stall or takeover, takeover holds only its task, and handback resumes the same session to a valid report. Reuse existing helpers and distinguish invalid completed reports from failed submission tools.
- [x] Preserve configured stall/nudge/rebuild/retry budgets, current state and retained user project. Document established T-35 cancellation/timing and unknown silence cause; pass the complete gate and clean owned resources.

Accepted by root on 2026-10-09. The normalized contract now retains reliable outcome and exit-code facts for Codex command results and explicit Claude `is_error` values; missing facts remain unknown. Existing role evidence exposes only the tool name, outcome and exit code. Native offline replay and the real SQLite/Git/private-tmux fake-agent scenario verify that a failed report command remains a live turn through targeted takeover and same-session handback to a valid report, without changing monitor or recovery behavior. The sanitized T-35 metadata follow-up is recorded in the third-review evidence; its silence cause remains unknown. The full gate passed with 1,290 passed, 14 skipped and 90.32% coverage in 514.91s; complete log: `/private/tmp/shikiru-task123-full-check-green.log`. Focused checks passed 44 with 14 skipped; log: `/private/tmp/shikiru-task123-focused-check.log`. Source/test provenance covers 17 files at manifest SHA256 `16e67b5fe7ed9a28d28e6dd37d08541e51709f499cab37eb28fd0935c5cedb43`, recorded in `/private/tmp/shikiru-task123-source-provenance.json`; after the full gate, only documentation/tracking evidence changed. The temporary `fake_agent` site-packages link and task UV cache were removed. Host review found no new Task123 processes or private fixtures; baseline PID 49080 had ended, and no process was terminated. A nonfatal coverage warning named `.coverage.Pavels-Mac-mini_local.pid41628.X1c1vpUx`; it was left untouched because its provenance was unknown.

### Task 124: Revise the HTML reference for the third review
Refs: third-review presentation contract; current `docs/design/references/focus-steel-approved.html`; Tasks 113a/122–123 and planned 125–127.
- [x] Update the existing reference to demonstrate indexed task window labels, dark full tmux chrome, wall time/date with seconds/full year/no hostname, and distinguishable glyph-free pane-title parts. Preserve task-name text, separate task windows and no-focus-steal semantics.
- [x] Demonstrate coherent popup header/body/control/dropdown styling, visible header `Esc ×`, separating rule, conditional narrow scrollbar and advertised working navigation shortcuts. Initial content-appropriate frames remain stationary across pagination/filter/update; compact/tiny navigation and draft/native-select semantics remain coherent in the prototype.
- [x] Preserve the original color palette and historical PNG, keep prototype facts illustrative, and retain the prior HTML in Git. Review browser-rendered wide/compact color/mono interactions and geometry; record proof and pass the complete gate before committing the reference/design changes.

Accepted by root on 2026-10-09. The HTML reference now shows `0:dashboard` and the configured task window as `1:T-43 Fix login redirect`, with explicit navigation to the separate task window. Popup headers, rules, fixed controls, native dropdowns and page shortcuts share one treatment; the prototype’s scroll-linked track is one terminal cell wide and appears only for overflowing content. Source-matched browser evidence covers wide/compact color and monochrome, 36 screenshots, full/short/empty History pages, stationary popup frames and dashboard underlays, native Select Escape, TextArea editing, and resize restoration. The measured track is 7.21875 px against a 7.22461 px terminal cell; short and empty History pages have no track. Facts and provenance are in `/private/tmp/shikiru-task124-scrollbar-browser-facts.json` and `/private/tmp/shikiru-task124-scrollbar-provenance.json`; HTML and decoded-prototype SHA256 values are `56cecc218414f86956a1b7c21e4549b0a6d09db1e5dac3287803917dc40d29bd` and `90ddbec5e16c1c1eff3cb2aa1cd51f789e4336e1a40957e16e9cdc7812f5c062`. Native headless Chrome on this macOS host suppressed its overlay thumb, so the reference renders its own scroll-linked indicator; the failed CSS-only attempt is preserved under `/private/tmp/shikiru-task124-native-overlay-diagnosis`. The Tiny preview measures 240×150 px (31×7 cells) and is illustrative only; Task128 later verified the product’s 20×8 floor. The original palette declarations and historical PNG are unchanged (PNG SHA256 `3c91f20ee6e96bb2b0f36c8f6baa5b1565fb625eec269974578230e841f5ece3`). The full gate passed with 1,290 passed, 14 skipped and 90.31% coverage in 511.54s; log: `/private/tmp/shikiru-task124-scrollbar-gate.log`. At Task124 acceptance, Tasks125–128 product behavior and native 20×8 verification remained open; each was accepted after its subsequent review.

### Task 125: Style the complete project tmux chrome
Refs: third-review tmux style inheritance and corrected option-scope investigation; revised Task124 HTML; 05 dashboard titles, 06 §5.3/§6.1, 07 §3; `tmux/decorator.py`, session/layout wiring, title rendering/defaults and private attached-client tests. Evidence: [Task 125 tmux chrome](../docs/task125-tmux-chrome-evidence-2026-10-09.md).
- [x] Set missing session status and per-window status styles locally, retaining the existing correct window scope for task formats and adding coherent Home formats. Use the approved dark palette for the full line and blank fill, active/inactive labels and status-left/right; no green inherited background, hostname, global-option mutation or other-session change.
- [x] Display actual window index before each existing task title while retaining `0:dashboard`, configured task-name templates and explicit custom layouts. Keep seconds and four-digit-year wall time/date, without engine-uptime substitution or additional update loop.
- [x] Distinguish pane role, agent/model, iteration, context and ownership/state parts using existing palette tokens and truthful semantic colors. Keep words/glyph-free shipped defaults, unchanged title values and explicit custom templates without heuristic text parsing.
- [x] Verify real attached private-tmux rendered line/SGR, including blank fill and inherited-green baseline, window indices, clock/date/no-host, colored pane parts, two-session/global isolation, custom templates and no focus steal. Stored option reads alone are insufficient. Pass the complete gate and remove owned processes/sockets.

### Task 126: Unify popup styling and actual keyboard controls
Refs: third-review popup contract and native Select proof; revised Task124 HTML; 06 §5.4–5.5/§6.1, 07 §3; app/detail/palette popup routes and existing integration matrix.
- [x] Share the repeated popup presentation contract across actual Help/Actions/Views/History/Usage/Evidence/Diagnostics/Answer/Resolve and configured-widget routes: consistent title case/weight, body/secondary text, header rule, button/dropdown focus/disabled styling and visible `Esc ×`, preserving the palette and stationary underlay.
- [x] Use a one-column vertical scrollbar only when the popup body overflows. Render straight, coherent native Select left/right borders in collapsed, focused and expanded states; keep native selection behavior and Escape precedence.
- [x] Advertise and implement useful page/control shortcuts with actual action semantics, disabled boundaries and coherent labels. Preserve Input/TextArea/Select editing, drafts, task/role context and explicit form submission; no shortcut consumes entered text or bypasses validation.
- [x] Extend the existing mounted real SQLite/Textual route tests for keyboard/mouse equivalence, pagination boundaries and native selector/input behavior. Review settled wide/compact color/mono renders against Task124, including focused/expanded dropdowns and conditional scrollbars; pass the complete gate.

### Task 127: Keep popup frames stable through content changes
Refs: third-review History page1/page2 and tiny-controls reproductions; revised Task124 HTML; 06 popup contract, 07 §3; shared popup sizing, mounted routes and paginated body projections.
- [x] Choose content-appropriate initial popup dimensions, then preserve outer frame size/origin through pagination, filters, live content and dropdown expansion. Reuse a small shared sizing contract where necessary; no duplicated per-page sizing or speculative UI framework.
- [x] Keep header/title/`Esc ×` and applicable controls fixed outside the scrolling body and inside the visible frame, including History navigation at 20×8. Constrain on terminal resize and regain the initial preferred dimensions when space returns, while preserving drafts, selection, reading position and originating context.
- [x] Verify actual rendered frame/header/control and Home-underlay coordinates before/after full→short/empty pages and updates with real SQLite/Textual. Cover representative natural Help/Views, History and form routes, wide/compact/20×8, and mounted/standalone compatibility; assertions must test usability and stability rather than constants alone.
- [x] Review color/mono native renders using matching facts against Task124, pass the complete gate and retain honest proof/cleanup evidence.

Accepted by root on 2026-10-09. The shared `PopupFrame` captures a purpose-appropriate preference after meaningful initial content is ready, preserves it through pages, filters, live updates and dropdown changes, and clamps/restores it across viewport resize. Fixed popup chrome remains inside the visible frame and outside the scrolling body. `widget_is_attached` guards deferred sizing/readiness callbacks after close; the real SQLite/Textual lifecycle route replays those callbacks and verifies Home navigation and retained task identity. The full gate passed with 1,293 passed, 14 skipped and 90.45% coverage in 568.66s. The 426 retained native artifacts are attributed to the pre-guard source; attached-route rendering is unchanged by the detached-only guard. Full facts, source hashes, failed attempts and cleanup are recorded in [Task 127 evidence](../docs/task127-popup-frame-evidence-2026-10-09.md). Task128 was still open at Task127 acceptance and was accepted on 2026-10-09.

### Task 128: Verify the third review and reconcile Phase 11 evidence
Refs: every third-review issue, Tasks122–127; 07 §3/§5; updated HTML and accepted second-review record.
- [x] Run connected fake-agent/private-tmux/operator walkthroughs for failed-tool evidence and unchanged recovery, safe resizing, scoped indexed/colored tmux chrome, popup shortcuts/native Select and stable geometry. Include configured/custom widgets/layouts, paused/stopped/restarted and no-focus-steal cases.
- [x] Review source-matched native color/mono Home, popup and attached-tmux proof at wide, compact and 20×8 plus the 16-column failing boundary. Verify actual frame/underlay/control coordinates across pagination/updates and restored viewport, conditional one-column scrollbars and truthful retained tool facts against Task124 HTML.
- [x] Run complete normal and `TERM=dumb NO_COLOR=1` quality gates and existing macOS clean-install/attach/navigation smoke on the final implementation. Assess bounded real-agent verification under 07 §5 for actual changed execution behavior only; record source/versions/limits/failures and make no unrun platform or provider-cause claims.
- [x] Reconcile active design/operator/Focus/parity/README and issue outcomes in a new third-review release record, preserving accepted prior evidence/PNG and open59/74h/74p. After root review, audit/clean only owned processes/private sockets/cache/profiles and leave the retained user project/resources untouched.

Accepted by root on 2026-10-09. The production TaskFlow→TaskTitles→PaneDecorator route used real SQLite, Git, a fake agent and isolated tmux; connected color and monochrome observations are recorded separately. Final-source native captures, geometry, three clean-install smokes, full normal/monochrome gates, retained failed attempts, platform/provider limits and independent visual/cleanup reviews are reconciled in the [third-review release record](../docs/release-readiness-2026-10-09-phase11-third-review.md). The 484-file source/test tree remains byte-identical at SHA-256 `9c2a766483af3bc1804e6c30ee51fd8244c9bba6f2824f41537a2460121d955b`; root's independent source snapshot is `/private/tmp/shikiru-task128-root-accepted-source.json`.

### Fourth-review follow-up

Scope: [fourth real-project review](../docs/tui-feedback-fourth-review.md), planned from ten read-only analyses. Preserve the accepted Tasks 105–128 source/evidence and independently open Tasks 59/74h/74p; where the specification lists superseded behaviour, replace the old expectation and its obsolete tests rather than keeping both. Execute Tasks 129–140 sequentially with a fresh agent per task, root review and corrections by that agent. **The TUI is the source of truth:** the HTML reference is revised in Task 139, after the product changes, to match the rendered TUI. Analysis was static; reproduce each reported behaviour natively before changing it.

### Task 129: Popups open with useful focus and share the Tasks-list scrollbar colours
Refs: fourth-review popups; `ui/popup.py` `.popup-body` scrollbar rules, `plugins/widgets/queue.py` list scrollbar, `ui/theme.py`; popup screens in `dashboard/app.py` (Help, Views, History, context widgets), `escalations.py` PacketScreen, `dialogue.py`, `palette.py` standalone Actions; 06 §5.4–5.5; Tasks 126–127.
- [x] Reproduce which popups initially focus `Esc ×` (the first focusable control under `AUTO_FOCUS`). Give each an explicit initial target: Help and read-only bodies their scroller, Views its list, History its filter, context widgets their content, Decision evidence and Dialogue text their scrollers, standalone Actions its Find input. Menus, forms and task-detail routes that already focus a useful control are unchanged.
- [x] Escape closes from every initial focus; Tab still reaches `Esc ×` and Enter/click closes it; read-only bodies scroll without changing the Task 127 frame; drafts, selection and originating context are preserved.
- [x] Name the Tasks-list scrollbar colours (thumb, hover, active, track) once in `ui/theme.py` and use them for the Tasks list, Recent list and every popup body; the scrollbar still appears only on overflow and keeps its one-column size.
- [x] Table-driven mounted real SQLite/Textual test per popup route (focused selector, Escape returns Home) and one computed-style comparison of popup and Tasks-list scrollbar colours. Replace Task 126 focused-close expectations; review settled color/mono renders.

Proof: the unchanged code opened all seven popup routes (Help, Views, command history, Usage details, standalone palette, decision packet, dialogue text) on `Esc ×` and gave popup bodies a different scrollbar from the Tasks list; the table cases in `tests/integration/test_dashboard_popup_focus.py` (real SQLite/Textual) now start each route on its target, return to the origin on Escape, let Tab reach `Esc ×` for Enter to close, and scroll Help from its initial focus. Computed styles show the Tasks, Recent, Help and Views scrollbars identical (one column, thumb, hover, active and track from `ui/theme.py`), and live Help and Views exports in colour and `NO_COLOR` were reviewed visually. `uv run poe check` passed: 1,334 passed, 14 skipped, 90.63% coverage.

### Task 130: Make Tab traverse only visible, fully highlighted dashboard stops
Refs: fourth-review focus; `plugins/widgets/usage.py`, `resources.py`, `escalations.py`, `dashboard/app.py` (`_HomeAttentionStatus`, `_HomeFooter`, scroll containers), `detail.py` body; 06 §5.4; Tasks 107, 112, 117, 118.
- [x] Reproduce, with native exports, the partial highlight of multi-row Usage and Resources and the invisible Tab stops (scroll containers `#focus-root`, `#focus-readouts`, `#escalation-panels`, `#task-detail-body`, the footer grid, inert Usage placements, the quiet escalation bar).
- [x] Skip inert or invisible widgets through `allow_focus` / non-focusable containers while keeping their explicit routes (Views › Attention, Enter on Resources/Usage, mouse and Page-key scrolling at 20×8). Preserve startup focus on the task list.
- [x] Give each focusable readout one shared focus style covering every wrapped row at wide, compact and 20×8 sizes; focusing never scrolls, selects or writes a command (Task 117).
- [x] When at least one decision exists, render the bar's decision in the armed style at startup (no task selected: the first pending escalation) while the task list keeps focus, so the `Enter …` hint is meaningful. Update the Tab-chain and `attention.focus()` tests that relied on the old chain; add a Tab sweep asserting every stop is displayed, non-empty and visibly styled, plus a multi-row focus render check.

Proof: reproduced the invisible Tab stops in the unchanged app (root, readouts, attention scroller, footer grid, quiet bar), then Tab at 120×40, 48×20 and 20×8 visits only displayed, non-empty, highlighted stops; focused Usage/Resources fill every wrapped row (not the rule above Resources) with the selected surface and the decision bar is armed at startup with the task list focused (`tests/integration/test_dashboard_tab_stops.py`). `uv run poe check` green.

### Task 131: Escalation bar follows the selected task and reads clearly
Refs: fourth-review escalations; `EscalationsPanel` (`_refresh`, selection state), `_select_task_context`, `ui/compact.py` `_attention_group_status`, `engine/escalations.py` headline, `domain/escalation.py` `EscalationReason`, `dashboard/views.py` and `contract.py` escalation view; 03 §8, 06 §5.4–5; Tasks 107, 116, 118, 130.
- [x] The bar follows the selected task (click, cyclic Up/Down, search result, Recent row). A task with an escalation shows its first, or keeps the one already chosen with Left/Right, and the `Enter …` hint applies to it. A task without one shows `T-12<white bold> is not escalated<white>` with the hint falling back to `Ctrl+P actions`, plus a grey ` · N decisions on other tasks` only when N > 0. With no selection (startup, Esc clear) the bar shows the first pending escalation in the armed style, or the existing `No decisions needed`. Engine-fault, stall and engine-status rows keep precedence. No focus move, no command, no feedback loop with the bar's own click/Enter paths; multi-decision flows and Enter targets stay correct (Task 118).
- [x] The escalation number and task number are bold; other parts keep their style. Carry the persisted `EscalationReason` through the dashboard view and show a total domain label (`Decision required`, `Integration blocked`, …) instead of the raw value, in the bar and the expanded cards; never parse or rewrite the stored headline, packet or evidence. Reuse existing readable labels (for example `detail.py`) and decide the step word (`consult`) by an existing label, leaving it unchanged if none exists.
- [x] One table test proving every `EscalationReason` has a label; real SQLite/Textual tests with two escalated tasks plus a plain task asserting selection sync, the `is not escalated` text and other-decisions suffix, the no-selection default, Esc clear, unchanged focus, Enter opening the matching form, and bold/readable rendering. Review color/mono renders.

Proof: with two escalated tasks and a plain task, Up/Down/click move the bar between the task's first decision (armed, `Enter …`) and `T-n is not escalated · 2 decisions on other tasks` with `Ctrl+P actions`; Escape returns to the first pending decision, focus stays on the task list, navigation queues no command and Enter opens only the followed escalation's form. `E-n`/`T-n` render bold and the persisted reason shows as `Decision required`/`Integration blocked` (domain label table, total over `EscalationReason`) at 120x40 and 48x20; stored headlines are untouched. `uv run poe check` green.

### Task 132: Align the token usage and resource bars with the operator templates
Refs: fourth-review bars; `ui/compact.py` `_home_project_usage_text`, `_process_resource_text`, `_host_resource_text`, `ResourceMetric.render`, `plugins/widgets/usage.py`, `resources.py`, `dashboard/diagnostics.py`; 05 §3, 06 §5.4; Tasks 106, 110, 111, 117.
- [x] Token bar renders `62<white bold> sessions<grey> · 62.5M<cyan bold> tokens<grey>[ · $4.28<green bold> API<grey>]`. The API segment appears only with API-billed cost data (recorded `$0.00` counts; API-equivalent estimates and subscription/unknown modes do not). A known token total is always bold cyan; partial coverage keeps an honest grey qualifier after the template, and an unknown total renders grey (never as a bold cyan measurement); per-mode detail stays in Project Usage. Wrap at ` · ` boundaries.
- [x] Resource bar renders `Host<white bold> CPU<white> 5%<cyan> RAM<white> 12.3/24.0 GiB<cyan> · Shikiru<white bold> CPU<white> 1%<cyan> RSS<white> 0.4 GiB<cyan> · Agents<white bold> CPU<white> 2%<cyan> RSS<white> 1.2 GiB<cyan>`; Host keeps `RAM` used/total, Shikiru and Agents are labelled `RSS` with no denominator; an empty agent tree reads `Agents<white bold> process tree is empty<grey>`. Unavailable, stale (amber age) and failed states keep their meanings and never become zero; groups wrap whole. This supersedes the Task 110/117 per-group denominator and `RAM` label for process groups; Project Diagnostics keeps its explanation and totals, adjusted to say RSS.
- [x] Update the existing assertions on `API billed`, `4/4 sessions` and the old empty/denominator wording; add mounted real SQLite / fake-agent cases asserting style spans for known, unknown-token, `$0.00`, estimate-only, empty-tree, stale and failed states. Review color/mono at wide, compact and 20×8.

Proof: the unchanged code rendered `62.5M tokens · 62/62 sessions · API billed $4.28` and `RAM 0.4/24.0 GiB` / `process tree empty` (reproduced with `ResourceMetric` and the Home usage bar); natively at 120x40, 48x20 and 20x8 (Textual `render_lines` with a per-character style map, and NO_COLOR) the bars follow the templates, wrap only between groups at 48x20, and keep every value whole at 20x8, where an over-wide qualifier or Host group wraps by words inside the group. `tests/integration/test_dashboard_bar_templates.py` mounts the real widgets over SQLite (billed, `$0.00`, estimate-only, subscription-only, unknown tokens, partial, empty) and fake resource samples (populated, empty tree, unavailable, stale, failed) and asserts each character's text and style; unbolding the token value fails six cases. `uv run poe check` is green on the final tree (1365 passed, 14 skipped, 90.69 % coverage).

### Task 133: Stabilise task-list geometry and typography
Refs: fourth-review tasks list; `plugins/widgets/queue.py` row CSS/text (`_focus_row_text`, `_focus_row_marker`, headings, selection `.-highlight` border), 06 §5.4; Tasks 107, 113, 116.
- [x] Reserve a leading blank column on every active and Recent row and put one blank row between task rows; show selection by background/emphasis only so selecting or deselecting never changes any row's text column or row position (replacing the border that shifts text, and the narrow-width outline workaround). Supersedes Task 116's contiguous-row rule.
- [x] Add the blank line after the `Recent` heading as after `TASKS`, and give `Recent` the same colour as `TASKS`.
- [x] Render the task number bold with its title regular, in active and Recent rows and under highlight and `NO_COLOR`. Recent status words (`Done`, `Cancelled`) take their glyph's colour; preserve the Task 113 active-status animation.
- [x] Confirm natively whether `TASKS · Decision first` is still rendered and remove it if so (Task 106). Update the obsolete gap/grey-status expectations; add real SQLite/Textual tests comparing row text coordinates selected vs unselected, blank-row spacing, heading colours and status/glyph colours at wide, short and compact sizes; review color/mono renders.
- Proof: new real SQLite/Textual tests show row text offsets identical selected vs unselected in both lists at 100x35, 48x20 and 20x8 (failing before the change), one blank row between rows, the Recent heading colour equal to TASKS with a blank line after, bold id with regular title, and status words coloured as their glyph. `TASKS · Decision first` survived only for a missing selection in the default view and is removed; native renders reviewed at 60x30, 48x20 and 20x8.

### Task 134: Show Recent as a bottom-aligned three-task window with Later/Earlier
Refs: fourth-review Recent; `plugins/widgets/queue.py` (`_load_pages`, `RECENT_TASK_LIMIT`, `RECENT_BODY_MAX_ROWS`, `_sync_focus_task_list_height`), `DashboardViews.task_browser_pages`, `defaults.toml`, Home CSS; 06 §5.4; Tasks 107, 116, 133.
- [x] Recent holds at most the 30 most recent terminal tasks and shows a window of 3 of them (both numbers `defaults.toml` tunables, defaults 30 and 3; validated so the cap is at least the window), newest first, from the existing History ordering and `task_browser_pages` with a window offset; no new SQL. The independently scrollable body, scrollbar and scroll cues of Task 116 are removed.
- [x] `↑ Later` pages toward newer tasks and `↓ Earlier` toward older ones, by one window, in the heading row; each is disabled at its boundary and the pair is hidden when there are three or fewer Recent tasks; the oldest page may hold fewer than three when the cap is not a multiple of the window. The cap applies to Recent only: full History pagination stays unlimited. No PageUp/PageDown: active and Recent tasks stay one common selection list driven by Up/Down. Down from the last visible Recent row selects the next older task and shows the Earlier page; Up from the first Recent row of an older page shows the Later page; Up from the newest page's first row goes to the last active task; Down from the oldest Recent task wraps to the first active task. The labels are indicators and mouse click targets; a click pages without changing the selection.
- [x] A new completion, search or filter change resets to the newest window and the offset clamps if the count shrinks; the selected task keeps its identity and inspector when a mouse click pages it out of the window; full History pagination is unchanged; the active-list "more" arithmetic still uses the total terminal count, not the Recent cap.
- [x] Bottom-align the Recent block (fixed heading and three row slots; blank space above the heading when fewer than three tasks) in the wide layout so an active list of any length never pushes it away; compact layouts keep natural flow; the selected active row stays visible down to 20×8. Replace the obsolete Task 116 scroll tests with real SQLite/Textual cases (seven terminal tasks paged by Up/Down across window edges in both directions; more than 30 terminal tasks showing exactly the newest 30, wrap at the 30th, a non-multiple configured cap giving a short last page, History still listing all; boundaries, reset on completion, selection across mouse paging, fewer than three, bottom alignment at 120×40/short/36×20, keyboard/mouse equivalence, no command writes); review color/mono renders.
- Proof: seven terminal tasks paged by Up/Down across both window edges and back, 33 terminal tasks show exactly the newest 30 and wrap at the 30th (History still counts all 33), a limit of 7 gives a one-task last page under a stable heading, the pager hides at three or fewer and dims at its boundaries, clicking pages without changing the selected task, and a new completion, search or filter resets to the newest window; the wide block stays at the same rows with 2 or 14 active tasks at 120x40 and 80x24, and the selected active row stays visible at 20x8 (native text renders reviewed at 120x40, 80x24/14, 48x20, 36x20, 20x8). `uv run poe check` is green (1414 passed, 90.70 % coverage).

### Task 135: Right-align the latest command in the actions bar
Refs: fourth-review actions bar; `dashboard/app.py` footer CSS (`#focus-footer Button` vs `#workspace-command-status`) and wide/medium layouts; 06 §5.4; Task 120.
- [ ] Reproduce natively that the status text sits left beside Help; fix the style precedence so the latest command's last character ends on the footer's right edge and follows it as the width grows. Action order, detach, Help, the 54–72 and sub-48 fallbacks and the hidden 20×8 footer are unchanged; decide and state the 54–72 column row-3 alignment (right).
- [ ] Extend the Task 120 footer test with right-edge assertions across a width sweep (73, 100, 120 and wider) for Pending, Applied and Rejected feedback; review color/mono renders.

### Task 136: Colour agent context by usage level
Refs: fourth-review tmux; `engine/titles.py` `TaskTitles._refresh_pane`, `tmux/decorator.py` styled title, `ui/tokens.py` palette tokens, `domain/runs.py` `context_title`, `config/defaults.toml` `[dashboard.titles]` and its settings model; 05 §3, 06 §5.3; Tasks 114, 125.
- [ ] Add pure domain classification of `used_pct` into ok / warn / alert with `defaults.toml` thresholds (`40`, `60`, validated warn < alert), applied to the displayed rounded value so 40 and 60 belong to the higher level. Below 40 is green (sage), 40 to below 60 yellow (amber), 60 and above red (dusty rose), reusing existing palette tokens and the title-row contrast already accepted. Unreported context stays grey with the existing marker. Leave `gauge_color` and the dashboard gauges (70/90) unchanged and document both threshold sets.
- [ ] Unit table for the classifier at 0, 39, 40, 59, 60, 100; integration through the fake agent `UsageReported` events with FakeClock and a real attached private tmux asserting the rendered SGR of the context cell (stored option reads alone are insufficient), plus the unknown case.

### Task 137: Preserve agent pane titles across session restart
Refs: fourth-review tmux; `engine/layout_guard.py` (`_create`, `_restore_missing_panes`, `ensure`/`restore`), `engine/titles.py` `TaskTitles.refresh`, `tmux/client.py`, `bootstrap.py` wiring, `operator/lifecycle.py`; 06 §5.1/§5.3; Tasks 109, 114, 125.
- [ ] Reproduce with a private tmux server that killing the Shikiru session and starting it again leaves recreated agent panes showing the tmux default title (hostname) until the next transition or usage event; the cause is *unconfirmed* (statically: new panes carry no `@shk_title_format`).
- [ ] Re-apply titles from stored run/task state whenever panes are created or individually restored, before the first attach and without a new usage event, by injecting the existing title refresh into the layout guard (constructor injection, wiring in `bootstrap.py`). Refresh after pane ids are stored, outside lock hazards, inside the per-task fault isolation; no new persisted title state.
- [ ] Real tmux / SQLite / fake-agent integration: kill the session then run one engine tick (`wait_until`, never sleep), and separately kill a single pane; assert the rendered border shows role, agent/model, iteration and context with no hostname and no stale `operator` flag. Pass the complete gate and remove owned sockets.

### Task 138: Document that takeover is not restored after respawn
Refs: fourth-review tmux; `engine/takeover.py`, `engine/layout_guard.py` (`_release_lost_takeovers`, `_restore_view_panes`), 03 §9 "Lost interactive pane", 06 §5.1/§5.3/§227, 04, `README.md`.
- [ ] State in 03 §9, 06 §5.1 and §5.3, 04 (interactive sessions) and the README take over/hand back line that a taken-over pane is not restored as taken over after a respawn (session or pane killed, host reboot, the Operator leaving the TUI): the engine records `takeover_lost`, hands the role back, releases the `intervention` hold unless another role or `pause` holds the task, shows the read-only view and resumes the same agent session; the Operator takes over again. Keep the session-resume claim to what is verified per agent. Do not edit dated release records.
- [ ] Extend the existing lost-takeover layout test to assert the run is handed back, `takeover_lost` is recorded, the hold is cleared and the rendered pane title carries no operator flag; no new test beyond that.

### Task 139: Revise the HTML reference to match the TUI
Refs: [HTML reference](../docs/design/references/focus-steel-approved.html), Tasks 129–138 as delivered (the TUI is the source); fourth-review specification; Tasks 113a, 124.
- [ ] Update the prototype from the delivered TUI renders, not from this plan: focus highlight on whole readout widgets and skipped inert stops, escalation bar following the selected task (including `T-n is not escalated`), bold `E-n`/`T-n` with readable reasons, the token and resource templates (Host `RAM` used/total, Shikiru/Agents `RSS` with no denominator, `process tree is empty`), right-aligned latest command, reserved leading column with blank rows and non-shifting selection, `Recent` heading colour and blank line, bold task numbers, glyph-coloured status words with the TUI's Cancelled glyph, and the three-task bottom-aligned Recent with `↑ Later` / `↓ Earlier` replacing the scroll cues and their scroll state, and Up/Down moving through the common list across window edges.
- [ ] Reflect popup initial focus (body/list/filter rather than `Esc ×`, with a body fallback for read-only modals), the Tasks-list scrollbar colours adopted from `ui/theme.py`, and context-level colours (green below 40 %, yellow 40–59 %, red 60 % and above) with example panes at each level in the tmux window mock.
- [ ] Keep the original palette declarations and historical PNG, keep prototype values illustrative, retain the prior HTML in Git, and review browser-rendered wide/compact color/mono states against matching native TUI captures. Update the active design/operator/Focus docs that quote superseded behaviour (06 §5.3–5.5, `tui-focus.md`, `tui-feedback-followup.md` where it states now-superseded rules) and pass the complete gate.

### Task 140: Verify the fourth review and reconcile Phase 11 evidence
Refs: every fourth-review issue, Tasks 129–139; 07 §3/§5; accepted third-review record.
- [ ] Run connected fake-agent / private-tmux / operator walkthroughs for focus traversal, escalation sync, bars, task-list geometry and Recent paging, popup focus, footer alignment, context colours, title preservation after session kill, and takeover loss, including paused/stopped/restarted engines, custom layouts/widgets and no-focus-steal.
- [ ] Review source-matched native color/mono Home, popup and attached-tmux proof at 120×40, 101×35, 80×24, 48×20, 36×20 and 20×8 against the revised HTML; compare actual rendered coordinates and styles, not prototype samples.
- [ ] Run complete normal and `TERM=dumb NO_COLOR=1` gates and the existing macOS clean-install/attach/navigation smoke on the final implementation; assess bounded real-agent verification under 07 §5 only for changed execution behaviour (none is expected); record source, limits and failures with no unrun platform claims.
- [ ] Reconcile active design/operator/Focus/parity/README and each issue's outcome in a new fourth-review release record, preserving accepted prior evidence, the original PNG and open gates 59/74h/74p; clean only owned processes, sockets, caches and renderer profiles.

### Fourth-review issue coverage

| Reported issue | Implementation / verification |
|---|---|
| Partial focus highlight; invisible Tab stops; escalation bar highlighted by default | 130, 140 |
| Popup scrollbar colour | 129, 139, 140 |
| Esc selected by default in popups | 129, 139, 140 |
| Token usage bar template | 132, 139, 140 |
| Resource bar template | 132, 139, 140 |
| Escalation numbers bold; readable reasons; bar follows selected task | 131, 139, 140 |
| Blank rows, reserved column, non-shifting selection | 133, 139, 140 |
| `Recent` heading blank line and colour; bold task numbers; coloured status | 133, 139, 140 |
| Recent bottom-aligned, three tasks, Later/Earlier | 134, 139, 140 |
| Last command right-aligned | 135, 140 |
| Takeover not restored after respawn (documentation) | 138, 140 |
| Context colour by level | 136, 139, 140 |
| Agent pane titles preserved across session restart | 137, 140 |
