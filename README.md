# Shikiru (しきる)

Shikiru coordinates coding agents through a defined development workflow: turn a request into
reviewed changes, integrate them, and ask you when a decision needs human judgment. It runs locally
with Git worktrees, a persistent engine and a terminal dashboard in tmux. Claude Code and Codex CLI
are built in; different agents and models can work on the same task in different roles.

## How it works

| Participant | Responsibility |
| --- | --- |
| **Operator** | You: submit requests, choose policy, inspect results, answer decisions and intervene. |
| **Leader** | An agent that triages requests, plans larger work, answers implementation questions and reviews changes. |
| **Implementer** | An agent that changes code, runs project checks, updates selected trackers and resolves integration conflicts. |
| **Helper** | A headless agent used to reconstruct missing structured reports or classify otherwise unrecognized agent failures. It does not run the development workflow. |
| **Engine** | Shikiru's deterministic coordinator: schedules tasks, validates reports, applies transitions, manages Git integration and records state. |

A request receives a task ID such as `T-1`, its own branch and a Git worktree. The Leader normally
triages it into one of two routes:

- **Implement:** the Implementer makes changes; the Leader accepts them, requests another iteration,
  raises an Operator decision, or replaces the task with more suitable tasks.
- **Plan:** the Leader commits a plan and can create dependent follow-up tasks when the request asks
  for execution. A request only to produce a plan can finish with the plan itself.

Accepted changes are rebased onto the configured base and integrated locally or through a GitHub
pull request. Conflicts go through bounded Implementer repair and Leader review. Requests needing
your input, exhausted recovery limits and unsafe integration conditions become **escalations**
(`E-1`, etc.) with context and applicable answers. Other eligible tasks can continue.

You can submit work with explicit mode, complexity, priority, dependencies or role/model choices.
Independent tasks run in parallel up to the configured slot limit; dependencies, pauses, resource
pressure and provider quota can delay dispatch. Each task retains reports, Git facts, agent sessions
and a timeline so you can understand both its result and how it got there.

## Requirements and installation

- Python 3.14+ and uv; Git 2.45+ and tmux 3.5+.
- A single Git repository per project, initialized at its root with at least one commit.
  Submodules and multi-repository projects are unsupported.
- Claude Code or Codex CLI installed and configured for your account.
- `gh` and an accessible GitHub remote if you choose pull-request integration.

Install from a Shikiru source checkout:

```sh
cd /path/to/shikiru
uv tool install .
shikiru --help
```

macOS is the platform exercised locally. CI is configured for Ubuntu 24.04 and macOS 15; see
[workflow runs](https://github.com/ferrum-code-dev/shikiru/actions/workflows/ci.yml) for
platform-specific results.

## Initialize and run

Start at the root of a committed project repository:

```sh
cd /path/to/project
shikiru init
shikiru doctor
shikiru start
shikiru submit "Fix a small, specific issue and verify it"
shikiru attach
```

The eight-step wizard checks the repository, builds role/model/effort ladders, lets you choose
tracker files and ignored cache paths, sets the orchestration limits, reviews each agent's options and
chooses the integration mode, then writes `.shikiru/config.toml` and runs Doctor. The Orchestration step sets the maximum parallel
tasks, the maximum passes per task, the maximum quota wait in seconds, the stall and tool timeouts
in seconds (the silence allowed before a turn is stopped as stalled, without and with a running
tool), the push interval in seconds (default 14400, four hours; 0 pushes after every local
integration), the host CPU percentage below which new tasks may start, and the free RAM in GB that must be
available before a new task starts. Each field starts at the project default, and Finish rejects an
out-of-range value by focusing that field before anything is written. The Agents step lists the
options each installed agent plugin offers (for the built-in Claude Code and Codex plugins: the
compaction threshold in percent, the billing declaration, left unset unless you choose one, and
extra CLI arguments split like a shell command line). They are written explicitly under
`[agents.<name>.options]` so you can edit them later; an invalid value focuses its field and
nothing is written. An agent plugin without the settings capability is named on that step and
keeps its own defaults. Available model and effort
choices come from agent plugins; where no authoritative catalog exists, Doctor cannot promise model
availability. Tracker and cache fields start empty: add only what you want Shikiru to use. Cancel
explicitly with the Cancel button; `Ctrl+F` briefly reveals the background artwork.

`shikiru init --yes` skips the wizard and writes editable defaults with model placeholders. Replace
them with models supported by your agent, review the config, and run Doctor before starting.
Ordinary `doctor` checks environment, config, plugins and state without running task turns;
`doctor --probe` additionally runs live agent-contract checks and can consume paid provider usage.

## CLI

Project commands find the nearest `.shikiru/` above the current directory; use the global
`--project PATH` to select a project explicitly. `--help` on each command describes its arguments
and defaults; the root help also offers shell-completion setup and `--version`.

| Command | Use |
| --- | --- |
| `shikiru init` | Configure a repository through the wizard; `--yes` writes editable defaults. |
| `shikiru start` | Start or reuse its detached engine and ensure its tmux session; `--attach` enters it. |
| `shikiru stop` | Stop orchestration while keeping project state and tmux agent panes; `--kill-session` also closes that project's tmux session. |
| `shikiru submit "REQUEST"` | Queue work and return its allocated task ID. |
| `shikiru status` | Show engine/queue state, tasks, escalations, usage and quota; add `T-1` for one task or `--watch` for updates. |
| `shikiru ls` | List discovered project sessions; `--host HOST` queries an SSH host. |
| `shikiru attach` | Attach to the current project; outside a project, choose a numbered session. A project name selects it directly; `--host HOST` uses SSH. |
| `shikiru doctor` | Diagnose requirements, config, plugins and local state; `--probe` includes live checks. |
| `shikiru mcp` | Serve the project's operator tools over stdio to an MCP client. |

For example, skip triage for a known implementation, choose a complexity ladder and wait for
another task:

```sh
shikiru submit --mode implement --complexity high --priority 10 --after T-1 "Implement the accepted design"
shikiru status T-2 --watch
shikiru --json status
shikiru --project /path/to/project --set orchestration.max_parallel=1 start
```

`--after` is repeatable. `--leader` and `--implementer` pin a role using
`agent:model[:effort]`; obtain supported IDs from your installed agent and wizard. Global `--json`
provides machine-readable command results. Queue acknowledgement and a task ID mean the request
was recorded, not that the work finished; inspect status or the dashboard for the outcome.

Session discovery preserves healthy results alongside diagnostics for stale or unreadable project
state, with applicable repair commands. `ls` exits nonzero when discovery is incomplete. Deleting
`.shikiru/` loses its state; initialization creates new state, not a recovery of deleted tasks.
Shikiru permits one attached client per project session and refuses a second attachment rather
than changing the existing terminal. Detach that client before moving to another terminal.

## Dashboard and Operator control

Home shows the engine, repository cleanliness, task queue, active work, decisions and usage.
Up/Down selects tasks; `f` filters status, `/` searches by ID or title, and `m` loads older tasks
when offered. Task details bring together the current briefing, results, dependencies and related
tasks, reports/Git evidence, event and agent activity, usage, sessions and workspace access.
Timelines support paging, following the latest entries and agent-activity filters by role/turn.
Use `?` for context-specific navigation help.

Open **Actions** with `Ctrl+P` or `F12` in the dashboard, or your tmux prefix followed by Space in
any Shikiru pane. Actions apply to the selected task and, where appropriate, a chosen role.
Availability depends on task/session state; **More…** exposes additional controls.

| Action | Effect |
| --- | --- |
| **Submit / Prioritize** | Add work or change its dispatch priority. |
| **Message** | Send guidance to a role at the start of its next turn. |
| **Take over / Hand back** | Stop automation for the task and open an existing role's agent session interactively in its pane. Hand back returns ownership to the engine, which continues with notice of your intervention. An existing pause remains until Resume. |
| **Pause / Pause now / Resume** | Hold after the current turn, interrupt immediately, or clear the pause. Pausing one task leaves other eligible tasks free to progress. |
| **Rung** | Select another agent/model/effort for that role from its next turn. A provider change uses a fresh session with handoff context. |
| **Open session** | Inspect recorded activity for a task role without taking ownership of its agent session. |
| **Retry** | Re-run the current step. |
| **Cancel** | Abandon the task while keeping its branch; unintegrated work can retain its worktree for inspection. |
| **Answer** | Resolve an escalation using an offered choice or written answer; retry, accept-as-is and cancel are available where applicable. |
| **Export** | Write retained task evidence as Markdown or JSON, with optional agent signals and explicit gaps for unavailable evidence. |
| **Command history / Diagnostics** | Inspect pending, applied or rejected commands, engine health, faults and resources. |
| **Start engine / Stop engine** | Control project orchestration from the dashboard. |

The task's tmux window provides role panes and a shell. Task details can open that workspace;
resuming the agent interactively through Take over is different from just viewing its output.
The default tmux prefix is `Ctrl+B`; prefix then `d` detaches without stopping the engine.
`shikiru attach` reconnects later.

Built-in widgets include usage/quota, host and process CPU/RAM, disk space and project/run sizes,
Docker container health/resource use, and engine faults. Docker is optional; unavailable samples
are reported. The configured widget list controls what is enabled; extended views are available
through Actions. Pane layouts, titles, refresh rates, warning thresholds and animation are
configurable.

## Agents, ladders and recovery

A **rung** is one agent/model/effort choice. A **ladder** is an ordered list of rungs: it lets
Shikiru progress to another choice under its recovery/review policy, including a different provider.
Leader and Implementer have their own ladders. Implementer can additionally use `low`, `normal`
and `high` complexity ladders; an unspecified complexity ladder inherits its base ladder.

In `.shikiru/config.toml`, a rung can be written as a short string, `"agent:model"` or
`"agent:model:effort"`, or as an inline table, `{ agent, model, effort }` (effort optional).
Both forms can appear in one ladder, and existing table ladders keep loading unchanged:

```toml
[roles.leader]
ladder = ["codex:gpt-6.1-sol:high", { agent = "claude", model = "claude-sonnet-5-5" }]
```

The short form reads its third part as the effort, so a model or effort containing `:`, or an empty
effort, must use the table form. A malformed short entry, or one with an empty part, fails config
loading and names the entry. `init` and the wizard write the short form, and the table form for any
rung the short form cannot express.

Rejected implementations return for another review iteration, with a possible Implementer rung
increase. Provider unavailability gets bounded backoff/retries; quota can wait for a reset or move
to a later different-provider rung when the configured wait budget is exhausted. Stalls receive
limited continuation attempts. Authentication problems, unrecoverable failures and exhausted limits
request Operator attention rather than silently completing a task.

Helper has a separate ordered ladder. It tries later entries after failed calls or unusable
answers, within one total Helper timeout. Any rebuilt report must still pass validation; Helper
neither approves work nor decides the workflow. Agent session resumption and compaction are
provider-specific; automatic compaction thresholds are configurable per agent. Token counts,
context, costs, billing mode and quota are shown when supplied by
the agent; missing data stays unknown, and quota observations may be stale. A quota block is
revalidated periodically in the background (and on an Operator retry) against newer provider readings, without delaying other tasks or commands, so queued work
resumes when quota returns before the predicted reset. Quota reset times are shown in your local
timezone. A task waiting on quota shows an amber `⧗ quota` in its tmux window tab, and each agent
pane affected by the wait shows a `⧗ Quota blocked · next check …` (or `retry …`) strip above its
output until the wait ends.

## Configuration and automation

Settings merge in this order: packaged defaults → `.shikiru/config.toml` → repeated global
`--set key.path=value` overrides for that invocation. Use the wizard for project choices and edit
the config for additional controls. An already-running engine keeps the settings it started with;
stop/start it to apply engine policy changes. When a new engine run starts, the dashboard re-reads
the config for slot counts, gauge thresholds, briefing and timeline display, animations, the palette
confirmation delay, and the disk, Docker and resource refresh intervals; timeouts, Docker scope, the
repository status cache, the motion timer and the widget list apply when the dashboard starts. An
invalid config keeps the last valid values and shows an alert in the header.

| Configuration area | Controls |
| --- | --- |
| `project`, `repo` | Project name, tracker files agents read/update, integration base/remote, ignored cache paths. |
| `roles`, `agents` | Role and complexity ladders, review escalation policy; plugin CLI arguments, compaction threshold and optional billing declaration. |
| `orchestration` | Parallel task slots, review iterations and local merge, local squash or pull-request integration; `push_interval_seconds` (seconds between pushes of locally integrated bases, default 14400) and `remote_recheck_seconds` (seconds before an absent remote is checked again, default 60); the init wizard also sets the stall and tool timeouts, the quota, CPU and free-RAM limits below. |
| `orchestration.resources` | CPU/free-RAM gates for dispatch; these do not impose CPU/RAM limits on already-running agents. |
| `orchestration.quota_refresh` | `interval_seconds` between quota block revalidations (default 300) and `timeout_seconds` bounding each probe (default 30). |
| `orchestration.timeouts`, `.network`, `.faults`, `.retries` | Stall/tool/Helper/quota/integration/PR limits, remote polling, engine-fault retries, and configurable provider-unavailable retries. |
| `dashboard` | Widgets, pane splits/titles, animation, display/refresh limits and resource/quota warning thresholds. |
| `sessions`, `doctor`, `escalation` | SSH-query timeout, live-probe limits and the amount of attempted work summarized in decision context. |
| `notify` | Optional external notification command and timeout; receives a JSON event on stdin. |
| `logging`, `retention` | Engine log rotation and pruning horizons for finished run files, events and completed commands. |

**Integration:** `orchestration.integration = "merge"` rebases and applies accepted work to the
local base. `"squash"` ("Local squash and merge") lands the rebased task as a single commit parented
by the current base and carrying the task's complete tree; an empty task adds no commit. It shares
every local-merge guard, and the Result and Results of the completed task show that commit's SHA and
subject, its destination and the integrated file and line totals, which stay accurate after the task
branch and worktree are cleaned up. `"pr"` pushes the task branch, opens/adopts a GitHub PR and waits for it to merge;
conflicting/closed/failed or overdue PRs require recovery or a decision. Shikiru does not bypass
repository review policy. Dirty overlapping changes in the main checkout block local integration;
commit, move or otherwise resolve them before retrying.

**Pushes (merge and squash modes):** `orchestration.push_interval_seconds` (default `14400`, four hours; a
nonnegative whole number of seconds) controls when locally integrated work reaches `repo.remote`.
Integrations within an interval accumulate and the first one sets the deadline, so later ones do
not postpone it. `0` pushes after each successful local integration. Only the base branch is pushed,
never forced and never task branches or tags; a rejected or timed-out push raises an engine fault,
keeps the work pending and retries with backoff. A remote that is not configured is not pushed to
and raises no fault: the work stays pending and the remote is checked again after
`orchestration.remote_recheck_seconds` (default `60`, a positive whole number of seconds). A check
that itself fails is a fault like a failed push: it is retried with the same backoff and recovers
once the check succeeds. Pending work survives restarts. PR mode does not use this setting. Stop and restart the engine to load a changed value.

**Caches:** selected Git-ignored cache paths can be cloned into task worktrees using filesystem
copy-on-write. If unsupported, Shikiru warns and leaves caches uncloned; it does not silently make
an expensive ordinary copy. Trackers and caches are optional.

**Notifications:** tmux messages and an optional hook report completions, escalations, integration
waits, lost takeovers and engine faults. Urgent undelivered notifications survive restarts; a crash
can cause a duplicate delivery, so hooks should tolerate repeats.

**Provider retry timing:** `[orchestration.retries]` configures unavailable-agent retries in
seconds, with a multiplier and maximum delay. Defaults are 30 seconds, multiplier 4, 300 seconds
maximum and 3 retries (delays of 30, 120 and 300 seconds before ladder fallback). The retry count
may be zero and is limited to 20. Stop and restart the engine to load changed values. These settings
are separate from `[orchestration.faults]`, which controls engine tick retries.

**Automation and extensions:** CLI JSON supports scripts. MCP exposes status/task/escalation queries
(an escalation carries its packet plus parsed `question`, `agent_options` and `resolutions`),
session peeks, submission, prioritization, pause/resume/cancel/retry, rung changes, messages,
takeover/handback, escalation resolution and event waiting. It uses the same operator services and
command queue as the dashboard. Agent and widget plugins use Python entry points; the
[agent contract](src/shikiru/agents/contracts.py), [SDK](src/shikiru/agents/sdk/) and
[contract test kit](src/shikiru/agents/testing/) support additional agents. A plugin opts into the
wizard's Agents step by giving its agent a `settings()` method that returns
[`AgentSetting`](src/shikiru/agents/settings.py) entries (key, label, help, kind, default and
bounds or choices); the plugin's own factory still validates the resulting options. It can raise
[`AgentOptionsError`](src/shikiru/agents/settings.py) with the rejected option key so the wizard
focuses that field, or omit the key for an agent-wide rejection.

For exact fields and defaults, see [defaults.toml](src/shikiru/config/defaults.toml),
[settings validation](src/shikiru/config/settings.py), [Claude options](src/shikiru/plugins/agents/claude/options.py)
and [Codex options](src/shikiru/plugins/agents/codex/options.py). The [MCP tools](src/shikiru/mcp/server.py)
provide the automation contract.

## Persistence, diagnosis and safety

The engine is independent of the terminal. Restart it with `shikiru start` to reload persisted
state, reconcile unfinished work and resume eligible tasks. Re-running `start` also ensures the
project's tmux session. Terminal detach, engine stop, task pause and task cancellation are distinct
operations: detaching changes only your connection; Stop interrupts engine-supervised turns and
ends orchestration while preserving state and the tmux session. Start reconciles and resumes
eligible work. Use task controls when you need to halt a specific task without stopping others.

| Project path | Contents |
| --- | --- |
| `.shikiru/config.toml` | Project settings. |
| `.shikiru/state.db` | Tasks, commands, events, sessions, decisions and recovery state. |
| `.shikiru/worktrees/` | Per-task Git checkouts. |
| `.shikiru/runs/` | Turn prompts, reports, collected facts, streams and logs. |
| `.shikiru/logs/engine.log` | Rotating engine diagnostics. |

Use Doctor for setup/state problems, status for progress and decisions, and dashboard Diagnostics
for live resource/fault information. Retention removes eligible finished evidence after configured
horizons while preserving unresolved work; export evidence you need to keep. Missing/corrupt state
is not repaired by deleting `.shikiru/`.

Shikiru orchestrates agents; it does not sandbox them. Agents run as your user, inherit their CLI's
environment and may use your credentials. Use only repositories and hosts you trust.

- Claude Code defaults to `--permission-mode bypassPermissions`.
- Codex uses `workspace-write`, with the run directory, task worktree Git directory and shared
  repository `.git` writable. Agents can affect shared Git refs, hooks and configuration.
- Worktrees separate task files, but do not enforce isolation from the main checkout or other
  worktrees. Project instructions and notification commands also run with your privileges.
- State files are owner-only, not a security boundary against agents running as you. Prompts,
  reports and exports can contain project-sensitive content; review them before sharing.

Review changes and commits before trusting them. Agent review and a valid report are not proof that
software is correct.

## Validate a checkout and a real project

```sh
uv sync --locked
PYTEST_ADDOPTS='--dist=loadfile' uv run poe check
bash scripts/release-readiness-smoke.sh
```

The quality gate covers formatting, lint, boundaries, types, unused-code checks and behavior tests.
Real Claude/Codex adapter code is tested offline using recorded protocol output and controlled
subprocesses: invocation, parsing, resumption, compaction, usage and failures. Engine integration
uses the fake-agent plugin with real Git, SQLite and private tmux. Live provider contracts and
real-agent end-to-end scenarios are opt-in and excluded from normal CI.
The package smoke builds wheel/sdist and exercises an isolated installed package with a fake agent,
a disposable HOME/repository and private tmux server; it makes no paid provider calls. GitHub CI
syncs the locked development environment and runs both checks with one test worker per CPU core
(`SHIKIRU_TEST_CPUS_PER_WORKER=1`) and job timeouts. The isolated installation resolves the
built package's declared runtime dependencies.
Automated checks do not establish reliability for every provider or real project.

### Verify real agents

These checks use your authenticated agent CLIs and consume real provider usage. Run them locally
from the Shikiru checkout after `uv sync --locked`, with Git/tmux available and the agents you are
testing installed, authenticated and permitted to execute the scratch project's commands. Run
sequentially (`-n 0`); timeouts are not spending or RAM limits.

For a configured project's actual role ladders, start with `shikiru doctor --probe` in that project.
For automated adapter verification, choose model IDs supported by your account first:

- Claude contract tests accept `SHIKIRU_CLAUDE_LIVE_MODEL` and
  `SHIKIRU_CLAUDE_LIVE_CHANGE_MODEL` for the initial and model-change checks. Set both to supported
  models; the model-change check skips if no second model is configured.
- Codex contract tests currently use `_LIVE_MODEL`, `_LIVE_OTHER_MODEL` and `_EFFORT` in
  [their test module](tests/integration/test_codex_headless_contract.py). Review/update those test
  constants for your account; they are separate from project configuration and have no equivalent
  model environment overrides.

Enable live checks only for the invocation below. Include just the test file for each agent you
have configured (both are shown):

```sh
SHIKIRU_E2E=1 uv run pytest -n 0 --no-cov -k live \
  tests/integration/test_claude_headless_contract.py \
  tests/integration/test_codex_headless_contract.py
```

These exercise report submission, measured usage, session/model resumption, stop/resume, structured
Helper answers and context compaction. The compaction check deliberately grows context and can
consume substantially more usage than a basic report check. Review skips as well as failures.

For complete workflow verification, review `REAL_AGENT`, `REAL_MODEL`, `REAL_EFFORT` and
`REAL_ROLE_LADDERS` in [real_agent_config.py](tests/e2e/real_agent_config.py); these configure all
roles in disposable fixture repositories, independently of your `.shikiru/config.toml`. Start
with one completion/integration scenario:

```sh
SHIKIRU_E2E=1 uv run pytest -n 0 --no-cov \
  tests/e2e/test_real_agents.py::test_real_agents_e1_completes_and_integrates
```

Then run all six real-agent scenarios:

```sh
SHIKIRU_E2E=1 uv run pytest -n 0 --no-cov tests/e2e/test_real_agents.py
```

They cover completion/integration, next-turn Operator messages, planning with dependent parallel
tasks, MCP decision resolution, restart recovery and takeover/handback. Tests use private tmux
servers and disposable projects under `~/.cache/shikiru-e2e/`, with cleanup afterwards. Results go
to `build/e2e/`; failure diagnostics go to `build/e2e/diagnostics/`. The harness has per-attempt and
one-hour invocation time budgets and can retry an eligible failed scenario once, consuming
additional usage. Its parallel-work scenarios disable CPU/free-RAM dispatch gates; monitor your
host and account usage. See [budgets.py](tests/e2e/budgets.py) for the current limits. These paid
checks are excluded from normal CI and have not been run as part of this cleanup.

### Check your own project

For a provider-backed check, clone a committed project into a fresh disposable directory, initialize
Shikiru, review its ladders and submit one small, reviewable change. While active, detach/reattach,
exercise a task pause and, where useful, Take over/Hand back; stop/start the engine to inspect
recovery. Confirm the resulting diff, tests and Git history. Use a model and account you are willing
to spend usage on; `doctor --probe` is also a live check.

## Design

[Design](docs/design.md) explains the architecture and invariants. The retained
[CLI examples](docs/design/references/cli-output-examples.html) and
[dashboard reference](docs/design/references/dashboard-example.html) show the interface direction.
