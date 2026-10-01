# Phase 7b release-readiness evidence

Later evidence and the current release claim: [release-readiness-2026-10-01-phase7c.md](release-readiness-2026-10-01-phase7c.md).

Date: 2026-10-01 (the E2E invocation started 2026-09-30 22:33 UTC and the artifact is dated 2026-09-30). Source commit: `6116446` (Task 74a, bounded E2E). This document records the three independent reviews of `66aeb3a`, the fixes of Tasks 60-74a and the evidence for them. It amends [release-readiness-2026-09-30.md](release-readiness-2026-09-30.md); where the two differ, this one is current.

## Release claim

The evidence supports **supervised use** on macOS by an operator who watches the engine and trusts the repository and host (01 section 6). It does not support unattended operation, Linux use or public distribution:

- Every reviewer item that could be fixed in code was fixed and tested; the items left are listed below.
- The real-agent E2E passed, but only after one automatic rerun, and the failed attempt showed an intermittent agent-side failure that is not understood (limitation 1).
- Shikiru does not sandbox agents. An agent can write into the operator's checkout or other worktrees. The engine now reports it when it blocks a merge; it does not prevent it.
- Linux is unverified, the repository has no LICENSE, and run directories grow without bound.

## Verification results

| Check | Result |
|---|---|
| `uv run poe check`, normal environment | Passed at `6116446`: 894 passed, 15 skipped, 89.62% coverage (gate 80%). Earlier at `3615b39`: 873 passed, 15 skipped, 89.62% |
| `uv run poe check`, `TERM=dumb` | Passed at `6116446`: 894 passed, 15 skipped, 89.62%. Earlier at `3615b39`: 873 passed, 15 skipped, 89.60% |
| `uv run poe test --no-cov`, repeated | No failure in 8 sequential runs: 3 at `6116446` (894 passed, 15 skipped each) and 5 at `3615b39` (873 passed, 15 skipped each); 82-107 s per run |
| `SHIKIRU_E2E=1 uv run poe e2e`, one invocation | Passed at `6116446`: 7 passed in 31 m 11 s (fake-agent scenario and real E1-E6), exit status 0, one automatic rerun (E3 attempt 0 failed). Artifact `build/e2e/2026-09-30.json` (git-ignored) records `source_commit` `6116446fe768caf044e4ffe3d806517a51f8d869`, `source_dirty` false, `started_at` 2026-09-30T22:33:26Z, `finished_at` 23:04:37Z, `exit_status` 0 |
| Live model-change checks | Codex passed (7.64 s), Claude with `SHIKIRU_CLAUDE_LIVE_CHANGE_MODEL=sonnet` passed (9.88 s), both at `6116446`, see below |
| Clean-install smoke `scripts/release-readiness-smoke.sh` | Passed on macOS (Darwin 25.3.0) at `6116446`, about 18 s. `build/release-readiness/clean-install-2026-10-01.json` records `source_commit` `6116446fe768caf044e4ffe3d806517a51f8d869` and status `passed`. The commit of this document changes only documentation after that run |
| Linux | **Not verified.** The docker daemon was down and nothing was started or installed. The smoke script is written for GNU/Linux but has never been run there |

Tested CLI versions: Claude Code 2.1.284, Codex CLI 0.159.2, tmux 3.7c, git 2.55.0, Python 3.14.7.

An earlier invocation at `3615b39` (started 2026-09-30 23:19 CEST, before the bounds existed) was interrupted by the coordinator after 50 minutes: E1, E2 and E5 had passed and E3 attempt 0 stalled for 26 minutes. It is not evidence of a pass and is described under limitation 2.

### E2E scenarios and agent-reported cost

Costs are the agents' own estimates, not verified billing. The artifact records only the final attempt of a scenario, so E3's cost may be partial. Scenarios use the cheapest rungs (Claude haiku/low, Codex gpt-5.6-luna/low).

| Scenario | Covers | Result | Cost |
|---|---|---|---|
| E1 | triage, implement, verify, merge, tracker tick | passed | $0.1209 |
| E2 | operator message delivered to verify | passed | $0.1577 |
| E3 | plan, dependent tasks in parallel, conflict resolution | attempt 0 failed, rerun passed | $0.9313 |
| E4 | AI operator resolves a product decision over MCP | passed | $0.1840 |
| E5 | SIGKILL of the engine mid-turn, restart resumes the same session | passed | $0.1091 |
| E6 | take over one task's role, hand back | passed | $0.3119 |
| Sum | | | $1.8149 |

Per-scenario wall durations were not recorded: the artifact holds only the invocation window (31 m 11 s for everything, rerun included), and the passing scenarios' temporary directories are removed by pytest. 07 section 5's per-scenario durations therefore remain estimates and are stated as such; they were not replaced with invented numbers.

E3 attempt 0 (diagnostics copied to the evidence scratchpad; `build/e2e/diagnostics/2026-09-30-e3-attempt-0.txt`): T-2's Codex implementer finished the code but reported that it could not commit because "Git was denied permission to create the worktree index lock"; the Leader consulted and escalated T-2 with `decision_required`, and the attempt ended with the task tree unfinished. The operator checkout was clean. The automatic rerun passed. The run files of the attempt were removed with the pytest directory, so whether this was a Codex sandbox denial on the shared git directory (which Shikiru adds as a writable root, 04 section 5), a transient failure or something else was not established.

### Live model-change contract checks

Run natively on 2026-10-01 at `6116446`, subscription access (no API spend recorded):

| Command | Result |
|---|---|
| `SHIKIRU_E2E=1 uv run pytest --no-cov -n 0 tests/integration/test_codex_headless_contract.py -k live_model_change -q` | 1 passed in 7.64 s: the nonce is recalled across the model change and each turn's emitted `-c model_auto_compact_token_limit` follows the selected model |
| `SHIKIRU_E2E=1 SHIKIRU_CLAUDE_LIVE_CHANGE_MODEL=sonnet uv run pytest --no-cov -n 0 tests/integration/test_claude_headless_contract.py -k live_model_change -q` | 1 passed in 9.88 s: base model haiku, changed to sonnet, nonce recalled, hook compaction limit follows the selected model |

Both model pairs report the same window, so these runs prove that the limit is taken from each turn's model and that the real formats parse, not that a per-model window differs; the offline test with a genuine redacted rollout covers a differing window. No real-agent E2E scenario changes a live session's model (07 section 5 defines none).

## Reviewer items

Sources: the three independent read-only reviews of `66aeb3a` (F01-F08, F09-F23, whole-system release readiness), as recorded in `tasks/plan.md` Phase 7b and [release-readiness-2026-09-30.md](release-readiness-2026-09-30.md). Status: fixed, partially fixed, or limitation.

| Item | Task | Commit(s) | Status |
|---|---|---|---|
| One tick fault, a vanished tmux server or session ended or wedged the engine; an unrecoverable stop was silent | 60 | `56e5df0`, `a031ea8` | Fixed: `TickGuard` with bounded backoff, tmux session recreated, `engine_stopped` carries the reason and notifies, service-wrapper recipe documented (06 section 5.8). An external supervisor is still needed to restart a stopped engine |
| A running task could be left without a flow (flow exception, `answer` race) | 61 | `78c3a51` | Fixed: per-tick live-flow ensure |
| An unwritable escalation packet killed the flow or wedged the engine; a restored packet lacked evidence | 62 | `a57a463` | Fixed: the escalation is the row, the packet is a rendering; a restored packet carries the recorded reports, facts and turn tail that still exist |
| `supersede` and cancelling an `uncommitted_work` escalation could erase uncommitted work | 63 | `08d8182` | Fixed; ignored files stay outside the guard, documented |
| Operator rung pins were lost by transitions (F10 residuals) | 64 | `4c9e9d9` | Fixed for every transition path. `rejections_on_rung` is still written from the decision snapshot (limitation 9) |
| Holds did not cover nudges, the spawn window, `rebase --continue`, PR polling, dispatch, the lock wait | 65 | `fb15806` | Fixed; 03 section 6 lists the paths |
| Replayed commands reported a rejection; operator messages lost in a crash window; non-atomic `take_over`; unsafe message requeue | 66 | `fc524a2`, `4ff2386` | Fixed for the commands and the message queue; `take_over` replay respawns the pane (limitation 10) |
| PR polling waited forever; cancel left a PR open; merged local branch kept; task roots started from a stale base | 67 | `f8104bc`, `490dec2`, `c020801`, `3785a75` | Fixed; any failing check escalates a PR (limitation 11) |
| `RepoState.base_ref` failed to load for old rows; explicit complexity replaced by triage; E2E CPU caps | 68 | `aa4f9a7`, `c21176d`, `f2bbf20` | Fixed |
| Notification hooks blocked the loop or outlived their timeout; cancelled git killed mid-operation; a dead-leader turn group unrecorded; start-time comparison fragile on Linux | 69 | `463bbe8`, `d0201b3`, `752e019` | Fixed; a `setsid` descendant is documented as outside the guarantee (limitation 7) |
| Trust boundary undocumented; `.shikiru` world-readable; unbounded logs; smoke macOS-only | 70 | `e755356`, `e5431e0`, `f2d4730`, `17d1d8f` | Partially fixed: the trust boundary is documented, the state is private, the engine log rotates, the smoke is written for Linux and verified on macOS only. Run directories grow unbounded and there is no LICENSE (limitations 5, 6) |
| Comment and lint claims exceeded what tools enforce (F20, F22) | 71 | `42e9e68`, `5438c8b` | Fixed: a mechanical comment check with a keep marker, exact import and banned-api claims; the literal rule stays a review convention |
| TaskFlow still concentrated policies (F18) | 72 | `2b8169f`, `c3918fc`, `3201026`, `908cc3e`, `388588b`, `c02c6d6` | Fixed |
| F06/F07 not covered through real wiring; live model-change unasserted; evidence artifact lacked commit and tool versions; flaky process tests | 73 | `4b365f5`, `b8de3d0`, `7c547a7`, `aee0b78`, `7f688d8`, `c721a7f`, `29064ed`, `a2b8bad`, `3615b39` | Fixed; the single original flake was never identified (limitation 12) |
| E2E attempts of 30 minutes; silent merge wait on a dirty operator checkout (the 2026-09-30 E3 stall) | 74a | `583c90a`, `6116446` | Partially fixed: bounded attempts, a stall detector, diagnostics, `integration_wait=60` in E2E and a notified `integration_waiting` event. The agent's write outside its worktree cannot be prevented (limitation 3) |
| F01-F23 of the earlier review (Phase 7) | 41-58 | see the 2026-09-30 document | As recorded there; the items it marked resolved by Phase 7b are resolved above |

## Do all design sections agree with the code?

**Method.** `git diff 66aeb3a..HEAD -- docs/design` touches all seven design files (01: D8 and the trust boundary; 02: engine faults, module table, enforced rules, run tree, permissions; 03: holds, liveness, replay, escalation atomicity, supersede, base ref, `integration_waiting`; 04: trust notes; 05: config, events, commands, migrations, growth; 06: guard, notifier, service recipes; 07: values and comment rules, E2E bounds, platforms). I read every added line and checked a sample against the code.

**Checked against the code and found to match:**
- 02 engine file list against `src/shikiru/engine/` (every module listed exists and every engine module is listed); `TickGuard` recoverable (`ShikiruError`, `OSError`) versus unexpected split and the `engine_stopped` reason; `orchestration.faults` and `[logging]` values in `defaults.toml`.
- 03/04/05 constants: `START_TIME_TOLERANCE_SECONDS` 10 s, `GIT_STOP_GRACE` 10 s, `PR_POLL` 5 min, `pr_wait` 604800, the failing check states in `workspace/github.py`, `MAX_DISPATCH_FAILURES` 3.
- 03 command results: `kept_worktree` in the cancel and resolve handlers; 05 private modes (`PRIVATE_DIR_MODE` 0700, `PRIVATE_FILE_MODE` 0600); migrations for `repo.base_ref` and `complexity_explicit`.
- 03 section 6 `integration_waiting`, 05 event list and 06 notifier list against `EventKind.INTEGRATION_WAITING`, `integration.py` and `notifier.py`; 07 section 5 budgets, stall window (4 minutes), `integration_wait=60` and `max_quota_wait=300` against `tests/e2e/budgets.py`, `watchdog.py` and `test_real_agents.py`.
- Everything the 2026-09-30 document recorded as checked (slots, lint contract, notification queue, holds, supersede seeding, escalation atomicity, Codex window rule) was not re-verified line by line at `6116446`; it was covered by the passing tests.

**Not checked:** the exact set of hold checks inside each integration turn, the `say` file naming, the 03 section 10 scheduling text, the 06 acknowledgement timing beyond the tests that exercise them, the wording of the systemd and launchd recipes (documented shape, never run by CI), the 07 instruction to keep the rerun classes aligned with `tests/e2e/diagnostics.py`, and every section not touched since `66aeb3a`.

**Residual discrepancies:**
1. 04 section 5 says every Codex turn adds `-c model_auto_compact_token_limit`; `CodexTurn._token_limit` returns `None` and the flag is omitted when no window is known. Only the doctor gate at `start` prevents this.
2. 07 section 5 gives per-scenario durations as estimates; the results artifact does not record them, so they could not be replaced with measurements.
3. 02 section 3 is an illustrative tree and not exhaustive by intent.

No other discrepancy was found in the sampled parts. This is not a claim that every line of every design section was re-verified.

## Remaining limitations

Reconciled with the 2026-09-30 document: its limitations 1, 2, 4, 5 and 7 are resolved and not repeated; its limitations 3, 6, 8, 9, 10 and 11 are carried or restated below.

1. **Intermittent Codex commit failure in E3.** One E3 attempt of the final invocation escalated because a Codex implementer reported a denied git index lock in its worktree. The cause was not established (run files gone). The harness reran it and it passed. Until understood, expect occasional reruns; a second failure in one invocation must be treated as a finding.
2. **Agent writes into the operator checkout (2026-09-30 E3 incident).** A Codex implementer wrote the tracker and test files into the project checkout by absolute path while also editing its worktree, and the merge waited for a clean checkout until the attempt timed out. Isolation is a prompt convention (01 section 6), and the E2E projects live under the system temp directory, which Codex's sandbox probably leaves writable (not verified). Shikiru now records and notifies `integration_waiting` with the dirty paths and, in E2E, escalates within a minute; in normal use it still waits up to `integration_wait` (30 minutes) before `integration_blocked`, and it never detects or undoes such a write otherwise.
3. **No sandbox.** Claude runs in `bypassPermissions`, Codex gets the shared `.git` as a writable root, and worktree isolation is not enforced (01 section 6). Use only on repositories and hosts you trust.
4. **Linux unverified.** The smoke script and the platform notes are written for GNU/Linux but no Linux run was made. macOS only.
5. **No LICENSE.** The repository has no LICENSE file and no `license` field in `pyproject.toml`; a license is an owner decision and must precede any public distribution.
6. **Run directories grow without bound.** Only the engine log rotates. There is no pruning command by design (05 growth note).
7. **`setsid` descendants.** A process that leaves its group with `setsid` is outside `stop()` and orphan cleanup (03 section 4.2).
8. **Command acknowledgement is not in the effect's transaction.** Replay is idempotent by command id, which makes this safe (2026-09-30 limitation 8).
9. **`rejections_on_rung` counters are written from the decision snapshot**, so a concurrent operator change to the counter itself (not the rung pins) is overwritten by the turn's decision.
10. **`take_over` replay respawns the pane** when the role is still taken over, which can disturb a pane the operator already attached to.
11. **Any failing check escalates a PR**; required and optional checks are not distinguished (03 section 6).
12. **Unidentified flake.** One original full-suite failure at `17d1d8f` was never reproduced in 20 runs plus load runs, and is not explained. The 8 runs recorded above had none.
13. **Stale `TURN_STARTED` event on a hold-stopped turn**: the timeline shows a turn as started that a hold stopped before it produced anything.
14. **Unpushed local commits are not part of a PR-mode task root**: a pr-mode root starts from the fetched remote base (03 section 6).
15. **The engine does not restart itself** after a fault stop; an external service manager is required (06 section 5.8, recipes not run by CI).
16. **Reporting is agent-estimated cost**; no billing was verified, and E2E costs are partial for a rerun scenario.
17. **Owner decisions still pending**: the license (item 5), and the literal and no-comment conventions (kept by decision 2026-09-30).
18. **Phase 8 (operator workspace)** is planned and not implemented; nothing in this evidence covers it.

Limitations 9 to 11, 13 and 14 come from Task notes and reviewer findings recorded in `tasks/plan.md` and the 2026-09-30 document; they were not re-tested on this revision beyond the passing suite.

## Phase 7c: Codex sandbox roots (Task 74b)

- Free probes on Codex 0.159.2 (macOS, project outside `$TMPDIR` under `~/.cache`): `codex sandbox -c sandbox_mode="workspace-write" -c sandbox_workspace_write.writable_roots=[<common .git>]` in a linked worktree denies `<common>/worktrees/<id>/index.lock` (`Operation not permitted`) while the common directory itself is writable; adding `git rev-parse --absolute-git-dir` as a root makes `git commit` pass. `-P :workspace` ignores `sandbox_workspace_write`; `codex sandbox` needs the worktree as cwd, not `-C`.
- Live check, 2026-10-01, Codex 0.159.2, first turn then `exec resume` on the test's other model: `SHIKIRU_E2E=1 uv run pytest --no-cov -n 0 tests/integration/test_codex_headless_contract.py -k worktree_commits -q` passed (1 passed, 18 s) with the repository, worktree and run directory under `~/.cache/shikiru-e2e/`, and no denial marker in either turn's stream. The session rollout shows `writable_roots` = run directory, worktree git directory and common `.git` on both the first and the resumed turn.
- Not verified: a host without `approvals_reviewer = auto_review` (this host has it, which masked the earlier denial), a host with its own `default_permissions` profile, Codex versions other than 0.159.2 and Linux (`codex sandbox` there uses another backend). `shikiru doctor` now probes the sandbox and warns when the version is not 0.159.2. The full `poe e2e` was not re-run for this task (Task 74h).
