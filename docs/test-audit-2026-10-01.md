# Test-value audit, 2026-10-01

Owner question: the suite grew from 779 to 974 collected tests since `66aeb3a` while coverage stayed near 89%. Every test added or changed in `git diff 66aeb3a..HEAD -- tests` was audited against AGENTS.md (behaviour over implementation; unit tests only for domain rules, models and parsers; one table per rule; no tests of framework, enum or constant listings, wording or private attributes; no mocks of own classes).

Scan results: no mocks of Shikiru classes, no sleeps, no access to private attributes of production classes (the `self._x` hits are private state of test-local helper classes). Wording asserts were limited to a handful of `match=`/`in text` checks. The problems were test-tooling volume, near-duplicate scenarios and enum-listing tables.

Scope and status as of 2026-10-01: the audit covered only the tests added or changed since `66aeb3a`; the files older than that were audited by Task 74o (last section). The suite was 779 collected at `66aeb3a`, 974 at `237b559`, 943 after this audit and about 1072 after Phase 7d, so the inflation is reduced in the audited part, not undone; the numbers are updated by Tasks 74o and 74p.

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

## Task 74o: the older suite (2026-10-01)

Method: every file with more than about 15 tests or with one of the reviewer's patterns (`match=` wording, own-class stubs, duplicated polling closures, an index name, a monkeypatched own function) was read, its collected tests ranked by smell, and the verdict recorded below. A test was removed or merged only when another test still fails for the same reverted fix (mutations below) or it tested only a framework feature, an enumeration, a wording or trivial wiring.

Numbers (collected / run), `c4b5f3b` before, after the last commit of this task:

| | collected | passed | skipped | coverage |
|---|---|---|---|---|
| before | 1079 | 1058 | 21 | 90.90% |
| after | 961 | 939 | 15 | 90.84% |

Per directory (collected): unit 381 -> 319, integration 691 -> 635, e2e 7 -> 7. The skip count is environment dependent (real-agent contract checks and Docker, 15 in the final run) and was 21 in the baseline run; no skipped test was removed by this task.

### Verdicts

| File (tests) | Verdict | Reason / covering test or mutation |
|---|---|---|
| test_prompts_renderer (47 -> 11) | MERGED | 9 templates x 3 payloads re-tested `domain.fencing.fence`: one rendering test per template now uses the hostile backtick payload; 9 `StrictUndefined` tests removed (Jinja feature). Mutation M1 |
| test_ui_glyphs (42 -> 8) | MERGED | 35 one-per-glyph tests became one invariant over `ALL_GLYPHS`; the width-rule table stays |
| test_literal_rule (32 -> 25) | MERGED | three tables became one (violation lines + kept count); rows with identical code paths dropped (float, tuple-in-comparison variants); AnnAssign and `spawn` rows kept because they are separate branches |
| test_comment_rule (12), test_import_contract_completeness (6), test_e2e_results (3), test_soak_smoke (1) | KEPT | one table per tool rule, already reduced by 74i; the e2e_guards stall report assert lost its format-wording split |
| test_tmux_isolation (1) | REMOVED | asserted the fixture's own socket name (tests the test framework) |
| test_domain_escalation (24 -> 10) | MERGED | 15 per-reason tests of "exactly one recommended option" are one loop over the enum |
| test_engine_escalations (18 -> 4) | MERGED | 15 per-reason packet tests are one loop; the heading-presence asserts (template shape) dropped, evidence and the single star stay |
| test_domain_reports (27 -> 20) | REMOVED / MERGED | report-kind to model mapping test removed (M6: wrong mapping fails four flow tests); `depends_on` accept table 4 -> 2 rows; invalid-fields table (18) kept, it is the rule table |
| test_operator_render | REWRITTEN | box-drawing, wording and "absent text that was never seeded" asserts removed; the data values stay |
| test_operator_cli | REWRITTEN / REMOVED | wording asserts removed; the palette test replaced our own `run_palette` by monkeypatch and only verified a three-line env lookup (trivial wiring), removed; the real `palette` command cannot run without a terminal |
| test_store_events | REWRITTEN | the index-name assert became "the plan searches, never scans" (M4) |
| test_engine_daemon (50 -> 49) | KEPT / REMOVED | one test per replay or handler; the helper-without-headless build test duplicated the `Helper` constructor test (M5) |
| test_process (16 -> 13) | MERGED | stdin, cwd and exit status in one scenario (mutations M7, M8: ignoring cwd or stdin fails it) |
| test_config_loader (16) | MERGED | seven error-attribution tests became one table of (source, prefix); no wording beyond the source |
| `match=` in 26 places | REWRITTEN | exception type only; kept `match` only on four generic `ValueError` guards (ruff PT011 requires it) and the injected failure identity in `test_engine_escalations` |
| test_agents_registry, test_dashboard_app | REWRITTEN | the factory-failure test asserts the chained cause, not its message; ambiguity tests keep the distribution names (the operator needs them) |
| test_engine_integration (27), engine_flow (23), pull_request (22), workspace_roots (19), escalations, scheduler (17), store_tasks (16), store_database (14), operator_doctor (16), operator_sessions (14), engine_restart (15 rows), engine_faults, recovery, retention, domain_steps (39), domain_scheduling (21), domain_motion (18), ui_strip, sdk_reset_time (16), headless contracts (19 each), test_e2e_guards (19) | KEPT | each test is a distinct rule, recovery path or table row at a real boundary (names reviewed one by one); the domain files are rule tables, no enumeration or wording tests |
| 17 `wait_for`-style closures (engine_daemon x9, restart x4, recovery, retention, faults, flow_liveness, notification_delivery, decomposition, turn_processes, reconcile_orphans, operator_stop_wait, escalation_faults, scheduler, pull_request) | REPLACED | one helper `tests/support/waiting.wait_until_advancing`; the three special cases (deadline-bounded advance in recovery, view-count gate in pull_request, `wait_for_command`) keep their own predicate |
| `_StubServices` x3 (+ `_DashboardSources` in dashboard_motion), `_WidgetHostApp` x2, `_context`/`_views` x3 | REPLACED | `tests/support/dashboard.py` (`StubServices`, `WidgetHostApp`, `widget_context`, `state_views`) |
| `_FakeDistribution` / `_FakeEntryPoint` x2 | REPLACED | `tests/support/entry_points.py` |

### Mutations (main tree, restored after each run)

- M1: `report.json | fenced("json")` -> `report.json` in `_macros.md.j2`: the rendering test fails for every template.
- M4: `WHERE kind = ?` -> `kind || '' = ?` (defeats the index): the lookup test fails.
- M5: `if headless is None:` -> `if False:` in `Helper.__init__`: `test_helper_requires_headless_support` fails (it also failed the removed engine-build test).
- M6: `ReportKind.PLAN: PlanReport` -> `TriageReport`: four tests fail (`test_a_plan_report_enqueues_its_new_tasks`, `test_scope_change_on_an_unknown_task_escalates_without_creating_tasks`, `test_helper_rebuilds_after_both_report_nudges`, the removed mapping test).
- M7: `cwd=cwd` -> `cwd=None` in `SubprocessRunner.run`; M8: stdin forced to `None`: the merged process test fails both.
- Phase 7/7b/7c/7d regression suites (soak smoke, engine fault/hold/recovery/restart, tick guard, holds) are untouched apart from the shared polling helper; the engine tests pass unchanged against the helper.

### Remaining limits

The suite is smaller by 118 collected tests (11%) with the same coverage; the remaining volume is real-boundary scenarios (tmux, git, SQLite, processes) and domain rule tables, each protecting a named fix. Rendering assertions of the TUI widgets and the agent-facing `report` CLI diagnostics were kept because the rendered text is the observable contract there.

