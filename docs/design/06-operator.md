# 06 · Operator Interfaces

All interfaces call the same `OperatorService` use cases. CLI, MCP and the dashboard palette are thin adapters over it.

## 1. CLI

Global options:
- `--project PATH|NAME` (default: the nearest ancestor with `.shikiru/`). A NAME is resolved through the `SHIKIRU_PROJECT_DIR` environment of the running `[shikiru] NAME` session.
- `--json` (machine output, no colour, and no spinner on stdout)
- `--set k=v`
- `-q`

Exit codes: `0` ok, `1` error, `2` usage, `3` not initialised, `4` engine down, when the command needs the engine.

| Command | Purpose |
|---|---|
| `init [--yes]` | Wizard (§2). `--yes` accepts all detected defaults |
| `start [--attach]` | Start the engine daemon and the tmux session. Report config errors. `--attach` attaches afterwards |
| `stop [--force]` | Stop the engine gracefully (current turns keep running in panes and are reconciled on the next start). `--force` also kills the tmux session |
| `attach [--host H] [--project P]` | §5.5 |
| `ls [--host H]` | List Shikiru sessions: name, project path, engine state, active and queued counts |
| `status` | Engine, slots, active tasks, holds, open escalations, quotas |
| `submit "<request>" [flags]` | Enqueue a request (05-data §1 flags). Prints `T-n` |
| `queue` / `tasks [--all]` / `show T-n` | Queue, tasks, task detail (timeline, runs, turns, reports, cost, branches) |
| `prioritize T-n (--priority N / --top / --bottom / --before T-m)` | Re-prioritise a queued task |
| `pause T-n [--now]` / `resume T-n` / `cancel T-n` / `retry T-n [--step S]` | Task control; always scoped to the named task only |
| `rung T-n <role> <agent:model:effort>` | Model override from the next turn |
| `say T-n --to leader/implementer "<text>"` | Message an agent session |
| `peek T-n --role R [--lines N]` | Last assistant message, pane screen, usage |
| `sessions T-n` | Session ids and resume commands (from each plugin's `resume_hint`) |
| `escalations [--all]` / `resolve E-n <option> [text] [--rung a:m:e]` | Escalations |
| `events [--follow] [--since ID] [--task T-n]` | Event stream (NDJSON with `--json`) |
| `usage` | Tokens, cost and quotas by agent, role and task |
| `doctor [--probe]` | Environment and config checks. `--probe` runs 04-agents §5 |
| `config show/set` | 05-data §1 |
| `mcp` | Stdio MCP server (§3) |
| internal (hidden): `engine`, `dash`, `hook`, `report`, `palette`, `tmux-event`, `idle` | Process entrypoints |

**Human output**:
- Rich panels, tables and trees, truecolor with a 256-colour fallback. `NO_COLOR` and non-TTY output are respected.
- OSC 8 hyperlinks for files and PR URLs.
- `rich.status` spinners and progress for long steps (init discovery, start, doctor probes).
- Commands that change state wait for the engine's acknowledgement (§4) and show the result.

## 2. `shikiru init` wizard

The wizard is a Textual app in inline mode (`App.run(inline=True)`). Every step is prefilled with a detected default.

1. **Repos**: if cwd is a git work tree → monorepo `path="."`. Otherwise scan subfolders (depth ≤ 4, skipping ignored and heavy dirs) for `.git`. Multi-select. Record the current branch of each as `base`.
2. **Agents**: detect `claude` and `codex` on PATH, with version and login state (`claude auth status`, `codex login status`). Choose ladders for leader, implementer and helper. Model lists come from agent caches (`~/.codex/models_cache.json`) plus aliases.
3. **Trackers**: glob `tasks/*.md`, `docs/**/plan*.md`, `**/todo*.md` → pick.
4. **Skills prefix**: detect installed skills that match `using-*` in `~/.claude/skills`, plugins and `~/.codex/skills` → suggest the per-agent `prompt_prefix` (empty allowed).
5. **Caches**: list existing ignored dirs among the known cache names → select. Check copy-on-write support.
6. **Orchestration**: `max_parallel` (default `min(2, cpu/4)`), integration mode.
7. **Write** `.shikiru/config.toml`, add `.shikiru/` to `.git/info/exclude`, run `doctor` (without probe) and offer `doctor --probe`.

## 3. MCP server (`shikiru mcp`)

- Stdio transport (official `mcp` SDK, `FastMCP`). The project comes from `--project` or cwd.
- Remote use: `ssh host shikiru mcp --project ferrum`. For a remote host, the Operator's agent config is: `{"command": "ssh", "args": ["host", "shikiru", "mcp", "--project", "ferrum"]}`.

Tools map 1:1 to `OperatorService`, with typed pydantic inputs and outputs:

| Tool | Notes |
|---|---|
| `status`, `list_tasks`, `get_task`, `list_escalations`, `get_escalation` (full packet), `usage` | read |
| `submit_task`, `prioritize_task`, `pause_task`, `resume_task`, `cancel_task`, `retry_task`, `set_rung` | write |
| `send_message`, `peek_session`, `list_sessions` | session access and intervention |
| `resolve_escalation` | write |
| `wait_for_events(since_id, kinds?, task_id?, timeout_s ≤ 300)` | long-poll. Lets an AI Operator block until an escalation or completion arrives |

Resources: `shikiru://escalations/{id}` (packet markdown) and `shikiru://tasks/{id}/summary`.

## 4. Command acknowledgement

1. `OperatorService` inserts a `commands` row.
2. If the engine is up, it polls for `status != pending` for up to 10 s, showing a spinner. The engine checks commands every 250 ms.
3. `rejected` returns the engine's reason, e.g. "T-5 is not queued".
4. If the engine is down:
   - Queue-only commands (`submit`, `prioritize`, `cancel` of queued tasks) are applied directly by the service against the DB, which is safe because no engine is writing, and it says so.
   - Other commands fail with exit code 4.

## 5. Dashboard

### 5.1 tmux session

**Name**:
- `[shikiru] <project.name>`, with `.` and `:` replaced by `_` (tmux forbids them).
- Always targeted exactly as `-t '=[shikiru] name'`.
- The session environment stores `SHIKIRU_PROJECT_DIR` so that `ls` and `attach` can find the project.
- **Name collision**: if a session with the same name already exists for a different `SHIKIRU_PROJECT_DIR`, `start` fails and suggests setting `project.name`. This lets many instances run side by side.

**Session options** set by the engine:
- `remain-on-exit on`, `renumber-windows on`, `allow-rename off`, `automatic-rename off`
- `pane-border-status top`, `pane-border-lines heavy`, `pane-border-format " #[fg=#{@shk_color}]#{@shk_glyph} #{pane_title} "` (per-pane `@shk_*` options, §7.1)
- `window-status-format` / `window-status-current-format` use the `@shk_state_glyph` and `@shk_state_color` window options
- `allow-set-title off`: agents can't overwrite titles.
- `allow-passthrough on`: notifications.
- `mouse on`, `status-style` from the theme
- Session hook `pane-died` → `run-shell -b "shikiru tmux-event pane-died #{pane_id}"`

**Windows**:
- Each active task has a window named by `titles.window`, e.g. `T-12 Add retry to parser`.
- When there are no task windows, a single window named `shikiru` exists.
- Its leader and implementer panes run `shikiru idle <role>`, which shows an idle panel (§7.6), and its shell pane is a normal shell.

**Window transitions** (a tmux session must never reach zero windows):
- First task starts → create the task window, then kill the `shikiru` window.
- Last task ends → create the `shikiru` window, then kill the task window.

**Pane protection**:
1. `remain-on-exit` together with the `pane-died` hook. The engine respawns `system` and `shell` immediately and handles agent panes through 03-workflow §8.3.
2. The engine installs these key bindings idempotently at start. They only change behaviour inside `[shikiru]` sessions:

   ```
   bind-key x if-shell -F '#{m:[[]shikiru[]]*,#{session_name}}' 'display-message "Protected pane (use shikiru stop --force)"' 'confirm-before -p "kill-pane #P? (y/n)" kill-pane'
   bind-key & if-shell -F '#{m:[[]shikiru[]]*,#{session_name}}' 'display-message "Protected window (use shikiru stop --force)"' 'confirm-before -p "kill-window #W? (y/n)" kill-window'
   bind-key Space if-shell -F '#{m:[[]shikiru[]]*,#{session_name}}' 'display-popup -E -w 70% -h 60% "shikiru palette"'
   ```

3. `shikiru stop --force` is the only supported way to close the session.

**Layout**:
- `LayoutBuilder` walks the `dashboard.layout` tree. The first pane of each split is the existing pane. Then it runs `split-window -h|-v -l <size>% -t <pane>` for siblings.
- It returns `{role: pane_id}`, which is stored in `tasks.window_id` and `runs.pane_id`.
- Unknown pane names in the config are rejected by validation.
- The fixed pane roles are `system`, `leader`, `implementer` and `shell`. Omitting `shell` is allowed.

### 5.2 Titles

- The engine sets titles with `select-pane -T` on every change it observes: rung, iteration, usage every `refresh_seconds`, holds.
- Fields available for templates: `task_id`, `title`, `project`, `role`, `agent`, `model`, `effort`, `iteration`, `max_iterations`, `step`, `status`, `context_free`, `cost`, `tokens_in`, `tokens_out`, `elapsed`, `active`, `queued`, `max_parallel`, `cwd`, `flags`.
- `flags` is a short English status with its §7.1 glyph: `⏸︎ paused`, `⌨ operator`, `⧗ quota`, `‼ escalated`, `⚠︎ stalled`. Border colour follows the same state.

### 5.3 System pane (`shikiru dash --task T-n | --idle`)

A Textual app. Widgets come from `dashboard.system.widgets`, in order, each in a collapsible titled panel. The widget contract:

```python
class DashboardWidget(Protocol):
    """A dashboard panel plugin, registered under the `shikiru.widgets` entry point group."""
    name: str
    title: str
    refresh_seconds: float | None                 # None = refresh on state change only
    def create(self, ctx: WidgetContext) -> textual.widget.Widget: ...

@dataclass(frozen=True)
class WidgetContext:
    project: ProjectInfo; state: StateReader; settings: Settings
    options: dict[str, Any]                        # [dashboard.widgets.<name>] table
    focus_task: str | None
```

`StateReader` provides read-only queries, and `on_change` fires when `PRAGMA data_version` changes (polled every 500 ms).

| Widget | Content | Source |
|---|---|---|
| `project` | Project path, and repos with current branch, ahead/behind of `base` and dirty count; active task branches | `git` via `workspace.git` (cached 10 s) |
| `strips` | Active tasks as comic strips (§7.2): the focused task expanded, split children nested, holds as stamps, cost per task | DB |
| `queue` | "Next up" thumbnails in dispatch order with priority, dependencies and the reason each is waiting | DB + `Scheduler.explain()` |
| `escalations` | Open escalations as splash panels (§7.4) | DB |
| `usage` | Tokens (input, cache, output) and cost per agent since engine start, as reported by each plugin's normalized `Usage` (labelled "API-equivalent" when `cost_basis` says so); quota bars per window with reset countdown and observation age | DB `runs`, `quotas` (engine stores plugin values) |
| `resources` | Host CPU%, RAM used/total; this instance's CPU% and RSS, summed over the engine plus all tmux pane process trees in the session | `psutil` (sampled every 2 s) |
| `docker` | Containers: name, CPU%, memory; containers whose compose `working_dir` is inside `.shikiru/worktrees/T-n` are tagged with the task | `docker stats --no-stream --format json` and `docker ps --format json` (every 5 s, hidden if Docker is unavailable) |
| `dialogue` | Dialogue feed (§7.3), last 20 lines | DB events and reports |

The command palette (§5.4) is always available.

### 5.4 Command palette

- Opened with `Ctrl+P` inside the system pane (Textual command palette) or `prefix + Space` anywhere in a Shikiru session (tmux popup running `shikiru palette`).
- Actions: submit request (multi-line editor), prioritise, pause/resume, intervene (pauses the focused task, then selects its agent pane), cancel, retry step, change rung, message agent, peek, resolve escalation (a form showing the packet with option radio buttons and answer text), open event log, stop engine.
- Every action calls `OperatorService` and shows the acknowledgement as a toast.

### 5.5 Attach

- **Local**: `attach` lists `tmux list-sessions -F '#{session_name}\t#{session_windows}\t#{session_attached}'` filtered by the `[shikiru] ` prefix. If there is exactly one match, or `--project` is given, it attaches directly. Otherwise it shows an inline Textual selectable list.
- **Attach itself**: `os.execvp("tmux", ["tmux", "attach", "-t", "=[shikiru] name"])`, or `switch-client` when already inside tmux.
- **Remote**: `ssh <host> shikiru ls --json` → list → `os.execvp("ssh", ["ssh", "-t", host, shlex.join(["tmux", "attach", "-t", "=[shikiru] name"])])`. The remote command is shell-quoted because ssh joins its arguments. The host is anything `ssh` accepts (aliases from `~/.ssh/config`).
- No timeout wraps an attach.

## 6. Notifications

`Notifier` handles the `notify.events` kinds:
1. **tmux**: `display-message` to clients of the session, plus a red status-bar flag for escalations.
2. **Desktop** (`notify.desktop`): OSC 777 / OSC 9 through tmux passthrough to the attached terminal; otherwise `terminal-notifier` (macOS) or `notify-send` (Linux) when present.
3. **`notify.command`**: event JSON on stdin (for Slack, ntfy, email or remote AI Operators).

## 7. Signature design: comic strips (`dashboard.style = "comic"`)

Shikiru is recognisable by **structure, not colours**. The state of the work is drawn as a comic page: every task is a **4-panel strip** (a Japanese *yonkoma*), key moments are **speech bubbles**, and escalations are **splash panels**.
**English is the primary language** of every label and message. Kanji appear only as small decorative marks, never as the only carrier of meaning.

Everything is built from Rich renderables in `shikiru.comic`, so the CLI (`status`, `show`, `escalations`) and the dashboard draw the same pictures. That is one implementation, not two. Only Unicode box-drawing and block characters are used, so it works in any terminal, inside tmux and over SSH.

### 7.1 Visual language

The visual language is a **system with meaning**. Every colour, symbol and movement answers one of three questions: *who is acting*, *what state is this in*, *how alive is it right now*. The comic strip is the structure; this language fills it.

#### Colour (semantic tokens)

Truecolor, with automatic fallback to 256 and 16 colours. The tokens are defined in `theme.tcss` / `comic.theme` and can be overridden in `[dashboard.colors]`.

| Token | Default (dark / light) | Meaning, used consistently everywhere |
|---|---|---|
| `role.leader` | violet `#a78bfa` / `#6d28d9` | The Leader acts: its strip panels, its pane border, its speech bubbles |
| `role.implementer` | cyan `#22d3ee` / `#0e7490` | The Implementer acts |
| `role.helper` | amber-sand `#e0b872` / `#92400e` | The Helper acts |
| `role.operator` | magenta `#f472b6` / `#be185d` | Operator input, intervention, messages |
| `role.shikiru` | silver `#cbd5e1` / `#334155` | Engine decisions |
| `state.active` | the acting role's colour, pulsing | Work in progress |
| `state.success` | green `#4ade80` / `#15803d` | Accepted, merged, done |
| `state.rework` | orange `#fb923c` / `#c2410c` | Changes requested, retries |
| `state.hold` | yellow `#facc15` / `#a16207` | Paused, quota, resources, integration wait |
| `state.alert` | red `#f87171` / `#b91c1c` | Escalation, failure, crash |
| `state.idle` | grey `#64748b` / `#94a3b8` | Pending, queued, inactive |

**Gauges use a continuous scale** (context used, quota used, CPU, RAM, budget): green → yellow at 70% → red at 90%, interpolated in OKLCH so the steps look even.

**The same colours drive tmux**:
- **Pane borders**: each agent pane carries `@shk_color` and `@shk_glyph` pane options, and `pane-border-format` renders `#[fg=#{@shk_color}]`. The pane border lights up in the role colour while that role acts, turns yellow on a hold, turns red on an escalation, and dims when idle. This is verified on tmux 3.7.
- **Window list**: each task window's status-bar entry shows its current state glyph in the state colour, e.g. `1 ⚒ T-12` cyan and `2 ‼ T-14` red. This comes from the `@shk_state` window options.

**Accessibility**: colour never carries meaning alone. Each state also has a glyph, a border style and a word. `NO_COLOR` gives monochrome with bold, dim and reverse styles.

#### Symbols (glyph vocabulary)

A single registry `comic.glyphs` maps meanings to glyphs. Two sets exist:
- `unicode` (default): works with any font;
- `nerd`: Nerd Font icons, chosen in the init wizard from a live preview.

A unit test rejects any `unicode` glyph that is double-width, ambiguous-width or emoji-presentation (the check that excludes `☰`), so alignment never breaks.

| Meaning | `unicode` | `nerd` |
|---|---|---|
| Triage / Plan / Implement / Verify / Merge / PR | `⌕` `≡` `⚒` `⚖︎` `⑂` `⇪` | magnify, list, hammer-wrench, scale-balance, source-merge, source-pull |
| Leader / Implementer / Helper / Operator / Shikiru | `◆` `▲` `✦` `⌨` `⚙︎` | crown, hammer, lifebuoy, keyboard, cog |
| Running / done / rework / consult | spinner `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` / `✔` / `↺` / `?` | spinner / check / refresh / help |
| Paused / quota wait / resources / operator / stalled / escalated | `⏸︎` `⧗` `▥` `⌨` `⚠︎` `‼` | pause, timer-sand, memory, keyboard, alert, alert-octagon |
| Dependency / blocked by | `⤷` / `⊘` | arrow-down-right, cancel |
| Context gauge | `○ ◔ ◑ ◕ ●` (0/25/50/75/100%) | circle-slice-1..8 |
| Tokens / cost / time | `⇅` `$` `◷` | swap-vertical, currency-usd, clock |

#### Motion (dynamic TUI)

Motion is **driven by real signals**, so it can be read at a glance:

| Motion | Trigger | Meaning |
|---|---|---|
| **Heartbeat**: the active panel's border shimmers in the role colour | `Activity` signals | Pulse speed follows activity: tool calls per minute. Fast = busy, slow = thinking, frozen and yellow = stalled |
| **Ink wipe**: the next panel fills left to right (300 ms) | step transition | The task moved forward |
| **Stamp slam**: a scaled stamp `ACCEPTED` / `REWORK` / `MERGED` appears, then settles into a glyph | verdicts, merge | The outcome of a turn |
| **Loop arrow draw**: the rework arrow draws itself from Verify back to Implement | `changes_requested` | The task is looping; the count increments |
| **Typewriter bubbles**: new dialogue lines type in (≤ 400 ms) | report and verdict summaries, engine decisions | A new message arrived |
| **Splash pulse**: speed lines alternate red and dim at 1 Hz until the escalation has been viewed | new escalation | Needs attention; pulsing stops once seen |
| **Rolling counters**: cost, tokens and elapsed time roll to their new value; quota countdown ticks | usage updates | Live spend and time |
| **Sparklines** `▁▂▃▄▅▆▇█`: last 30 min of tokens/min per agent, and CPU/RAM | usage and resource samples | Trends, not just current values |
| **Slide out**: a finished strip collapses into a one-line "Done" shelf entry | `task_done` | Completed work leaves the stage |

- **Frame rate**: capped at 12 fps.
- **Visibility**: animations pause when the tmux window isn't visible (`#{window_active}` and `#{session_attached}` polled at 1 Hz), so hidden dashboards cost no CPU.
- **Setting**: `dashboard.animations = "full" | "reduced" | "off"`. `reduced` keeps state changes and counters but no shimmer or pulses; `off` renders static frames. `TEXTUAL_ANIMATIONS` is respected.

### 7.2 Task strip (`TaskStrip`)

```
┏━ T-12 · Add retry to parser ━━━━━━━━━━━━━━━━━━━━━━━━┓
┃起 ⌕ Triage ┃承 ⚒ Implement┃転 ⚖︎ Verify  ┃結 ⑂ Merge  ┃
┃ ✔ 3m      ┃ ✔ ↺×1 41m   ┃ ⠹ ◆ 2/5    ┃  ···      ┃
┃ ◆ opus    ┃ ▲ codex/luna┃ ◑ ctx $1.84┃           ┃
┗━━━━━━━━━━┻━━━━━━━━━━━━━┻━━━━━━━━━━━━┻━━━━━━━━━━━┛
  ╰──────── rework ×1 ←──────╯
```

**Panels** follow the story arc 起承転結 (setup, development, twist, resolution). The kanji is a corner mark; the English stage name is the label.

| Mode | Panels |
|---|---|
| implement | Triage · Implement · Verify · Merge (or `PR` in PR mode) |
| plan | Triage · Plan · Review (skipped, dimmed) · Merge |
| split | Triage · Split → child ids (e.g. `T-15 T-16 T-17`) · Children (progress `2/3 done`) · Done. Child strips are drawn indented under the parent |

**Panel states** (§7.1 tokens):
- pending: dotted border `┄`, `state.idle`, stage glyph only
- active: heavy border in the acting role colour with the heartbeat shimmer, spinner, role glyph, iteration gauge `▓▒░`
- done: `state.success` border, `✔` plus duration
- rework: `state.rework` with `↺ ×n`
- hold: `state.hold` stamp
- escalated or failed: `state.alert`, `‼` corner, splash pulse

Each panel's body shows at most 3 short lines: status, `agent/model` of the acting role, elapsed time or iteration `2/5`.

**Loops**:
- A rework loop is an arrow under the strip from Verify back to Implement, with its count.
- A consult is a small inset `? asked Leader` in the Implement panel.

**Stamps** are drawn over the active panel: `PAUSED`, `OPERATOR`, `QUOTA · resets 14:10`, `WAITING RESOURCES`, `STALLED`.

**Responsive**:
- ≥ 60 columns: horizontal strip.
- 36–59 columns: a 2×2 grid.
- < 36 columns: a single line `T-12 ▸ Verify 2/5 · opus/high`.

### 7.3 Dialogue feed (`SpeechBubble`)

Key moments are one-line English quotes taken from reports and engine events, attributed to a character:

```
 長 Leader: “AC3 lacks a tier-2 test.”                     T-12 · 2m
 員 Implementer: “Added the test; all tiers pass.”           T-12 · now
 助 Helper: “Answered the trust dialog (Enter).”             T-14 · 5m
 仕 Shikiru: “Switched implementer to codex/sol after 2 rejections.”
```

The sources:
- verdict `summary` (Leader)
- report `summary` or `question` (Implementer)
- Helper actions
- engine decisions (rung changes, holds, merges)

Bubbles are truncated to the width. Enter opens the full text.

### 7.4 Splash panel (`SplashPanel`)

An open escalation takes over the top of the system pane in every window until it is resolved:

```
╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱
┃ ESCALATION E-3 · T-14 · decision required            ‼ ┃
┃ “Should rounding follow banker's rules or half-up?”     ┃
┃ ★ 1 Half-up (matches the UI spec)   2 Banker's   3 Cancel ┃
┃ [r] resolve   [o] open packet                          ┃
╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱
```

### 7.5 Page composition (system pane)

The system pane is a comic page: panels separated by gutters, arranged top to bottom as the widgets configured in `dashboard.system.widgets`:
1. **Header panel**: project, engine status, slots `2/2 active`, and the small seal mark `仕切る`.
2. Splash panels, one per open escalation.
3. Task strips: the focused task first and expanded, the others compact.
4. **Next up**: queued tasks as a row of small thumbnail panels with priority and waiting reason.
5. Dialogue feed.
6. **Margin notes**: compact panels for usage and quota bars, resources and Docker.

### 7.6 Other surfaces

| Surface | Design |
|---|---|
| `shikiru start` | Wordmark `SHIKIRU` in block letters with a speed-line underline, the tagline "runs the show", and a small `仕切る` seal |
| Idle leader/implementer panes (`shikiru idle`) | An empty comic panel reading "Standing by — no active tasks", with the role name and a decorative role mark (長 or 員) |
| Pane titles | English role name first, kanji mark as a prefix (05-data `dashboard.titles`) |
| `show T-n` / `status` | The same strip, dialogue and splash renderables printed once |

Colours, glyphs and motion follow §7.1 on every surface: Rich output in the CLI uses the same tokens and glyphs, without animation.

**`style = "plain"`** keeps strips, panels, colour tokens and word labels but drops kanji marks, speed lines, screen tones and non-ASCII glyphs. It uses ASCII-safe box characters for limited fonts, and forces `animations = "reduced"`.
