# Shikiru (しきる)

> 仕切る — *to run the show.*

Shikiru is a CLI tool that orchestrates AI coding agents for software development.
Operators give it tasks or goals: humans, other tools, or AI agents. Shikiru queues them and runs a fixed, deterministic pipeline:
the **Leader** (長 Chō) plans and verifies, and the **Implementer** (員 In) does the work.
Each task runs on its own branch until the Leader accepts it. Then Shikiru merges it back.
It escalates to the Operator only when something critical comes up.

```
┌ system ─────────────┬ 長 Chō · claude:opus/high · iter 2 ───────┐
│ 仕切る ferrum         │ (live Claude Code session)                │
│ ▶ T-12 verifying 2/5 ├ 員 In · codex:gpt-5.6-luna/xhigh ──────────┤
│ ⏸ T-13 queued        │ (live Codex session)                      │
│ ドン! E-3 needs you  ├ shell ─────────────────────────────────────┤
│ cpu 41% ram 18G $4.2 │ $                                         │
└──────────────────────┴───────────────────────────────────────────┘
```

## Highlights

- **Agent-agnostic**: agents are plugins. Claude Code and Codex CLI are built in.
- **Real interactive sessions** in tmux panes. Shikiru detects turns and limits through agent hooks and session transcripts, not by reading the screen.
- **Parallel tasks**: each task gets its own git worktree and branch, with copy-on-write build caches, a dependency graph, priorities and resource-aware scheduling.
- **Resilient**: model ladders, a wait for quota resets followed by a real session resume, and recovery after crashes or restarts.
- **Operators everywhere**: TUI dashboard, CLI (`--json`), MCP server, event stream, and local or remote (SSH) attach.
- **Works with your setup**: your CLAUDE.md/AGENTS.md, skills, plugins, plan and todo files, monorepo or multi-repo.

## Quick start

```sh
uv tool install shikiru
cd ~/Projects/my-product
shikiru init            # wizard: repos, agents, plan files, caches
shikiru start           # launches the engine and the tmux session "[shikiru] my-product"
shikiru submit "Implement task 20 from Phase 2 in tasks/plan.md"
shikiru attach
```

## Documentation

- Design: [`docs/design`](docs/design/README.md)
- Implementation plan: [`tasks/plan.md`](tasks/plan.md), progress: [`tasks/todo.md`](tasks/todo.md)

## Requirements

Python 3.14+, tmux 3.4+, git 2.45+, and at least one supported agent CLI (`claude`, `codex`).
