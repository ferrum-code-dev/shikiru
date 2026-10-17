# Phase 11 eighteenth review: palette redesign and Take over in the bar

Status: in progress (Tasks 241-246). Refines the [seventeenth review](tui-feedback-seventeenth-review.md) where it differs. **The TUI is the source of truth.** The operator approved the decisions below; do not redesign.

Execution order: Task 241, then 242, 243, 244, 245, 246, one task at a time.

## Operator request
"Re-design and re-work the palette so it follows the current design choices and be more Operator friendly. Moreover, add a 'Take over a role' action to the task actions bar (think more concise name)." Also: add a note about tmux `<prefix> Space` to the Help ("this is a cool feature I didn't know about"). It is fine that the standalone tmux palette has no Extensions group.

## Approved decisions
1. **One grouped action list** for the task (direction A). Each row opens the same small popup as the matching actions-bar button. The legacy form (Find action, Action list, Task match/ID fields, Advanced toggle) goes away. Selection is background-only (like the Tasks list), no reverse-video buttons.
2. **Task choosing**: a `Switch task…` row opens a Task picker popup styled like the Tasks list (one-line rows, `/` search, Enter chooses). It replaces the find box, the match list and the free-text Task ID.
3. **Wording**: one short name per action everywhere (bar, list, popup title, hint): Message, Pause, Pause now, Resume, Cancel, Take over, Hand back, Retry, Priority, Rung, Open session (was "Peek session"), Export evidence, Submit task, Stop engine. The documented term "rung" stays; its popup gets a one-line explanation of the agent:model:effort level.
4. **Take over in the bar** whenever it can work (a non-terminal task whose acting role has a live session, so the bar and the palette agree). Hand back is unchanged and is shown instead when the operator owns a role. On escalated tasks Answer stays first and Cancel last within the existing four-slot cap.

## Approved mockup (option A)
Esc `×` closes; selection is background only; rows are hidden when unavailable, except Resume with holds as today.
```
Actions · T-12 Fix login · Leader        Esc ×
 Task
  Answer        Answer the recorded decision
  Message       Delivered on the next turn
  Take over     Stop the turn, work in the pane
  Pause         Stop after the current turn
  Cancel        Abandon this task
  More…         Pause now · Priority · Retry · Rung · Open session · Export evidence   (expands in place)
 Views          (dashboard only; hidden in the standalone tmux popup)
  Evidence & reports · Usage · Commands · Project diagnostics   (one row each)
 Project
  Submit task · Stop engine · Switch task…                      (one row each)
 ↑↓ select   Enter open
```

## Architect clarifications
- The `·`-joined lines of the sketch are one row each in the real list (one selectable row per item).
- "Resolve escalation" is the same as Answer and disappears from the list.
- With no task in context the list shows only Project (Submit task, Stop engine, Switch task…) plus Views where available.
- Popups opened from the list return to the list when closed by Esc or `×` (the Ctrl+P menu return of Task 225) and close after queueing like Pause/Resume/Cancel (Tasks 226/227, ✓ for 5 s). Popups opened from the actions bar keep today's behaviour (close, no list).
- All popups follow the common style: `ui/popup.py` PopupFrame, `Esc ×`, `key_hint` hints that go grey when the key is inert (Task 239), `.popup-edit` for edit areas, labelled rows, background-only selection, one-column scrollbar, wraps at 100/60/48/40 columns.

## Tasks
- **Task 241, Take over in the bar and one name per action**: `briefing_actions` offers Take over whenever the palette's availability for the acting role allows it (a pure `can_take_over` input fed from `task_action_availability`); every action has one short name in the bar, list, popup titles and hints; Help gets a `<prefix> Space` row; docs and the HTML reference match.
- **Task 242, Task picker popup**: new `dashboard/task_picker.py` (PopupFrame; `/` search under the title, Esc clears then closes; one-line rows with the shared status markers; background-only selection; Up/Down/Home/End/PgUp/PgDn; Enter chooses and dismisses with the task id; bounded pages via `search_task_targets` with the `m load more` hint). Not wired into the palette yet.
- **Task 243, Actions list**: the option-A list replaces the contextual menu, the More-actions toggle and the legacy no-context form entry of `PaletteScreen`, the Ctrl+P / `<prefix> Space` routes (`_open_task_palette_route`, `_menu_available`, `_preset_popup`); rows open the existing preset popups through `initial_action`; `Switch task…` opens the picker and re-targets the list; `PaletteApp` uses the same list without Views/Extensions; header `Actions · T-n title · Role`.
- **Task 244, Simple action popups**: Take over, Hand back, Retry, Pause now, Priority, Rung, Open session and Stop engine in the common style: title, one guidance line, only their own fields (Role select where needed, Priority number, Rung with its one-line explanation), hint line `Enter take over` etc. (grey key while inert), no "Run action" or "Back to actions" button, close after queueing with ✓ 5 s; the role pane selection after Take over stays.
- **Task 245, Submit task and Export evidence popups** in the common style. The architect proposes mockups and gets operator approval before implementation.
- **Task 246, Delete the legacy form** (Find action, Action select, Task match select, More matches, free-text Task ID, the old Advanced toggle, Back to actions) and every test, doc and code path only they used; the HTML reference palette sections are redrawn to the new list.

## Acceptance
The collected cases (1644) must not grow; both gates (`uv run poe check`, `TERM=dumb NO_COLOR=1 uv run poe check`) pass before every commit; the HTML reference and design docs change in the task that changes the behaviour they draw (exact srcdoc round trip and a new SHA-256 when the HTML changes).
