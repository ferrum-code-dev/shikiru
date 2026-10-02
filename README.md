# Shikiru (しきる)

> 仕切る — *to run the show.*

Shikiru is a CLI tool that orchestrates AI coding agents for software development.
Operators give it tasks or goals: humans, other tools, or AI agents. Shikiru queues them and runs a fixed, deterministic pipeline:
the **Leader** plans and verifies, and the **Implementer** does the work.
Each task runs on its own branch until the Leader accepts it. Then Shikiru merges it back.
It escalates to the Operator only when something critical comes up.

```
┏━ T-12 · Add retry to parser ━━━━━━━━━━━━━━━━━━━━━━━━┓
┃起 Triage   ┃承 Implement ┃転 Verify    ┃結 Merge    ┃
┃ ✔ 3m       ┃ ✔ iter 2    ┃ ▓▓▒░ 2/5    ┃  ···       ┃
┗━━━━━━━━━━━━┻━━━━━━━━━━━━━┻━━━━━━━━━━━━━┻━━━━━━━━━━━━┛
 長 Leader: “AC3 lacks a tier-2 test.”
 員 Implementer: “Added the test; all tiers pass.”
```

## Highlights

- **Agent-agnostic**: agents are tested code plugins behind a normalized contract. Claude Code and Codex CLI are built in, and new agents are added as plugins using the agent SDK and the contract test kit.
- **Live role views** in tmux panes show each headless turn; Operators can take over a role in its native TUI when needed.
- **Parallel tasks**: each task gets its own git worktree and branch, with instant copy-on-write build caches, a dependency graph, priorities and resource-aware scheduling.
- **Resilient**: model ladders, a wait for quota resets followed by a real session resume, and recovery after crashes or restarts.
- **Comic-strip workspace**: the Textual shell groups current widgets into navigable workspace, activity, usage, diagnostics and extension sections. The selectable task browser, shared task detail, existing task/role navigation and contextual searchable action palette are implemented; richer history and responsive composition remain Phase 8 work.
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
- Operator Workspace: [`docs/tui-workspace.md`](docs/tui-workspace.md) — Phase 8 specification and implementation status; the shared shell, task browser, task detail, existing-window navigation and contextual palette have shipped, while later operator capabilities remain planned.
- Implementation plan: [`tasks/plan.md`](tasks/plan.md), progress: [`tasks/todo.md`](tasks/todo.md)

## Requirements

Python 3.14+, tmux 3.4+, git 2.45+ (checked by `shikiru doctor`), and at least one supported agent CLI (`claude`, `codex`).
