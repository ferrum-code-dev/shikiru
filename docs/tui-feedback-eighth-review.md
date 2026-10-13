# Phase 11 eighth real-project review

Status: delivered (Tasks 156–166, 2026-10-13; see the [record](release-readiness-2026-10-13-phase11-eighth-review.md)). This specification refines the [seventh review](tui-feedback-seventh-review.md). **The TUI is the source of truth**: the [HTML reference](design/references/focus-steel-approved.html) follows the product (Task 166).

Analysis: four read-only static analyses (Sonnet 5.5 medium for refresh/header and the integration-test audit, Haiku 5.5 xhigh for titles/`!!`/`.shikiru` size and the unit-test audit). Nothing was run.

## Requirements (operator findings 1–8 on Project diagnostics, plus test hygiene)

### Layout (1)
No blank line and no horizontal rule above `Resources`: the popup header rule is the only rule there. `Resources` (first permanent section) is drawn without its own rule; the rule between later sections stays. The standalone `Diagnostics · Docker/Disk/Faults` Views popups follow the same rule (their first section is unruled).

### Observed / stale hints (2, 3)
- *Cause:* `resource_sample()` marks a cached sample STALE when its age reaches the sample interval (2 s), the same period as the refresh, so the popup flickers `stale`/`observed now`; the Home bar only reports stale after a real sampling failure.
- Stale means only "a later sampling failure kept the last good observation". The age-based branch is removed. A section heading shows an age only in that stale state (`stale · observed 2m ago`, amber); fresh, empty, failed and unavailable states show none. Resources shows no age hint at all; its failure rows (`failed · …`, `unavailable · …`, `Refresh  failed · …`, `process tree is empty`, `CPU/RSS unavailable`) stay. The Usage panel's own `observed 2m ago` is unchanged (it reports an external quota observation).

### Disk (4, 7)
- The `Runs` row becomes `.shikiru`: the apparent size of the project's `.shikiru` folder (run directories, task worktrees, state database, logs); copy-on-write clones count in full, hard links once, symlinks are not followed, an unreadable subtree is a failure. The size is measured on its own slower cadence (`disk_size_refresh_seconds`, 300) under the existing single-flight; free space keeps `disk_refresh_seconds`.
- *Decision:* free space below the warn level (10 %) ends with a ` ‼` marker, the glyph agent window tabs use for escalation (`GLYPH_ESCALATED`), bold amber; below the alert level (5 %) the same marker in dusty rose. `220.5 GiB of 240.1 GiB ‼`. This also makes the level readable without colour. (Literal `!!` is two cells and cannot join the single-cell glyph registry.)

### Refresh timing (6) and header (8)
- The popup re-reads its cached Resources, Engine and Faults exactly when the Home Resources bar refreshes: `views.resources()` (the only sampling point) notifies subscribers after every call; the popup and the Faults section subscribe and drop their own timers. The popup still never starts a sample (06 §5.4).
- The header (and the Home attention status) uses the same gate as the other Home widgets (`widget_is_current`), so `Uptime` keeps ticking under a pushed popup. The motion-teardown safety tests stay.

### Title case (5)
One pattern everywhere: sentence case (first word capitalised; proper nouns and identifiers such as Shikiru, Docker, Esc, T-12 keep theirs). *Decisions:* segments after ` · ` are lower case (`Diagnostics · docker`, `Activity · dialogue`, `Task filter · active`, like the existing `Attention · decisions, stalls and faults`); after-key labels are lower case (`u details`, `e evidence`, like `Ctrl+P actions`); `Project diagnostics`, `Submit answer`, `Take over`, `Hand back`, `Set rung`, `Run doctor`, help copy likewise. `SHIKIRU` (masthead) and `TASKS` (surface heading) stay as documented upper-case exceptions, written into 06 §6.1.

### Test hygiene
The suite grows by wording-pinning and per-size copies. Apply AGENTS.md "Test value": remove the SVG capture side effects and `taskNN-*` names, merge per-rule copies into one table, move pure-service tests out of the Textual file, simplify the monster layout tests to behaviour (reachability, selection, focus, rule outcomes) instead of per-size wording, trim redundant boundary cases. Never remove the only coverage of a regression the operator found (listed in the Task 157–159 refs). Report collected-case counts before and after; coverage stays above 80 %.

## Superseded accepted behaviour

| Accepted | Superseded |
|---|---|
| Seventh review: heading `observed … ago` on Resources/Docker/Disk; resource view stale by age | Age shown only when stale after a failure |
| Disk `Runs` = `.shikiru/runs` | `.shikiru` = whole folder; free-space level marker `‼` |
| Panel and bar refresh on separate timers | One refresh moment via notification |
| Title Case outliers, `Diagnostics · Docker` | Sentence case, lower case after ` · ` |

## Execution

Test hygiene first (Tasks 156–159, so the product tasks rewrite fewer and leaner tests), then the product findings (160–165), then Task 166 verifies, updates the HTML reference and docs and writes the record. One fresh agent per task, review by the orchestrator, each commit with a green `uv run poe check`.
