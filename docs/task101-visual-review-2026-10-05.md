# Task 101 native parity review

Implementation source: `116c571daee451bee57bd576ff1552d55036d5c0`. The immutable approved reference remains [Focus Steel HTML](design/references/focus-steel-approved.html) (`542a644c1d0a9204b235f4c0c10385547c48db588de40c5357e4492dc2f3fa72`) and [736-pixel view](design/references/focus-steel-approved-736.png) (`3c91f20ee6e96bb2b0f36c8f6baa5b1565fb625eec269974578230e841f5ece3`). The original Task 97 audit baseline was `c4b8a069771d9804c9afd7640aadae09db0b6c17`; this review records the measured implementation at the source revision above.

The image is RGB8 at 736×1100. The product frame runs x=5..730 and y=190..878 inclusive: 726×689 px. Its inner content runs x=22..713: 692 px, or about 96.1 nominal text advances. The approved body uses 12 px `ui-monospace, SFMono-Regular, Menlo, Consolas, Liberation Mono, monospace`; its nominal advance is 7.2 px and its line pitch is 19.8 px (`12 × 1.65`). The 726 px frame therefore maps to 100.8 columns and the 689 px height to 34.8 rows: the measured native wide grid is 101×35. Brand tracking is a visual treatment and is excluded from body-cell measurement.

The CSS 520 px stack breakpoint maps to 72.2 columns, so 73 columns split and 72 stack. Explicit compact width 380 px maps to 52.8 columns, so 54–72 columns retain the stacked inspector and 53 or fewer keep the task list primary. These are width-only rules. Boundary behavior at 73/72 and 54/53 is covered; 80×14 stays split and scrolls vertically.

The Task 99 fixture supplies the matched Home population: six Active records and one Recent Done record. This is screenshot evidence, not a production row cap. Task, usage, stage, signal and role/session values are seeded in a temporary real SQLite database and minimal project. The displayed `sonnet` and `gpt-6-luna` session facts are fixture records, not agent runs or configuration defaults. The Home fixture takes Host RAM/CPU from the existing `ResourceSampler`; it supplies no engine PID or role panes, so the sampled Shikiru process tree is empty. `StubServices(engine_running=False)` deliberately leaves the engine disconnected: the UI says `Engine stopped` and `Uptime unknown`. No connected fake-agent or verified uptime claim is made here. The Answer form uses a separate seeded packet containing its recorded question; the missing-packet capture keeps the actual evidence error visible.

## Measured mapping and corrections

| Reference measurement | Native observation and correction |
|---|---|
| Content origin x=22; list/inspector rule x=312; inspector body begins x=329 | On 101×35, Textual content starts at cell 2, the rule is at cell 42, and inspector text starts at cell 46. The native 42/58 columns absorb the source 13 px list padding, 1 px rule and 16 px inspector padding without overlapping text or the frame. |
| Resource group 2 begins x=375, with equal `1fr` groups and a 14 px gap | The source start is 49.0 advances after the content origin. Native group 2 starts at cell 51 on 101 columns and cell 61 on 120 columns; both groups are equal and remain aligned after resize without resampling. |
| Source line pitch 19.8 px; body text advance 7.2 px | Textual SVG exports use a 12.2 px horizontal advance and 24.4 px line pitch in their Rich/Fira Code drawing surface. The exports include Rich terminal chrome, so comparison uses terminal cells and text-node coordinates, not rendered screenshot pixel size or viewer chrome. |
| CSS breakpoints 520 px / 380 px | Native thresholds are split at 73 columns, stacked inspector at 54–72, and list-only at 53 or below. Resize tests preserve selected identity, search, focus, scroll position and form state; short-wide 80×14 does not switch to list-only. |
| Selected fill and focused edge in the approved PNG | The fill is `#233340`. Independent RGB8 decoding confirms the focused edge is `#99c8ff`: 272/272 pixels at y=470, 271/272 at y=469 and y=521, plus the vertical sample at (22,490). The fill samples at (23,490) and (40,510) are `#233340`. The implementation now uses a dedicated selection-edge token; general steel text/key accent remains `#83bddd`. |
| Modal source width is 100%, max 460 px; compact controls wrap and scroll | 460/7.2 maps to 63.9 columns, so the palette is bounded at 64 columns with content-height scrolling. Wide form actions stay on one row; at 36 columns they wrap as complete captions, and at 20 columns concise captions keep both actions visible. Form submits are action-specific and menu labels are left-aligned with non-reflowing focus. |
| Footer hints remain mouse-active without a permanent button bank | At 101×35 and 120×40 all four captions remain complete. At 48×20 hints wrap to two rows; at 36×20 the `Ctrl+P actions` hint wraps across two rows without clipping. Mouse footer routes remain reachable while search has focus. |
| The inspector ends with a quiet separator and `e Evidence` | The 101×35 `dash --task` capture shows a full-width one-cell rule before `e Evidence`, after the compact usage and Current/Used rows. The 48×20 and 36×14 Evidence routes retain access through the same shared detail view. |

Vertical measurements are CSS line-box starts relative to the frame, not glyph baselines. Each visible 1 px rule maps to one native cell. For spacing, absolute cumulative boundaries use `floor(boundary_px / 19.8 + 0.5)`; the gap between consecutive rounded boundaries is applied to each item. This keeps fractional remainders across rows rather than resetting at the first task. The source task item begins at 227.8 px including its 6 px top padding; its title line box begins at 233.8 px. A two-line task item occupies 51.6 px (`2 × 19.8 + 12` padding).

| Source line-box start from frame | Quantized row/boundary |
|---|---:|
| Header 15; operations 52.8; usage 84.6 | 1; 3; 4 |
| Resource rule 114.4; resource text 122.4 | 6; 6 |
| Attention 157.2; `TASKS` 200 | 8; 10 |
| First task title 233.8; state 253.6 | 12; 13 |
| Successive task titles 285.4, 337, 388.6, 440.2, 491.8 | 14, 17, 20, 22, 25 |
| Recent rule 549.4; label 562.4; Done title 588.2; state 608 | 28; 28; 30; 31 |
| Footer rule 648.8; text 658.8; frame end 688.6 | 33; 33; 35 |

The quantized source rows above are frame-relative. The next table records the actual 101×35 selected Home export by Rich terminal row, computed from its SVG text-node y coordinates as `(y − 20) / 24.4`; rules use their visible glyph row. This keeps native line placement distinct from CSS targets and browser glyph baselines.

| Content | Source target row | Native row | Native minus target |
|---|---:|---:|---:|
| Header / operations / usage | 1 / 3 / 4 | 1 / 3 / 4 | 0 / 0 / 0 |
| Resource rule / text | 6 / 6 | 5 / 6 | −1 / 0 |
| Attention / stopped-state second line | 8 / — | 8 / 9 | 0 / +1 line |
| `TASKS` | 10 | 11 | +1 |
| Six Active task-title rows, in native display order | 12, 14, 17, 20, 22, 25 | 13, 16, 19, 21, 23, 25 | +1, +2, +2, +1, +1, 0 |
| Recent rule / label / Done title / state | 28 / 28 / 30 / 31 | 27 / 28 / 29 / 30 | −1 / 0 / −1 / −1 |
| Footer rule / text / bottom frame | 33 / 33 / 35 | 32 / 33 / 34 | −1 / 0 / −1 |

The `Engine stopped` status occupies a second attention line in this disconnected fixture, moving `TASKS` and the first task below the one-line source target. Native selected-row border and padding absorption plus wrapped task/state lines account for the intermediate title-row spacing; cumulative gaps then reconverge at the sixth Active title. The Recent and footer text remain within one cell of their source targets. At 120×40, the same text order and line-wrap-free titles remain, while the bottom-anchored footer moves to row 38 and the frame ends at row 39; this is the taller terminal grid, not a different spacing target.

The measured Home now has the four-row identity/operations/usage/resource stack, equal midpoint resource groups, one attention widget, decision-first unframed rows, the 42/58 split and Recent group. The detail order is identity/state, recorded milestones, acting role/stage/elapsed, situation, latest retained signal, separately recorded signal age, task usage, Current/Used role rows, then Evidence. Subscription rows use the declared agent count and preserve recorded charge, estimate and unknown as separate facts.

The primary task menu follows the approved order: Answer decision when applicable; Inspect task; Evidence & reports; Message a role; Take over/Hand back a role when applicable; Usage; Commands; Project diagnostics. Entries are quiet, left-aligned labels with a secondary meaning. Task/project mutations and secondary views/extensions remain available through More actions. Direct Answer shows the selected decision identity and recorded question without a redundant target selector; generic `resolve` retains its multi-decision picker. A same-task E1 draft cannot replace a new E2 target or leak its guidance, while the same-decision draft still resumes. Opening menus/forms remains read-only; only explicit submit queues a command. Attention uses direct Answer and keeps stopped/held/ownership facts in the single attention widget without claiming automation continues.

## Native evidence and compatibility

Captures use the existing `App.export_screenshot()` and temporary SQLite/Textual support. No renderer, screenshot dependency, harness or golden catalogue was added. True-color exports were captured with `TERM=xterm-256color`, `NO_COLOR` unset and `FORCE_COLOR` unset. Monochrome exports use `NO_COLOR=1` with `FORCE_COLOR` unset. Rich SVG text nodes and cell coordinates were inspected where the QuickLook thumbnail did not paint a body; no product behavior or export path was changed for that viewer limitation.

The color capture directory is `/private/tmp/shikiru-phase10-task101-116c571-color`; the monochrome directory is `/private/tmp/shikiru-phase10-task101-116c571-mono`. They contain matched 101×35, 120×40, 80×24, 64×24 and 80×14 Home captures; 48×20, 36×20, 36×14 and 20×8 Home/detail/Usage/Evidence routes; wide and compact contextual menus/forms; healthy recorded-packet, missing-packet/error and stale-resolution Answer states; and a separate long-title wrapping proof. Representative names are `home-selected-101x35-color.svg`, `home-selected-120x40-color.svg`, `home-selected-64x24-color.svg`, `task101-home-short-wide-80x14-mono.svg`, `home-enter-detail-36x14-color.svg`, `dash-task-20x8-color.svg`, `task-usage-36x14-color.svg`, `task-evidence-36x14-color.svg`, `task101-context-menu-101x35-color.svg`, `task101-answer-healthy-recorded-packet-120x32-color.svg`, `task101-answer-missing-packet-100x35-color.svg`, `task101-answer-stale-resolution-120x32-color.svg` and `home-long-title-wrap-101x35-color.svg`.

`PYTEST_XDIST_AUTO_NUM_WORKERS=4 UV_CACHE_DIR=/private/tmp/shikiru-phase9-uv-cache uv run poe check` passed at this source: 1,215 passed, 14 skipped, 90.40% coverage. A targeted `TERM=dumb NO_COLOR=1` compatibility set passed 43 checks across CLI outputs, tmux layout/session and palette bindings, built-in widgets, external/configured views, disabled motion and navigation. Fresh native capture runs passed 10 selected integration cases each in true-color and `NO_COLOR=1` mode, including direct Answer, stale decision, compact routes and read-only mouse navigation. Focused direct-answer/draft and compact routes also pass. Phase 9 CLI, role-pane and plugin contracts remain intact.

This review ran on macOS 26.3.1, Apple Silicon arm64, Python 3.14.7, uv 0.12.5 and tmux 3.7c. GNU/Linux, CJK/ambiguous-width behavior and widths below 20 columns remain unverified. Task 102 owns the final full `TERM=dumb` gate, connected fake-agent walkthrough, actual macOS clean-install smoke and final owned-resource review.
