# Task 95 rendered visual review

The review used the approved Focus reference at [focus-steel-approved-736.png](design/references/focus-steel-approved-736.png). The files below are native SVG output from a real SQLite-backed Textual `DashboardApp` through `App.export_screenshot()`, captured from the Task 95 working tree based on `0d04f61`. Color review ran with `NO_COLOR` unset and `TERM=xterm-256color`; macOS Quick Look made disposable PNG previews in `/private/tmp` for manual inspection. No rendering dependency or screenshot harness was added to the product.

| State | Terminal | Native artifact | Size |
|---|---:|---|---:|
| Wide Home with decision, usage and resources | 120×40 | [wide-home-120x40.svg](../build/review/task95/wide-home-120x40.svg) | 78,146 B |
| Compact Home list | 48×20 | [compact-home-48x20.svg](../build/review/task95/compact-home-48x20.svg) | 25,279 B |
| Short Home | 36×14 | [short-home-36x14.svg](../build/review/task95/short-home-36x14.svg) | 17,299 B |
| Very small Home | 20×8 | [very-small-home-20x8.svg](../build/review/task95/very-small-home-20x8.svg) | 8,771 B |
| Task decision and holds | 36×20 | [task-detail-36x20.svg](../build/review/task95/task-detail-36x20.svg) | 20,709 B |
| Task usage with API and subscription sessions | 36×20 | [task-usage-subscription-36x20.svg](../build/review/task95/task-usage-subscription-36x20.svg) | 21,218 B |
| Task view selector | 36×20 | [task-views-36x20.svg](../build/review/task95/task-views-36x20.svg) | 18,586 B |
| Contextual action form | 36×20 | [task-actions-36x20.svg](../build/review/task95/task-actions-36x20.svg) | 32,370 B |
| Contextual action form | 20×8 | [task-actions-20x8.svg](../build/review/task95/task-actions-20x8.svg) | 11,369 B |
| Stale resource sample with error and age | 120×40 | [stale-resource-error-120x40.svg](../build/review/task95/stale-resource-error-120x40.svg) | 67,162 B |

The first wide render exposed an empty gap between the list and inspector and two competing vertical separators. The list column now has an explicit width and the split uses one separator. Long task rows keep their ID and state on concise lines with ellipses; nonzero dispatch priority stays in the identity line, while the selected task detail retains the complete title and all holds. A final populated render shows the decision and pause hold in the list and full task context in the inspector. List reordering restores the same visible task anchor while keeping the selected identity in the inspector. Review of the compact selector showed that a one-row button height hid its label because Textual adds line padding; the Open control now uses its content height and is visible at 36×20. The normal palette colors remain legible, with decisions/holds in amber, active/focus cues in steel blue and billed usage in sage. Resource failure text and observed age remain visible without replacing the last sample.

At 36×14 and 20×8, only a portion of the Home readouts and task list fits at once; the selected row still identifies the task and its state, while Enter opens the full title and scrollable task detail. Keyboard navigation and scrolling keep usage Details, actions and form controls reachable; longer form entry is more comfortable with additional rows. Textual can expose a fractional `scroll_y` during interpolation while the intended `scroll_target_y` is unchanged, so the Evidence resize integration checks the retained target, focus/input value and visible filter control instead of rounding an intermediate frame.
