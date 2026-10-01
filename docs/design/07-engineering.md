# 07 · Engineering

## 1. Stack

| Concern | Choice |
|---|---|
| Runtime | Python 3.14, uv (`uv tool install .` from the Shikiru checkout) |
| CLI / TUI | Typer + Rich / Textual (pinned minor versions) |
| Validation | pydantic v2 |
| Config | `tomllib` |
| Templates | Jinja2 with `StrictUndefined` |
| DB | stdlib `sqlite3` in `store`, called via `asyncio.to_thread` from the engine |
| Metrics | psutil |
| MCP | official `mcp` SDK (FastMCP, stdio) |
| External | tmux ≥ 3.4, git ≥ 2.45; optional `gh`, `docker` |
| Quality | ruff (format + `select = ["ALL"]` with a short documented ignore list, `banned-api` rules from 02 §2), basedpyright `strict`, import-linter (02 §2 layers), the comment check (§2.8), vulture |
| Tests | pytest, pytest-asyncio, pytest-timeout, coverage (`fail_under = 80`, `patch = ["subprocess"]`, `parallel = true`) |

## 2. Conventions

1. **SOLID**:
   - one reason to change per class (if its description needs "and", split it);
   - extend through protocols and entry points;
   - constructor injection only;
   - `bootstrap.py` is the single composition root.
2. **KISS/YAGNI**:
   - no speculative options, layers or generic frameworks;
   - functions over classes when there is no state;
   - one way to do each thing.
3. **Pure core**: `domain` has no I/O. Decisions (transitions, rungs, recovery, scheduling, escalation options) are pure functions.
4. **Boundaries**:
   - SQL only in `store`;
   - subprocesses only via `process.SubprocessRunner`;
   - time only via `clock.Clock`;
   - agent specifics only in `plugins/agents/*`;
   - enforced by the tools named in 02 §2 (import-linter, ruff `banned-api`).
5. **Values**: operator tunables go in `defaults.toml` (05 §1). Protocol constants are named module constants. No other literals. This stays, and so does the no-comments rule in item 8: they keep review uniform. Only the comment rule is checked by a tool (item 8); the literal rule is a review convention, because no lint rule separates a named constant from a local magic value without also rejecting idiomatic code.
6. **Async**: the engine is asyncio. Blocking work goes through `asyncio.to_thread` or async subprocesses.
7. **Errors**: a `ShikiruError` hierarchy with user-facing messages. The CLI maps errors to exit codes. No bare `except`, no silent fallbacks.
8. **No comments**: names explain. A comment that is genuinely needed carries the in-place exclusion marker `# keep: <reason>`; the reason is required. `uv run poe comments` (`tests/support/comment_rule.py`, part of `poe check`) tokenizes `src/`, `tests/` and `scripts/` Python files and fails on every comment other than a shebang or encoding line, a tool directive in exactly one of these forms (anything else after the directive, such as free prose, is rejected): `noqa: CODE[, CODE]` optionally followed by ` -- <reason>`, `type: ignore[rules]`, `pyright: ignore[rules]` (or `basic`/`standard`/`strict`), `pragma: no cover` or `no branch`, `fmt: off`/`on`/`skip` or a `keep:` comment with a non-empty reason. Shell scripts are not checked. Docstrings only on public contracts (`agents.contracts`, `DashboardWidget`, operator services, report and command models) and their implementations.
9. **Commits**: conventional commits, one logical change each. Refactorings go in separate `refactor:` commits.
10. **Gates**:
    - `uv run poe check` (format, lint, comments, imports, types, dead code, non-E2E tests with coverage) must pass before every commit.
    - `uv run poe e2e` runs real-agent scenarios.

## 3. Tests

| Layer | What | How |
|---|---|---|
| Unit | `domain` rules (transitions, `pick_rung`, `recovery.decide`, `pick_ready`, escalation options), report and command model validators, config merge, SDK parsers (`ResetTimeParser`), plugin `TurnParser`s over recorded streams, glyph width safety | Table-driven, no I/O |
| Integration | Everything else, against real SQLite, git, tmux and Textual, with the fake agent: store; workspace; tmux client, layout, guard and decorations; session manager, turn monitor and turn runner; take over and hand back; `shikiru view`; task flow paths; integration strategies (`gh` faked on PATH); engine and handlers; reconciler; operator services; CLI; MCP (in-memory client); widgets (Textual `run_test`); plugin offline contract checks | Behaviour assertions on observable outcomes: DB rows, events, git history, files, tmux state |
| E2E | Real Claude Code and Codex | §5 |

**Test infrastructure** (`tests/support/`):
- **`tmux_server` fixture**: `tmux -L shikiru-test-<uuid> -f /dev/null` with a fixed `-x 200 -y 50`, `default-shell /bin/sh` and a clean env. Killed in teardown, unique per xdist worker.
- **`wait_until(predicate, timeout)`**: the only way to wait. No sleeps.
- **`FakeClock`**: injected into the engine, so timeouts, backoffs, quota waits and PR polls run instantly.
- **Fake agent plugin** (`tests/fake_agent/`, a test-only package with its own entry point): a fake CLI run per turn through `StreamingTurn`, printing a JSONL stream its own `TurnParser` reads, resuming sessions by id from files under its run dir.
  - It is scripted by `SHIKIRU_FAKE_SCRIPT` to: accept, changes_requested×N, needs_decision, no report, fail(kind, resets_at), unknown failure text, exit without result, stall.
  - It runs the real `shikiru report` and commits files.
  - Its `interactive_command` is a line-reading REPL on the same session, for take-over tests on `tmux_server`.
  - It passes `AgentContractSuite`.
- **In-process engine**: flow tests run `build_engine()` inside the test process, so coverage is measured directly. The subprocess patch covers the fake CLI, `shikiru report` and the CLI.

**Meaningful tests, no snapshot spam**:
- Assert behaviour and properties: rendered width ≤ available columns; every state shows a word or glyph under `NO_COLOR`; a packet has all sections and exactly one ★; a template contains the request and the report instruction, and `StrictUndefined` raises on missing fields.
- Golden snapshots are limited to one strip per mode at two widths.
- No tests of private functions or mocks of our own classes: fake only process boundaries (agents, `gh`).
- Each test name states the behaviour (`test_rejected_twice_moves_implementer_up_one_rung`).

## 4. Agent samples

Plugin offline checks replay protocol fixtures committed under `tests/fixtures/agents/<agent>/`: per sample the stdout JSONL, stderr, exit code and expected signals, plus session-log excerpts where the plugin reads them (Codex rollout `token_count`). The fixture set includes genuine redacted captures and synthetic behavior cases. Fixture documentation identifies which evidence is genuine; synthetic cases exercise parser and recovery paths that have not been captured from a live run. Genuine captures preserve the relevant CLI stream shape while removing secrets and private content. When an agent CLI changes format, add new samples next to old ones.

## 5. E2E scenarios (`poe e2e`, `SHIKIRU_E2E=1`)

**Fixture project** `tests/fixtures/sample-project`:
- a tiny Python package with pytest;
- `CLAUDE.md`/`AGENTS.md` telling agents to test, commit and tick `tasks/todo.md`;
- `tasks/plan.md` with small tasks. Phase B's todo items are **adjacent lines**, so parallel tasks force a rebase conflict.
- It is one git repository; multi-repo projects are out of scope (D20).

**Runs**:
- Each scenario copies the fixture, runs `init --yes`, then starts with global `--set` rung overrides for the cheapest test rungs; it asserts only observable outcomes.
- Each scenario attempt has its own budget (table below), and the invocation has one (60 minutes; an attempt gets the smaller of its budget and what is left). No attempt may take 30 minutes.
- The E2E engine configuration shortens the waits that matter only when something is wrong, without changing what a scenario verifies: `orchestration.timeouts.integration_wait=60` (default 1800), so a merge blocked by a dirty operator checkout escalates as `integration_blocked` within a minute, and `orchestration.timeouts.max_quota_wait=300` (default 28800), so a quota wait escalates instead of hanging. `timeouts.stall`, `timeouts.tool`, the recovery backoff and `pr_wait` are left alone: a stall stop is behaviour the scenarios may exercise, the backoff is capped at 60 s and `pr_wait` only applies to the PR strategy.
- **Stall detector** (`tests/e2e/watchdog.py`): an attempt fails at once when no new event was recorded and no turn process (`turn.pid` of a running turn) was alive for 4 minutes. A stall or a budget timeout fails with the last event, the recent events, every task's status, step, holds and wait reason, `git status` of the operator checkout and the tail of `.shikiru/logs/engine.log`.
- **Reruns**: one automatic rerun, only for agent flakiness, judged from the attempt's diagnostics and never when the engine recorded an `engine_fault`: an agent wrote in the operator checkout (an `integration_waiting` event or a dirty checkout after the failure, which also covers the stall and timeout a blocked merge causes), or an agent skipped a rule so the delivered work is wrong (a plain assertion on the outcome). A Shikiru, lifecycle, CLI, MCP or cleanup failure, a sandbox denial (`Operation not permitted`, `was denied permission` or `Permission denied` in an agent transcript, which is a writable-roots configuration fault, 04 §5), and a stall or timeout without such evidence, fail without a rerun. E2E projects live in a private directory under `~/.cache/shikiru-e2e/`, never under `$TMPDIR` (Codex treats `$TMPDIR` as writable, which masked the denial), and the results artifact records the sandbox mode, the project parent, `$TMPDIR` and the Codex access settings of the host (`approvals_reviewer`, `approval_policy`, `default_permissions`, `sandbox_mode`).
- Every failed attempt writes `build/e2e/diagnostics/<date>-<scenario>-attempt-<n>.txt` (error, every event, task states, checkout status, log tail) and names the path in the failure, so a failed run is diagnosable without rerunning.
- `poe e2e` writes `build/e2e/<start UTC date and time>.json`, keyed by the invocation's start so a later or partial invocation never overwrites an earlier one, and copies it to `build/e2e/latest.json`. Per scenario it records pass/fail, the wall `duration_seconds`, the agent-reported cost (`null` when no agent reported one, otherwise the sum over its attempts) and `attempts`: every attempt with its status, `failure_class`, `rerun_reason`, cost, duration and diagnostics path. It also records the run's `source_commit`, `source_dirty` (uncommitted changes in the tree), `tool_versions` (claude, codex, tmux, git, python; `null` for a tool that is not installed) and `started_at`/`finished_at` (UTC). `build/` is git-ignored; the release summary of the last full passing invocation is committed as `docs/e2e-results.md` (a hand-copied excerpt of that artifact, refreshed with each release-readiness document).
- A plan AC that names an E2E scenario is ticked only after a passing `poe e2e` run; the ticking commit message states the run date, scenarios and cost.

| Scenario | Attempt budget | Measured duration (2026-10-01, one attempt each) |
|---|---|---|
| fake-agent operator flow | 45 s | not recorded; the seven tests took 1263.96 s together, 6 s more than the six real scenarios |
| E1 | 10 min | 118 s |
| E2 | 10 min | 190 s |
| E3 | 20 min | 378 s (the slowest) |
| E4 | 10 min | 176 s |
| E5 | 10 min | 113 s |
| E6 | 12 min | 284 s |

The durations are the artifact's `duration_seconds` of the invocation that started at 2026-10-01 07:59:03 UTC at `e78c057`: every scenario passed at its first attempt, the invocation took 21 m 03 s (`docs/e2e-results.md`, `docs/release-readiness-2026-10-01-phase7c.md`). They are one sample, not a distribution. The budgets are 2.5 to 5.3 times those values (E6 2.5, E2 to E4 3.2 to 3.4, E1 and E5 about 5), headroom for a slower agent turn and a flaky run; the 2026-09-30 invocation, which had no per-scenario record, took 24 m 52 s in all and 31 m 11 s with a rerun on the second. A run that exceeds its row is a finding, not a reason to raise the row unseen. The 2026-09-30 E3 stall (a Codex implementer also wrote the plan, tracker and test files into the operator checkout by absolute path, and the merge then waited the full 30 minutes for a clean checkout twice over) is the case the `integration_wait` setting, the `integration_waiting` event and the stall detector exist for. Shikiru cannot prevent an agent from writing outside its worktree (01 §6), so this is an agent-behaviour limit, and the diagnostics name it.

| # | Scenario | Proves |
|---|---|---|
| E1 | `submit "Implement task A1 from tasks/plan.md"` | Triage → implement → verify → merge; commit on `base`; A1 ticked; window closed; usage recorded for both agents; the project checkout stays clean and its base moves only by the merge |
| E2 | A task, plus an Operator `send_message` to the Leader while the Implementer's first turn runs, adding an edge-case requirement | The message delivered with the first verify prompt (queued, no hold), a changes_requested verdict citing it, final accept with the edge case handled |
| E3 | `submit "Implement all tasks in phase B"` with `max_parallel = 2` | Plan → dependent new tasks; two task windows at once; the conflict in todo.md resolved; all merged |
| E4 | An ambiguous request ("rounding mode is a product decision") | `decision_required` packet; an AI Operator resolves it via `shikiru mcp` (`wait_for_events` → `resolve_escalation`); task done |
| E5 | `SIGKILL` the engine after the implement `turn_started` event, then `start` | The orphaned turn process ended; the turn re-run on the same session id; it completes; no duplicate commits |
| E6 | Two parallel tasks; the Operator takes over T-1's implementer, then hands back | Only T-1 gets `intervention` and its pane runs the agent's TUI; T-2 completes; after hand back T-1 resumes the same session and completes |

**Covered by integration tests with the fake agent instead**:
- quota wait and resume, unavailable backoff, ladder moves;
- turn process exiting without a result, stall stop and resume, failure classification by the Helper, report rebuild;
- PR strategy, plan mode, remote attach argv, pane restoration, take over and hand back, queued `say`.

**Platforms and the clean-install smoke**:

`scripts/release-readiness-smoke.sh` installs the checkout into a disposable HOME and runs the CLI flows with the fake agent on a private tmux socket. It feature-detects the platform: util-linux `script -q -e -c` versus BSD `script -q FILE cmd` (`script --version`), `sha256sum` versus `shasum -a 256`, and a `PATH` made of the directories of `uv`, `tmux`, `git` and `python3` plus `getconf PATH`. Its scratch directory is `/tmp` unless `SHIKIRU_SMOKE_TMPDIR` is set: it must stay short because the private tmux socket path has to stay under about 100 characters (the OS limit is 104 on macOS and 108 on Linux), and the script refuses to run when it would not.

Each invocation writes its own `build/release-readiness/clean-install-<UTC date and time>-<pid>.{json,log}` (a failed rerun cannot overwrite a passing artifact) and, only when it passes, copies them to `clean-install-latest.{json,log}`. The artifact's `source_diff_fingerprint` hashes the tracked working-tree diff, the script, the wait helper and every `docs/release-readiness-*.md` and `docs/e2e-results.md`, so it follows the current evidence documents. A missing `uv`, `tmux`, `git`, `python3` or `script` stops the script with `missing tool: <name>`, and the temporary directory is removed on every exit, including the socket-path refusal.

**Linux verification recipe** (not yet run; follow it on a GNU/Linux host with util-linux `script`, tmux ≥ 3.4, git ≥ 2.45, uv and Python 3.14, outside any container that lacks a pty): `bash scripts/release-readiness-smoke.sh` must exit 0 and write `clean-install-latest.json` with `status: passed`. Then check the three Linux-specific points: (1) `script --version` prints util-linux, so the attach step runs `script -q -e -c '<attach command>' <log>`, in the background with stdin from `/dev/null`; the log `attach.log` is printed at the end and must show the attach and `[detached`; the step must not hang (if it does, `script` is reading the EOF of `/dev/null` as input: report it); (2) `source_diff_fingerprint` in the artifact is 64 hex characters (computed with `sha256sum`, no `shasum`); (3) `ls /tmp | grep shikiru-release-smoke` and `ps` show nothing left afterwards. Record the host (`uname -a`, `script --version`, `tmux -V`), the commit and the artifact path in the release-readiness document, and only then change the table row to "Verified".

| Platform | Status |
|---|---|
| macOS | Verified: the smoke passes; `poe check` runs here |
| GNU/Linux | The script is written for it but NOT verified by a run |

Linux notes from the code: process identity compares `psutil` start times with a tolerance because they derive from the boot time there and move when the clock is stepped (`ProcessIdentity.matches`); tmux ≥ 3.4 and git ≥ 2.45 apply on both platforms.
