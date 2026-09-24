# 01 · Overview

## 1. Purpose

Shikiru (しきる, 仕切る "to run the show") runs a **deterministic development pipeline**, and AI agents do the thinking inside it.
Operators submit requests, watch progress and intervene. Shikiru decides *when* things happen; agents decide *how* to do the work.

## 2. Roles

| Role | Mark | Kind | Responsibility |
|---|---|---|---|
| Orchestrator (Shikiru) | 仕 | Deterministic engine | Queue, scheduling, workspaces, prompts, turns, loops, integration, recovery, escalation |
| Leader (Chō) | 長 | Interactive AI session | Triage, planning, decomposition, verification, answering Implementer questions, scope decisions |
| Implementer (In) | 員 | Interactive AI session | Implementation, tests, commits, tracker updates, conflict resolution |
| Operator (Yūzā) | — | Human / software / AI agent | Submit, prioritise, observe, intervene, resolve escalations |
| Helper (Joshu) | 助 | Headless one-shot small model | Rebuilds a missing report, classifies a stuck screen |

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
| D1 | Interactive agent TUIs in tmux panes. Prompts are injected as a one-line pointer to a prompt file; the plugin signals turn end, and the agent submits a report file | Headless runs; LLM-driven orchestration (TMUX Swarm) | Operator choice. Real sessions stay visible and usable |
| D2 | Local rebase + fast-forward by default. `integration = "pr"` opens a PR instead | PR only; leave branch | Operator choice. Matches the current flow |
| D3 | TOML config | YAML, JSON | Operator choice |
| D4 | The Leader decides scope changes; the Operator is notified | Always escalate | Operator choice |
| D5 | Python 3.14, uv, Typer+Rich, Textual, SQLite WAL, tmux ≥ 3.4 | — | Fits the requirements |
| D6 | One git worktree per task under `.shikiru/worktrees/`; build caches copied copy-on-write | In-place branches; full clones; symlinked caches | Isolation with warm caches at almost no cost |
| D7 | Leader and Implementer sessions last for the whole task; each new task starts clean | Session per step | Keeps review context, like the manual loop |
| D8 | Detached engine daemon per project; the tmux session is only its view | Engine inside a pane | Pane or terminal loss can't kill orchestration |
| D9 | SQLite `commands` table as the operator→engine bus | Socket/HTTP API | Local, persistent, and works while the engine is down |
| D10 | Remote access over SSH (`ls --host`, `attach --host`, `ssh host shikiru mcp`) | Network server | No ports, no auth code |
| D11 | Every submission is a task that the Leader triages into `implement` or `plan`; `plan` is the one way to decompose work up front (its `new_tasks` own their tracker entries), verify `supersede` the one way after an attempt | Parsing plan files; a `split` triage outcome | Plan formats vary. `split` was a second way to create child tasks (principle 6), orphaned the parent's tracker entry, and small models over-split trivial requests |
| D12 | Ladders: the Leader has one base ladder; the Implementer has ladders per complexity. Moving up resumes the session when the agent stays the same | Single model | Mirrors manual model switching |
| D13 | Real session resume after quota, crash or restart | Fresh session with a summary | Keeps context |
| D14 | Agents are code plugins behind a normalized contract, built on a small SDK and a shared contract test kit | Agents described in config (v4 profiles); engine parsing transcripts, prices or quota | Testable, stable, decoupled |
| D15 | One signal consumer per agent session (`SessionMonitor`), which fans out to turns | Turns reading the stream directly | No lost or raced signals |
| D16 | Recovery decisions are a pure function `decide(failure, counters, policy) → Action` | Recovery logic spread across the runner | Unit-testable and keeps the runner small |
| D17 | A permanent home window `shikiru` plus one window per active task | Swapping windows so the count never reaches zero | Simpler, and still meets the "one window remains" rule |
| D18 | Comic-strip TUI with a small semantic visual language: role and state colours, meaningful glyphs, motion driven by signals | Decorative theme; large animation catalogue | Distinctive and informative, at bounded cost |
| D19 | The CLI carries only entry points and `submit`/`status`; every other operator action is reached through the palette or MCP | A CLI verb per operator action (v4) | One action, one adapter. The CLI stays small enough to remember |
| D20 | One git repository per project: the project root is the repository root. Submodules are unsupported. Operators with several repositories run one Shikiru per repository | Multi-repo task roots assembled from several worktrees plus symlinked top-level entries | Symlinks into the live checkout let agents read and edit it, and left tracker edits outside any task branch. Agents started above the repositories, so Codex missed per-repo `AGENTS.md` and Claude asked about external imports. PR integration would need N coordinated PRs. Multi-repo coordination, if ever needed, belongs above projects, not in the engine |

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

## 6. Glossary

| Term | Meaning |
|---|---|
| Task | Unit of scheduling `T-<n>`, created from an Operator request or by the Leader |
| Step | Engine stage of a task: `triage`, `plan`, `implement`, `consult`, `verify`, `integrate` |
| Turn | One prompt → report exchange with one agent session |
| Run | One agent session bound to a task and role. It survives resumes and same-agent model changes |
| Rung | `{agent, model, effort}` entry of a ladder |
| Slot | Parallel lane `1..max_parallel`, exported as `SHIKIRU_SLOT` |
| Hold | Task pause: `paused` and `intervention` are stored on the task and cleared by `resume` (or a finished turn); the quota wait is derived from the agent's block and clears automatically |
| Escalation | Critical issue that needs an Operator decision: `E-<n>` |
