# Shikiru (しきる)

Shikiru coordinates AI coding agents through a deterministic development workflow. The Leader
triages, plans and verifies; the Implementer changes the project. The Operator submits work,
reviews progress and resolves decisions.

Each task gets a Git worktree and branch. Shikiru validates agent reports, runs the workflow and
integrates accepted work. The terminal dashboard runs in tmux; the engine continues if its client
disconnects.

## Requirements

- Python 3.14+ and uv.
- Git 2.45+ and tmux 3.4+.
- One Git repository per project, initialized at its root with at least one commit. Submodules and
  multi-repository projects are unsupported.
- At least one supported agent CLI, Claude Code or Codex CLI, installed and configured for your account.
- `gh` is needed only if you choose pull-request integration; local merge is the default.

`shikiru doctor` checks the tool versions, project, config, plugins and local state. The minimum
versions above are enforced by Doctor. macOS is the platform exercised in this repository;
GNU/Linux has not yet been verified here.

The GitHub Actions workflow is configured to run the locked quality gate and isolated fake-agent
package smoke on Ubuntu 24.04 and macOS 15 for pushes to `main` and pull requests. No hosted run has
been observed from this checkout, so CI and Linux execution remain unverified until a run completes.

## Install

Install from a Shikiru source checkout:

```sh
cd /path/to/shikiru
uv tool install .
shikiru --help
```

To check a source checkout, run the locked sync and project quality gate:

```sh
uv sync --locked
PYTEST_ADDOPTS='-n 2 --dist=loadfile' uv run poe check
```

## Initialize a project

Start in the root of a committed project repository. The setup wizard detects the base branch,
installed agent plugins, trackers and ignored build caches, then writes `.shikiru/config.toml`.

```sh
cd /path/to/project
git rev-parse --show-toplevel
git log -1 --oneline
shikiru init
shikiru doctor
```

Review `.shikiru/config.toml`, especially every role's model and effort ladder, before starting.
`shikiru init --yes` skips the wizard and writes editable defaults with model placeholders; replace
them with models supported by your agent. Doctor checks the configured choices where the agent
provides an authoritative catalog.

The installed defaults and validated settings are documented in
[`defaults.toml`](src/shikiru/config/defaults.toml) and
[`settings.py`](src/shikiru/config/settings.py). Plugin options are defined in
[`Claude options`](src/shikiru/plugins/agents/claude/options.py) and
[`Codex options`](src/shikiru/plugins/agents/codex/options.py). Shikiru also exposes the
[agent contract](src/shikiru/agents/contracts.py), [agent SDK](src/shikiru/agents/sdk/) and
[contract test kit](src/shikiru/agents/testing/).

## Common commands

```sh
shikiru doctor
shikiru start                 # start the engine and tmux session
shikiru submit "Fix a small, specific issue"
shikiru status                # project status
shikiru status T-1            # one task
shikiru attach                # attach to this project's session
shikiru stop                  # request an orderly engine stop
```

`shikiru doctor --probe` runs live agent turns for the configured role ladders. Those turns can
consume paid provider usage; ordinary `shikiru doctor` does not run them. Use
`shikiru --set key.path=value ...` to override config for one command. The CLI also supports JSON
output with `--json`; `shikiru mcp` runs the stdio MCP server. `shikiru ls --host HOST` and
`shikiru attach --host HOST` access sessions over SSH.

## Dashboard

`shikiru start` creates a tmux session named `[shikiru] <project>`. Attach to it with
`shikiru attach`, or use `shikiru start --attach`. On Home, Up/Down selects a task, `/` searches,
`Ctrl+P` opens contextual actions, and `?` opens help. Use your tmux prefix followed by `d` to
detach; the engine keeps running.

If the engine process exits, start it again with `shikiru start`; it reloads persisted state and
reconciles unfinished work. If the tmux client or session is lost while the engine is running,
`shikiru start` ensures the session again, and `shikiru attach` reconnects. Use the dashboard or
`shikiru status` to inspect tasks and decisions.

## Trust and safety

Shikiru orchestrates agents; it does not sandbox them. Agents run as your user, inherit the
environment available to their CLI and may be able to use your credentials. Use Shikiru only on
repositories and hosts you trust.

- Claude Code uses `--permission-mode bypassPermissions` by default. It can run commands and edit
  files with your user's privileges.
- Codex uses its `workspace-write` sandbox. Shikiru adds the run directory, task worktree Git
  directory and shared repository `.git` directory as writable roots. A task can change shared
  Git refs, hooks or configuration.
- Worktree isolation is not enforced. Prompts ask agents to stay in their task worktree, but an
  agent can reach the main checkout and other worktrees.
- `.shikiru/` state is owner-only, not a sandbox. Configured notification commands and commands in
  project instructions run with the Operator's privileges too.

Review changes and commits before trusting them. Shikiru reports usage and cost only when the
agent reports those values; unknown values stay unknown.

## Repeatable project check

Automated integration tests use Shikiru's fake agent. The following manual check uses your
configured provider. Try it in a fresh, disposable clone of a committed project, not a checkout
with work you need to preserve. Choose a narrow task and a configured model you are willing to
use; task turns may incur provider charges.

```sh
git clone /path/to/project /path/to/shikiru-check
cd /path/to/shikiru-check
git rev-parse --show-toplevel
git status --short
git log -1 --oneline
shikiru init
```

Review the generated config and model ladders, then run the ordinary environment check and a small
task:

```sh
shikiru doctor
shikiru start
shikiru submit "Make one small, reviewable change to the disposable project"
shikiru status
shikiru attach
```

While a task is active, detach with your tmux prefix then `d`, and reattach with `shikiru attach`
to confirm the engine and task continue. You can also use `Ctrl+P` to pause or resume a task; if a
task raises a decision, open it in the dashboard and choose an offered answer. To check restart
recovery, request `shikiru stop`, poll `shikiru status` until it reports the engine stopped, then
run `shikiru start` and confirm the same task resumes.

When the task finishes, inspect the dashboard decision, Git history and resulting diff in the
disposable clone.

For diagnosis, `.shikiru/state.db` stores state, `.shikiru/logs/engine.log` records engine logs and
`.shikiru/runs/` holds task reports and turn files. Logging rotation and completed-state retention
are configurable under `[logging]` and `[retention]` in `.shikiru/config.toml`; see the packaged
[`defaults.toml`](src/shikiru/config/defaults.toml). `shikiru status` and the dashboard show the
current task and decision state. Repeat from a new clone when you want a clean run. Use
`doctor --probe` only when you specifically need a live agent-contract check.

## Design

See [the concise design](docs/design.md). The retained [CLI output examples](docs/design/references/cli-output-examples.html)
and [Focus dashboard reference](docs/design/references/focus-steel-approved.html) show approved
interface directions.
