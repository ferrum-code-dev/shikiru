# Phase 11 seventh-review verification

Status: Task 155 verified Tasks 151–154 on 2026-10-12 against the final source, revised the HTML reference and marked the spec delivered. The spec is [`tui-feedback-seventh-review.md`](tui-feedback-seventh-review.md); the preceding record is the [sixth review](release-readiness-2026-10-12-phase11-sixth-review.md). GNU/Linux remains unverified; everything ran on macOS (Darwin 25.3.0, Python 3.14.7, Textual 6.3.0).

Source: `eaba7e2`, `1c4d032`, `88f2973`, `6fd13e4`, `ca48b1a`, `b0107b7`, `aafd53b`, `bf60d73`, `086db65`, `bd4d6c7` (Tasks 151–154), then Task 155's `45bd3b2` (a test-only synchronisation fix), `a107472` (HTML reference) and `5d84702` (design documents), with this record, the README and spec links and the plan and todo ticks in the last commit. No product code changed during verification.

## Issue-by-issue result

| Request | Commits | Result | How verified |
|---|---|---|---|
| One layout without duplicates: Resources and Engine permanent, then Docker, Disk and Faults (Faults only while a fault is open); usage-style rule and grey heading; no scope, CPU/RAM explanation or engine-lock row; no `Diagnostics · Resources` entry | `eaba7e2`–`bd4d6c7` (151–154), `a107472` | pass | Native popup dumps at wide and compact, colour and `NO_COLOR`, token-compared with the HTML (see below); per-character tests from Tasks 152 and 154. |
| Refresh like the Resources bar: Resources, Engine and Faults re-read the cache on `resources_refresh_seconds`, Docker on `docker_refresh_seconds`, Disk on `disk_refresh_seconds`; never a second sample; repaint only on change | `b0107b7`, `aafd53b`, `bd4d6c7` | pass | Task 154 timer test with a zero sample count and `wait_until`; `observed now` and the `now`/`2m`/`1h` form in the native render. |
| CPU/RAM permanent; Docker, Disk and Faults are default-on `DIAGNOSTICS` plugins rendered generically in `[dashboard].widgets` order; a disabled plugin disappears | `6fd13e4`, `ca48b1a`, `bd4d6c7` | pass | Widget-order and disabled-plugin tests (Task 154); native default render shows docker, disk, faults in that order; design 05 lists the keys. |
| Docker essentials: running of total, total CPU and RAM of running, problems, top five by CPU, `Elsewhere`, project scope, `usage unavailable` when `stats` fails | `eaba7e2`, `1c4d032`, `88f2973` | pass | Domain and sampler tests (Task 151); native `Running 6 of 8 · CPU 47% · RAM 1.8 GiB`, `Problems web unhealthy · worker exited (1)`, the five largest by CPU, `Elsewhere 2 running · CPU 3.0% · RAM 300 MiB`. |
| Disk section: `Free` with amber and red levels, `Runs` | `6fd13e4`, `ca48b1a` | pass | Native `Free 222.4 GiB of 460.4 GiB`, `Runs 0 B`; sampler and widget tests (Task 153). |
| Faults section: `task · scope · error · recoverable · ×N` with `first` and `latest` | `bd4d6c7` | pass | Native `T-1 · merge · timeout · recoverable` / `×3` with both timestamps; the two-fault HTML sample matches line for line. |
| Views: standalone `Diagnostics · Docker`, `Diagnostics · Disk`, `Diagnostics · Faults`; Project diagnostics opens from the Resources bar (Enter or click) and the task menu | `bd4d6c7`, `a107472` | pass | Native Home Views list (14 entries) shows the three diagnostics entries and no Resources entry; Enter on the Resources bar opened the popup natively. |
| One observed-age form (`now`, `2m`, `1h`) in the popup and the Resources bar | `aafd53b` | pass with limit (d) | Native and HTML headings read `observed now`; the Home header's repo-status age still uses `H:MM:SS` (limit d). |

## Native renders

The real `bootstrap.run_dashboard` wiring built the `DashboardApp` over a throwaway project with real `DashboardServices`, `ResourceSampler`, `DockerSampler` and `DiskSampler`. A fake `docker` script on `PATH` returned fixed `ps` and `stats` records (ten containers: six running in the project, one unhealthy, one exited with status 1, two running elsewhere). The app ran headless, the popup opened with Enter on the Resources bar, and Textual's line filters were applied before each character's bold and colour was read. `TMUX_TMPDIR` pointed at an empty scratch directory, so the resource path's tmux probes could not reach the user's default socket. The host Docker daemon was asked only read-only questions (`docker ps -a`, `docker stats --no-stream`, `docker version`); the host had no containers.

Captured: the popup at wide (100×50, content 58 columns) and compact (48×30, content 37 columns with the scrollbar), in colour and with `NO_COLOR=1 TERM=dumb`, with faults open and with none; the standalone Docker, Disk and Faults views (with a fault and with none); and the popup at 20×8. The monochrome captures set both `NO_COLOR=1` and `TERM=dumb`; `TERM=dumb` was not captured on its own.

Textual's `NO_COLOR` output is a luminance-grey mapping that keeps bold, not a colour drop: ink `#e1e1e1`, grey `#a4a4a4`, bold cyan figures `#b3b3b3`, amber `#b6b6b6`, dusty rose `#aeaeae`, rule `#454545`. The sixth-review record described the mono output as dropping colour; this record corrects that wording.

## HTML reference

`docs/design/references/focus-steel-approved.html` SHA-256 `1a71524a2d5824a49dd3a80182724fd9747dd061f3cc70d347a17b6a9d76a0d8`. The reference is an iframe whose `srcdoc` holds the app, entity-encoded; Task 155 decoded it, edited the app and re-encoded it with an exact round-trip check, so the file keeps its encoding and only the app changed. The popup now draws the delivered sections, the standalone views open the same sections, Views lists Command history, Usage details and the three Diagnostics entries, and the note says Project diagnostics opens from the Resources bar or the task Actions menu. The popup frame is 58 columns wide and 37 compact.

The Chrome check ran the decoded app in headless Google Chrome under a throwaway profile (deleted), driving the Situation, Terminal and No color controls and the Resources bar, and read each popup line per visual line and per token (text with digits normalised, bold and colour), then compared with the native dumps:

- Stopped scene (no faults, no engine), wide and compact, colour and No color: every line matches.
- Decision scene with two faults, wide and compact, colour and No color: every line matches except the Agents row and the Status row. The sample shows active agents and a running engine; the native fixture had neither.
- Standalone Docker (11 lines), Disk (4 lines), Faults with a fault (8 lines) and Faults with none (0 lines): all match.

Wrap points match the native ones, including `Running 6 of 8 · CPU 47%` / `RAM 1.8 GiB`, the Problems wrap, the Top 5 rows, `Elsewhere`, and the Faults `first` / ISO stamp lines. The wide and compact screenshots were read and match the TUI. The repository file itself renders in Chrome with its iframe.

## Design and operator documents

Design 05 (commit `5d84702`) adds the `disk` widget, `docker_scope`, `disk_refresh_seconds`, the free-space thresholds and the Disk interval row. Design 06 §5.4 describes the popup sections in order, the Docker facts and scope, the Disk section, timer refresh without sampling, the one age form, the disappearing of disabled sections and the Views placement; §6.1 records the shared section styling and the Disk NO_COLOR gap. Design 02 lists the Disk sampler and widget. Design 01 needed no change (D24 names only the generic Diagnostics section). `docs/tui-focus.md` describes Views in general and the resource snapshot, not the popup, so it is unchanged. The design README marks the seventh review delivered.

## Gates

| Run | Tree | Result |
|---|---|---|
| `uv run poe check` (default workers) | `bd4d6c7`, before any Task 155 change | 1,643 passed, 14 skipped; 1 failed: `test_resource_sampler_deduplicates_overlapping_recorded_agent_roots` (RSS within 4.2 MB under load; passes alone) |
| `uv run poe check`, first re-run | + HTML draft | 1,643 passed, 14 skipped; 1 failed: `test_resource_sampling_failure_is_stale_and_task_navigation_still_works` (Faults list read before its refresh; fixed in `45bd3b2`, then 5 of 5 passes alone) |
| `uv run poe check`, second run | + test fix and design drafts | 1,643 passed, 14 skipped; 1 failed: `test_a_restarted_engine_completes_the_task_it_lost_at_a_step_boundary[conflicts report submitted]` (fake-agent report missing; 13 of 13 pass alone, twice) |
| `uv run poe check`, second re-run | same tree | 1,643 passed, 14 skipped; 1 failed: `test_resource_sampler_deduplicates_overlapping_recorded_agent_roots` again |
| `PYTEST_ADDOPTS='-n 2 --dist=loadfile' uv run poe check` | final tree | green: 1,644 passed, 14 skipped, 91.20 % coverage, 587 s, exit 0 |
| `TERM=dumb NO_COLOR=1 PYTEST_ADDOPTS='-n 2 --dist=loadfile' uv run poe check` | final tree | green: 1,644 passed, 14 skipped, 91.21 % coverage, 590 s, exit 0 |

The default ten-worker `uv run poe check` was not green on this host in any of its four runs. Each run failed one timing-sensitive test while the rest passed: the RSS test twice, the Faults refresh test and the engine restart test. Each named test passed when run alone. The host had a load average of 4 to 7 on 10 cores, with the user's live session and Docker Desktop running. The green evidence is the two-worker run over the same tests, the setting earlier release records used on this host. None of the known full-suite flakes listed in the seventh-review rules occurred.

## Remaining limits

- (a) Under `NO_COLOR` the Disk free level (ok, warn, alert) has no word or glyph and differs only by a grey shade, which does not meet the word-or-glyph rule of 07 §3. Docker problem kinds differ by their words (`unhealthy`, `restarting`, `exited (N)`) and a shade. The Faults count keeps its `N open` word and differs only by shade.
- (b) At 20×8 the popup content area is 10×4 (measured). Sections scroll, ISO timestamps in the Faults section break mid-token (`2026-10-1` / `2T21:15:1`), and a Disk unit can wrap alone (`of 460.4` / `GiB`).
- (c) Disk values read `120.0 GiB` with one decimal, and zero bytes read `0 B`.
- (d) The Home header's repo-status age still uses `H:MM:SS` (`dashboard/services.py`, `format_age`).
- (e) The standalone `Diagnostics · Docker`, `Diagnostics · Disk` and `Diagnostics · Faults` Views entries duplicate the popup sections (native lines identical).
- (f) The Task 153 commits `6fd13e4` and `ca48b1a` carry `Co-Authored-By: Claude Haiku 5.5`; the history was not rewritten, by decision.
- (g) Docker project scope relies on compose labels; containers started with plain `docker run` count as `Elsewhere`.
- (h) The free-space thresholds of 10 % (amber) and 5 % (red) are an orchestrator choice, tunable as `[dashboard] disk_warn_free_pct` and `disk_alert_free_pct`.
- (i) Other findings:
  - At 20×8 the Top 5 rows lose their names and show the CPU figure as `3…` (`3…    410` / `MiB`), which hides the figure. This is a layout defect; it was not fixed in this task.
  - The standalone `Diagnostics · Faults` entry opens empty while no fault is open, although the Views list shows it.
  - The popup can show `Faults 2 open` in the Engine section while the Faults section still lists one fault. The two sections appear to refresh on separate watch cycles; this is inferred from the observed text and not traced. The test now waits for both.
  - Disk `Runs` measures `.shikiru/runs/` only. The spec's "run and evidence directories" is not implemented literally: evidence exports go to operator-chosen destinations and are not counted.
  - The HTML reference draws five of the TUI's fourteen Home Views entries (the others are filters, Attention, Strips, Dialogue, Operator actions and Help). It does not revise the Home Resources bar sample, its No-color palette outside the popup is not Textual's grey mapping, and its Tiny preview keeps ISO stamps whole where the TUI breaks them.
  - The default-worker gate is red on this host under load (see Gates), so the green evidence depends on the two-worker setting.

## Hygiene

No process started for this task remains: the Chrome probes were stopped by process id, and the render and gate tools exited. No tmux socket of mine remains, and the empty `shk-tmux-*` fixture directory left by a gate run was removed. The user's `default` tmux server and the live `test-project` session were not touched. The Task 155 scratch workspace (renders, Chrome profiles, decoded HTML copies, tools and logs) was deleted.
