# Phase 11 fifteenth review: action popups and help

Status: in progress (Tasks 223-227). Refines the [fourteenth review](tui-feedback-fourteenth-review.md) where it differs. **The TUI is the source of truth.** The operator approved the decisions below; do not redesign.

## Action popups (Tasks 223, 225, 226 and 227)

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

### Resume (Task 225)
- Reworked the same way as Pause: no `Back to actions` from task details; the submit button becomes the hint `Enter resume task` (tiny panes `Enter resume`), activated by Enter while the action is available and disabled when it is unavailable, pending or in flight.
- Resume is not destructive, so Enter runs it at once with no second step; Esc closes the popup.

### Close after queueing (Task 226)
- A Pause, Resume or Cancel popup from task details closes as soon as its command is queued or applied, through the Esc close path, so one Enter sends exactly one command (for Cancel, the second Enter). A rejected command keeps the popup open with its reason.
- The outcome stays visible on Home: the footer shows the latest command (`Pending: Pause T-1`) and the task detail refreshes. Ctrl+P popups and Message follow Task 227.

### Ctrl+P, Message and the confirmation mark (Task 227)
- A Pause, Resume or Cancel form opened from the Ctrl+P menu returns to the Actions menu once its command is queued or applied (the Back path), so repeated clicks or Enter cannot queue a duplicate; a rejected command keeps the form open with its reason. Task-detail popups still close (Task 226) and show no mark.
- A successful send shows a bold sage `✓` (glyph only, no text) for `dashboard.action_confirmation_seconds` (5 seconds). On Message it sits right after the hint line `Ctrl+S queue message · Enter new line`; on the Ctrl+P menu it sits next to the entry of the action just queued (Pause, Resume or Cancel). When the hint does not fit beside it (48 columns and narrower) the `✓` takes its own row under the hint for those seconds. A new send, opening another action, Back, or the timer clears it.
- Success text is no longer written to the result area: a queued or applied Message clears its text and focus returns to the empty box, and the Ctrl+P menu has no result line. Rejections and errors keep their text and reason in the result area, and a rejected send keeps the Message text.
- A command that is still unacknowledged after the ack wait keeps its `Pending` warning text (the engine running but not acknowledging is a warning, not a success), and its Message text stays in the box.
- An empty Message cannot be sent (Send and Ctrl+S are disabled), so a cleared box cannot queue an empty message.
- The hint keeps its colours in every state: keys bold cyan and labels grey, after a send, while the `✓` shows, after a rejected send, after focus changes, and after the armed Cancel hint resets. Disabled hints keep these colours too (the state shows through the disabled control and the Unavailable line).

## Help panel (Task 224)

The Help popup follows the common popup style and has one tab per context.

- Header as the other popups: `Help · <where it was opened>` (`Focus` on Home, `Task details · T-123` from a task) and the `Esc ×` close control; there is no bottom `Esc close` line.
- A tab row under the header: `Home  Task details  Resolution`. The active tab is cyan bold on the selected surface (no glyphs), the others grey. Left/Right (or a click) switch tabs and show the new tab from the top; the tab matching the opening context is preselected. Help cannot be opened from the Resolution popup, so that tab is a reference reached with Right. The Workspace window has only the `t` key, which is listed under Task details.
- The body is a scrolling list (focus on it; Up/Down, PgUp/PgDn, one-column scrollbar) of cyan sentence-case group titles with `key | action` rows: keys cyan bold, actions in the primary text colour, stacked below 32 columns.
- All rows come from one key table (`dashboard/help_keys.py`) built from the real bindings; the Home escalation and task-search rows appear only when their widgets are configured, and the tmux detach row only inside the project session.
- Removed: the billing mode and source paragraph (a configuration reference; Usage shows the unknown-billing counts but not the source, which is parked), the `History` arrows line, and the vague Workspace and mouse lines.
