# Quota and task usage improvements

The Operator requested fixes to quota labels, Claude percentages, long reset countdowns, and
separate input/output token counts. This document is the tracker for the two follow-up tasks
below. Each task checks only its own entry when complete.
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
- `UsageBody._tokens` adds session input and output into one number. The requested `r/w` means
  the existing normalized `input_tokens / output_tokens`, not cache read/write. Keep those
  existing token semantics: do not add cache counters, which overlap input for Codex, or change
  provider token accounting as part of this display change. Existing detailed cache counters
  remain available. This keeps the new column a split of the currently displayed total.

## Execution order

The quota display task can run alongside the token column task. No other dependencies are
required. Implementation work is emitted in the plan report rather than performed by this
planning task.

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
