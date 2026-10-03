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
- **Options**, set at the scope tmux gives each one:
  - *Session*: `renumber-windows on`, `mouse on`. Applied on every `start`, not only when the session is created.
  - *Window*, applied to each window as it is created (the home window and every task window): `remain-on-exit on`, `allow-rename off`, `automatic-rename off`, `allow-set-title off`, `pane-border-status top`, `pane-border-lines heavy`.
  - A window option set at session scope reaches only the session's current window, so `remain-on-exit` in particular must be set per window: without it a dead pane disappears instead of staying for the guard (§5.2).
- **Windows**:
  - The permanent home window `shikiru` has `shikiru dash --home` and a shell.
  - Each active task has its own window, named by `titles.window`, built from `dashboard.layout`. It is created at dispatch and closed at a terminal status.
- **Role panes** run `shikiru view T-n ROLE`: a read-only Rich live tail of `runs/T/<role>/view.jsonl` (turn boundaries with step and rung, messages, tool calls, usage, failures) through `ui.StreamLine`. It writes nothing and survives engine restarts. While the role is taken over (03 §9), the pane runs the agent's own TUI instead.

### 5.2 Layout and protection (`LayoutGuard`)

- **Build**: the layout tree is built with `split-window -h|-v -l <size>%`. `{role: pane_id}` is stored in `tasks.panes`.
- **Guard**: every `poll_seconds`, the engine first recreates the session and its home window if the tmux server or session is gone (02 §1), then the guard compares each task window with the DB.
  - A missing pane (killed) is recreated in the layout.
  - A dead `system`, `shell` or view pane is respawned.
  - A dead taken-over pane (the Operator exited the TUI) becomes a `HandBack` (03 §9). A taken-over role whose pane is gone (tmux server loss, host reboot, a killed pane) is rebuilt, never as a read-only view of a held role: `LayoutGuard` appends `takeover_lost` and queues the `HandBack` (03 §9).
  - Each restoration emits `pane_restored`.

  There are no global key-binding overrides, so closing a pane has no lasting effect.
- **Palette key**: one conditional binding. `prefix + Space` opens `shikiru palette` in a popup inside `[shikiru]` sessions and keeps its default (`next-layout`) elsewhere. The binding passes tmux's originating `#{pane_id}` through hidden `--origin-pane`; it runs the popup command through a format-expanded `run-shell` so the pane ID is resolved before shell quoting. The operator layer resolves the current task and role from the project's recorded pane mapping instead of relying on the popup's inherited `TMUX_PANE`.

### 5.3 Titles and decorations

- **Titles** use the `dashboard.titles` templates. Fields:
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

`shikiru dash --home | --task T-n` is a Textual app with one shared navigable shell. The permanent home window opens the Home workspace. A task window opens the same shell with its existing `focus_task` context and the shared task detail fixed to that task.

Home chooses its browser/detail composition from the actual pane size: at 100 columns or more and over 18 rows it shows the browser and one shared detail panel side by side; otherwise it stacks them in a scrollable compact view. The detail panel stays mounted through resize and task selection, preserving browser identity and its local form, timeline and scroll state. Its initial detail result is discarded if selection changes while file evidence is loading. A compact palette keeps the focused form field and Run control reachable by scrolling; three-row text areas retain one editable row between borders at 18 rows or less.

The shell groups current widgets in Workspace (`header`, `escalations`, `strips`, `queue`), Activity (`dialogue`), Usage (`usage`) and Diagnostics (`resources`, `docker`, `faults`). Home composes bounded Attention, Running and Waiting summary controls above the existing Workspace widgets. Running counts `running` and `integrating` tasks with no holds, scheduler wait reason or recorded current-turn quota wait. Waiting counts non-terminal queued, escalated, held, scheduler-waiting or recorded current-turn quota-wait tasks. Attention counts grouped task/project rows, so multiple reasons for one task remain one row. These categories can overlap and are never presented as an additive project total. Occupied slots continue to use `occupied_slot_ids`, including paused tasks and pull-request waits; slot occupancy is not the Running count. Each summary control opens its matching attention or task-browser content.

Usage names its scope as project recorded runs or the selected task's runs. Token totals include explicit `UsageReported` sessions and legacy runs with positive token evidence; all-zero legacy token columns stay unknown, with explicit, legacy and unknown coverage shown. A quota-only agent has unknown token values. Billed and API-equivalent subtotals remain separate; known legacy prices keep a legacy label even when token provenance is unknown, and priced/unknown run counts keep partial subtotals from reading as complete spend. Zero is shown only for a recorded zero. Quota gauges use only reported windows and display the reported reset countdown and `observed_at` age. The project usage and selected-task run aggregates are cached by SQLite `data_version`; motion refreshes only advance countdowns and observation ages.

Diagnostics refreshes host/process-tree resources and Docker at their configured sampling cadence, outside motion callbacks. It labels an empty process tree separately from a measured zero, an absent Docker CLI/daemon as unavailable, and failed samples as failed. After a later failure it keeps the last timestamped observation marked stale and shows the sample error; an observation age always comes from the stored sample timestamp. `faults` lists open engine-fault streaks and their recorded scope, task, error, repeat count and first/latest timestamps. A failed diagnostic source leaves the other panels and task browser available.

`queue` is the selectable task browser. It searches task IDs and titles case-insensitively, combines search with Active, Waiting, History or All, reports the matching count, and loads longer lists in fixed-size bounded pages. Load more reads the loaded page range and the next page from one SQLite snapshot before appending; state updates also refresh the loaded range while retaining the visible reading position. Up/Down moves its selected task and keeps that row visible. `/` focuses search; from another expanded section it returns to Workspace first. Text inputs and modal screens retain `/` as input. Clear and filter buttons are explicit controls. Escape clears a non-empty search first, then moves focus to the list when search is empty. The selected task's compact identity and situation stay visible when a refresh, filter or search removes its row; Reveal clears the search and opens the matching task category. The browser keeps navigation state in the dashboard process and reads task pages and counts through `StateReader`; it writes no state.

Configured widget order is preserved within each section. A configured third-party widget is mounted in Extensions, in its relative configured order, so it remains reachable without changing its entry point or context. `Tab` and `Shift+Tab` move section focus, mouse activation selects a section, `z` expands the focused section, and `Escape` returns to the section list with the previous focus and scroll position restored. The compact footer names the focused or expanded section and provides Expand, Back and Help controls. `?` opens contextual help with all key bindings. Focus is shown by a marker and reverse/bold styling so it remains readable with `NO_COLOR`.

In Home, `Enter` on a selected browser row opens the shared task detail. `Escape` returns to the same browser selection, search and reading position. The detail shows task situation, the semantic strip for its actual mode and step, latest validated agent report and latest validated verdict, request and acceptance criteria, bounded recorded evidence, workspace targets, sessions and usage. A Leader verdict remains available after a later implementation report. An engine override replaces an agent verdict only in the same numeric turn; a newer Leader verdict takes precedence over an older engine override. Each report and verdict shows its recorded source. Missing or unreadable report/facts paths do not suppress independent role signals, and basic SQLite task state remains visible. Completed and cancelled tasks remain inspectable after ordinary worktree/window cleanup while retained run files and events exist; detail shows stored status/reason, available reports, decisions, integration events and engine-computed git facts. A recovered `turn_failed` event remains timeline evidence and never changes the task's terminal status. Detail adds blocker evidence from the stored scheduler reason and its available records, plus a collapsible family and relationship group. Relationship pages show their current slice and provide Previous and More controls, disabled at the first and last page. Related-task navigation reuses the existing detail callback and leaves Home's selected task identity unchanged. Submitted and recorded requeued ages use their own stored timestamps; the dashboard does not derive current-blocker duration, an ETA or a guaranteed start. Report and git-fact reads run away from the UI loop; lightweight timeline refreshes read only the bounded page and do not reload those files. Timeline reads preserve engine event IDs and per-file signal order, isolate a failed role source as visible evidence, and leave task state available. Following latest pauses when the Operator scrolls away from the timeline tail; page changes and incoming rows preserve the reading position and selected evidence identity, and Follow latest resumes it explicitly. The mounted state subscription is cancelled when detail closes.

The shared detail refreshes basic task and ownership state after an overlay closes or the compact detail becomes visible again. Motion refreshes leave timeline pages on their own clock and keep report, facts, packet text and parsed packet evidence cached until their file identity or content changes. Normalized signal tails remain a bounded live observation. Selection changes invalidate the whole pending detail result so old task evidence cannot overwrite the selected task.

`dash --task T-n` fixes the detail to `T-n`. Its Activity and Usage sections are scoped to that task and its situation includes a compact global decision/fault count. The task view omits project-wide header, queue, escalations, strips and diagnostics panels. Configured third-party widgets remain in Extensions. Detail actions select only existing task windows or role panes through activation-time state reads; missing and terminal targets are reported without creating workflow resources. The actions button opens the shared palette with the task and selected role. Home browser selection and `Ctrl+P` carry their current task identity through the additive `WidgetContext` callbacks; old callback fields remain available.

```python
class DashboardWidget(Protocol):
    """A system-pane panel plugin (entry point group `shikiru.widgets`)."""
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

`WidgetContext.open_palette` remains available for existing widgets. `open_palette_context` carries an optional action, task ID and role to the shared screen; `on_task_selected` reports browser identity to the dashboard. The dashboard owns current navigation state and exposes no services or typed workflow commands to widgets.

Widgets re-read their view when `on_change` fires. Samplers such as resources and docker have their own interval.

| Section | Widget | Content |
|---|---|---|
| Workspace | Home summary | Attention group count, Running and Waiting task counts; each activates the corresponding content |
| Workspace | `header` | Project, engine lock up/down, slots `2/2`, integration mode, checkout branch/ahead/behind/dirty count and repository sample age (cached git data), then recorded fault and oldest-escalation context |
| Workspace | `escalations` | One row per task/project with grouped decisions, diagnosed stalls and recorded open faults; decisions retain creation-time/ID order, `r` opens resolution and `o` opens evidence or its explicit read error |
| Workspace | `strips` | Active tasks as strips (§6.2); the focused task expanded; children grouped under `parent_id` |
| Workspace | `queue` | Searchable, selectable task browser: Active, Waiting, History and All; queued tasks use dispatch order, history uses latest finish first, and selected task context survives refresh and filtering |
| Activity | `dialogue` | Last 20 speech bubbles (§6.3); `Enter` shows the full text |
| Usage | `usage` | Per agent: tokens; cost with an "API-equivalent" label when applicable, `–` when unknown; quota gauges with reset countdown and observation age, only for reported windows |
| Diagnostics | `resources` | Host CPU%/RAM, and this instance's CPU%/RSS (engine plus the process trees of all panes), sampled every 2 s |
| Diagnostics | `docker` | Containers with CPU% and memory (`docker stats --no-stream --format json` every 5 s); hidden when Docker is unavailable |
| Extensions | Configured third-party widgets | Each configured widget outside the built-in set, using the existing plugin entry point and context |

The system-pane shell uses current widgets. The selectable task browser and shared task detail are shipped through the existing widget and dashboard contracts. `TaskNavigation` resolves the task's recorded window or pane from current state when activated and selects only that existing target. Before selecting a role pane, it checks that the pane still belongs to the task's recorded window. A terminal task remains inspectable after its window and worktree have been cleaned up.

### 5.5 Palette

- `PaletteApp` offers searchable actions and task targets searched by ID or title through bounded pages with visible counts and a More control. It keeps an editable task ID and role, reads current stored state on each search, and retains the selected target outside the current match. Context comes from selected detail/browser identity or from the originating role pane in the tmux popup; a home popup has no preselected task. Advanced submission fields, including submission and role rung overrides, remain available in an expandable area.
- The operator layer prepares task-action applicability and escalation-resolution availability from current durable state. The palette refreshes this projection before dispatch; the engine can still reject a command if state changes after that read. Widgets call `OperatorCommands` and do not create workflow commands or duplicate eligibility rules.
- Forms explain next-turn delivery for messages and rung changes, pause versus pause-now, task ownership during take over, and restrictions remaining after resume or hand back. Cancel and stop show their scope and work-preservation effects before the form's explicit Run action.
- Resolve opens the exact selected escalation ID. It shows the Leader's question, recommended agent alternative and its consequences separately from the engine resolution kinds and their consequences. The palette derives allowed kinds from the durable row, requires answer text, and accepts an optional valid retry rung. Timeline, reports, facts, stream tails and the unchanged full packet stay available in collapsed evidence sections; expanded content scrolls with the form. A malformed or unavailable packet shows its evidence error while keeping the durable escalation and allowed kinds visible; malformed text remains available as the original packet.
- The mounted palette refreshes the selected escalation as another Operator resolves or cancels it. It disables stale submission and keeps entered guidance for copying; a race after submission is shown from the engine's rejection without creating a replacement command.
- It opens as a tmux popup (`prefix + Space`), and `Ctrl+P` in `dash` pushes the same screen.
- Every mutation calls `OperatorCommands`. The mounted palette observes durable state changes and shows `Sending`, then `Pending`, `Applied` or `Rejected`, with command ID, target and result. A completed pending form becomes available again when its durable row changes status. Pending duplicate checks use the exact typed payload so switching forms and reopening the palette cannot queue the same still-pending command again. **Applied** means the engine applied the command effect. For `say`, guidance is queued for the selected role's next turn; this status does not claim that an agent consumed it.
- The dashboard keeps one compact latest-command summary under its title and opens a paginated project history. Each history page contains at most 50 rows; the UI shows 20 at a time and offers newer/older navigation, left/right key bindings and status filters so older pending outcomes remain reachable. Full results and provenance stay in history rather than expanding workspace chrome. The palette, history and workspace summary read through `StateReader` and `OperatorQueries`, refresh from database changes, and recover commands submitted through another dashboard or MCP. Pending rows report current engine liveness without guessing whether their original client saw an engine-down queue or an acknowledgement timeout.
- The dashboard and popup use the shared Textual theme. Packet and dialogue evidence modals use its surface, border, text and focus tokens.

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

Shikiru's look comes from **structure and meaning**:
- tasks are 4-panel comic strips (*yonkoma*);
- key moments are speech bubbles;
- escalations are splash panels.

Every colour, glyph and movement answers one of three questions: *who acts*, *what state*, *how alive*. English carries all meaning; kanji are decorative marks. Everything lives in `ui` and is shared by CLI and dashboard.

### 6.1 Tokens and glyphs

**Colours**: Rich renderables keep the existing semantic role/state palette; the Textual shell, palette and evidence modals share named `background`, `surface`, `neutral-border`, `primary-text`, `secondary-text` and `focus` tokens. Truecolor, with 256/16 fallback; `NO_COLOR` switches to monochrome with bold, dim and reverse. Role and state colors keep their existing meanings.

| Token | Colour | Meaning |
|---|---|---|
| `leader` | violet `#a78bfa` | The Leader acts |
| `implementer` | cyan `#22d3ee` | The Implementer acts |
| `helper` | sand `#e0b872` | The Helper acts |
| `operator` | magenta `#f472b6` | Operator input or intervention |
| `engine` | silver `#cbd5e1` | Engine decisions |
| `success` | green `#4ade80` | Accepted, merged, done |
| `rework` | orange `#fb923c` | Changes requested, retries |
| `hold` | yellow `#facc15` | Paused, quota, waiting |
| `alert` | red `#f87171` | Escalation, failure |
| `idle` | grey `#64748b` | Pending, queued |
| `background` | dark navy | Application background |
| `surface` | blue-grey | Ordinary panel surface |
| `neutral-border` | slate | Section and modal edges |
| `primary-text` | pale grey | Main text |
| `secondary-text` | muted grey | Supporting text |
| `focus` | near-white | Keyboard and mouse focus |

Gauges (context, quota, CPU, RAM) are green below 70%, yellow below 90%, red above.

**Glyphs**: one set, registered in `ui.glyphs`. A unit test rejects wide and emoji-presentation characters. Ambiguous-width characters are allowed and drawn one cell wide, as Textual assumes; terminals set to CJK ambiguous-wide are unsupported. Glyphs that have an emoji variant carry U+FE0E (`⚖︎ ⚙︎ ⏸︎ ⚠︎`).

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

- A red panel framed by `╱` speed lines.
- It holds the headline, the Leader's question, and numbered options with ★.
- It is shown at the top of the system pane in every window while the escalation is open.

### 6.5 Motion (`dashboard.animations = true`)

Motion is driven by real signals, so the dashboard shows how alive the system is:

| Motion | Meaning |
|---|---|
| Active panel spinner runs only while a stream signal arrived in the last 10 s; otherwise it freezes, and after the stall timeout the panel turns `hold` with `⚠︎` | Working vs idle vs stalled |
| Stamp flash (400 ms reverse video) when a verdict or merge lands | A turn outcome just happened |
| Escalation border pulses red/dim at 1 Hz while open | Needs attention |
| Elapsed time, quota countdowns and cost tick live | Live time and spend |

Motion comes from a pure `frame(state, now)` function with an injected clock, capped at 4 Hz. Setting `animations = false` renders static frames while state, ages and countdowns continue to refresh. `NO_COLOR` and `TERM=dumb` use textual state and focus cues so color and animation never carry meaning alone.
