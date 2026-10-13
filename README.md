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
- **Compact operator workspace**: Focus Home keeps selected-task detail inline at wide and compact widths. Engine uptime, project usage and Host/Shikiru/Agents resource readouts stay concise; detailed usage, quota, model history, evidence and actions open on demand. Configured secondary widgets remain reachable through Views.
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

After pulling a change that adds, removes or renames a widget or agent plugin (an entry point in `pyproject.toml`), reinstall with `uv tool install . --reinstall` (editable: `uv tool install --editable . --reinstall`). Entry points are written at install time, so an editable install needs the reinstall too.

Choose an explicit model supported by each selected agent in the wizard. `shikiru init --yes`
writes editable defaults with model placeholders; review the role ladders and run `shikiru doctor`
before starting.

## Dashboard

`shikiru start` creates Focus Home in the project's `[shikiru] <name>` tmux session. On a wide pane,
the task list and selected-task inspector sit side by side. Select a task by clicking it or using
Up/Down to update the inline detail; Escape clears the selection after closing any overlay. Press
`/` to show and focus task search, enter a title or task ID, then press Enter to move to matching
results. Enter activates the focused control and never opens a second task-detail screen.

Click a task row or use Up/Down to select it. Press `Ctrl+P` to open contextual Actions with the
selected task and role pre-filled. Its primary actions are Answer decision when one applies,
Evidence & reports, Message a role, Take over/Hand back when applicable, Usage, Commands and
Project diagnostics. More actions opens the remaining task and project operations. Answer opens the
recorded question directly; only its explicit submit queues an answer. Form inputs retain native
editing keys, and Escape returns without submitting. In task detail, `e` opens Evidence, `u` opens
Usage Details and `v` opens the broader contextual Views menu. The Views menu also opens Help,
filters, command history and configured secondary widgets, including Extensions.
Selecting a task updates its inline detail, and Escape clears the selection after closing an
overlay. Home's `Ctrl+P` opens contextual Actions over the running dashboard. `prefix + Space`
does the same on Home; in other Shikiru panes it opens the standalone palette with that pane's
task and role context. The binding is session-scoped, so unrelated sessions keep tmux's normal
`next-layout` action. When the Home footer is visible it shows the effective prefix and detach
hint; at the 20×8 reachability floor, `Ctrl+P`, `?`, Escape and tmux prefix+`d` remain available
while the footer is hidden. `?` opens contextual help.

A taken-over role is not restored as taken over after its pane is respawned. If the tmux server or
session is killed, the host reboots, the pane is killed, or the Operator exits the agent's TUI, the
role returns to its read-only view. The current step then resumes on the same agent session, unless
the task is paused or another role is still taken over. Take over again to continue interactively.

The Phase 11 first-delivery record covers Tasks 105–112; the [2026-10-08 follow-up](docs/release-readiness-2026-10-08-phase11-followup.md)
records accepted second-review work through Task 121, current matched native captures,
walkthroughs, test gates and limits. The [2026-10-09 third-review record](docs/release-readiness-2026-10-09-phase11-third-review.md)
reconciles accepted Tasks 122–128, with Task 128 accepted by root on 2026-10-09. The
[2026-10-11 fourth-review record](docs/release-readiness-2026-10-11-phase11-fourth-review.md) verifies Tasks 129–139 issue by issue with gates, smoke and limits.
The [2026-10-11 fifth-review record](docs/release-readiness-2026-10-11-phase11-fifth-review.md) verifies Tasks 142–145; Task 146 (Implementer pane titles) is closed as not reproduced.
The [2026-10-12 sixth-review record](docs/release-readiness-2026-10-12-phase11-sixth-review.md) verifies Tasks 148–149 (50/90 quota gauge colours, `Project usage` title and layout) and the HTML reference.
The [2026-10-12 seventh-review record](docs/release-readiness-2026-10-12-phase11-seventh-review.md) verifies Tasks 151–155 (Project Diagnostics sections, Docker essentials and scope, the Disk section, timer refresh) and the HTML reference.
The [2026-10-13 eighth-review record](docs/release-readiness-2026-10-13-phase11-eighth-review.md) verifies Tasks 156–165 (leaner tests, stale and age hints only after a failure, one refresh moment, the `.shikiru` size and `‼` marker, sentence-case titles, the task detail following under popups) and the HTML reference.
The [2026-10-13 ninth-review record](docs/release-readiness-2026-10-13-phase11-ninth-review.md) verifies Tasks 167–173 (bold `SHIKIRU` and the `Tasks` heading, the plugin reinstall hint, a steadier test suite, command history paged with the arrow keys and coloured without a raw result) and the HTML reference.
The [Focus specification](docs/tui-focus.md) describes current routes and evidence rules; the
[parity record](docs/tui-parity.md) preserves the measured Phase 10 audit and its historical artifacts.

The dashboard labels command outcomes **Pending**, **Applied** or **Rejected**. Pending means the
command is still in the durable queue; when the engine is stopped it can apply after the next
engine start. Applied means the engine acknowledged the command effect. For an agent message, that
means it was queued for the role's next turn, not that the agent has consumed it.

Usage shows what agents reported: input/output token totals and their reported, legacy or unknown
coverage; actual billed amounts with their recorded basis; API-equivalent estimates separately;
reliable context; and reported quota windows. Each run can also carry an explicit plugin billing
declaration (`api`, `subscription` or `unknown`) with its source. Subscription mode does not imply
a zero task charge, a plan fee or a purchased account; quota belongs to the provider/account scope
reported by the plugin. Unknown values remain unknown. Session totals can span model changes, so
earlier models and per-turn observations appear in Usage Details without assigning cumulative
totals to each model. Retained event history can have explicit gaps.

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
- Compact Operator TUI: [`docs/tui-focus.md`](docs/tui-focus.md) — current Focus routes plus the billing and usage evidence contract. [`docs/tui-parity.md`](docs/tui-parity.md) and the [Phase 10 release record](docs/release-readiness-2026-10-05-phase10.md) preserve the accepted Tasks 97–102 audit. The [Phase 11 first-delivery record](docs/release-readiness-2026-10-07-phase11.md) preserves Tasks 105–112; the [second-review follow-up](docs/release-readiness-2026-10-08-phase11-followup.md) preserves Tasks 113–121; and the [third-review record](docs/release-readiness-2026-10-09-phase11-third-review.md) records accepted Tasks 122–128 and their source-attributed evidence. The [approved standalone mockup](docs/design/references/focus-steel-approved.html) was first revised in Task 113a and revised again in Task 124; both revisions preserve its approved palette. The historical [selected 736-pixel view](docs/design/references/focus-steel-approved-736.png) remains unchanged.
- Phase 9 release evidence: [`docs/release-readiness-2026-10-04-phase9.md`](docs/release-readiness-2026-10-04-phase9.md) — macOS gates, connected fake-agent walkthroughs, fresh E1–E6, visual review, limits and host hygiene.
- Phase 10 release evidence: [`docs/release-readiness-2026-10-05-phase10.md`](docs/release-readiness-2026-10-05-phase10.md) — connected correction walkthroughs, final gates, macOS clean-install/navigation smoke, native-cell review, limits and host hygiene.
- Workspace walkthrough evidence: [`docs/workspace-walkthroughs-2026-10-03.md`](docs/workspace-walkthroughs-2026-10-03.md) — executed flows, fixtures and observed limits.
- Implementation plan: [`tasks/plan.md`](tasks/plan.md), progress: [`tasks/todo.md`](tasks/todo.md)

## Requirements

Python 3.14+, tmux 3.4+, git 2.45+ (checked by `shikiru doctor`), and at least one supported agent CLI (`claude`, `codex`).
