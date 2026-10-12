# Phase 11 seventh real-project review

Status: planned (Tasks 151–155, 2026-10-12); the operator approved the section layout, the Docker scope, the Disk section and the disappearing of disabled sections below. This specification refines the [sixth review](tui-feedback-sixth-review.md) and Design 05 §3, 06 §5.4 and §6 where the requirements below supersede it. **The TUI is the source of truth**: the [HTML reference](design/references/focus-steel-approved.html) follows the product (Task 155).

Analysis: two read-only static analyses (Sonnet 5.5 medium for the panel structure and refresh, Sonnet 5.5 medium for the Docker facts, with a read-only look at the local Docker CLI). Nothing else was run.

## Findings

- The Project Diagnostics popup re-reads its cached facts only on a database commit; it has no timer. The Home Resources bar refreshes every `[orchestration.resources] sample_seconds` (2 s) and keeps sampling while the popup is open, so the popup's values and `observed … ago` freeze. Only Docker refreshes on its own timer.
- Duplicates and noise: the constant `Scope · current Shikiru project` row, a four-line explanation of CPU/RAM, up to three `observed … ago` lines, four wordings of "no sample", a lone `Engine lock` row, open engine faults rendered both in the popup and in the `faults` widget, and a second near-duplicate `Diagnostics · Resources` popup in the Views menu.
- The popup hard-wires the `docker` plugin; any other diagnostics plugin appears only as its own Views popup. Docker lists every container on the host, unsorted and uncapped, as raw strings, with no count, total, health or exit state.

## Requirements

### Layout
The operator approved this layout (notation: grey labels, bold white row names, bold cyan numbers, green / amber / red state words; headings are a rule plus a grey title like the Project usage panel; values illustrative):

```
Project diagnostics

── Resources ───────────────── observed 2s ago
Host     CPU 5.0%   RAM 12.3 / 24.0 GiB
Shikiru  CPU 1.2%   RSS 0.4 GiB
Agents   CPU 2.3%   RSS 1.2 GiB

── Engine ─────────────────────────────────────
Status   Running · up 3h 12m
Faults   None open

── Docker ───────────────────── observed 3s ago
Running  3 of 5 · CPU 45% · RAM 1.9 GiB
Problems web-db unhealthy · worker exited (1)
Top 5 by CPU
  api       38%   410 MiB
  web        5%   1.2 GiB
  cache      2%    96 MiB
Elsewhere 2 running · CPU 3% · RAM 300 MiB

── Disk ─────────────────────────────────────
Free     120 GiB of 500 GiB
Runs     1.2 GiB
```

- With open engine faults the Faults row reads `2 open` (amber) and a Faults section lists each fault (`T-12 · merge · timeout · recoverable · ×3`, then `first … · latest …`).
- Each fact appears once. The scope row, the CPU/RAM explanation, the lone `Engine lock` row and the legacy `Diagnostics · Resources` popup are removed. `observed … ago` appears once per section heading, in the Usage panel's grey form.
- Resources and Engine are permanent. Docker, Disk and Faults are default-on `DIAGNOSTICS` widget plugins; the popup renders every configured one generically, in `[dashboard].widgets` order. A section whose plugin is not configured disappears (no `not configured` row); a configured but unavailable, failed or stale source keeps its honest state inside its section. The Faults section renders nothing when no fault is open.
- Unavailable, stale and failed states keep their existing meaning (06 §5.4); wording is unified (`unavailable`, `failed`, `stale · observed … ago`). Wrapping happens at ` · ` boundaries, and the popup stays usable at 20×8.

### Refresh
Resources, Engine and Faults re-read their cached observations on the same cadence as the Home Resources bar (`resources_refresh_seconds`), without waiting for a database commit, and never start a second sample (06 §5.4). Docker keeps `docker_refresh_seconds`; Disk refreshes on a slower `disk_refresh_seconds`. A tick re-renders only when the content changed.

### Docker
- Facts: running of total containers, total CPU and memory of the running ones, problems (unhealthy, restarting, exited with a non-zero code), the top 5 running containers by CPU (ties by memory, then name), each with CPU and memory, and an `Elsewhere` total.
- Sampling: `docker ps -a --format json` and `docker stats --no-stream --format json` together; the values are parsed to numbers by a pure domain parser, never kept as display strings. No `docker inspect`, no network or block I/O, PIDs, ports, images or daemon version. If `ps` succeeds and `stats` fails, counts and problems still show with `usage unavailable`.
- Scope (*decision*): containers are the project's when their compose working directory is inside the project root or its task worktrees; all others are summed in `Elsewhere`. `[dashboard] docker_scope = "project"` (or `"all"`) is a tunable.

### Disk
A default-on `disk` plugin shows free and total space of the filesystem holding the project and the size of the run and evidence directories (`Runs`); the directory walk is cached for `disk_refresh_seconds`.

## Superseded accepted behaviour

| Accepted | Superseded by this review |
|---|---|
| 06 §5.4: diagnostics reads the cache only on change; unconfigured Docker labelled `Docker · not configured` | Timer refresh; a disabled section disappears |
| `Scope · current Shikiru project`, CPU/RAM explanation, `Engine lock · held` rows | Resources / Engine sections |
| Docker rows `name · CPU … · memory …` for every host container | Counts, problems, top 5, `Elsewhere` |
| Views › `Diagnostics · Resources` popup | Removed (duplicate of Project diagnostics) |

## Execution

Tasks 151–155 run sequentially, one fresh agent per task (Sonnet 5.5 medium for 151, 152 and 154, Haiku 5.5 xhigh for 153 and 155), root review and corrections by the same agent. Follow AGENTS.md test-value rules. Each commit needs a green `uv run poe check`.
