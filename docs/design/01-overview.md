# 01 · Overview

## 1. Purpose

Shikiru (しきる, 仕切る "to run the show") runs a **deterministic development pipeline**, and AI agents do the thinking inside it.
Operators submit requests, watch progress and intervene. Shikiru decides *when* things happen; agents decide *how* to do the work.

## 2. Roles

| Role | Mark | Kind | Responsibility |
|---|---|---|---|
| Orchestrator (Shikiru) | 仕 | Deterministic engine | Queue, scheduling, workspaces, prompts, turns, loops, integration, recovery, escalation |
| Leader (Chō) | 長 | Resumed AI session, one headless turn at a time | Triage, planning, decomposition, verification, answering Implementer questions, scope decisions |
| Implementer (In) | 員 | Resumed AI session, one headless turn at a time | Implementation, tests, commits, tracker updates, conflict resolution |
| Operator (Yūzā) | — | Human / software / AI agent | Submit, prioritise, observe, intervene, resolve escalations |
| Helper (Joshu) | 助 | Headless one-shot small model | Rebuilds a missing report, classifies an unrecognised failure |

English names come first everywhere. Kanji marks are decoration only.

## 3. Principles

1. **The engine decides the flow; agents do the work.** Every transition is code. Agent output only picks one of a fixed set of schema-validated outcomes.
2. **No agent coupling.** The engine sees only normalized signals, usage, quota and failure kinds, all through `shikiru.agents.contracts`. How an agent produces them is private to its plugin.
3. **Single writer.** Only the engine writes state. CLI, MCP and dashboard read state and append `commands`; the one exception is atomically incrementing the `sequences` row to allocate a task id on submit. Agents only touch files.
4. **Escalate only on critical issues.** Retries, model ladders, quota waits and Leader decisions come first.
5. **Project conventions win.** Shikiru adds no coding rules. It points agents at the request, the trackers and the project's own instructions and skills.
6. **KISS/YAGNI.** Build only what a requirement needs. One way to do each thing. Tunables live in config only when an operator really needs them.

## 4. Decision log

| # | Decision | Rejected | Reason |
|---|---|---|---|
| D1 | Each turn is one headless agent process on a resumed session (`claude -p --resume`, `codex exec resume`). The prompt is a one-line pointer to a prompt file; the agent submits a report file; the turn ends when the process exits. Signals come only from the agent's structured output stream. Panes show a live view of that stream; an Operator takes a session over interactively and hands it back (03 §9) | Interactive TUIs driven through the screen (paste + Enter injection, readiness markers, startup dialogs, screen stall and operator-prompt detection, hooks as the turn signal); LLM-driven orchestration (TMUX Swarm) | Every Task 21 E2E failure came from treating human TUIs as an API: CLIs auto-update and reword dialogs overnight (Codex 0.156 did). Structured streams are the CLIs' machine interface. Panes are mostly for watching; interventions are occasional and explicit |
| D2 | Local rebase + fast-forward by default. `integration = "pr"` opens a PR instead | PR only; leave branch | Operator choice. Matches the current flow |
| D3 | TOML config | YAML, JSON | Operator choice |
| D4 | The Leader decides scope changes; the Operator is notified | Always escalate | Operator choice |
| D5 | Python 3.14, uv, Typer+Rich, Textual, SQLite WAL, tmux ≥ 3.4 | — | Fits the requirements |
| D6 | One git worktree per task under `.shikiru/worktrees/`; build caches copied copy-on-write | In-place branches; full clones; symlinked caches | Isolation with warm caches at almost no cost |
| D7 | Leader and Implementer sessions last for the whole task (every turn resumes them); each new task starts clean | Session per step | Keeps review context, like the manual loop |
| D8 | Detached engine daemon per project; the tmux session is only its view | Engine inside a pane | Pane or terminal loss can't kill orchestration: a lost tmux server or session is recreated by the engine, and a fault in one tick is retried, not fatal (02 §1) |
| D9 | SQLite `commands` table as the operator→engine bus | Socket/HTTP API | Local, persistent, and works while the engine is down |
| D10 | Remote access over SSH (`ls --host`, `attach --host`, `ssh host shikiru mcp`) | Network server | No ports, no auth code |
| D11 | Every submission is a task that the Leader triages into `implement` or `plan`; `plan` is the one way to decompose work up front (its `new_tasks` own their tracker entries), verify `supersede` the one way after an attempt | Parsing plan files; a `split` triage outcome | Plan formats vary. `split` was a second way to create child tasks (principle 6), orphaned the parent's tracker entry, and small models over-split trivial requests |
| D12 | Ladders: the Leader has one base ladder; the Implementer has ladders per complexity. Moving up resumes the session with the new model when the agent stays the same | Single model | Mirrors manual model switching |
| D13 | Real session resume after quota, stall, crash, take-over or restart | Fresh session with a summary | Keeps context |
| D14 | Agents are code plugins behind a normalized contract (`start_turn` → signal stream), built on a small SDK and a shared contract test kit that replays recorded streams | Agents described in config (v4 profiles); the engine parsing streams, transcripts or quota | Testable, stable, decoupled |
| D15 | One signal consumer per turn process (`TurnMonitor`), which persists facts, feeds the pane view and decides stalls | Several readers of one stream; a long-lived per-session monitor | The process is the turn, so its stream has one natural owner; no lost or raced signals |
| D16 | Recovery decisions are a pure function `decide(failure, counters, policy) → Action` | Recovery logic spread across the runner | Unit-testable and keeps the runner small |
| D17 | A permanent home window `shikiru` plus one window per active task, whose role panes are live views (or a taken-over session) | Swapping windows so the count never reaches zero | Simpler, and still meets the "one window remains" rule |
| D18 | Comic-strip TUI with a small semantic visual language: role and state colours, meaningful glyphs, motion driven by signals | Decorative theme; large animation catalogue | Distinctive and informative, at bounded cost |
| D19 | The CLI exposes the project entry points, `submit`/`status`, and session discovery/attachment (`ls`/`attach`); other mutating actions are reached through the palette or MCP | A CLI verb for every operator action (v4) | Essential lifecycle and session access stay easy to remember without duplicating the full operator surface |
| D20 | One git repository per project: the project root is the repository root. Submodules are unsupported. Operators with several repositories run one Shikiru per repository | Multi-repo task roots assembled from several worktrees plus symlinked top-level entries | Symlinks into the live checkout let agents read and edit it, and left tracker edits outside any task branch. Agents started above the repositories, so Codex missed per-repo `AGENTS.md` and Claude asked about external imports. PR integration would need N coordinated PRs. Multi-repo coordination, if ever needed, belongs above projects, not in the engine |
| D21 | Usage, cost, context and quota are only what the agent reports: tokens always; cost only when the agent marks it priced; context only from a reliable window; quota windows only when reported. Missing values show as missing | A Shikiru price table; ccusage | Measured 2026-09-24 against billing: Claude's cost is right only with `costBasis: "list"` (via OpenRouter it was 200× too high), Codex reports no cost, and a price table drifts per provider. ccusage prices from LiteLLM tables, needs Node, and `--offline` silently reports $0 for Claude sessions |
| D22 | Failures are mapped by the plugin from structured errors; only unrecognised text goes to the Helper, whose classification below a confidence threshold escalates | A "System One" classification model (e.g. Jev, Laya) as the main signal path | It makes transitions probabilistic, cannot extract values like ids or reset times, and adds a hosted vendor or a PyTorch model to a CLI. It may sit behind the Helper later if ever needed |
| D23 | Every supported agent compacts proactively at its plugin option `compact_at_pct` (default 80), including during a single running turn, and continues the same task without context overflow. Compaction and any recovery from a context failure are wholly inside the plugin, against the agent's own view of its context window, in every access mode the agent supports. Core Shikiru has no context handling: it only displays `Compacted` and `context_used_pct` | Compaction only between turns; waiting for overflow before compacting; core-driven context recovery (new session with handoff); a context size or provider setting in Shikiru's configuration | Context can fill within one turn. The window is a property of the model and provider that can change without notice, and only the agent knows it, so only its plugin can act on it. The Operator configures agent, model and effort; access is the agent's own configuration |

## 5. Lessons from prior art

**TMUX Swarm Tools.**
- **Kept:** tmux visibility, persona and guidance files.
- **Rejected:** an LLM orchestrating through `send-keys` and `capture-pane` string markers. It's non-deterministic, has no state and can't recover.

**shikiru_v4.**
- **Kept:**
  - file-pointer prompt injection
  - a synchronous report validator
  - git facts computed by the engine
  - pane-id addressing and `remain-on-exit`
  - rungs carrying `{agent, model, effort}`
  - copy-on-write caches
  - a pre-flight probe
- **Rejected:**
  - regex screen-scraping
  - agent profiles in config
  - a fresh respawn after quota, losing context
  - effect-replay layers
  - speculative hardening
  - docs pinned by tests
  - ~150 config keys
  - a CLI verb per operator action, duplicating the dashboard and MCP
  - tests touching the operator's tmux server

## 6. Trust boundary

Shikiru orchestrates; it does not sandbox. Agents run as the Operator's user, in the Operator's environment, on the Operator's host:
- Claude Code defaults to `--permission-mode bypassPermissions` (04 §4), so it can run any command and change any file the user can.
- Codex runs in `workspace-write` with the run directory and the repository's shared `.git` directory as extra writable roots (04 §5). A task can therefore alter refs, hooks and config that every worktree shares.
- Worktree isolation is a prompt convention and a scheduling rule (D6), not an enforced boundary. An agent can reach the main checkout and other tasks' worktrees. An agent that edits the main checkout is detected only when it blocks a merge: the local merge then records the notified `integration_waiting` event naming the dirty paths (03 §6). Shikiru does not compare the checkout before and after a turn: the operator edits it concurrently, so a change cannot be attributed to an agent without false positives.
- The same holds for notification commands the Operator configures and the commands in the project's own instructions.

Shikiru must be used only on repositories and hosts the Operator trusts. Remote access is SSH (D10), so host access equals Shikiru access.

## 7. Glossary

| Term | Meaning |
|---|---|
| Task | Unit of scheduling `T-<n>`, created from an Operator request or by the Leader |
| Step | Engine stage of a task: `triage`, `plan`, `implement`, `consult`, `verify`, `integrate` |
| Turn | One prompt → report exchange: one agent process on a new or resumed session |
| Run | One agent session bound to a task and role. It survives resumes, take-overs and same-agent model changes |
| Rung | `{agent, model, effort}` entry of a ladder |
| Slot | Parallel lane `0..max_parallel-1`, exported as `SHIKIRU_SLOT` (with `max_parallel = 2`, tasks see `SHIKIRU_SLOT=0` and `SHIKIRU_SLOT=1`) |
| Hold | Task pause: `paused` and `intervention` (a taken-over session) are stored on the task and cleared by `resume` or hand back; the quota wait is derived from the agent's block and clears automatically |
| Escalation | Critical issue that needs an Operator decision: `E-<n>` |
