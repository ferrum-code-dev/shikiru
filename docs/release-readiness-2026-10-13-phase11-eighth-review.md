# Phase 11 eighth-review verification

Status: Task 165 verified Tasks 156–164 on 2026-10-13 against the final source, found and fixed one defect of the same kind as finding 8, and Task 166 revised the HTML reference and the design documents and marked the spec delivered. The spec is [`tui-feedback-eighth-review.md`](tui-feedback-eighth-review.md); the preceding record is the [seventh review](release-readiness-2026-10-12-phase11-seventh-review.md). GNU/Linux remains unverified; everything ran on macOS (Darwin 25.3.0, Python 3.14.7, Textual 6.3.0).

Source: `342615c`, `63f3d79`..`d1960f4`, `5f0d78c`..`db0dc80`/`92e45cc`, `b3f12f2`..`ebc70c4` (test hygiene, Tasks 156–159), `d50b5bc`, `6c37d45` (Task 160), `bacd9a2` (161), `3fc5041` (162), `a65e805`, `0757238` (163), `aaad55e`, `8ae346d` (164), then Task 165's `883f297` (the task detail follows under a popup) and Task 166's `062eae1` (HTML reference) and `3dcea3b` (design documents), with this record, the README and spec links and the plan and todo ticks in the last commit.

## Issue-by-issue result

| Finding | Commits | Result | How verified |
|---|---|---|---|
| 1 no blank line and no rule above `Resources` | `3fc5041` | pass | Native popup before and after; the standalone Docker and Disk popups too. |
| 2 `observed`/`stale` flicker in Resources | `d50b5bc` | pass | 13 s of real timers, polled every 0.1 s, before and after. |
| 3 Docker and Disk `observed now` | `d50b5bc` | pass | Fresh headings bare; `stale · observed …` only after a real Docker failure and an unreadable `.shikiru` subtree. |
| 4 Disk `Runs` unclear | `a65e805`, `0757238` | pass | `.shikiru measuring…`, then the size, on the real popup. |
| 5 sentence-case titles | `aaad55e`, `8ae346d` | pass with limit (g) | Views menu, help, command history, task actions menu and popup titles driven in the real app. |
| 6 popup refreshes with the bar | `bacd9a2` | pass | Change instants of the Home bar and the popup recorded over 13 s, before and after. |
| 7 low free space marker | `3fc5041` | pass with limit (a) | Warn and alert in colour and with `NO_COLOR=1 TERM=dumb`, plus narrow widths. |
| 8 header Uptime under the popup | `bacd9a2` | pass | Uptime read every 0.1 s for 6 s with the popup open, before and after. |
| Task detail follow under a popup (found during 165) | `883f297` | fixed | See finding 9 below. |

## Native renders

A throwaway harness built the real `DashboardApp` with the same wiring as `bootstrap.run_dashboard` (real `DashboardServices`, `ResourceSampler`, `DockerSampler`, `DiskSampler`, the real `SystemClock` and real timers) over two throwaway git projects (one with a task and two open faults, one empty). A fake `docker` script on `PATH` returned ten containers (eight in the project, one unhealthy, one exited with status 1, two elsewhere) and a failing mode for the stale check; the host Docker daemon was never asked. `TMUX_TMPDIR` pointed at an empty scratch directory. The popup opened with Enter on the Resources bar. The baseline was a `git worktree` of `0d55954` (before Task 160) put first on `PYTHONPATH`, so the same harness drove both trees. The monochrome runs set `NO_COLOR=1 TERM=dumb`.

1. **Layout.** Before: a blank line and a double rule between the popup header and `Resources`. After: the header rule is the only one at 100×50, 52×70 and 47×70; later sections keep theirs; the standalone Docker, Disk and Faults popups start with their heading directly under the header rule.
2. **Flicker.** Before: `Resources observed now` and `Resources stale · observed now` alternated every two seconds (54 of 126 polls were stale). After: 127 polls, none showed `observed` or `stale`.
3. **Docker and Disk hints.** Before: both headings read `observed now`. After: bare headings; with the fake `docker` failing and a `chmod 000` directory under `.shikiru`, both read `stale · observed now` (the age form is `now`, `2m`, `1h`), and both returned to bare headings after recovery.
4. **Disk row.** Before: `Runs 0 B`. After: `.shikiru measuring…` for 1.9 s (one resources-refresh tick), then `.shikiru 140.3 KiB`; `Free` and `.shikiru` values start in the same column.
5. **Titles.** Views lists `Task filter · active`, `Command history · pending, applied and rejected`, `Activity · dialogue`, `Diagnostics · docker`, `Diagnostics · disk`, `Diagnostics · faults` (Title Case before); help carries `History: p previous · n next`; command history shows `p previous  n next`; the task actions menu entries (`Evidence & reports`, `Message a role`, `Take over a role`, `Fewer actions`, `Prioritize task`, `Export task evidence`) and the popup titles (`Project diagnostics`, `Help · Focus`, `Views`) read in sentence case.
6. **Refresh moment.** Instants at which the bar and the popup text changed. Before: bar 0.13, 4.13, 8.12, 12.13 s; popup 1.29, 3.28, 5.28, 7.28, 9.27, 11.27 s (its own timer). After: both at 0.15, 4.15, 8.14 and 12.14 s, within the 20 ms poll.
7. **Marker.** At 48 % free there is no marker. With the warn level raised the row ends ` ‼` in bold amber `#dfb16c` with the free figure amber; with the alert level raised both are dusty rose `#e3a0a0`. Under `NO_COLOR=1 TERM=dumb` the marker stays bold and shifts to the luminance greys `#b6b6b6` (warn) and `#aeaeae` (alert). At 30 columns the marker wraps with the unit (`GiB ‼`), at 20 the same, at 14 it wraps alone.
8. **Header Uptime.** With an engine-started event for the harness process, Uptime under the open popup read `1s` and stopped (before) and read `1s` to `7s` over six seconds (after).
9. **Task detail follow (not in the spec).** With the evidence view open on a task and a new engine event written, the new entry showed within 4 s. With `?` open over the detail, the entry did not show within 4 s and appeared only after the popup closed: the task detail's own gate (`widget_is_active`, top of the stack) froze its timeline follow and activity ages while the detail stayed visible beneath. It is the same defect as finding 8, so `883f297` switched it to `widget_is_current`, the gate of every Home widget and the header, and removed the unused `widget_is_active`. After: the entry shows under the popup within 4 s. Two tests that pinned the old behaviour changed with it: the covered-screen facts test now asserts the facts refresh under the covering screen, and the Home inspector no longer asserts it stays stale while a task detail covers it (its remaining checks and the in-flight discard test stand). The existing activity-ages test gained an `under-popup` case.

## HTML reference

`docs/design/references/focus-steel-approved.html` SHA-256 `05c1a3ddcbf4faf106b114052e07e160b462dd7f9d5e6b9121f1f8017ca9f7a8`. It is an iframe whose `srcdoc` holds the app, entity-encoded; Task 166 decoded it, edited the app and re-encoded it with an exact round-trip check, and the bytes outside the `srcdoc` are unchanged. The popup now draws no rule above the first section (in the composite popup and in each standalone popup), no age hints, the Disk size row `.shikiru` with a nine-column label, and a bold ` ‼` marker after a low free value (the decision scene shows the warn level, the takeover scene the alert level). Views, headings, buttons and hints follow sentence case with lower case after ` · ` and after keys (`u details`, `e evidence`, `p previous`, `n next`, `Diagnostics · docker`, `Command history · pending, applied and rejected`, `All statuses`, `Ctrl+P actions · v views`), keeping `SHIKIRU` and `TASKS`.

The Chrome check ran the decoded app plus a measuring script in headless Google Chrome under a throwaway profile (deleted; only my own Chrome processes ran, the operator's Chrome was never touched). For the stopped, decision and takeover scenes it read every visual line of the popup and of the standalone Docker, Disk and Faults popups per character (bold and colour mapped to the palette roles) and compared with the native dumps of the same sections, normalising numbers and size units and dropping the middle-dot separators the CSS draws:

- Stopped and decision scenes, wide (58 columns) and compact (37 columns, a 47×70 terminal), colour and `NO_COLOR=1 TERM=dumb`: 368 lines compared, every line matches except the Agents row and the Status row of the decision scene. The sample shows active agents and an engine up for `3h 12m`; the native fixture had no agents and an engine up for seconds.
- The takeover scene's alert-level Disk popup matches the native alert render in colour and monochrome.
- The wrap points, bold rows, colours and the marker match; the decision scene's warn marker is bold amber.

The wide screenshot was read: Resources sits directly under the header rule and the later sections keep their rules.

## Design and operator documents

Design 06 (commit `3dcea3b`) states that a sample is stale only after a sampling failure and an age shows only then, that the popup has no leading rule, that the task detail follows under a popup like the Home widgets and the header, and that the popup, its Views entries and the Totals name use sentence case; designs 02, the Focus specification and the parity table follow. Design 05 already carried the Disk cadences and thresholds from Task 163, and design 06 §6.1 already carried the title rule and the marker from Tasks 162 and 164. The design README marks the eighth review delivered.

## Test hygiene numbers

| Measure | At `0d55954` (start of Task 156: 1,665 cases) | After Task 164 | After Task 165 |
|---|---|---|---|
| Collected cases | 1,665 | 1,604 | 1,605 |
| Test functions (`tests/`) | 994 | not measured | 950 |
| Lines in `tests/` | 62,017 | not measured | 60,423 |
| Lines in `tests/integration` | 49,551 | not measured | 47,849 |
| Lines in `tests/unit` | 4,360 | not measured | 4,305 |

What was removed or merged: the screenshot-capture generator and about 90 SVG-writing calls with their fixtures (156); sampler, widget-config, help, NO_COLOR, draft and layout tests folded into tables and per-size copies trimmed in `test_dashboard_app.py` (7,142 to 6,508 lines, 64 to 55 functions, 157); tab-stop, popup-route, palette, legacy-turn, role-signal, hold-set, task-activity, detail-marker and resource-failure groups in nine files, plus style-only and real-host duplicate tests (158); docker, first-step, verify-ladder, integrate-conflict and exclude-append tests, repeated boundary cases, two config tests subsumed by loader tests, and `slugify` moved to unit (159). Task 160 to 164 added a few lean tests and rewrote those pinning superseded behaviour.

What the audits proposed and was rejected: the attention-entry group (each test keeps a distinct rule; trimmed, not merged); merges whose setups or rules differ (Task 158); deleting `test_missing_pane_is_dead`, the only coverage of the killed-pane branch, and merging the two tmux session tests with different setups (159); the Up/Down/Left/Right-inert escalation tests, the motion teardown and overlap tests, the one-column scrollbar assertion, the rejected-command recovery and the in-flight report discard test, which guard operator-found regressions.

## Gates

| Run | Tree | Result |
|---|---|---|
| `uv run poe check` (default workers) | `883f297` before the test updates | 1,583 passed, 14 skipped; 1 failed: `test_home_shared_detail_refreshes_after_task_detail_closes`, which pinned the Home inspector staying stale under a task detail (updated in the fix) |
| same, re-run | + test update | 1,583 passed, 14 skipped; 1 failed: `test_contextual_message_draft_survives_back_action_change_and_another_tasks_answer` (a 24 s test; 3 of 3 passes alone, with and without the change) |
| same, re-run | same tree | 1,583 passed, 14 skipped; 1 failed: `test_relationship_pages_keep_tail_tasks_navigable_and_blocking_dependencies_visible` (a Textual mount-time `NoMatches` in `TaskDetailPanel.on_mount`, a path the fix does not touch; 3 of 3 alone and 32 of 32 under ten parallel copies, with and without the change) |
| same, re-run | `883f297` | green: 1,584 passed, 14 skipped, 91.17 % coverage, 256 s |
| `TERM=dumb NO_COLOR=1 uv run poe check` | `883f297` | green: 1,584 passed, 14 skipped, 91.18 % coverage, 253 s |
| `uv run poe check` (default workers) | final tree (documents only changed since `883f297`) | green, exit 0 |

The host was otherwise idle (load average about 2 at the start, 7 to 13 during gate runs). No worker count was lowered; the default ten workers were used throughout. The first red was caused by this task's own change; the next two were load flakes that passed alone, and the next two runs were green.

## Remaining limits

- (a) Under `NO_COLOR` the marker is the same glyph at warn and alert, so the two levels differ only by a grey shade (`#b6b6b6`, `#aeaeae`); the marker does mark low free space without colour. At very narrow widths it wraps alone (14 columns), at 20 and 30 columns it wraps with the unit (`GiB ‼`).
- (b) The first Disk render reads `.shikiru measuring…` for one short tick (the resources-refresh cadence, 1.9 s measured) while the first walk of the folder runs.
- (c) The standalone `Diagnostics · docker` and `Diagnostics · disk` popups repeat the popup sections; `Diagnostics · faults` opens empty while no fault is open.
- (d) Tasks 156–159 and this task's own gate runs show timing flakes in the default-worker gate under load (the CPU wall-clock test, a dashboard help test, the 24 s draft test and a Textual mount race), each passing alone.
- (e) The Task 161 unsubscribe on unmount is not asserted: a test proves that `views.resources()` stops notifying a callback after its unsubscribe function runs, but not that the popup panel and the Faults section call it when they unmount.
- (f) The Task 161 notification table has three cases (sampled or fresh, stale, failed) with the identical assertion: the callback is heard after every call whatever the sample, so the cases differ only in the value handed over and add little beyond the first.
- (g) Capitalised words after ` · ` remain in data rows and status sentences by the spec's own exception: command-history rows (`#1 · Pending · Submit · T-1`), task states (`· Queued`), `· Search: …` in the browser count line and `T-1 · No decision needed`.
- (h) The task detail now also refreshes the Home inspector beneath it while it is open (every Home widget already did); the duplicate refresh is one more read per tick.
- (i) The HTML sample shows active agents and an engine up for hours where the native fixture had none; its Views list shows five of the fourteen entries; it has no stale demonstration; the other limits of the seventh record (tiny preview, No-color palette outside the popup, Home resources bar sample) stand.
- (j) The compact check used a 47×70 terminal (37 content columns) rather than 48×30, because the Resources bar is not reachable at 30 rows.
- (k) The Task 153 commits `6fd13e4` and `ca48b1a` carry `Co-Authored-By: Claude Haiku 5.5`; the history was not rewritten.

## Hygiene

The Chrome profiles, the throwaway projects, the fake `docker`, the harness and its renders, the `0d55954` worktree and every scratch file were deleted. No process, tmux socket or Chrome instance of mine remains; the `shk-tmux-*` fixture directories left by gate runs were removed. The operator's live session, `test-project`, default tmux server and Chrome were not touched.
