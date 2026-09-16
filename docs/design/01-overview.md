# 01 · Overview

## 1. Purpose

Shikiru (しきる, 仕切る "to run the show") runs a **deterministic development pipeline**. AI agents do the thinking inside it.
Operators submit requests, watch progress and intervene. Shikiru decides *when* to run something. Agents decide *how* to do it.

## 2. Roles

| Role | Name | Kind | Responsibility |
|---|---|---|---|
| Orchestrator | 仕切る Shikiru | Deterministic engine | Queue, scheduling, workspaces, prompts, turn protocol, loops, integration, recovery, escalation |
| Leader | 長 Chō | Interactive AI session | Triage, planning, decomposition, verification, answering Implementer questions, scope decisions |
| Implementer | 員 In | Interactive AI session | Implementation, tests, commits, tracker updates, conflict resolution |
| Operator | ユーザ Yūzā | Human / software / AI agent | Submit, prioritise, observe, intervene, resolve escalations |
| Helper | 助手 Joshu | Headless one-shot small model | Handles cases where an agent's output doesn't match Shikiru's expected formats: rebuilds missing reports, classifies unknown failures and stuck screens |

## 3. Principles

1. **Engine decides the flow, agents do the work.** Every transition is code in the engine, never a model's choice of what to run next. Agent output only selects one of a fixed set of outcomes, enforced by a schema.
2. **Signals, not scraping.** Hooks, report files and transcripts are the primary signals. Pane capture is used only as evidence for the Helper when those signals stop.
3. **Agents touch files, Shikiru touches the DB.** Agents exchange prompt, report and hook-event files. Only Shikiru processes write SQLite.
4. **Open for new agents and widgets** through entry-point plugins, and closed for modification.
5. **Escalate only on critical issues.** Holds (quota, resources, pause) clear on their own. The Leader resolves scope questions itself. Everything else is retried, moved up the model ladder or replanned first.
6. **Project conventions win.** Shikiru adds no coding rules. It refers agents to the request, the plan files and the project's own instructions, and uses the configured skill prefix.
7. **KISS/YAGNI.** No speculative hardening, no layers without a present need, a small configuration surface with good defaults.

## 4. Decision log

| # | Decision | Alternatives rejected | Reason |
|---|---|---|---|
| D1 | **Interactive agent TUIs in tmux panes**, prompts injected by pasting a one-line pointer, completion signalled by hooks plus a report file | Headless runs; agent-driven orchestration (TMUX Swarm) | Operator selection. Operators see and can use the real sessions. Hooks make it reliable. |
| D2 | **Local integration by default** (rebase, then fast-forward). `integration = "pr"` pushes and opens a PR instead | PR only; leave branch | Operator selection. Matches the current commit-to-main flow and stays safe in parallel |
| D3 | **TOML** config | YAML, JSON | Operator selection. Supported by the stdlib, Codex uses it, dotted-key overrides |
| D4 | **Leader decides scope changes** (moves ACs, creates follow-up tasks), the Operator is notified, no escalation | Always escalate | Operator selection. Fewer interruptions |
| D5 | Python 3.14, uv, Typer+Rich CLI, Textual dashboard, SQLite (WAL), tmux 3.4+ | Go/Rust; web UI | The suggested stack fits. Textual is the most capable Python TUI |
| D6 | **One git worktree per task per affected repo** under `.shikiru/worktrees/`, build caches copied as copy-on-write clones (APFS `clonefile`, Linux `reflink`) | In-place branches (no parallelism); full clones (slow, disk-heavy); symlinked caches (shared mutable state) | Isolation with warm caches at almost no disk cost |
| D7 | **Leader and Implementer sessions persist for the whole task** (triage → iterations → integration). Each new task starts with clean sessions | A new session per step | Matches the manual copy-paste loop. The agents keep context across review rounds |
| D8 | **Detached engine daemon** per project (one `flock`), with the tmux session as its view | Engine inside a tmux pane | Closing or rebuilding panes can't kill orchestration |
| D9 | **SQLite `commands` table as the operator→engine bus** | HTTP/Unix socket API | Everything is local and a DB is needed anyway. Commands submitted while the engine is down are kept and applied on the next start |
| D10 | Remote access = **SSH** (`attach`, `shikiru mcp` over `ssh host`) | Network MCP/HTTP server | No new ports, auth or daemon. Reuses the operator's SSH config |
| D11 | Every submission is a **request task**. The Leader's triage turns it into `implement`, `plan` or `split` | Separate request types with a parser for the plan files | Plan formats vary. The Leader reads them the way the Operator does today |
| D12 | **Model ladder per role and complexity**. Moving up happens on failed verifications, unavailability or quota policy. Switching model within the same agent resumes the session. Switching to another agent starts a new session with a handoff | A single model | Mirrors the manual luna→sol switching |
| D13 | **Resume the real session** after a quota wait, a crash or an engine restart (`claude --resume`, `codex resume`) | A fresh session with a summary | Keeps context. Prior art lost it |
| D14 | Plugins live in `src/shikiru/plugins/…` and register through `pyproject` entry points | Top-level `src/plugins` | A wheel needs them inside the package. Third-party plugins use the same entry points |

## 5. Lessons applied from prior art

**TMUX Swarm Tools (reference).** *Keep:* tmux-native visibility and persona and guidance files. *Reject:* an LLM that orchestrates by `send-keys` and parses `capture-pane` output for `ARCHITECT REQUEST:` strings. That approach is non-deterministic and burns tokens on polling. It has no queue, no state and no recovery.

**shikiru_v4 (archive).**
*Keep:*
- file-pointer prompt injection (`load-buffer` → `paste-buffer -p` → `Enter`)
- a synchronous `shikiru report` validator with exit codes
- engine-computed git facts in the verify prompt
- panes addressed by pane id
- `remain-on-exit`
- holds vs escalations, where an escalation frees its slot
- ladder rungs that carry `{agent, model, effort}`
- copy-on-write cache cloning
- a `doctor --probe` pre-flight check

*Reject:*
- regex screen-scraping as the backbone
- a fresh respawn after a quota stop, which lost context
- about 40 engine modules with effect replay
- speculative security hardening
- docs pinned by tests
- about 150 config keys
- tests that ran against the operator's real tmux server

## 6. Glossary

| Term | Meaning |
|---|---|
| Request | Operator text submitted to the queue, e.g. `Implement all tasks in phase B` |
| Task | The unit of scheduling: `T-<n>`, derived from a request or created by the Leader |
| Step | One engine stage of a task: triage, implement, verify, replan, integrate |
| Turn | One prompt→report exchange with one agent session |
| Run | One agent session bound to a task and role (it survives resumes and model switches within the same agent) |
| Slot | A parallel execution lane `1..max_parallel`, exported as `SHIKIRU_SLOT` |
| Hold | A temporary, self-clearing pause of a task |
| Escalation | A critical issue that needs an Operator decision: `E-<n>` |
