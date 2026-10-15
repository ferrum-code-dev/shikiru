# Phase 11 thirteenth-review verification

Status: Task 208 verified Tasks 197–207 on 2026-10-15 against the final source, fixed five small defects, trimmed the test table, updated the HTML reference and reconciled the documents. The spec is [`tui-feedback-thirteenth-review.md`](tui-feedback-thirteenth-review.md) (delivered); the preceding record is the [twelfth review](release-readiness-2026-10-14-phase11-twelfth-review.md). Everything ran on macOS (Darwin 25.3.0, Python 3.14.7, Textual 6.3.0); GNU/Linux remains unverified.

Source: `9308546` (spec), `007bffc` (197), `4ef60eb` (198), `401eaca` (199), `7228f4b` and `ff65888` (200), `c62bb14` (201), `3c7b434` (202), `f2228c0` (203), `18ce637` (204), `473cdf8` (205), `8ad84c2` (206), `d50831e` (207), then Task 208: the Usage body without its repeated title, Up/Down in a scrolling resolution panel, the Events and Agent activity bodies without their title, the Dependencies hint and the severity separator, the test trim, and the reference and documents. The native baseline is `9308546`, a worktree of the commit that planned the review.

## Issue-by-issue result

| Item | Result | Native evidence (real `DashboardApp`, `ResolutionScreen`, `SessionPicker` over seeded throwaway projects, colour and `NO_COLOR`, 130x44 down to 40x8) |
|---|---|---|
| (a) `needs your input` white | pass | `#dce2e9` (PRIMARY_TEXT) against grey `#9aa5b5` for the rest of the consequence; `#e1e1e1` against `#a4a4a4` in `NO_COLOR`. |
| (b) edit box under the option | pass | Empty box directly below `Change behavior ✎`, no title, bottom rule only, no left stripe, selected background while focused; sent `Change behavior — backend` plus the typed lines; an empty box sent the label alone. Before: the boxed `▊▔▎` frame under `Write your own answer…`, prefilled. |
| (c) keys | pass | Ctrl+S, Ctrl+J and Ctrl+Enter each queued the answer; Enter only added a line (focus stayed in the box); → entered it, ← at the start and Esc returned to the list (the modal stayed open); ← mid-text stayed; Tab from the list reached Details, no box; Enter on an unflagged alternative sent at once; own answer sent `mine`. |
| (d) Details | pass | Own Tab stop (Up returns, Tab/Shift+Tab move), highlight `#233340` on heading and body only, grey labels, white values, step names cyan, success `#90cba6`, failure `#e3a0a0`, rework and recovery amber `#dfb16c`, `+N earlier` grey. |
| (e) selection without bars | pass | No `▌▎▍▏▊` in any Home, panel, popup or resolution dump; fold headers, `RowList` rows, Tasks list, Views menu and session picker use `#233340` (`#313131` in `NO_COLOR`) with bold names; the pager reads `↑ later` / `↓ earlier`, grey words and cyan arrows. Before: Views menu and picker in Textual's default blue. |
| (f) briefing | pass | Progress, Result so far and Problems for running, waiting-on-dependency, rejected, escalated, Done and Cancelled at 100, 60, 48, 40 and 40x8; the Cancelled-at-consult task reads `○ Cancelled · stopped during consult`, `✕ Consult stopped`, no `?`, no `waiting on`; a Done task with 45 events shows no `?`; verbs per state (Answer/Message/Pause/Cancel; Pause/Cancel while waiting; Take over/Message/Pause/Cancel running) fit one row at 60, 48 and 40 columns. The Cancel verb requests the palette's cancel action (covered by the existing request test) and queued nothing in every walk. |
| (g) folds | pass | Order and no project-wide fold confirmed; Task description first with a bullet list; Results, Decisions, Events, Agent activity, Usage (`$1.52 billed · 65 tokens`, aligned table, amber note), Dependencies (aligned, Enter opens), Workspace and Settings tables; buttons hidden for Done and Cancelled. 100 events, nudges, verdicts, escalation and Operator text produced no JSON, ids or ISO times in any row; the selected pane was at most four lines. `NO_COLOR`: text identical to the colour run, glyphs (`✓ ✗ !`) carry the tone, key names bold. |
| (h) project views | pass with a limit | Home `v` lists Command history, Attention, Workflow strips, Activity dialogue, Usage details and three Diagnostics views (all opened); Enter on the usage, resources, escalations bars and the command status opens Project usage, Resources, the resolution screen and Command history. Ctrl+P offers Commands and Diagnostics, not Workflow strips, Activity dialogue, Project usage or Resources (see the limits). |

## Defects found and fixed

- Usage fold body repeated its title (`Usage · …`): first row is now `$1.20 billed · 4.2M tokens`.
- At 40x14 and 40x8 Down scrolled the resolution panel instead of selecting (the body's scroll binding won; the baseline was identical): the arrow bindings are priority; two sizes added to a test.
- The Events and Agent activity bodies repeated their title (`Events · following`): now `following` / `paused · End to follow`; the unused section argument was dropped.
- The Dependencies hint showed with no related tasks, and the severity separator in Decisions took the failure colour.
- A palette route change (Views and extensions opening the Home menu) broke the compact evidence route and was reverted; no code change from it remains.

## Test trim

| Measure | Review start (`9308546`) | Before the trim | After |
|---|---|---|---|
| Collected cases | 1,632 | 1,681 | 1,651 |
| Test functions | 965 | 990 | 990 |
| Lines in `tests/` | 60,175 | 61,347 | 61,313 |

The target (at most 1,632) was not reached: the remaining +19 cases are new rule tables for behaviours that had no test (results, decisions, phrases, usage rows, terminal stages, the digest rows, resolution keys). Merged into one table each: briefing size and verb-row copies (and a duplicated compact size), the phrase and packet tables, the terminal-stage table, the fold-key cases, the workspace-tools states, the wait-reason states and the small-pane resolution sizes; one duplicate terminal-Done row was dropped. No behaviour lost its row; nothing in the spec lost its only coverage.

## HTML reference

`docs/design/references/focus-steel-approved.html` SHA-256 `b957346a9da185d90925f52f393cf24b09c1494b221738fbe6aa6f5bedd99c61` (was `90df8a03…1672`). The `srcdoc` was decoded, edited and re-encoded; the original re-encodes to the same bytes and the edited text decodes back exactly, bytes outside the `srcdoc` unchanged. It now draws the white `needs your input`, the edit box under the option with the bottom rule, the `Ctrl+S send · Enter new line · ← back` hint, Details as label/value rows and a Tab stop, the Progress / Result so far / Problems briefing with Cancel and no Message while waiting, the nine folds and their bodies, background-only selection, grey `↑ later` / `↓ earlier` with cyan arrows. Headless Google Chrome with a throwaway profile (deleted; the operator's Chrome untouched), colour and No color, wide, compact and tiny: computed colours matched the native cells (`rgb(220, 226, 233)` white, key names `rgb(131, 189, 221)`), box borders `0 0 0 1px`, Enter inserted no send, ← at the start and Esc returned to the list, Ctrl+S opened the preview result, no `▌`, no overflow outside the terminal; screenshots read for layout.

## Decisions

`ESCALATION_RESOLVED` and `OPERATOR_MESSAGE` events carry a `text` field read by Decisions; the timeline gained an internal `agent` category (Agent activity) so `latest_signal` keeps the full stream; Ctrl+Enter reaches the app only with the kitty keyboard protocol that tmux does not forward, so the hint shows Ctrl+S and Ctrl+J also sends.

## Gates

| Run | Result |
|---|---|
| `uv run poe check` | green: 1,630 passed, 14 skipped, 91.44 %, 266 s |
| `TERM=dumb NO_COLOR=1 uv run poe check` | green: 1,630 passed, 14 skipped, 91.43 %, 265 s |

The first plain gate (before the revert above) failed one test of the reverted change; it passed after the revert. No known flake occurred.

## Remaining limits and parked items

- Ctrl+P does not offer Workflow strips, Activity dialogue, Project usage or Resources (Home `v` and the bars do); the palette's Views and extensions route opens the task folds.
- The workspace buttons scroll horizontally at 40 columns instead of wrapping.
- A queued task with no records shows `? ? ? ?` stages; Events says `No recorded entries match.` for an empty unfiltered list; a waiting task's Problems row reads `Needed Waiting on dependency.`.
- A task whose detail fails to load shows every verb; that state is unreachable with real data.
- The HTML reference keeps illustrative sample data and static Events and Agent activity rows.
- Known flakes: `test_task_evidence_export.py::test_palette_export_controls_and_full_result_remain_reachable`, `test_engine_daemon.py::test_take_over_holds_only_that_task_and_hand_back_resumes_its_session`, `test_dashboard_terminal_palette_event_keeps_home_mounted_under_actions[size1]` (load only).

## Hygiene

The `base` and `head` worktrees, the Chrome profile, harnesses, dumps and throwaway projects were deleted. The operator's live session, `test-project`, default tmux server and Chrome were not touched.
