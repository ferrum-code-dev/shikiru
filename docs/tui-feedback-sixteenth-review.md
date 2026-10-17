# Phase 11 sixteenth review: help, escalations and views clean-up

Status: in progress (Tasks 228-231). Refines the [fifteenth review](tui-feedback-fifteenth-review.md) where it differs. **The TUI is the source of truth.** The operator approved the decisions below; do not redesign.

Execution order: Task 228, then 229, 230 and 231, one task at a time.

## Escalations screen (Task 228)
- Ctrl+S is the only send key on the answer box. Ctrl+J and Ctrl+Enter no longer send and do nothing in the box (Textual reports Ctrl+J as `ctrl+j`, which the text area does not insert). They leave the help, tests and current docs. Enter keeps its behaviour.
- The `Details` line shows a right-aligned `t show/hide` (key `t` bold cyan, label grey). It stays one row while folded at 100, 60, 48 and 40 columns and in the tiny layout. Click, Enter, Space and `t` keep toggling `What was tried`.

## Help panel (Task 229)
- Remove `Focus` from the help title: the Home help reads `Help` or `Help · Home` (the task picks one), a task reads `Help · Task details · T-n`.
- The Home help group `Decisions` becomes `Escalations`.
- The `v` row reads `Focus task details sections`.
- The `Space` and `click` rows are one row, `Space / click`.
- Task details wording says `action` instead of `verb` wherever the operator reads it (help rows, hints, docs); internal identifiers such as `TaskDetailVerb` and `task-detail-verbs` stay.
- The help tab `Resolution` is renamed `Escalations` (tab row `Home  Task details  Escalations`), with its group names.

## Task actions bar (Task 229)
- A right-aligned hint `v details` (key `v` bold cyan, label grey) sits on the task actions bar. It is not focusable; a click focuses the folds (`TaskDetailPanel.focus_folds()`). It stays outside the `#task-detail-verbs` container, and shows keys only or hides when the free width is too small. The actions-row width test at compact and tiny sizes still passes.

## Escalation bar (Task 230)
- Left and Right no longer switch between a task's escalations. The bar shows only the first open escalation of the followed task, because an escalation belongs to its task and switches with it. The engine's capacity for more than one open escalation per task is verified and reported; no switcher is built.
- The bar keeps Enter (resolve or answer), click and Ctrl+P. The `r` key goes (it duplicates Enter), and the `o` key leaves the bar.

## Extra details on the Escalations screen (Task 230)
- The Decision evidence panel (`PacketScreen`) stays. It opens only from the Escalations screen with `o`, and only while its Details section is unfolded. A hint line `o extra details` (key `o` bold cyan, label grey) sits below the short details; it is hidden when Details folds, and `o` is inert while folded.
- It is one scrolling page in the popup frame, titled `Extra details · T-n` with the `Esc ×` close control. Esc closes it and returns to the Escalations screen.
- Section titles are cyan, sentence case: `Question`, `Timeline`, `Reports`, `Facts`, `Recent signals`, `Sessions`. Empty sections are omitted. Time and label columns are grey (`labelled_rows`), values are white, the scrollbar is one column, and the text wraps at 100, 60, 48 and 40 columns.
- An unreadable packet (`evidence_error`) shows as one amber line, followed by the original packet text as today.
- Fault evidence (`_open_fault_evidence`) keeps working, adapted to the new screen if needed.

## Views panel and Home `v` (Task 231)
- The Views panel goes from every place: `_ContextViewsScreen`, `action_open_views`, the palette `Views and extensions` route, `TaskPaletteRoute.VIEWS`, help rows, docs and the HTML reference. On Home, `v` is a silent no-op.
- `v` stays inside task details (focus folds). The contextual view plumbing stays: `_open_contextual_view`, `_run_context_choice`, `_ContextChoice` and `WidgetContext.open_contextual_view`, which Usage, Resources, Faults and the status bar call.
- Tests open Usage, Resources, Escalations and Help by clicking or pressing Enter on the real widgets and footer buttons; the `choose_focus_view` helper is rewritten.

## Workflow strips and Activity dialogue (Task 231)
- The Workflow strips and the Activity dialogue lose their Home entry point only. Their code, config and tests stay, except a test that reached them only through the Views panel, which is adapted or deleted. The widget plugin contract and the placement enum stay.
- Extension widgets (placement `EXTENSIONS` and external plugin widgets) become entries in Ctrl+P under one `Extensions` section, and each opens as before.

## Tests
- The collected cases (1654) must not grow. Tests of removed features are deleted, and new behaviour extends existing tables.

### Task 230 outcome
- The store has no uniqueness rule, so a task can hold several open escalations (`EscalationStore.open` only inserts; `ensure_actionable` tolerates several; `settle_open` resolves all of a task's open escalations). The engine raises one per escalation and a retry or cancel settles them together. The bar shows the first open one; the next appears once it is resolved.
- `PacketScreen` and its widget copy are gone. `dashboard/extra_details.py` holds `ExtraDetailsScreen`, opened by `ResolutionScreen`. Fault evidence never used the packet screen (it opens `FOCUS_FAULTS_VIEW` through `open_contextual_view`); the dead fallback that pushed the packet screen is removed.
- A packet without text or a task without a Details section shows no hint and `o` is inert.

## Operator findings after Tasks 229-231 (Task 232)
- The Help title is the constant `Help`; the heading is no longer part of `HelpContext`.
- A hint `← → switch section` (arrows bold steel blue, label grey, keys only in the tiny pane) sits on its own row directly under the Help tab row, in the gap the tab row's margin used.
- The `Extensions` group in Ctrl+P is a top-level group in the project and the task menu (above More actions) whenever at least one extension entry exists, and hidden when there is none. The default config has no EXTENSIONS widget, so the group is absent by default.
- Limit: the standalone `shikiru palette` popup (`run_palette`) receives no extension entries. Opening an extension widget needs the mounted dashboard (the palette only returns the entry to `DashboardApp`, and the standalone app has no such callback), so listing entries there would offer controls that cannot act; Home uses the in-dashboard palette.

## Extra details panel rework (Task 233)
- Timeline rows are coloured by event tone.
- Reports and Recent signals are selectable tables, each with a shaded structured details block under the selection; the selection is a background only, without a left bar.
- Facts is removed from the page.
- Sessions are formatted without commands.
- The collected cases do not grow.

## Full session id (Task 234)
- Sessions in Extra details print the full session id; it wraps onto a second line when it does not fit and is never cut.
