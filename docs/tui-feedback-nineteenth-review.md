# Operator palette usability follow-up

Status: in progress (Tasks 247–248). Refines the eighteenth review and design 06 where it differs. The working TUI is the priority; the HTML is an illustrative reference and exact parity is not required.

Execution order: Task 247, then Task 248, with independent review and required checks before each commit.

## Operator requests

- Align every dropdown with its label, keep its selected item visible, and preselect a meaningful default instead of an ambiguous `Select an item` entry. The displayed default must match what the action actually uses; preserve automatic/current/any semantics where appropriate.
- Remove the misleading role from the Actions title; keep task identity and title when a task is selected.
- Make Submit task and Export evidence use the common action-popup surface treatment for their frame, body and fields, keeping their contents and edit/focus behavior readable.
- Move Project diagnostics from Views to Project and name it Diagnostics. Move Commands from Views to Project and name it Command history. Remove the Views section. Keep task-scoped Evidence & reports and Usage available through the existing task detail folds.
- Remove Open session from the Task action list; do not remove retained session evidence or unrelated public contracts.
- Offer Stop engine only while the engine is running and Start engine while it is stopped; switch back after a successful start, and never queue a stop for an already stopped engine.

## Implementation boundaries

Reuse the existing popup and select components, semantic styles, operator services, engine lock liveness and ProjectLifecycle. Preserve command semantics, originating task/role context, keyboard editing, dropdown dismissal, and narrow-pane scrolling. Start must reuse project configuration/overrides, preserve the user's session and attached client, and report failure in the popup. Do not invent a queued start command that requires a stopped engine to consume it.

No exact HTML parity work is required. Update current operator/help documentation where the user-facing behavior changes. Tests cover actual rendered control usability and command/lifecycle outcomes; extend existing behavior tables and avoid shape, wording, framework and style inventories. Use isolated fixture tmux servers and clean owned resources only.
