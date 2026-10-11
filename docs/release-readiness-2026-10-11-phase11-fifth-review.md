# Phase 11 fifth-review verification

Status: Task 147 verified Tasks 142–145 on 2026-10-11 against the final source; **Task 146 (Implementer pane titles after restart) was not reproduced and is closed: the operator confirmed it no longer occurs**. Documentation only: no code changed during verification. Earlier accepted evidence, the original PNG and the independently open gates 59, 74h and 74p are unchanged. GNU/Linux remains unverified; everything ran on macOS (Darwin 25.3.0, Python 3.14, Textual 6.3.0). The spec is [`tui-feedback-fifth-review.md`](tui-feedback-fifth-review.md); the preceding record is the [fourth review](release-readiness-2026-10-11-phase11-fourth-review.md).

Source: `fc1bb18` (Tasks 142–145) plus the HTML commit `docs(reference): align the Focus Steel HTML with the fifth-review TUI`.

## Issue-by-issue result

| Issue | Commit | Result | How verified |
|---|---|---|---|
| `T-n · No decision needed` (grey `N decisions on other tasks`) | `1a7e2d8` | pass | `test_dashboard_escalation_follow.py` (style spans at wide and compact; suffix count and absence). Native dump at 120×40 reads `T-1 · No decision needed · 2 decisions on other tasks … Ctrl+P actions`; HTML in headless Chrome reads the same at wide and compact. |
| Up/Down inert on the focused escalations bar | `11a43e7` | pass | `test_up_and_down_do_nothing_while_the_bar_has_focus_and_the_list_still_moves`: bar content, selection and scroll offsets unchanged; list arrows still move the selection. HTML: ArrowUp/ArrowDown dispatched on the focused bar leave the selection on T-44 (wide and compact). |
| One-column task-detail scrollbar | `b1c8972` | pass | `test_dashboard_popup_focus.py` computed-style test for Home wide, compact and standalone (content width and colours); native dumps in Task 144. The HTML draws no scrollbar in the detail pane (its modal bar was already 1ch), so it needed no change. |
| Integrating and other in-progress statuses animate | `fc1bb18` | pass | `test_dashboard_motion.py` (Integrating and Starting cycle the marker with a FakeClock; static when paused, PR/CI wait, recorded integration wait, escalated, Done, engine down, `animations=false`) and the `is_in_progress_animated` table in `test_domain_motion.py`. HTML: the predicate covers Starting, Triaging, Planning, Implementing, Consulting, Verifying and Integrating, and is static in the Stopped scene; Decision, Waiting, Paused, Operator and Done rows never animate. |
| Implementer pane titles after restart | none | **closed, not reproduced** | See below. |

## Task 146 (closed)

The executing agent tried: whole-server kill with two and three tasks (stacked and test layouts), a single Implementer pane kill, a dead-pane respawn, and read the hand-back path. Every Leader and Implementer title was correct and no hostname showed. A stale pane id that resolves to another task's pane after a server restart is theoretically possible (`_is_healthy` does not check window membership) but unlikely because windows are recreated in task order; `CosmeticDecorator` swallows tmux failures with a once-per-streak log. No speculative fix was made. Task 137's tests (`test_engine_layout.py` title tests, `test_engine_titles.py`) were run and pass. Questions for the operator: tmux version and `tmux.conf`/`pane-border-format` overrides; which action preceded the missing title (server kill, `Ctrl+B x` on one pane, detach and reattach, hand back after takeover); whether the pane showed the hostname or was empty; whether it recovered after the next usage event; and the engine log around the restart (the cosmetic-failure line).

## Walkthroughs and visual checks

Fake agent, real SQLite and Textual, private tmux via the existing integration tests: the escalation-follow, motion, popup-focus, domain-motion, title, layout and workspace-walkthrough modules (141 tests) passed in 32 s. A throwaway Textual dump of the real Home at 120×40 with an unescalated task selected was compared with the HTML rendered in headless Chrome (temporary profile, deleted) at wide and compact, colour and No color: bar wording, `›` list marker with the bold status blink, and spinner only in the detail matched. The probe test and Chrome profile were deleted.

## HTML reference

`docs/design/references/focus-steel-approved.html` SHA-256 `59a864187ea7ba57544282e34f94dfd4843e5586965feafea56f9acce6642772`. Palette declarations and the 736 px PNG are untouched. List rows now show `›` plus the blinking bold status for an in-progress task (the TUI list has no spinner glyph); the detail keeps the braille marker.

## Gates

| Gate | Result |
|---|---|
| `uv run poe check`, first run | 1,483 passed, 1 failed: `test_task_family_and_blockers.py::test_nested_family_navigation_returns_through_each_detail_to_the_selected_root` (timing-sensitive; 12 passed alone) |
| `uv run poe check`, second run | 1,484 passed, 14 skipped, 90.78%, 306 s |
| `TERM=dumb NO_COLOR=1 uv run poe check`, first run | 1,482 passed, 2 failed: `test_resource_sampler_cpu.py::test_agents_cpu_counts_a_busy_child_from_its_first_sample` (host at 97% CPU during the gate pushed the busy-child sample past its bound) and `test_task_evidence_export.py::test_palette_export_controls_and_full_result_remain_reachable[False-size1]`; both passed alone |
| same, second run | 1,484 passed, 14 skipped, 90.76%, 311 s |

Skipped on purpose: the macOS clean-install smoke (no packaging or install change) and any paid real-agent run (07 §5: no execution, recovery, quota or budget behaviour changed).

## Remaining limits

- Task 146 is closed without a change: unreproduced and confirmed gone by the operator. The window-membership weakness in `_is_healthy` remains a theoretical, unfixed observation.
- With no attention groups, the compact no-groups bar status still scrolls on Up/Down because it otherwise overflows its two-line strip (Task 143).
- Integrating waiting on a PR, CI or retry stays static; only the engine's own integration animates.
- The escalation-panel, wizard and session-picker scrollbars remain two columns (out of scope).
- Timing-sensitive tests: `test_task_family_and_blockers` navigation, `test_resource_sampler_cpu` busy child (new, load-dependent), `test_task_evidence_export` palette export and `test_engine_daemon` takeover/partial hand-back (failed once in a docs-only gate re-run, `test_a_partial_hand_back_keeps_the_intervention_hold_until_the_last_role_returns`); each passes alone.
- GNU/Linux is unverified; gates 59, 74h and 74p remain open.

## Hygiene

No process, tmux socket or Chrome profile of mine remained; the user's live session and default tmux server were not touched.
