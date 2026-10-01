# Test-value audit, 2026-10-01

Owner question: the suite grew from 779 to 974 collected tests since `66aeb3a` while coverage stayed near 89%. Every test added or changed in `git diff 66aeb3a..HEAD -- tests` was audited against AGENTS.md (behaviour over implementation; unit tests only for domain rules, models and parsers; one table per rule; no tests of framework, enum or constant listings, wording or private attributes; no mocks of own classes).

Scan results: no mocks of Shikiru classes, no sleeps, no access to private attributes of production classes (the `self._x` hits are private state of test-local helper classes). Wording asserts were limited to a handful of `match=`/`in text` checks. The problems were test-tooling volume, near-duplicate scenarios and enum-listing tables.

Numbers: before 974 collected (959 passed, 15 skipped), coverage 89.93% at `237b559`; after 943 collected (928 passed, 15 skipped), coverage 89.93%.

Safety rule: a test was removed only when a retained test fails for the same reverted fix. Mutations were run in a scratch `git archive` tree (main tree never edited): packet write failure re-raised (4 retained packet tests fail), packet evidence collected before the directory check (merged restart test fails), PR close loop reduced to one attempt (retry-after-restart test fails), integration rebase not fetching the base (retry-after-conflict test fails), `next_backoff` uncapped (3 unit rows fail), `Database` creating the file world-readable (CLI owner-only test fails, which is why that test was KEPT rather than removed).

## Test tooling

| Test | Verdict | Reason / covering test |
|---|---|---|
| test_e2e_guards: stall-window table | KEEP | rule table |
| test_e2e_guards: stalled attempt fails fast with diagnostics | KEEP | trimmed the format-wording assert; the evidence identities stay |
| test_e2e_guards: sandbox denial found in snapshot | KEEP | parser feeding the rerun rule |
| test_e2e_guards: attempt outliving its budget | KEEP | budget rule, `match=` wording removed |
| test_e2e_guards: finished attempt returns | REMOVE | restates the trivial path; every other guard test returns through it |
| test_e2e_guards: rerun table 13 rows | REWRITE | 10 rows; dropped a second sandbox row, a lone ENGINE_FAULT row and ShikiruError (same outcome as OSError / fault+waiting rows) |
| test_e2e_guards: invocation budget | KEEP | rule |
| test_e2e_results: session without real run preserves results | KEEP | rule |
| test_e2e_results: source/tools/window recorded | REMOVE | listed keys and versions, restating the implementation |
| test_e2e_results: later invocation does not overwrite / every attempt recorded | MERGE | one scenario with failed+passed attempts then a later invocation |
| test_e2e_results: source_state dirty | KEEP | provenance rule |
| test_comment_rule: allowed table, rejected table, line report | MERGE | one table (21 -> 12 rows); line report removed as trivial |
| test_import_contract_completeness | KEEP | one table protecting 02 §2 completeness |
| test_codex_sandbox_probe: pass, fail table | MERGE | one table, 3 rows; real-codex test KEEP (skipped without the CLI) |

## Engine: escalation packet and replay families

| Test | Verdict | Reason / covering test |
|---|---|---|
| escalation_faults: flow cannot write packet | KEEP | engine-level repair with unreadable history |
| escalation_faults: dependency escalation not blocking others | KEEP | also subsumes the removed scheduler dependency test |
| escalation_faults: restart + git evidence per tick | MERGE | the two tests share one setup; one scenario asserts fault count, no repeated git calls and repair |
| scheduler: dependency escalation with unwritable packet | REMOVE | covered by the dependency test above (mutation: packet write re-raised) |
| scheduler: dispatch escalation with unwritable packet | KEEP | distinct raise path |
| escalations: state failure rolls back packet | KEEP | rollback rule |
| escalations: unwritable packet follows repair | KEEP | the only test of valid reports restored into a repaired packet |
| daemon: replay say, submit, hand_back, retry, take_over | KEEP | each handler has its own replay logic |
| daemon: replay cancel, set_rung, pause | MERGE | one table with the applied state and the single event |
| daemon: replay resolve cancel/retry | REWRITE | cancel only; both resolutions share the replay path |
| daemon: PR close cancel/resolve, slow close, retry after restart | KEEP | distinct entry points and a non-blocking property |
| daemon: PR close failing once then succeeding | REMOVE | covered by retry-after-restart (mutation: single attempt) |
| daemon: take-over lost, cleanup failure, dirty worktree, owner-only, log rotation | KEEP | distinct rules |

## Engine: pull request, holds, faults, rungs

| Test | Verdict | Reason / covering test |
|---|---|---|
| pull_request: case table | REWRITE | dropped the plain closed-retry case, the base-moved variant subsumes it |
| pull_request: stall table | REWRITE | dropped packet wording text, kept reason, URL and state |
| pull_request: pause during polling + paused past budget | MERGE | one scenario |
| pull_request: failed polls retried / exhausted | MERGE | one table of two outcomes |
| pull_request: rebase starts from remote base | REMOVE | retry-after-conflict fails when the base is not fetched |
| pull_request: other new tests (unsettled checks, merged-missing, root from remote base, stale fetch failure, merged branch kept, retry rebases) | KEEP | distinct rules |
| holds: nudge, spawn window, conflict turn, lock wait, base-access budget | KEEP | one test per execution path (invariant 2) |
| tick_guard: backoff, unexpected limit, reset, flapping, classification | KEEP | `match=` wording removed |
| tick_guard: thousands of recoverable faults | REMOVE | covered by the `next_backoff` unit table (mutation: uncapped) and the flapping test |
| faults (7 tests) | KEEP | distinct recovery paths; wording asserts replaced by the observable stop reason |
| flow: dirty supersede, set_rung counters | KEEP | rules |
| flow: triage complexity table | REWRITE | explicit low case removed (enum listing) |
| flow / recovery: pin survives the move | REWRITE | leader/implementer parametrization reduced to the implementer, the case the bug cleared; recovery keeps its two move kinds |
| recovery: hold during quota wait (paused/intervention) | KEEP | both hold kinds block the continue turn |
| store_tasks: mode in same write, override merge table | KEEP | rules |
| flow_liveness, decomposition, integration (local merge), notifications, notification_delivery, turn_ordering, turns, workspace_roots, git_mutation_cancel, process_identity, turn_processes, operator_fault_status, dashboard header, private_dirs, store_database migration | KEEP | distinct behaviours at real boundaries; fault-status test lost two shape asserts |
| operator_cli: init and submit owner-only | KEEP | removal attempted; mutation (world-readable db) shows no other test fails |
| store_events: command-applied lookup uses the kind index | KEEP (unsure) | asserts an index name, an implementation detail, but no behavioural test catches the performance fix |

## Domain unit tests

| Test | Verdict | Reason |
|---|---|---|
| scheduling: ready tasks | REWRITE | per-Hold parametrization removed |
| steps: triage mode | MERGE | folded into the triage-plan test; the two-mode table is gone |
| steps: triage complexity | REWRITE | explicit-low row removed; unset and explicit-normal stay |
| steps / ladder / escalation: override fields | KEEP | renamed-field rules |
| faults: streaks, backoff | KEEP | domain rules |
| process_identity | KEEP | parser/rule |

## Unchanged-in-substance edits

Constructor-argument updates (`pr_wait`, dropped `overrides={}`), terminal sizes, fixtures and support modules were reviewed and kept; they carry no tests of their own. Other changed tests (dashboard widgets/wizard/motion/palette, headless contracts, e2e conftest/real agents) were reviewed: KEEP.
