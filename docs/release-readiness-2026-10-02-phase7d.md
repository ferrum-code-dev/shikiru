# Phase 7d release-readiness evidence

Date: 2026-10-02. Code under test: `b1ee928` (tree clean; the commit of this document changes only documentation). The full E2E invocation, the first `poe check` runs and the first soak seeds ran at the earlier `159db49`; the two later commits (`7ac1139`, `b1ee928`) touch engine start-up, and the runs they invalidate were repeated at `b1ee928`, as listed below. The one thing not repeated at `b1ee928` is the full E2E invocation (not requested); the live Claude model-change check and the live Codex worktree-commit check, which start real turns through the spawn gate, were. This document records the Phase 7d fixes (Tasks 74j-74u), the final verification of Task 74p and what is still not verified. It amends [release-readiness-2026-10-01-phase7c.md](release-readiness-2026-10-01-phase7c.md); where they differ, this one is current. Two acceptance criteria of Task 74p are not covered by anything here and are left to the orchestrator: the first-use walkthrough and the final five-reviewer review. **Nothing in this document is a reviewer's verdict.**

## Release claim

| Use | Claim the evidence supports |
|---|---|
| Supervised use with Claude on macOS, trusted repository and host | **Supported.** E1-E6 passed in two full invocations (each needed one E3 rerun, see below), the suite is green, the live checks pass |
| Unattended use with Claude on macOS (the engine run by hand, launchd or systemd per 06 §5.8, notifications on) | **Supported for what was exercised:** the fake-agent soak (below) and the E2E runs with Claude. No soak with real agents ran, and the engine does not restart itself after a fault stop (the service manager does, 06 §5.8; that recipe is not run by CI) |
| Codex | **Supported only on the verified configuration:** Codex CLI 0.159.2, `approvals_reviewer = auto_review`, no custom `default_permissions` profile, project outside `$TMPDIR`, repository trusted. `shikiru doctor` probes the sandbox; treat a denial as a configuration fault. Other versions and hosts are unverified |
| Pull request mode | **Verified only against the fake `gh` and a bare remote.** Never run against real GitHub |
| Linux | **Excluded.** Designed for, never run there |
| Public distribution | **Excluded.** No LICENSE (owner decision), no CI or packaging metadata, Linux unverified, agents are not sandboxed |

Shikiru does not sandbox agents: Claude runs in `bypassPermissions`, Codex gets the shared `.git` as a writable root, and an agent can write into the operator's checkout or other worktrees; the engine reports a blocked merge and never undoes such a write (01 §6).

## Verification results

Host: macOS (Darwin 25.3.0), Claude Code 2.1.284, Codex CLI 0.159.2, tmux 3.7c, git 2.55.0, Python 3.14.7.

| Check | Result |
|---|---|
| `uv run poe check`, normal | At `159db49`: 942 passed, 15 skipped in 160.5 s (2 m 45 s wall), coverage 91.00% (gate 80%). At `b1ee928`'s tree (before its commit): 944 passed, 15 skipped in 152.3 s, coverage 91.00% |
| `uv run poe check`, `TERM=dumb` | Passed: 942 passed, 15 skipped in 150.3 s, coverage 90.99% |
| `uv run poe test --no-cov`, 5 further sequential runs | At `159db49` and again at `b1ee928`: all 10 passed (942 and 944 passed, 15 skipped, 119.4-120.9 s each) |
| `poe literals` / `poe comments` / `poe imports` | green; `literals kept by # keep:: 0` (no `# keep:` marker in `src`; the 7 occurrences in the repository are checker test fixtures); imports: "5 kept, 0 broken" |
| `poe soak --seed d7e` (default length) | 367.2 s, no violations. merge0: 70 tasks submitted, 81 done and 12 cancelled (plans and supersedes count children), 124 commands; faults: engine_kill 6, turn_kill 3, tmux_loss 2, engine_kill_integrating 2, git_hook_fail 2, engine_kill_locked 1, tmux_loss_takeover 1, engine_kill_escalating 1, engine_term 1, engine_freeze 1. pr1: 72 submitted, 83 done, 16 cancelled, 83 PRs landed and 4 closed, 140 commands; faults: engine_kill_integrating 4, turn_kill 3, engine_kill_locked 2, git_hook_fail 2, gh_fail 2, remote_down 1, hook_stuck 1, db_lock 1, engine_kill 1, engine_term 1, engine_kill_escalating 1, tmux_loss 1; engine fault scopes `engine loop` 2 and `dispatch fetch` 3, all recovered |
| `poe soak --seed d7g` at `b1ee928` | 373.3 s, no violations. merge0: 73 submitted, 103 done, 10 cancelled; faults: turn_kill 3, engine_term 3, git_hook_fail 3, engine_kill 3, engine_kill_integrating 2, engine_stop_start 1, engine_int 1, db_lock 1, tmux_loss 1. pr1: 71 submitted, 80 done, 15 cancelled, 81 PRs landed and 7 closed; faults: engine_kill_escalating 3, engine_kill_locked 2, remote_down 2, engine_freeze 2, git_hook_fail 1, engine_kill 1, tmux_loss 1, hook_fail 1, engine_int 1, engine_kill_integrating 1, tmux_loss_takeover 1, turn_kill 1, db_lock 1, engine_term 1; scopes `layout restore` 1, `dispatch fetch` 3, `notification hook` 1 |
| `poe soak --seed d7f` | 379.2 s, no violations. merge0: 76 submitted, 99 done, 11 cancelled; faults: engine_kill 4, db_lock 3, engine_term 3, turn_kill 3, engine_kill_integrating 2, tmux_loss 2, engine_stop_start 1, git_hook_fail 1. pr1: 73 submitted, 71 done, 9 cancelled, 71 PRs landed and 7 closed; faults: db_lock 4, engine_term 3, git_hook_fail 2, engine_kill_escalating 2, tmux_loss_takeover 2, gh_fail 1, hook_stuck 1, hook_fail 1, engine_int 1, turn_kill 1, tmux_loss 1, engine_kill_integrating 1, engine_kill_locked 1; scopes `dispatch fetch` 2, `layout restore` 2, `notification hook` 3 |
| `SHIKIRU_E2E=1 uv run poe e2e` at `159db49` | 7 passed in 24 m 28 s; E3 needed its one rerun (see below). Numbers in [e2e-results.md](e2e-results.md) and 07 §5 |
| Live Codex model change | 1 passed in 9.29 s |
| Live Claude model change (`sonnet`) | 1 passed in 10.30 s at `159db49`; 1 passed in 9.35 s at `b1ee928` |
| Live Codex worktree commit outside `$TMPDIR` | 1 passed in 28.89 s at `159db49`; 1 passed in 19.76 s at `b1ee928` |
| `shikiru doctor` on a scratch project (`init --yes`, no `--probe`) | Exit 0, every check ok: tmux 3.7c, git 2.55.0, repository, config, db schema 7, copy-on-write, Claude 2.1.284 authenticated, Codex 0.159.2 authenticated, `plugin:codex:sandbox` writable, fake plugin |
| Clean-install smoke `scripts/release-readiness-smoke.sh` | 8 of 8 passed at `159db49` and 5 of 5 at `b1ee928` (15-18 s each); `clean-install-latest.json` records `source_commit` `b1ee928`, status `passed`. Cumulatively 40 consecutive passes after the fix below; before it 2 failures in 11 runs |
| Linux | Not verified |

### Runs at earlier revisions in this phase (all recorded, none hidden)

Each was run before a later fix and is evidence about that earlier revision only:
- At `48b86ff`: `poe check` normal and `TERM=dumb` green (939 passed); 5 plain runs, **1 failed** (see flake 1); soak seeds `d7a` (326.7 s) and `d7b` (339.6 s) clean; two 12-iteration smoke loops and a load experiment.
- At `d5624af`: 5 plain runs green; one full E2E invocation passed (22 m 29 s, E3 one rerun, $1.4038); smoke 5 runs with **1 failure** (flake 3).
- At `a8e2dea`: `poe check` green twice, 5 plain runs with **1 failure** (flake 4); soak seeds `d7c` (6 m 52 s wall) and `d7d` (7 m 12 s wall) clean.

### Flakes found by this verification and what each was

Every failure was diagnosed to a cause. Every flake below has an identified cause.

1. **`start`: "engine did not acquire its lock after start"** (1 in about 25 plain runs at `48b86ff`, in `test_a_signal_ends_the_turn_releases_the_lock_and_a_restart_resumes[2]`). First recorded as unidentified because the detached engine's stderr went to `/dev/null`. Product changes: `d5624af` (`start` fails at once when the engine process exited) and `7ac1139` (the detached engine's stdout and stderr go to `.shikiru/logs/engine.out`, 0600, truncated by each start; `start`'s early-exit and lock-timeout errors quote its last 2000 bytes; `status` prints `last engine output: <path>` and `doctor` lists an `engine-output` check while the file is non-empty; tests `test_start_fails_with_the_engine_exit_when_the_engine_dies_at_start`, which feeds a corrupt database and asserts the cause and the file mode, and `test_status_and_doctor_point_at_the_output_of_a_stopped_engine`). A 40-iteration loop of `test_engine_signals.py` under heavy mixed load (five concurrent full suites) then reproduced it once, and the captured output named the cause: **"engine lock is held"**. Cause (a product defect): every liveness probe (`start`'s 50 ms wait, `status`, the test helpers) takes the engine lock exclusively for an instant, and an engine starting at that moment made its single non-blocking attempt, concluded another engine was running and exited, so `start` timed out after 5 s. Fix `b1ee928`: the engine retries every 20 ms for up to 1 s (03 §7); regression test `test_an_engine_started_while_a_liveness_probe_holds_the_lock_waits_and_takes_it` failed before (the engine task died with `EngineAlreadyRunningError`) and passes after. The reproduction was in `test_a_second_signal_ends_the_engine_by_force_and_a_restart_resumes` (iteration 1 of 40; the other 39 passed, run at `7ac1139` before the fix), not in the originally failing test, so that the original failure was this race is inferred from the identical symptom and the mechanism, not captured. No loop was run after the fix; the concurrent-start stress (10 parallel processes, 30 runs) was run before the capture existed and passed.
2. **`test_hook_descendants_die_with_the_hook[cancellation]`** (1 in a `poe check`): a genuine **product defect**, fixed in `a4b0e43`. A task cancelled while its process was still being started (before `asyncio` handed the process back) killed only the leader (asyncio's own cleanup), so hook descendants (`sleep 1000`) outlived the cancelled delivery: an orphan-process leak when the engine cancelled a hook delivery or helper call at that instant. Regression test `test_cancelled_while_the_process_is_still_starting_leaves_no_descendant` (deterministic, scheduled with `call_soon` so the cancel lands in the start window): failed before (descendant alive after 5 s), passes after. The start is now completed under `asyncio.shield` and the group killed; the spawn-gate pipe write end is closed on every path (it leaked when the start was cancelled).
3. **Smoke "normal-size inline interactive wizard" timeout** (also the unexplained flake of Phase 7c, same signature: the pane showed Step 7, then Step 6). Cause **identified**, a product defect in the wizard: after "Next" is hidden on the last step Textual resets keyboard focus **asynchronously, to "Back"**, so the smoke's `Tab` then `Enter` pressed Back whenever the keys arrived in that window (measured: with the reset done, `Tab` reaches "Run Doctor"; in the failing runs the order was different). Fixed in `a8e2dea`: focus moves to "Run Doctor" in the same update (06 §3); the smoke sends `Enter` only. Regression test `test_init_wizard_keyboard_focus_lands_on_run_doctor_when_next_leaves` (failed before with focus on Back). 35 passes after the fix.
4. **`test_a_blocked_hook_never_drops_an_urgent_event_but_bounds_the_informational_backlog`** (1 in 5 plain runs at `a8e2dea`): a **test defect**. Since Task 74t an urgent hook runs beside an informational one; the test's hook scripts wrote each event with two appends (`cat >>` then `echo >>`), so the two interleaved and the reader saw two JSON documents on one line (the captured file shows event 72 immediately followed by event 9). The scripts append one line per write now (`159db49`); the product behaviour asserted (urgent never dropped, backlog bounded) is unchanged.
5. **E3 fails its first attempt about half the time.** In both full invocations of this phase the Claude leader (haiku, low effort) triaged "Implement all tasks in phase B" as one implement task instead of a plan, so "2 new task windows were never open at once" (no engine fault, clean checkout, `an agent skipped a rule`); the one rerun passed. A targeted A/B with only E3 and four runs: with the Task 74j prompt sentence the first attempt passed 2 of 2; with the Phase 7c template (sentence removed) it failed 2 of 2, so the sentence is not the cause. Across today's six E3 first attempts 4 failed. This is agent variance; the E2E policy absorbs it with its one rerun but a first-attempt pass rate near half is a limitation of the scenario, not proof of the engine (limitation 28).

## Phase 7d items

Commits are `git log --oneline a371b41..b1ee928`. Tests named are the ones written first; "failed before" is as reported by the implementing agents (their mutation or revert checks are recorded in their commit work, not re-run here) except where stated "verified here".

| Task | Finding | Commits | Evidence |
|---|---|---|---|
| 74j (blocker) | An agent leaving its task branch reached `done` unintegrated | `8f8f1d5` | The worktree `HEAD` must be `refs/heads/<branch>` before facts, acceptance, rebase and integration (03 §6; prompt tells the agent). Integration tests with real git and the fake agent: switch branch and commit, detach `HEAD`, stay on branch, and in PR mode with the fake `gh` (`test_engine_integration.py`, `test_engine_pull_request.py`, `test_workspace_roots.py`). **Not exercised with a real agent**: an agent that deliberately leaves its branch cannot be scripted, so it stays covered by the integration tests only |
| 74k | No SIGTERM/SIGINT handling | `d177050`, `bde4ae5` | Graceful shutdown, bounded by `orchestration.shutdown_seconds`, second signal forces; `stop` returns after the lock is released. Real engine process tests (SIGTERM, SIGINT, second signal, `stop && start`) and the soak (`engine_term`, `engine_int`, `engine_stop_start` faults) |
| 74l | Notifications lost on restart; handler errors rejected | `8bf9a1b`, `0162c8d` | Durable notification cursor; `OperationalError` in a command handler retried. Integration tests; soak invariant 5 (every escalation and fault event reaches the hook) with kills at new escalations |
| 74m | Sandbox and reconcile fixes without regression power | `c3bc60f`, `f9fafbe`, `d8341af` | Linked-worktree Codex argv test, once-only orphan end test, a leader exit with a descendant holding stdout ends within the drain bound |
| 74n | Doc and evidence drift, tool floors | `513e9af`, `3240c25`, `c4b5f3b` | `doctor` fails tmux below 3.4 or git below 2.45; 07 §1, 05 §1, 02, 03 corrected; 7c document corrected |
| 74o | Test-value audit of the older suite | `d64aace`, `469b90e`, `049c11f`, `bea20ce`, `70b5180`, `48b86ff` | Recorded in `docs/test-audit-2026-10-01.md` |
| 74q (F10) | A turn completion overwrote an operator's rung | `d0bec33` (`ddee0d5` test support) | A transition writes only what the decision changed (03 §3, 05); table tests over accept, reject, escalate, rung escalation, completion |
| 74r (F22) | No-literals rule not mechanical | `3c0e3f5`, `d2b40d3`, `265ad47`, `dfe00b3`, `5d6b2e7` | `poe literals` in `poe check`, table-tested checker, 07 §2 |
| 74s | Start-up, tmux and process hygiene (stability) | `ab79c8e`, `997e054`, `5bcc026`, `44c2e27`, `3a54a6d`, `3334bfc`, `fc282ce`, `40b74bc` | Commands wait for the start sequence; cosmetic tmux faults never kill a turn; a failed take-over respawn is rolled back; migration in one immediate transaction; every helper leads its own group, spawn gate (a turn does not run until its pid is recorded); `turn_stopped`; cache copy, worktree removal and rebase timeouts leave classifiable states. Exercised by the E2E runs (spawn gate with Claude and Codex in real sessions) and the soak |
| 74t | Bounded resources, configurable timeouts (stability) | `3e957b2`, `0f9e513`, `c385303`, `2ab0fc2`, `320a1ba`, `24e145e`, `73411bb`, `0ebde48` | Per-tick dispatch budget, urgent notification slot, bounded reads, retention and run directory size in `status`, interrupted rebase undone |
| 74u | Soak and chaos run (stability evidence) | `46ff753`, `63ecdd6`, `f70829a`, `f9dca2a`, `a4a827a`, `9214c3c`, `212dc18`, `6c70790`, `7eff523`, `19b8199` | `poe soak`; it found five product defects (a locked database stopping the engine in three places, a failing shutdown step leaking turns, a CLI traceback), each fixed with a failing-first test; mutation checks of the engine in [soak-results.md](soak-results.md) (5 mutations each caught). Two more seeds in this document |
| 74p fixes | This verification | `d5624af`, `a4b0e43`, `a8e2dea`, `159db49`, `7ac1139`, `b1ee928` | Flakes 1-4 above |

## Limitations, reconciled

Phase 7c limitations (numbers as in that document). "Closed" means a task of Phase 7d fixed it; "open" ones are stated with why they are not a blocker or critical issue for the claimed use.

| 7c | Limitation | Now |
|---|---|---|
| 1 | Intermittent Codex commit failure in E3 | Resolved as far as evidenced: the live worktree-commit check and three E2E invocations (7c and two today, Codex in E3 and E6) show no denial; not proven impossible |
| 2 | Agent writes into the operator checkout | **Open, mitigated** (escalates; never undone, 01 §6). Visible bounded effect, workaround stated |
| 3 | No sandbox | **Open** (01 §6), design decision for a trusted repository and host |
| 4 | Linux unverified | **Open**, excluded from the claim |
| 5 | No LICENSE | **Open**, owner decision |
| 6 | Run directories grow unbounded | **Closed** by 74t |
| 7 | `setsid` descendants | **Open.** A descendant that leaves its process group (an agent's own daemon) is not killed; it needs an agent to deliberately detach. Not a work-loss or hang of Shikiru; listed for the reviewers to judge |
| 8 | Command acknowledgement outside the effect's transaction | **Open by design**, replay is idempotent (soak invariant 5) |
| 9 | `rejections_on_rung` overwritten | **Closed** by 74q |
| 10 | `take_over` replay respawns the pane | **Closed** by 74s (rollback and live-pane tag) |
| 11 | Any settled failing PR check escalates | **Open, narrowed.** Visible, the operator resolves it; PR mode only |
| 12 | Unidentified suite flake at `17d1d8f` | Still unexplained for that revision (it may well have been flake 1, whose cause was a start-up race that is now fixed; not shown). Every failure observed in this phase (flakes 1-4) was diagnosed |
| 13 | Stale `TURN_STARTED` | **Closed** by 74s |
| 14 | Unpushed local commits are not in a PR-mode root | **Open**, PR mode only, the operator pushes first |
| 15 | The engine does not restart itself after a fault stop | **Open.** The engine stops after `unexpected_fault_limit` unexpected faults and notifies; restart is the service manager's job (06 §5.8, recipe not run by CI). Judged operational, but the reviewers should confirm it is not a stability item for the unattended claim |
| 16 | Cost is agent-estimated | **Open** |
| 17 | Owner decisions pending | **Open**: the license. The literal and no-comment conventions are mechanical (`poe literals`, `poe comments`) |
| 18 | Phase 8 not implemented | **Open**, a later feature phase |
| 19 | Codex on other configurations | **Open**, excluded from the claim |
| 20 | PR mode never run against real GitHub | **Open**, stated |
| 21 | No unattended soak | **Closed for the fake agent** (six clean full-length seeds here at three revisions, `d7a` to `d7f`, plus five in Task 74u); no soak with real agents |
| 22 | A hung remote can hold the tick | **Closed** by 74t |
| 23 | A stuck hook can delay an urgent notification | **Closed** by 74t |
| 24 | Fake-agent E2E scenario duration not recorded | **Open**, minor (the pytest total is) |
| 25 | Smoke fingerprint post-dates the evidence docs | **Open**, minor; the artifact records `159db49`, this document and the e2e summary are committed after it |
| 26 | Smoke flake | **Closed**: cause found (wizard focus reset) and fixed, 35 passes after |
| 27 | Final independent review not done | **Open** (orchestrator, Task 74p) |

New in this document:

28. **E3's first attempt fails about half the time** (flake 5): agent triage variance, absorbed by the one rerun; a E3 that fails both attempts is a finding.
29. **Task 74j is not exercised with a real agent** (integration tests only).
30. **The engine output file is bounded by truncation per start, not by a size cap**: it holds what the engine process prints (normally nothing, an exception trace on a crash); a pathological flood would grow it until the next start.
30a. **Load sensitivity of two tests.** With five full suites running at once on this host (about 4 s per test second), `test_a_long_lived_engine_keeps_its_run_directories_bounded` (3 times) and one scheduler test exceeded their 60 s waits; both pass in 12 s and 1 s on an idle host and in all single-suite runs. That is overload of the host, not a product finding.
31. **Revision coverage.** The full E2E invocation ran at `159db49` (and earlier at `d5624af`); it was not repeated after `7ac1139` and `b1ee928` (engine output redirection and the engine's lock retry). Those two were covered by `poe check`, 5 plain runs, 5 smoke runs, soak seed `d7g` and the live Claude and Codex turns at `b1ee928`.
32. The first-use walkthrough (Task 74p AC 3) has not been run.

## Task 59, restated

Task 59 asked for E1-E6 with resume, model-change and takeover scenarios, and a portable clean-install smoke. Against this evidence: the gate criterion and the evidence criterion hold; E1-E6 passed in one invocation at the final revision (E3 after one rerun, an agent decision); E5 (resume) and E6 (takeover) are native scenarios; model change is covered by the two live checks (Codex and Claude with `sonnet`) and integration tests, not by a native E2E scenario (07 §5 defines none); the smoke passes on macOS only, so "portable" is still not demonstrated. Task 59 stays open on those two criteria.
