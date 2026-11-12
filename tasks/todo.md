# Todo: Muse Code agent plugin

Plan: [plan.md](plan.md). Each phase ends green (`PYTEST_ADDOPTS='--dist=loadfile' uv run poe check`)
and is one or more conventional commits. Run `uv sync --locked` first in a fresh worktree and check that
imports resolve to it.

## Phase 0: captures and spikes (no code)

- [ ] Capture redacted genuine `exec --json` streams in an empty scratch git repository: first turn, resumed
      turn (`--session-id`), tool turn, stopped turn (`SIGTERM`), headless `--output-schema` result.
- [ ] Capture the failure streams and stderr: rejected `META_API_KEY`, missing credentials (empty
      `XDG_CONFIG_HOME`), unknown model, step cap.
- [ ] Capture `muse --version`, `muse usage --json` (logged in and logged out) and a `model/list` reply.
- [ ] Spike: force a successful compaction (long conversation, thresholds just above the 2.2% startup
      prompt) and capture the `context_compaction_installed` event from `session.jsonl`; record how a
      session id maps to the log path and whether `XDG_DATA_HOME` moves it.
- [ ] Spike: confirm the allowance `window` length (5 hours?) from `usage/changed` `windowDurationMins`.
- [ ] Spike: check whether the engine's `prompt.md` is in `turn_dir` when a turn starts, and decide
      between reusing it and writing a prompt file.
- [ ] Write `tests/fixtures/agents/muse/README.md` (version, date, what is redacted, what is synthetic).

## Phase 1: options and parser

- [ ] `plugins/agents/muse/__init__.py`, `options.py`: constants (`muse`, `exec`, `--json`,
      `--session-id`, `--prompt-file`, `--output-schema`, flags, record types), `MuseOptions`
      (`args` default `--trust-workspace --disable-sandbox`, `compact_at_pct`, `billing_mode`),
      `MUSE_SETTINGS` from `shared_settings`.
- [ ] `turn.py`: `MuseTurnParser.feed/finish` mapping records to signals (session, message, tools, usage,
      quota, terminal), malformed-line handling, interrupted turns, resumed usage accumulation.
- [ ] Failure classification (auth, quota with reset selection, unavailable, other, unknown) as a pure
      function; reset selection through `ResetTimeParser`.
- [ ] Unit tests over the Phase 0 fixtures: one table per rule, including unrecognised text -> `UNKNOWN`.
- [ ] Commit: `feat(agents): parse Muse Code turn streams`.

## Phase 2: process sequence and compaction

- [ ] `session_log.py`: locate the session log, read compaction and `model_completed` events, count
      compactions at turn start.
- [ ] `MuseTurnSequence.start/advance`: argv (`exec --json`, optional `--session-id`, `--model`,
      `--reasoning-effort`, compaction thresholds, `args`, `--prompt-file`), environment with
      `MUSE_NO_AUTO_UPDATE=1`, one retry after a context failure.
- [ ] Compaction signals from the session log; `context_used_pct` from the prompt estimate.
- [ ] Unit tests for threshold-to-flag mapping and log parsing.
- [ ] Commit: `feat(agents): run Muse Code turns and report compaction`.

## Phase 3: agent, doctor, quota, interactive

- [ ] `agent.py`: `MuseAgent.start_turn` (`StreamingTurn.start`), `interactive_command`, `settings`,
      `billing_declaration`, `create` with `options_error`.
- [ ] `doctor`: binary, version (warn off the tested `1.4.15`), login via `muse usage --json`, model
      checks against the catalog, compaction threshold versus startup prompt.
- [ ] `read_quota`: `muse usage --json` -> `QuotaReading` (null allowance -> `None`).
- [ ] Integration tests with a fake `muse` executable on `PATH`: missing binary, logged out, logged in,
      quota probe.
- [ ] Commit: `feat(agents): add the Muse Code agent plugin`.

## Phase 4: model catalog

- [ ] `catalog.py`: one-shot `muse serve --disable-shell --disable-write --no-session-log` client
      (`initialize`, `initialized`, `model/list`), timeouts, clean shutdown, parse into `ModelCatalog`.
- [ ] `MuseAgent.model_catalog()` with caching and an error catalog on failure.
- [ ] Tests: parser unit tests; integration test against the fake executable speaking the same lines.
- [ ] Commit: `feat(agents): list Muse Code models and efforts`.

## Phase 5: helper headless

- [ ] `headless.py`: `MuseHeadless.ask` (prompt and schema in a temp directory, flags per the plan,
      timeout, parse the final JSON object, `HeadlessOutputError` on missing or invalid output).
- [ ] `MuseAgent.headless()` returns it; add the `PLR0917` ignore.
- [ ] Tests: success, invalid JSON, missing result, non-zero exit, via the fake executable.
- [ ] Commit: `feat(helper): run the helper on Muse Code`.

## Phase 6: wiring, contract suite, docs

- [ ] `pyproject.toml`: entry point, keyword, ruff ignore; confirm `uv run lint-imports` passes.
- [ ] `tests/integration/test_muse_headless_contract.py` on `AgentContractSuite`; adjust the suite for
      internal compaction if no real `installed` event could be captured (separate commit
      `test(agents): allow agents that compact internally`).
- [ ] `docs/design.md`: agent list, quota rule, compaction, trust boundary (sandbox disabled).
- [ ] `README.md`, `CONTRIBUTING.md`: built-in agents, prerequisites (`muse login`), live-test constants.
- [ ] Commit: `docs(agents): document the Muse Code agent`.

## Phase 7: live verification

- [ ] Live contract checks with a real `muse` (`SHIKIRU_E2E=1`, as for Codex): first turn writes a
      valid report, usage, stop leaves the session resumable, headless `ask`, compaction.
- [ ] Opt-in e2e rung with a Muse ladder; verify an unattended run through the engine in tmux, including
      `interactive_command` take-over and a commit from a linked worktree.
- [ ] `PYTEST_ADDOPTS='--dist=loadfile' uv run poe check` green on the final tree.
- [ ] Replace synthetic quota/overload fixtures with real captures when the first occurrence is seen.
