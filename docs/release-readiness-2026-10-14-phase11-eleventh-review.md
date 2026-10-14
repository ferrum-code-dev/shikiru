# Phase 11 eleventh-review verification

Status: Task 185 verified Tasks 180–184 on 2026-10-14 against the final source, replaced the task detail of the HTML reference, reconciled the design documents and marked the spec delivered. The spec is [`tui-feedback-eleventh-review.md`](tui-feedback-eleventh-review.md); the preceding record is the [tenth review](release-readiness-2026-10-13-phase11-tenth-review.md). GNU/Linux remains unverified; everything ran on macOS (Darwin 25.3.0, Python 3.14.7, Textual 6.3.0).

Source: `b07f3bf` (180, briefing data), `3714164`, `7d85cd3` (181, verbs and briefing render), `ae04400`, `b4360f0` (182, details list), `ef06dd3` (183, arrow lists), `8837bc5`, `7da0f3b`, `946080c` (184, cleanup), `d1df283` (timeline filter layout fix after a Task 183 geometry flake), then Task 185's `f0d3779` (verb overflow fix), `95b12a1` (HTML reference) and `aff8633` (design documents), with this record, the spec status and the plan and todo ticks in the last commit. The baseline is `4fffb19`, the head before Task 180.

## Requirement-by-requirement result

| Requirement | Commits | Result | How verified |
|---|---|---|---|
| One briefing in the Home wide and compact inspector, the task popup and the task window | `b07f3bf`..`7d85cd3` | pass with limits (a)-(e) | Native dumps of a running, a waiting-on-decision, a rejected-with-verdict, a Done and a Cancelled task (see below). Before: `› ToolStarted`, `Last recorded signal · 2s ago · ordering uncertain`, session rows, `u details` and `e evidence`. |
| Rows only with data (Needed from you, Now, Produced, Verdict, PR, Cost; terminal task drops Now) | `b07f3bf`, `7d85cd3` | pass with limits (a)-(d) | The waiting task showed Needed from you and a labelled test line only; the Done task showed Produced with the report summary; the Cancelled task showed only the header, milestones and `Enter details ▸`. |
| Actions row: at most three verbs by state, each also in `Ctrl+P` | `3714164`, `7d85cd3` | pass | Verbs by state: running Take over, Message, Pause; waiting Answer, Message, Pause; paused Take over, Message, Resume; operator-owned Hand back, Message; queued Message, Pause; Done and Cancelled none. Pressing each opened `PaletteScreen` (`Take over role`, `Message agent`, `Pause task`, `Resume task`, `Hand back role`, each with the task and role) and Answer opened `Resolve escalation · E-1 · T-2`; none queued a command. |
| `Enter details ▸` and Enter on the focused inspector open the details list | `ae04400` | pass with limit (f) | Tab reached the verbs and then `Enter details ▸`; Enter opened `TaskDetailsListScreen`. |
| Details list: nine sections with grey headlines, selection bar, hint, project entries, `v`, `u`, `e` | `ae04400`, `b4360f0` | pass with limit (g) | Dump: `▌` on the selected row, `↑↓ select · Enter open` grey, headlines `verdict rejected`, `$1.52 billed`, `priority 0`, `1 blocker`, then `Command history ...`, `Header`. `v` opened the list from a focused inspector, `u` Usage and sessions, `e` Events. |
| Events and Agent activity lists (↑↓, PgUp/PgDn, End follow/pause, filters) | `ef06dd3` | pass with limit (h) | 60 seeded events and 45 signals: heading `following`, three Downs gave `paused · End to follow`, PgDn showed the next 20 rows, PgUp and End returned to `following`; the role filter applied on change; `p` and `n` did nothing. |
| Relationships open the related task on Enter | `ef06dd3` | pass | A queued task depending on T-1 showed `blocker  T-1 ...`; Enter opened T-1's briefing in a second popup, Esc returned to the first. |
| Workspace, Settings, Engine facts without engine ids or window/pane ids | `ae04400` | pass | Section dumps showed Branch and Worktree path, Mode, Submitted, Dispatch priority, and the facts text; a scan of the Home inspector, popup and every section of nine tasks found none of `› ToolStarted`, `Last recorded signal`, `u details`, `e evidence`, `Work is active`, `Queued for execution`, `task window ID`, `pane ID`, `Timestamp evidence`, `Event ID`, `Follow`, `Related task`. The baseline showed `Last recorded signal`, `u details`, `e evidence` and `Queued for execution` on every task. |
| Removed items stay gone (`p`/`n`, entry select, related select, Follow) | `ef06dd3`, `8837bc5` | pass | Keys `p` and `n` in the Events view changed nothing. |
| Esc: embedded inspector clears the selection, window goes Home, popup goes up | `ae04400` | pass with limit (g) | Window: Esc from a section returned to the summary, the next Esc left the dashboard. Home: Esc cleared the selection. |
| Colour and `NO_COLOR` | `7d85cd3` | pass | Styles below. |
| Test cleanup | `8837bc5`, `7da0f3b` | pass | See Numbers. |

## Native verification

A throwaway harness patched `DashboardApp.run_async` to `run_test`, then called `bootstrap.run_dashboard`, so the real wiring ran (`build_operator`, `DashboardServices`, `StateReader`) over a throwaway git project seeded with the helpers of `tests/integration/test_dashboard_briefing.py` plus a paused, an operator-owned (taken-over run), a queued dependent task, 60 events and a 45-row signal log. `TMUX_TMPDIR` pointed at an empty directory. The baseline was a `git worktree` of `4fffb19` first on `sys.path`; the harness asserted the module path of each run. Cells were read from the compositor with foreground and bold flag.

- **Home wide (100x40), colour.** Title bold white `#dce2e9`; state line `▲ Implementing` bold steel `#83bddd` with `· Implementer · rework 2 of 4` bold grey `#9aa5b5` and `31m total` below it; milestones green, steel `›`, grey `·`; rule `#3d4654`; `Needed from you` bold amber `#dfb16c` with the question white; row labels grey, values white, `Cost` values sage `#90cba6`; verbs steel, `Enter details ▸` grey. Decision hold state line bold dusty rose `#e3a0a0`.
- **Compact (Home 48 columns, window 48 and 40).** `▲ Implementing · rework 2/4`, `? › · ·`, `"Running the parser tests after the r…" 2s`, `3 commits +120 −34 · 1✓ 1✗`, `$1.52 billed`, `Take over  Message  Pause  Enter ▸`; the waiting task read `Needs you: Which failures should the parser retry?` wrapped under the label.
- **Popup and window.** The same rows; the window at 100 columns put the actions row on one line with `Enter details ▸` at the right; a 60x8 pane showed the header and rows and scrolled.
- **`NO_COLOR` (`TERM=dumb`).** Luminance greys: bold `#e1e1e1` title, `#b3b3b3` steel text, grey `#a4a4a4`, `Needed from you` bold `#b6b6b6`, cost `#bcbcbc`; bold survives, so the headings stay distinguishable.
- **Defect found and fixed.** At 40 columns the third verb was clipped (`Paus` beside `Enter ▸`): the verb buttons had a two-cell right margin. `f0d3779` removes it and adds a test that every offered verb stays inside the actions row at 40 columns (it fails without the fix).
- **Before.** The baseline inspector showed `› Implementing`, `Implementer · Implement · time unknown`, the last report summary, `› ToolStarted`, `Last recorded signal · 2s ago · ordering uncertain`, token and billing lines with `u details`, two session rows and `e evidence`.

Not claimed: the engine itself was never run, the tmux window and pane navigation was not exercised (the harness had no session), and the pending command left by seeding stayed pending in every run.

## HTML reference

`docs/design/references/focus-steel-approved.html` SHA-256 `79944d9e7ace4eda952a646bfb2fa75bf60fbf0ec77f28ae73af3c666a88b0e3`. Task 185 decoded the entity-encoded `srcdoc` (`&#x27;` included), edited the app and re-encoded it; re-encoding the unedited text reproduced the original bytes and the bytes outside the `srcdoc` are unchanged. Changes: `focusedDetail` now draws the briefing (header, state line with rework, total time, milestones, Needed from you, Now with the last words, Produced with agent-reported tests, Verdict, PR, Cost, the actions row and `Enter details ▸`; compact form for the Compact and Tiny widths), with sample data for T-41, T-42, T-43, T-44 and T-47; `u details` and `e evidence` and `taskUsage` are removed; a new `details` popup is the details list (nine sections, grey headlines, selection bar, hint), opened by `Enter details ▸` or `v` with a task selected, with ↑↓ and Enter (all sections but Usage open the existing Evidence popup, Usage opens the Usage popup), clicks and Esc; the help text is updated. The reference scripted no `p`/`n` and no PgUp/PgDn lists, so none were added.

The Chrome check loaded the decoded app plus a measuring script in headless Google Chrome under a throwaway profile (deleted; the operator's Chrome was not touched). Wide and compact, colour and No color, for T-43, T-42, T-41 and T-47: computed colours and weights matched the native dumps (title `rgb(220,226,233)` bold, state line steel bold, `Needed from you` and `Needs you:` amber bold, labels grey, costs sage, verbs steel, `Enter details ▸` grey; in No color the same luminance steps `#efefef` and `#b8b8b8`). The details popup showed the selected `▌` row bold on `#233340` with grey headlines, ArrowDown moved the selection and Esc closed it. Limit (i) records the differences.

## Design and operator documents

06 §5 (task detail, Escape, keys), the workspace specification (detail section), Focus §4 and §6 and the parity table describe the briefing, the actions row and the details list instead of the old summary, the Views selector, `p`/`n` paging and the `u details`/`e evidence` links. The spec is marked delivered; the README and the design README each gained one sentence.

## Numbers

| Measure | At `4fffb19` | Now |
|---|---|---|
| Collected cases | 1,598 | 1,589 |
| Test functions (`tests/`) | 943 | 945 |
| Lines in `tests/` | 59,508 | 59,412 |
| `detail.py` lines | 2,219 | 1,635 |
| `views.py` lines | 1,906 | 1,898 |
| `contract.py` lines | 983 | 954 |

The new modules (`briefing_view.py`, `details_list.py`, `details_list_view.py`, `row_list.py`, `section_rows.py`) hold 1,158 lines, plus `domain/briefing.py`. Task 184's cleanup removed 13 cases (1,601 to 1,588), not the roughly 30 hoped for; Task 185 added one (1,589). Functions rose by two because the new tests are plain functions where the removed ones were parametrised.

| Run | Result |
|---|---|
| `uv run poe check` (default workers) | green: 1,568 passed, 14 skipped, 91.17 % coverage, 253 s |
| `TERM=dumb NO_COLOR=1 uv run poe check` | green: 1,568 passed, 14 skipped, 91.19 % coverage, 255 s |

## Remaining limits

- (a) Rework reads `N of M-1`: iteration counts rounds from 1, so the default limit 5 reads `rework 2 of 4`.
- (b) The `Enter answer` hint is dropped in favour of the Answer verb. Total time is omitted for Done and Cancelled tasks (no finish time is recorded). CI is never shown (nothing records it).
- (c) Produced numbers are parsed from the rendered recorded-facts text and are a snapshot taken at verify or hand-off, not live.
- (d) Not implemented, for the reasons in the spec: the Events headline `N recorded` (no cheap count), the activity hint, acceptance-criteria status, the plan checklist, ETA and the current file.
- (e) The task-window popup title truncates the task id at about 30 columns, and the category filter label wraps at narrow widths.
- (f) The Home inspector body joins the Tab chain only when it overflows; Tab and Enter reach the details through the always focusable `Enter details ▸` button. The spec's "body focusable consistently" holds for the task window (focused at start) but not for a short embedded inspector; Enter on the body was not exercised.
- (g) Esc from a section returns to the summary (or Home), not to the details list: the list is reopened with `v`. The spec's "section, list, closed" is met as two levels. In Home, `v` opens the task list only when the inspector or a task popup has focus; with the task list focused it opens the project Views, as before.
- (h) The turn filter applies on Enter, not per keystroke. The selected entry of Events and Agent activity still shows its own `Timestamp ·` line and its data as JSON.
- (i) The HTML reference draws the Compact layout for the Compact and Tiny widths, opens Pause and Resume through the Actions menu (it has no pause popup), and opens the Evidence popup for every section except Usage; its sample cost line shows only billed amounts.
- (j) The pause-then-assert sweep (70 sites) and the Task 171 reduced verification remain from the ninth review.
- (k) Flakes seen: `test_overlapping_recorded_agent_roots_are_counted_once` (RSS tolerance under load, passes alone) did not recur in either gate. `test_task_evidence_filter_and_reading_position_survive_resize_and_refresh` was noted by Task 184 at `ef06dd3`; the `d1df283` layout fix followed.
- (l) The stash `stash@{0}` ('sweep') still exists for the operator to drop.
- (m) The plan names this record `...2026-10-13-...`; it is dated 2026-10-14.

## Hygiene

The `4fffb19` worktree, the Chrome profile, the throwaway projects, the harness and its dumps were deleted. No process, tmux socket or Chrome instance of mine remains. The operator's live session, `test-project`, default tmux server and Chrome were not touched.
