# Phase 11 fourth-review verification

Status: Task 140 verified Tasks 129–139 on 2026-10-11 against the final source. This record is documentation only; the single code change made during verification is a test fixture (below). Earlier accepted evidence, the original PNG and the independently open gates 59, 74h and 74p are unchanged. GNU/Linux remains unverified; everything here ran on macOS (Darwin 25.3.0, Python 3.14.7, tmux 3.7c, Textual 6.3.0).

Source: `d9edce4` (Tasks 129–139 up to `86bbf4a`, plus the `test:` hardening below). The spec is [`tui-feedback-fourth-review.md`](tui-feedback-fourth-review.md); the preceding record is the [third review](release-readiness-2026-10-09-phase11-third-review.md).

## Issue-by-issue result

Tests are `tests/integration/...` unless noted and were run for this record (149 selected tests passed in 14.8 s, plus the walkthrough/engine selection below). "Seen" means the item was inspected in a native Textual export of the real Home rendered to PNG (colour and `NO_COLOR`) at 120×40, 101×35, 80×24, 48×20, 36×20 and 20×8, built from a seeded SQLite project (two active tasks, one escalation, eight terminal tasks, a pending command) with the existing `dashboard_app` helper.

| Item | Result | Evidence |
|---|---|---|
| Tab visits only visible stops; multi-row highlight covers all stats rows but not the rule; escalation bar armed by default | pass | `test_dashboard_tab_stops.py::test_tab_visits_only_visible_stops_and_highlights_the_readouts`, `::test_resources_highlight_covers_every_stats_row_and_not_the_rule`, `::test_decision_bar_is_armed_while_the_task_list_keeps_focus`, `::test_quiet_bar_is_not_a_tab_stop_but_views_attention_still_reaches_it`. Seen: armed bar with the task list focused; a Tab walk visited Recent, detail buttons, footer buttons, command status and Usage only. |
| Popup scrollbar colour equals the Tasks list; popups do not open on `Esc ×` | pass | `test_dashboard_popup_focus.py::test_popup_bodies_and_task_lists_share_one_scrollbar_style`, `::test_popup_starts_on_its_useful_control_and_escape_returns_to_its_origin`, `::test_popup_reaches_its_close_control_with_tab_and_enter_closes_it`. Seen: Help popup with `Esc ×` unhighlighted. |
| Token and resource bar templates (incl. `process tree is empty`) | pass | `test_dashboard_bar_templates.py` all four tests (eight token states, six resource states, whole-group wrapping). Seen: bold white count, cyan `Host CPU 10%`. The seeded stub has no sample, so the Host RAM/RSS values are proven by the tests, not by my renders. |
| Escalation bar: bold amber `E-n` / bold `T-n`, readable reason, follows selected task, `T-n is not escalated` | pass | `test_dashboard_escalation_follow.py` (five tests). Seen in native render and in the installed-package attach of the smoke (`T-1 is not escalated · Ctrl+P actions`). |
| Task-list geometry: reserved column, blank rows, non-shifting selection, `Recent` colour and blank line, bold ids, status colours | pass | `test_task_browser.py::test_selecting_a_task_row_never_moves_its_text_in_either_list`, `::test_recent_heading_shares_the_tasks_colour_and_a_blank_line_after_it`, `::test_task_number_is_bold_and_the_title_regular_in_active_and_recent_rows`. Seen: steel `Recent`, sage `Done`, grey `Cancelled`, bold ids; in `NO_COLOR` the selection is the grey surface. |
| Recent: three-task bottom-aligned window, Later/Earlier, 30 cap, Up/Down paging | pass | `test_home_recent_window.py` (11 tests) and `tests/unit/test_ui_recent_window.py`. Seen at 120×40 (bottom-aligned, `↑ Later ↓ Earlier`). |
| Footer: latest command right-aligned | pass (see limit for 73–80 columns) | `test_dashboard_app.py::test_latest_command_status_ends_on_footer_right_edge_across_widths`. Seen at 120×40. Limit below for 73–80. |
| Pane-title context colours (<40 green, 40–59 yellow, ≥60 red) | pass | `unit/test_domain_runs.py::test_context_level_classifies_the_percentage_shown_in_the_title`, `test_engine_titles.py::test_the_rendered_context_cell_takes_the_colour_of_its_usage_level`, `::test_unreported_context_renders_as_a_grey_dash` (real tmux, `capture-pane -e`). |
| Titles preserved after session kill | pass | `test_engine_layout.py::test_session_restart_redraws_agent_pane_titles_without_a_usage_event`, `::test_a_single_restored_agent_pane_is_retitled`, `::test_a_role_handed_back_before_its_pane_is_rebuilt_is_titled_without_the_operator_flag`, `::test_killed_agent_pane_rebuilds_the_task_window`. |
| Takeover not restored after respawn (documentation) | pass | Docs match behaviour: `test_engine_daemon.py::test_a_takeover_lost_with_the_tmux_server_is_handed_back_with_a_notified_event`, `::test_take_over_holds_only_that_task_and_hand_back_resumes_its_session`, `::test_replaying_a_take_over_whose_pane_was_lost_respawns_it_without_a_second_event`. |

Connected fake-agent / private-tmux walkthroughs (all passed): `test_workspace_walkthroughs.py` (five tests, including queued submit surviving engine and dashboard restarts, escalation updated from another operator, and takeover leaving other tasks and pause visible); `test_engine_daemon.py` takeover, pause and restart tests (`test_pause_survives_a_takeover_and_its_hand_back_until_resume`, `test_a_restarted_engine_gives_every_escalated_task_one_actionable_escalation`, `test_a_turn_stopped_by_a_pause_is_recorded_as_stopped_not_left_running`); stopped, paused, owned and held Home states (`test_dashboard_app.py::test_home_attention_tracks_owned_held_and_engine_state_changes`, `::test_home_status_keeps_stopped_engine_fault_context_without_escalations_plugin`); custom layouts/widgets (`test_dashboard_app.py::test_configured_usage_and_docker_views_remain_reachable`, `unit/test_config_loader.py::test_explicit_task_layout_override_remains_intact`); no focus steal (`test_dashboard_remaining_widgets.py::test_attention_refreshes_after_another_client_resolves_without_stealing_focus`, and the escalation-follow test asserts focus stays on the list).

## Visual review notes

- 20×8 floor: the Home shows the selected task row (`! T-2 · Blocked decision task` with its status) wrapped inside the frame, in colour and `NO_COLOR`; no clipping error or traceback.
- 36×20 and 48×20: header, armed/followed escalation bar, task rows and footer controls are reachable; the Recent block is below the fold in natural flow and scrolls.
- 80×24: header through detail pane shown; Resources rows, footer and most of Recent are below the fold and scroll. The same cut-off exists at `ed7aa72` (before Task 129), so it is not a regression.
- Attached tmux: the smoke's real attach (80×24 client) rendered the new escalation bar (`T-1 is not escalated`), bold ids and the `✓` Recent row. No separate attached-tmux 20×8 capture was taken; 20×8 is a Textual-level result.
- The renders came from a throwaway test harness and were not retained or committed.

## Gates

| Gate | Result |
|---|---|
| `uv run poe check` (normal), source `86bbf4a` | 1,434 passed, 14 skipped, 90.72% coverage, 253.99 s |
| `TERM=dumb NO_COLOR=1 uv run poe check`, source `86bbf4a` | **failed**: 21 failed, 1,413 passed. All were colour-asserting tests in `test_dashboard_tab_stops.py` and `test_dashboard_bar_templates.py` (Textual's monochrome filter turns the asserted hex colours into greys). Test-side defect, not product: fixed in `d9edce4` (a `colour_terminal` fixture that unsets `NO_COLOR` for those two modules). |
| `TERM=dumb NO_COLOR=1 uv run poe check`, source `d9edce4` | 1,434 passed, 14 skipped, 90.73%, 254.77 s |
| `uv run poe check` (normal), source `d9edce4`, first run | 1,433 passed, 1 failed: `test_task_evidence_export.py::test_palette_export_controls_and_full_result_remain_reachable[True-size1]` timed out at its 30 s geometry wait. Not reproduced in 56 further isolated and concurrent runs; cause still unknown. |
| same, second run | 1,434 passed, 14 skipped, 90.71%, 254.26 s |
| macOS smoke `SHIKIRU_SMOKE_TMPDIR=/private/tmp bash scripts/release-readiness-smoke.sh` | passed on `d9edce4` in 35 s: `build/release-readiness/clean-install-20261011T014109Z-74832.json` and `.log` (git-ignored; fake agent, billing unknown). Clean install, setup, Doctor, start/submit/merge, status/list, normal and 68×16 picker attach, eight installed-interpreter Textual tests, cleanup. |

Bounded real-agent verification (07 §5): no paid scenario is warranted. Tasks 129–139 changed presentation (bars, task list, Recent, popups, titles), one layout-guard title refresh and test helpers; they changed no execution, recovery, quota or budget behaviour. No paid E1–E6 run was made; earlier E-series results remain historical evidence for their own sources.

## Remaining limits

- At 73–80 columns the wide footer hides the latest-command status when tmux detach is unavailable (column budget; found in Task 135, not fixed).
- A quiet project with a selected task now reads `T-n is not escalated` (with the other-decision count only when N > 0) instead of `✓ No decisions needed`, by Task 131's design; `No decisions needed` shows only with no selection.
- Tabbing to the Recent list while the selection is on an active row highlights no Recent row; the only focus cue is a faint tint on the blank gap rows between items (`#task-browser-recent-list` lacks the `background-tint: transparent` rule the active list has). Cosmetic, found by the native render; not fixed here.
- Known timing-sensitive tests, not fixed beyond the waits raised in `2f417b5`: `test_task_evidence_export` palette export (failed once here, as in the third review), `test_dashboard_motion_skips` queue motion, `test_task_family_and_blockers` navigation, the `test_dashboard_app` footer/message-draft tests and `test_engine_daemon` takeover. Each passes alone; re-running the gate resolved the failure.
- GNU/Linux is unverified; gates 59, 74h and 74p remain open.

## Reconciliation and hygiene

`docs/design/README.md`, `docs/tui-feedback-fourth-review.md`, `README.md`, `tasks/plan.md` and `tasks/todo.md` point to this record. The Phase 10 audit in `tui-parity.md` is unchanged and still labelled historical.

Process and socket audit after the runs: no pytest, tmux (other than the user's live session), smoke or renderer process of mine remained; the smoke removed its temporary HOME, project and socket. Five pre-existing stale socket files (`shikiru-task109-root-title-probe`, `shk-third-*`) have no server behind them and were left untouched.
