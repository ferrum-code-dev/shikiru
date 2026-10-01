# Phase 7c release-readiness evidence

Date: 2026-10-01. Source commit of every run below: `e78c057` (Task 74i); the evidence commit that adds this document changes only documentation. This document records the three final reviews of `b1c16d0`, the fixes of Tasks 74b-74i and the evidence for them. It amends [release-readiness-2026-10-01-phase7b.md](release-readiness-2026-10-01-phase7b.md) (which amends [release-readiness-2026-09-30.md](release-readiness-2026-09-30.md)); where they differ, this one is current. The fourth acceptance criterion of Task 74h, a final independent review, has not been done: nothing here is a reviewer's verdict.

## Release claim

| Use | Claim the evidence supports |
|---|---|
| Supervised with Claude on macOS, an operator watching, a trusted repository and host | **Supported.** E1-E6 passed in one invocation at the first attempt, the suite and both live model-change checks pass, and every reviewer item that could be fixed in code is fixed |
| Supervised with Codex in a real repository on macOS | **Supported only on the verified configuration**: Codex CLI 0.159.2, `approvals_reviewer = auto_review`, no custom `default_permissions` profile, the project outside `$TMPDIR`, the repository trusted. The worktree-commit failure that blocked this in the last review is fixed and exercised by one E2E invocation (E3 and E6 use Codex), the live worktree-commit check and the doctor sandbox probe. Other Codex versions, hosts without `auto_review`, hosts with their own permission profile and Linux are unverified, so run `shikiru doctor` first and treat a sandbox denial as a configuration fault |
| Unattended | **Not supported.** No soak run, the engine does not restart itself after a fault stop, a hung notification hook can delay an urgent notification by up to 30 s, PR mode was never run against real GitHub, run directories grow without bound |
| Linux | **Not verified.** Nothing was run on Linux |
| Public distribution | **Not supported.** No LICENSE (owner decision), Linux unverified, agents are not sandboxed, no soak |

Shikiru still does not sandbox agents: Claude runs in `bypassPermissions`, Codex gets the shared `.git` as a writable root, and an agent can write into the operator's checkout or other worktrees. The engine reports it when it blocks a merge; it does not prevent it (01 section 6).

## Verification results

All at `e78c057`, tree clean, macOS (Darwin 25.3.0). Tested tools: Claude Code 2.1.284, Codex CLI 0.159.2, tmux 3.7c, git 2.55.0, Python 3.14.7.

| Check | Result |
|---|---|
| `uv run poe check`, normal environment | Passed: 928 passed, 15 skipped in 120.6 s, coverage 89.93% (gate 80%) |
| `uv run poe check`, `TERM=dumb` | Passed: 928 passed, 15 skipped in 120.1 s, coverage 89.93% |
| `uv run poe test --no-cov`, 5 extra sequential runs (never concurrent with the E2E run) | No failure: 928 passed, 15 skipped each, 95.2-96.6 s per run |
| `SHIKIRU_E2E=1 uv run poe e2e`, one invocation | Passed: 7 passed in 21 m 03 s, exit status 0, no rerun (see below) |
| Live Codex model change | `SHIKIRU_E2E=1 uv run pytest --no-cov -n 0 tests/integration/test_codex_headless_contract.py -k live_model_change -q`: 1 passed in 9.93 s |
| Live Claude model change | `SHIKIRU_E2E=1 SHIKIRU_CLAUDE_LIVE_CHANGE_MODEL=sonnet uv run pytest --no-cov -n 0 tests/integration/test_claude_headless_contract.py -k live_model_change -q`: 1 passed in 9.44 s |
| Live Codex worktree commit outside `$TMPDIR` | `SHIKIRU_E2E=1 uv run pytest --no-cov -n 0 tests/integration/test_codex_headless_contract.py -k worktree_commits -q`: 1 passed in 15.72 s |
| `shikiru doctor` on a scratch project (`init --yes` in a copy of the fixture; free, no `--probe`, so no model turn) | Exit 0, every check ok: tmux 3.7c, git 2.55.0, repository, config, db schema 6, copy-on-write, Claude 2.1.284 authenticated, Codex codex-cli 0.159.2 authenticated, **`plugin:codex:sandbox`: worktree git directory and run directory writable**, fake plugin |
| Clean-install smoke `scripts/release-readiness-smoke.sh` | Passed on macOS in about 18 s. `build/release-readiness/clean-install-20261001T082109Z-1800.json` (and `clean-install-latest.json`) records `source_commit` `e78c057dc1322ad4f2570b8ef7bff0b55b065fc7`, `source_diff_fingerprint` `33bffefe...75a9` and status `passed`. The commit of this document changes only documentation; the artifact is git-ignored. Repeated on the evidence commit `05c11fb`: 1 of 5 further runs **failed** at the stage `normal-size inline interactive wizard` (a 20 s pane wait timed out; artifact `clean-install-20261001T082615Z-36752.json`) and the next 4 passed, with stdout to a pipe or to `/dev/null` alike. The failure was not reproduced and is not explained (limitation 26) |
| Linux | **Not verified.** Nothing was started or installed there. The smoke script is written for GNU/Linux with a documented recipe (07 section 5) but has never been run there |

The gate counts differ from Phase 7b (894 passed) because Task 74i removed and merged tests (974 collected to 943 collected, 928 passed plus 15 skipped, coverage unchanged at 89.93%; `docs/test-audit-2026-10-01.md`).

### E2E invocation

Started 2026-10-01 07:59:03 UTC, finished 08:20:06 UTC (21 m 03 s; pytest 1263.96 s) at `e78c057`, `source_dirty` false. Artifact `build/e2e/20261001T075903Z.json` (schema 4, git-ignored; the summary is committed as [e2e-results.md](e2e-results.md)). Sandbox settings recorded by the harness: Codex `sandbox_mode` workspace-write, `approvals_reviewer = auto_review`; projects under `/Users/pavel/.cache/shikiru-e2e`, outside `$TMPDIR` (`/var/folders/99/dgcz2lpx7ts5bpvl7zvt4fnw0000gn/T/`); Claude in `bypassPermissions`. Cheapest rungs (Claude haiku/low, Codex gpt-5.6-luna/low).

| Scenario | Covers | Attempts | Duration | Budget | Cost |
|---|---|---|---|---|---|
| fake agent | operator flow, no agent | 1 (not an artifact entry) | about 6 s with fixture overhead | 45 s | none |
| E1 | triage, implement, verify, merge, tracker tick | 1, passed | 117.9 s | 10 min | $0.0972 |
| E2 | operator message delivered to verify | 1, passed | 189.7 s | 10 min | $0.1570 |
| E3 | plan, dependent tasks in parallel, conflict resolution | 1, passed | 377.5 s | 20 min | $0.4637 |
| E4 | AI operator resolves a product decision over MCP | 1, passed | 175.9 s | 10 min | $0.1583 |
| E5 | SIGKILL of the engine mid-turn, restart resumes the same session | 1, passed | 112.9 s | 10 min | $0.0999 |
| E6 | take over one task's role, hand back | 1, passed | 284.1 s | 12 min | $0.2483 |
| Sum | | 6 attempts, no rerun, no diagnostics file | 1258.1 s | | $1.2244 |

Costs are the agents' own estimates, not verified billing. This is one invocation: it shows that the Task 74b fix works on this host and that E3 passed at its first attempt, not that the 2026-09-30 intermittent failure cannot recur (limitation 1). The measured durations replaced the estimates in 07 section 5 and in `docs/e2e-results.md`. No Claude usage limit was hit. No scenario showed a sandbox denial (the harness would have failed it without a rerun).

## Reviewer items

Sources: the three final reviews of `b1c16d0` as listed under Phase 7c in `tasks/plan.md` (Tasks 74b-74i), and the earlier Phase 7b table in [release-readiness-2026-10-01-phase7b.md](release-readiness-2026-10-01-phase7b.md) (Tasks 60-74a), whose items remain as recorded there unless a row below changes them. Status: fixed, partially fixed or limitation. Commits are `git log --oneline b1c16d0..HEAD`.

| Item | Task | Commit(s) | Status |
|---|---|---|---|
| Codex implementer in a linked worktree could not commit: Codex 0.158+ protects the worktree git directory and `exec resume` loses the first turn's extra roots | 74b | `6f81bba` | Fixed: the run directory, the worktree git directory and the common `.git` are `-c` writable roots on every turn. Verified on 0.159.2 (live check and E2E); other versions and host configurations unverified (limitation 3) |
| No check that the sandbox allows commits | 74b | `d5cdfc3` | Fixed: `doctor` runs a free `codex sandbox` probe in a scratch worktree under `~/.cache/shikiru` and warns on another Codex version |
| E2E projects under `$TMPDIR` masked the denial; a sandbox denial was rerun as flakiness | 74b | `20b1a10`, `64bfc4f` | Fixed: projects under `~/.cache/shikiru-e2e`, the artifact records the sandbox settings, a denial fails without a rerun, the live worktree-commit test runs outside `$TMPDIR` |
| One failing cleanup (for example `gh pr close`) stopped scheduling, `ensure_flows` and escalation restore; `end_orphans` could run again on a retry | 74c | `891cb3d` | Fixed: each reconcile step and task isolated and recorded; orphans ended once per engine start |
| One bad pane starved every other task's window restoration; a persistent fault was silent after the first notification | 74c | `88242be` | Fixed: per-task isolation, re-notification every `repeat_notify_seconds` with a `repeat` count |
| Command loop outside the fault barrier; database errors and invariant errors indistinguishable from environmental ones | 74c | `637ed2a`, `b3c6817` | Fixed: second `TickGuard` around the command loop, `OperationalError` recoverable, `InvariantError` unexpected |
| Backoff could overflow; flapping faults flooded the event log | 74c | `20a87fe` | Fixed: iterative cap and per-class rate limiting |
| A hung `notify.command` hook could delay or drop an escalation; hook timeout was 2 h | 74d | `db00397` | Fixed for dropping (urgent events never dropped, delivered first, 30 s timeout); **partially** for delay: one hook runs at a time, so an urgent event can wait up to 30 s behind a stuck informational one (limitation 9) |
| `status` and the dashboard hid an engine fault streak or an old unanswered escalation | 74d | `070d502`, `754be20` | Fixed |
| `git fetch`, `gh pr view` and `gh pr close` ran inside the command loop or scheduling tick without bound; a transient fetch failure counted as a dispatch failure | 74e | `22c83cb`, `a3b69c2` | Fixed: cancel acknowledges at once and the PR close runs off-loop with retry; a failed base fetch is a per-task backoff and never a dispatch failure. The fetch still holds the tick up to `fetch_timeout_seconds` (30 s) per attempt per ready task (limitation 8) |
| One failed `gh pr view` escalated; failing checks escalated before settling | 74e | `237c554` | Fixed. Any settled failing check still escalates, required and optional are not distinguished (limitation 11) |
| Integration rebase used a stale base; `retry` after a conflict only polled | 74e | `bc2a201` | Fixed |
| A taken-over role was left held behind a read-only pane after tmux loss or reboot | 74f | `b127b82` | Fixed: `takeover_lost` event, notified, role handed back. A `take_over` replay can still respawn the pane (limitation 10) |
| A leftover task branch was adopted silently; cleanup could `rmtree` a dirty root without worktree metadata | 74f | `3f4964a` | Fixed: `StaleTaskBranchError` unless the tip is the base sha; such a root is kept |
| Turn directories mis-sorted past 99; `command_applied` lookup unbounded; unwritable packet directory re-collected evidence every tick | 74f | `a221159` | Fixed |
| `init` wrote `config.toml` world-readable first; `make_private_dir` raced | 74f | `7f578d1` | Fixed |
| Widget import contract was a partial denylist; 02 section 2-3 drifted | 74g | `8a57b73` | Fixed: exact whitelist for widgets, the completeness test extended, 02 section 3 matches the tree (checked below) |
| `asyncio.wait_for` and `timeout` use unlisted; the comment checker accepted prose after directives; literal violations in `turn_context.py`, `acceptance.py`, `recovery.py` | 74g | `22a51e3`, `fb34437` | Fixed: exceptions listed in 02 section 2, documented directive forms only, names for the literals. The no-literals rule remains a review convention (owner decision) |
| The smoke script leaked its temp directory on the socket-path exit, hid a missing tool, overwrote a passing artifact and ignored the current evidence docs | 74g | `20b88a2` | Fixed. Linux recipe documented, not run (limitation 4) |
| The E2E artifact was keyed by date and kept only the final attempt, so a rerun hid its first failure; no committed summary | 74g | `3a8908d` | Fixed: schema 4 records every attempt and the per-scenario duration; `docs/e2e-results.md` is committed |
| A `reference-transaction` hook of a cancelled mutating git call outlived it (leaked process after a full-suite run) | 74g | `237b559` | Fixed: each mutating git call leads its own process group, stopped gracefully and swept (03 section 7). A `setsid` descendant is still outside it (limitation 7) |
| Root-fragile `chmod` tests, a test importing helpers from a test module, `evidence.py` swallowing an unreadable history | 74g | `a30c313` | Fixed |
| The suite grew 779 to about 974 tests with coverage flat, against the AGENTS.md test-value rules | 74i | `d5ba80b`, `4cfb83a`, `5133c9b`, `7904993`, `c659679`, `85c66b7` | Fixed: audit recorded; 974 to 943 collected, coverage unchanged. The regression power was kept by naming the covering test or by mutation, not re-proven here beyond the passing suite |
| Phase 7b items (Tasks 60-74a) | 60-74a | see the Phase 7b document | As recorded there; its limitations are reconciled below |

## Do all design sections agree with the code?

**Method.** `git log --stat b1c16d0..HEAD -- docs/design` shows changes in all seven design files (01 only the Codex sandbox sentence). I read every line added in `git diff b1c16d0..HEAD -- docs/design` and checked a sample of 26 concrete claims against the code, below. This is a sample; it is not a claim that every line of every section was re-verified.

**Sampled and found to match:**
1. 02 section 3 engine tree against `src/shikiru/engine/` and `handlers/`: a script found no engine module missing from the document; the other listed directories (`domain`, `workspace`, `ui`, `operator`, `dashboard`, the Claude and Codex plugin directories) list exactly the files present.
2. `TickGuard`/`FaultReporter`/`PullRequestClosing` exist and `PullRequestClosing` is wired in `bootstrap.py`.
3. `InvariantError(ShikiruError)` in `errors.py`; `StaleTaskBranchError` in `errors.py` and raised from `workspace/roots.py`.
4. `orchestration.faults` values in `defaults.toml`: `backoff_initial_seconds` 2, `backoff_max_seconds` 60, `unexpected_fault_limit` 10, `repeat_notify_seconds` 300, `stop_notify_seconds` 30 (05 growth table).
5. `orchestration.network`: `fetch_timeout_seconds` 30, `poll_attempts` 8, `pr_close_attempts` 5.
6. `notify.timeout_seconds` 30 and `INFORMATIONAL_QUEUE_SIZE` 64 (`notification_delivery.py`); urgent kinds `ESCALATION` and `ENGINE_FAULT` in `notifier.py`; `deliver_final` called by the daemon on a reasoned stop.
7. `GIT_STOP_GRACE` 10 s and the mutating-call stop grace (`workspace/git.py`); `PR_POLL` 5 minutes and `pr_wait` 604800.
8. The failing check states (`FAILURE`, `ERROR`, `TIMED_OUT`, `CANCELLED`, `STARTUP_FAILURE`, `ACTION_REQUIRED`) in `workspace/github.py`.
9. Migration 006 creates `idx_events_kind`; migrations 004 and 005 exist; `PRAGMA user_version` schema 6 (the doctor `db` check).
10. `PRIVATE_DIR_MODE` 0700, `PRIVATE_FILE_MODE` 0600 and `mkdir(exist_ok=True)` in `private.py`.
11. `EventKind.TAKEOVER_LOST`, `ENGINE_RECOVERED`, `PR_WITHDRAWN`, `INTEGRATION_WAITING` exist; `takeover_lost` is notified; `LayoutGuard` queues a `HandBack` for a lost role.
12. Orphan cleanup is guarded by `_orphans_ended` in `reconcile.py` (once per engine start).
13. `TESTED_CODEX_VERSION` 0.159.2 and the doctor warning for another version; the sandbox probe scratch directory is `~/.cache/shikiru` (`SANDBOX_PROBE_PARENT`).
14. The Codex argv: `sandbox_workspace_write.writable_roots` from the run directory, `--absolute-git-dir` and `--git-common-dir` (`options.py`, `turn.py`) on both turn kinds.
15. 07 budgets (E1 10 min, E2 10, E3 20, E4 10, E5 10, E6 12, invocation 60) against `tests/e2e/budgets.py`; stall window 4 minutes against `tests/e2e/watchdog.py`.
16. `integration_wait` and `max_quota_wait` overrides in `tests/e2e/test_real_agents.py`; the sandbox denial markers in `tests/e2e/diagnostics.py`.
17. The artifact fields of 07 (`schema_version` 4, `started_at`/`finished_at`, `source_commit`, `source_dirty`, `tool_versions`, `sandbox`, per scenario `duration_seconds`, `cost`, `attempts` with `failure_class`, `rerun_reason`, `diagnostics`) against the real `build/e2e/20261001T075903Z.json` and `build/e2e/latest.json`.
18. 07 comment rule: `keep:` marker and the directive forms (`noqa: CODE -- reason`, `pragma: no cover`) in `tests/support/comment_rule.py`.
19. The smoke script prints `missing tool: <name>`; its artifact has `source_diff_fingerprint` and writes `clean-install-latest.json` only on pass (seen on the real run).
20. `shikiru doctor` reports the `plugin:codex:sandbox` and version checks (seen on the real run).
21. `status`/dashboard: `Queries.oldest_escalation_age_seconds`, `open_fault_streaks` and the header `alerts` including "oldest escalation".
22. 05 events list against `EventKind`: `hold_set`, `taken_over`, `handed_back`, `engine_started`, `engine_fault`, `engine_recovered`, `engine_stopped`, `pr_opened`, `pr_withdrawn`.
23. 02 enforced-rules text: the engine boundary forbids `agents.registry`, `agents.sdk`, `agents.testing` and the completeness test exists (`tests/integration/test_import_contract_completeness.py`).
24. 03 section 9 `HandBack` queued by the dead-pane path in `layout_guard.py`.
25. `process.py` documented as the only subprocess gateway; the `asyncio.wait_for`/`timeout` exception list names `process.py`, `notification_delivery.py`, `operator/probe.py` and `agents/testing/contract_suite.py`; `deliver_final` and the probe contain them.
26. 04 section 5 Codex flags: `-c sandbox_mode`, the roots, `exec resume` taking the same overrides (argv built in `turn.py`).

**Discrepancies found:**
- Fixed in this change (docs only): 04 section 5 said every Codex turn adds `-c model_auto_compact_token_limit`, but `CodexTurn._token_limit` returns `None` and the flag is omitted when no window is known (the doctor gate at `start` prevents it). The text now says so. This was residual discrepancy 1 of the Phase 7b document.
- Fixed in this change: 07 section 5's duration table held estimates; it holds the measured values and states the real budget ratios (it had said 2-3 times).
- Not a discrepancy of code but unverified: 04 section 5's claim that 14 of 14 multi-turn rollouts on 0.156 to 0.159 show `writable_roots: []` on later turns comes from a past investigation and was not re-counted here.
- No other mismatch was found in the sample.

**Not checked:** the exact hold checks inside each integration turn; the `say` file naming; 03 section 10 scheduling text; the turn directory number format (`NN` at least two digits) in code (only the test exists); the 06 acknowledgement timing beyond the tests; the wording of the systemd and launchd recipes (never run by CI); the claim that Claude and Codex plugin windows follow the selected model beyond the live checks; the 01, 02 and 03 parts not touched since `b1c16d0`; and every section not touched since `66aeb3a`.

## Limitations, reconciled

Phase 7b limitations (numbers as in that document):

| 7b | Limitation | Now |
|---|---|---|
| 1 | Intermittent Codex commit failure in E3 | **Probably resolved, not proven.** The cause traced in Task 74b (Codex 0.158+ protects the worktree git directory; the host's `auto_review` hid it under `$TMPDIR`) is fixed, and E3 passed at its first attempt with projects outside `$TMPDIR`. One passing invocation does not show the failure cannot recur; a second failing E3 in one invocation must still be a finding |
| 2 | Agent writes into the operator checkout | **Open, mitigated.** E2E escalates within a minute; in normal use the engine waits `integration_wait` (30 minutes) and never undoes such a write |
| 3 | No sandbox | **Open** (01 section 6) |
| 4 | Linux unverified | **Open** |
| 5 | No LICENSE | **Open** (owner decision) |
| 6 | Run directories grow unbounded | **Open** |
| 7 | `setsid` descendants | **Open** |
| 8 | Command acknowledgement outside the effect's transaction | **Open by design**, safe because replay is idempotent |
| 9 | `rejections_on_rung` written from the decision snapshot | **Open** (not changed by Tasks 74b-74i) |
| 10 | `take_over` replay respawns the pane | **Open** |
| 11 | Any failing check escalates a PR | **Narrowed.** Only a settled failing state escalates (Task 74e); required and optional checks are still not distinguished |
| 12 | Unidentified suite flake at `17d1d8f` | **Still unexplained.** 7 further full runs at this commit (2 `poe check`, 5 plain) and the 8 recorded in Phase 7b had no failure |
| 13 | Stale `TURN_STARTED` on a hold-stopped turn | **Open** |
| 14 | Unpushed local commits are not in a PR-mode root | **Open** |
| 15 | The engine does not restart itself after a fault stop | **Open** (service recipes not run by CI) |
| 16 | Cost is agent-estimated | **Open** |
| 17 | Owner decisions pending | **Open**: the license; the literal and no-comment conventions stay |
| 18 | Phase 8 not implemented | **Open** |

Open now, in addition (numbers continue):

19. **Codex on other configurations.** Behaviour on a host without `approvals_reviewer = auto_review`, with its own `default_permissions` profile, on Codex versions other than 0.159.2 (the host also runs a 0.159.3 app-server daemon, which this evidence does not cover) and on Linux is unverified; `doctor` warns on a version difference and probes the sandbox.
20. **PR mode was never exercised against real GitHub.** The integration tests use a bare remote and a fake `gh`.
21. **No unattended soak.** The longest real run is the 21-minute E2E invocation.
22. **A hung remote can hold the scheduling tick.** A base fetch in dispatch is bounded by `fetch_timeout_seconds` (30 s) but runs in the tick; with several ready PR-mode tasks and a hung remote the tick can stall up to 30 s per ready task before each backs off. Not measured.
23. **The single-flight notification hook** (Task 74d): an urgent notification can wait up to 30 s behind a stuck hook run.
24. **E2E fake-agent scenario duration** is not recorded in the artifact; the real scenarios are single samples.
25. **The smoke artifact's fingerprint** covers the evidence documents at `e78c057`; this document and the refreshed `e2e-results.md` post-date it.
26. **Smoke flake.** 1 of 6 smoke runs at the evidence commits failed in the interactive wizard stage (a 20 s wait for pane content), 5 passed; cause unknown. A failing smoke at release time should be rerun once and a second failure treated as a finding.
27. **Final independent review not done** (Task 74h, fourth criterion).

## Task 59, restated

Task 59 asked for E1-E6 with resume, model-change and takeover scenarios, and a portable clean-install smoke. Against this evidence: the gate criterion and the evidence document criterion hold; E1-E6 passed in one invocation at the first attempt, with resume (E5) and takeover (E6) native scenarios, and the live model-change contract checks now both pass (Codex and Claude with `sonnet`); 07 section 5 still defines no native model-change E2E scenario, so model change is covered by those live checks and by integration tests, not by `poe e2e`. The smoke passes on macOS only, so "portable" is not demonstrated. Task 59 therefore stays open on those two criteria.
