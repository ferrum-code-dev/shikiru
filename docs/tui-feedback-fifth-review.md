# Phase 11 fifth real-project review

Status: Tasks 142–145 delivered, 146 open (not reproduced), 147 verified in the [2026-10-11 record](release-readiness-2026-10-11-phase11-fifth-review.md). This specification refines the [fourth review](tui-feedback-fourth-review.md) and Design 05 §3, 06 §5–6 where the requirements below supersede it. **The TUI is the source of truth**: the [HTML reference](design/references/focus-steel-approved.html) follows the product (Task 147).

Analysis: four read-only static analyses (Sonnet 5.5 medium for the escalation bar, motion and tmux items, Haiku 5.5 xhigh for the scrollbar). Nothing was run natively; claims marked *unconfirmed* must be reproduced by the implementing task before it changes behaviour.

Style notation as in the fourth review: the style follows the text it describes.

## Requirements

### Escalations bar
- While the bar has focus, Up and Down do nothing: they neither cycle attention groups nor scroll, never move the task selection, and never bubble to scroll containers. *Decision:* Left and Right stay active, only when the followed task has two or more escalations, because they are the only route to a second or third escalation of one task; if the operator meant all four arrows, Left/Right are disabled and the palette's resolve flow becomes the route.
- A selected task without an escalation reads `T-12<white bold> · No decision needed<white> · 5 decisions on other tasks<grey>`. The grey segment appears only when other tasks hold decisions (N > 0; `1 decision on other tasks` for one). The right-aligned hint keeps falling back to `Ctrl+P actions`. Engine-fault, stall and engine-status rows keep precedence; the no-selection and no-decisions-anywhere states are unchanged (`✓ No decisions needed`, or the first pending escalation armed).

### Task details
- The task-detail scrollbar is one column wide in the Home wide and compact layouts and the standalone task view, like the Tasks list and popup bodies. The width is named once in `ui/theme.py` and shared. The Recent list has no scrollbar (fixed window), and the Select dropdown, wizard and session-picker scrollbars are out of scope unless trivially covered by the same constant.
- Every in-progress status animates the same braille circling as Implementing while work is under way: Starting, and the Triage, Plan, Implement, Consult, Verify and Integrate steps. *Decision:* one pure domain predicate decides it for the detail and the list. Integrating animates while the engine itself integrates (no agent turn is needed), but not while it waits on a PR, CI or retry, nor when held (paused/intervention), waiting, escalated, stalled, terminal, engine down or animations off. The list rows keep the bold status blink; no new glyph column is added there.

### Tmux
- Agent pane titles are restored for both pane roles (Leader and Implementer) after the session, server or a single pane is killed and restarted. The cause is *unconfirmed*: statically the Task 137 refresh treats both roles alike; a stale pane id that now resolves to another task's pane after a tmux server restart is the leading hypothesis. The implementing task reproduces first (two tasks, whole-server kill, single Implementer pane kill, dead pane) and fixes what it finds.

## Superseded accepted behaviour

| Accepted | Superseded by this review |
|---|---|
| Fourth-review / Task 131: `T-12 is not escalated` | `T-12 · No decision needed` with the same other-decisions suffix |
| Task 131: Up/Down on the focused bar cycle attention groups | Up/Down inert while the bar is focused |
| Task 113a / 06 §6.5: spinner only for a live agent turn | Also the engine's own integration and every in-progress step |
| Task 129/139: task-detail body scrollbar width 2 (Textual default) | One column |

## Execution and evidence

Implement Tasks 142–147 sequentially, one fresh agent per task (Haiku 5.5 xhigh for 142–144, Sonnet 5.5 medium for 145–147), root review and corrections by the same agent. Follow AGENTS.md test-value rules. Each commit needs a green `uv run poe check`.
