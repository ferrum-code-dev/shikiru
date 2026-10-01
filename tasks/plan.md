# Shikiru Implementation Plan

Source of truth: [`docs/design`](../docs/design/README.md). Progress: [`todo.md`](todo.md).

Phase 8 target specification: [Operator Workspace](../docs/tui-workspace.md). Its tasks update the normative design as each slice ships.

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
- [ ] An offline test builds a real linked `git worktree` and asserts the Codex argv for a first and a resumed turn carries its gitdir, the common directory and the run directory as writable roots; the test-only `_worktree_git_dirs` fallback in production code is removed or justified.
- [ ] A test that fails when `end_orphans` runs a second time (a reconcile retried after a fault must not end a turn that started after the first attempt); the vacuous test is replaced.
- [ ] A turn whose leader exits while a descendant still holds the stdout pipe ends within the bounded drain time, not the stall timeout, or the limitation is written into 03 §7 with the measured bound (decide by cost: the simplest fix wins).
- Tests: mutation check recorded in the report for each (revert the code, see the new test fail).

### Task 74n: Documentation and evidence corrections
Refs: reviewer findings of the conventions review.
- [ ] 07 §1 states what the code does (SQLite is called synchronously from the loop with a 5 s busy timeout) or the code moves blocking work to `to_thread`; the simplest consistent choice, with the reasoning. 05 §1's constants table lists the real notify timeout (`notify.timeout_seconds`); 02 §51 names `run_dashboard`; 03 §4.2's Linux claim says "designed for, verified on macOS only".
- [ ] tmux ≥ 3.4 and git ≥ 2.45: `doctor` compares the installed versions with the documented floors and fails below them, or the README and 07 §1 restate the floors as "verified on tmux 3.7c, git 2.55.0" (prefer the doctor check if it is a few lines).
- [ ] `docs/release-readiness-2026-10-01-phase7c.md` and `docs/test-audit-2026-10-01.md` no longer cite the dangling `05c11fb` and no longer call the test inflation "fixed" (974 to 943 against 779 before; the older files were not audited); F21 (Linux) and the reviewer-found defects not fixed in Phase 7d are listed as limitations.
- [ ] The duplicated or config-worthy constants and the git and cache-copy timeouts are handled by Tasks 74r and 74t.
- Tests: as applicable; `poe check` and `poe comments` green.

### Task 74o: Test-value audit of the older suite
Refs: AGENTS.md (Test value), 07 §3. Reviewer sample: `test_prompts_renderer.py` has 47 tests (9 templates x 3 payloads re-test `domain.fencing.fence`, 9 test Jinja `StrictUndefined`, 9 enumerate `PromptName`), `test_ui_glyphs.py` 42 (35 one per glyph), about 41 tests of test tooling, three duplicated `_StubServices` stubs, two duplicated fake-distribution classes, a hand-rolled "advance FakeClock while polling" closure about 16 times, wording asserts through `match=`, an index-name assertion, a monkeypatched own function.
- [ ] The audit of Task 74i is extended to the files it did not cover: every file above is reduced to one table per rule, framework and enumeration tests are removed, the duplicated stubs and the FakeClock polling closure become shared helpers under `tests/support`, wording and index-name assertions assert behaviour instead, and `monkeypatch.setattr` of an own function is replaced by the real boundary or removed.
- [ ] A test is removed only when another test still fails for the same reverted fix (mutation or named covering test); coverage stays at least 80% and `poe check` is green. The result (collected tests and coverage before and after) extends `docs/test-audit-2026-10-01.md`.

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
- [ ] A fault in `_prepare` (reconcile, orphan reaping, layout restore) at engine start never lets a pending command run a flow before reconcile succeeded: commands wait until the engine start sequence completed once, and `end_orphans` can never read the `turn.pid` of a turn the engine itself started; one table test over the start-up fault points.
- [ ] A purely cosmetic tmux failure (title, decoration, status line, timeout included, `CommandTimeoutError` as well as `CommandFailed`) never kills a running turn, never escalates and never fails a command; it is logged once per streak. A `take_over` whose pane respawn fails after the commit is reported to the operator as what it is (taken over, pane could not be created, how to recover) and not as "rejected" while the hold stays; a failed respawn is retried or recoverable through the lost-pane path.
- [ ] `_migrate` reads `user_version` and applies the migration inside one immediate transaction, so two processes opening an old database (engine and CLI after an upgrade) never double-apply.
- [ ] Every helper subprocess (git, gh, tmux, doctor probes) runs in its own process group and a timeout kills the group, so descendants (hooks, MCP servers) never leak; the turn's `turn.pid` record cannot be missed by a crash between spawn and write (record the group before the turn starts reading, or reap by session/group identity at start), and the context-retry process is recorded at spawn.
- [ ] A turn stopped by a hold no longer leaves a stale `TURN_STARTED` as the latest turn event (visible state is accurate in `status` and the dashboard); a `take_over` replay does not respawn a live pane.
- Tests: integration with real SQLite/git/tmux and real processes per item (injected faults at the real boundary: a failing reconcile step with a pending `resume`, a hanging tmux title call during a turn, two concurrent opens of a v(N-1) database, a timed-out git call with a hook child, a kill between spawn and pid write); each fails before its fix.

### Task 74t: Bounded resources and configurable timeouts (stability)
Refs: 03 §7, 05, 07; reviewer defect hunt items 4, 9, 10.
- [ ] A failed or timed-out cache copy removes its partial destination, and a retry never accepts a half-populated cache; the cache copy and the git rebase/worktree timeouts are `defaults.toml` keys sized for large repositories, a timed-out `git worktree remove` or rebase leaves a state the next cleanup or `_try_rebase` classifies correctly (no permanent "dirty" or phantom conflict), and the cache copy no longer blocks the command loop for its duration (runs off the loop with the existing bounded-wait idiom).
- [ ] Memory and scan bounds: `evidence.py` reads only the tail of `view.jsonl`; `SpawnedProcess` keeps a bounded stderr tail; `OperatorMessages._find` no longer scans the whole run directory per `say`; a retention policy for run directories, events and finished command rows (config keys, conservative defaults, never touches a live task's files or evidence of an open escalation) keeps a long-running engine bounded, and `status` reports the size.
- [ ] A hung remote (fetch, `gh pr view`) cannot hold the scheduling tick or the command loop longer than a small configured bound per tick regardless of the number of ready tasks (a total per-tick budget, with the rest deferred to the next tick).
- [ ] A single-flight notification hook cannot delay an urgent notification by more than a small bound (urgent events use their own delivery slot).
- Tests: integration per item with real git, a slow fake `gh`, a failing `cp`, a large `view.jsonl`, a long-lived engine over many short tasks with the fake agent (retention keeps the run directory count bounded); each fails before its fix.

### Task 74u: Soak and chaos run (stability evidence)
Refs: 07 §5; invariants 1–5.
- [ ] A bounded, deterministic soak (`poe soak`, not part of `poe check`; at most about ten minutes) drives the real engine with the fake agent through many tasks (dependencies, plans, rejections, escalations and resolutions, parallel slots, pull-request mode with the fake `gh`) while a chaos driver injects faults at random but seeded points: SIGKILL of the engine, SIGTERM, tmux server loss, SIGKILL of a turn process, a locked database, a failing `gh`/`git` call, a held hook. After each recovery and at the end the five invariants are asserted from durable state and git (no task lost or duplicated, every `done` task integrated or accounted, no live orphan process, no leaked tmux server or temp directory, no pending command or escalation stranded, notifications delivered), and the seed is printed so a failure replays.
- [ ] Every defect the soak finds is fixed in the same task or filed as a blocker/critical item that Phase 7d does not close until fixed; the soak result (seed, duration, faults injected, tasks completed) is recorded in the release evidence.
- Tests: the soak itself plus a short smoke variant (seconds) that runs in `poe check`.

### Task 74p: Phase 7d verification, re-review and release evidence
Refs: 07 §5.
- [ ] `uv run poe check` passes in a normal and a `TERM=dumb` environment and in repeated runs; the real-agent E2E E1–E6 pass in one invocation on the resulting revision (projects outside `$TMPDIR`), including a scenario or live check for Task 74j when it can be exercised with the cheapest rungs, and `poe soak` passes on two different seeds; the macOS smoke passes at the final commit; measured durations are recorded in `docs/e2e-results.md` and 07 §5.
- [ ] A new `docs/release-readiness-*-phase7d.md` states the release claim, the closed findings, the soak evidence and the remaining limitations, all of which must be non-stability items (Linux and any unverified agent or host configuration are excluded from the claim, no LICENSE/CI/packaging metadata is an owner decision, PR mode against real GitHub is stated as verified only against the fake `gh` unless run for real). Task 59 is restated against it.
- [ ] A first-use walkthrough on a clean environment: build the wheel, install it into a fresh virtual environment, follow only the README (`init`, `doctor`, `start`, `submit`, `status`, `take_over`/`hand_back`, `stop`) on a scratch git repository with the cheapest real rungs for Claude and for Codex, and record every deviation from the README; any step that needs knowledge not in the README is a defect fixed in this phase.
- [ ] The final review is a loop, run by the orchestrator on the final commit with five read-only reviewers on the strongest available model (not the implementers' model), each with a different lens and instructed to try to break the claim rather than confirm it: (1) the five invariants against the soak and chaos evidence, including mutations of the fixes; (2) an operator following the README and the design end to end, including every documented command and failure message; (3) data safety and security (worktree cleanup, secrets, file modes, process and tmux leaks, injection through task text and paths); (4) concurrency, crash windows and resource bounds in the engine, store and plugins; (5) conventions, test value, documentation and evidence honesty. They use the severity scale above and report no blocker and no critical issue for the claimed use, and every major is fixed or justified. If a round finds any, a task in this phase fixes it and the whole round repeats; the loop ends only with a clean round. Then Task 74h's last AC is ticked.


## Phase 8 — Operator Workspace

Specification: [Operator Workspace](../docs/tui-workspace.md), requested 2026-09-30. It defines the target experience; current behaviour changes only as the tasks below ship. Phase 7b comes first. Retain the engine's single-writer model, typed operator commands, agent-independent signals, tmux role views, SSH access, honest usage reporting and semantic comic-strip visual language.

Each task updates the relevant sections of 01–07 and the workspace specification if implementation resolves a detail differently. Add contracts only with a consuming feature. All tests follow 07 §3: observable behaviour against real boundaries, no mocks of Shikiru classes, no tests of framework features or wording, and no new snapshot catalogue.

### Task 75: Workspace design alignment and shared visual shell
Refs: [Workspace §1–3, §13, §15](../docs/tui-workspace.md#13-visual-system), 01 D18, 02 §2/4, 06 §5.4/§6.
- [ ] Record the workspace composition decision in 01 and its responsibilities in 02; update 06 to distinguish the home workspace, task detail and secondary views. Link the target specification from the design index and README, clearly distinguishing planned and shipped behaviour.
- [ ] Introduce shared background, surface, neutral border, text and focus tokens; apply them to the dashboard shell, palette and evidence modals while preserving role/state meanings, width-safe glyphs and monochrome cues.
- [ ] Add the shell's visible focus, contextual footer, help and section expansion/back navigation, using current widgets. Keep Textual/Rich and existing plugin entry points; document placement/order of configured built-ins and reachable third-party Extensions. No configured plugin is silently dropped.
- Tests: Textual integration: navigate/expand/return to a section with keyboard and mouse, preserve focus, reach a configured external widget, and read state/focus without colour. Assert behaviour and width properties, not token inventories or screenshots.

### Task 76: Selectable task browser and search
Refs: Workspace §5/§7/§15, 03 §10, 06 §5.4.
- [ ] Add ready-to-render task rows and bounded read queries consumed by an interactive browser. ID, title and state remain accessible; Active, Waiting, History and All membership/order follow §5.1, with queued tasks in scheduler dispatch order.
- [ ] Search task ID/title and combine it with filters; show matching count and distinguish an empty project from an empty search result.
- [ ] Preserve selected task ID, filter/search and scroll across state updates and row reordering. When selection leaves a filter, retain its detail/context until deliberate navigation; keep the selected visible row in view.
- [ ] Update 02's component list, 06's widget contract/navigation and 05 only for introduced settings. Preserve existing widget fields/callbacks; new capabilities are additive and have consumers.
- Tests: Textual over real SQLite: search by title, combine a filter, reorder tasks while selected, complete the selected task, clear search, and load more history without resetting selection. Unit tests only for new domain projection rules.

### Task 77: Task detail and navigation to existing windows
Refs: Workspace §6/§7, 02 §2, 06 §5.1/§5.4/§6.2.
- [ ] Render shared task detail from operator queries: situation, expanded strip, latest report/verdict, request/ACs, recorded evidence, workspace/session metadata and actions. Mark reported tests and engine-computed facts with their source; distinguish unavailable evidence.
- [ ] `dash --task` opens detail fixed to its task, with task-scoped activity/usage and a compact global attention indicator. Home Enter opens/focuses the selected task detail and Escape restores browser context.
- [ ] Open an existing task/role pane through injected navigation callbacks that resolve the current target at activation. A missing or terminal task window produces a readable result without recreating workflow resources.
- [ ] Document task-view composition and narrow identity/title requirements in 06; preserve plan/implement, setup, consult, rework and integration meanings.
- Tests: real SQLite/Git reports plus Textual and `tmux_server`: inspect a task's latest verdict, return to the same browser position, switch to its existing window, and inspect a completed task after window/worktree cleanup without recreating them.

### Task 78: Contextual actions and searchable palette
Refs: Workspace §7–8, 03 §9, 06 §5.2/§5.5.
- [ ] The shared palette offers searchable actions and task selection by ID/title. Carry the selected task/role from detail and the originating task/role pane from the tmux popup; show editable targets and advanced submission/rung fields.
- [ ] Prepare applicable actions and disabled reasons through the operator layer using domain state; revalidate when dispatching and preserve the engine's authoritative rejection. Widgets do not implement workflow eligibility or construct commands.
- [ ] Display action timing and consequences: message/rung changes apply to the next turn; pause differs from pause-now; takeover holds the task; resume/hand-back respect remaining restrictions. Cancellation and stop expose scope/work-preservation consequences in the action form.
- [ ] Update 03/06 descriptions and 02/06 contracts for the consumed context/navigation callbacks; preserve the small CLI surface and conditional tmux palette binding.
- Tests: Textual and `tmux_server`: message and change rung without typing the task ID, change the target deliberately, open the popup from a role pane, and reject an action whose target state changed. Existing fake-agent boundary tests establish timing.

### Task 79: Durable command feedback and ownership banners
Refs: Workspace §9/§15, 05 §2, 06 §1/§5.5, 03 §7/§9.
- [ ] Expose bounded read-only command history/status through `StateReader` and operator queries. Show Sending, Pending, Applied or Rejected with command ID, target and returned result; preserve stored command statuses and distinguish engine-stopped queueing from acknowledgement timeout.
- [ ] Track pending outcomes after the palette closes and recover them after dashboard restart, including commands from another dashboard or MCP. A pending form cannot accidentally submit the same action again on refresh or return.
- [ ] Applied message feedback describes guidance queued for the role's next turn. Persistent ownership banners name taken-over roles and all remaining holds; hand back and resume issue their own explicit existing commands.
- [ ] Document the read API, outcome vocabulary and acknowledgement-versus-delivery distinction in 02/05/06. Add no command writer, acknowledgement path or navigation-state table.
- Tests: real SQLite/fake engine and Textual: command completes after the acknowledgement wait, palette closes while pending, dashboard restarts, another client submits, rejection appears, and hand back leaves another ownership/pause hold visible. Assert one applied command effect.

### Task 80: Home overview and attention composition
Refs: Workspace §3–4, 06 §5.4/§5.7, 01 D18/D21.
- [ ] Compose the home header, Attention/Running/Waiting summaries, task browser/detail, and recent project decisions/activity. Counts follow the specified definitions and distinguish task categories from slot occupancy.
- [ ] Attention groups open escalations, diagnosed stalls and recorded engine faults; informational retries, quota waits and deliberate pauses retain their own meanings. Engine lock liveness stays distinct from fault and signal observations.
- [ ] Summaries navigate to their content, decisions stay visible until durable resolution, and a missing packet shows its evidence error while retaining durable escalation information. Global task-window attention routes back to home.
- [ ] Update 06 composition/attention rules and 02 data sources; use existing recorded faults and activity rules without a heartbeat, new long-wait policy or external notification system.
- Tests: seeded real state plus fake-agent transitions: paused/PR-waiting tasks and slots count correctly, escalation resolves from another client, unavailable packet retains the decision, recovery does not appear as an open decision, and attention updates without stealing focus.

### Task 81: Escalation resolution with structured evidence
Refs: Workspace §10.1, 03 §8, 06 §5.4–5.5/§6.4.
- [ ] Present the question, recommended alternative, consequences and allowed resolution fields before expandable reports, facts, timeline and tails. Keep the complete original packet accessible and preserve agent alternatives versus engine resolution-kind semantics.
- [ ] Route the selected escalation through the shared palette with its exact ID; map choices using the validated resolution contract, including answer/rung requirements.
- [ ] Refresh forms when another Operator resolves/cancels the task; disable stale submission, preserve entered text for copying and show engine rejections from later races without resubmitting.
- [ ] Update 03/06 packet and resolution presentation, keeping escalation state/evidence and existing work-preservation rules authoritative.
- Tests: Textual over real SQLite/fake engine: inspect consequences and full evidence, submit each applicable resolution class, resolve from another client while editing, and handle packet-read failure and the submission race.

### Task 82: Blocker navigation and task families
Refs: Workspace §10.2–10.3, 03 §2/§6/§10, 06 §5.4.
- [ ] Show specific dependency/slot/resource/quota/pause/ownership/PR blockers with their available evidence and navigation. Distinguish dashboard resource sample age, scheduler reason, provider quota reset and engine retry/probe time.
- [ ] Group children under parents with expand/collapse and filter-matched ancestor context. Show dependencies/dependents separately and superseded replacements only when supported by recorded evidence.
- [ ] Keep plan-parent integration separate from child implementation completion; show exact family outcome counts. Show submitted/recorded queue age without inventing current-blocker duration, ETA or guaranteed start position.
- [ ] Update 02/06 consumed relationship/blocker view contracts and 03's operator-facing explanations; reuse dispatch/domain rules and existing stored graph relationships.
- Tests: real SQLite/Git/fake-agent decomposition and supersede: follow a blocked dependency, navigate back with selection intact, see active children under a done parent, search a child with ancestor context, and distinguish unknown quota resets and cancelled replacements.

### Task 83: Completion history and task evidence timeline
Refs: Workspace §11, 05 §2–3, 06 §2/§5.4.
- [ ] Make historical task detail usable after terminal cleanup; retain recorded cancellation/supersede reasons, reports, decisions and integration outcome without inventing terminal failure states for recovered turn errors.
- [ ] Add bounded timeline/evidence queries and filters by role, turn and category. Distinguish engine events from normalized agent signals, preserve source ordering and identify missing timestamps/evidence or an incomplete live JSONL row.
- [ ] Live evidence follows until the Operator scrolls away; provide an explicit Follow latest action. Updates and pagination preserve reading position and selected evidence identity.
- [ ] Update 02/05/06 query, source-ordering and retained-evidence descriptions. Read recorded files without a second consumer of agent stdout or agent-specific parsing in the UI.
- Tests: real SQLite and recorded normalized files: browse a closed task, page/filter a long timeline, receive a partial then complete live row, preserve selection while new events arrive, and resume following explicitly. Test ordering through public behaviour, not implementation helpers.

### Task 84: Honest usage and secondary diagnostics
Refs: Workspace §12, 01 D21/D23, 04 §1, 06 §5.4.
- [ ] Expose project recorded totals and selected-task usage with explicit scope, tokens, cost basis and priced/unknown-run coverage. Separate billed and API-equivalent totals and label partial subtotals; show unknown values distinctly from measured zero.
- [ ] Preserve only reliable reported context and quota windows, including reset/observation age. Add no inferred pricing, spend budget, completion percentage, ETA, daily charts or time-period labels unsupported by queries.
- [ ] Move host/process-tree resources, Docker and recorded engine-fault evidence into reachable diagnostics. Distinguish empty, unavailable, stale and failed samples; failures leave unrelated task data readable.
- [ ] Update 05/06 metric definitions, scope, sampling/observation labels and widget placement; keep expensive reads off motion refreshes.
- Tests: real runs/quotas and samplers: mixed cost bases, partial pricing, unknown/zero values, per-task versus project totals, aged quota observations, unavailable Docker and a failed sample without lost task navigation.

### Task 85: Responsive layouts and accessible refresh behaviour
Refs: Workspace §7/§13–14, 06 §5.4/§6, 07 §3.
- [ ] Choose wide browser/detail or compact list/detail from actual pane dimensions. At 48 and 36 columns keep task ID/title, state, hold/blocker and all action/result controls reachable; short panes collapse summaries and keep content scrollable.
- [ ] Resizing preserves selection, focus, search and scroll; focused rows/forms remain visible. Footer/help, keyboard and mouse paths operate under `NO_COLOR` and disabled animation with textual state/ownership cues.
- [ ] Preserve the signal-driven motion rules and 4 Hz cap; static motion settings still allow state/countdown refresh. No tmux split rearrangement or blank/clipped critical panels is introduced.
- [ ] Document named layout limits, compact strip/row forms and terminal support in 05/06/07. Retain the snapshot limit and avoid operator breakpoint settings without a concrete need.
- Tests: Textual size/interaction matrix for wide, 48-column, 36-column, short and very small panes, monochrome and static frames. Assert reachable critical content, stable identity/focus and width bounds; visually inspect representative home/task/form states and record findings.

### Task 86: Markdown and JSON evidence export
Refs: Workspace §11/§15, 02 §2, 05 §3, 06 §5.5.
- [ ] Add a read-only operator export use case consumed by task detail/palette, with an injected local file writer. Export request, task outcome/relationships, available reports/facts/timeline, session/rung metadata and usage provenance; signal excerpts are an explicit inclusion option.
- [ ] Markdown and JSON contain a format version, task ID, export time and unavailable sections; preserve recorded source ordering and unknown costs, and escape/fence embedded content. Export does not imply a complete agent transcript or reconstruct cleaned worktrees.
- [ ] Destination selection, explicit overwrite choice, success and write failures are visible. Export never queues a workflow command or changes engine state; no recorder, timed playback, HTML renderer or network service is added.
- [ ] Document contents, format/version, destination behaviour and retained-evidence limits in 02/05/06 and the workspace specification.
- Tests: integration with real task records/files: parse JSON, verify meaningful Markdown evidence and hostile fenced text boundaries, export after worktree cleanup, mark missing sections, and handle existing/unwritable destinations while database task/event/command state stays unchanged.

### Task 87: Operator documentation and workflow walkthroughs
Refs: Workspace §16, 01–07, README.
- [ ] Reconcile 01–07 with the completed workspace: decision log, responsibilities/file list, action timing/ownership, read/export contracts, widget placement/defaults, keyboard navigation, visual/compact forms and testing requirements. Update the design index and workspace implementation status.
- [ ] Rewrite README dashboard/navigation examples around shipped home/task views, contextual actions and evidence inspection; document palette access from tmux, pending outcomes, usage provenance and export limits without duplicating the CLI action surface.
- [ ] Run and document the eight workspace walkthroughs with the fake agent, including another Operator's escalation resolution, remaining takeover holds, engine-down pending command recovery and terminal history after window cleanup.
- [ ] Every published shortcut, example and screenshot/terminal sketch agrees with actual behaviour; planned or unverified capabilities are labelled. Keep the workspace specification and task references internally consistent.
- Verification: inspect real Textual/tmux flows and the produced export artifacts; review documentation links and examples. No tests that pin prose or screenshots.

### Task 88: Workspace release verification and evidence
Refs: Workspace §16, 07 §5.
- [ ] `uv run poe check` passes normally and with `TERM=dumb` on the resulting revision, preserving coverage ≥ 80% and architecture boundaries.
- [ ] Real-agent E1–E6 pass in one `poe e2e` run on that revision; record date, scenarios, duration, source commit, CLI versions and reported cost or unknown cost as appropriate.
- [ ] Clean-install smoke and the documented workspace entry/navigation paths pass on macOS; Linux is reported as verified only after an actual run. Record representative home/task pane sizes, monochrome/static operation and remaining limitations.
- [ ] A new `docs/release-readiness-*.md` records Phase 8 delivery, fake-agent walkthrough evidence, final checks and design agreement. Tick Tasks 75–88 only after their own ACs are met; planning this phase does not count as implementation.
