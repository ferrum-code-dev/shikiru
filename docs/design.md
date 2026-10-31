# Design

Shikiru is a local coordinator for coding agents. It turns Operator requests into tasks, runs a
fixed development/review workflow and integrates accepted changes. Agents propose results through
structured reports; engine code validates them and decides what can happen next. The
[README](../README.md) describes the complete user-facing capability set and operating commands.

## Participants and terms

| Term | Meaning |
| --- | --- |
| Operator | The person submitting work, configuring policy, resolving decisions and intervening in sessions. |
| Leader | The agent role for triage, planning, implementation consultation and review. |
| Implementer | The agent role that changes code, verifies its changes, updates selected trackers and repairs integration conflicts. |
| Helper | A headless support role that reconstructs a missing report or classifies an unrecognized failure; it cannot approve work or choose workflow transitions. |
| Engine | One asynchronous orchestration process per project, protected by a project lock. |
| Task / escalation | A persisted request (`T-n`) and a decision requiring Operator input (`E-n`). |
| Agent / rung / ladder | A provider CLI plugin; one agent/model/effort choice; an ordered sequence of those choices. Roles are responsibilities, so they may use the same agent or different agents. |
| Turn / session / slot | One prompted agent execution; its resumable provider conversation; capacity for one running task. A session can span multiple turns. |

The project is one committed Git repository. Each task receives its own branch and worktree under
`.shikiru/worktrees/`, keeping concurrent task files separate. Tracker files are optional project
plans/checklists included in agent context and updated as work proceeds. Optional ignored caches
are cloned into worktrees using copy-on-write; unsupported filesystems produce a warning and no
cache clone. Neither worktrees nor cache cloning are a sandbox.

## Components and authority

```mermaid
flowchart LR
    O[Operator: CLI, dashboard or MCP] --> C[Durable command queue]
    C --> E[Project engine]
    E --> A[Agent plugins: Leader, Implementer, Helper]
    A --> R[Reports and observed signals]
    R --> E
    E --> G[Git worktrees and integration]
    E --> S[SQLite state and events]
    S --> O
```

Operator services are shared by CLI, Textual/tmux dashboard and stdio MCP. Readers expose stored
state and collected evidence; mutations enter a typed durable command queue. An acknowledgement
records whether a command is pending, applied or rejected, not whether its task is complete. Submit
allocates the task ID at enqueue time. The engine owns task-state transitions; UI clients and
agents do not directly choose state changes.

SQLite records tasks, holds, dependencies, commands, events, agent runs/sessions, escalations,
quota and recovery facts. Turn directories under `.shikiru/runs/` contain prompts, facts, reports,
streams and diagnostics. Git is the authority for branches, commits, cleanliness and integration;
agent statements about Git are not accepted as a substitute for observed repository facts.

## Workflow

1. **Triage:** Leader selects an implementation or planning route and task complexity. Explicit
   submission mode skips triage; supplied complexity or role overrides take precedence.
2. **Plan route:** Leader commits planning/tracker changes. A request to execute the plan can emit
   follow-up tasks with dependencies; a planning-only request need not emit work. The plan task
   integrates its changes. Follow-up tasks are scheduled when their dependencies are satisfied.
3. **Implement route:** Implementer changes the task worktree and reports its changes/checks.
   Questions can go to Leader consultation; unresolved judgment becomes an escalation.
4. **Review:** Leader accepts, requests rework, escalates, or supersedes the task with replacement
   tasks. Rework consumes a bounded iteration budget and may advance the Implementer ladder.
   Supersession repoints dependents to replacement work rather than silently dropping it.
5. **Integration:** Engine rebases accepted work onto the selected base and applies the configured
   strategy. Conflicts invoke bounded Implementer repair and Leader verification. Completion is
   recorded only after integration settles; an open PR is still unfinished work.

Each turn receives the request, relevant earlier reports, Operator messages and workspace facts.
A report must match the expected schema and allowed outcomes. Accepting or superseding a task with
uncommitted work or the wrong checked-out task branch is rejected by engine checks. This validates
the workflow contract; it does not establish the correctness of the implementation or its tests.

## Scheduling, agent selection and recovery

The scheduler respects dependency completion, task priority, available parallel slots, task holds,
CPU/free-RAM dispatch gates and provider quota blocks. A cancelled prerequisite cancels dependent
queued work. Limits on dispatch do not cap resources already-running agent processes can consume.
When a queued task's selected agent has a quota block beyond `max_quota_wait` (in seconds),
the scheduler advances that role to the next unblocked agent in its ladder. Shorter blocks wait
for reset; without an available later agent, the task stays queued until the block expires.

A persisted quota block is a prediction, not a lock. While a task waits on a block, in a turn's
quota wait or queued, including after an engine restart, the engine revalidates it every
`[orchestration.quota_refresh] interval_seconds` by asking the agent plugin's optional
`read_quota(since)` capability for a fresh reading, bounded by `timeout_seconds` and run off the
event loop. An Operator retry forces one revalidation. A block records when it was set; it is
released only by a reading observed after that instant that shows no exhausted window and covers
every window the block was exhausted on (`domain.quota_blocks`). Older samples, other quota buckets,
null or missing windows and the mere passing of an interval never release a block. A plugin
without the capability, or a probe that finds nothing newer, keeps the block until its deadline; a
probe that fails is an `engine_fault` (and `engine_recovered` later), never silently ignored. The
Codex plugin answers from the `limit_id: codex` bucket of its rollouts: a trailing event of another
bucket with null windows does not replace the latest usable Codex reading.

Leader and Implementer resolve their own ordered ladders; Implementer can select a low/normal/high
complexity override, otherwise its base ladder applies. A task-specific rung override selects its
agent/model/effort from the next turn. Same-provider sessions can resume; a provider change starts
a fresh session with handoff context from recorded work. Plugins own the actual resume and context
compaction behavior.

Recovery is explicit and bounded by failure type: unavailable providers receive backoff/retries,
quota failures wait for reset within policy or move to a later different-provider rung, and stalls
receive limited continuation attempts. A quota failure's reset comes from, in order, the provider's
explicit reset value, its date text (`try again at Nov 4th, 2026 7:52 AM`, read in the host's local
zone because the provider prints local time; with no zone to establish it stays unknown), then the
reset of the windows reported exhausted. No evidence leaves the reset unknown and the wait bounded
by `QUOTA_PROBE`. A Codex failure message containing `at capacity` counts as
unavailability: retry using `[orchestration.retries]`'s initial delay, multiplier and maximum delay,
then advance to the next rung when its retry count is exhausted. The packaged policy uses 30 seconds,
a multiplier of 4, a 300-second cap and three retries, which gives delays of 30, 120 and 300 seconds.
The retry count can be set to zero for immediate ladder fallback and is capped at 20. These settings
are loaded when the engine starts; stop and restart it to apply changes. A later rung may use another
model on the same agent. Engine tick retries remain configured separately under
`[orchestration.faults]`.
Authentication errors, unsupported/unclassified failures, missing reports and exhausted limits
produce visible decisions.
Helper can rebuild a missing report or classify an otherwise unknown failure, trying its own
ladder after call failures or unusable answers within one shared timeout. Its output is validated
before recovery uses it.

An escalation retains the question, evidence, what was tried and applicable resolutions. Operator
answers can return to Leader consultation; retry, accept-as-is or cancellation is offered only
where applicable. An escalated or held task does not prevent independent eligible tasks from
progressing. Engine infrastructure faults are recorded and retried with backoff; repeated
unexpected faults can stop the engine instead of looping silently.

## Git integration and lifecycle

Local **merge** rebases the task branch, then fast-forwards a checked-out base or updates its ref
with an expected-old-value check. Dirty paths overlapping the incoming changes block integration;
blocked prerequisites are retried within a limit and then escalated. Task worktrees are checked
before integration so off-branch or uncommitted work is not silently discarded.

Local **squash** shares those preconditions, the serialized integration boundary and conflict
consultation. After the rebase it creates one commit of the task branch's complete tree on the
current base tip, titled with the task ID and title and carrying `Shikiru-Task` and
`Shikiru-Squashed-Tip` trailers, then fast-forwards a checked-out base or updates its ref with the
same expected-old-value check; a task whose tree equals the base's lands nothing. The original
branch is no longer an ancestor of the base, so a restart before completion is recording finds the
already landed commit by the branch-tip trailer instead of squashing again. `TASK_DONE` persists the
landed commit list and the integrated file, insertion and deletion totals before cleanup; Result and
Results derive from them, so an empty integration shows zero commits rather than falling back to
earlier reports. Records without these fields keep their previous derivation.

**Pull-request** mode pushes the rebased task branch and creates or adopts a GitHub PR through
`gh`. The engine polls its state, requires the merge to land, fetches and verifies the resulting
base, and handles conflicts, failed checks, closure and timeout explicitly. Repository review and
merge policy remain in force. Integration progress is recorded for restart reconciliation.

In merge and squash modes a push service publishes locally integrated bases to `repo.remote`. Each completed
integration is registered with the base's durable pending state in the same transaction that
completes the task, so a crash cannot lose it. The first pending integration fixes the deadline
(`orchestration.push_interval_seconds`, default four hours); later ones do not postpone it, and zero
pushes right after each integration. Pushes run off the engine loop with a timeout, send only
`refs/heads/<base>` without force, tags or task branches, and are acknowledged up to the registered
count, so an integration arriving during a push stays pending. Failures use the engine-fault
backoff and recovery events per base, so a rejected base keeps its work pending without holding
back the others; shutdown awaits or cancels an outstanding push. Pull-request mode does not create the service. Changed values load on engine restart.

Successful tasks release their active sessions/worktrees. Cancellation retains the branch and
preserves a worktree when it contains unintegrated work. Terminal detach leaves engine and agents
running. Engine Stop interrupts supervised agent turns and ends orchestration while retaining the
tmux session; task pause/cancel controls have separate semantics. Start reloads persisted state,
reconciles unfinished turns, integration and pending commands, and resumes eligible work. It also ensures the project tmux
session; losing a view does not itself delete task state.

## Operator intervention and views

Pause holds after a turn; Pause now interrupts it. Messages are delivered to a role at the next
turn. Retry re-runs the current step, and prioritization changes dispatch order. These actions use
the same queued-command path from dashboard or MCP.

Take over holds task automation, interrupts its active flow and opens an existing role session
interactively in its tmux pane. Other eligible tasks keep running. Hand back restores engine
ownership and queues notice that the Operator worked in the session; an independent pause remains
until resumed. Ownership changes and their command IDs are recorded together so command replay
does not duplicate the transition. A lost takeover pane is released and reported.

The dashboard combines task briefings with results, relationships, reports/Git evidence, event and
agent activity, usage and workspace access. Actions are contextual; disabled operations explain
why they are unavailable. History distinguishes recorded evidence from current observations and
missing data. Export writes retained evidence as Markdown/JSON, optionally including agent signals,
without changing engine state. Command history and diagnostics expose delivery failures, engine
faults and resource conditions.

CLI provides setup/lifecycle/status/discovery; its JSON results support scripts. MCP additionally
exposes task controls, decisions, session peeks and event waiting. SSH supports remote discovery
and attachment, not a separate distributed scheduler. Discovery isolates stale-project errors so
healthy sessions stay usable, and offers explicit repair actions without mutating project state.
Shikiru admits one attached client per project session; direct raw tmux use is outside that policy.

## Configuration, observability and extensions

Configuration merges packaged defaults, project `.shikiru/config.toml` and invocation `--set`
overrides, then validates common and plugin-specific settings. Packaged defaults declare no role
ladders because agents and models are project choices. A ladder rung is either an
`agent:model[:effort]` string or an inline `{ agent, model, effort }` table, and one ladder may mix
both. The short form takes its third part as effort. `init` and the wizard write a rung as a string
only when parsing that string gives back the same rung; a model or effort containing `:`, or an
empty effort, is written as a table. The wizard configures repository,
role ladders, trackers, caches, orchestration limits (maximum parallel tasks, iterations, quota wait,
stall and tool timeouts, push interval in seconds, CPU percent and free RAM) and integration, and finishes with Doctor. Its
orchestration values are checked against the step's own rules at Finish, before anything is written,
and an out-of-range value focuses its field; hand-written configs keep the settings validation alone.
Model catalogs come from installed plugin/CLI capabilities; lack of authoritative data is visible
rather than a claim that an arbitrary model is supported. Probe mode runs live checks; ordinary
diagnostics do not run workflow turns. When the engine starts a new run, the dashboard re-applies the
settings its views and widgets read as they render: gauge thresholds, briefing line counts, timeline
refresh, animations, the palette confirmation delay, and the disk, Docker and resource refresh and
sampling intervals. Timeouts, Docker scope, the repository status cache, the motion timer and the
widget list keep the values the dashboard started with. An invalid file keeps the last valid values
and raises a header alert.

Plugins normalize streamed signals, reports, session identity, errors, usage, billing and quota
into [agent contracts](../src/shikiru/agents/contracts.py). The engine imports those contracts,
not provider implementations. Agent and widget plugins are discovered through entry points;
the [SDK](../src/shikiru/agents/sdk/) and [contract kit](../src/shikiru/agents/testing/) support
additional agents. Built-in widgets cover tasks, decisions, usage/quota, host/process resources,
Docker, disk and faults. Layout, titles, warning thresholds, refresh and motion are configurable.

Usage preserves provenance and uncertainty: absent tokens/cost/context/billing are not invented,
and quota readings can be stale. An active quota block shows as a banner in the home usage section
and in the expanded usage view, counting the tasks waiting on it; a queued task whose recorded wait
reason is quota, or a running task in a mid-turn quota wait, carries an amber `quota` marker in the
task list. Every human-facing quota deadline is shown in the viewer's local timezone, with the date
only when the reset falls on another local day and a `Z` only for a zero offset; the domain
formatter renders the zone it is given and the dashboard boundary localizes, while storage, events
and JSON stay UTC-aware. CPU/RAM, disk,
Docker and fault diagnostics complement task progress. Notifications go to tmux and an optional
external command receiving JSON on stdin.
A durable delivery cursor replays undelivered urgent events after restart; delivery is not
exactly-once, so external hooks must tolerate repeats.

Rotating engine logs and configurable retention bound stored history. Retention removes eligible
finished run files/events and completed commands while protecting unresolved work, holds, retained
worktrees and unsettled cancelled PRs. Export before expiry when long-term evidence is needed.
Deleting `.shikiru/` destroys its local state; initialization cannot reconstruct deleted history.

## Trust and implementation boundaries

Agents run with the Operator's privileges and environment. Claude Code defaults to
`bypassPermissions`; Codex uses `workspace-write` with the run directory and task/shared Git metadata
writable. Worktree separation is a convention, not enforced host isolation. Owner-only state files
protect against other local users, not an agent running as the Operator. Project instructions,
provider tools and notification commands can execute with those privileges; reports/exports may
contain sensitive project data. Agent review is assistance, not a correctness or security proof.

Implementation boundaries keep decisions auditable: pure domain rules; SQL in `store`; subprocesses
through `process.SubprocessRunner`; time through `clock.Clock`; constructor-injected dependencies;
concrete wiring in `bootstrap.py`; provider details in `plugins/agents/*`. Tunables and validation
live in [defaults](../src/shikiru/config/defaults.toml) and
[settings](../src/shikiru/config/settings.py). The [CLI examples](design/references/cli-output-examples.html)
and [dashboard reference](design/references/dashboard-example.html) retain the visual direction.
