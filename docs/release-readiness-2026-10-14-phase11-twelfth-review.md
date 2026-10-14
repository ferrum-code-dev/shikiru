# Phase 11 twelfth-review verification

Status: Task 196 verified Tasks 186–195 on 2026-10-14 against the final source, updated the HTML reference, reconciled the design and operator documents and marked the spec delivered. The spec is [`tui-feedback-twelfth-review.md`](tui-feedback-twelfth-review.md); the preceding record is the [eleventh review](release-readiness-2026-10-14-phase11-eleventh-review.md). GNU/Linux remains unverified; everything ran on macOS (Darwin 25.3.0, Python 3.14.7, Textual 6.3.0).

Source: `00c6f32` (186, Up/Down from every bar), `6507301` (187, no `u`/`e`), `f36cb42` (188, background highlight and the arrow-reachable `What was tried`), `d20400a` (189, the digest), `8449671` (190, `needs_input` alternatives), `da52d96` (191, wrapped Now), `c9e97e5`, `ef1523e`, `92028e4` (192-194, independent timelines, inline folds, entry points), `39cb767` (195, steel-blue key names), then Task 196's `ce88154` (cleanup), `61ecc58` and `f7ef8c8` (two small fixes found by the check), `cbefb08` (the colour assertions under `NO_COLOR`), and the HTML reference, documents and this record in the last commit. The baseline for the native before/after is `494c757`, the commit that planned the review.

## Issue-by-issue result

| # | Item | Result | Native evidence (real `DashboardApp` / `ResolutionScreen` over seeded throwaway projects, colour and `NO_COLOR`) |
|---|---|---|---|
| 1 | Down/Up select tasks with focus on the escalation bar, usage, resources and the footer | pass | Before (`494c757`): Tab to each stop, Down, Down, Up, Up left the selection on T-2 at 120x40 and 48x20 for all four bars; only the task list moved. After: the same keys give T-2, T-3, T-1, T-3, T-2 from every stop, in both sizes, and the focus stays on the bar. |
| 2 | `What was tried` reachable with the arrows, Enter expands without sending | pass | Before: Down walked to Cancel task and stopped, the row was never selected (Enter there armed the cancel and a second Enter queued it). After: the fifth Down, after the five choices, selects the row (hint `↑↓ select · Enter expand`), Enter expands it (hint `Enter collapse`), a second Enter collapses it, and the command queue stayed empty throughout. |
| 3 | A `needs_input` alternative opens the prefilled box on Enter; Tab adds details to another | pass | Before: Enter on `Provide the API key` queued `AnswerResolution('Provide the API key')` at once and Tab did nothing. After: the row reads `Provide the API key ✎` / `needs your input · …`, Enter opens the box with `Provide the API key — ` and queues nothing; typing `sk-123` and Enter queued `E-1 AnswerResolution(text='Provide the API key — sk-123')`. Tab on the recommended alternative opened `Keep existing behaviour — ` and sending `use v2` queued `Keep existing behaviour — use v2`; Enter on it alone still queued `Keep existing behaviour` at once. |
| 4 | The digest for a task that ran rounds | pass | A task with implement x3, verify x2, consult x1, six reports, two nudges and an Operator message, raised through the real `EscalationRaiser`: the expanded row read the reason sentence, `Rounds: implement ×3, verify ×2, consult ×1 (iteration 3 of 5)`, `+4 earlier`, `Implementer: 2 commits, 1 tests passed, 1 failed`, `Leader verdict: changes requested`, `Recovery: 2 nudges`, `Produced: 3 commits, 5 files, +120 -34`, `Operator: 1 message` (eight lines, the cap). The `1 tests passed` grammar was wrong and is fixed in `f7ef8c8`. |
| 5 | No `▌`, a Tasks-list-style highlight in the resolution panel | pass | Before: 18 `▌` glyphs in the colour dumps, bold selected labels. After: none in colour or `NO_COLOR`; the selected rows carry the `$selected-surface` background (`#233340`, `#313131` in `NO_COLOR`) and identical text columns. |
| 6 | Cyan key names on every panel, `NO_COLOR` keeps bold | pass after two fixes | Every dump was scanned for key words (`Enter`, `Esc`, `Tab`, `Ctrl+P`, `PgUp`, `End`, `↑↓`, `Up/Down`, `Left/Right`) painted in a colour other than `#83bddd`. Painted and bold: Home hint bar and footer (`↑↓`, `Esc`, `Ctrl+P`, `?`), the `Ctrl+P actions` and `Enter answer ↵` bar, Help (`Up/Down`, `Escape`, `/`, `v`, `o`, `r`, `Left/Right`, `←`, `→`), every popup's `Esc ×`, the resolution `Choose` hint, the Events and Agent activity hint (`↑↓`, `PgUp/PgDn`, `End`), the Recent pager (`↑`, `↓`; the `Later`/`Earlier` words keep their steel or grey), the session picker (`Enter`, `Esc`). Two were missed and fixed in `61ecc58`: the briefing's `↓ details` hint was uniformly grey, and the `End` in a paused `Events · paused · End to follow` heading was grey. In `NO_COLOR` the same words are bold (`BOLD: ['Choose', 'Enter', 'Esc', 'Tab', '↑↓']` and so on). |
| 7 | Now wraps a long message | pass | A 120-word message: 6 lines ending with `…` at 100 and 60 columns, 2 lines at 48, 40 and a 40x8 pane, nothing below overflowing; before, one line cut at 160 characters. |
| 8 | `u`/`e` inert | pass | Before: both keys pushed a `TaskDetailScreen` (Events) from Home and from the task window. After: the screen stack and focus are unchanged and no command is created. |
| 9 | Inline folds | pass with limits (c)-(e) | Home wide (130x44), Home compact (60x44), the task window (100x40, 48x30), a 40x10 pane and the task popup: Tab reaches the `↓ details` hint; Enter focuses the first header; Down moves between headers; Enter, Space or Right open; Left closes; five folds open at once with their nested lists capped; Esc from a list returns to its header, Esc from a header leaves the panel (and in the popup closes it); a nudge event recorded while Events and Agent activity were open appeared with no key pressed, folded sections costing no read; `v` with the inspector focused focuses the folds and with the task list focused opens the project Views. Headlines are live (`report implementation`, `$1.52 billed`, `priority 0`). |

`NO_COLOR`: the text of every dump is identical to the colour run (two scrollbar glyphs of a live list differ); the selected-surface background is `#313131` instead of `#233340`, steel text is `#b3b3b3`, and the key names stay bold.

## Method

A throwaway harness imported the real `DashboardApp`, `ResolutionScreen` and `SessionPicker`, seeded each scenario with the helpers of `tests/integration/test_dashboard_briefing.py` and `tests/support`, ran them with Textual's `run_test`, and read the cells from the compositor with foreground, background and bold. It ran once against `HEAD` and once against a `git worktree` of `494c757` first on `sys.path` (the harness printed the module path of each run), at 120x40, 100x40, 60 columns, 48x20 to 48x40, 40x40 and 40x8-12, with `TERM=xterm-256color` and with `TERM=dumb NO_COLOR=1`. Nothing was claimed from a run that raised. The worktrees, projects, harness and dumps were deleted afterwards.

Not claimed: the engine was never run, the tmux window and pane navigation was not exercised, and the Leader prompts that tell it when to set `needs_input` were not run against a real Leader.

## HTML reference

`docs/design/references/focus-steel-approved.html` SHA-256 `90df8a03f209de6071dba8b7e5ce40379e46d8a4f4b36dfd6ea711d49f791672` (was `79944d9e…b0e3`). The entity-encoded `srcdoc` was decoded, edited and re-encoded; the original re-encodes to the same bytes, the edited text decodes back exactly and the bytes outside the `srcdoc` are unchanged.

Changes: the briefing's last row is the `↓ details` hint (`↓` compact) and the nine sections are folds below it (a header with its headline and a body per section, arrow, Enter, Space, Right, Left and Esc keys, several open at once, state kept across task changes, the `v`, hint, menu Evidence and menu Usage routes focus or open them); the details popup is gone; `Now` wraps to a six-line clamp (two in compact and tiny) over a longer sample message for T-43; Up/Down from the focused escalation bar and the other bars select tasks and keep the focus on the bar; `e` and `u` are gone from the keys and the Help text; the resolution modal has the background-only highlight, the starred row's `★` without `▌` or bold, a `Provide the retry limit ✎` alternative with `needs your input ·` and a prefilled box on Enter, Tab on any other alternative opening the same box, `What was tried` as the last arrow row with the digest, and a hint that follows the selected row (`Enter send · Tab add details`, `Enter answer`, `Enter expand`/`Enter collapse`); key names (footer, bar hints, `Esc ×`, Return, Help, Recent pager, fold list hints, modal hint) use the bold accent `shk-key`.

Chrome check: the decoded app plus a measuring script in headless Google Chrome under a throwaway profile (deleted; the operator's Chrome was not touched), colour and No color. Colours and weights matched the native cells (key names `rgb(131, 189, 221)` = `#83bddd`, weight 700; `rgb(239, 239, 239)` bold in No color); `Now` measured 6, 2 and 2 lines at wide, compact and tiny; folds measured as listed under item 9; the modal had no `▌`, the selected row used the `#233340` selected surface, Enter on the flagged row opened `Provide the retry limit — ` with the caret at the end and focus in the box, Enter in the box reached the preview result, Tab on the first alternative opened `Retry transient I/O errors — `, the tried row was reached with five Downs and Enter toggled it without leaving the modal; Down on the focused escalation bar moved T-43 to T-44 with focus kept on the bar; `u` and `e` opened nothing. Screenshots at wide and compact widths were read for layout.

## Design and operator documents

06 §5 (Up/Down from every bar, `u`/`e`, the folds hint, the wrapped Now, the resolution screen's highlight, `needs_input`, the digest and the key-name rule), 05 (`Option.needs_input`, `[escalation] tried_lines`), 03 §8 (the digest and the flag in the packet), `tui-focus.md`, `tui-workspace.md` (briefing and §10.1), `tui-parity.md`, the design README, the README and the status lines of the fifth, tenth and eleventh specs (each now points at the twelfth review where it was superseded) describe the delivered behaviour. The spec is marked delivered.

## Numbers

| Measure | At `52385a2` (start of the review) | Start of Task 196 (`39cb767`) | Now |
|---|---|---|---|
| Collected cases | 1,589 | 1,631 (1,624 in the brief) | 1,632 |
| Test functions (`tests/`) | 945 | 964 | 965 |
| Lines in `tests/` | 59,412 | 60,133 | 60,171 |
| `detail.py` lines | 1,635 | 1,280 | 1,278 |
| `views.py` lines | 1,898 | 1,898 | 1,877 |
| `contract.py` lines | 954 | 958 | 951 |

Task 196's cleanup removed the embedded timeline path of `task_detail` (its `timeline_*` arguments, `include_timeline`, `TaskDetailView.timeline`, the read-error projection for it and `DEFAULT_TASK_TIMELINE_CATEGORY`) and converted the three tests that read `detail.timeline` to the sections' own `task_timeline`, so no behaviour test was lost and the case count did not fall (the commit is 38 insertions and 70 deletions); `include_timeline=False` disappeared from 20 call sites. Cases rose by one: the new test that proves the details hint and a paused timeline heading paint their key names. Vulture at 60 % confidence found nothing else from Tasks 186-195; `current_tick` in `dashboard/tick.py` has been unused since `ad7ad11` and is left alone.

| Run | Result |
|---|---|
| `uv run poe check` (default workers) | green: 1,611 passed, 14 skipped, 91.42 % coverage, 299 s |
| `TERM=dumb NO_COLOR=1 uv run poe check` | green: 1,611 passed, 14 skipped, 91.45 % coverage, 258 s |

The first `NO_COLOR` gate failed: nine tests of Tasks 188 and 195 and the new one compared steel-blue foregrounds, which `NO_COLOR` turns grey (the plain gate had passed). They now use the `colour_terminal` fixture of the other colour-asserting dashboard tests (`cbefb08`); both gates above ran on the final code. No known flake occurred.

## Remaining limits

- (a) The resolution panel at 80x12 shows only the Decision rows until Down scrolls to the list (the baseline is identical).
- (b) The answer box for a flagged or Tab-opened alternative appears under the `Write your own answer…` row, the shared box, not under the alternative; the highlight of `What was tried` includes the rule row above it and, expanded, the digest.
- (c) The folds' selection bar and the arrow-list rows keep the eleventh review's `▌`; the twelfth review removed it only from the resolution panel.
- (d) Enter on an interactive fold (Events, Agent activity, Blockers and related) opens it and moves into its list; Esc returns to the header, so opening several interactive folds takes Enter then Esc each.
- (e) The HTML reference draws Events and Agent activity as static rows (no paging or arrow list), appends the compact `Now` age on its own line, and keeps the fold bodies as illustrative sample data; its details popup, Evidence popup and Usage popup are gone because the routes open the folds.
- (f) The session picker's selected row uses Textual's default blue, not the Shikiru theme (unchanged by this review).
- (g) The pause-then-assert sweep (70 sites) and the Task 171 reduced verification remain from the ninth review; `stash@{0}` ('sweep') still exists for the operator to drop.
- (h) Known flakes: `test_task_evidence_export.py::test_palette_export_controls_and_full_result_remain_reachable` and `test_engine_daemon.py::test_take_over_holds_only_that_task_and_hand_back_resumes_its_session` (timing under load); see the gate lines for any occurrence.

## Hygiene

The `494c757` and `52385a2` worktrees, the Chrome profile, the throwaway projects, the harness, the HTML working files and every dump were deleted. No process, tmux socket or Chrome instance of mine remains. The operator's live session, `test-project`, default tmux server and Chrome were not touched.
