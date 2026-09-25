# Shikiru Implementation Plan

Source of truth: [`docs/design`](../docs/design/README.md). Progress: [`todo.md`](todo.md).

**Rules**:
- Follow `CLAUDE.md`/`AGENTS.md` and 07 §2–3.
- Work on one task at a time.
- `uv run poe check` must be green before every commit.
- Tick each AC here and the task in `todo.md` when done.
- An AC naming an E2E scenario is ticked only after a passing `poe e2e` run; the ticking commit message states the run date, scenarios and cost.

**Order**:
- Phases 0–3 build the whole pipeline against the fake agent.
- Phase 4 makes it real (E1, E2).
- Phase 4b moves agents to headless turns on resumed sessions with panes as live views (D1 revised, D21, D22). Its tasks are numbered 31–38 so earlier numbers stay stable; do them before Phase 5. Task 38 (code health) runs alongside: its correctness items first, the rest together with the Phase 4b tasks they touch.
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
- [ ] With Phase 4b tasks: E8, E9 (Task 35), E25, F9 (Task 36). F24 is closed by Tasks 33–34. F3 and C1 duplicate E5 and F2.
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
- [x] Turn argv (new, resume, model change, auth modes), stream-json parser, failure mapping incl. stderr tags, usage and context rules, `QuotaReported` from `rate_limit_event`, `interactive_command`, `headless`, `doctor`.
- [ ] Compaction per 04 §4: `/compact` before a turn at `compact_at_pct`, compact-and-rerun on `CONTEXT` (verify `/compact` on an overflowed session), `Compacted` from `compact_boundary`.
- [ ] Recorded samples under `tests/fixtures/agents/claude/` (subscription, API key, OpenRouter, rate limit, unrecognised model); offline checks pass; live checks pass.

Task 33 verification note (2026-09-25): `uv run poe check` passes with a dedicated `TMUX_TMPDIR` (584 passed, 14 skipped; coverage above the 80% gate). An offline replay now includes a genuine redacted OpenRouter stream. The default sandbox's host-only probe failed with `gaierror`; the same host-only probe with network escalation returned HTTP 200. With the local settings environment loaded only into the Claude subprocess, one short Claude Code 2.1.281 turn on the configured Haiku model emitted `SessionStarted`, `Message`, non-zero `UsageReported`, and `TurnEnded`. This shows the earlier retry-only timeout was caused by the sandbox network restriction; no auth, model, or protocol failure reproduced after escalation. The other genuine sample types, full live contract suite, and `/compact` after context overflow remain open. Overflow was not attempted.

Task 33 live verification follow-up (2026-09-25): the configured OpenRouter host resolved under escalation; a credential-free `HEAD` to its origin returned HTTP 403. The bounded live suite produced three passes (usage, stop/resume, headless ask) and three failures. The report check did not pass even though its captured stream had a success terminal; a sanitized diagnostic did not preserve whether the report artifact was missing or invalid. The other two failures exposed missing prompt imports and scratch-repo setup in the new live-test overrides; those test setup errors were corrected. The model-change test is also invalidly configured because the configured Haiku and Sonnet model IDs are identical, so it now skips in that case. The live low-compaction check now skips when the provider omits `context_used_pct`, as required by D21. After correcting the test setup, a model-memory attempt hit three genuine `api_retry` events with HTTP 429 before the 60-second turn deadline and emitted no terminal result. This is an endpoint rate-limit response, with no evidence of an auth or stream-protocol rejection. The provider does not report a reliable context window, and the current 429 blocks another bounded compaction call, so no genuine `/compact` boundary was captured; context overflow was not attempted. The live report artifact, a real model change, and live compaction remain unverified. Task 33 ACs remain open.

Task 33 live verification follow-up (2026-09-25): after the OpenRouter token was updated, the non-tilde `anthropic/claude-haiku-latest` model identifier returned HTTP 400 with no usage. OpenRouter's current Claude Code guide configures the alias as `~anthropic/claude-haiku-latest`; one bounded, content-free turn using that alias succeeded, started a session, emitted a message, and ended successfully. Its OpenRouter generation record identifies `anthropic/claude-4.5-haiku-20251001` via Amazon Bedrock and bills $0.00786875; Claude Code's own $0.03934375 estimate has `costBasis=unknown`, so it is not used as billed cost. Immediate key/account aggregate deltas were zero; the generation record provided the per-request charge. The report-artifact contract test was rejected by automatic approval review because its repository-derived prompt/context would be sent to OpenRouter without explicit authorization for that payload, so it was not executed. The previous successful live calls cannot be itemized against the refreshed key or generation IDs; the activity endpoint requires a management key, which this key does not have. No more live spend was incurred. This content-free turn does not verify report creation, memory/resume, `/compact`, or overflow. OpenRouter did not provide a reliable context-use percentage, so compaction and overflow remain unverified. Keep both Task 33 ACs open pending authorized report-test payload and genuine subscription/API-key/error samples; no Task 33 AC or todo item is complete.

Task 33 report-payload audit (2026-09-25): the live report check now uses a clean temporary git repo and run directory. The prompt file contains one fixed `shikiru_command("report", "--file")` invocation against a prepared JSON input, forbids other file inspection or commands, and requests only `REPORT_OK` after success. The input JSON is fixed synthetic data; `report.kind` and the generated `ImplementationReport` schema are the only turn files reachable through `current_turn`. The Claude process cwd is the empty scratch repo; its sole `--add-dir` is the scratch run directory; its `HOME` and `CLAUDE_CONFIG_DIR` point to empty scratch directories. A local-only audit test verifies the file manifest, command paths, prompt, schema, and stripped API-key/token environment; it passes without calling Claude or OpenRouter. Under 04 §4, no OpenRouter auth mode is specified: the test-only launcher must load the base URL and bearer token from the local settings file into the Claude child after subscription-mode auth filtering, and explicitly set `ANTHROPIC_API_KEY` to an empty string per OpenRouter's setup. No Task 33 plugin auth change was made. This fixed payload is ready for review, but no external request was made; both Task 33 ACs remain open.

Task 33 live report verification (2026-09-25): one bounded OpenRouter Claude Code run using the tilde Haiku alias and only `Read,Bash` tools completed the audited first-turn report check. Claude read the prompt file, invoked `shikiru report` with the fixed synthetic JSON, produced a schema-valid report, and emitted `TurnEnded`. A redacted normalized replay excerpt was added at `tests/fixtures/agents/claude/turn_openrouter_report_live_redacted.jsonl`; it contains no credentials, prompt text, paths, command contents, tool output, or private response text. The current-key usage endpoint measured $0.05804775 before the run, unchanged immediately after, and $0.076317 after delayed settlement, an aggregate key-usage delta of $0.01826925. Claude Code's estimate was $0.069104 with `costBasis=unknown`; it is not treated as billed cost. This verifies only the OpenRouter first-turn report check. Subscription/direct API-key samples, error samples, remaining live checks, and compaction after overflow remain open; no Task 33 AC or todo item is complete.

### Task 34: Codex headless plugin
Refs: 04 §5.
- [x] `exec` / `exec resume` argv with closed stdin, workspace-write sandbox with the run dir and the git common dir writable (verify persistence across `exec resume`), `--json` parser, error-message JSON mapping, rollout context and `rate_limits`, `interactive_command`, `headless`, `doctor`.
- [x] Compaction per 04 §5: `model_auto_compact_token_limit` from `compact_at_pct` and the model's window on every turn, compact-and-rerun on `CONTEXT`, `Compacted` from the rollout's `compacted` entry.
- [ ] Recorded samples under `tests/fixtures/agents/codex/` (first turn, resume with model change, usage limit, API key with null `rate_limits`); offline checks pass; live checks pass, including a commit from the worktree. First-turn and resume-with-model-change samples are genuine redacted 0.156.1 captures; the low-threshold compaction sample is a genuine redacted protocol excerpt. The live suite and worktree commit check pass. Genuine headless usage-limit and API-key/null-`rate_limits` captures remain unavailable: the live environment has subscription credentials only and no captured usage-limit failure. Error and null-rate-limit cases have synthetic offline coverage, documented as synthetic in the fixture README.

Task 34 verification note (2026-09-25): `uv run poe check` passes (696 passed, 28 skipped; coverage exceeds the 80% gate). All six live contract checks pass, as do the isolated worktree model-change persistence check and doctor (8 passed in 139.12s). The API-key/null-`rate_limits` and usage-limit recorded-sample evidence remains open for the reasons above.

### Task 35: Engine on process-per-turn; screen machinery removed
Refs: 03 §4, §7, 04 §6, 05 §1–2.
- [x] `SessionManager.session_for` per 03 §4.1; `TurnMonitor` (single consumer, run facts, `quotas` from `QuotaReported`, `view.jsonl`, decorations, stall → `stop()`); `TurnRunner` per 03 §4.3 incl. resumed nudge turns and `Helper.classify_failure` with `CLASSIFY_CONFIDENCE`.
- [x] Recovery per 03 §7 (`Stalled` → resumed `continue`, quota block → resumed `continue`); cancellation stops the turn; `turn.pid` written and removed.
- [x] Removed: `InteractiveAgent`/`AgentSession`/`PaneHandle`, keys, pointer injection and submit confirmation, `before_turn` (compaction moves into the plugins, 04 §1 obligation 8), `HookRecorder` and `shikiru hook`, `activity.is_operator_prompt`, `Helper.classify_screen`, `QuotaPoller` and `quota()`, `timeouts.launch`, `compact_below_free_pct`, `runs.agent_state` (migration), `agents.<name>.prompt_prefix` (a slash or `$` skill prefix does not work in headless turns; projects name skills in their own instructions).
- Tests: integration with the fake CLI: accept; a `Compacted` signal becomes a `compacted` event; changes_requested → accept on one resumed session; no report → nudge turn; rebuild; stall stop → resume; unknown failure classified above and below the threshold; model change resumes the session; different agent → handoff.

### Task 36: Pane views, take over / hand back, queued `say`
Refs: 03 §9, 06 §2, §4, §5.1–5.2.
- [ ] `shikiru view T-n ROLE` renders `view.jsonl` live (`ui.StreamLine`); role panes run it; `LayoutGuard` respawns dead view panes.
- [ ] `TakeOver`/`HandBack` commands, handlers and `Takeover` per 03 §9; MCP `take_over`/`hand_back`; a dead taken-over pane hands back; `taken_over`/`handed_back` events.
- [ ] `Say` queues `messages/<role>/NNN.md`, delivered into the role's next turn dir and listed in its prompt.
- Tests: integration on `tmux_server` with the fake agent: the view shows a turn's messages and tool calls; take over stops the turn, holds only that task and runs the REPL; exiting the REPL hands back and the step completes on the same session; a message sent mid-turn appears in the next prompt of that role only.

### Task 37: Probe and real E2E on headless turns
Refs: 04 §3, 06 §2, 07 §5.
- [ ] `doctor --probe` runs live check 1 per ladder agent in `.shikiru/probe/`, reporting the stream tail and stderr on failure.
- [ ] E2E E1 and E2 (queued delivery, 07 §5) pass.

## Phase 5 — Resilience and operator completeness

### Task 22: Engine restart recovery
Refs: 03 §7.
- [ ] `Reconciler`: rebuild windows, end orphaned turn processes (`turn.pid`), re-run current steps idempotently on resumed sessions, integration restart.
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
- [ ] `PaletteApp` with every action including the escalation resolution form and take over / hand back; tmux popup and `Ctrl+P` in `dash`.
- Tests: Textual `run_test` for submit, prioritize, resolve, take over.

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
