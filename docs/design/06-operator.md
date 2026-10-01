# 06 · Operator Interfaces

## 1. Operator layer

CLI, MCP and the dashboard palette are thin adapters over three small services:

| Service | Responsibility |
|---|---|
| `OperatorQueries` | Read-only views over `StateReader`: status, tasks, task detail, escalations (with packet), peek (DB + the role's `view.jsonl`) |
| `OperatorCommands` | `send(command) -> Ack`. Inserts the typed command row; if the engine is up, waits up to 10 s for `done`/`rejected`; if it's down, returns `queued` ("applies when the engine starts"). Delivery is at least once and handlers are idempotent by command id (03 §7), so a command applied just before an engine restart is acknowledged `done`, never `rejected`, when replayed |
| `EventFeed` | `read(since, kinds, task)` and `wait(since, kinds, task, timeout)` over `events` |

## 2. CLI

- **Global options**: `--project PATH` (default: nearest ancestor with `.shikiru/`), `--json`, `--set k=v`.
- **Exit codes**: `0` ok, `1` error, `2` usage, `3` not initialised, `4` command rejected.

The CLI gets an Operator into a project, in and out of its session, and back out again. It is not a second copy of the operator surface: **every mutating action except `submit` lives in the dashboard palette (§5.5) for humans and in MCP (§4) for AI Operators and scripts.** One action, one adapter.

| Command | Purpose |
|---|---|
| `init [--yes]` | Wizard (§3); `--yes` writes the detected defaults |
| `start [--attach]` | Validate config and plugins, start the engine, ensure the session. On a running engine it only ensures the session. A name collision with another project fails with a hint to set `project.name` |
| `stop [--kill-session]` | Stop the engine; running turns are stopped (their sessions persist) and re-run by resume on the next start |
| `ls [--host H]` | Every Shikiru project on this host, or on `H` (§5.6): name, directory, engine state, running and queued counts, open escalations |
| `attach [NAME] [--host H]` | Attach to a session; with no `NAME` and more than one match, the picker (§5.6) |
| `submit "<request>" [flags]` | Prints the `T-n` id (05 §1 flags) |
| `status [T-n] [--watch]` | Without an id: engine, slots, active tasks with `wait_reason`, queue, open escalations, usage and quota per agent. With one: that task's strip, timeline, reports and verdicts, branches and sessions. `--watch` redraws on `StateReader.on_change` |
| `doctor [--probe]` | Environment, repository-root (D20), config and plugin checks; `--probe` validates the configured ladders (04 §3): live contract check 1 on each agent's first rung and a smoke turn on every other distinct rung, showing the stream tail and stderr when a probe fails |
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
7. **Finish**: write `.shikiru/config.toml`, add `.shikiru/` to `.git/info/exclude`, run `doctor`. `init --yes` writes the editable `default` model placeholder, so review the role ladders and run `doctor` before `start`.

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
  - A dead taken-over pane (the Operator exited the TUI) becomes a `HandBack` (03 §9).
  - Each restoration emits `pane_restored`.

  There are no global key-binding overrides, so closing a pane has no lasting effect.
- **Palette key**: one conditional binding. `prefix + Space` opens `shikiru palette` in a popup inside `[shikiru]` sessions and keeps its default (`next-layout`) elsewhere.

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

### 5.4 System pane and widgets

`shikiru dash --home | --task T-n` is a Textual app that stacks the `dashboard.widgets` panels in configured order.

```python
class DashboardWidget(Protocol):
    """A system-pane panel plugin (entry point group `shikiru.widgets`)."""
    name: str
    def create(self, context: WidgetContext) -> textual.widget.Widget: ...

@dataclass(frozen=True)
class WidgetContext:  views: DashboardViews; focus_task: str | None
                      on_change: Callable[[OnChange], Awaitable[None]]   # StateReader.watch
                      open_palette: Callable[[str], Awaitable[None]]

class DashboardViews(Protocol):
    """Ready-to-render view models; the only data a widget sees."""
    async def header(self) -> HeaderView: ...            # project, engine up, slots, checkout (git cached 10 s)
    def strips(self) -> list[TaskStripData]: ...         # active tasks, focused first, children under parents
    def queue(self) -> list[QueueRow]: ...               # dispatch order
    # escalations(), dialogue(), usage(), resources(), containers() arrive with their widgets
```

**Views are the widget API.** `dashboard.views` builds them from `StateReader`, `Settings` and `domain` rules (`pick_rung`, dispatch order, the acting role of a step). A widget only renders: it never sees the state DB, domain types, config or tmux, so an external widget depends on `dashboard.contract` and `ui` alone. View models are frozen dataclasses in `dashboard.contract` (`TaskStripData` is `ui`'s). They may gain fields, but never lose or change them.

Widgets re-read their view when `on_change` fires. Samplers such as resources and docker have their own interval.

| Widget | Content |
|---|---|
| `header` | Project, engine up/down, slots `2/2`, and the checkout's branch, ahead/behind `base` and dirty count (git data cached 10 s) |
| `escalations` | Splash panel per open escalation (§6.4); `r` resolves through the palette form, `o` opens the packet |
| `strips` | Active tasks as strips (§6.2); the focused task expanded; children grouped under `parent_id` |
| `queue` | Queued tasks in dispatch order: priority, dependencies, `wait_reason` |
| `dialogue` | Last 20 speech bubbles (§6.3); `enter` shows the full text |
| `usage` | Per agent: tokens; cost with an "API-equivalent" label when applicable, `–` when unknown; quota gauges with reset countdown and observation age, only for reported windows |
| `resources` | Host CPU%/RAM, and this instance's CPU%/RSS (engine plus the process trees of all panes), sampled every 2 s |
| `docker` | Containers with CPU% and memory (`docker stats --no-stream --format json` every 5 s); hidden when Docker is unavailable |

### 5.5 Palette

- `PaletteApp` offers: submit (complexity defaults to "Triage decides"), prioritise, pause/resume, take over (then select that pane) / hand back, cancel, retry, change rung, message agent, peek, resolve escalation (a form with the packet and options), stop engine.
- It opens as a tmux popup (`prefix + Space`), and `Ctrl+P` in `dash` pushes the same screen.
- Every action calls `OperatorCommands` and shows the `Ack`.

### 5.6 `ls` and `attach`

`SessionLister` returns one row per project, for the local host or for `H`; `ls` prints the rows, `attach` picks one from them.

- **Rows**: `tmux list-sessions` filtered by the `[shikiru] ` prefix and each session's `SHIKIRU_PROJECT_DIR`; engine liveness comes from the project's `engine.lock`, while running, queued and escalated counts come from its DB. `ls` opens each DB with SQLite read-only mode and never changes its schema, progress or other project state. SQLite may create or update WAL and shared-memory coordination sidecars while reading a WAL database; a newly created WAL file is empty, and existing database or WAL contents remain unchanged.
- **`attach`**: exactly one row, or a matching `NAME` → `os.execvp("tmux", ["tmux", "attach", "-t", "=[shikiru] NAME"])` (`switch-client` when inside tmux). Otherwise an inline selectable list of the same rows.
- **Remote** (`--host H`, on both): `ssh H shikiru ls --json` gives the rows; `attach` then runs `os.execvp("ssh", ["ssh", "-t", H, shlex.join(["tmux", "attach", "-t", "=[shikiru] NAME"])])`. `--host` needs nothing installed locally but `ssh`, and nothing remote but `shikiru`.

### 5.7 Notifications

`Notifier`, on `escalation`, `task_done`, `integration_waiting` (a local merge waits for a dirty checkout, 03 §6), `engine_fault` (the first fault of a failing tick streak, 02 §1) and an `engine_stopped` that carries a `reason` (the engine stopped on a fault rather than on request):
- runs `tmux display-message` in the session;
- runs `notify.command`, when set, with the event JSON on stdin (for Slack, ntfy or a remote AI Operator). A non-zero exit, a timeout (`notify.timeout_seconds`, 30 s by default) or a missing command is logged with the event id and recorded as an `engine_fault` of scope `notification hook`, once per streak (02 §1).

Delivery never runs on the command loop. The `Engine` queues each notifiable event on `NotificationDelivery`, whose single task delivers them one at a time, so a hung hook cannot delay `stop`, `pause` or scheduling. Events have two priorities: *urgent* (`escalation`, `engine_fault`, an `engine_stopped` with a reason) and *informational* (`task_done`, `integration_waiting`):
- urgent events are delivered before informational ones, in order, and are never dropped, so a stuck hook (killed after `notify.timeout_seconds`) delays but cannot silence an escalation or a fault;
- the informational backlog holds 64 events; when full, the oldest informational event is dropped with a logged warning;
- a delivery failure is logged and the next event is delivered;
- the engine loop supervises the task like the task flows: an unexpected failure is logged and the task restarted;
- a hook runs in its own process group, which is SIGKILLed on its timeout and on cancellation, so no descendant it started outlives the hook (03 §4.2);
- on engine stop the task is cancelled, which terminates a running hook, and pending events are dropped, so `stop` never waits for a hook. The one exception is the `engine_stopped` event of a fault stop: it is delivered directly, bounded by `orchestration.faults.stop_notify_seconds`.

### 5.8 Keeping the engine running

`shikiru start` spawns the engine detached; nothing restarts it if the process dies (a fault stop, an OOM kill, a reboot). To keep it running, run the hidden entrypoint under a service manager and restart it on failure. A restarted engine reconciles persisted state (03 §7) and recreates the tmux session (02 §1). Give the service the `PATH` your agent CLIs, `git`, `gh` and `tmux` need. `shikiru start` reuses a running engine, so it remains the way to ensure the session and attach.

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
</dict></plist>
```

`Restart=on-failure` and `SuccessfulExit=false` restart the engine after a fault stop (non-zero exit) but not after `shikiru stop` (exit 0).

## 6. Visual design: comic strips with a semantic visual language

Shikiru's look comes from **structure and meaning**:
- tasks are 4-panel comic strips (*yonkoma*);
- key moments are speech bubbles;
- escalations are splash panels.

Every colour, glyph and movement answers one of three questions: *who acts*, *what state*, *how alive*. English carries all meaning; kanji are decorative marks. Everything lives in `ui` and is shared by CLI and dashboard.

### 6.1 Tokens and glyphs

**Colours**: one palette, rendered on the app's own dark background. Truecolor, with 256/16 fallback; `NO_COLOR` switches to monochrome with bold, dim and reverse.

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

Motion comes from a pure `frame(state, now)` function with an injected clock, capped at 4 Hz. Setting `animations = false` renders static frames.
