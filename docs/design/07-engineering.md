# 07 · Engineering

## 1. Stack

| Concern | Choice |
|---|---|
| Runtime | Python 3.14 (`asyncio.TaskGroup`, `type` aliases, PEP 649 annotations), packaged with **uv** (`uv tool install shikiru`) |
| CLI | Typer + Rich |
| TUI | Textual (dashboard, palette, inline wizard and pickers) |
| Models / validation | pydantic v2 |
| Config | `tomllib` (read), `tomlkit` (edit) |
| Templates | Jinja2 (`StrictUndefined`, autoescape off) |
| DB | stdlib `sqlite3` via `asyncio.to_thread` in the engine; SQL migrations |
| System metrics | psutil |
| MCP | `mcp` (official SDK, FastMCP, stdio) |
| External binaries | tmux ≥ 3.4, git ≥ 2.45, optional `gh`, `docker`, `terminal-notifier`/`notify-send` |
| Lint / format | ruff (`select = ["ALL"]` with a short, documented ignore list; format) |
| Types | basedpyright `strict` |
| Dead code / deps | vulture (min-confidence 80), deptry |
| Tests | pytest, pytest-asyncio, pytest-cov (`fail_under = 80`), pytest-timeout |

## 2. Conventions

- **SOLID**: one responsibility per module, following the component table in 02-architecture §2.
  - Extension goes through protocols and entry points.
  - Constructors take their dependencies (no globals or singletons). The only composition root is `engine/daemon.py::build_engine()` or `cli/app.py::build_service()`.
- **No god objects**: a class over ~200 lines or with more than 7 public methods gets split. Steps, adapters and widgets stay small.
- **No agent- or environment-specific literals** in the core: model names, flags, paths, keys and timeouts live in `defaults.toml` or in adapters.
- **No comments.** Clear names instead. Docstrings only on public contracts (`contracts.py`, `DashboardWidget`, `OperatorService`, report models) and on their implementations.
- **Pure domain**: `domain/` has no I/O and is unit-tested.
- **Subprocesses**: argv lists only, never `shell=True`. Every call goes through one of `tmux.client`, `workspace.git`, or `SubprocessRunner` (timeouts, decoded output, typed errors).
- **Errors**: a `ShikiruError` hierarchy with user-facing messages. The CLI maps it to exit codes. No bare `except`.
- **Commits**: conventional commits. Refactorings go in separate `refactor:` commits.
- **Quality gate** (`uv run poe check`): `ruff format --check`, `ruff check`, `basedpyright`, `vulture`, `deptry`, `pytest -m "not e2e"` with coverage. It must be green before every commit made by an agent.

## 3. Test strategy

| Layer | Scope | Real dependencies | Share |
|---|---|---|---|
| Unit | `domain/` (transitions, ladder, scheduling, escalation policy, holds), report validators, config merge and overrides, reset-time and telemetry parsing against recorded fixtures | none | small |
| Integration | store and migrations; workspace (real git repos in tmp, CoW clone); integration merge/conflict/PR (PR through a fake `gh` on PATH); tmux client, layout, protection and window lifecycle on a **private socket**; `SignalInbox` and hook/report CLIs; engine flow with the **fake agent**; CLI commands; MCP tools through an in-memory client; dashboard widgets through Textual `run_test()` snapshots | git, tmux, sqlite, Textual | most |
| E2E | Full `shikiru` with real Claude Code and Codex on a fixture project | everything | scenarios §5 |

**Isolation rules** (lessons from v4):
- Tests never touch the Operator's tmux server. `SHIKIRU_TMUX_SOCKET` (test-only env var, read by `tmux.client`) points at `tmux -L shikiru-test-<uuid>`, which is killed in teardown.
- Tests never touch the real `~/.claude` or `~/.codex` state, except E2E, which uses real agents by design.
- Every test has a timeout (default 60 s, E2E 60 min). No wall-clock sleeps: wait on conditions with deadlines.

**Fake agent** (`tests/fixtures/fake_agent/`):
- A small line-reading REPL, launched in a real pane like any agent, so pasted pointers arrive on stdin. It is registered as the `fake` agent plugin in the test environment.
- It reads scripted behaviours from `SHIKIRU_FAKE_SCRIPT`: accept, changes_requested×N, needs_decision, no-report, quota(resets_at), crash, stall, dialog.
- It writes hook events in real Claude payload format, runs the real `shikiru report`, and edits and commits files.
- It proves the Open-Closed contract and makes every engine path deterministic.

## 4. Recorded fixtures

- `doctor --probe` and E2E runs save hook payloads, statusline JSON, transcript and rollout tails and Helper responses under `tests/fixtures/recorded/<agent>/<version>/`.
- Parser unit tests run against every recorded version. Upgrading a CLI means recording a new version, not rewriting parsers blindly.

## 5. E2E scenarios (`pytest -m e2e`, requires `SHIKIRU_E2E=1`, logged-in agents)

**Fixture project** `tests/fixtures/sample-project`:
- A tiny Python package `calc` with pytest.
- `CLAUDE.md` and `AGENTS.md` telling agents to run tests, commit, and tick items in `tasks/todo.md`.
- `tasks/plan.md` with Phases A–B and 5 small tasks with ACs.
- A copied `node_modules`-like cache dir to prove CoW cloning.
- A second repo variant for multi-repo.

Each scenario copies the fixture to a tmp dir, runs `shikiru init --yes` with cheap rungs (`--set` to low-cost models), `start`, and asserts on observable outcomes only: git history, tracker files, `shikiru show --json`, events, the tmux session state.

| # | Scenario | Proves |
|---|---|---|
| E1 | `submit "Implement task A1 from tasks/plan.md"` → done | Triage → implement → verify → merge. Commit on `main`, A1 ticked in todo.md, window closed, `shikiru` window restored, cost and tokens recorded for both agents |
| E2 | Task whose AC requires an edge case the Implementer is told (via `say`) to skip at first | `changes_requested` loop ≥ 2 iterations; `say` message delivered; final accept |
| E3 | `submit "Implement all tasks in phase B"` with `max_parallel = 2` | Split → children with dependencies; two task windows in parallel; dependency order respected; rebase conflict in todo.md resolved by the Implementer; all merged |
| E4 | `submit "Plan tasks for adding a power operation"` | Plan mode: plan.md updated and merged; `new_tasks` enqueued when asked |
| E5 | Request with a deliberate product ambiguity ("choose rounding mode per product decision") | `decision_required` escalation packet complete; an AI Operator resolves it through `shikiru mcp` (`wait_for_events` → `resolve_escalation`); task completes |
| E6 | Kill the engine mid-implement (`SIGKILL`), then `start` | Reconcile: same session id resumed, turn completes, no duplicate commits |
| E7 | Kill the implementer pane process mid-turn | Pane death → resume with the same session id → completes |
| E8 | Manual intervention: type into the implementer pane through `send-keys` as an Operator | `intervention` hold set, no nudges, `resume` continues, completes |
| E9 | `attach --host localhost` (sshd on the CI host) and `ls --host` | Remote listing and attach command construction; the attached client sees the session |
| E10 | Monorepo vs multi-repo variants of E1 | Topology agnostic: worktree mirroring and per-repo merges |
| E11 | `doctor --probe` for both agents | Launch, report, resume, telemetry, no dialogs |

Quota exhaustion, overload, stall with dialog, ladder moves and budget escalation can't be triggered on demand with real agents. They are covered by fake-agent integration tests driven by recorded real failure payloads.
