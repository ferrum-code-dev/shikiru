# 07 · Engineering

## 1. Stack

| Concern | Choice |
|---|---|
| Runtime | Python 3.14, uv (`uv tool install shikiru`) |
| CLI / TUI | Typer + Rich / Textual (pinned minor versions) |
| Validation | pydantic v2 |
| Config | `tomllib` |
| Templates | Jinja2 with `StrictUndefined` |
| DB | stdlib `sqlite3` in `store`, called via `asyncio.to_thread` from the engine |
| Metrics | psutil |
| MCP | official `mcp` SDK (FastMCP, stdio) |
| External | tmux ≥ 3.4, git ≥ 2.45; optional `gh`, `docker` |
| Quality | ruff (format + `select = ["ALL"]` with a short documented ignore list, `banned-api` rules from 02 §2), basedpyright `strict`, import-linter (02 §2 layers), vulture |
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
   - all enforced by lint.
5. **Values**: operator tunables go in `defaults.toml` (05 §1). Protocol constants are named module constants. No other literals.
6. **Async**: the engine is asyncio. Blocking work goes through `asyncio.to_thread` or async subprocesses.
7. **Errors**: a `ShikiruError` hierarchy with user-facing messages. The CLI maps errors to exit codes. No bare `except`, no silent fallbacks.
8. **No comments**: names explain. Docstrings only on public contracts (`agents.contracts`, `DashboardWidget`, operator services, report and command models) and their implementations.
9. **Commits**: conventional commits, one logical change each. Refactorings go in separate `refactor:` commits.
10. **Gates**:
    - `uv run poe check` (format, lint, imports, types, dead code, non-E2E tests with coverage) must pass before every commit.
    - `uv run poe e2e` runs real-agent scenarios.

## 3. Tests

| Layer | What | How |
|---|---|---|
| Unit | `domain` rules (transitions, `pick_rung`, `recovery.decide`, `pick_ready`, escalation options), report and command model validators, config merge, SDK parsers (`ResetTimeParser`, `PriceTable`), glyph width safety | Table-driven, no I/O |
| Integration | Everything else, against real SQLite, git, tmux and Textual, with the fake agent: store; workspace; tmux client, layout, guard and decorations; session manager, monitor and turn runner; task flow paths; integration strategies (`gh` faked on PATH); engine and handlers; reconciler; operator services; CLI; MCP (in-memory client); widgets (Textual `run_test`); plugin offline contract checks | Behaviour assertions on observable outcomes: DB rows, events, git history, files, tmux state |
| E2E | Real Claude Code and Codex | §5 |

**Test infrastructure** (`tests/support/`):
- **`tmux_server` fixture**: `tmux -L shikiru-test-<uuid> -f /dev/null` with a fixed `-x 200 -y 50`, `default-shell /bin/sh` and a clean env. Killed in teardown, unique per xdist worker.
- **`wait_until(predicate, timeout)`**: the only way to wait. No sleeps.
- **`FakeClock`**: injected into the engine, so timeouts, backoffs, quota waits and PR polls run instantly.
- **Fake agent plugin** (`tests/fake_agent/`, a test-only package with its own entry point): a line-reading REPL in a real pane that emits hook lines through `HookRecorder`.
  - It is scripted by `SHIKIRU_FAKE_SCRIPT` to: accept, changes_requested×N, needs_decision, no report, fail(kind, resets_at), crash, stall, dialog.
  - It runs the real `shikiru report` and commits files.
  - It passes `AgentContractSuite`.
- **In-process engine**: flow tests run `build_engine()` inside the test process, so coverage is measured directly. The subprocess patch covers `shikiru hook`/`report`/CLI.

**Meaningful tests, no snapshot spam**:
- Assert behaviour and properties: rendered width ≤ available columns; every state shows a word or glyph under `NO_COLOR`; a packet has all sections and exactly one ★; a template contains the request and the report instruction, and `StrictUndefined` raises on missing fields.
- Golden snapshots are limited to one strip per mode at two widths.
- No tests of private functions or mocks of our own classes: fake only process boundaries (agents, `gh`).
- Each test name states the behaviour (`test_rejected_twice_moves_implementer_up_one_rung`).

## 4. Agent samples

Plugin offline checks use real agent files committed under `tests/fixtures/agents/<agent>/`: hook payloads, transcript and rollout excerpts, error entries, quota probe output. They are captured once from real runs. When an agent CLI changes format, add new samples next to the old ones.

## 5. E2E scenarios (`poe e2e`, `SHIKIRU_E2E=1`)

**Fixture project** `tests/fixtures/sample-project`:
- a tiny Python package with pytest;
- `CLAUDE.md`/`AGENTS.md` telling agents to test, commit and tick `tasks/todo.md`;
- `tasks/plan.md` with small tasks. Phase B's todo items are **adjacent lines**, so parallel tasks force a rebase conflict.
- It is one git repository; multi-repo projects are out of scope (D20).

**Runs**:
- Each scenario copies the fixture, runs `init --yes --set` with the cheapest rungs, `start`, and asserts only observable outcomes.
- Each scenario has a 30-minute timeout and one automatic rerun.
- `poe e2e` writes `tests/e2e/results/<date>.json` (scenario, pass/fail, cost).
- A plan AC that names an E2E scenario is ticked only with a passing results file committed.

| # | Scenario | Proves |
|---|---|---|
| E1 | `submit "Implement task A1 from tasks/plan.md"` | Triage → implement → verify → merge; commit on `base`; A1 ticked; window closed; usage recorded for both agents; the project checkout stays clean and its base moves only by the merge |
| E2 | A task whose AC needs an edge case, plus an Operator `send_message` to the Implementer mid-turn | ≥ 1 changes_requested loop, the message delivered without tripping `intervention`, final accept |
| E3 | `submit "Implement all tasks in phase B"` with `max_parallel = 2` | Plan → dependent new tasks; two task windows at once; the conflict in todo.md resolved; all merged |
| E4 | An ambiguous request ("rounding mode is a product decision") | `decision_required` packet; an AI Operator resolves it via `shikiru mcp` (`wait_for_events` → `resolve_escalation`); task done |
| E5 | `SIGKILL` the engine after the implement `turn_started` event, then `start` | Session attached or resumed with the same id; turn completes; no duplicate commits |
| E6 | Two parallel tasks; the Operator types into T-1's implementer pane | Only T-1 gets `intervention`; T-2 completes; resuming T-1 completes it |

**Covered by integration tests with the fake agent instead**:
- quota wait and resume, unavailable backoff, ladder moves;
- pane death, stall with dialog, report rebuild;
- PR strategy, plan mode, remote attach argv, pane restoration.
