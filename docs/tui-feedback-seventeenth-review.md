# Phase 11 seventeenth review: task browser clean-up, Load more hint and Help sizing

Status: in progress (Tasks 236-238). Refines the [sixteenth review](tui-feedback-sixteenth-review.md) where it differs. **The TUI is the source of truth.** The operator approved the decisions below; do not redesign.

Execution order: Task 236, then 237, then 238, one task at a time.

## Task browser clean-up (Task 236)
- The older, non-Home task-browser layout is deleted entirely: the preview block, the `Reveal selected task` button, the Clear button, the search row, the filter buttons, the old count text and the non-Home Enter path. Home is the only layout and stays exactly as it is: `f` shows statuses that have no tasks and the Home header shows no count.
- The `Family context` text is deleted together with the data path that only fed it.
- Fields that only the removed layout read go with it.
- The collected cases (1646) must not grow; tests of removed controls are deleted and worthwhile behaviour is ported to the Home placement.

## Load more hint (Task 237)
- The `Load more` button becomes a hint in the common style: `m` bold cyan and ` load more` grey. `m` loads the next page and so does a click on the hint. It shows only while more tasks exist and is not a Tab stop.
- `m` is routed like `f` and `/`: Home only, not in text inputs, modals, expanded Selects or the Agent activity fold. The Home Help tab gets an `m` row.

## Help panel sizing (Task 238)
- One blank line follows the `← → switch section` hint row.
- The panel keeps one size when switching sections: the size of the tallest (and widest) section measured at the current width, capped by the viewport; the scrollbar (one column) appears only when that does not fit; the size is re-measured on resize; shorter sections leave empty space below.
