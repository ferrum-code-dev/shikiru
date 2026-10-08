# Workspace walkthrough evidence — 2026-10-03

The shipped application source exercised here is baseline commit `eed895fda03fb04a548786cc7e8818d9070aa602`. The five connected Task 87 walkthroughs and original evidence record were added in commit `8dc061c924fb7b9048da39811d3dd8fc060f1718`, derived from that application baseline. After review corrections, the walkthrough module was rerun from the uncommitted working tree based at `8dc061c`; that pre-commit run includes the updated search-submit route and escalation-summary assertions below. The UI boundary, plan and tmux checks below are existing integration tests. No real-agent E1–E6 scenario was run.

This is the historical Phase 8 walkthrough record. Task 96's additional Focus operator flows, mixed billing/model-history projections and release verification are recorded separately in the [2026-10-04 Phase 9 release record](release-readiness-2026-10-04-phase9.md).

The Enter-to-open-detail and narrow system-pane observations below are historical Phase 8 behavior, not the current Home contract. Phase 11 selects tasks into inline detail, keeps contextual overlays on the mounted Home dashboard, and uses the default full-width Leader/Implementer/Project folder 40/40/20 task window without a system pane. The historical first-delivery evidence is in the [2026-10-07 record](release-readiness-2026-10-07-phase11.md); current native captures and connected verification are in the [2026-10-08 second-review record](release-readiness-2026-10-08-phase11-followup.md).

All commands used the project `.venv` through `uv`, isolated pytest temporary projects, real SQLite and Git where the fixture owns them, Textual `run_test`, `FakeClock` with `wait_until_advancing`, and `tests/support/tmux.py::tmux_server` on a unique private socket. The suite did not connect to the user's tmux server or cache. The Task 87 file uses the installed fake agent and a private Shikiru tmux session to run the engine for flows 1, 3, 5, 6 and 7.

## Connected fake-agent engine walkthroughs

Command:

```sh
UV_CACHE_DIR=/tmp/shikiru-task87-uv-cache PYTEST_XDIST_AUTO_NUM_WORKERS=1 \
  uv run pytest -q --no-cov tests/integration/test_workspace_walkthroughs.py
```

Observed after the review corrections: `5 passed in 22.53s`. Each test built a temporary project, initialized its Git repository, started a real engine against SQLite and the isolated tmux fixture, and used the fake agent for task transitions.

1. `test_fake_agent_workspace_finds_verdict_and_applies_contextual_message` submitted a titled implementation task, waited for an actual Leader `changes_requested` verdict, pressed `/`, entered the title in Home search and pressed Enter to move focus to the result list, then pressed Enter again to open shared detail and saw that verdict. `Ctrl+P` opened the contextual palette with the selected task ID and Implementer role already filled; the test entered no task ID. Sending a message reached **Applied**, and the exact message appeared in the durable Implementer message file.
2. `test_fake_agent_escalation_updates_an_open_resolution_form_from_another_operator` used repeated fake-agent `needs_decision` reports to create a durable escalation. The open palette showed the actual question, “Which parser behavior should be retained?”, recorded no structured agent alternatives, and marked the engine's `answer` resolution as recommended with its consult-turn consequence; it also showed the cancellation consequence. The Operator entered an answer. A second SQLite connection queued `CancelResolution`; the engine applied it, the open form disabled its stale submit control, showed the resolved state, and retained the entered answer. The task became cancelled.
3. `test_fake_agent_task_takeover_leaves_other_task_progress_and_pause_visible` kept one fake Implementer turn stalled while a second fake-agent task completed. The Operator paused and took over only the stalled task's Implementer. After Hand back, intervention ownership cleared while the pause hold remained. Task detail displayed the remaining paused hold; the other task stayed done.
4. `test_dashboard_queued_submit_survives_engine_and_dashboard_restarts_once` opened the Home palette with the engine stopped and submitted a task. The durable command remained **Pending** after the first dashboard closed. Starting the engine applied it; the fake task completed. After stopping the engine, reopening the durable command as pending to model recovery, starting the engine again, and reopening the dashboard, the command showed **Applied**. SQLite contained exactly one `TASK_QUEUED` event and one task for that command.
5. `test_real_terminal_cleanup_remains_exportable_with_missing_evidence_marked` drove three real outcomes: DONE, operator CANCELLED during an active fake Implementer session, and CANCELLED with reason SUPERSEDED after a fake Leader verdict. For each task the test observed worktree deletion and its tmux window closing, reopened Task detail, and exported JSON through the dashboard. Each artifact retained the expected task ID and outcome; the superseded artifact retained `outcome.reason`. After deleting one retained report, the DONE artifact listed `reports` under `unavailable_sections`. Export left the durable command count unchanged.

The JSON artifacts were read and parsed from the temporary fixture project during the test; they are not checked-in user data. Export reads and validates its separate SQLite and file sources, but these runs do not establish one cross-source snapshot, a complete agent transcript, or recovered worktree contents.

## Seeded UI boundaries and plan/tmux engine coverage

The following existing UI checks seed durable rows and events to set up the boundary being exercised; they are not fake-agent task executions. They use real Textual rendering and inspect visible text, current selection/focus, enabled controls and output files as appropriate.

```sh
UV_CACHE_DIR=/tmp/shikiru-task87-uv-cache PYTEST_XDIST_AUTO_NUM_WORKERS=1 \
  uv run pytest -q --no-cov \
  tests/integration/test_task_browser.py::test_browser_keeps_selection_while_search_filter_state_and_history_change \
  tests/integration/test_task_browser.py::test_state_refresh_preserves_visible_list_position_and_selected_task \
  tests/integration/test_task_browser.py::test_dashboard_slash_focuses_search_and_a_slash_inside_search_is_text \
  tests/integration/test_task_family_and_blockers.py::test_child_search_shows_parent_context_and_expands_the_family_group \
  tests/integration/test_dashboard_app.py::test_dashboard_mounts_the_default_widgets \
  tests/integration/test_dashboard_app.py::test_default_dashboard_widgets_fit_a_narrow_system_pane \
  tests/integration/test_dashboard_app.py::test_home_resizes_through_the_terminal_matrix_without_losing_selection_or_focus \
  tests/integration/test_dashboard_app.py::test_home_help_works_with_mouse_and_keyboard_without_color \
  tests/integration/test_dashboard_app.py::test_dashboard_activity_widget_focus_expands_and_restores_scroll \
  tests/integration/test_dashboard_app.py::test_task_detail_toolbar_and_timeline_form_remain_reachable_after_resize \
  tests/integration/test_dashboard_app.py::test_dashboard_compact_footer_and_persisted_state_are_readable_without_color \
  tests/integration/test_dashboard_app.py::test_dashboard_mouse_navigation_expands_and_restores_external_widget \
  tests/integration/test_dashboard_motion.py::test_disabled_animations_keep_countdowns_live_without_animating_frames \
  tests/integration/test_dashboard_palette.py::test_pending_form_guards_survive_form_switches_and_palette_reopen \
  tests/integration/test_dashboard_palette.py::test_popup_pane_context_selects_its_recorded_task_and_role \
  tests/integration/test_dashboard_app.py::test_ctrl_p_pushes_the_shared_operator_palette \
  tests/integration/test_dashboard_app.py::test_ctrl_p_carries_the_selected_browser_task \
  tests/integration/test_dashboard_task_detail.py::test_enter_opens_shared_detail_and_escape_restores_browser_selection_and_scroll \
  tests/integration/test_dashboard_task_detail.py::test_compact_palette_form_keeps_visible_text_cursor_and_run_after_resize
```

Observed: `21 passed`.

- Flow 2's browser tests kept selected task identity, list position and preview readable through filter/history and state refresh; the Home resize test kept task identity, search focus and text while moving through 120×40, 48×20, 36×20, 48×14 and 20×8. These are separate seeded boundary executions.
- The browser moved its selected task with Up/Down, and Enter/Escape returned from detail to the same browser row, focus and scroll position. Tab reached Activity; `z`, Escape and `?` expanded the focused section, restored it and opened context-aware help.
- Flow 4's UI check showed a seeded plan family with completed parent context and running/queued descendants together; the family outcome counts distinguished those statuses.
- Flow 8's default-widget check mounted configured widgets `header`, `escalations`, `strips`, `queue`, `dialogue`, `usage`, `resources`, `docker` and `faults`; a second check inspected their narrow system-pane placement. Home was rendered at 120×40 and resized down through 48×14, 36×20 and 20×8. Task detail and its timeline controls were inspected at 36×20 and 48×14; the compact palette retained its Message text, cursor and Run control at 48×14. The `NO_COLOR` checks inspected visible status words and controls. With `NO_COLOR`, `TERM=dumb` and `animations = false`, static frames stayed still while countdowns continued to advance.
- The same checks exercised Home help by mouse and keyboard, section expansion/restoration, `/` to focus search while a slash typed inside search remained text, contextual `Ctrl+P`, form-switch/reopen guards, and the originating task/role in a popup pane.

Plan and tmux behavior used engine/CLI integrations, not seeded palette assumptions:

```sh
UV_CACHE_DIR=/tmp/shikiru-task87-uv-cache PYTEST_XDIST_AUTO_NUM_WORKERS=1 \
  uv run pytest -q --no-cov \
  tests/integration/test_engine_flow.py::test_a_plan_report_enqueues_its_new_tasks \
  tests/integration/test_engine_scheduler.py::test_plan_children_start_only_after_the_parent_landed_its_tracker_changes \
  tests/integration/test_tmux_palette_binding.py::test_bind_palette_key_dispatches_on_the_shikiru_session_pattern_else_next_layout \
  tests/integration/test_tmux_palette_binding.py::test_popup_command_dispatch_expands_origin_and_preserves_argv \
  tests/integration/test_operator_cli.py::test_start_reports_that_an_existing_engine_was_reused
```

Observed: `5 passed in 1.69s`. The fake-agent plan report left its parent INTEGRATING and enqueued its child QUEUED; the scheduler test held plan children until the parent's tracker changes landed. The popup integration exercised the conditional `prefix + Space` route: Shikiru sessions dispatch the palette with the originating pane expanded into argv, while another session keeps `next-layout`. The CLI integration verified startup when an engine is already running.

The plan transition and seeded family rendering are separate executions. Together they show the actual enqueue/merge gate and that family summary does not equate a parent task's terminal status with all descendants being finished.

These are behavioral integrations, not tests that pin documentation wording or screenshots. They provide no real-agent E1–E6 evidence; that remains Task 88.
