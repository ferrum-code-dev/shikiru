# 07 · Engineering

## 1. Stack

| Concern | Choice |
|---|---|
| Runtime | Python 3.14, uv (`uv tool install .` from the Shikiru checkout) |
| CLI / TUI | Typer + Rich / Textual (pinned minor versions) |
| Validation | pydantic v2 |
| Config | `tomllib` |
| Templates | Jinja2 with `StrictUndefined` |
| DB | stdlib `sqlite3` in `store`, called synchronously from the engine loop (§2 item 6) |
| Metrics | psutil |
| MCP | official `mcp` SDK (FastMCP, stdio) |
| External | tmux ≥ 3.4, git ≥ 2.45 (enforced by `doctor`, `TMUX_MINIMUM_VERSION` and `GIT_MINIMUM_VERSION`); optional `gh`, `docker` |
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
5. **Values**: operator tunables go in `defaults.toml` (05 §1); protocol values and implementation constants are named module constants. `uv run poe literals` (`tests/support/literal_rule.py`, part of `poe check`) parses `src/` and `tests/support/` with `ast` and fails on:
   - a numeric literal other than 0, 1 and -1, in any position (slices, indexes and default arguments included), unless it is the value of a module-level assignment (the named constant), an enum member, or inside a type annotation; a field default is allowed only as a reference to a named constant;
   - a string literal that is an operand of `==`, `!=`, `in` or `not in` (except the `__name__ == "__main__"` guard), an element of an argv list or tuple passed as the first argument of `run`, `spawn`, `spawn_detached` or `replace` or as `argv=`, or the value of a `kind=`, `event=` or `type=` argument.

   Out of scope by design: docstrings, f-string text, user-facing and log messages, type annotations, string `match` patterns (the type checker verifies them against their `Literal`), dictionary keys, subscripts and `.get` arguments (the keys of a row or payload are read next to the SQL or model that defines them; naming each is the indirection F22 criticised), argv words passed as separate arguments to a wrapper that owns the CLI (`Tmux`, `Git`), and values in `defaults.toml`. The exclusion is the in-place marker `# keep: <reason>` on a line of the literal, reason required; a marker without a reason excludes nothing. `poe literals` prints how many literals the marker excluded, so exceptions stay visible. The rule buys one place to read each protocol value and tunable, a mechanical answer to "is this number a tunable?", and no reviewer judgement; it does not decide what deserves a name beyond those positions, whether a tunable belongs in `defaults.toml` (an operator-relevant value does, an implementation timing is a named constant) or whether a name is good. The no-comments rule in item 8 is checked the same way.
6. **Async**: the engine is asyncio. Subprocesses are async (`process.SubprocessRunner`) and long filesystem work (cache removal, sandbox probe) goes through `asyncio.to_thread`. `store` and the `workspace` file operations are called synchronously from the loop, by design: the engine is the only writer process, a transaction is one short local statement batch, and the `ASYNC240` ignore for `workspace/**` states the same for filesystem calls. The cost is bounded: a write lock held by another process (a CLI command, an external tool) stalls the loop for at most `busy_timeout` (5 s, 05 §2) per call, after which `OperationalError` is a recoverable fault the loop guards retry on backoff (02 §1). The soak run (`docs/soak-results.md`, db_lock faults held for 2 to 8 s) found no stall-related loss once those guards existed (Task 74u): turn processes and streams keep running while the loop waits, and the stall timeouts are minutes, not seconds. Moving every call to `to_thread` would add a thread hop to each of the hundreds of small reads per tick without removing the lock wait, so it is not done.
7. **Errors**: a `ShikiruError` hierarchy with user-facing messages. The CLI maps errors to exit codes. No bare `except`, no silent fallbacks.
8. **No comments**: names explain. A comment that is genuinely needed carries the in-place exclusion marker `# keep: <reason>`; the reason is required. `uv run poe comments` (`tests/support/comment_rule.py`, part of `poe check`) tokenizes `src/`, `tests/` and `scripts/` Python files and fails on every comment other than a shebang or encoding line, a tool directive in exactly one of these forms (anything else after the directive, such as free prose, is rejected): `noqa: CODE[, CODE]` optionally followed by ` -- <reason>`, `type: ignore[rules]`, `pyright: ignore[rules]` (or `basic`/`standard`/`strict`), `pragma: no cover` or `no branch`, `fmt: off`/`on`/`skip` or a `keep:` comment with a non-empty reason. Shell scripts are not checked. Docstrings only on public contracts (`agents.contracts`, `DashboardWidget`, operator services, report and command models) and their implementations.
9. **Commits**: conventional commits, one logical change each. Refactorings go in separate `refactor:` commits.
10. **Gates**:
    - `uv run poe check` (format, lint, comments, literals, imports, types, dead code, non-E2E tests with coverage) must pass before every commit.
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
- Responsive Textual integrations exercise actual pane sizes and verify visible/focusable task identity, actions, results, forms, and preserved selection, values and reading position across resize and state refresh. Visual review inspects rendered artifacts from these flows; do not turn them into golden screenshot tests.
- [`docs/workspace-walkthroughs-2026-10-03.md`](../workspace-walkthroughs-2026-10-03.md) records the eight Task 87 executions, their source revision, commands, fixtures and observed results. It labels connected fake-agent engine runs separately from seeded SQLite UI-boundary checks; it is not evidence for real-agent E1–E6.
- [`docs/release-readiness-2026-10-04-phase9.md`](../release-readiness-2026-10-04-phase9.md) records the Task 96 connected fake-agent operator walkthroughs and separate seeded usage projections, alongside full release verification. It preserves the earlier Phase 8 walkthrough and release records.
- [`docs/release-readiness-2026-10-05-phase10.md`](../release-readiness-2026-10-05-phase10.md) records the final Focus correction walkthroughs, source-attributed native review, normal and `TERM=dumb` gates, macOS clean-install/navigation smoke, platform limits and bounded hygiene audit.
- [`docs/release-readiness-2026-10-09-phase11-third-review.md`](../release-readiness-2026-10-09-phase11-third-review.md) records connected engine/title/pane decoration evidence separately from seeded UI boundary tests, final-source native captures, complete normal and monochrome gates, the macOS clean-install/attach smoke, and their limits.
- Keep named layout bounds in the UI implementation and describe the supported compact forms in the operator design. Add no layout configuration until there is a concrete Operator choice.
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

| Scenario | Attempt budget | Passing duration (2026-10-02, `159db49`) | Passing duration (2026-10-03, `01ab54e`) | Quota-limited attempt (2026-10-03, `0734ef3`) |
|---|---|---|---|---|
| fake-agent operator flow | 45 s | not recorded; the seven tests took 1468.61 s together | not recorded; the seven tests took 1284.00 s together | not selected by the real-agent artifact |
| E1 | 10 min | 104 s | 83.9 s | 11.1 s over two attempts; quota exhaustion |
| E2 | 10 min | 175 s | 182.2 s | 246.8 s; quota exhaustion then watchdog stall |
| E3 | 20 min | 563 s over two attempts (157 s failed, 406 s passed; the slowest) | 346.3 s | 246.3 s; quota exhaustion then watchdog stall |
| E4 | 10 min | 186 s | 189.7 s | 11.2 s over two attempts; quota exhaustion |
| E5 | 10 min | 109 s | 167.3 s | 251.9 s; quota exhaustion then watchdog stall |
| E6 | 12 min | 327 s | 308.6 s | 247.1 s; quota exhaustion then watchdog stall |

The 2026-10-03 durations in the table preserve the earlier passing invocation at `01ab54e`; they are historical samples, not the newest accepted run or a distribution. The 2026-10-02 invocation at `159db49` remains the previous passing sample: E3 failed its first attempt (the leader triaged the request as one implement task instead of a plan) and passed on its one rerun; the 2026-10-01 `d5624af` invocation did the same, and an E3-only A/B showed the cause is not the Task 74j prompt sentence. The additional quota-limited 2026-10-03 durations at `0734ef3` are failure-to-quota times, not successful scenario measurements; their costs were null. A single passing attempt took at most 406 s in the 2026-10-02 run (E3), against budgets of 10, 12 and 20 minutes; the 2026-10-03 E3 attempt took 346.3 s. A run that exceeds its row is a finding, not a reason to raise the row unseen. The 2026-09-30 E3 stall (a Codex implementer also wrote the plan, tracker and test files into the operator checkout by absolute path, and the merge then waited the full 30 minutes for a clean checkout twice over) is the case the `integration_wait` setting, the `integration_waiting` event and the stall detector exist for. Shikiru cannot prevent an agent from writing outside its worktree (01 §6), so this is an agent-behaviour limit, and the diagnostics name it.

The newest accepted full E1–E6 evidence is the 2026-10-04 invocation at [`20261004T114936Z.json`](../../build/e2e/20261004T114936Z.json), summarized in the [invocation record](../e2e-results.md). It ran from 11:49:36.516 to 12:12:48.020 UTC (23 m 11.5 s; pytest reported 1,391.98 s), with source `d49791c269ceef683c8a3155593d3f5ec9b5825d` and `source_dirty=true`; the uncommitted delta was documentation and the clean-install smoke selector, not application code. E1–E5 passed at attempt 0. E6's one rule-skip failure was diagnosed, then its single permitted retry passed; all six scenarios passed. Every reported cost was null, so cost is unknown, not zero. Leader, Implementer and Helper used Codex `gpt-6-luna` at low effort; E4 used a scripted MCP operator. This proves that invocation's dirty source state; later commit `c4b8a06` is not its source revision. The initial 2026-10-04 setup failure and E6 diagnostic remain preserved in the linked record.

Task 121 was accepted by root on 2026-10-08. Its 2026-10-08 E2E evidence is a partial E3-only invocation, not a new full E1–E6 result. The E3 node passed once on clean source `dd8b49bafad2701fbcd621038c70a7d14c0260f2`; its artifact, diagnosed earlier selector-environment failure and reproduction command are in [`docs/e2e-results.md`](../e2e-results.md) and the [Phase 11 second-review record](../release-readiness-2026-10-08-phase11-followup.md). Existing budgets, watchdog and rerun policy were unchanged.

The [Phase 11 third-review record](../release-readiness-2026-10-09-phase11-third-review.md) records accepted Task 128 verification: complete normal and monochrome quality gates, connected fake-agent and clean-install evidence, source-matched native captures, and limits. No real-provider E1–E6 rerun was made: the third-review implementation adds normalized tool outcome facts and changes TUI/tmux chrome, popups and layout, without changing agent execution, recovery, quotas or budgets. The fake-agent smoke is not real-provider E2E evidence; the earlier E3 remains specific to its own source and run.

| # | Scenario | Proves |
|---|---|---|
| E1 | `submit "Implement task A1 from tasks/plan.md"` | Triage → implement → verify → merge; commit on `base`; A1 ticked; window closed; usage recorded for both agents; the project checkout stays clean and its base moves only by the merge |
| E2 | A task, plus an Operator `send_message` to the Leader while the Implementer's first turn runs, adding an edge-case requirement | The message delivered with the first verify prompt (queued, no hold), a changes_requested verdict citing it, final accept with the edge case handled |
| E3 | `submit "Implement all tasks in phase B"` with `max_parallel = 2` | Plan → dependent new tasks; two task windows at once; the conflict in todo.md resolved; all merged |
| E4 | An ambiguous request ("rounding mode is a product decision") | `decision_required` packet; an AI Operator resolves it via `shikiru mcp` (`wait_for_events` → `resolve_escalation`); task done |
| E5 | `SIGKILL` the engine after the implement `turn_started` event, then `start` | The orphaned turn process ended; the turn re-run on the same session id; it completes; no duplicate commits |
| E6 | Two parallel tasks; the Operator takes over T-1's implementer, then hands back | Only T-1 gets `intervention` and its pane runs the agent's TUI; T-2 completes; after hand back T-1 resumes the same session and completes |

**Soak** (`uv run poe soak [--seed S] [--budget-seconds N] [--mode merge|pr]`, not part of `poe check`; evidence in `docs/soak-results.md`):
- One seeded, wall-clock run of the real `shikiru engine` (started by `shikiru start`, real SQLite, git and tmux on a private socket, the fake agent, a fake `gh` and a bare remote for `pr` mode) over two *arenas*, one per integration mode, side by side. The seed is printed first; the submissions (task kinds: plain, rejected, rounds exhausted, auth and other failures, decision needed, plan, supersede, uncommitted work; dependencies and priorities), the operator operations (`say`, `prioritize`, `pause`/`resume`, `set_rung`, `take_over`/`hand_back`, `cancel`, `retry`, plus every escalation resolved) and the fault schedule are all drawn from it and printed, so a failure replays the same schedule (the timing of the real processes stays real). `tests/soak/` holds one small class per concern: `scenarios` (the schedule), `chaos` (the injector), `invariants` (the checker), `reporter`, `hosting` (the pull request service behind the fake `gh`), `supervisor` (restarts the engine and flags an exit nobody caused), `operators` and `run`/`driver` (one arena's life; the arenas under one budget).
- **Faults**: SIGKILL of the engine (also timed to the first integration and the first new escalation), SIGTERM, SIGINT, `stop` then `start`, SIGSTOP/SIGCONT for a few seconds, loss of the tmux server, SIGKILL of a turn process group, a write lock held on the database from another connection for seconds (shorter and longer than the 5 s busy timeout), `gh pr create|view|list|close` failing for a few calls (`fail_next`), one failing reference update of the engine (a `reference-transaction` hook that spares agents) a notification hook stuck or failing for seconds, the pull request remote unreachable, and the engine killed and then the database locked. Task kinds also include unavailable and quota failures, a stalled turn and a turn that never reports. A status probe runs `shikiru --json status` (project and one task) every few seconds and any failure is a finding. A full disk is not simulated.
- **Invariants**, asserted from durable state, git and the process table, with the seed, the schedule and the offending task in the report. During the run, after the engine has been stable for a few seconds: (1) no task has agent processes of two process groups in its worktree, two live recorded turns, or a headless turn beside a taken-over session; (2) no turn process starts in a task that has been held for several samples; (5) no command stays pending, no `escalated` task lacks an open escalation row and packet, no open escalation lacks an `escalated` task, and every `escalation` and `engine_fault` event reached the hook. At the end: (3) every `done` task's commits are in the base (or, in `pr` mode, its change is on the remote base) and a cancelled task with committed work keeps its branch or has it on the base; (4) every task's base commit contains the work of each done dependency; (5) every issued command is `done` or `rejected` with a reason, every task is terminal or an explained open escalation (or waits on one), no engine fault was unrecoverable, the engine never exited unexpectedly or stopped on a fault, and after `stop --kill-session` no process of the arena, no tmux server and no temporary file is left.
- The default budget is 480 s: 60 % submissions and faults, the rest draining (the run ends as soon as every task is terminal). A run that finds a violation keeps its directory and prints it; a clean one removes everything it started, by process group, and the driver checks the process table afterwards. `tests/integration/test_soak_smoke.py` runs a seconds-long variant (three tasks, one engine kill, fixed seed) inside `poe check`.

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

Linux notes from the code: process identity compares `psutil` start times with a tolerance because they derive from the boot time there and move when the clock is stepped (`ProcessIdentity.matches`); tmux ≥ 3.4 and git ≥ 2.45 apply on both platforms. The floors are chosen, not measured: all evidence (checks, smoke, E2E, soak) ran on tmux 3.7c and git 2.55.0, no older release was available to test, so a floor is the oldest release we expect to carry the features used, not a tested minimum; `doctor` refuses to start below them so an untested older version is a visible choice of the operator, not a silent one.
