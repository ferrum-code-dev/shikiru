# Todo: Muse Code agent plugin

Plan: [plan.md](plan.md). Each entry is a separate task, ordered by the dependencies in the plan report. Run `uv sync --locked` first in a fresh worktree and check that imports resolve to it. Each phase ends green (`PYTEST_ADDOPTS='--dist=loadfile' uv run poe check`) and uses conventional commits.

### Phase 0: captures and spikes (no code)

- [ ] 1. Capture redacted genuine exec --json streams in an empty scratch git repository

## Acceptance criteria
- Capture redacted genuine `exec --json` streams in an empty scratch git repository: first turn, resumed turn (`--session-id`), tool turn, stopped turn (`SIGTERM`), headless `--output-schema` result.

- [ ] 2. Capture the failure streams and stderr

## Acceptance criteria
- Capture the failure streams and stderr: rejected `META_API_KEY`, missing credentials (empty `XDG_CONFIG_HOME`), unknown model, step cap.

- [ ] 3. Capture Muse version, usage and model catalog

## Acceptance criteria
- Capture `muse --version`, `muse usage --json` (logged in and logged out) and a `model/list` reply.

- [ ] 4. Capture a successful Muse compaction

## Acceptance criteria
- Spike: force a successful compaction (long conversation, thresholds just above the 2.2% startup prompt) and capture the `context_compaction_installed` event from `session.jsonl`; record how a session id maps to the log path and whether `XDG_DATA_HOME` moves it.

- [ ] 5. Confirm the allowance window length

## Acceptance criteria
- Spike: confirm the allowance `window` length (5 hours?) from `usage/changed` `windowDurationMins`.

- [ ] 6. Verify turn prompt file availability

## Acceptance criteria
- Spike: check whether the engine's `prompt.md` is in `turn_dir` when a turn starts, and decide between reusing it and writing a prompt file.

- [ ] 7. Measure Muse doctor and catalog latency

## Acceptance criteria
- Measure `muse --version`, `muse usage --json` and the `muse serve` catalog round trip (cold and warm) to set the doctor and catalog timeouts.

- [ ] 8. Check default model handling

## Acceptance criteria
- Check how `claude` and `codex` treat the `default` model that non-interactive `init` writes, and decide Muse's behaviour to match.

- [ ] 9. Review wizard and plugin checks

## Acceptance criteria
- Read the wizard and `PluginChecks` flow once more against the plan's "Init, the wizard and doctor" section.

- [ ] 10. Document Muse fixtures

## Acceptance criteria
- Write `tests/fixtures/agents/muse/README.md` (version, date, what is redacted, what is synthetic).

### Phase 1: options and parser

- [ ] 11. Add Muse options and protocol constants

## Acceptance criteria
- `plugins/agents/muse/__init__.py`, `options.py`: constants (`muse`, `exec`, `--json`, `--session-id`, `--prompt-file`, `--output-schema`, flags, record types), `MuseOptions` (`args` default `--trust-workspace --disable-sandbox`, `compact_at_pct`, `billing_mode`), `MUSE_SETTINGS` from `shared_settings`.

- [ ] 12. Parse Muse turn streams

## Acceptance criteria
- `turn.py`: `MuseTurnParser.feed/finish` mapping records to signals (session, message, tools, usage, quota, terminal), malformed-line handling, interrupted turns, resumed usage accumulation.

- [ ] 13. Classify Muse failures and reset times

## Acceptance criteria
- Failure classification (auth, quota with reset selection, unavailable, other, unknown) as a pure function; reset selection through `ResetTimeParser`.

- [ ] 14. Test Muse stream parsing and failure rules

## Acceptance criteria
- Unit tests over the Phase 0 fixtures: one table per rule, including unrecognised text -> `UNKNOWN`.

### Phase 2: process sequence and compaction

- [ ] 15. Read Muse session logs

## Acceptance criteria
- `session_log.py`: locate the session log, read compaction and `model_completed` events, count compactions at turn start.

- [ ] 16. Run Muse turn processes

## Acceptance criteria
- `MuseTurnSequence.start/advance`: argv (`exec --json`, optional `--session-id`, `--model`, `--reasoning-effort`, compaction thresholds, `args`, `--prompt-file`), environment with `MUSE_NO_AUTO_UPDATE=1`, one retry after a context failure.

- [ ] 17. Report Muse compaction and context usage

## Acceptance criteria
- Compaction signals from the session log; `context_used_pct` from the prompt estimate.

- [ ] 18. Test Muse thresholds and session logs

## Acceptance criteria
- Unit tests for threshold-to-flag mapping and log parsing.

### Phase 3: agent, doctor, quota, interactive

- [ ] 19. Implement the Muse agent

## Acceptance criteria
- `agent.py`: `MuseAgent.start_turn` (`StreamingTurn.start`), `interactive_command`, `settings`, `billing_declaration`, `create` with `options_error`.

- [ ] 20. Add Muse installation and login checks

## Acceptance criteria
- `doctor([])` (the init/wizard detection gate): binary, version (warn off the tested `1.4.15`), login = `muse usage --json` exit 0 **or** non-empty `META_API_KEY` ("not verified by doctor"). Quick, bounded, deterministic; failing any check hides Muse from init and the wizard.

- [ ] 21. Check Muse models and compaction in doctor

## Acceptance criteria
- `doctor(models)`: add model checks against the catalog and the compaction threshold versus the startup prompt; no turn is run.

- [ ] 22. Expose and validate Muse settings

## Acceptance criteria
- Settings: exactly `compact_at_pct`, `billing_mode`, `args` (default `--trust-workspace --disable-sandbox`); `create()` rejects unknown keys via `options_error`.

- [ ] 23. Read Muse quota

## Acceptance criteria
- `read_quota`: `muse usage --json` -> `QuotaReading` (null allowance -> `None`).

- [ ] 24. Test Muse doctor and quota integration

## Acceptance criteria
- Integration tests with a fake `muse` executable on `PATH`: missing binary, logged out, logged in, quota probe.

### Phase 4: model catalog

- [ ] 25. Implement Muse model catalog transport

## Acceptance criteria
- `catalog.py`: one-shot `muse serve --disable-shell --disable-write --no-session-log` client (`initialize`, `initialized`, `model/list`), timeouts, clean shutdown, parse into `ModelCatalog`.

- [ ] 26. Expose the Muse model catalog

## Acceptance criteria
- `MuseAgent.model_catalog()` with caching and an error catalog on failure; `complete=True`, each model `offered`; ambiguous-`args` error like Codex; bounded by the measured timeout. The wizard lists an agent in ladders only when this succeeds, so this phase gates wizard support.

- [ ] 27. Test Muse model catalog discovery

## Acceptance criteria
- Tests: parser unit tests; integration test against the fake executable speaking the same lines.

### Phase 5: helper headless

- [ ] 28. Implement Muse headless calls

## Acceptance criteria
- `headless.py`: `MuseHeadless.ask` (prompt and schema in a temp directory, flags per the plan, timeout, parse the final JSON object, `HeadlessOutputError` on missing or invalid output).

- [ ] 29. Expose Muse headless support

## Acceptance criteria
- `MuseAgent.headless()` returns it; add the `PLR0917` ignore. The wizard offers an agent for the Helper scope only when this is non-`None`.

- [ ] 30. Test Muse headless calls

## Acceptance criteria
- Tests: success, invalid JSON, missing result, non-zero exit, via the fake executable.

### Phase 6: wiring, contract suite, docs

- [ ] 31. Register the Muse agent

## Acceptance criteria
- `pyproject.toml`: entry point, keyword, ruff ignore; confirm `uv run lint-imports` passes.

- [ ] 32. Add Muse agent contract tests

## Acceptance criteria
- `tests/integration/test_muse_headless_contract.py` on `AgentContractSuite`; adjust the suite for internal compaction if no real `installed` event could be captured (separate commit `test(agents): allow agents that compact internally`).

- [ ] 33. Update built-in agent tests

## Acceptance criteria
- Update `tests/integration/test_operator_plugin_checks.py` (settings defaults for `muse`) and any test enumerating the registered agents.

- [ ] 34. Test Muse setup and wizard integration

## Acceptance criteria
- Wizard/init integration tests with a fake `muse` on `PATH`: offered when installed and logged in or `META_API_KEY` only; absent when missing or logged out; selectable for Leader, Implementer and Helper; slow `muse serve` shows the catalog error; generated `[agents.muse]` section.

- [ ] 35. Document Muse design and trust boundaries

## Acceptance criteria
- `docs/design.md`: agent list, quota rule, compaction, trust boundary (sandbox disabled).

- [ ] 36. Document Muse prerequisites and live tests

## Acceptance criteria
- `README.md`, `CONTRIBUTING.md`: built-in agents, prerequisites (`muse login`), live-test constants.

### Phase 7: live verification

- [ ] 37. Run Muse live contract checks

## Acceptance criteria
- Live contract checks with a real `muse` (`SHIKIRU_E2E=1`, as for Codex): first turn writes a valid report, usage, stop leaves the session resumable, headless `ask`, compaction.

- [ ] 38. Verify Muse through the engine in tmux

## Acceptance criteria
- Opt-in e2e rung with a Muse ladder; verify an unattended run through the engine in tmux, including `interactive_command` take-over and a commit from a linked worktree.

- [ ] 39. Run the final project check

## Acceptance criteria
- `PYTEST_ADDOPTS='--dist=loadfile' uv run poe check` green on the final tree.

- [ ] 40. Replace synthetic failure captures when available

## Acceptance criteria
- Replace synthetic quota/overload fixtures with real captures when the first occurrence is seen.
