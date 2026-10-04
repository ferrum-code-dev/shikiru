# Task 98 native Home review

The implementation source is [`bf70544b37fac849100efd9a462be0efb8764704`](../src/shikiru/plugins/widgets/queue.py) (2026-10-04). The current mixed captures were freshly exported from this revision with the real SQLite integration fixture. The immutable approved HTML reference SHA-256 is `542a644c1d0a9204b235f4c0c10385547c48db588de40c5357e4492dc2f3fa72`; the approved PNG SHA-256 is `3c91f20ee6e96bb2b0f36c8f6baa5b1565fb625eec269974578230e841f5ece3`. The comparison surface is the inner iframe `.shk-terminal`. Prototype controls, palette legend and the native exporter/Quick Look title bar are excluded.

The current mixed-data captures in [`build/review/task98`](../build/review/task98/) are native Textual `DashboardApp.export_screenshot()` renders from the real SQLite integration fixture:

- `mixed-home-100x35-color.svg` and `mixed-home-120x40-color.svg`: `NO_COLOR` unset, `TERM=xterm-256color`.
- `mixed-home-100x35-mono.svg` and `mixed-home-120x40-mono.svg`: `NO_COLOR=1`, `TERM=dumb`.

The fixture contains six active rows—Implementing, Verifying, waiting on a dependency, paused, operator-owned/held and queued—then a separate `Recent` label and one actual completed task. Usage comes from recorded SQLite rows: 2.7k input/output tokens, reported and unknown coverage, an API charge, unreported API costs, one subscription agent and an API-equivalent estimate. These values are fixture data, not production constants. The temporary project is a real Git repository. Host CPU and RAM use one actual `ResourceSampler` observation; no Shikiru process sample was supplied, so instance resources remain unavailable. The app state is injected as running; no engine or agent was started.

The approved 736px frame is approximately 725×689px. Its 12px monospace glyph advance is about 7.2px with a 19.8px line box, matching the 100×35 working grid. The 520px width breakpoint maps to roughly 68–72 terminal columns; Home keeps the split at 80 columns and stacks at 48, based on width only. Wide Home uses the 42/58 list/inspector split.

Native spacing follows the cumulative source-CSS-to-cell quantization in [parity §3](tui-parity.md#reference-css-spacing-baseline-and-native-cell-quantization): frame border and padding share a cell budget, footer and resource rules absorb adjacent margins, and the repeated 6px task-row padding alternates blank cells as accumulated spacing reaches a whole cell. No pixel-perfect claim is made. Measured native widget rows at rest are:

| Grid | Home readouts | Attention | Task list | Visible list row starts |
| --- | --- | --- | --- | --- |
| 100×35 | y=1, h=7; usage wraps to two rows | y=8, h=1 | y=11, h=20 | T-1 11, T-2 14, T-3 16, T-4 19, T-5 21, T-6 24, Recent 27, Done 29 |
| 120×40 | y=1, h=6; usage stays on one row | y=7, h=1 | y=10, h=26 | T-1 10, T-2 13, T-3 15, T-4 18, T-5 20, T-6 23, Recent 26, Done 28 |

Rows are normal-weight and unframed; the selected row uses the shared quiet `#233340` surface and steel outline. `Recent` is a disabled group label outside the task row, separated by the reference rule. The attention strip stays present and task-scoped: a hold on one task is not described as a global automation pause. It uses semantic color in color terminals and stays readable in monochrome. At 100×35, the usage details wrap naturally; at 120×40 they fit inline. Resource Host and Shikiru groups share the available line with a quiet separator. Long titles wrap at character boundaries.

The integration suite exercises healthy quiet, owned/held, stopped/faulted and actionable states at 100×35, 120×40, 80×24 and 48×20 using real SQLite state and Textual widgets. At 48×20 and 20×8, keyboard scrolling keeps the full selected identity and critical state reachable, and Enter/Ctrl+P preserve the existing detail and action routes. Searched All retains every matching completed or cancelled row across bounded pages; the Home-only one-row Recent projection applies only when All is unsearched. See [dashboard Home behavior](../tests/integration/test_dashboard_app.py) and [task-browser behavior](../tests/integration/test_task_browser.py).

Earlier status-only exports in the same ignored directory retain their actual source provenance: the healthy, owned-held, stopped-faulted and actionable 120×40 renders came from `f96e4c6a52673e459ee629392fc27392624b8cfe`; `healthy-48x20.svg`, `healthy-80x24.svg` and `owned-held-stopped-48x20.svg` came from `8978b4e5effa0a4842559bc6e461db651498e416`. These older captures are state examples, not the spacing or color proof for the current code. The last is monochrome. The current matched color/mono files above are the layout proof.

The full implementation-code gate before `bf70544` was committed reported **1,160 passed, 14 skipped, 90.06% coverage**. After the documentation and completion checklist updates, repeated runs of `PYTEST_XDIST_AUTO_NUM_WORKERS=4 UV_CACHE_DIR=/private/tmp/shikiru-phase9-uv-cache uv run poe check` each reported **1,160 passed, 14 skipped**; their xdist coverage reports were **90.07%** and **90.06%**. All runs used private temporary E2E directories and isolated tmux sockets; the coverage variation came from separate xdist runs, with no intervening code changes.

Task 98 leaves the shared inspector presentation and content for Task 99 and direct contextual attention routes for Task 100. The inspector in these captures still uses the earlier detail arrangement. Task 101 owns full responsive and compatibility parity measurement, and Task 102 owns the connected final review. Task 98 does not claim final Phase 10 or pixel-level parity.
