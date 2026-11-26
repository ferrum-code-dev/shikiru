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
- `turn.py`: `MuseTurnParser.feed/finish` maps records to signals (session, message, tools, usage, quota, terminal), malformed-line handling, interrupted turns and resumed usage accumulation. Tool detail is reported as soon as output arrives; uncorrelated results are deduplicated against pending calls using the started call ID or scheduled idempotency key.

- [x] 13. Classify Muse failures and reset times

## Acceptance criteria
- Failure classification (auth, quota with reset selection, unavailable, other, unknown) as a pure function; reset selection through `ResetTimeParser`.

- [x] 14. Test Muse stream parsing and failure rules

## Acceptance criteria
- Unit tests over the Phase 0 fixtures: one table per rule, including unrecognised text -> `UNKNOWN`; test shared model-choice assessment with and without runtime-default support. Tool fixtures cover immediate detail, same-call uncorrelated-result deduplication for bash and file tools, and unnamed unrelated results.

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

- [x] 36. Document Muse prerequisites and live tests

## Acceptance criteria
- `README.md`, `CONTRIBUTING.md`: built-in agents, prerequisites (`muse login`), live-test constants.

### Phase 7: live verification

- [x] 37. Run Muse live contract checks

## Acceptance criteria
- Live contract checks with a real `muse` (`SHIKIRU_E2E=1`, as for Codex): first turn writes a valid report, usage, stop leaves the session resumable, headless `ask`, compaction.

- [x] 38. Verify Muse through the engine in tmux

## Acceptance criteria
- Opt-in e2e rung with a Muse ladder; verify an unattended run through the engine in tmux, including `interactive_command` take-over and a commit from a linked worktree.

- [x] 39. Run the final project check

## Acceptance criteria
- `PYTEST_ADDOPTS='--dist=loadfile' uv run poe check` green on the final tree.

- [x] 40. Replace synthetic failure captures when available

## Acceptance criteria
- Replace synthetic quota/overload fixtures with real captures when the first occurrence is seen.

### Phase 8: helper schema fix (T-157)

- [x] 41. Compose self-contained helper schemas

## Acceptance criteria
- `classify_failure` and `rebuild_report` send one root object schema with every `$ref` resolved and no top-level `oneOf`; the schema is valid for Claude, Codex and Muse; `unrecoverable` is a field of that object.

- [x] 42. Test composed helper schemas with a JSON Schema validator

## Acceptance criteria
- Every report kind and the classification schema pass `check_schema`, resolve all references and accept an Unrecoverable payload; the classification sample is accepted; a completed report with null optional fields is returned.

- [x] 43. Check Muse helper trust and session-log flags

## Acceptance criteria
- Determine whether untrusted workspace and `--no-session-log` make the Muse helper rung fail with a valid schema.

### Phase 9: Muse context usage (T-160)

- [x] 44. Report Muse context usage from the catalog window

## Acceptance criteria
- `start_turn` awaits the cached catalog and passes the window of the rung's model to the turn; `UsageReported.context_used_pct` is the latest prompt estimate over that window, capped at 100.
- A catalog that is unavailable or lacks the model leaves `context_used_pct` unknown and emits an `EvidenceFault` naming the reason; no window is guessed, and a previous turn's share is cleared rather than carried over.
- The fake Muse integration tests cover a listed window (25% for 50,000 of 200,000 tokens), an unlisted model and an unavailable catalog.

### Phase 10: expired quota windows (T-162)

- [x] 45. Show an expired quota window as unknown

## Acceptance criteria
- A window whose known reset has passed shows `○ --%` and `resets unknown` in the dashboard and `shikiru status`; a window without a reset keeps its percentage and shows `resets unknown`.
- The stored reading, block logic, JSON output and blocker evidence keep the raw values; the rule lives once in `effective_used_pct` and `reset_is_known`.
- Domain tables cover expired, exactly-at-reset, future and no-reset windows; a FakeClock integration test shows an expired 100% window as unknown with the stored value still 100.0.

### PR check repair (T-168)

#### Task 46

- [x] 46. Capture failing check evidence and settle retry policy

## Acceptance criteria
- Consult the Leader on the default repair-attempt limit and per-project configurability, and record the decision in `tasks/plan.md` before implementing policy; the Leader consults the Operator if needed.
- GitHub PR status exposes failing check names, results, available details or links, and the head to which they apply while preserving pending-check and conflict behavior.
- Missing or failed evidence retrieval is visible and is not interpreted as a passing check; tests cover check runs, status contexts and pending checks.
- Tick this entry and document any chosen configuration in the appropriate project documentation.

#### Task 47

- [x] 47. Repair settled failing checks through bounded durable rounds

## Acceptance criteria
- A PR with a fixable failing check ends up merged without operator involvement.
- A PR with a persistently failing check escalates after the attempt limit, with the failure details attached.
- Merge conflicts and the wait-budget timeout behave as they do today.
- A repair round starts only when all checks have settled, gives the agent the failing check evidence, verifies and safely publishes its fix, and waits for the published head's checks before another attempt.
- Attempts and round progress survive restart and repeated polls without duplicate attempts or unsafe pushes; review feedback, holds, closure and remote-head changes are handled safely.
- A demonstrated infrastructure or external failure escalates with its evidence and actual reason; exhausted attempts are identified in the escalation reason.
- Integration tests cover successful repair, exhaustion, unfixable failures, pending checks, restart, publication reconciliation, conflicts and timeout using the established test infrastructure.
- Tick this entry and update the design documentation for the implemented workflow.

#### Task 48

- [x] 48. Show check repair in operator views

## Acceptance criteria
- The dashboard and operator-facing views identify a task actively fixing PR checks and expose the relevant failure details.
- Exhausted repair attempts appear with an explicit exhaustion reason and attached failure evidence in operator views.
- Integration tests exercise the visible repair and escalation states, including refresh or restart where applicable.
- Tick this entry and update user-facing documentation for the final behavior.

### Billed vs estimated cost (T-172)

- [x] Show API-declared Claude costs as billed
  - Acceptance: an API-declared agent with a provider-reported cost shows it as billed; a subscription agent still shows an estimate; a task mixing both shows two separate figures, not a blended one; existing behaviour is otherwise unchanged.
  - Rework (review): classification follows the run's persisted billing declaration (`TurnSpec.billing_mode`, set from `selection.run.billing_mode`), not the current agent options, so a resumed session keeps its original basis after a configuration change. Integration coverage in `tests/integration/test_claude_billed_cost.py` drives a real Claude turn over both change directions, and runs a mixed task through the engine flow (API implementer, subscription leader) to the dashboard usage panel, which shows the billed and estimated totals separately.

### API-equivalent costs for Codex and Muse (T-175)

- [x] Fetch and retain OpenRouter model prices and apply them to current turn usage for the existing usage views.
- [x] Resolve the Muse retry: remove `-1.0` in full, otherwise remove `.0`, only after an exact miss.
- [x] Verify the four previously cataloged Muse runtime model names on an authenticated real setup; record the empty current catalog response.
- [x] Run the final project check.
- [x] Rework Codex estimates to use the runtime model recorded for each call; unknown runtime models stay unknown.

### Waiting turns in shikiru ls (T-176)

- [x] Count held, wait-reason and mid-turn quota-waiting turns as Waiting in `shikiru ls`
  - Acceptance: the table, narrow view and JSON show a Waiting count between Running and Queued. A turn is waiting while it is held, while it carries a wait reason, or while its quota block holds; it counts as running again once the block lifts or the turn restarts after its quota failure. `shikiru ls` uses the agent recorded by `TURN_STARTED`, including when project settings differ or the stored rung index is out of range; the dashboard stops showing the wait after routing changes. Queued and escalated counts are unchanged. Integration coverage in `tests/integration/test_operator_sessions.py`.

### Cache folder candidates for init (T-178)

- [x] Suggest the well-known cache and dependency folders that are Git-ignored
  - Acceptance: `init` suggests `.mypy_cache`, `.ruff_cache`, `.tox`, `.next`, `.nuxt`, `.dart_tool` and `vendor` alongside the existing names, only when Git ignores the directories. Matching ignored files are not suggested. `build` is not suggested: it is a generic output name that this repository also writes release-smoke artifacts into. Integration coverage in `tests/integration/test_init_cache_detection.py`.
- [x] Make `doctor` suggest newly detected Git-ignored cache folders missing from `[repo] caches`
  - Acceptance: Doctor lists informational suggestions for detected folders missing from configured cache patterns; suggestions never affect its status. The README lists the detected names. Integration coverage in `tests/integration/test_operator_doctor.py`.
- [x] Agree the final cache list with the Product Owner before merge.
  - Agreed: `.venv`, `node_modules`, `target`, `.gradle`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.tox`, `.next`, `.nuxt`, `.dart_tool` and `vendor`; `build` excluded. Detection stays limited to Git-ignored folders, and doctor's cache suggestions stay non-failing.

### Report handoff ends the turn (T-179)

- [x] Stop a turn as soon as it submits its report instead of waiting for its agent process to end
  - Acceptance: the monitor stops the turn through `Turn.stop()` once a new valid report appears, drains it within `shutdown_seconds`, then the existing report validation, workflow and Git checks run. A terminal signal already waiting is consumed first; a report that is invalid or unchanged since the attempt began never triggers the stop. Prompts tell agents that submitting is their final action. Integration coverage in `tests/integration/test_engine_report_handoff.py`.
- [x] Rework the handoff after review: honour a terminal drained during the stop, check the report after every signal, parse it only when it changes, and cover failure, chatter, ignored-stop and cancellation races
  - Acceptance: `tests/integration/test_engine_report_handoff.py` covers a failure arriving with the report, a continuously emitting agent, an agent ignoring the stop (killed after the grace period) and cancellation while stopping. The fake CLI parser honours `interrupted` like the real plugins.
- [x] Rework the handoff after the second review: give the agent `report_grace_seconds` to end its own turn, and treat an unended stream after the stop as stalled
  - Acceptance: ordinary turns keep their final usage and message; a lingering agent costs at most the grace period; a drain timeout returns `Stalled` and does not consume the report. Integration coverage in `tests/integration/test_engine_report_handoff.py`.

### Stream holder spawn in report handoff tests (T-180)

- [x] Fix the Ubuntu CI failure of the stream-holder handoff test
  - Acceptance: the fake agent starts its detached stream holder in its own process group instead of its own session, because the uv-managed Linux Python lacks `POSIX_SPAWN_SETSID`; the test no longer fails with a missing `stream-holder.pid` on Linux. Integration coverage in `tests/integration/test_engine_report_handoff.py`.

### Muse API-equivalent cost after report handoff (T-183)

- [x] Price Muse turns whose session log holds more completions than the stream's last `run.usage`
  - Acceptance: `MuseTurn._cumulative_usage` keeps only the first N session-log completions, where N is the sum of the `calls` counts in the last `run.usage` models entries, so the priced calls match the stream totals; `priced_usage` stays strict. Integration coverage in `tests/integration/test_muse_priced_turn.py` uses a fake Muse whose session log holds extra trailing completions beyond the stream's last `run.usage`. Replay of T-181 session `01a20a54-9b19-7661-9ef1-5db3cf6410d5` prices the first 113 calls at about 0.108 USD with `muse-ember-1.0`.

### Task diff review rework (T-195)

- [x] Match landed commits by resolved Git object ID
  - Acceptance: merge and squash landing evidence with full commit IDs resolves to the same objects as abbreviated result commits; a PR whose branch head is itself a task merge commit keeps the full net task range and commit option.
- [x] Preserve quoted filenames in diff file selection
  - Acceptance: changed file names use NUL-delimited Git output and a real-Git integration test selects a Unicode and special-character path and renders its patch.
- [x] Keep selected pathspecs literal
  - Acceptance: a real-Git test changes both `[ab].txt` and `a.txt`, selects `[ab].txt`, and sees only that file's patch.
- [x] Compare recreated staged deletions with the merge-base version
  - Acceptance: real-Git tests cover identical replacements (no net changed file) and changed replacements (only the actual net content changes).

### Task diff popup (T-196)

- [x] Show a task's Git diff from the Results section (`d`) and **More… → View diff**
  - Acceptance: commit and file dropdowns, coloured two-way scrollable patch, `f` fullscreen keeping selections and scroll, `Esc` close, **Changes so far** labelling, landed merge, squash and pull request evidence after cleanup, and visible empty, binary, non-UTF-8, missing-evidence and Git-error states. Integration coverage in `tests/integration/test_task_diff.py`, with real engine landings in `tests/integration/test_engine_squash.py` and `tests/integration/test_engine_pull_request.py`.
- [x] Render changed staged-deletion replacements as a correct patch
  - Acceptance: headers, hunk header, removed and added lines render as separate, coloured lines; identical replacements stay out of the file list; literal and Unicode paths keep working.
- [x] Keep other files selectable when one staged-deletion replacement cannot be shown
  - Acceptance: real-Git tests cover an invalid UTF-8 and an unreadable replacement beside a valid changed file; the error is confined to the affected file and identical replacements stay out of the list.

### Dismiss dashboard faults (T-197)

- [x] Add a direct `Dismiss fault` action to each open fault card, outside Details and without help text beneath it.
  - Acceptance: dismissal removes the card and updates the open count; a durable dismissed event preserves history without marking recovery; after restart, a new occurrence of the same scope/task opens a fresh card. Cover event folding, stale commands, the dashboard action and persistence with domain and SQLite-backed integration tests.

### Authentication fallback across the configured order (T-199 follow-up)

- [x] Allow authentication recovery to select eligible earlier agents without retry loops.

## Acceptance criteria
- An authentication failure selects an eligible different agent from the effective configured order, including earlier rungs, and skips blocked or unavailable agents.
- Repeated authentication failures cannot cycle between agents and escalate with the authentication reason when no eligible alternative remains.
- Regression coverage reproduces the Muse quota fallback, low-complexity triage retaining Claude, and Claude authentication failure sequence with an eligible earlier Codex, and covers exhausted alternatives.
- Documentation describes the resulting authentication recovery behavior, and the full project check passes.

### Visible fault dismissal label (T-198)

- [x] Make the `Dismiss fault` label readable on each open fault card
  - Acceptance: the button renders its label on its own row instead of a blank line, and the dashboard fault-dismissal integration test asserts the label is on screen while dismissal still queues the fault event.

### Latest CI refresh failures (T-201)

- [x] Preserve a focused fault's screen position after refresh and wait for scroller focus before keyboard input.
  - Acceptance: the context popup preserves the focused widget's screen row across layout updates and restores visibility if that row cannot be retained. The diff test waits for deferred focus, then verifies arrow-key scrolling and fullscreen preservation.

### Codex turn permissions (T-202)

- [x] Default new and resumed Codex turns to unsandboxed permissions, preserve explicit arguments, update Doctor and document filesystem and network access.
- [x] Cover command construction and explicit arguments; extend the opt-in live worktree/resume test to check writes outside the worktree.
- [x] Verify authenticated live first and resumed turns can write outside the worktree, and that the workspace-write sandbox denies the same location.
- [x] Remove the obsolete Codex sandbox check from the Doctor reference.

- [x] Synchronize release smoke ladder navigation with each visible scope before opening Helper.

### Optional auto-merge for pull requests (T-203)

- [x] Title pull requests with the accepted commit subject followed by the task ID, refreshed on adoption and after each accepted feedback or check repair round.
  - Acceptance: the title is `<subject> (T-n)` regardless of the setting; a feedback or check repair round that publishes refreshes it together with the description.
- [x] Request GitHub auto-merge when `orchestration.pr_auto_merge` is `true` and withdraw it while the task is held, cancelled or has an unfinished round.
  - Acceptance: `gh pr merge <url> --auto --squash --subject --body --match-head-commit` for the published head; idempotent across restarts and adopted PRs; the request follows a republished round; a refusal escalates with GitHub's reason; nothing is sent when the setting is `false`.
- [x] Document the setting, the repository prerequisites and the accepted limitation in `README.md` and `docs/design.md`.
- [x] Cover the `gh` boundary with the fake `gh`: request argv, republish, withdrawal on hold, cancellation and an active round, refusal, and the title.
- [x] Rework after review: confirm withdrawal before a feedback or check repair round starts, and reconcile a request GitHub accepted whose reply was lost.
  - Acceptance: a round does not start while its withdrawal is unconfirmed (the task escalates); a withdrawal reads GitHub's `autoMergeRequest` and fails if the request is still held; a request is recorded before it is sent and a lost reply is confirmed on GitHub rather than refused; gh-boundary tests cover both and a parser unit test covers the held request.
