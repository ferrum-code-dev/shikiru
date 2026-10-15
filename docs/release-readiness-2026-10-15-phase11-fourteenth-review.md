# Phase 11 fourteenth-review verification

Status: Task 219 verified Tasks 209–220 on 2026-10-15 against the final source, fixed one flaky test, one small defect and the colour tests' NO_COLOR handling, trimmed the test table back to the review start, updated the HTML reference and reconciled the documents. The spec is [`tui-feedback-fourteenth-review.md`](tui-feedback-fourteenth-review.md) (delivered); the preceding record is the [thirteenth review](release-readiness-2026-10-15-phase11-thirteenth-review.md). Everything ran on macOS (Darwin 25.3.0, Python 3.14.7, Textual 6.3.0); GNU/Linux remains unverified.

Source: `8060bdb` (spec), `814c001` (209), `182c961` (210), `33e5fd8` (211), `0aa9dc3` (212), `73ad927` and `e0675e2` (213), `822f423` and `727c5be` (220), `a886bf9` (214), `3a12ff8` (215), `726a3e9` (216), `9db17de` (217), `9807b85` (218), then Task 219: `2a03275` (activity headline wait), `15a45f9` (filter-bar arrows), `06637b5` (test trim), `ed9f7eb` (HTML reference), `a4a95d0` (colour tests), `50102a2` (documents) and this record. The native baseline is `ad56834`, the thirteenth-review tip, in a worktree.

## Issue-by-issue result

Real `DashboardApp`, `TaskDetailPanel` and `ResolutionScreen` over seeded throwaway projects (`FakeClock`, real SQLite), colour and `NO_COLOR` (`TERM=dumb`), 130x44 down to 40x8; text and per-cell colour read from the compositor.

| Item | Result | Native evidence |
|---|---|---|
| Escalation panel keys | pass | Enter on an editable option opened the box (focus in it, list hint `↑↓ select · Enter edit`, box hint `Ctrl+S send · Enter new line · Esc back`); Left and Right did nothing on the list or in the box; Enter inserted a line; Esc hid the box, focus on the list; moving to another row hid an alternative's box and discarded its text; a non-empty own-answer draft stayed visible under its row through Esc and row moves. |
| Details turn lines | pass | `triage · Leader · triaged as implement`, `implement · Implementer · 3 commits, 12 tests passed`, `verify · Leader · changes requested (1 blocker)`, `consult · Leader · escalated`; step `#83bddd`, role `#dce2e9`; Details stays its own Tab stop (`Enter expand · ↑ choices`). |
| One-column scrollbars | pass | Measured: the resolution box reports `scrollbar_size_vertical=1` with the thumb in the last column once scrolled (100x40, 48x40, 40x14); the palette Message field reports 1 (`Region(x=21, width=58)`) at 100x40 and 48x30, colour and `NO_COLOR`. |
| Stage line | pass | A queued task reads `· Triage · Implement · Verify · Merge` with no `?`; a running task with 45 nudges after its `step_started` events keeps `✓ Triage › Implement`; `?` appears only on a seed whose running stage had no event (`? Triage unknown`). |
| Tab and verbs | pass | Tab from the Home list lands on `Take over`, then Message, Pause, Cancel, then the first fold header; the body is not in the chain; no `↓ details` text anywhere; Down on a verb keeps the selection and focus. All four verbs fit at 48 and 40 columns (`Take over  Message  Pause  Cancel`, 33 cells). |
| Briefing colours | pass | Group titles and `Summary` `#83bddd`, `+n` `#90cba6`, `−n` `#e3a0a0` in Commits and in the header's `main (+n -m)`, `Blocker` and `Pending` labels bold amber, the time line flush left, `Added   added T-7 Admin page` for a planning task, compact lines `Pending: Waiting on dependency.`; `NO_COLOR` text identical, bold cues kept. |
| Fold keys and titles | pass | Titles cyan (bold focused), headlines right-aligned, one blank row after an open fold only; Enter on a header opened it and entered its list (Events, Agent activity, Dependencies), Esc stepped back to the header, Enter on an open header folded it; Left and Right do nothing; a second click folds (covered by the fold test). |
| Bottom hint line | pass | Header `↑↓ move · Enter open/close`; Events `↑↓ select · End follow · Esc back`; Agent activity adds `f filter`, the filter bar `r role · t turn · x clear · Esc back`; Dependencies `↑↓ select · Enter open · Esc back`; Esc is named only inside a list or body. Workspace `t task window` only for a live task with a window (covered by the hint test; no window in the seeds). No buttons in Workspace. |
| Task description, Results, Decisions | pass | `Acceptance criteria` cyan bold; commit hashes `#83bddd`; `Plan  added T-2 Admin page` sage and the scope text in full, wrapped; `reported by the Implementer` grey; Decisions a chronological log (grey time, steel `Leader`, white bold `You`, grey `Engine`, wrapped details), headline `question open` or `N decisions`. |
| Events and Agent activity | pass | `following`/`paused` right-aligned on the fold header; short phrase rows; below them only the selected row's full text on `#1c2530` (`#242424` in `NO_COLOR`), no meta line; Agent activity rows `time role turn command`, filter bar `role: any · turn: any` (`f`, `r`, `t`, `x`); Down at the 20th row loaded the older page (24 rows total). |
| Usage, Settings | pass | `$1.52 billed · 65 tokens`: cost white, `65` cyan bold, `tokens` grey; Settings headline `implement · standard priority`, row `standard (0)`. |
| Esc from inside a list | pass, as designed | Real keys on Home: Esc 1 returns the list to its header (selection kept); Esc 2 on the header clears the selection and returns focus to the Home task list; Esc on a verb clears it at once. |
| `NO_COLOR` pass | pass | Text of every briefing, tab, panel and resolution dump identical to colour; line counts, titles and labels differ only in lightness, and key names and bold labels keep their bold cue. |

## Defects found and fixed

- `test_activity_tables_show_status_columns_full_text_block_filter_and_older_rows` failed in two of six runs with coverage on (once on unchanged code): it asserted the fold headlines right after the rows appeared. The headline paints a moment later; the test now waits for it (`2a03275`). It then passed 12 of 12 with coverage, 10 of 10 alone and inside its file under `-n 10`.
- Up and Down in the Agent activity filter bar moved the Home task selection (the bar was not an arrow-key owner); it now owns them like a verb or a list (`15a45f9`, with a case in the verb test).
- The NO_COLOR gate failed three tests added by this review (briefing line counts and titles, the header's line counts, the activity block's background): they assert rendered colours without the `colour_terminal` fixture. They now use it (`a4a95d0`); a palette test (`test_contextual_project_actions_queue_only_after_explicit_submission`) failed once in that gate under load and passed alone four of four and in the next full runs.

## Test trim

| Measure | Review start (`ad56834`) | Before the trim | After |
|---|---|---|---|
| Collected cases | 1,651 | 1,654 | 1,651 |
| Test functions | 966 | 969 | 968 |
| Lines in `tests/` | 61,734 | 62,240 | 62,236 |

The review added three behaviour cases with no earlier coverage (briefing colours, header line counts, the filter-bar arrows) and was otherwise one-for-one. Merged: the 60-column briefing wrap size into 100 columns (both wide layout; 100, 48 and 40x8 remain) and the filter-bar arrow case into the verb arrow test. No wording, shape or constant test was found among the new ones; every operator-found regression keeps its test.

## HTML reference

`docs/design/references/focus-steel-approved.html` SHA-256 `f3b116594dbf11c0a96178e67d7320b981246c1c0539efaabca9649715a9f2f0` (was `b957346a…9c61`). The `srcdoc` was decoded, edited and re-encoded; the original re-encodes to the same bytes, the edited text decodes back exactly and the bytes outside the `srcdoc` are unchanged. It now draws the resolution modal (`Enter edit`, `Ctrl+S send · Enter new line · Esc back`, no arrow keys, box hidden on Esc with the own-answer draft kept, `step · Role · what` turns, thin scrollbar), the briefing (cyan titles and Summary, coloured line counts, Blocker/Pending, no `↓ details`, Cancel verb, `reported by the Implementer`, flush-left time line), the folds (cyan titles, right-aligned headlines, a blank row after an open fold, Enter/Esc/Space keys, the bottom hint line with `t task window` for a live Workspace, the decision log, shaded Events and Agent activity blocks with the filter bar, `123.4k tokens`, no Workspace buttons, the Settings headline) and the header's coloured `main (+24 -8)`. Headless Google Chrome with a throwaway profile (deleted; the operator's Chrome untouched) drove the page over the DevTools protocol, colour and No color, wide, compact and tiny: computed colours matched the native cells (`rgb(131, 189, 221)`, `rgb(144, 203, 166)`, `rgb(227, 160, 160)`, `rgb(223, 177, 108)`, block `rgb(28, 37, 48)`), Enter inserted a newline without sending, Left and Right changed nothing, Esc hid the box and kept the modal, the filter bar cycled and cleared, and No color kept the bold cues. The wrapper file also rendered in Chrome with its iframe.

## Decisions

Priority words carry no mode (`standard`, `high`, `low`; the exact number stays in the Priority row). Decisions reads its kinds by event kind (newest 60, no new storage) and stage states read `task_queued`, `step_started`, `turn_started` and `verdict` (newest 200). Esc names itself in the hint only inside a list or body; on a header it leaves, which on Home clears the selection. The test provenance string stays `agent-reported`; the display says `reported by the Implementer`.

## Gates

| Run | Result |
|---|---|
| `uv run poe check` | green: 1,630 passed, 14 skipped, 91.56 %, 266 s |
| `TERM=dumb NO_COLOR=1 uv run poe check` | green: 1,630 passed, 14 skipped, 91.57 %, 291 s |

An earlier NO_COLOR gate failed the four tests above (three fixed, one load flake that passed alone). Known flakes did not occur: `test_dashboard_terminal_palette_event_keeps_home_mounted_under_actions[size1]`, `test_resource_sampler_identity`.

## Remaining limits and parked items

- Ctrl+P does not offer Workflow strips, Activity dialogue, Project usage or Resources (Home `v` and the bars do).
- At 40x8 the resolution panel shows no answer box and no hint (tiny panes show keys only and the frame scrolls); at 40x14 the typed text is visible.
- Down on a verb leaves the selection and focus alone but scrolls the inspector body by a line when it overflows.
- The Results fold shows its `so far` note under a headline that already says `so far`.
- `NO_COLOR` shows the cyan titles in the grey lightness of other labels; only bold marks keys and amber labels.
- A `stash@{0}` (`On main: sweep`) predates this review and was left alone.
- Known flakes (load only): `test_task_evidence_export.py::test_palette_export_controls_and_full_result_remain_reachable`, `test_engine_daemon.py::test_take_over_holds_only_that_task_and_hand_back_resumes_its_session`, `test_dashboard_terminal_palette_event_keeps_home_mounted_under_actions[size1]`, `test_resource_sampler_identity`, and the palette test named above.

## Hygiene

The `base` worktree, the Chrome profile, the probe harness, dumps and throwaway projects were deleted. The operator's live session, `test-project`, default tmux server and Chrome were not touched.
