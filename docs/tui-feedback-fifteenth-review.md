# Phase 11 fifteenth review: action popups and help

Status: in progress (Tasks 223-224). Refines the [fourteenth review](tui-feedback-fourteenth-review.md) where it differs. **The TUI is the source of truth.** The operator approved the decisions below; do not redesign.

## Action popups (Task 223)

Applies to the shared `PaletteScreen` reached from task details (an action preset) or from the Ctrl+P menu.

### Message
- Title `Message agent · T-123` (no role part; the role stays in the Role field). Take over, Hand back, Rung and Peek keep the role in their titles.
- A popup opened from task details has no `Back to actions` button; Esc closes it. Popups reached from Ctrl+P keep it.
- The `Queue message` button becomes the hint `Ctrl+S queue message · Enter new line` (keys cyan bold, labels grey). Enter inserts a newline in the Message area; Ctrl+S queues it.
- The Role drop-down has no blank `Select an item` entry; it defaults to the task's action role (Leader fallback).
- The Message area uses the common edit-area style of the resolution answer box (auto height 2-6, bottom line `$neutral-border`, `$focus` with `$selected-surface` when focused, transparent cursor line), shared through `ui/popup.py`.

### Pause and Cancel
- No `Back to actions` from task details; the submit button becomes the hint `Enter pause task` / `Enter cancel task`, activated by Enter while the action is available.
- Cancel confirms in two steps: the first Enter turns the hint into `Enter again to cancel task` (amber); Esc or any other key backs out, the second Enter cancels.
- The static text never shows a scrollbar unless it cannot fit the pane height.

## Help panel (Task 224)

Help panel tabs; specified when Task 224 starts.
