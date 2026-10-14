# Phase 11 tenth review: escalation resolution panel

Status: delivered (Tasks 176–179, 2026-10-13; see the [record](release-readiness-2026-10-13-phase11-tenth-review.md)); the `▌` marker, the mouse-only `What was tried`, the canned sentence and Enter sending every alternative are superseded by the [twelfth review](tui-feedback-twelfth-review.md). This specification supersedes the resolve form of the palette (Design 06 §5.5, `tui-workspace.md` Resolve, `tui-parity.md` Attention) where it differs. **The TUI is the source of truth**: the [HTML reference](design/references/focus-steel-approved.html) follows the product (Task 179). The operator approved both layouts below (2026-10-13).

Analysis: one read-only static analysis (Sonnet 5.5 medium) of every surface that resolves an escalation. Nothing was run; the operator reviewed the layouts as text mockups.

## Requirements

### One panel, one list of choices
The panel that resolves an escalation shows only what helps the decision: who and what (task, readable reason, age), the question or the reason sentence, the choices, and on demand what was tried. It replaces the palette's resolve form (Escalation select, status line, summary text, Resolution select, guidance line, six evidence collapsibles, `Back to actions`, per-mode submit labels and the retry rung field). It always opens for one concrete escalation: from the escalation bar (Enter, click, Left/Right choose among a task's decisions first), the Home attention strip, the task menu's answer action, or `Ctrl+P` Resolve escalation (which opens the bar's current decision, else the first pending one, else says no decision is needed). The generic escalation picker is removed. The Decision evidence popup (`o`, raw packet) stays.

Leader decision (`decision_required`, and the answer path of `iterations_exhausted`):
```
 Resolve escalation · E-3 · T-12            Esc ×
 ──────────────────────────────────────────
 Decision                    waiting 12m<grey>
 Task      T-12<white bold> · Add retry to parser
 Question  Which failures should the
           parser retry?<white bold>
 ──────────────────────────────────────────
 Choose<steel-blue bold>   ↑↓ select · Enter send<grey>
 ▌★ Retry transient I/O errors<highlighted, steel>
 ▌  Syntax errors fail at once.<grey>
    Fail fast<steel>
    Never retry.<grey>
    Write your own answer…<steel>
    Cancel task<dusty-rose>
    Keeps its branch.<grey>
 ──────────────────────────────────────────
 What was tried ▸<grey>
```
Engine escalation (the 13 retry-or-cancel reasons; `iterations_exhausted` adds Accept in sage):
```
 Resolve escalation · E-7 · T-3             Esc ×
 ──────────────────────────────────────────
 Decision                     waiting 3h<grey>
 Task      T-3<white bold> · Fix flaky test
 Reason    Integration blocked<white bold>
           Rebase conflicts could not be
           resolved.<grey>
 ──────────────────────────────────────────
 Choose<steel-blue bold>   ↑↓ select · Enter send<grey>
 ▌★ Retry<highlighted, steel bold>
 ▌  Re-run the step that raised this.<grey>
    Cancel task<dusty-rose>
    Keeps its branch.<grey>
 ──────────────────────────────────────────
 What was tried ▸<grey>
```

### Rules
- *Choices:* each alternative the Leader proposed (all of them, recommended starred and preselected) is a row with its consequence in grey beneath it; `Write your own answer…` appears when the reason allows an answer and reveals the answer text box under the row (Enter inside it sends, Ctrl+Enter inserts a newline); the engine kinds Retry (steel), Accept (sage) and Cancel task (dusty rose) follow, each with the engine's consequence beneath. Meaning is carried by position, star, colour and text, so it survives `NO_COLOR`.
- *Sending:* Enter or a click on a choice sends it at once (a Leader alternative sends its label as the answer). Cancel needs a second Enter: the first turns the row into `Press Enter again to cancel`, any other key or choice resets it. A successful send closes the panel and the Home command status shows Pending then Applied; a rejection keeps the panel open with the engine's reason on one line. A decision resolved elsewhere, stale or missing shows its reason on one line and disables the choices; a typed answer draft is kept per escalation as today.
- *Content:* Decision shows the task id and title, the readable reason with the engine's one-sentence cause for engine escalations (never both the raw headline and its parts), the question for Leader decisions, and the age since the escalation was created. `What was tried` is the only evidence section (collapsed; absent when empty); Timeline, Reports, Facts, Recent turn signals, Sessions and the original packet are dropped from the panel because the task detail, the Leader's terminal and the `o` popup show them. The retry rung field is dropped (`Set rung` remains an action).
- *Sizes:* wide as drawn; from 48 columns the title shortens to `Resolve · E-3 · T-12`; at 40 columns and below Task and Reason fold into the title and only the question, the list and the hint remain; tiny panes show the question and the list and scroll the rest.

## Superseded accepted behaviour

| Accepted | Superseded by this review |
|---|---|
| Palette resolve form with selects, summary text, six evidence collapsibles, per-mode submit | The choice panel above |
| Generic `Ctrl+P` resolve with an escalation picker | Opens one concrete escalation |
| Evidence collapsibles inside the form | `What was tried` only; raw packet via `o` |

## Out of scope
The non-focus attention cards (`SplashPanel` with numbered options and `Allowed actions`) and the `o` popup are unchanged; the cards may get the same trim in a later review.

## Execution and evidence
Tasks 176–179, one fresh agent per task (Sonnet 5.5 medium), root review after each. Follow the AGENTS.md test-value rules: delete the wording and shape tests of the old form, keep the behaviour tests (routing, no dispatch on open, race with another client, draft kept, degraded packet, real engine applies the resolution). Each commit needs a green `uv run poe check`.
