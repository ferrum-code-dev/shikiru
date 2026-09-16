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
┃ ✔ 3m      ┃ ✔ iter 2    ┃ ▓▓▒░ 2/5   ┃  ···      ┃
┗━━━━━━━━━━┻━━━━━━━━━━━━━┻━━━━━━━━━━━━┻━━━━━━━━━━━┛
 長 Leader: “AC3 lacks a tier-2 test.”
 員 Implementer: “Added the test; all tiers pass.”
```

## Highlights

- **Agent-agnostic**: agents are plugins with optional capabilities (hooks, transcripts, quota). Claude Code and Codex CLI are built in, and any other TUI agent works through config alone.
- **Real interactive sessions** in tmux panes that Operators can watch and step into, one task at a time.
- **Parallel tasks**: each task gets its own git worktree and branch, with instant copy-on-write build caches, a dependency graph, priorities and resource-aware scheduling.
- **Resilient**: model ladders, a wait for quota resets followed by a real session resume, and recovery after crashes or restarts.
- **Comic-strip dashboard**: every task is drawn as a 4-panel strip.
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
