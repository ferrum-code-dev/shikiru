# Design

## Purpose

Shikiru runs a fixed software-development workflow around coding agents. The engine decides which
step runs and validates each result; agents perform the work. The Operator submits requests,
observes progress and resolves decisions that need human input.

## Architecture

- One detached asynchronous engine runs per project. SQLite stores project state and a durable
  command queue; CLI, MCP and dashboard clients read state and submit commands.
- tmux is the live operator view. Losing a terminal or tmux session does not stop a running engine.
- Each task uses a Git worktree and task branch under `.shikiru/worktrees/`. The project root is
  one Git repository; submodules and multi-repository projects are unsupported.
- The Leader triages, plans and verifies. The Implementer changes the project and resolves merge
  conflicts. Shikiru moves through triage, planning, implementation, review and integration in
  code; an agent report selects only a validated outcome.
- Each agent turn is a headless process on a new or resumed agent session. Plugin adapters
  normalize structured output into the shared contract. The engine does not parse provider
  protocols.

## Task flow and recovery

Reports are validated before a transition. The Leader may split planned work into dependent tasks;
otherwise work follows the configured review loop. Accepted work is integrated using the selected
strategy, local merge by default. Retries, quota waits and rung changes are automatic within the
configured policy; critical unresolved issues become Operator decisions.

The engine owns task-state transitions. CLI, MCP and dashboard clients submit durable commands;
the submit path also allocates task IDs. On restart, the engine reloads the database, reconciles
unfinished work and resumes eligible tasks on their existing agent sessions. Agent run files,
reports, streams and logs live under `.shikiru/`; retention bounds completed history.

## Agent contract

Plugins own process arguments, session resume, provider output parsing, reported usage and
provider-specific failures. The core sees normalized turn signals, reports, quota facts and failure
kinds through [`agents/contracts.py`](../src/shikiru/agents/contracts.py). The small
[agent SDK](../src/shikiru/agents/sdk/) and [contract test kit](../src/shikiru/agents/testing/)
support plugins without moving provider logic into the engine.

## State and configuration

Project configuration at `.shikiru/config.toml` overrides packaged defaults; repeated `--set`
values override both for one command. Defaults and accepted settings are defined in
[`defaults.toml`](../src/shikiru/config/defaults.toml) and
[`settings.py`](../src/shikiru/config/settings.py). Agent-specific options are validated by each
plugin.

The engine writes SQLite state and task worktrees. Operator clients use a durable command queue;
agent processes work in task worktrees and write turn reports through `shikiru report`. Git facts
are read by Shikiru rather than accepted from agent claims.

## Operator surface

The CLI covers initialization, engine lifecycle, task submission and status, diagnostics, and tmux
session discovery and attachment. The Textual dashboard provides task detail, decisions and
contextual actions. MCP exposes the operator services over stdio. Remote session access uses SSH.

The [CLI output examples](design/references/cli-output-examples.html) and
[Focus dashboard reference](design/references/focus-steel-approved.html) are the retained visual
references.

## Trust boundary

Shikiru orchestrates; it does not sandbox. Agents run with the Operator's privileges and
environment. Claude Code defaults to `bypassPermissions`; Codex runs `workspace-write` with the
task worktree's Git metadata and shared repository `.git` directory writable. Worktree isolation
is a prompt and scheduling convention, not an enforced security boundary. Owner-only `.shikiru/`
files protect stored state from other local users, not from an agent running as the Operator.
Configured notification commands and project instructions also run with the Operator's
privileges. Use Shikiru only on repositories and hosts you trust.

## Implementation boundaries

- The engine imports agent contracts and does not import provider-specific agent modules; those
  details stay in `plugins/agents/*`.
- SQL stays in `store`; subprocesses use `process.SubprocessRunner`; time uses `clock.Clock`.
- `domain` decisions are pure functions. Constructors receive dependencies; `bootstrap.py` wires
  concrete components.
- Operator tunables live in packaged defaults. Protocol values and implementation constants have
  names and are checked by the project's lint rules.
