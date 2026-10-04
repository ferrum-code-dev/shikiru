# Shikiru (しきる)

> 仕切る — *to run the show.*

Shikiru is a CLI tool that orchestrates AI coding agents for software development.
Operators give it tasks or goals: humans, other tools, or AI agents. Shikiru queues them and runs a fixed, deterministic pipeline:
the **Leader** plans and verifies, and the **Implementer** does the work.
Each task runs on its own branch until the Leader accepts it. Then Shikiru merges it back.
It escalates to the Operator only when something critical comes up.

## Highlights

- **Agent-agnostic**: agents are tested code plugins behind a normalized contract. Claude Code and Codex CLI are built in, and new agents are added as plugins using the agent SDK and the contract test kit.
- **Live role views** in tmux panes show each headless turn; Operators can take over a role in its native TUI when needed.
- **Parallel tasks**: each task gets its own git worktree and branch, with instant copy-on-write build caches, a dependency graph, priorities and resource-aware scheduling.
- **Resilient**: model ladders, a wait for quota resets followed by a real session resume, and recovery after crashes or restarts.
- **Operator workspace**: the current Home combines task search, task detail, attention and progress summaries. Task detail keeps reports, verdicts, blockers, relationships and retained timeline evidence available after ordinary window cleanup. A shared palette handles contextual actions, and configured widgets stay grouped in navigable Workspace, Activity, Usage, Diagnostics and Extensions sections. The approved compact Focus layout and subscription-aware usage summary are planned for Phase 9; see [`docs/tui-focus.md`](docs/tui-focus.md).
- **Operators everywhere**: a dashboard with a command palette for humans, an MCP server for AI Operators, a small CLI (`--json`) for scripts, and `ls`/`attach` across machines over SSH.
- **Works with your setup**: your CLAUDE.md/AGENTS.md, skills, plugins, plan and todo files; one git repository per project.

## Quick start

From the Shikiru checkout:

```sh
uv tool install .
cd ~/Projects/my-product
shikiru init            # wizard: base branch, agents, plan files, caches
shikiru start           # launches the engine and the tmux session "[shikiru] my-product"
shikiru submit "Implement task 20 from Phase 2 in tasks/plan.md"
shikiru attach
```

Choose an explicit model supported by each selected agent in the wizard. `shikiru init --yes`
writes editable defaults with model placeholders; review the role ladders and run `shikiru doctor`
before starting.

## Dashboard

`shikiru start` creates the Home workspace in the project's `[shikiru] <name>` tmux session.
From Home, press `/` to focus task search and enter a title or task ID. Press Enter once to move
focus to the matching results; then use Up/Down to select a row and press Enter again to open its
detail. Escape returns to the same browser selection. Detail shows the latest validated verdict
alongside the request, task state, relationships, blocker evidence and available history.

Press `Ctrl+P` in the dashboard to open the shared action palette with the selected task and role
pre-filled. Inside a Shikiru tmux session, `prefix + Space` opens the same palette in a popup; a
popup opened from a role pane carries that pane's current task and role. Outside a Shikiru session,
the tmux binding keeps its normal `next-layout` action. `?` opens workspace help.

The current dashboard is the verified Phase 8 workspace. Phase 9's approved task-list/inspector layout, compact resource and usage readouts, and explicit subscription metadata are planned; the examples and values in the [Focus specification](docs/tui-focus.md) describe the target, not shipped behavior.

The dashboard labels command outcomes **Pending**, **Applied** or **Rejected**. Pending means the
command is still in the durable queue; when the engine is stopped it can apply after the next
engine start. Applied means the engine acknowledged the command effect. For an agent message, that
means it was queued for the role's next turn, not that the agent has consumed it.

Usage shows what agents reported: token totals and their coverage, priced cost with its recorded
basis, context when an agent supplied a reliable value, and reported quota windows. Unknown values
stay unknown; billed and API-equivalent amounts remain separate.

Use **Export evidence** from task detail or the palette to write Markdown or JSON locally. Signal
excerpts are opt-in, the destination's parent directory must already exist, and export does not
create a workflow command. It describes retained Shikiru evidence; missing files are listed as
unavailable. It does not recreate cleaned worktrees or provide a complete agent transcript or a
single cross-source snapshot. See [Operator Workspace](docs/tui-workspace.md) for the full
interaction and evidence contracts.

From anywhere else:

```sh
shikiru ls --host devbox         # every project on that machine and how it is doing
shikiru attach --host devbox     # pick one and drop into its session
```

## Trust and safety

Shikiru runs AI agents unattended with your user's privileges. Use it only on repositories and hosts you trust.

- **Claude Code** runs with `--permission-mode bypassPermissions` by default (the `args` option), so it can run any command and edit any file your user can.
- **Codex** runs with the `workspace-write` sandbox. Shikiru adds the run directory, the worktree's own git directory and the repository's shared `.git` directory as writable roots on every turn (verified on Codex 0.159.2; `shikiru doctor` probes it with a free sandbox command and warns on other versions), so a task can change refs, hooks and config that every worktree shares.
- Both agents inherit your environment, including any secrets in it. Their access otherwise follows your native Claude and Codex settings.
- **Worktree isolation is not enforced.** Each task works in its own git worktree, and the prompts tell the agent to stay there, but nothing stops an agent from touching other paths, the main checkout or other tasks' worktrees.
- Shikiru keeps `.shikiru/` (state, prompts, reports, logs, transcripts) owner-only, but it is not a sandbox. It does not limit what agents, configured notification commands or the commands in the project's own instructions can do.

Logs and run files live under `.shikiru/`. The engine log rotates by size (`[logging]` in the config, 10 MiB and 3 backups by default). Run directories (`.shikiru/runs/`: prompts, reports, raw agent output and transcripts) and the events of tasks that finished more than 30 days ago are removed by the running engine (`[retention]` in the config); a task with an open escalation, a kept worktree or an open pull request is never touched. `shikiru status` shows how many run directories exist and their size.

## Platform support

- macOS is verified: `uv run poe check` and `scripts/release-readiness-smoke.sh` run there.
- GNU/Linux: the smoke script is written for it but NOT verified by a run. Process identity uses `psutil` start times with a tolerance for clock steps on Linux.
- tmux 3.4+ and git 2.45+ on both; `shikiru doctor` fails below these floors. Every run so far used tmux 3.7c and git 2.55.0: the floors are chosen, not tested lower (07 §5). The smoke keeps its scratch directory under a short path (`/tmp`, or `SHIKIRU_SMOKE_TMPDIR`) because the private tmux socket path must stay under about 100 characters.

## Documentation

- Design: [`docs/design`](docs/design/README.md)
- Operator Workspace: [`docs/tui-workspace.md`](docs/tui-workspace.md) — Phase 8 interaction and evidence specification. Tasks 75–88 are verified on macOS; a fresh Codex gpt-6-luna E1–E6 run, the normal and `TERM=dumb` full gates, and the clean-install/navigation smoke passed. GNU/Linux was not run and remains unverified. The older Task 59, 74h and 74p review gates remain open. See the [2026-10-03 release record](docs/release-readiness-2026-10-03-phase8.md).
- Compact Operator TUI: [`docs/tui-focus.md`](docs/tui-focus.md) — approved Phase 9 target; behavior remains planned until Tasks 89–96 ship. The [approved standalone mockup](docs/design/references/focus-steel-approved.html) and [selected 736-pixel view](docs/design/references/focus-steel-approved-736.png) are durable references.
- Workspace walkthrough evidence: [`docs/workspace-walkthroughs-2026-10-03.md`](docs/workspace-walkthroughs-2026-10-03.md) — executed flows, fixtures and observed limits.
- Implementation plan: [`tasks/plan.md`](tasks/plan.md), progress: [`tasks/todo.md`](tasks/todo.md)

## Requirements

Python 3.14+, tmux 3.4+, git 2.45+ (checked by `shikiru doctor`), and at least one supported agent CLI (`claude`, `codex`).
