# Quota and task usage improvements

The Operator requested fixes to quota labels, Claude percentages, long reset countdowns,
historical task quota consumption, and separate input/output token counts. This document is the
tracker for the three follow-up tasks below. Each task checks only its own entry when complete.
This planning change implements no application behaviour.

## Findings and decisions

- Claude's `ClaudeTurnParser._quota_window` copies `utilization` to `used_pct` without converting
  fractional utilization to percent. Its fixtures currently use percentage-shaped values.
- Claude reports `five_hour`/`seven_day`; `CodexQuotaProbe` reports `primary`/`secondary`.
  Normalize the built-in windows to `5h`/`7d` at the provider boundary. Codex fixtures include
  `window_minutes` (300 and 10080); do not mislabel an explicitly different duration.
- Dashboard `_quota_countdown` calls `_duration`, which never emits days. The shared
  `ui.age.format_duration` already supports days, but has different sub-hour formatting; preserve
  existing sub-day countdown behaviour when reusing it or its day decomposition.
- `DashboardViews.task_detail` attaches current project quota observations for the task's agents.
  `dashboard.detail` refreshes those observations on motion. `UsageBody` renders them as current
  account percentages and reset countdowns. Neither is evidence of quota consumed by this task.
- `QuotaStore.record` upserts one account snapshot per agent/window. `TurnMonitor` also writes
  signals into role view logs, but durable task accounting must not depend on reading retained
  logs or live account snapshots on every dashboard refresh.
- `UsageBody._tokens` adds session input and output into one number. The requested `r/w` means
  the existing normalized `input_tokens / output_tokens`, not cache read/write. Keep those
  existing token semantics: do not add cache counters, which overlap input for Codex, or change
  provider token accounting as part of this display change. Existing detailed cache counters
  remain available. This keeps the new column a split of the currently displayed total.

## Execution order

The quota display task can run alongside the token column task. Historical task quota accounting
depends on quota normalization so it stores canonical names and consistent percentage units.
No other dependencies are required. Implementation work is emitted in the plan report rather
than performed by this planning task.

## Tasks

### Q1 — Normalize quota presentation

- [x] Implement quota labels, Claude percent conversion and day-aware reset countdowns.

Acceptance criteria:

1. Built-in Claude and Codex five-hour/seven-day windows use `5h`/`7d` consistently in the
   project quota view and task evidence. Keep provider protocol keys separate from public names;
   preserve other plugins' window names. Respect an explicit nonstandard Codex window duration.
2. Claude fractional utilization is converted exactly once: 0, 0.0036, 0.36 and 1 mean 0%,
   0.36%, 36% and 100%. Codex `used_percent` is already percent and is not rescaled. Update the
   synthetic Claude quota fixtures and affected contract expectations instead of using a
   value-based heuristic that mistakes small percentages for fractions.
3. Existing databases remain readable; legacy window aliases do not produce duplicate displayed
   windows after new canonical observations arrive. Old Claude snapshots have ambiguous units:
   do not blindly multiply them or pretend they are corrected historical evidence. Invalidate
   ambiguous latest snapshots and wait for a fresh observation, or preserve explicit uncertainty.
   Do not change quota block deadlines or rewrite retained raw provider evidence.
4. A countdown of 135h 46m renders as `5d 15h 46m`. Cover the transition around 24h, ordinary
   sub-day values, unknown resets and expired resets, with nonnegative remaining time.
5. Parser/contract tests cover source normalization; SQLite/dashboard integration verifies legacy
   compatibility, visible labels/percentages and countdown transitions with `FakeClock`.

References: `src/shikiru/plugins/agents/claude/turn.py`,
`src/shikiru/plugins/agents/codex/quota.py`, `src/shikiru/agents/contracts.py`,
`src/shikiru/store/quotas.py`, `src/shikiru/store/migrations/`,
`src/shikiru/dashboard/views.py`, `src/shikiru/ui/age.py`,
`tests/fixtures/agents/claude/turn_subscription.jsonl`,
`tests/fixtures/agents/claude/turn_rate_limit.jsonl`,
`tests/integration/test_claude_headless_contract.py`,
`tests/integration/test_codex_headless_contract.py`,
`tests/integration/test_store_quotas.py`, `tests/integration/test_dashboard_usage_panel.py`,
`tests/integration/test_dashboard_motion.py`.

### Q2 — Record and display historical task quota consumption

- [x] Replace task Usage live quota rows with durable, task-scoped consumption evidence.

Depends on Q1.

Acceptance criteria:

1. Persist quota evidence with task, run/session and turn identity, canonical provider/window,
   percentage, observation time and reset deadline. Capture the available start/end observations
   around each turn, including failed/interrupted work; distinguish a missing baseline from zero.
   Store enough coverage/provenance to explain an unavailable or partial total after restart.
   Keep SQL in the store and calculation rules pure in the domain.
2. A usable pair of ordered observations in the same reset period contributes the percentage
   point increase, e.g. 20% to 23.5% contributes 3.5% of that window. Sum disjoint observed
   intervals across this task's roles, resumed turns and old/new sessions by provider/window.
   Never add cumulative session values repeatedly or count duplicate observations twice.
3. Establish a baseline before charging an interval. A lone end observation cannot establish
   total task usage, and an older cached account snapshot cannot establish full turn coverage.
   Use observations collected within the turn for a partial observed delta when full boundaries
   are unavailable. Preserve any boundary gap as partial coverage. Inspect provider collection:
   Codex currently collapses rollout quota to the latest sample at finish; retain available
   in-turn observations or capture trustworthy boundaries where supported. Filter resumed
   rollouts to this turn; collection time must not redate older samples. Do not fabricate a
   baseline, use another task's final value as this task's start, or add live provider calls
   solely for dashboard refreshes.
4. Split accounting at a changed reset deadline or a decrease in usage. Require a baseline in
   the new period; never subtract across resets or infer that the old window reached 100%.
   Missing reset identity, out-of-order/stale samples, interrupted collection and missing
   boundaries produce partial/unavailable coverage rather than invented consumption. A true
   observed zero delta is distinct from missing data. Summed consumption can exceed 100%
   across reset periods; do not clamp the task total to a gauge range.
5. Provider quota is account-wide. Present recorded deltas as observed estimates of task quota
   consumption, with coverage visible when incomplete. Overlapping same-provider task intervals
   are ambiguous: do not allocate or double-charge their shared account delta as exact usage;
   mark affected coverage unavailable/partial. External account activity cannot be detected
   reliably, so document the estimate limitation without inventing a token-to-quota conversion.
6. Task Usage shows historical percentages per provider/window and no live remaining quota or
   reset countdown. For example, a fully observed 20% to 23.5% interval shows 3.5% consumed,
   not the current 23.5% account usage. Active tasks update only from new recorded task evidence;
   finished task figures remain stable after later account use, reset countdown ticks, reopen
   and engine restart. Legacy tasks without sufficient evidence show unavailable consumption;
   no historical backfill from current snapshots is required.
7. Replace the task-detail motion refresh path as well as initial rendering. Keep project-wide
   quota gauges and scheduler quota blocks live and functioning. Use durable task evidence that
   survives ordinary run-log/event retention; avoid a new dashboard log-scanning path.
8. Domain rule tables cover interval validity, resets, coverage and aggregation. Real SQLite,
   fake-agent and dashboard integration covers persistence/restart, resumed turns, failed turns,
   two tasks sharing a provider, missing evidence and stable finished-task values while global
   quota changes. Adapt the current motion test that expects task quota reset ticking.

References: `src/shikiru/engine/monitor.py`, `src/shikiru/engine/turns.py`,
`src/shikiru/engine/sessions.py`, `src/shikiru/plugins/agents/codex/turn.py`,
`src/shikiru/plugins/agents/codex/quota.py`, `src/shikiru/agents/contracts.py`,
`src/shikiru/store/quotas.py`, `src/shikiru/store/runs.py`,
`src/shikiru/store/reader.py`, `src/shikiru/store/retention.py`,
`src/shikiru/operator/queries.py`, `src/shikiru/dashboard/contract.py`,
`src/shikiru/dashboard/views.py`, `src/shikiru/dashboard/detail.py`,
`src/shikiru/dashboard/usage_body.py`, `tests/integration/test_engine_monitor.py`,
`tests/integration/test_engine_restart.py`, `tests/integration/test_dashboard_task_detail.py`,
`tests/integration/test_dashboard_motion.py`.

### Q3 — Split task token counts into read/write values

- [x] Show task session input/output values in a single `Tokens r/w` column.

Acceptance criteria:

1. Rename task Usage's `Tokens` header to `Tokens r/w`; render each session's existing
   `input_tokens / output_tokens` separately, e.g. 1,340,000 input and 83,500 output become
   `1.34M / 83.5k`. Apply this to current and older session rows without changing cumulative
   session accounting or the task's aggregate token total.
2. Use decimal k/M units with up to three significant digits for abbreviated values, removing
   unnecessary fractional zeros; keep values below 1,000 as whole counts. Carry rounding across
   unit boundaries consistently. Scope formatting changes to this column unless shared reuse
   preserves other existing displays.
3. `r` means reported input and `w` reported output. Cache read/write counters remain separate
   diagnostic fields and are not added to either side; retain provider-specific normalization
   and do not reinterpret the shared Usage contract in this task.
4. Preserve reported zero (`0 / 0`), legacy known token totals, and unavailable usage (an em dash).
   Unreported default zeros must not become apparently measured usage.
5. The wider values fit the task Usage table at supported narrow widths without overlapping,
   clipping one side of the pair, or losing the meaning of r/w. Adapt the existing column-drop
   behaviour or use a readable stacked layout when the complete pair cannot fit.
6. Dashboard integration checks meaningful token values, unknown/zero cases, older sessions,
   cache counters that would expose double counting, and wide/narrow rendering. Do not add
   tests that merely enumerate headers; verify the rendered behaviour and numeric meaning.

References: `src/shikiru/dashboard/usage_body.py`, `src/shikiru/dashboard/contract.py`,
`src/shikiru/ui/compact.py`, `src/shikiru/plugins/agents/codex/usage.py`,
`src/shikiru/plugins/agents/claude/turn.py`, `tests/unit/test_usage_rows.py`,
`tests/integration/test_dashboard_task_detail.py`,
`tests/integration/test_dashboard_usage_panel.py`.

## Verification and completion

Follow `AGENTS.md` for each task: `uv sync --locked` in its fresh worktree, then
`PYTEST_ADDOPTS='-n 2 --dist=loadfile' uv run poe check` before every commit. The full gate must
pass with coverage at least 80%. Use real SQLite/git/tmux boundaries and the fake agent where
appropriate, `FakeClock` and `wait_until`, and behaviour-focused tests rather than mocks of
project classes or snapshot churn. Unit tests belong to domain rules, models and parsers;
rendering and persistence checks belong in integration tests. Add no application tests for this
documentation-only plan. Each implementation task checks its own entry in this file and reports
the verification it ran.
