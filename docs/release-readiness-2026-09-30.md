# Phase 7 release-readiness evidence

Later evidence and the current release claim: [release-readiness-2026-10-01-phase7b.md](release-readiness-2026-10-01-phase7b.md).

Date: 2026-09-30. Source commit: `96bb4b7` (Task 59's `test(e2e)` fix on top of `99c2006`, the Task 58 head). This document restates the 2026-09-29 audit ([release-readiness-2026-09-29.md](release-readiness-2026-09-29.md)) after Tasks 41-58 fixed the findings of the 2026-09-29 implementation review (F01-F23).

## Verification results

| Check | Result |
|---|---|
| `uv run poe check`, normal environment | Passed: 764 passed, 15 skipped, 88.78% coverage (gate 80%) at `99c2006`; re-run at `96bb4b7` after the test-only change: 764 passed, 15 skipped, 88.76% |
| `uv run poe check`, `TERM=dumb` | Passed at `99c2006` (764 passed, 15 skipped, 88.76%) and again at `96bb4b7` (764 passed, 15 skipped, 88.76%) |
| `SHIKIRU_E2E=1 uv run poe e2e`, final run | Passed on 2026-09-30 at `96bb4b7`: 7 passed in 24m52s (fake-agent scenario plus real-agent E1-E6 in one invocation); artifact `build/e2e/2026-09-30.json`, `exit_status` 0 |
| Clean-install smoke `scripts/release-readiness-smoke.sh` | Passed on macOS (Darwin 25.3.0) at `96bb4b7` in about 17 s; `build/release-readiness/clean-install-2026-09-30.json` records `source_commit` `96bb4b728efffb07f2842b4e390f69c9a8b74570` and status `passed` |

Tested CLI versions: Claude Code 2.1.284, Codex CLI 0.159.2, Python 3.14.7, tmux and uv as installed on the macOS host.

### E2E scenarios and agent-reported cost

Costs are the agents' own estimates, not verified billing. Scenarios use the cheapest rungs (Claude haiku/low, Codex gpt-5.6-luna/low).

| Scenario | Covers | First run (failed) | Final run (passed) |
|---|---|---|---|
| E1 | triage, implement, verify, merge, tracker tick | passed, $0.1296 | passed, $0.1108 |
| E2 | operator message delivered to verify | passed, $0.1504 | passed, $0.1526 |
| E3 | plan, dependent tasks in parallel, conflict resolution | failed, $1.0578 | passed, $0.5274 |
| E4 | AI operator resolves a product decision over MCP | passed, $0.1929 | passed, $0.2164 |
| E5 | SIGKILL of the engine mid-turn, restart resumes the same session (resume) | passed, $0.1075 | passed, $0.1072 |
| E6 | take over one task's role, hand back (takeover; same-session resume) | passed, $0.5327 | passed, $0.2555 |
| Sum | | $2.1709 | $1.3699 |

The first E2E run of 2026-09-30 (`99c2006`, 36m59s, 1 failed, 6 passed) failed E3 after its one automatic rerun. The plan behaved correctly (T-2, T-3 and T-4 all finished, the tracker was ticked), but `_assert_dependent_new_tasks` required every dependency of some new task to be a new sibling. Since Task 48 every new task also depends on the plan task (03 section 2), so the check could never hold. It was a stale test assertion, not a product defect. Commit `96bb4b7` changed it to require at least one dependency on a new sibling, and the whole E2E was rerun in a single invocation. Run-1 results were saved before the rerun replaced the dated artifact. The final artifact is the only one used as evidence; the 2026-09-29 and earlier artifacts are historical. The E5 and E6 scenarios cover session resume and takeover with real agents. No real-agent scenario changes the model of a live session (07 section 5 defines none), so Task 59's "model-change scenario included" is not met by `poe e2e`; model change is covered by the plugin tests and by the live contract checks below.

### Live model-change contract checks (Task 73)

Run natively on 2026-09-30 (Codex at `b8de3d0`, Claude at `a2b8bad`), Codex CLI 0.159.2 and Claude Code 2.1.284 (subscription access, no API spend recorded):

| Command | Result |
|---|---|
| `SHIKIRU_E2E=1 uv run pytest --no-cov -n 0 tests/integration/test_codex_headless_contract.py -k live_model_change` | Passed in about 10 s: a `gpt-5.6-luna` turn, then `codex exec resume` on `gpt-5.6-terra`; the nonce is recalled, each turn's emitted `-c model_auto_compact_token_limit` equals 80% of the selected model's window (both 258,400 tokens, so 206,720), and after the switch the real rollout's last `turn_context` names terra and its `token_count` window is what the next terra turn uses |
| `SHIKIRU_E2E=1 SHIKIRU_CLAUDE_LIVE_CHANGE_MODEL=sonnet uv run pytest --no-cov -n 0 tests/integration/test_claude_headless_contract.py -k live_model_change -q` | Passed in 10.67 s on `a2b8bad` (run by the coordinator; subscription turns, no API spend recorded): base model the test's default haiku, changed to `sonnet`; the nonce is recalled and each turn's hook compaction limit equals the selected model's window (200,000 tokens, so 160,000). Without the variable the check skips (no alternate model configured on this host by default) |

Claude's two models use the same 200,000-token window, so that run likewise cannot distinguish a per-model window. Both Codex models report the same window, so the live run proves the limit is taken from each turn's model and that the real `turn_context` format parses, but cannot tell a per-model window from a shared one; the offline test with a genuine redacted rollout (`rollout_model_change_turn_context_live_redacted.jsonl`) covers a window that differs between models.

### Platform limits of this evidence

The smoke script is macOS-only (macOS `script` syntax, a hard-coded homebrew PATH, `shasum`). No Linux environment was available (docker daemon down). **Linux readiness is not claimed and was not verified.**

## Findings F01-F23

Source: `/tmp/shikiru-review-2026-09-29.md` (review of `e56832b`).

| Finding | Subject | Task | Commit(s) | Status |
|---|---|---|---|---|
| F01 | Integration can discard uncommitted task work | 42 | `21d7a2e` | Fixed |
| F02 | Agent shutdown does not own descendants or bound pipe draining | 46 | `68c5cf7` | Fixed |
| F03 | Takeover ownership released while native agents remain active | 44 | `e7c0c1e` | Fixed |
| F04 | Integration bypasses persisted task holds | 43 | `564d7c9` | Fixed; nudges, the spawn window, `rebase --continue`, PR polling and the scheduler were gated later (Task 65) |
| F05 | Stale escalation actions can resurrect canceled tasks | 45 | `9acc604` | Fixed; cancelling an `uncommitted_work` escalation keeps the dirty worktree |
| F06 | PR completion leaves dependent work on a stale local base | 47 | `611c5ea` | Fixed; the local merged-branch cleanup is resolved by Task 67 (limitation 4) |
| F07 | Decomposition does not deliver parent tracker changes to children | 48 | `4431d5f` | Fixed |
| F08 | Notification hook blocks command processing and scheduling | 52 | `48e25a1` | Fixed |
| F09 | Triage discards the selected complexity and title | 49 | `d8cf6a9` | Fixed; limitation 5 is resolved by Task 68 |
| F10 | Completed turn overwrites a concurrent rung change | 49 | `d8cf6a9` | Fixed |
| F11 | Dependency cycles accepted and wait forever | 50 | `ca14f8f` | Fixed |
| F12 | Pending command replay not idempotent | 53 | `4737276` | Fixed; acknowledgement is not in the effect's transaction (limitation 8) |
| F13 | SIGKILL strands delivered operator messages | 54 | `89a370e` | Fixed |
| F14 | Escalated state without an actionable escalation | 55 | `5f45ab8` | Fixed; persistent packet-write failure and restored packet content (limitation 6) |
| F15 | Codex model change reuses the previous model's window | 56 | `c8362f0` | Fixed |
| F16 | Reserved path characters redirect SQLite to another file | 51 | `fce47fa` | Fixed |
| F17 | Layout-sensitive assertion fails the gate | 41 | `d57286d` | Fixed; the gate passes with and without `TERM=dumb`. `1d7db7a` removed the saturable CPU caps in integration tests; Task 68 removed the last two in the E2E tests (limitation 7) |
| F18 | TaskFlow concentrates independently changing policies | 57 | `3979889`, `1e57b0b`, `6cab575`, `79b9259`, `1197b02`, `69ea0ba` | Fixed (turn context, escalation filing, event projection, one turn-boundary path, recovery extracted) |
| F19 | Live-view test covers replay, not live delivery | 58 | `99c2006` | Fixed |
| F20 | Claimed architecture enforcement exceeds lint contracts | 58 | `bfd3dc3` | Fixed for import and clock rules; the semantic agent-name rule and `loop.time()` stay review conventions, now documented as such |
| F21 | Smoke assumes a macOS temporary directory | 58 | `24a4f33` | Fixed for the temporary directory; the script stays macOS-only (limitation 9) |
| F22 | Blanket literal and comment conventions work against KISS | 58 | `ce2aed6` | Decided: the rules stay, pending owner decision (limitation 10) |
| F23 | Documented slot numbering disagrees with implementation | 58 | `33ef2c4` | Fixed in the docs: slots are zero-based, with a `SHIKIRU_SLOT` example |

## Known limitations

1. Resolved (Task 63): cancelling keeps a dirty worktree and reports its path.
2. Resolved (Task 65): pull-request polling checks the hold before every poll and stops quietly while one is set.
3. `RepoState.base_ref` is a required field.
4. Resolved (Task 67): the local branch of a `done` task is removed with its worktree in PR mode too.
5. Resolved (Task 68): `submit` complexity is optional; an explicit `--complexity normal` is kept and only an unset complexity takes the triaged one.
6. A persistent escalation-packet write failure leaves the task running idle until restart, and a packet restored after a restart lacks the original reports and facts (it is rebuilt from the timeline, sessions and escalation row).
7. Resolved (Task 68): the two E2E tests use `CPU_UNCAPPED_PCT` like the integration tests.
8. Command acknowledgement is not in the same transaction as the command's effect; replay is idempotent by command id, which makes this safe.
9. The clean-install smoke is written for macOS and GNU/Linux (Task 70: `script` invocation, `sha256sum`/`shasum`, PATH built from the environment, `SHIKIRU_SMOKE_TMPDIR`). It passed on macOS after that change; Linux has NOT been verified by a run.
10. F22 kept the literal and no-comment conventions, pending owner decision.
11. **Open item, owner decision needed: no LICENSE.** The repository has no LICENSE file and no `license` field in `pyproject.toml`. A license must be chosen and added before any public distribution.

## Do all design sections agree with the code?

The 2026-09-29 audit claimed every section agrees. After Tasks 41-58 that claim is restated narrowly, not asserted for the whole set.

**Method.** `git diff 6dbd6bf..HEAD -- docs/design` touches 01 (slot definition), 02 (TaskFlow collaborators, `NotificationDelivery`, lint description, run-tree listing, `messages/` file names), 03 (triage persistence, tracker delivery, cycle rejection, process groups, message requeue, worktree and base ref, uncommitted-work rule, PR merged base, holds, recovery, command replay, escalation reasons and atomicity, takeover, `say`, scheduling), 04 (stop contract, Codex window), 05 (`command_id`), 06 (`OperatorCommands`, notifications) and 07 (values rule).

**Checked against the code:**
- 01/03 slots: `range(max_parallel)` in `engine/scheduler.py` and `SLOT_ENV_VAR` in `agents/contracts.py`; zero-based, matches.
- 02 modules: every file in the run-tree listing exists under `src/shikiru/engine/`; lint description matches `pyproject.toml` (`banned-api` list including the clock entries, the agent-plugin import contract); no agent-name reference outside `plugins/agents`; no `loop.time()` use.
- 03/04 process group: `TURN_STOP_GRACE` and `STDERR_DRAIN_TIMEOUT` in `process.py`; matches.
- 06 notifications: queue of 64, drop oldest with a warning, supervised restart, cancellation on stop (`NotificationDelivery`); 2 h timeout (`NOTIFY_TIMEOUT_SECONDS = 7200`); matches.
- 03 holds: `Task.held` is checked in `integration.py`, `flow.py` and the `Reconciler`; `ResumeHandler` rejects a resume while a role is taken over; matches.
- 03 supersede tracker seeding: `SEED_COMMIT_MESSAGE` and `_seed_trackers` in `workspace/roots.py`; matches.
- 03 section 8 escalation atomicity (checked after the first draft): `EscalationRaiser.raise_escalation` writes the packet through a temporary file and rename, then applies the task state, the escalation row and the `escalation` event in one `atomically()` block, removes the packet on any exception, and refreshes titles afterwards with the failure logged (`_refresh_titles`). Every escalation path goes through this method (`escalation_filing`, `dependency`, flow, dispatch failure). Matches. A resolve of a non-escalated task is rejected in `handlers/escalations.py`.
- 04 section 5 Codex window rule: `_observed_window` counts a rollout `model_context_window` only when the preceding `turn_context` names the selected model, drops one with no recorded model, and falls back to `models_cache.json`; doctor's `context_window` check fails for an unknown model. Matches.

**Not checked:** the remaining claims in the touched sections were not verified line by line (for example the exact set of hold checks inside each integration turn, the `say` file naming details, the 03 section 10 scheduling text, the 06 `OperatorCommands` acknowledgement timing beyond the E2E and integration tests that exercise them). Sections untouched by Tasks 41-58 were not re-audited; their 2026-09-29 result stands as an earlier statement, not a new one.

**Residual discrepancies found:**
1. 04 section 5 says every Codex turn adds `-c model_auto_compact_token_limit`. `CodexTurn._token_limit` returns `None`, and the flag is then omitted, when no window is known. Only the doctor gate at `start` prevents running unprotected; a model added after `start` would run without the flag and without a diagnostic.
2. 03 section 7 describes the restored packet as rewritten "from durable state", which is accurate, but it is weaker evidence than a packet filed at escalation time (no reports, facts or turn tail). The design does not say so (limitation 6).
3. The 07 section 5 E3 description (dependent new tasks) does not mention the implicit plan edge that made the E3 assertion stale; the assertion is fixed and the description remains valid.
4. 02 section 3 is an illustrative tree; it lists the new engine modules but is not exhaustive by intent.
5. Resolved (Task 68): the two E2E CPU caps of 100 now use the uncapped gate setting.

No other discrepancy was found in the sampled parts. The honest conclusion: the sections changed by Tasks 41-58 agree with the code in every claim checked above, with the residual items listed; this is not a claim that every line of every design section was re-verified.
