# Test suite audit (A1)

## T-71 follow-up

The table below is the original name-and-first-lines audit, not an implementation plan. The
follow-up checked the bodies and retained the separate recovery, lock, Git and integration cases.
In particular, row 7 switches to another branch whereas row 10 detaches HEAD; neither substitutes
for the other. Removing assertions from row 7 would not avoid its engine workflow. The quota family
tests reset selection and persisted deadlines; the cited domain tests instead cover release of an
existing block. The export walkthrough covers completed, cancelled and superseded cleanup, and
the wizard resize test checks scrolling and restored geometry. The restart refusal that row 33
could not find is covered by `test_restart_refuses_while_a_task_runs_and_leaves_the_engine_alone`.
The mixed-ladder wizard and consultation test exercise the full configured paths; the retention
test already uses only three completed tasks to exercise repeated live-engine cycles. The context
menu test exercises several routes, not just a timer. These are not established shortcuts.

The timeout quick win needs a startup barrier: starting a 0.5-second timeout immediately could
kill the hook before its child starts. The revised test observes a running child before releasing
the real timeout. The sleep audit's statement about no avoidable real-time waits overlooked this
real timeout, although its count of direct sleep sites is a separate observation. The vacuous
briefing width test was removed; other briefing tests cover rows, line caps and screen height.
Motion data-change tests now disable their watcher and mutate data before the tick; separate
writer-commit tests exercise watcher refresh for the header and queue.

On the same non-isolated machine, before and after runs used `PYTEST_ADDOPTS='--dist=loadfile'
uv run pytest -n 10 -m 'not e2e' --durations=0 -p no:cacheprovider`, with coverage enabled.
Before: 2,529 passed, 14 skipped, 92% coverage, 307.08 s. After: 2,528 passed, 14 skipped,
92% coverage, 288.95 s. The removed briefing case took 6.76 s before; the hook timeout case
took 10.03 s before and 0.55 s after. Motion header/queue cases took 0.53/0.59 s before and
0.07/0.19 s after. These are single runs on a shared machine, so the 18.13 s wall-time change
is not a reliable CI prediction. The original audit's historical timings and machine conditions
cannot be re-established from current tests. No worker or scheduling change is recommended
without a separate comparable benchmark. The after measurement preceded the final explicit
assertions for the timeout error and cancelled delivery; both were subsequently verified.

Report for [Test suite speed](test-suite-speed.md) A1. No test and no source changed. The verdicts
below are a review of test names and the first lines of each body, not of every line, so each one
should be confirmed before the test is edited.

## Measurement

- Command: `PYTEST_ADDOPTS='--dist=loadfile' uv run pytest --durations=0 -p no:cacheprovider`, run once on a
  fresh log (`/tmp/t70-a1/measure-3.log`, not part of the repo). The `addopts` of `pyproject.toml` still
  apply: `-n auto` and coverage.
- Worker count: 5 (10 CPUs divided by the default of 2 CPUs per worker, `tests/support/xdist_workers.py`).
- The command does not exclude `e2e`, unlike `poe test` (`-m 'not e2e'`). The 11.8 s e2e test below is
  therefore included here and not in the gate.
- Result: 2,530 passed, 20 skipped, 0 failed, exit 0. Wall time 346 s (5:46). Collected items: 2,550.
- **Conditions: this was not an idle machine, so these are not the idle-machine figures the verdict asked
  for.** Other projects' Shikiru processes were live during the run: the `[shikiru]` tmux session for
  `shikiri-yaku` (pid 1743) and its helpers, engines and MCP servers for `ferrumcode.dev`, `shikiri-yaku`,
  `shikiru` and `test-project`, and a Codex app server. A Claude session and two Codex sessions were also
  open. The turn 10 and 12 reports list 14 other-project engine processes. A test engine (pid 95727) and
  its tmux server (pid 95812), left by an earlier pytest run from the T-68 worktree, were still alive, and
  20 `shikiru-test-*` tmux servers were up, aged from about 10 hours to 5 days. A check made after this
  run (09:31) still shows these processes. None were stopped, as the decision for this task required.
  Their CPU and tmux activity can add contention to the call phase, so these times may be higher than on
  an idle machine. Do not use them as targets until an idle run replaces them.
- The run recorded in `71b8c373` (345 s, 1,643.4 s recorded) was also taken on a machine that was not
  idle. This run agrees with it within about 1% of wall time.
- Earlier logs in `/tmp/t70-a1/` from overlapping runs on one log are not used here. The clean run above
  is the only measurement in this document.

## Time split

| Phase | Seconds | Share |
| --- | --- | --- |
| call | 1,614.9 | 97.4% |
| setup | 30.8 | 1.9% |
| teardown | 11.4 | 0.7% |
| **Sum of recorded phases** | **1,657.2** | |

The sum over 5 workers gives 331 s against 346 s of wall time, so packing is about 96%. Saving CPU
seconds saves gate time roughly at one fifth.

4,703 entries under 5 ms are hidden by pytest, so small tests are undercounted. Their total is at most
about 23.5 s.

### Time per directory

| Directory | Seconds |
| --- | --- |
| `tests/integration` | 1,645.3 |
| `tests/e2e` | 11.8 |
| `tests/unit` | 0.1 (visible entries only; unit tests are below 5 ms each) |

431 tests take at least 1 s, 72 at least 5 s, and 16 at least 10 s. The 40 slowest take 393.4 s, which
is 23.7% of the total.

### Files with the most total time

| Seconds | File | Dominant cost |
| --- | --- | --- |
| 94.9 | `integration/test_engine_pull_request.py` | real git remote, engine, fake GitHub |
| 86.3 | `integration/test_engine_daemon.py` | real engine processes |
| 83.7 | `integration/test_engine_restart.py` | engine kill and restart at 13 boundaries |
| 76.8 | `integration/test_dashboard_app.py` | Textual pilot, real clock in places |
| 72.1 | `integration/test_engine_recovery.py` | engine restarts and quota waits |
| 67.1 | `integration/test_engine_flow.py` | scripted real turns |
| 62.8 | `integration/test_engine_integration.py` | real git plus engine turns |
| 53.7 | `integration/test_engine_feedback_recovery.py` | real git plus engine |
| 52.3 | `integration/test_engine_feedback_rounds.py` | real git plus engine |
| 51.7 | `integration/test_engine_feedback_landing.py` | real git plus engine |
| 44.6 | `integration/test_dashboard_wizard.py` | Textual wizard pilot |
| 40.4 | `integration/test_engine_scheduler.py` | fake clock; real setup per test |
| 37.1 | `integration/test_workspace_walkthroughs.py` | real engine and tmux |
| 35.7 | `integration/test_dashboard_wizard_agents.py` | Textual wizard pilot |
| 33.7 | `integration/test_engine_signals.py` | real engine processes and signals |

## The 40 slowest tests

Verdicts follow the plan's vocabulary. "Remaining" names the design rule (`docs/design.md`) or the test
that still protects the behaviour after the verdict. "Keep as is" carries no change, and the remaining
column then names the rule the test protects.

| # | s | Test (file::name) | Protects | Why slow | Verdict | Remaining guarantee |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 13.7 | `soak_smoke::test_a_short_soak_with_an_engine_kill_ends_with_every_invariant_held` | Random schedule with an engine kill keeps every invariant | Full harness, real engine restart | keep as is | Crash recovery; no lower-level test can replace it. |
| 2 | 12.9 | `engine_retention::test_a_long_lived_engine_keeps_its_run_directories_bounded` | Retention keeps run directories bounded while the engine runs | Real engine over many cycles, 2 slots | make faster: fewer tasks to reach the retention threshold | Retention bounds stored history (design, observability). |
| 3 | 12.5 | `dashboard_wizard::test_init_writes_mixed_multirung_ladders_and_runtime_resolves_complexity` | Init writes mixed rung ladders; runtime resolves complexity | Textual pilot across all wizard pages | make faster: write the ladders on fewer pages. | Rung text: `tests/unit/test_ladder_text.py`. |
| 4 | 11.8 | `e2e/test_operator_fake_agent::test_fake_agent_operator_flow_completes_and_integrates` | The whole operator CLI flow reaches integration | Real tmux and engine process | keep as is | Outside `poe test`; only this plan's command runs it. |
| 5 | 10.8 | `dashboard_wizard::test_init_writes_project_fields_and_orchestration_after_back_navigation_and_resizes` | Back navigation and resize keep the entered values | Pilot, git setup, long names | cover at a lower level for resize (`tests/unit/test_ui_size.py`, to be checked) | Back navigation stays in this test. |
| 6 | 10.7 | `engine_flow_liveness::test_an_answer_resolved_while_the_old_flow_is_finishing_leaves_one_live_flow` | A race between an answer and a finishing flow leaves one live flow | Real turns and a stall step | keep as is | Operator answers can return to consultation (design, escalation). |
| 7 | 10.4 | `engine_pull_request::test_an_agent_that_left_its_task_branch_opens_no_pull_request` | No PR is opened for the wrong branch | Real engine, fake GitHub | make faster: keep only the branch check and the no-PR assertion | The branch rule stays with row 10. The no-PR side has no other test. |
| 8 | 10.4 | `engine_feedback_recovery::test_a_feedback_rebase_is_verified_again_and_the_landing_keeps_every_commit` | A feedback rebase is verified again, and every commit lands | Real git rebase, engine | keep as is | Changed tip returns to verification (design, PR feedback). |
| 9 | 10.3 | `engine_flow::test_fresh_session_consult_keeps_the_operator_decision_and_the_implementer_question` | Consultation keeps the operator decision and the implementer question | Scripted real turns up to the consult limit | make faster: fewer real turns, if the limit can be a fixture setting | Escalation and decision rules (design, workflow). |
| 10 | 10.3 | `engine_integration::test_a_task_whose_agent_left_its_branch_never_reaches_done_and_keeps_its_work` | A task on the wrong branch is never done, and its work is kept | Real git and three turns | keep as is | Engine checks reject the wrong branch (design, workflow). |
| 11 | 10.1 | `engine_feedback_landing::test_a_squashed_pull_request_with_published_feedback_is_cleaned_up` | A squashed landing cleans up the task | Real engine and PR | keep as is | Git lifecycle (design). |
| 12 | 10.1 | `engine_notifications::test_hook_descendants_die_with_the_hook[timeout]` | A timed-out hook's child processes die with it | Waits the real 10 s hook timeout | make faster: use `_QUICK_HOOK_TIMEOUT_SECONDS` (0.5 s), already defined in the file | Same rule; saves about 9.5 s. |
| 13 | 9.7 | `dashboard_wizard::test_invalid_orchestration_values_focus_their_field_and_boundaries_round_trip` | Out-of-range values focus their field; boundaries round-trip | Pilot | cover at a lower level for boundary values (settings); keep one focus test | `tests/unit/test_config_loader.py` covers only a valid CPU value; no unit test of an out-of-range value was found. Keep the focus test until one exists. |
| 14 | 9.7 | `workspace_walkthroughs::test_real_terminal_cleanup_remains_exportable_with_missing_evidence_marked` | Cleanup of a finished task keeps its evidence exportable, with gaps marked | Real engine, real tmux, scripted task | make faster: script one task, not the full walkthrough | Successful tasks release their sessions and worktrees; Export writes retained evidence. Unit coverage of gap marking not checked. |
| 15 | 9.6 | `engine_feedback_recovery::test_feedback_preparation_recovers_completed_conflict_resolution` | A completed conflict fix is recovered, not redone | Real git and engine | keep as is | Repair and verification (design, integration). |
| 16 | 9.6 | `engine_feedback_recovery::test_rework_checkpoint_failure_replays_completed_report_without_exhausting_budget` | A replayed report does not use up the iteration budget | Real engine and git | keep as is | Bounded iterations (design, PR feedback). |
| 17 | 9.3 | `workspace_walkthroughs::test_fake_agent_escalation_disables_an_open_resolution_resolved_by_another_operator` | An escalation resolved by another operator disables the open resolution | Real engine and tmux | keep as is | Operator decisions (design). |
| 18 | 9.3 | `engine_feedback_recovery::test_merged_feedback_recovers_prepared_landing_after_publication_checkpoint_loss` | A merged PR recovers the prepared landing after a lost checkpoint | Real git, engine restart | keep as is | Restart reconciliation of integration (design). |
| 19 | 9.3 | `engine_feedback_rounds::test_requested_changes_are_reworked_published_and_answered` | The full PR feedback round | Real engine, fake GitHub, turns | keep as is | PR feedback rounds (design). |
| 20 | 9.2 | `engine_signals::...[2]` | SIGINT ends the turn, releases the lock, and a restart resumes | Real engine processes | keep as is | As row 22. |
| 21 | 9.2 | `engine_escalation_faults::test_a_restart_with_an_unwritable_packet_directory_keeps_running_and_stops_collecting` | An unwritable packet directory on restart is a fault, and the engine keeps running | Real engine restart | keep as is | Engine faults are recorded and retried (design). |
| 22 | 9.1 | `engine_signals::test_a_signal_ends_the_turn_releases_the_lock_and_a_restart_resumes[15]` | SIGTERM ends the turn, releases the lock, and a restart resumes | Real engine processes | keep as is | Engine Stop and restart (design). |
| 23 | 9.1 | `test_dashboard_app::test_task_context_menu_closes_and_opens_secondary_views_without_commands` | The menu opens secondary views and sends no commands | `SystemClock`: real-time waits in a Textual app | make faster: drive its waits with `FakeClock` | Actions are contextual (design, operator views). |
| 24 | 9.1 | `engine_feedback_landing::test_a_branch_advanced_after_verification_is_kept_when_the_pull_request_merges` | Commits added after verification survive a merge | Real git and engine | keep as is | Integration and cleanup (design). |
| 25 | 9.1 | `engine_restart::test_a_restarted_engine_completes_the_task_it_lost_at_a_step_boundary[integration consult running]` | A restart at the integration boundary completes the task | Real engine kill and restart | keep as is | Crash recovery (as row 40). |
| 26 | 9.0 | `engine_signals::test_a_second_signal_ends_the_engine_by_force_and_a_restart_resumes` | A second signal forces the engine down; a restart resumes | Real engine processes | keep as is | As row 22. |
| 27 | 8.6 | `engine_feedback_landing::test_a_push_without_its_checkpoint_is_kept_unless_the_pull_request_head_proves_it[unproven]` | A push without a checkpoint is kept unless the PR head proves it | Real git and engine | keep as is | Cleanup retention rule (design, PR feedback). |
| 28 | 8.4 | `engine_feedback_recovery::test_an_interrupted_reply_is_found_by_its_marker_instead_of_posted_twice` | An interrupted reply is found by its marker, not posted twice | Real engine and fake GitHub | keep as is | Reply markers (design, PR feedback). |
| 29 | 8.3 | `engine_feedback_landing::test_a_pull_request_merged_before_feedback_publication_keeps_the_local_feedback_commit[start-event-lost]` | A local feedback commit survives a merge that came first | Real git and engine | keep as is | Integration (design). |
| 30 | 8.1 | `engine_feedback_landing::test_a_push_without_its_checkpoint_is_kept_unless_the_pull_request_head_proves_it[head-proves-it]` | As row 27, when the PR head proves the push | Real git and engine | keep as is | As row 27. |
| 31 | 7.9 | `engine_feedback_rounds::test_a_push_that_loses_the_lease_keeps_both_sides_and_sends_no_replies` | A lost force-with-lease keeps both sides and sends no replies | Real git and engine | keep as is | Lease rule (design, PR feedback). |
| 32 | 7.6 | `engine_feedback_landing::test_a_pull_request_merged_before_feedback_publication_keeps_the_local_feedback_commit[recorded]` | As row 29, with the merge event recorded | Real git and engine | keep as is | As row 29. |
| 33 | 7.5 | `cli_restart::test_restart_with_force_replaces_an_engine_with_a_running_task` | `--force` replaces an engine that has a running task | Two engine starts | keep as is | Restart refusal without `--force`: no test found in this list; check. |
| 34 | 7.4 | `actions_list::test_actions_list_opens_popups_returns_to_the_list_and_switches_task` | Popups return to the list and switch tasks | Textual pilot | keep as is | Operator views (design). |
| 35 | 7.4 | `workspace_walkthroughs::test_fake_agent_task_takeover_leaves_other_task_progress_and_pause_visible` | Taking over one task leaves the other task's progress visible, and its pause hold stays | Real engine, real tmux, two scripted tasks | keep as is | Takeover and pause hold independently (design, operator sessions). |
| 36 | 7.1 | `dashboard_app::test_contextual_message_draft_survives_back_and_action_change` | A message typed in a context menu survives going back and changing the action | Textual pilot with a seeded task and command queue | keep as is | Messages are delivered to a role at the next turn (design, operator views). |
| 37 | 6.8 | `engine_feedback_rounds::test_changes_that_stay_rejected_escalate_after_the_attempt_limit` | Changes still rejected after the attempt limit escalate instead of looping | Real git, engine, fake GitHub and several scripted turns | keep as is | Bounded iterations (design, PR feedback). |
| 38 | 6.8 | `dashboard_palette::test_applied_message_feedback_keeps_next_turn_guidance` | Feedback that a message was applied keeps the guidance for the next turn | Textual palette app with a real state reader and a seeded task | keep as is | Messages are delivered at the next turn (design). |
| 39 | 6.7 | `task_family_and_blockers::test_relationship_pages_keep_tail_tasks_navigable_and_blocking_dependencies_visible` | Pages keep the tail tasks reachable; blockers stay visible | Seeded tasks, dashboard reader | keep as is | Pagination has no unit test found; keep this one. |
| 40 | 6.7 | `engine_restart::test_a_restarted_engine_completes_the_task_it_lost_at_a_step_boundary[conflicts turn running]` | A restart at the conflict boundary completes the lost task | Real engine kill and restart | keep as is | Start reloads state and reconciles turns (design, recovery). |

Verdict counts: 7 `make faster`, 2 `cover at a lower level`, 31 `keep as is`. No test was marked
`merge with`, `duplicate of` or `not needed`.

### Parametrised families (totals over all cases)

| Test | Cases | Total s | Verdict |
| --- | --- | --- | --- |
| `engine_restart::test_a_restarted_engine_completes_the_task_it_lost_at_a_step_boundary` | 13 boundaries | 68.3 | keep as is: each boundary is a different crash point |
| `engine_signals::test_a_signal_ends_the_turn_releases_the_lock_and_a_restart_resumes` | 2 signals | 18.3 | keep as is |
| `engine_feedback_landing::test_a_push_without_its_checkpoint...` | 2 | 16.7 | keep as is |
| `engine_feedback_landing::test_a_pull_request_merged_before_feedback_publication...` | 2 | 15.8 | keep as is |
| `engine_pull_request::test_pull_request_lands_through_the_done_path` | 4 | 15.7 | keep as is |
| `engine_recovery::test_quota_failure_without_reset_uses_the_stored_window` | 5 | 14.3 | cover at a lower level for 4 cases: `tests/unit/test_domain_quota_blocks.py` exists, but its coverage of this rule is not verified |
| `github_feedback::test_inline_reply_keeps_its_body_and_lands_in_its_discussion` | 9 | 13.8 | keep as is |
| `engine_flow_pinning::test_a_rung_set_during_a_turn_is_never_undone_by_the_turn_completing` | 4 | 12.5 | keep as is |

## Quick wins

Estimates are CPU seconds saved, from the measured numbers above.

1. **Hook timeout, row 12.** Use the 0.5 s constant already defined in `test_engine_notifications.py` for
   the `[timeout]` case. About 9.5 s saved. Very low risk: the rule (children die with the hook) does
   not depend on the length of the timeout.
2. **Duplicate branch check, row 7.** Keep only the no-PR assertion and trim the rest. Up to 10.4 s
   saved, but this needs the branch rule to remain in row 10. Low risk.
3. **Quota without reset, 4 of 5 cases.** Move them to the domain tests. About 11 s saved. Medium risk:
   the domain coverage must be confirmed first.
4. **Wizard resize, row 5.** Move the resize check to a unit test. Not estimated.

Items 1 to 3 together save about 30 s of 1,657 s, roughly 2% of CPU. The slow time is spread over
431 tests of at least 1 s. No single change gives a large saving. The larger lever is how many real
engine and process starts happen in call time, which is 97.4% of the total.

## Shared costs

Earlier probe, not repeated in this run. Measured in isolation with `uv run pytest -o addopts='' -p no:cacheprovider --durations=0` on four
existing tests. Setup and teardown are pytest's own phases.

| Fixture or helper | Setup s | Teardown s | Test call s | Test |
| --- | --- | --- | --- | --- |
| `tmux_server` | 0.01 | 0.01 | 0.05 | `test_tmux_layout::test_build_places_panes_in_configured_order` |
| `engine_project` (two engine starts, one forced restart, running turn) | 0.01 | 0.02 | 4.31 | `test_cli_restart::test_restart_with_force_replaces_an_engine_with_a_running_task` |
| `engine_env` (real flow with scripted turns) | 0.02 | 0.02 | 6.78 | `test_engine_flow::test_fresh_session_consult_keeps_the_operator_decision_and_the_implementer_question` |

Across the suite, setup totals 30.8 s and teardown 11.4 s, under 3% of CPU. The fixtures are not the
expensive part. The cost of starting an engine process and running turns sits in the call phase, and
I did not isolate it further.

Leftover processes: during the run, 20 `shikiru-test-*` tmux servers were alive, aged from about 10 hours
to 5 days. Test teardown, or runs that crashed before teardown, leave them behind. I did not
find the cause.

## Sleep audit

Search counted 27 Python `sleep(...)` call expressions and 18 shell `sleep` command invocations
under `tests/`. Python source embedded in child-process payloads counts as a call expression;
definitions, prose, callback references, and `has_pending_sleep()` probes do not. In particular,
`tests/unit/test_wait_for_ci.py:67` passes `clock.sleep` as a callback but does not call it there.

- **Real-time harness waits (17 Python):** the e2e watchdog uses `SystemClock` at
  `tests/e2e/watchdog.py:52,71` (`tests/e2e/conftest.py:32`), and the soak harness uses it at
  `tests/soak/arena.py:166,181`, `chaos.py:110,120,129,162,176,211`,
  `supervisor.py:45`, and `run.py:103,129,135,142,156,161`. These are 2 e2e and 15 soak
  sites. E2e runs through `poe e2e`; pytest does not collect soak, which runs through `poe soak`.
- **Fake clock (5 Python):** `tests/integration/test_engine_recovery.py:65` and
  `tests/unit/test_fake_clock.py:18,49,61,77`. These register fake deadlines and do not wait in
  real time.
- **Shared wait helpers (2 Python):** `tests/support/waiting.py:30` uses `asyncio.sleep(0)` only to
  yield to the event loop; `:54` uses `asyncio.sleep(_POLL_INTERVAL_SECONDS)` for the sanctioned
  `wait_until` polling helper. Test bodies do not call real `time.sleep` or `asyncio.sleep` directly.
- **Python child-process payloads (3):** `tests/support/spawn_then_die.py:13` and
  `tests/integration/test_process.py:142,260` sleep inside child programs used to test process
  handling; they are not waits in the test runner.
- **Shell child-process payloads (18):** long-lived commands are started and then cleaned up by the
  tests at `tests/integration/test_git_mutation_cancel.py:83`,
  `test_tmux_decorator.py:35,118,236,282`, `test_tmux_layout.py:38`, `test_docker_sampler.py:24`,
  `test_tmux_session.py:26,94,185,223`, `test_engine_notifications.py:145,185`,
  `test_process.py:124,169,241`, `test_workspace_caches.py:146`, and
  `test_engine_integration.py:1239`.
- **Avoidable real-time waits in the default gate:** none. The one real-time sleep site used by the
  gate is the shared `wait_until` polling helper; e2e and soak waits are outside the default gate.

`tests/integration/test_dashboard_app.py` uses `SystemClock` in 64 places, but it contains no `sleep`
calls. Row 23 is the case in the top 40.

## Notes

- No test and no source changed. Measurement logs are in `/tmp/t70-a1/` and are not part of the repo.
- Correction to `71b8c373`: its family figures were not verified against the log. The pull-request
  done-path family has 4 cases (15.7 s), not 12 in 2 tests. The restart family has 13 cases and 68.3 s,
  not 62.9 s. The figures in this document replace both.
- Verdicts rest on names and the first lines of bodies. Confirm each one before editing.
- The 40 slowest rows include the e2e test (row 4), which the gate excludes.
