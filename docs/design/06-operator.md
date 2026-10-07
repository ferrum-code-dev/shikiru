# 06 · Operator Interfaces

## 1. Operator layer

CLI, MCP and the dashboard palette are thin adapters over three small services:

| Service | Responsibility |
|---|---|
| `OperatorQueries` | Read-only views over `StateReader`: status, tasks, task detail, bounded task evidence pages across SQL events and recorded normalized signal files, escalations (with packet), peek (DB + the role's `view.jsonl`), bounded command history and one-command status |
| `OperatorCommands` | `send(command) -> Ack`. Inserts the typed command row (a database that stays locked past its 5 s busy timeout raises `DatabaseError`, the CLI's readable exit, and queues nothing: retry); if the engine is up, waits up to 10 s for `done`/`rejected`; if it's down, returns `queued` ("applies when the engine starts"). The client message distinguishes engine-down queueing from an elapsed acknowledgement wait; both remain `pending` in storage until handled. Delivery is at least once and handlers are idempotent by command id (03 §7), so a command applied just before an engine restart is acknowledged `done`, never `rejected`, when replayed |
| `EventFeed` | `read(since, kinds, task)` and `wait(since, kinds, task, timeout)` over `events` |

`StateReader.command_history(limit, offset, status)` returns one bounded newest-first page and its continuation metadata; `command_status(command_id)` reads one row and `pending_command(command)` checks for a matching pending payload. Submit matching ignores only the task ID allocated by the store; all entered submission fields still participate. `OperatorQueries.command_history(expanded=False)` reads the three newest; expanded project history uses 20-row UI pages, each capped at 50, with an all-status or status-filtered continuation. Stored `pending`, `done` and `rejected` are shown as **Pending**, **Applied** and **Rejected**; **Sending** is local to a form. An elapsed acknowledgement wait is not a rejection. A recovered pending row shows current engine liveness and leaves its original acknowledgement wait context unknown because the command row does not persist it.

## 2. CLI

- **Global options**: `--project PATH` (default: nearest ancestor with `.shikiru/`), `--json`, `--set k=v`.
- **Exit codes**: `0` ok, `1` error, `2` usage, `3` not initialised, `4` command rejected.

The CLI gets an Operator into a project, in and out of its session, and back out again. It is not a second copy of the operator surface: **every mutating action except `submit` lives in the dashboard palette (§5.5) for humans and in MCP (§4) for AI Operators and scripts.** One action, one adapter.

| Command | Purpose |
|---|---|
| `init [--yes]` | Wizard (§3); `--yes` writes the detected defaults |
| `start [--attach]` | Validate config and plugins, start the engine, ensure the session. On a running engine it only ensures the session. A name collision with another project fails with a hint to set `project.name`. `start` waits up to 5 s for the new engine to hold its lock and fails at once when the engine process has already exited. The detached engine's stdout and stderr go to `.shikiru/logs/engine.out` (0600, truncated by each start, so it holds the current start's output only, normally empty); the error of an early exit or a lock timeout ends with its last 2000 bytes, so a crash before the engine log exists is never lost |
| `stop [--kill-session]` | Stop the engine; running turns are stopped (their sessions persist) and re-run by resume on the next start. The ack is returned once the engine has released its lock, so `stop && start` is safe; if the lock is still held after `orchestration.shutdown_seconds` plus `TURN_STOP_GRACE`, the command fails |
| `ls [--host H]` | Every Shikiru project on this host, or on `H` (§5.6): name, directory, engine state, running and queued counts, open escalations |
| `attach [NAME] [--host H]` | Attach to a session; with no `NAME` and more than one match, the picker (§5.6) |
| `submit "<request>" [flags]` | Prints the `T-n` id (05 §1 flags) |
| `status [T-n] [--watch]` | Without an id: engine, slots, active tasks with `wait_reason`, queue, open escalations with the age of the oldest unresolved one, the open engine fault streaks (scope, task, error, first and last seen, repeats, recoverable; derived from `engine_fault` and `engine_recovered` events, 02 §1), usage and quota per agent, and the number and total size of the task run directories (05 §3, Growth). With one: that task's strip, timeline, reports and verdicts, branches and sessions. `--watch` redraws on `StateReader.on_change` |
| `doctor [--probe]` | A non-empty `engine.out` is listed as an `engine-output` check (informational), and `status` prints `last engine output: <path>` while the engine is down; environment (tmux ≥ 3.4 and git ≥ 2.45 fail the check below those versions), repository-root (D20), config and plugin checks; `--probe` validates the configured ladders (04 §3): live contract check 1 on each agent's first rung and a smoke turn on every other distinct rung, showing the stream tail and stderr when a probe fails |
| `mcp` | Stdio MCP server |
| hidden: `engine`, `dash`, `palette`, `view`, `report` | Process entrypoints. `view T-n ROLE` is the live view a role pane runs (§5.1) |

**Output**:
- Human output uses the `ui` renderables and tokens (§6): panels, tables, strips.
- Spinners show on long operations.
- OSC 8 links for paths.
- `NO_COLOR` and non-TTY output switch to monochrome.
- `--json` prints pydantic models.

## 3. `init` wizard

`dashboard/wizard.py` is an inline Textual app. It shares `operator/detectors.py` with `init --yes`:
1. **Repository**: the project root must be a git repository root (`git rev-parse --show-toplevel` equals it); otherwise `init` fails naming the path (D20). `base` = the current branch.
2. **Agents**: installed plugins whose `doctor()` passes. Enter explicit `agent:model[:effort]` ladders for leader, implementer and helper; the empty model field requires an operator choice.
3. **Trackers**: scan Markdown files up to four path components from the repository root, skipping `.git` and `.shikiru`; select files under a `tasks` directory or whose stem starts or ends with `plan` or `todo` (case-insensitive).
4. **Caches**: existing git-ignored paths with common cache directory names, preserving repository-relative paths, plus a CoW support check.
5. **Parallelism**: set the maximum number of active tasks.
6. **Integration mode**: choose local merge or pull request integration.
7. **Finish**: write `.shikiru/config.toml`, add `.shikiru/` to `.git/info/exclude`, run `doctor`. `init --yes` writes the editable `default` model placeholder, so review the role ladders and run `doctor` before `start`. When Next disappears on the last step, keyboard focus moves to Run Doctor, so Enter runs it (left to Textual, the focus fell back to Back after an asynchronous reset and a key sent at that moment stepped back).

## 4. MCP (`shikiru mcp`)

- **Transport**: stdio via FastMCP.
- **Remote use**: `{"command": "ssh", "args": ["host", "shikiru", "--project", "/path", "mcp"]}`.
- **Tools** map 1:1 to the operator services:

| Group | Tools |
|---|---|
| Queries | `status`, `list_tasks`, `get_task`, `list_escalations`, `get_escalation`, `peek_session` |
| Commands | `submit_task`, `prioritize_task`, `pause_task`, `resume_task`, `cancel_task`, `retry_task`, `set_rung`, `send_message`, `take_over`, `hand_back`, `resolve_escalation` |
| Events | `wait_for_events(since_id, kinds?, task_id?, timeout_s ≤ 300)` |

## 5. Dashboard

### 5.1 tmux session

- **Name**: `[shikiru] <project.name>`, with `.` and `:` replaced by `_`, always targeted as `=[shikiru] name`. The session env stores `SHIKIRU_PROJECT_DIR`.
- **Size**: the session is created detached at 240×64, so views get readable panes before anyone attaches; an attaching client resizes it as usual.
- **Terminal title**: the Shikiru session enables tmux title updates and sets the terminal title to the project name, without a Shikiru prefix.
- **Options**, set at the scope tmux gives each one:
  - *Session*: `base-index 0`, `renumber-windows off`, `mouse on`, and the project terminal title. These session-local options keep Home at index 0 and stop a removed Home from shifting a task or operator window into that slot, even when the user's tmux server uses another global base index; the global settings are unchanged. If an unrelated window already occupies index 0, session recovery reports the collision and leaves it intact.
  - *Task window*: `remain-on-exit on`, `allow-rename off`, `automatic-rename off`, `allow-set-title off`, `pane-border-status top`, `pane-border-lines heavy`.
  - *Home window*: the same lifetime and rename options, with `pane-border-status off`. The top pane-border row is Shikiru's own extra Home chrome; the Home identity row remains, task role labels keep their border row, and tmux's user-configured status line is left alone.
  - A window option set at session scope reaches only the session's current window, so `remain-on-exit` in particular must be set per window: without it a dead pane disappears instead of staying for the guard (§5.2).
- **Windows**:
  - The permanent home window is `dashboard` (`0:dashboard`), running `shikiru dash --home`. Start and session recovery identify it by its owned pane marker or command, so renaming it does not create a second Home. A restart restores its name and index. When two identified Home windows exist, recovery retains the preferred active Home and removes the duplicate without moving unrelated window identities.
  - Each active task has its own window, named exactly by `titles.window`, built from `dashboard.layout`. It is created at dispatch and closed at a terminal status. New task windows and their splits are detached: dispatch and guard restoration leave the attached client's current window and pane unchanged. Explicit task or role navigation and takeover still select the requested target.
- **Role panes** run `shikiru view T-n ROLE`: a read-only Rich live tail of `runs/T/<role>/view.jsonl` (turn boundaries with step and rung, messages, tool calls, usage, failures) through `ui.StreamLine`. It writes nothing and survives engine restarts. While the role is taken over (03 §9), the pane runs the agent's own TUI instead.

### 5.2 Layout and protection (`LayoutGuard`)

- **Build**: the packaged layout is a full-width vertical stack: Leader 40%, Implementer 40%, and a Project folder shell at 20%. It omits the system pane. The tree is built with detached `split-window -h|-v -l <size>%`; `{role: pane_id}` is stored in `tasks.panes`. Projects with no layout override inherit this default. Existing explicit overrides, including special panes such as `system`, remain unchanged until the operator edits or removes the override.
- **Guard**: every `poll_seconds`, the engine first recreates the session and its home window if the tmux server or session is gone (02 §1), then the guard compares each task window with the DB.
  - A missing configured pane (killed) is recreated beside surviving leaves in the layout. Restoring a missing pane preserves surviving pane ids and processes, then reapplies a direct vertical stack's configured geometry.
  - A dead configured special pane or view pane is respawned.
  - A dead taken-over pane (the Operator exited the TUI) becomes a `HandBack` (03 §9). A taken-over role whose pane is gone (tmux server loss, host reboot, a killed pane) is rebuilt, never as a read-only view of a held role: `LayoutGuard` appends `takeover_lost` and queues the `HandBack` (03 §9).
  - Each restoration emits `pane_restored`.

  There are no global key-binding overrides, so closing a pane has no lasting effect.
- **Palette key**: one conditional binding uses the effective inherited or session-specific prefix. In a live Home pane, `prefix + Space` sends F12 to the current pane so its mounted `DashboardApp` opens contextual Actions over Home. In other `[shikiru]` panes it opens the standalone `shikiru palette` app in a tmux popup and passes the originating `#{pane_id}` through hidden `--origin-pane`; task and role come from the project's recorded pane mapping rather than popup-inherited `TMUX_PANE`. Unrelated sessions keep tmux's `next-layout` default.

### 5.3 Titles and decorations

- **Titles** use the `dashboard.titles` templates. Task window names use `dashboard.titles.window` unchanged, without a Shikiru or project prefix. The packaged shell title is `Project folder · {cwd}`; an explicit title override is preserved. Fields:
  - `task_id`, `title`, `project`, `role`, `glyph`, `agent`, `model`, `effort`
  - `iteration`, `step`, `status`
  - `context`, `cost`: preformatted (`42%`, `$1.84`), `–` when the agent reports no value (04 §1)
  - `active`, `queued`, `max_parallel`, `cwd`
  - `flags`: `⏸︎ paused`, `⌨ operator`, `⧗ quota`, `‼ escalated`, `⚠︎ stalled`
- **Decorations** follow §6.1:
  - Pane options `@shk_color` and `@shk_glyph` feed `pane-border-format " #[fg=#{@shk_color}]#{@shk_glyph} #{pane_title} "`.
  - Window options `@shk_state_color` and `@shk_state_glyph` feed the window-status formats.
  - `TurnMonitor` and `TaskFlow` update them on change.

### 5.4 Workspace shell and widgets

Tasks 93–96 shipped Home's Focus composition, shared compact task detail and responsive compatibility as specified in [Compact Operator TUI](../tui-focus.md). Phase 11 Tasks 105–112 completed the current selection, overlay, layout and native verification routes. Phase 8's operator service behavior remains in force: state changes still go through `OperatorCommands`, browser/detail queries remain bounded, the engine stays the single writer, and existing widgets and callbacks remain compatible. See the [Phase 11 release record](../release-readiness-2026-10-07-phase11.md) and the historical [Phase 9 record](../release-readiness-2026-10-04-phase9.md).

`shikiru dash --home` is the mounted Textual Home workspace. The packaged task window contains the Leader view, Implementer view and Project folder shell from `dashboard.layout`; it has no dashboard or system pane. The standalone `shikiru dash --task T-n` entry remains available for an explicit task-focused dashboard. An explicitly configured `system` layout leaf is restored as a shell; to use it for a task dashboard, the operator must replace the pane command with an explicit current-project `dash --task T-n` invocation. `LayoutGuard` does not start that view automatically.

Home uses configured widget placements to build compact identity/engine and uptime, operating-count, project-usage, Host/Shikiru/Agents resource and conditional attention readouts. It splits list and inspector at 73 columns or wider and stacks the inspector below that width; at 20×8, identity and task detail remain reachable by scrolling. The footer hides below 9 rows; selection clear, contextual Actions and Help remain available through Escape, `Ctrl+P` and `?`, while tmux prefix+`d` detaches the client. The compact palette keeps its focused control reachable by scrolling; short text areas retain an editable row.

Home preserves the existing `dashboard.widgets` list and shipped default. `header`, `queue`, `escalations`, `usage` and `resources` fill their Focus readout/list placements only when configured. Running, Waiting and grouped Attention are shown as a short operating-count line and a conditional actionable row, not separate cards or permanent navigation controls. Running counts `running` and `integrating` tasks with no holds, scheduler wait reason or recorded current-turn quota wait. Waiting counts non-terminal queued, escalated, held, scheduler-waiting or recorded current-turn quota-wait tasks. Attention counts grouped task/project rows, so multiple reasons for one task remain one row. These categories can overlap and are never presented as an additive project total. Occupied slots continue to use `occupied_slot_ids`, including paused tasks and pull-request waits; slot occupancy is not the Running count.

Usage names its scope as project recorded runs or the selected task's runs. Token totals include explicit `UsageReported` sessions and legacy runs with positive token evidence; all-zero legacy token columns stay unknown, with explicit, legacy and unknown coverage shown. A quota-only agent has unknown token values. Billed and API-equivalent subtotals remain separate; known legacy prices keep a legacy label even when token provenance is unknown, and priced/unknown run counts keep partial subtotals from reading as complete spend. Zero is shown only for a recorded zero. Quota gauges use only reported windows and display the reported reset countdown and `observed_at` age. The project usage and selected-task run aggregates are cached by SQLite `data_version`; motion refreshes only advance countdowns and observation ages.

The Resources widget samples at its configured cadence outside motion callbacks; Diagnostics reads the latest cached observation without starting a second sample. The resource groups are Host CPU/used RAM, Shikiru CPU/RSS excluding agents, and this project's Agents CPU/RSS. Each RAM denominator is sampled host total; Shikiru and Agents show process RSS, not host-used RAM or a per-process limit. It distinguishes an empty process tree from a measured zero, an absent Docker CLI/daemon as unavailable, and failed samples as failed. After a later failure it keeps the last complete timestamped observation marked stale and shows the sampling error; an observation age always comes from the stored sample timestamp. `faults` lists open engine-fault streaks and their recorded scope, task, error, repeat count and first/latest timestamps. A failed diagnostic source leaves the other panels and task browser available.

`queue` is the selectable task browser. It searches task IDs and titles case-insensitively, combines search with Active, Waiting, History or All, reports the matching count, and loads longer lists in fixed-size bounded pages. Load more reads the loaded page range and the next page from one SQLite snapshot before appending; state updates also refresh the loaded range while retaining the visible reading position. `/` opens and focuses Home search. Text inputs and modal screens retain `/` as input. Press Enter to move focus from search to results; Up/Down then selects a task and keeps that row visible. Selection updates inline detail; Enter activates the focused control and never opens a duplicate detail screen. Home filter and history navigation are available from the mouse/keyboard Views menu rather than a permanent filter bar. Escape clears a non-empty search first, then hides the empty search field and returns focus to the list. The selected task's compact identity and situation stay visible when a refresh, filter or search removes its row; Views offers Reveal when the selected task no longer matches. The browser keeps navigation state in the dashboard process and reads task pages and counts through `StateReader`; it writes no state.

Widget entry-point names, callbacks and configuration remain stable. Home's primary readouts use their explicit placement, while secondary widgets appear from the Views menu in configured relative order. `strips` remains a secondary workflow view; `dialogue` is in Activity; `docker` and `faults` are in Diagnostics; detailed Usage and command history are reachable from Views. A plugin without a placement capability defaults to Extensions. Missing or broken configured widgets fail by name. When visible, the Home footer provides selection, clear, contextual Actions, detach and Help; below 9 rows, keyboard and tmux routes remain available without the footer. Click or use Up/Down to select a task and update the shared inline detail; Enter activates the focused control without opening duplicate task details. Escape closes an active overlay first, then clears Home's task selection. `Ctrl+P` opens contextual actions, `e` opens Evidence, `u` opens task-scoped Usage Details, `v` opens contextual Views, `?` opens contextual help and `/` opens search. Focus uses textual markers so it remains readable with `NO_COLOR`. The task-focused window and Home inspector share the compact task detail and contextual Views composition.

Actions, Answer, task Evidence and Usage, Project Totals, Diagnostics, history, contextual widget details, packet evidence, dialogue text and task Views open as bounded scrollable overlays over the mounted dashboard. They use the graphite surface and neutral border tokens; transparent overlay screens leave the current workspace visible underneath at wide and compact sizes. `Esc ×` closes each overlay, while Escape first dismisses an expanded native selector. Home's tmux prefix+Space event and `Ctrl+P` both open the same contextual `PaletteScreen` in its existing `DashboardApp`; separate task and role panes retain standalone palette entry and recorded task/role context. The compact summary retains its inline `u Details` and `e Evidence` links; task Usage has no self-link, Answer has no Evidence link, and Evidence has no redundant Situation return control.

Home keeps the selected task's shared detail inline. Escape clears the Home selection after closing any active overlay, and a new selection restores the detail without losing search or list position. The compact summary shows task situation, actual stage/activity, holds and ownership, critical decision guidance, task usage and combined role/agent/model session rows. Session totals remain explicitly session-wide; retained earlier models and per-turn observations appear in Usage Details with semantic history gaps. `v` or the Views control opens purposeful views for reports/verdicts, request and criteria, evidence timeline/signals, blockers/relationships, engine facts and workspace targets. A Leader verdict remains available after a later implementation report. An engine override replaces an agent verdict only in the same numeric turn; a newer Leader verdict takes precedence over an older engine override. Each report and verdict shows its recorded source. Missing or unreadable report/facts paths do not suppress independent role signals, and basic SQLite task state remains visible. Completed and cancelled tasks remain inspectable after ordinary worktree/window cleanup while retained run files and events exist; detail shows stored status/reason, available reports, decisions, integration events and engine-computed git facts. A recovered `turn_failed` event remains timeline evidence and never changes the task's terminal status. Blockers retain the stored scheduler reason and available records; relationships retain their current page and Previous/More controls. Related-task navigation reuses the existing detail callback and leaves Home's selected task identity unchanged. Submitted and recorded requeued ages use their own stored timestamps; the dashboard does not derive current-blocker duration, an ETA or a guaranteed start. Report and git-fact reads run away from the UI loop; timeline refresh reads bounded evidence only when its view is open and does not reload those files during motion. Timeline reads preserve engine event IDs and per-file signal order, isolate a failed role source as visible evidence, and leave task state available. Following latest pauses when the Operator scrolls away from the timeline tail; page changes and incoming rows preserve the reading position and selected evidence identity, and Follow latest resumes it explicitly. The mounted state subscription is cancelled when detail closes.

The shared detail refreshes basic task and ownership state after an overlay closes or the compact detail becomes visible again. Motion refreshes leave timeline pages on their own clock and keep report, facts, packet text and parsed packet evidence cached until their file identity or content changes. Normalized signal tails remain a bounded live observation. Selection changes invalidate the whole pending detail result so old task evidence cannot overwrite the selected task.

`dash --task T-n` uses the same compact detail panel fixed to `T-n`, task-scoped usage and session history, and on-demand evidence views. It omits project-wide header, queue, escalations, strips and diagnostics panels. Configured third-party widgets remain in Extensions. Detail views select only existing task windows or role panes through activation-time state reads; missing and terminal targets are reported without creating workflow resources. `Ctrl+P` opens the shared palette with the task and current role context. Home browser selection and palette context carry task identity through additive `WidgetContext` callbacks; old callback fields remain available.

The `DashboardWidget` protocol stays compatible; a plugin may additionally expose `placement: WidgetPlacement` from `dashboard.contract`. `WidgetContext` receives the resolved placement, a `home_attention_present` flag so identity widgets can avoid duplicating the actionable Home attention row while retaining legacy alerts when that row is absent, and the optional `open_contextual_view` callback.

```python
class DashboardWidget(Protocol):
    """A dashboard panel plugin (entry point group `shikiru.widgets`)."""
    name: str
    def create(self, context: WidgetContext) -> textual.widget.Widget: ...

@dataclass(frozen=True)
class WidgetContext:  views: DashboardViews; focus_task: str | None
                      on_change: Callable[[OnChange], Awaitable[None]]   # StateReader.watch
                      open_palette: Callable[[str], Awaitable[None]]
                      open_palette_context: Callable[[PaletteRequest], Awaitable[None]] | None = None
                      on_task_selected: Callable[[str | None], None] | None = None
                      open_task_detail: Callable[[str], Awaitable[None]] | None = None
                      open_related_task_detail: Callable[[str], Awaitable[None]] | None = None
                      navigate_task_window: Callable[[str], Awaitable[str]] | None = None
                      navigate_role_pane: Callable[[str, str], Awaitable[str]] | None = None
                      navigate_home: Callable[[], Awaitable[str]] | None = None
                      placement: WidgetPlacement = WidgetPlacement.EXTENSIONS
                      open_contextual_view: Callable[[str], Awaitable[None]] | None = None

class DashboardViews(Protocol):
    """Ready-to-render view models; the only data a widget sees."""
    async def header(self) -> HeaderView: ...            # project, engine up, slots, checkout (git cached 10 s)
    def workspace_summary(self) -> WorkspaceSummaryView: ...  # grouped attention, running and waiting counts
    def attention(self) -> AttentionView: ...             # durable decisions, diagnosed stalls and open faults
    def strips(self) -> list[TaskStripData]: ...         # active tasks, focused first, children under parents
    def queue(self) -> list[QueueRow]: ...               # dispatch order
    def task_browser(self, *, task_filter, search, offset, limit, selected_task_id) -> TaskBrowserView: ...
    def task_browser_pages(self, *, task_filter, search, limit, page_count, selected_task_id) -> tuple[TaskBrowserView, ...]: ...
    async def task_detail(self, task_id: str) -> TaskDetailView | None: ...
    async def task_timeline(self, task_id: str, *, cursor, role, turn, category) -> TaskTimelinePageView | None: ...
    def dialogue(self, task_id: str | None = None) -> list[DialogueBubbleView]: ...
    def usage(self, task_id: str | None = None) -> list[AgentUsageView]: ...
    def global_attention(self) -> GlobalAttentionView: ...
```

**Views are the widget API.** `dashboard.views` builds them from `StateReader`, `Settings` and existing domain rules (dispatch order, acting role and diagnosed task activity). For Active ordering it diagnoses current running and integrating turns with `DashboardMotion`, then passes stalled IDs to the SQL page query before pagination; it does not read history rows to calculate that order. Home summary uses task status, holds, scheduler wait reasons and recorded current-turn quota waits, with slot occupancy from the existing domain helper. Attention reads durable open escalation rows in creation-time/ID order, existing `DashboardMotion` stall diagnoses and unrecovered `engine_fault` events. It groups reasons per task, counts those grouped rows for the Attention summary, retains decisions when packet reads fail, and only removes them after durable resolution. The header reads engine-lock liveness independently from those event and signal observations. Activity includes recorded workflow decisions and transitions; the UI does not create new operational signals or notifications. A browser page query returns one SQL-limited page plus exact counts for the active filter and search; its ancestor context is a separate label and does not change match counts, ordering or pagination. A page-range query returns contiguous bounded pages and their counts from one read snapshot. Neither loads history and slices it in Python. Task detail reads bounded direct children of the selected task, its actual parent and family root, dependencies, dependents and recorded replacements with continuation pages in one read snapshot; family outcome counts are rooted at the family root. Replacement links use only IDs recorded in a supersede scope-change event. Task history queries use a timestamp/event-ID keyset for SQL and byte-offset cursors per normalized role signal file. Each signal scan has a configured row bound even when category/turn filters reject recent rows; older cursors advance through those filtered rows. Pages merge by recorded UTC timestamp with deterministic engine/role/position ties, while preserving each source's order. The domain parser classifies normalized signal names, and the dashboard reads no agent stdout or provider-specific formats. Missing files/timestamps and an incomplete trailing JSONL row remain explicit evidence. A standalone timeline query refreshes mounted detail without reloading report/facts files. Blocker evidence uses the recorded scheduler reason or a running task's matching current-role quota events and active agent block, plus relevant task/slot/quota/PR state and the latest dashboard resource sample. Provider quota resets and engine retry/probe deadlines stay separate, as do scheduler resource decisions and dashboard sample age. The existing `queue()` result remains available to configured widgets. A widget only renders: it never sees the state DB, domain types, config or tmux, so an external widget depends on `dashboard.contract` and `ui` alone. View models are frozen dataclasses in `dashboard.contract` (`TaskStripData` is `ui`'s). Existing widget names, view fields and callbacks remain stable; browser task-selection and palette-context callbacks are additive.

Task 91 adds `DashboardViews.project_usage()` over every retained run row, including closed-task rows, and `TaskDetailView.usage` over one task's recorded lineages. Their `RecordedUsageView` separates token coverage, actual billed amounts, API-only missing-charge coverage, API-equivalent evidence and distinct declared subscription agents. `HeaderView` appends current and previous engine durations; the current duration requires a recorded start matching the verified lock-holder identity, and previous duration requires a matching recorded stop. `TaskSessionView` appends persisted run ID, current/live/taken-over status, bounded model/effort observations, attributed per-turn deltas only when identity is complete, and semantic history-gap reasons. Turn cost basis is known only for comparable same-basis cumulative observations; legacy numeric deltas without basis remain unknown and never borrow the run's latest cumulative basis. `DashboardViews.resource_sample()` exposes the last timestamped `ResourceView` without starting a sample. Task 93 consumes project coverage, verified uptime and resource fields in Focus Home; Task 94 consumes the retained task/session usage and model-history fields in its shared detail panel.

Task 92 ships shared Rich renderables in `ui.compact`: `ProjectUsageMetric` shows a compact input-plus-output total only when token evidence exists, along with nonzero coverage and concise billing cues. Cache counters remain separate. Full cache/provenance coverage, all-mode and API billed subtotals, API-equivalent estimates, missing API charges, subscription declarations and unknown-mode counts stay in `ProjectUsageDetails`; per-agent billed, estimated and unknown-cost evidence remains in the usage widget's detailed rows. Actual billed amounts, estimates, subscription declarations and unknowns keep separate labels. An API-charge gap counts only API-mode sessions; subscription or unknown billing does not imply a missing API charge. Subscription counts name declared agents, never accounts or fees. Task 110 extends `ResourceMetric` to Host, Shikiru-excluding-agents and Agents groups from the same timestamped sample, with sample state, age and error context. Host RAM uses used/total; process RAM uses RSS/host-total and does not imply a per-process limit. `AgentUsageRow` combines role, lineage, model, session-wide input-plus-output usage and billing/charge evidence. `StageActivity` uses the existing `DashboardMotion` signal projection and only says a signal was observed when that projection has current-turn signal evidence; holds and ownership remain separate, without a second activity timer. The legacy `TaskStrip` remains available to CLI and configured widgets.

`WidgetContext.open_palette` remains available for existing widgets. `open_palette_context` carries an optional action, task ID and role to the shared screen; `on_task_selected` reports browser identity to the dashboard; `open_contextual_view` opens a compatible secondary view. `WidgetPlacement` is an optional widget capability from `dashboard.contract`; a widget without it defaults to Extensions, and unsupported placements fail by configured name. The dashboard owns current navigation state and exposes no services or typed workflow commands to widgets.

Widgets re-read their view when `on_change` fires. Samplers such as resources and docker have their own interval.

| Placement | Widget or view | Content and access |
|---|---|---|
| Focus identity | `header` | Project, engine state and verified uptime, slots, integration mode, checkout status/sample age and short Running/Waiting/Attention counts |
| Focus project usage | `usage` | Shared compact input-plus-output token and reported billing coverage; detailed costs, declarations and quota windows open from Views |
| Focus resources | `resources` | Host CPU/used RAM, Shikiru CPU/RSS excluding agents, and project Agents CPU/RSS from one cached sample; age and explicit empty, stale, unavailable or failed state |
| Focus attention | `escalations` | Conditional row for grouped decisions, diagnosed stalls and recorded faults; selection updates inline detail, `r` opens resolution and `o` opens evidence |
| Focus task list and inspector | `queue` | Searchable, selectable task browser: Active, Waiting, History and All; queued tasks use dispatch order, history uses latest finish first, and selection/reading position survive refresh and reorder |
| Activity | `dialogue` | Last 20 speech bubbles (§6.3); `Enter` shows full text |
| Diagnostics | `docker`, `faults` | Container observations plus open engine-fault streaks, reachable from Views |
| Workflow | `strips` | Active tasks as strips (§6.2); reachable from Views when configured |
| Extensions | Configured third-party widgets | Each configured extension in relative configured order, using its existing entry point and context |

The ordered `dashboard.widgets` list and its shipped default remain unchanged. Primary placements follow Focus's readout/task composition; Views provides mouse and keyboard access to filters, command history, detailed usage and configured secondary widgets. The task-focused shell uses the shared compact detail and task-scoped contextual views. The selectable task browser and shared task detail are shipped through the existing widget and dashboard contracts. `TaskNavigation` resolves the task's recorded window or pane from current state when activated and selects only that existing target. Before selecting a role pane, it checks that the pane still belongs to the task's recorded window. A terminal task remains inspectable after its window and worktree have been cleaned up.

### 5.5 Palette

- `PaletteScreen` offers searchable actions and task targets searched by ID or title through bounded pages with visible counts and a More control. Home and task detail push it over the mounted dashboard; `PaletteApp` remains the standalone entry for task and role panes. It keeps an editable task ID and role, reads current stored state on each search, and retains the selected target outside the current match. Context comes from selected detail/browser identity or from the originating role pane in the tmux popup; a standalone Home palette has no preselected task. Advanced submission fields, including submission and role rung overrides, remain available in an expandable area.
- The operator layer prepares task-action applicability and escalation-resolution availability from current durable state. The palette refreshes this projection before dispatch; the engine can still reject a command if state changes after that read. Widgets call `OperatorCommands` and do not create workflow commands or duplicate eligibility rules.
- The task-context menu keeps the primary routes in reference order: applicable Answer, Evidence & reports, Message a role, applicable Take over/Hand back, Usage, Commands and Project diagnostics. More actions exposes remaining task/project operations and configured secondary views. Extensions remain reachable through Views, in configured relative order.
- Direct Answer binds the selected durable decision's identity and recorded question to its form, without a redundant target picker. Generic `resolve` keeps its multi-decision selector. Opening and navigating action forms is read-only; only an explicit submit queues the typed command.
- Forms explain next-turn delivery for messages and rung changes, pause versus pause-now, task ownership during take over, and restrictions remaining after resume or hand back. Cancel and stop show their scope and work-preservation effects before the form's explicit Run action.
- Direct Answer is available only when the selected durable escalation permits `Answer`; it binds that escalation ID and recorded question to the form, shows the recorded suggestion and consequence when present, and requires explicitly submitted answer text. Other attention decisions open their generic resolution form with the actual target and question and only the durable row's allowed resolution kinds. The global Resolve picker keeps its multi-decision selector. Timeline, reports, facts, stream tails and the unchanged full packet stay available in collapsed evidence sections; expanded content scrolls with the form. A malformed or unavailable packet shows its evidence error while keeping the durable escalation and allowed kinds visible; malformed text remains available as the original packet.
- **Export task evidence** is a searchable contextual palette action, reachable from the task inspector through `Ctrl+P`. It revalidates the entered task ID, offers Markdown or JSON, requires a destination and an explicit refuse/replace choice, and optionally includes normalized signal excerpts. Relative paths start at the project root, absolute paths are used directly, `~` is literal, parent directories must already exist, and the chosen filename is used without adding an extension. The result shows the resolved destination or the file error. Export uses the injected local writer and does not call `OperatorCommands`, create a command row, or show a durable command acknowledgement.
- Export format `shikiru.task-evidence` version `1` carries the request, recorded status/reason, task and rung metadata, paginated task relationships, retained validated reports/verdicts and facts files, task timeline in newest-first source order, role session usage provenance, task-scoped aggregate usage, export time and `unavailable_sections`. The default timeline contains recorded engine events and decisions; normalized `view.jsonl` excerpts are opt-in and are omitted from the default artifact, including the task detail's live signal tail. Unknown usage is `null`, measured token zero is retained, priced legacy zero costs remain known, and billed/API-equivalent costs stay separate. Markdown renders each document section in a dynamically sized JSON fence, keeping embedded task/report/fact/timeline content inside those blocks. Export exhausts the selected relationship and timeline sources but does not hold a single SQLite/filesystem snapshot across the artifact; it describes retained evidence as each source was read and never reproduces agent transcripts or cleaned worktrees.
- The mounted palette refreshes the selected escalation as another Operator resolves or cancels it. It disables stale submission and keeps entered guidance for copying; a race after submission is shown from the engine's rejection without creating a replacement command.
- The bounded action and detail screens use the graphite surface with a neutral border and `Esc ×`; they preserve the mounted dashboard behind the overlay. Home's `prefix + Space` and `Ctrl+P` push `PaletteScreen` into the running `DashboardApp`. Other Shikiru panes continue to open the standalone `PaletteApp` in a tmux popup with originating task/role context.
- Every mutation calls `OperatorCommands`. The mounted palette observes durable state changes and shows `Sending`, then `Pending`, `Applied` or `Rejected`, with command ID, target and result. A completed pending form becomes available again when its durable row changes status. Pending duplicate checks use the exact typed payload so switching forms and reopening the palette cannot queue the same still-pending command again. **Applied** means the engine applied the command effect. For `say`, guidance is queued for the selected role's next turn; this status does not claim that an agent consumed it.
- Focus shows one compact latest-command summary above its footer and opens the paginated project history from contextual Views. Each history page contains at most 50 rows; the UI shows 20 at a time and offers newer/older navigation, left/right key bindings and status filters so older pending outcomes remain reachable. Full results and provenance stay in history rather than expanding workspace chrome. The palette, history and Focus readouts use `StateReader` and `OperatorQueries`, refresh from database changes, and recover commands submitted through another dashboard or MCP. Pending rows report current engine liveness without guessing whether their original client saw an engine-down queue or an acknowledgement timeout.
- The dashboard and overlays use the shared Textual theme. Packet and dialogue evidence screens use the same bounded scroll surface, neutral border, close control and text tokens.

### 5.6 `ls` and `attach`

`SessionLister` returns one row per project, for the local host or for `H`; `ls` prints the rows, `attach` picks one from them.

- **Rows**: `tmux list-sessions` filtered by the `[shikiru] ` prefix and each session's `SHIKIRU_PROJECT_DIR`; engine liveness comes from the project's `engine.lock`, while running, queued and escalated counts come from its DB. `ls` opens each DB with SQLite read-only mode and never changes its schema, progress or other project state. SQLite may create or update WAL and shared-memory coordination sidecars while reading a WAL database; a newly created WAL file is empty, and existing database or WAL contents remain unchanged.
- **`attach`**: exactly one row, or a matching `NAME` → `os.execvp("tmux", ["tmux", "attach", "-t", "=[shikiru] NAME"])` (`switch-client` when inside tmux). Otherwise an inline selectable list of the same rows.
- **Remote** (`--host H`, on both): `ssh H shikiru ls --json` gives the rows; `attach` then runs `os.execvp("ssh", ["ssh", "-t", H, shlex.join(["tmux", "attach", "-t", "=[shikiru] NAME"])])`. `--host` needs nothing installed locally but `ssh`, and nothing remote but `shikiru`.

### 5.7 Notifications

Home attention is a read-only projection of durable decisions, current diagnosed task stalls and recorded open fault streaks. It does not acknowledge decisions or deliver notifications. The engine lock remains the liveness source; fault events and role signals provide separate evidence. Informational retries, quota waits, expected rework and deliberate pauses keep their workflow meaning.

`Notifier`, on `escalation`, `task_done`, `integration_waiting` (a local merge waits for a dirty checkout, 03 §6), `takeover_lost` (a taken-over role was handed back because its pane was lost, 03 §9), `engine_fault` (the first fault of a failing tick streak, 02 §1) and an `engine_stopped` that carries a `reason` (the engine stopped on a fault rather than on request):
- runs `tmux display-message` in the session;
- runs `notify.command`, when set, with the event JSON on stdin (for Slack, ntfy or a remote AI Operator). A non-zero exit, a timeout (`notify.timeout_seconds`, 30 s by default) or a missing command is logged with the event id and recorded as an `engine_fault` of scope `notification hook`, once per streak (02 §1).

Delivery never runs on the command loop. The `Engine` queues each notifiable event on `NotificationDelivery`, which delivers urgent events on one task and informational events on another, each lane one at a time, so a hung hook cannot delay `stop`, `pause` or scheduling. Events have two priorities: *urgent* (`escalation`, `engine_fault`, an `engine_stopped` with a reason) and *informational* (`task_done`, `integration_waiting`, `takeover_lost`):
- urgent events have their own delivery slot: they are delivered in order and never dropped, and a stuck informational hook (killed after `notify.timeout_seconds`) never delays one; only an earlier urgent hook still in flight can, and that one is killed after the same bound, so a stuck hook delays but cannot silence an escalation or a fault. The hook may therefore run twice at once, once per lane;
- the informational backlog holds 64 events; when full, the oldest informational event is dropped with a logged warning;
- a delivery failure is logged and the next event is delivered;
- the engine loop supervises both tasks like the task flows: an unexpected failure is logged and the task restarted;
- a hook runs in its own process group, which is SIGKILLed on its timeout and on cancellation, so no descendant it started outlives the hook (03 §4.2);
- the delivery cursor is durable (`notification_cursor`, 05 §2): it holds the id up to which every urgent event has been delivered, trailing the oldest urgent event still queued or in flight, and the engine writes it on each poll and at stop. A restarted engine replays the urgent events after the cursor, in order and ahead of any new one, so an escalation or fault committed shortly before a crash or kill, or still queued behind a stuck hook, is notified after the restart. Informational events are never replayed, and the first start of a project begins at its latest event. Delivery is at least once: an event delivered after the last cursor write (at most one poll, 0.25 s) before a kill is delivered again, as is a fault stop notified directly at stop, and a hook that failed or timed out counts as delivered (it is recorded as a `notification hook` fault, not retried);
- on engine stop the tasks are cancelled, which terminates a running hook, and pending events are not delivered by this engine (urgent ones are replayed by the next), so `stop` never waits for a hook. The one exception is the `engine_stopped` event of a fault stop: it is delivered directly, bounded by `orchestration.faults.stop_notify_seconds`.

### 5.8 Keeping the engine running

`shikiru start` spawns the engine detached; nothing restarts it if the process dies (a fault stop, an OOM kill, a reboot). To keep it running, run the hidden entrypoint under a service manager and restart it on failure. A restarted engine reconciles persisted state (03 §7) and recreates the tmux session (02 §1). Give the service the `PATH` your agent CLIs, `git`, `gh` and `tmux` need. `shikiru start` reuses a running engine, so it remains the way to ensure the session and attach.

The engine stops gracefully on SIGTERM and SIGINT (03 §7), which is how `systemctl stop` and `launchctl bootout` end it, and exits 0; a stop that outlives `orchestration.shutdown_seconds` (15 s) or a second signal ends the engine and its turn processes by force and exits non-zero. Keep the manager's stop timeout above the bound plus the 10 s turn kill grace: systemd's default `TimeoutStopSec` (90 s) already is, launchd's default `ExitTimeOut` (20 s) is not, hence the explicit values below.

The unit files below show the shape; they are documented recipes that CI does not run.

systemd user unit, `~/.config/systemd/user/shikiru-myproject.service`:

```ini
[Unit]
Description=Shikiru engine (myproject)

[Service]
ExecStart=/path/to/python -m shikiru --project /path/to/myproject engine
Environment=PATH=/usr/local/bin:/usr/bin:/bin
Restart=on-failure
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=default.target
```

launchd agent, `~/Library/LaunchAgents/dev.shikiru.myproject.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>dev.shikiru.myproject</string>
  <key>ProgramArguments</key><array>
    <string>/path/to/python</string><string>-m</string><string>shikiru</string>
    <string>--project</string><string>/path/to/myproject</string><string>engine</string>
  </array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ExitTimeOut</key><integer>30</integer>
</dict></plist>
```

`Restart=on-failure` and `SuccessfulExit=false` restart the engine after a fault stop (non-zero exit) but not after `shikiru stop`, `systemctl stop` or `launchctl bootout` (exit 0).

## 6. Visual design: comic strips with a semantic visual language

The comic strip and palette below describe Phase 8's shipped visual presentation and remain available for CLI and explicitly opened workflow detail. Tasks 93–94 apply the graphite/steel/sage/amber/dusty-rose Focus palette to Home and task detail. The shared task detail follows [Compact Operator TUI](../tui-focus.md#2-composition-and-palette), which supersedes the expanded strip composition and role colors with compact state/activity and combined usage. The meaning of task state, role, hold, ownership and signal-driven activity remains; compact views keep text and glyph alternatives, motion limits and monochrome access.

Shikiru's look comes from **structure and meaning**:
- tasks are 4-panel comic strips (*yonkoma*);
- key moments are speech bubbles;
- escalations are splash panels.

Every colour, glyph and movement answers one of three questions: *who acts*, *what state*, *how alive*. English carries all meaning; kanji are decorative marks. Everything lives in `ui` and is shared by CLI and dashboard.

### 6.1 Tokens and glyphs

**Colours**: the Textual shell, palette, task detail and evidence forms share one named palette. Focus and activity use steel blue; successful reported charges use sage; holds and decisions use amber; errors use dusty rose. Role and state words carry meaning without role-specific neon colors. Truecolor has 256/16 terminal fallbacks; `NO_COLOR` retains readable words and the existing bold, dim and reverse cues.

| Token | Colour | Meaning |
|---|---|---|
| `background` | `#11151b` | Application background |
| `surface` | `#171d25` | Ordinary panel surface |
| `neutral-border` | `#3d4654` | Section and modal edges |
| `primary-text` | `#dce2e9` | Main text |
| `secondary-text` | `#9aa5b5` | Supporting text |
| `focus` / `steel-blue` | `#83bddd` | Keyboard focus and observed activity |
| `success` / `sage` | `#90cba6` | Successful state and reported billed amount |
| `hold` / `amber` | `#dfb16c` | Holds, decisions, subscriptions and estimates |
| `alert` / `dusty-rose` | `#e3a0a0` | Errors and diagnosed stalls |

Gauges (context, quota, CPU, RAM) use sage below 70%, amber below 90%, and dusty rose from 90% upward. All roles share the quiet steel accent where color helps; text and glyphs identify each role.

**Glyphs**: one set, registered in `ui.glyphs`. A unit test rejects wide and emoji-presentation characters. Ambiguous-width characters are allowed and drawn one cell wide, as Textual assumes; terminals set to CJK ambiguous-wide are unsupported. Glyphs that have an emoji variant carry U+FE0E (`⚖︎ ⚙︎ ⏸︎ ⚠︎`).

The compact task readout uses English stage and state words and the registered one-cell activity glyph. Kanji remain decorative only in the retained `TaskStrip` renderable, which stays available for CLI output and configured strip plugins; compact task content does not require kanji or color to explain workflow state.

| Meaning | Glyphs |
|---|---|
| Stages: triage, plan, implement, verify, merge, PR | `⌕ ≡ ⚒ ⚖︎ ⑂ ⇪` |
| Roles: leader, implementer, helper, operator, engine | `◆ ▲ ✦ ⌨ ⚙︎` |
| States: done, rework, consult, paused, quota, stalled, escalated, blocked by dependency | `✔ ↺ ? ⏸︎ ⧗ ⚠︎ ‼ ⊘` |
| Context gauge | `○ ◔ ◑ ◕ ●` |
| Spinner | `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` |

State is never shown by colour alone: each state has a glyph, and a word wherever there is room. The wide strip shows both; the narrow strip (§6.2) shows the glyph alone.

### 6.2 Task strip (`ui.TaskStrip`)

```
┏━ T-12 · Add retry to parser ━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃起 ⌕ Triage ┃承 ⚒ Implement┃転 ⚖︎ Verify  ┃結 ⑂ Merge  ┃
┃ ✔ 3m       ┃ ✔ ↺1 41m     ┃ ⠹ ◆ 2/5     ┃  ·         ┃
┃ ◆ opus     ┃ ▲ codex/luna ┃ ◑ $1.84     ┃            ┃
┗━━━━━━━━━━━━┻━━━━━━━━━━━━━━┻━━━━━━━━━━━━━┻━━━━━━━━━━━━┛
```

- **Panels by mode**:
  - implement: Triage · Implement · Verify · Merge (or PR)
  - plan: Triage · Plan · — · Merge
- **Panel state**:
  - pending: dim, dotted border
  - active: heavy border in the acting role's colour, spinner, role glyph, iteration
  - done: green `✔` and duration
  - rework: orange `↺n`
  - escalated or failed: red `‼`
- During dispatch setup, a `running` task without a persisted step has one engine-coloured setup panel. If setup retries exhaust before the first step, its step-less escalation has the red escalation panel.
- **Stamps** drawn over the active panel, in the hold colour: `PAUSED`, `OPERATOR`, `QUOTA · resets 14:10`, `STALLED`.
- **Widths**: ≥ 60 columns gives the full strip; narrower gives one line, `T-12 ⚖︎ Verify 2/5 ◆ opus ⠹`.

### 6.3 Speech bubbles (`ui.SpeechBubble`)

`◆ Leader: “AC3 lacks a tier-2 test.”   T-12 · 2m` is one line in the speaker's colour.

**Sources**:
- verdict summaries (Leader);
- report summaries and questions (Implementer);
- `helper_action` events (Helper);
- `rung_changed`, `quota_blocked`, `scope_change`, `task_done` events (engine).

### 6.4 Splash panel (`ui.SplashPanel`)

`SplashPanel` renders the recorded escalation headline, question and options inside the configured attention view. Home presents the actionable decision in its conditional attention row; task context opens the shared Answer form. The packaged task window has no system pane, and decisions do not create a separate pane or duplicate Home view.

### 6.5 Motion (`dashboard.animations = true`)

Motion is driven by real signals, so the dashboard shows how alive the system is:

| Motion | Meaning |
|---|---|
| Active panel spinner runs only while a stream signal arrived in the last 10 s; otherwise it freezes, and after the stall timeout the panel turns `hold` with `⚠︎` | Working vs idle vs stalled |
| Stamp flash (400 ms reverse video) when a verdict or merge lands | A turn outcome just happened |
| Escalation border pulses red/dim at 1 Hz while open | Needs attention |
| Elapsed time, quota countdowns and cost tick live | Live time and spend |

Motion comes from a pure `frame(state, now)` function with an injected clock, capped at 4 Hz. Setting `animations = false` renders static frames while the configured `refresh_hz` timer still updates state, ages and countdowns. Report, facts and packet evidence load on detail mount or relevant state/file change, not on each motion tick. `NO_COLOR` and `TERM=dumb` use textual state and focus cues so color and animation never carry meaning alone.
