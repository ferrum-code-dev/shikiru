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
8. **No comments**: names explain. A comment that is genuinely needed carries the in-place exclusion marker `# keep: <reason>`; the reason is required. `uv run poe comments` (`tests/support/comment_rule.py`, part of `poe check`) tokenizes `src/`, `tests/` and `scripts/` Python files and fails on every comment other than a shebang or encoding line, a tool directive (`noqa`, `type: ignore`, `pyright:`, `pragma:`, `fmt:`, `ruff:`, `pylint:`) or a `keep:` comment with a non-empty reason. Shell scripts are not checked. Docstrings only on public contracts (`agents.contracts`, `DashboardWidget`, operator services, report and command models) and their implementations.
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
- Each scenario has a 30-minute timeout and one automatic rerun.
- `poe e2e` writes `build/e2e/<date>.json` (scenario, pass/fail, agent-reported cost or `null` when no agent reported one) together with the run's `source_commit`, `source_dirty` (uncommitted changes in the tree), `tool_versions` (claude, codex, tmux, git, python; `null` for a tool that is not installed) and `started_at`/`finished_at` (UTC). The file is temporary and git-ignored.
- A plan AC that names an E2E scenario is ticked only after a passing `poe e2e` run; the ticking commit message states the run date, scenarios and cost.

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

| Platform | Status |
|---|---|
| macOS | Verified: the smoke passes; `poe check` runs here |
| GNU/Linux | The script is written for it but NOT verified by a run |

Linux notes from the code: process identity compares `psutil` start times with a tolerance because they derive from the boot time there and move when the clock is stepped (`ProcessIdentity.matches`); tmux ≥ 3.4 and git ≥ 2.45 apply on both platforms.
