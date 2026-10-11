# Phase 11 sixth-review verification

Status: Task 150 verified Tasks 148–149 on 2026-10-12 against the final source and revised the HTML reference. Earlier accepted evidence, the original PNG and the independently open gates 59, 74h and 74p are unchanged. GNU/Linux remains unverified; everything ran on macOS (Darwin 25.3.0, Python 3.14, Textual 6.3.0). The spec is [`tui-feedback-sixth-review.md`](tui-feedback-sixth-review.md); the preceding record is the [fifth review](release-readiness-2026-10-11-phase11-fifth-review.md).

Source: `e90b335`, `b167e5e` (Task 148), `2c41d3a`, `269875b`, `f3558f3`, `328b621`, `f23df80`, `5f88c73` (Task 149) plus the HTML commit `b0075a7`. No code changed during verification.

## Issue-by-issue result

| Issue | Commits | Result | How verified |
|---|---|---|---|
| Subscription-limit gauge colours: sage below 50, amber 50 to below 90, dusty rose from 90, on the displayed whole percentage (`[dashboard.gauges]`) | `e90b335`, `b167e5e` | pass | Domain level table (49.4 to 100), the invalid-threshold settings case, and `test_dashboard_usage_panel.py` asserting rendered colours; context pane titles stay 40/60. Native `UsagePanel` render at 100 and 48 columns: 38 % sage `#90cba6`, 62 % amber `#dfb16c`, 91 % dusty rose `#e3a0a0`. |
| `Usage · Usage` popup title | `2c41d3a` | pass | `test_usage_popup_title_is_the_single_project_usage_label` reads `Project usage` from the real Home popup. |
| Project usage structure and duplication (Totals / Tokens / Coverage / Billing / By agent / Limits) | `f3558f3`, `328b621`, `f23df80`, `5f88c73` | pass | Per-character style tables for the full layout, five states, ` · ` wrapping and the 20-column stack; native dump at 100 and 48 columns shows each fact once with bold white, cyan and green values, grey labels and amber estimates. |

## Walkthroughs and visual checks

The integration modules for the usage panel, remaining widgets, bar templates and popup focus plus the unit domain, level and settings tests passed (145 selected tests, real SQLite and Textual). The real `UsagePanel` was rendered natively with the full fixture at 100 and 48 columns in colour and under `NO_COLOR`. The harness imports the colour fixture, which clears `NO_COLOR` for those tests, so the mono dump is identical to the colour one; the mono guarantees (bold kept, colour dropped) rest on the Task 149 proof and on the HTML `shk-mono` rule. The HTML was opened in headless Chrome (temporary profile, deleted) at wide and compact, colour and No color: the popup is titled `Project usage`; rows, wording and wrapping at ` · ` match the native render (compact wraps `$4.82 API`, `w 120k` and `3 unknown` to the next line); computed colours equal the native styles (white `#dce2e9`, cyan `#83bddd`, green `#90cba6`, amber `#dfb16c`, rose `#e3a0a0`, bold values); mono renders every value in the ink colour with bold kept. The probe test, the Chrome profile and the helper scripts were deleted.

## HTML reference

`docs/design/references/focus-steel-approved.html` SHA-256 `329446970af99cd276cd07cc1afa9ede26ae32ce50d1fd5bb66f59a16d5ccfa1`. The Project usage popup now follows the TUI (title `Project usage`, the six-row structure, sample values spanning sage 38 %, amber 62 % and rose 91 %, `74h 0m` reset format). Palette declarations, the 736 px PNG and all other illustrative values (Home usage bar, task usage, menus) are untouched. The Home menu entry label `Project totals` in the task-less menu was left as it is.

## Gates

| Gate | Result |
|---|---|
| `uv run poe check` | 1,513 passed, 14 skipped, 90.78 %, 284 s (first run, no failures) |
| `TERM=dumb NO_COLOR=1 uv run poe check` | 1,513 passed, 14 skipped, 90.80 %, 285 s (first run, no failures) |

Skipped on purpose: the macOS clean-install smoke (no packaging or install change) and any paid real-agent run (07 §5: no execution, recovery, quota or budget behaviour changed).

## Remaining limits

- Reset countdowns keep the `_duration` format (`74h 0m`, not `3d 2h` as in the approved mockup).
- The compact 20×8 popup frame leaves about 9 columns, so longer words (`subscription`, `API-equivalent`) fold at the edge.
- Under `NO_COLOR` the gauge level is readable only from the number and glyph, and the glyph steps (◑ shared by 38 % and 50 %, ● shared by 89 % and 91 %) do not align with the 50/90 thresholds (pre-existing; an operator decision is offered: align the glyph steps with the thresholds or accept the current ones).
- A stale quota observation keeps the colour of its value; its age is printed.
- The task-scoped usage view shares the renderers.
- GNU/Linux is unverified; gates 59, 74h and 74p remain open.

## Hygiene

No process, tmux socket or Chrome profile of mine remained; the user's live session and default tmux server were not touched.
