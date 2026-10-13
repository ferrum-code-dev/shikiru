# Phase 11 tenth-review verification

Status: Task 179 verified Tasks 176–178 on 2026-10-13 against the final source, replaced the resolve modal of the HTML reference, reconciled the design documents and marked the spec delivered. The spec is [`tui-feedback-tenth-review.md`](tui-feedback-tenth-review.md); the preceding record is the [ninth review](release-readiness-2026-10-13-phase11-ninth-review.md). GNU/Linux remains unverified; everything ran on macOS (Darwin 25.3.0, Python 3.14.7, Textual 6.3.0).

Source: `36e6387`, `33f1a0a` (176), `1974f0f`, `8cd09ac` (177), `c1eb679` (178), then Task 179's `e7f2254` (HTML reference) and `0373e17` (design documents), with this record, the README and spec links and the plan and todo ticks in the last commit. Task 175 (`24e0684`, the evidence-reveal fix) belongs to the ninth-review record.

## Requirement-by-requirement result

| Requirement | Commits | Result | How verified |
|---|---|---|---|
| One panel replaces the palette resolve form (Escalation select, summary, six evidence collapsibles, `Back to actions`, per-mode submit, retry rung) | `1974f0f`, `c1eb679` | pass | Before: the bar opened `Answer decision · E-1 · T-1` with Question, Leader suggestion, an answer box, seven collapsibles and `Back to actions` / `Submit answer`; engine reasons added a Resolution select and a rung field. After: the resolution screen only. |
| Opens for one concrete escalation from bar Enter, bar click, Home strip click, task menu Answer decision and `Ctrl+P` Resolve | `8cd09ac` | pass with limit (a) | All five routes on the real `DashboardApp`: each showed `Resolve escalation · E-1 · T-1` and queued nothing. Before: the same routes opened the palette form. |
| Leader decision layout (Decision, Choose, ★ alternatives, consequences, own answer, Cancel, collapsed What was tried) | `33f1a0a`, `1974f0f` | pass | Dumps at 100x40, 48, 40 columns and 80x8, colour and `NO_COLOR` (spans below). |
| Engine escalation layout (Reason with cause, Retry, Cancel; Accept for `iterations_exhausted`) | `1974f0f` | pass with limit (b) | `integration_blocked` and `iterations_exhausted` over the same sizes and palettes. |
| Sending: Enter or click sends; Cancel needs a second Enter; success closes and Home shows Pending | `1974f0f` | pass | Five paths each queued exactly one `Resolve` and closed the screen; the Home status read `Pending: Resolve E-…`. |
| Rejection stays open with the engine's reason on one line | `1974f0f` | pass | The engine lock was held so the send waited for an ack; the queued `Resolve` was marked rejected; the screen stayed open with `E-1 is already resolved` in dusty rose. |
| Resolved elsewhere, stale or missing: reason on one line, choices disabled, draft kept | `1974f0f` | pass | Resolving E-1 from a second connection showed `E-1 was resolved; task T-1 is escalated.` in amber; Enter queued nothing. An unknown id (`E-404`) opened with `escalation E-404 is not open` and no choices; a missing packet opened with the own-answer row and Cancel only. |
| Content: only `What was tried`, no raw headline beside its parts, age shown | `33f1a0a`, `1974f0f` | pass | Dumps: `Decision` with `waiting 2m`, Task, Reason or Question; no Timeline, Reports, Facts, Sessions or packet. |
| Sizes: wide, from 48 `Resolve · E-3 · T-12`, 40 and below folded, tiny scrolls | `1974f0f` | pass | Dumps at 100x40, 48x30, 40x30, 80x8. |
| Non-focus attention cards and the `o` popup unchanged | n/a | unchanged | Not touched by Tasks 176–178; limit (e). |
| Shared cause and consequence texts, test cleanup | `36e6387`, `c1eb679` | pass | `palette.py` 2,347 lines at `cbe2fe8`, 1,889 now; see Numbers. |

## Native verification

A throwaway harness patched `DashboardApp.run_async` to `run_test`, then called `bootstrap.run_dashboard` itself, so the real wiring ran (`build_operator`, `DashboardServices`, `SystemClock`, `StateReader`, the resolution screen factory) over throwaway git projects seeded with `tests/support/escalations.py` (`decision_required`, `integration_blocked`, `iterations_exhausted`, and a decision with its packet file missing). `TMUX_TMPDIR` pointed at an empty scratch directory. The baseline was a `git worktree` of `cbe2fe8` first on `sys.path`; the harness asserted the module path of each run. Every compositor cell was read with its foreground, background and bold flag; monochrome runs set `NO_COLOR=1 TERM=dumb`. Home was driven at 160x50, the screens resized for the layouts.

- **Leader decision, colour.** Title `Resolve escalation · E-1 · T-1` bold white `#dce2e9` with `Esc ×`; `Decision` and `waiting 2m` grey `#9aa5b5`; labels grey; Task and Question values white; `Choose` bold steel-blue `#83bddd` with the hint grey; the selected row on `#233340` with `▌` and `★` bold steel; the other alternatives steel, `Write your own answer…` steel, `Cancel task` dusty rose `#e3a0a0`, consequences grey, `What was tried ▸` grey.
- **Engine escalation.** The same with `Reason  Integration blocked` and the engine's cause in grey, `Retry` bold steel on the selected row, `Cancel task` dusty rose; `iterations_exhausted` lists the alternatives, `Accept` in sage `#90cba6`, Cancel.
- **`NO_COLOR`.** The same layout in luminance greys: `Choose` bold `#b3b3b3`, grey text `#a4a4a4`, `Accept` `#bcbcbc`, `Cancel task` `#aeaeae`, selection `#313131`; the bar, star, bold and text still carry the meaning.
- **Sizes.** 100x40 wide as drawn. 48x30: title `Resolve · E-1 · T-1`, everything else kept. 40x30: title `T-1 · Decision required`, only the question, the Choose heading and the list. 80x8 (tiny): the question and the list, the rest scrolls (a scrollbar thumb appeared); the Choose heading is hidden.
- **Cancel.** First Enter turned the row into `Press Enter again to cancel` (bold dusty rose); the second queued `CancelResolution`. `x` reset it (covered by the integration test).
- **Before.** The baseline opened the palette form (`Answer decision · E-1 · T-1`, Question, Leader suggestion and its consequence, the answer box, `Consequence:`, collapsed Timeline, What was tried, Reports, Facts, Recent turn signals, Sessions and Original full packet, `Back to actions`, `Submit answer`; for the engine reasons a Resolution select, a rung field and `Submit retry`).

Four things the harness does not claim: the engine itself was never run (the queued commands were inspected, and the engine side is covered by the integration tests), the harness's throwaway `Submit` command from seeding stayed pending in every run, the second-Enter reset by another key was read from the test rather than a dump, and the 40-column and tiny dumps were of the same decision only.

## HTML reference

`docs/design/references/focus-steel-approved.html` SHA-256 `022525320ba2c46c9eed1158a4e6e3b70ac590199652d2918df3558483f41d66`. Task 179 decoded the entity-encoded `srcdoc`, edited the app and re-encoded it; re-encoding the unedited text reproduced the original bytes, and the bytes outside the `srcdoc` are unchanged. Changes: the `resolve` overlay is the Choose-list panel (Decision with task and age, question, Choose heading and hint, the two Leader alternatives with consequences and the ★ on the recommended one, the own-answer row that reveals a labelled textarea, `Cancel task` in dusty rose with the engine's consequence text, a collapsed `What was tried ▸` toggled by click or `t`), its title follows the width (`Resolve escalation · E-7 · T-42`, `Resolve · E-7 · T-42` compact, `T-42 · Decision required` and no Decision section or Choose heading in the tiny preview), the footer is gone, and the size table entry grows to `[460, 410]`. Keyboard: ↑↓ select, Enter sends (a preview result, as before), the own-answer box sends on Enter and inserts a newline on Ctrl+Enter, Cancel needs a second Enter and any other key resets it; a click on a row sends it the same way. The old `Submit answer` footer, `Leader suggests` insight and `Answer → consult → continue` line are removed.

The Chrome check loaded the decoded app plus a measuring script in headless Google Chrome under a throwaway profile (deleted; the operator's Chrome was not touched). Wide, compact and tiny, colour and No color: the computed colours and weights matched the native dumps in colour (`Choose` bold `#83bddd`, grey `#9aa5b5` hint, consequences and `What was tried ▸`, selected row on `#233340` with bold steel mark and label, `Cancel task` `#e3a0a0`, Question and Task bold `#dce2e9`); the wide and compact frames showed everything without scrolling and the tiny preview scrolled. The interaction run confirmed Enter on the preselected row, ↓ Enter, own answer (Ctrl+Enter newline kept in the box, Enter sends), the Cancel double Enter with the reset by `x`, row clicks, the `t` toggle and the toggle click.

## Design and operator documents

06 §5.5 now describes `ResolutionScreen` (routes, Decision and Choose rules, sizes, race and rejection behaviour) and §6.4 names the screen as the action target; the workspace specification (attention, Resolve row, §10.1 and its race paragraph), the Focus specification and the parity table say the same and no longer mention the Answer form or the generic resolve selector. The spec is marked delivered. The README and the design README each gained one sentence.

## Numbers

| Measure | At `cbe2fe8` | Now |
|---|---|---|
| Collected cases | 1,607 | 1,598 |
| Test functions (`tests/`) | 951 | 943 |
| Lines in `tests/` | 60,565 | 59,508 |
| Lines in `tests/integration` | 47,979 | 46,559 |
| Lines in `tests/unit` | 4,305 | 4,377 |
| `palette.py` lines | 2,347 | 1,889 |

Task 177's proof quotes 1,608 cases before and after; that was the count after Task 176's added case, `cbe2fe8` itself collects 1,607. The net nine fewer cases come from deleting the old form's wording and shape tests and adding the new screen's behaviour tables.

| Run | Result |
|---|---|
| `uv run poe check` (default workers) | green: 1,577 passed, 14 skipped, 90.34 % coverage, 242 s |
| `TERM=dumb NO_COLOR=1 uv run poe check` | green: 1,577 passed, 14 skipped, 91.12 % coverage, 236 s |

## Remaining limits

- (a) `Ctrl+P` Resolve opens the task's first open decision, not the bar's Left/Right choice, because the palette cannot see the bar's selection. The task-menu and `Ctrl+P` entry test presses the menu button programmatically because `pilot.click` missed it. A palette opened with a bare `initial_action="resolve"` reports `unsupported palette action`; no caller does this.
- (b) The Cancel row shows the engine's full consequence text, not the short mockup line (`Keeps its branch.`); the HTML reference follows the product.
- (c) One earlier gate showed a palette-context-text race in `test_empty_home_footer_actions_are_mouse_reachable_and_read_only[size2]` that passed alone.
- (d) The sweep of 70 pause-then-assert sites remains descoped.
- (e) The non-focus attention cards (`SplashPanel`) and the `o` popup are unchanged and still show numbered options and `Allowed actions`.
- (f) The HTML reference draws one decision (the Leader layout) and its No color palette uses one ink for statuses, where the native `NO_COLOR` render uses luminance greys; the 40-column fold and the tiny pane share the tiny preview.

## Hygiene

The `cbe2fe8` worktree, the Chrome profile, the throwaway projects, the harness and its dumps were deleted. No process, tmux socket or Chrome instance of mine remains. The operator's live session, `test-project`, default tmux server and Chrome were not touched.
