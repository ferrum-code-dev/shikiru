# Todo: Muse Code agent plugin

Plan: [plan.md](plan.md). Each entry is a separate task, ordered by the dependencies in the plan report. Run `uv sync --locked` first in a fresh worktree and check that imports resolve to it. Each phase ends green (`PYTEST_ADDOPTS='--dist=loadfile' uv run poe check`) and uses conventional commits.

### Phase 0: captures and spikes (no code)

- [x] 1. Capture redacted genuine exec --json streams in an empty scratch git repository

## Acceptance criteria
- Capture redacted genuine `exec --json` streams in an empty scratch git repository: first turn, resumed turn (`--session-id`), tool turn, stopped turn (`SIGTERM`), headless `--output-schema` result.

- [x] 2. Capture the failure streams and stderr

## Acceptance criteria
- Capture the failure streams and stderr: rejected `META_API_KEY`, missing credentials (empty `XDG_CONFIG_HOME`), unknown model, step cap.

- [x] 3. Capture Muse version, usage and model catalog

## Acceptance criteria
- Capture `muse --version`, `muse usage --json` (logged in and logged out) and a `model/list` reply.

- [x] 4. Capture a successful Muse compaction

## Acceptance criteria
- Spike: force a successful compaction (long conversation, thresholds just above the 2.2% startup prompt) and capture the `context_compaction_installed` event from `session.jsonl`; record how a session id maps to the log path and whether `XDG_DATA_HOME` moves it.

- [x] 5. Confirm the allowance window length

## Acceptance criteria
- Spike: confirm the allowance `window` length (5 hours?) from `usage/changed` `windowDurationMins`.
- Confirmed: `windowDurationMins` is `300` (5 hours). The Muse SDK project recorded a `usage/changed` observation of a 300-minute window after a short turn ([issue #44](https://github.com/meta-models/muse-code-sdk/issues/44)); the protocol schema describes it as the provider's 5-hour-class block ([field reference](https://meta-models.github.io/muse-code-sdk/next/generated/msp/types/subscriptionusagewindow/)). Local reproduction was unavailable because this Muse CLI session is not logged in.

- [x] 6. Verify turn prompt file availability

## Acceptance criteria
- Spike: check whether the engine's `prompt.md` is in `turn_dir` when a turn starts, and decide between reusing it and writing a prompt file.
- Confirmed: `TurnManager._prepare_turn_dir` writes `turn_dir/prompt.md` before `TurnManager.run` invokes `on_start`; the agent's `start_turn` runs afterward in `_attempts`. Muse can pass that existing file to `--prompt-file`; retain the plan's write-if-missing fallback as a defensive path.

- [x] 7. Measure Muse doctor and catalog latency

## Acceptance criteria
- Measure `muse --version`, `muse usage --json` and the `muse serve` catalog round trip (cold and warm) to set the doctor and catalog timeouts.
- Measured with Muse 1.4.15, logged in, `MUSE_NO_AUTO_UPDATE=1`; wall clock from spawn to exit (version, usage) or to the `model/list` reply, then stdin closed and exit awaited (catalog). Warm: the normal data home, 10 runs. Cold: a fresh `XDG_DATA_HOME`, `XDG_CACHE_HOME` and `XDG_STATE_HOME` per run, 5 runs; the OS page cache was not dropped (no root). Each catalog call starts its own `muse serve`, so cold and warm differ by Muse's on-disk state, not by process start.
- `muse --version`: warm median 10 ms (9–12, n=10); cold median 10 ms (9–11, n=5).
- `muse usage --json`: warm median 2.19 s (1.33–2.60 s, n=10); cold median 1.70 s (0.92–2.29 s, n=5); every run exited 0 with an allowance. Network-bound. Without the auto-update guard (n=5): median 1.47 s (1.10–2.46 s), no clear difference.
- Catalog round trip: warm median 458 ms (455–493 ms, n=10), `providerCatalog` with 4 models, shutdown 9 ms. Initialize and `model/list` replies arrive about 1 ms apart, so the round trip is almost all server start-up.
- Catalog, cold: median 484 ms (483–494 ms, n=5) with the login credential copied but no cached catalog. It returns `bundledCatalog` with **zero models** and no error, and three consecutive cold calls did the same. Neither `muse serve` nor `muse usage` wrote the cache, so a machine without it would show no Muse models. The source of the cache is not established. The catalog must treat an empty bundled catalog as not authoritative (an error), never as "no models".
- Timeouts (for entries 20 and 25): `muse --version` and `muse usage --json` use the shared `DOCTOR_TIMEOUT_SECONDS` (60 s, about 23× the slowest usage run), so no Muse-specific doctor constant. Catalog: 10 s, about 20× the slowest round trip of 494 ms. Claude uses 20 s.

- [x] 8. Check default model handling

## Acceptance criteria
- Check how `claude` and `codex` treat the `default` model that non-interactive `init` writes, and decide Muse's behaviour to match.
- Claude Code 2.1.322 documents `default` as a model selection resolved to the account, organization, or configured runtime default. Its bounded local CLI probe produced no output before interruption, so this finding is based on [Claude's model configuration docs](https://code.claude.com/docs/en/model-config).
- Codex CLI 0.167.1 documents `--model` as overriding the configured model, and `codex debug models` lists concrete slugs without a `default` entry. The local `codex exec --model default` probe failed before model validation because the app server could not initialize (`Operation not permitted`). Runtime handling is therefore **unverified**; do not infer rejection from catalog absence.
- Decision for Muse: support the `default` sentinel so generated `muse:default` works, matching Claude's documented behavior. Add `ModelCatalog.supports_runtime_default`; `assess_model_choice` accepts the sentinel only when declared, so `PluginChecks`, Doctor and the ladder editor agree. Muse declares it, skips model context lookup for the sentinel, and omits `--model` at turn start, using its observed runtime default (`muse-ember-1.0`) rather than the catalog default (`muse-wonder-1.0`). This expands later catalog, shared assessment, turn sequence and integration-test entries as recorded in `tasks/plan.md`.

- [x] 9. Review wizard and plugin checks

## Acceptance criteria
- Read the wizard and `PluginChecks` flow once more against the plan's "Init, the wizard and doctor" section.
- Reviewed against `plan.md` "Init, the wizard and doctor" (items 1–6). Matches the code: detection runs `PluginChecks.check(name, {})` with no models and no catalog (`ProjectDetectors._agents`); the ladder picker lists only agents with an error-free catalog and at least one `offered` model (`InitWizardApp._load_model_catalogs`); headless gates the Helper scope; blank `args` writes `[]` and `options_rejection` focuses the rejected key; `render_config` uses the alphabetically first passing agent; the final Doctor passes configured models and the catalog (`Doctor._plugin_checks`); core code names no provider.
- Not yet present, as the plan expects: `ModelCatalog.supports_runtime_default`, the `default` sentinel, and the Muse plugin (entries 11 and 19–25).
- Open for the plan: a catalog error reaches the final Doctor as "could not be fully verified" (`_model_choices_check`), so the plugin still passes. The ladder step reads its catalog with options `{}` before the Agents step, so `args` that select a model are caught only at Doctor time. Codex behaves the same. Decide whether Muse should fail Doctor in that case.
- Muse's catalog should set `complete=True`, as Codex does. Claude's catalog leaves it `False`, so `assess_model_choice` would not reject unknown models for Claude.

- [x] 10. Document Muse fixtures

## Acceptance criteria
- Write `tests/fixtures/agents/muse/README.md` (version, date, what is redacted, what is synthetic).

### Phase 1: options and parser

- [x] 11. Add Muse options and protocol constants

## Acceptance criteria
- `plugins/agents/muse/__init__.py`, `options.py`: constants (`muse`, `exec`, `--json`, `--session-id`, `--prompt-file`, `--output-schema`, flags, record types), `MuseOptions` (`args` default `--trust-workspace --disable-sandbox`, `compact_at_pct`, `billing_mode`), `MUSE_SETTINGS` from `shared_settings`.
- Shared `ModelCatalog.supports_runtime_default` capability and `assess_model_choice` behavior: `default` is supported only when the catalog declares it.

- [x] 12. Parse Muse turn streams

## Acceptance criteria
- `turn.py`: `MuseTurnParser.feed/finish` mapping records to signals (session, message, tools, usage, quota, terminal), malformed-line handling, interrupted turns, resumed usage accumulation.

- [x] 13. Classify Muse failures and reset times

## Acceptance criteria
- Failure classification (auth, quota with reset selection, unavailable, other, unknown) as a pure function; reset selection through `ResetTimeParser`.

- [x] 14. Test Muse stream parsing and failure rules

## Acceptance criteria
- Unit tests over the Phase 0 fixtures: one table per rule, including unrecognised text -> `UNKNOWN`; test shared model-choice assessment with and without runtime-default support.

### Phase 2: process sequence and compaction

- [x] 15. Read Muse session logs

## Acceptance criteria
- `session_log.py`: locate the session log, read compaction and `model_completed` events, count compactions at turn start.

- [x] 16. Run Muse turn processes

## Acceptance criteria
- `MuseTurnSequence.start/advance`: argv (`exec --json`, optional `--session-id`, `--model`, `--reasoning-effort`, compaction thresholds, `args`, `--prompt-file`), environment with `MUSE_NO_AUTO_UPDATE=1`, one retry after a context failure. Omit `--model` for the `default` rung. Create `prompt.md` from `spec.prompt` when the engine has not written it; `compact_at_pct` accepts 0 to 99.0.

- [x] 17. Report Muse compaction and context usage

## Acceptance criteria
- Compaction signals from the session log; `context_used_pct` from the prompt estimate.

- [x] 18. Test Muse thresholds and session logs

## Acceptance criteria
- Unit tests for threshold-to-flag mapping and log parsing.

### Phase 3: agent, doctor, quota, interactive

- [x] 19. Implement the Muse agent

## Acceptance criteria
- `agent.py`: `MuseAgent.start_turn` (`StreamingTurn.start`), `interactive_command`, `settings`, `billing_declaration`, `create` with `options_error`.

- [x] 20. Add Muse installation and login checks

## Acceptance criteria
- `doctor([])` (the init/wizard detection gate): binary, version (warn off the tested `1.4.15`), login = `muse usage --json` exit 0 **or** non-empty `META_API_KEY` ("not verified by doctor"). Quick, bounded, deterministic; failing any check hides Muse from init and the wizard.

- [x] 21. Check Muse models and compaction in doctor

## Acceptance criteria
- `doctor(models)`: add model checks against the catalog and the compaction threshold versus the startup prompt; no turn is run. Preserve the declared `default` sentinel through shared checks and skip model-specific context lookup for it.

- [x] 22. Expose and validate Muse settings

## Acceptance criteria
- Settings: exactly `compact_at_pct`, `billing_mode`, `args` (default `--trust-workspace --disable-sandbox`); `create()` rejects unknown keys via `options_error`.

- [x] 23. Read Muse quota

## Acceptance criteria
- `read_quota`: `muse usage --json` -> `QuotaReading` (null allowance -> `None`).

- [x] 24. Test Muse doctor and quota integration

## Acceptance criteria
- Integration tests with a fake `muse` executable on `PATH`: missing binary, logged out, logged in, quota probe.

### Phase 4: model catalog

- [x] 25. Implement Muse model catalog transport

## Acceptance criteria
- `catalog.py`: one-shot `muse serve --disable-shell --disable-write --no-session-log` client (`initialize`, `initialized`, `model/list`), timeouts, clean shutdown, parse into `ModelCatalog`.

- [x] 26. Expose the Muse model catalog

## Acceptance criteria
- `MuseAgent.model_catalog()` with caching and an error catalog on failure; `complete=True`, each model `offered`; `supports_runtime_default=True`; ambiguous-`args` error like Codex; bounded by the measured timeout. The wizard lists an agent in ladders only when this succeeds, so this phase gates wizard support.

- [x] 27. Test Muse model catalog discovery

## Acceptance criteria
- Tests: parser unit tests; integration test against the fake executable speaking the same lines; assert the catalog advertises runtime-default support without adding a synthetic model choice.

### Phase 5: helper headless

- [x] 28. Implement Muse headless calls

## Acceptance criteria
- `headless.py`: `MuseHeadless.ask` (prompt and schema in a temp directory, flags per the plan, timeout, parse the final JSON object, `HeadlessOutputError` on missing or invalid output); omit `--model` when the rung is `default`.

- [x] 29. Expose Muse headless support

## Acceptance criteria
- `MuseAgent.headless()` returns it; add the `PLR0917` ignore. The wizard offers an agent for the Helper scope only when this is non-`None`.

- [x] 30. Test Muse headless calls

## Acceptance criteria
- Tests: success, invalid JSON, missing result, non-zero exit, via the fake executable.

### Phase 6: wiring, contract suite, docs

- [x] 31. Register the Muse agent

## Acceptance criteria
- `pyproject.toml`: entry point, keyword, ruff ignore; confirm `uv run lint-imports` passes.

- [x] 32. Add Muse agent contract tests

## Acceptance criteria
- `tests/integration/test_muse_headless_contract.py` on `AgentContractSuite`; adjust the suite for internal compaction if no real `installed` event could be captured (separate commit `test(agents): allow agents that compact internally`).

- [x] 33. Update built-in agent tests

## Acceptance criteria
- Update `tests/integration/test_operator_plugin_checks.py` (settings defaults for `muse`) and any test enumerating the registered agents.

- [x] 34. Test Muse setup and wizard integration

## Acceptance criteria
- Wizard/init integration tests with a fake `muse` on `PATH`: offered when installed and logged in or `META_API_KEY` only; absent when missing or logged out; selectable for Leader, Implementer and Helper; `default` validates only for catalogs that declare runtime-default support through `PluginChecks`, Doctor and the ladder editor; slow `muse serve` shows the catalog error; generated `[agents.muse]` section.

- [x] 35. Document Muse design and trust boundaries

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
