# 03 · Workflow

## 1. Task model

### 1.1 Fields and state

| Field | Meaning |
|---|---|
| `id` / `seq` | `T-<seq>` |
| `parent_id`, `origin` | `origin` is `operator` or `leader` |
| `title`, `request` | `request` is the verbatim text |
| `mode` | `implement` or `plan`. `NULL` until triage, unless `submit --mode` sets it |
| `complexity` | `low`, `normal` or `high`. Unset (`submit` without `--complexity`) until triage decides; an explicit value, `normal` included, is never replaced. The ladder uses `normal` while it is unset |
| `priority` | Integer; higher runs first |
| `depends_on` | Task ids |
| `status` | §1.2 |
| `step` | `triage`, `plan`, `implement`, `consult`, `verify` or `integrate` |
| `status_reason` | e.g. `superseded`, `cancelled by operator` |
| `holds` | Task-scoped: `paused`, `intervention` |
| `iteration` | Implement → verify rounds so far |
| `counters` | Persisted: `rejections_on_rung` (reset whenever the Implementer's rung changes: an automatic move, `set_rung` to a different rung, `retry --rung`), `consults` (reset each iteration), `conflict_rounds` (reset per integration attempt). Resolving an escalation resets the counter behind its reason |
| `rung_index` | Per role, persisted. Never decreases |
| `overrides` | Operator rungs per role |
| `repo` | `{base, base_ref, base_sha, branch}`, set when the task root is built |
| `slot`, `window_id`, `panes` | `panes` maps `{role: pane_id}` (the only place pane ids are stored) |
| `wait_reason` | Written by the scheduler, and `PR open` by `PullRequest` (§6) |
| `requeued_at` | Set when resumed after an escalation; gives precedence |
| `summary` | Final summary |

Counters that only matter within one turn (nudges, stall retries) live in memory. A restart re-runs the turn with those counters reset, which is bounded and safe.

### 1.2 Status

```
queued ──► running ──► integrating ──► done
   ▲          │             │
   └──────── escalated ◄────┘            any non-terminal ──► cancelled
```

| Status | Holds a slot |
|---|---|
| `queued` | no |
| `running` | yes (also while `paused`; `pause` on a `queued` task only keeps it from being dispatched) |
| `integrating` | yes, except while a PR is open (§6) |
| `escalated` | no. The worktree, sessions and window stay. On re-dispatch the task gets its previous slot if free; otherwise its next turns resume the sessions under the new `SHIKIRU_SLOT` |
| `done` | — terminal: window closed, worktrees removed, run files kept |
| `cancelled` | — terminal: window closed, the task's open pull request closed (§6), worktrees removed (one with uncommitted work is kept, §6), run files kept |

**Holds**:
- `paused` and `intervention` are task-scoped (§9).
- The **quota hold is derived, not stored on tasks**: a task whose next turn needs an agent with a row in `agent_blocks` waits until `until`.

## 2. Steps

| Step | Role | Template → report | Outcomes |
|---|---|---|---|
| triage | Leader | `triage` → `TriageReport` | `implement` → implement · `plan` → plan (also when the request needs decomposing into several tasks, as one spanning several tracker entries does; never for an open decision, which plan cannot raise and implement raises through consult) |
| plan | Leader | `plan` → `PlanReport` | → integrate; `new_tasks` are enqueued (the template tells the Leader to fill them only when the request asks to execute the plan, giving each new task its own tracker entry in place of this task's) |
| implement | Implementer | `implement` (i = 1) / `rework` → `ImplementationReport` | `completed` → verify · `needs_decision`/`blocked` → consult |
| consult | Leader | `consult` → `ConsultAnswer` | `answer` → implement (same iteration) · `escalate` → `decision_required`. More than `CONSULT_LIMIT` (3) consults → `decision_required` |
| verify | Leader | `verify` (+ `facts.md`) → `Verdict` | `accept` → integrate · `changes_requested` → implement (i + 1) · `escalate` → `decision_required` · `supersede` → `new_tasks` enqueued, dependents repointed to them, this task `cancelled` (`superseded`) |
| integrate | Implementer on conflicts only | `conflicts` → `ImplementationReport` | §6 |

**Final round.** When `iteration >= max_iterations`, the verify prompt carries `final_round = true`:
- The Leader must choose `accept`, `supersede` (decompose into new tasks) or `escalate`.
- A `changes_requested` in the final round raises `iterations_exhausted`.

**Triage** is read-only (the prompt forbids edits). Implement and rework turns tick the task's own tracker entry per project conventions; `plan` turns restructure entries (each new task gets its own, and ticks it when done); verify turns never change code or tests (anything missing or wrong is `changes_requested`) and edit only trackers, on scope changes, committing those edits.
A completed triage persists its `title` and `complexity` with the transition, so the next turn uses the ladder for that complexity (§3). An operator-chosen complexity (any of the three, `normal` included) keeps precedence over the triaged one; only an unset complexity takes the triaged value. The triaged `mode` is written in the same UPDATE as the transition.
`submit --mode` skips triage. The title is then the first line of the request, cut to 60 characters.

**Templates.** Every template:
- includes the request and earlier reports (fenced as quoted data), tracker paths, the sentence "If this work is already done in the workspace, verify it and report", and a workspace sentence naming the absolute task root: "Your workspace is `<task root>`, the current directory: read and change files only there. The project checkout it came from is off limits.";
- ends with the fixed report instruction (§4.3);
- never restates project coding rules.

`verify` (also after a rebase) tells the Leader to review the change with `git diff <base>...HEAD` in the workspace, and that the facts outrank the Implementer's claims.

**Facts** (`facts.md`) are computed by the engine from the task worktree and outrank the Implementer's claims:
- `git log --oneline base..HEAD`
- `git diff --stat base...HEAD`
- `git status --porcelain`
- which trackers changed

**Scope changes (D4).** A `Verdict` or `PlanReport` may carry `new_tasks` and `scope_changes`:
- The Leader updates trackers in the worktree and commits.
- The engine enqueues the new tasks (`origin = leader`) and emits `scope_change`, which is notified. A new task may depend on a later sibling, but the resulting dependency graph (including the implicit plan edge and repointed dependents) must be acyclic: a self-edge or cycle rejects the whole change, writing nothing, and escalates as `agent_protocol` naming the task ids on the cycle.
- The Operator undoes a change with `cancel T-n`.

**Where new tasks get their tracker entries.** The Leader commits tracker changes in its own task branch, which reaches the base only when that task integrates. Two cases, kept separate:
- **Plan.** Each new task implicitly depends on the plan task, in addition to its listed dependencies. It is ready only when the plan task is `done` (§6, so the plan's tracker changes are on the verified base ref its root starts from), and a cancelled plan task raises `dependency_cancelled` for it like any cancelled dependency.
- **Supersede.** The rejected task never integrates and its replacements must not depend on it (a cancelled dependency would cancel them). They have no implicit dependency. Their roots are seeded instead: when the engine creates a replacement's worktree it applies, three-way, the change the superseded branch made to the configured `project.trackers` since it left the base and commits it as the first commit of the replacement branch, so the tracker entries reach the base with the replacement's own integration. Nothing else of the rejected branch is carried over; when no trackers are configured, the superseded branch is gone, or its tracker paths are unchanged, nothing is seeded. A `supersede` verdict is subject to the uncommitted-changes rule of §6: a verdict on a dirty worktree is replaced by the engine's `changes_requested`, so tracker edits left uncommitted are committed (or discarded) before the task is superseded and reach the seed. A seeding failure removes the fresh worktree and branch and fails the dispatch (§10, step 5) instead of starting without the entries.

## 3. Review loop

```
loop:
  report = implement()
  while report.status != completed:
      answer = consult(report)           # escalates on escalate / CONSULT_LIMIT
      report = implement(answer)
  verdict = verify(report, facts, final_round = iteration >= max_iterations)
  match verdict.decision:
      accept            → integrate
      supersede         → enqueue new_tasks; cancel(superseded)
      escalate          → raise decision_required
      changes_requested → if final_round: raise iterations_exhausted
                          counters.rejections_on_rung += 1; counters.consults = 0
                          if ≥ roles.implementer.escalate_after: rung_index.implementer += 1; reset counter
                          iteration += 1
```

The Leader accepts only when no issues remain. Non-blocking residual risks go into `residual_risks` or `new_tasks`.

## 4. Sessions and turns

A turn is one agent process (D1, 04 §1): `Agent.start_turn(spec)` spawns it as a child of the engine, and it exits when the turn ends. Sessions outlive turns: every turn after the first resumes the role's session.

### 4.1 SessionManager

`session_for(task, role) → (rung, session_id | None, handoff)` decides what the role's next turn runs on:

| Situation | Next turn |
|---|---|
| Run with a `session_id`, same rung | Resume it |
| Rung change, same agent | Resume it with the new model and effort |
| No session yet, or a different agent | New session. If the task already has reports, the first turn is `handoff` (request, latest reports, facts) |

- **Turn environment**: Shikiru adds `SHIKIRU_PROJECT`, `SHIKIRU_TASK`, `SHIKIRU_ROLE`, `SHIKIRU_RUN_DIR` and `SHIKIRU_SLOT` to the Operator's inherited environment. Agent-native access settings and environment remain under Operator control and pass to child processes unchanged.
- The run row is created with the session; `session_id` and the resume command (`interactive_command`, shell-quoted) are stored when `SessionStarted` arrives.
- **Task end**: nothing to exit; the run is closed.

### 4.2 TurnMonitor

One monitor per turn process. It is the **only** consumer of `turn.signals()`. For each signal it:
- persists session facts on the run: `session_id`, usage (`UsageReported`), quota windows (`QuotaReported` → `quotas`), the last `Message` or `TurnEnded.last_message`;
- emits a `compacted` event for `Compacted` (pre and post tokens; display only, no decision depends on it);
- appends the signal, timestamped, to `runs/T/<role>/view.jsonl`, which the role's pane renders (06 §5.1);
- updates pane decorations (`PaneDecorator`).

Every `orchestration.poll_seconds` it checks `domain.activity.is_stalled(last_signal_at, tool_running, now, timeouts)`: no signal for `timeouts.stall` while no tool runs, or for `timeouts.tool` while one does. A stall calls `turn.stop()` and the monitor returns `Stalled`; the session is resumed by the next attempt.

The engine writes `runs/T/<role>/turn.pid` while the process runs, so a restarted engine can end an orphan (§7). The file holds the process id and its start time; a process is treated as the orphan only while both still match, so a reused pid is never signalled. Start times match within `START_TIME_TOLERANCE_SECONDS` (10 s): on Linux they derive from the boot time and shift when the clock is stepped, and no pid is reissued to a new process that quickly. The engine lock's liveness check (02 §1) compares the same way. When the leader had already exited before the file was written but its group still runs, the file holds the pid without a start time, and cleanup signals that group only while no process holds the pid.

Each turn process is started in its own process group, which the engine owns: `stop()` sends SIGTERM to the group, SIGKILLs it after `TURN_STOP_GRACE`, and always SIGKILLs it once the direct child has exited, so no descendant outlives its turn. Reading stderr after the exit is bounded by `STDERR_DRAIN_TIMEOUT`; a descendant that escaped the group and still holds the pipe cannot block `stop()`. The recorded pid is the group's id.

**Outside the guarantee.** A descendant that calls `setsid` (or `setpgid` into another group) leaves the turn's group, so neither `stop()` nor orphan cleanup can reach it. Only a cgroup (Linux) or a job object would contain such a process, and Shikiru does not use them: it supports macOS and Linux with plain POSIX process groups. This holds for notification hooks (06 §5.7) as well.

### 4.3 TurnRunner

`run(task, role, TurnSpec) → TurnResult`, where the result is one of:
- `Completed(report, rebuilt)`
- `Failed(kind, resets_at, message)`
- `Stalled`
- `ProtocolError`

1. **Turn dir** `runs/T/turns/NN-<step>-i<iteration>/`: return the previous turn's unreported messages (step 2), remove any stale `report.json`, point `runs/T/<role>/current_turn` at the new dir, then step 2's delivery, then write `prompt.md`, `report.schema.json` and `report.kind`. The pointer precedes the delivery so that a crash or a render failure after it leaves the messages in the turn `current_turn` names, where the next turn returns them.
2. **Operator messages**: move the role's queued `runs/T/messages/<role>/*.md` into the turn dir's `messages/`; `prompt.md` lists them under "Operator messages — read these first" (§9).
   Before this move, if the role's `current_turn` names a turn holding delivered messages but neither `report.json` nor `helper.json`, that turn was lost (engine killed or turn process dead) before its report was consumed: its messages return to the queue by moving each file back under its own name (a crash midway leaves the rest in that turn, returned by the next one, so none is ever lost). Message numbers only grow across the queue and every turn's delivered messages, so the replacement turn lists them ahead of newer ones. A turn with a report keeps its messages, and a repeat finds nothing to return. A failed turn returns its messages the same way at once.
3. **Start** the turn with the pointer line `Read {prompt path} and follow it exactly.` and the session from §4.1.
4. **Await** the monitor's terminal outcome: `TurnEnded`, `TurnFailed` or `Stalled`.
5. **On `TurnFailed(UNKNOWN)`**: `helper.classify_failure` (04 §6); a result with confidence ≥ `CLASSIFY_CONFIDENCE` becomes `Failed(kind)`, anything else `Failed(OTHER)` marked unclassified, which escalates `agent_error` without a retry (§7).
6. **On `TurnEnded`**:
   - a valid `report.json` → `Completed`;
   - otherwise nudge, up to `NUDGE_LIMIT` (2) times, each a short resumed turn with the line "Your turn ended without a valid report. Submit it with `shikiru report` as described at the end of {prompt path}.";
   - otherwise `helper.rebuild_report` → `Completed(rebuilt=true)`, saved as `helper.json`;
   - otherwise `ProtocolError`.

**The report instruction** ends every prompt:

> Write the report JSON matching `report.schema.json` to `report.draft.json` in this prompt's directory, never inside the workspace, and run `shikiru report --file <that file>`. If it exits non-zero, fix it and run it again. Then end your turn.

The draft stays out of the workspace so it is never committed, and the roles' drafts never overwrite each other there.

The command is spelled as `shikiru_command` (04 §1) renders it, so the agent's shell `PATH` never decides which Shikiru runs.

`shikiru report` works as follows:
- It resolves `$SHIKIRU_RUN_DIR/$SHIKIRU_ROLE/current_turn` and validates with the pydantic model named in `report.kind`, including cross-field rules.
- It writes `report.json` atomically.
- Exit codes: `0` ok, `2` invalid (errors on stderr), `3` no current turn.

**Cancellation**: `pause --now`, `cancel`, `retry` and take over (§9) call `turn.stop()` and cancel the task coroutine. Step state is persisted at step boundaries, and prompts are idempotent, so the step simply re-runs later on the same session.

## 5. Workspaces

- **Task root** `.shikiru/worktrees/T-12/` *is* the task's worktree of the project repository (D20). Agents run in it and never in the project checkout.
- **Worktree**: `git worktree add -b shikiru/T-12-<slug> <task root> <base>`. `base` is `repo.base` or the branch checked out in the project; `<base>` is its *base ref* (§6): the local branch, or, in PR mode once a PR has merged, the verified fetched remote base. The task keeps both (`repo.base`, `repo.base_ref`) and rebases, diffs and reviews against the ref. Creation is idempotent.
- **Caches** are copied copy-on-write (`repo.caches` globs that match ignored paths): macOS `cp -c -R`, Linux `cp -a --reflink=always`. If CoW isn't supported, nothing is copied and one warning is logged.
  - **Copy-on-write**: the filesystem (APFS; btrfs/XFS) creates only metadata that points at the same data blocks.
  - A 5 GB `node_modules` or `target` "copies" in about a second and uses no extra disk; only files that one side later modifies get their own blocks.
  - Every task gets a warm, fully independent cache.
- **Shared resources**: `SHIKIRU_SLOT` and `SHIKIRU_TASK` let project scripts separate ports and Docker Compose project names (slots are `0..max_parallel-1`, so `PORT=$((3000 + SHIKIRU_SLOT))` gives 3000 and 3001 for two slots).
- **Git hygiene**: `.shikiru/` is added to the repository's `.git/info/exclude`.
- **Cleanup** on a terminal status: `git worktree remove --force`, and the branch is deleted once merged. The task root goes last, with or without a repo; symlinks in it are removed, never followed.

## 6. Integration

Integration runs under the project integration lock. A branch with no commits ahead of `base` merges nothing and finishes cleanly.

**Uncommitted changes.** A verify `accept` or `supersede` on a worktree with uncommitted changes (`git status --porcelain` not empty) never reaches the rebase: the engine replaces it with its own `changes_requested` verdict whose issue lists the files ("Commit what belongs to this task and discard the rest."), stored as `engine-verdict.json` beside the verify turn's report. It is a normal `changes_requested` round (§3), final-round rules included. The same replacement applies to the post-rebase `accept` (a conflict round, step 1). Files that git ignores are outside the guard (`git status --porcelain` does not list them): ignored output is disposable and is removed with the worktree.

Terminal cleanup never erases uncommitted work either: every path that removes a task worktree (`done`, `superseded`, `cancel`, an escalation's `cancel`, startup cleanup) keeps a worktree whose `git status --porcelain` is not empty, together with its branch, and reports its path (the `kept_worktree` field of the command result). The Operator commits or discards the work and removes the worktree.

Integration itself never discards work: at its entry, and again before the strategy merges, a task worktree whose `git status --porcelain` is not empty (tracked or untracked, plan tasks and a branch equal to `base` included) raises `uncommitted_work` (§8) and the worktree is kept. A rebase already in progress is exempt, its conflict markers being the work in flight.

1. **Rebase** the task branch onto `base`.
   - On conflict, leave the rebase in progress so its conflict markers remain in the worktree. The Implementer `conflicts` turn resolves and stages them, then the engine continues the rebase before the Leader `verify` turn with post-rebase facts.
   - A conflicts report that isn't `completed` goes to consult (§2). A post-rebase `changes_requested` counts as a conflict round and re-runs the `conflicts` turn with the issues; `escalate` → `decision_required`.
   - At most `CONFLICT_ROUNDS` (2) rounds, then `integration_conflict`.
2. **Strategy**:
   - **`LocalMerge`** (`integration = "merge"`):
     - Pre-check: `merge-base --is-ancestor base branch`, and, if `base` is checked out in the main checkout, no overlap between its dirty paths and the changed paths.
     - If a check fails: retry every `INTEGRATION_RETRY` (2 min) for up to `timeouts.integration_wait`, then `integration_blocked`.
     - The wait is never silent: when it begins because the checkout is dirty on paths the merge touches, one `integration_waiting` event (task id, `paths` = the blocking dirty paths) is recorded and notified (06 §5.7). It is recorded once per merge attempt, not per retry; a base that moved past the branch does not record it. An agent that wrote into the operator's checkout instead of its worktree shows up this way: Shikiru cannot attribute checkout edits to an agent (the operator edits it too), so it has no separate detector.
     - Then: `git merge --ff-only` if `base` is checked out, else `git update-ref refs/heads/<base> <new> <old>`.
     - Every mutating git call of the engine (merge, rebase, apply, commit, worktree add/remove, branch delete, update-ref, fetch, push) is stopped gracefully on cancellation: SIGTERM, which git handles by removing its own lock files, then SIGKILL after `GIT_STOP_GRACE`. A killed git would leave `index.lock` or a half-updated tree in the operator's checkout, which a later run would see as a dirty overlap.
   - **`PullRequest`** (`integration = "pr"`):
     - `git push -u <repo.remote> <branch>` and `gh pr create --base <base> --head <branch>`, titled with the task title and with the accepted summary as its body. `pr_opened` records the PR URL and commits.
     - The task branch is Shikiru's; a re-push after a rebase uses `--force-with-lease` against the last pushed sha. An open PR already on that head and base (the engine died before `pr_opened`) is adopted instead of opening another.
     - Polling runs outside the integration lock: the slot is released, `wait_reason = "PR open"`, and `gh pr view <url> --json state,mergeCommit,mergeable,statusCheckRollup` is polled every `PR_POLL` (5 min).
     - **Liveness.** An open PR does not poll forever. A poll that finds it `CONFLICTING` with its base, with a failing check (`FAILURE`, `ERROR`, `TIMED_OUT`, `CANCELLED`, `STARTUP_FAILURE` or `ACTION_REQUIRED`), or still unmerged `timeouts.pr_wait` seconds after the wait began (a resume or engine restart begins it anew; held time never escalates, as the hold is checked before every poll) raises `integration_blocked` (§8) once, with the PR URL and the state in the packet's question. The PR stays open; `retry` polls it again under a fresh budget, so the Operator fixes the conflict or checks on GitHub first. Required versus optional checks are not distinguished: any failing check escalates.
     - `MERGED` → the merged base is fetched and verified (below), then done. `CLOSED` → `pr_closed`, whose `retry` pushes again and opens a new PR.
     - **Merged base.** A merged PR lands on the remote base, never on the local branch, and the operator's checkout, index and local changes are never reset, pulled or merged. On `MERGED` the engine reads the PR's `mergeCommit` (`gh pr view <url> --json state,mergeCommit`), fetches `+refs/heads/<base>:refs/shikiru/bases/<base>` from `repo.remote`, and verifies that the merge commit is the fetched ref or its ancestor (`merge-base --is-ancestor`). That holds for merge, squash and rebase merges alike, since the PR head itself is not an ancestor after a squash or rebase. Only then is the task `done`, so its dependents are not ready before the verified base exists. A failed fetch or a merge commit missing from the fetched base raises `integration_blocked` (§8); its `retry` fetches and verifies again without touching the PR.
     - **Merged branch.** A `done` task's local branch is removed with its worktree whichever way the PR merged (a squash or rebase merge leaves it out of the local base's history), unless the worktree is kept for uncommitted work, in which case the branch stays with it. A branch of a task that is not `done` is deleted only when it is merged into the local base.
     - **Base ref.** The ref new task roots start from is one injected rule. `merge` mode: the local base branch. `pr` mode: `refs/shikiru/bases/<base>`, fetched from `repo.remote` (`+refs/heads/<base>:refs/shikiru/bases/<base>`) every time a root is built, so a task starts from the remote base as it is then, with other people's pushes and earlier Shikiru merges alike; local commits that were never pushed are not part of it. The ref is Shikiru's own, so operator fetches and local commits never move it. The fetch times out after `orchestration.network.fetch_timeout_seconds` (30 s). A failed fetch (no remote, base missing there, network) is a recoverable `BaseFetchFailed`: the root is not built, nothing falls back to a stale ref, and the dispatch is retried on a backoff without counting toward `MAX_DISPATCH_FAILURES` (§10, step 5). The merged-base check above still applies to a dependent: the fetched base must contain the merged commit.
     - **Cancel.** Cancelling a task never waits for the network. Terminal cleanup of a `cancelled` task queues its open PR on `PullRequestClosing`, a supervised task of the engine (started and reaped like `NotificationDelivery`, no threads), and goes on: the cancel is acknowledged at once and the worktree is removed without waiting for `gh`. That task closes the PR (`gh pr close`; a PR that is no longer open is left alone) and records `pr_withdrawn` (url, state), so a cancelled task's PR cannot be merged by hand. A failed `gh` call is recorded through the `FaultReporter` (scope `pull request close`, task id) and retried on the `orchestration.faults` backoff, `orchestration.network.pr_close_attempts` times; after that the close waits for the next engine start. The pending close is derived from durable state, never stored apart: it is the cancelled task's open PR (below), and `Reconciler` queues every such task at engine start (§7), whether or not its root remains.
     - The open PR is the task's latest `pr_opened` not followed by a `pr_closed` escalation or a `pr_withdrawn` event. A resumed integration (§7) polls it instead of rebasing, pushing or opening another.
3. **Done**: the `done` status and `task_done` (summary, commits, residual risks) land in one transaction; then the window closes and the worktree is removed, last (§7).

**Holds.** A `paused` or `intervention` hold (§9) is one rule, `Task.held`, checked wherever the next external effect would start:
- before each step, before integration starts, before each integration turn, and by the `Reconciler` before it resumes a task (§7);
- in the `TurnRunner`, immediately before every agent process is spawned, nudges (§4.3) and the turns of a quota, backoff or stall recovery included; this also closes the window between the step's hold check and the spawn, and a turn that ends without a valid report is not nudged while held;
- after the wait for the integration lock and before `INTEGRATION_STARTED` is emitted, before the rebase and the merge, and before `git rebase --continue` after a conflict turn;
- in the `LocalMerge` wait for base access: when a retry finds the hold set the wait ends quietly as held, so a held task never escalates as `integration_blocked` from the wait budget;
- before every poll of an open pull request;
- by the scheduler: a queued task with a hold is not dispatched and takes no slot (§10).

A Git or `gh` operation that is already running finishes; no further step, turn, rebase, push, merge or poll starts while a hold is set. The push and pull request creation of one merge are a single step behind the merge check, so an observed open pull request may appear after a hold was set. The task keeps its status, worktree and slot, and `resume` re-enters integration from its persisted state; a polling task resumes polling the same pull request.

Dependents become ready only when their dependencies are `done`.

## 7. Recovery

The `TurnLoop` passes every non-`Completed` `TurnResult` to the pure `recovery.decide(result, counters, policy) → Action`, then applies the action:

| Result | Action |
|---|---|
| `Failed(QUOTA)` | Block the agent in `agent_blocks` until `resets_at + QUOTA_MARGIN` (unknown reset: now + `QUOTA_PROBE`). When the block ends, a resumed `continue` turn re-runs the step's prompt; a new failure repeats the cycle. If the wait exceeds `timeouts.max_quota_wait`: next rung with a different agent; none left → `quota_exhausted` |
| `Failed(UNAVAILABLE)` | Retry after 30 s, 2 min, 5 min; then next rung; none left → `ladder_exhausted` |
| `Failed(AUTH)` | Escalate immediately as `agent_auth` for rejected credentials or provider access, including payment/credit exhaustion that needs Operator action |
| `Failed(OTHER)` | Retry once, then `agent_error`; unclassified (§4.3 step 5) → `agent_error` at once. A context failure the plugin could not recover (04 §1, obligation 8) arrives here |
| `Stalled` | The process was stopped; a resumed `continue` turn (≤ 2 per turn), then `agent_stuck` |
| `ProtocolError` | `agent_protocol` |

**Rungs**:
- The resolved rung is `overrides[role]` if set, otherwise the ladder at `rung_index[role]`.
- A transition never writes the `overrides` of the snapshot it was decided on. The store merges it into the stored row in the same write: it sets the roles it names (`set_rung`, `retry --rung`) and clears a role (an automatic `move_up`) only while the stored override is still the one the decision saw. An operator `rung` acknowledged while a turn runs therefore survives that turn, whether it completes, is rejected at `escalate_after` or recovers to another rung, and applies to the next.
- The Leader uses `roles.leader.ladder`. The Implementer uses `roles.implementer.complexity.<c>.ladder`, falling back to `roles.implementer.ladder`.
- Each rung change emits `rung_changed`.

**Engine restart (`Reconciler`)**:
1. Rebuild the tmux session and windows from the DB (`LayoutGuard`).
2. End orphaned turn processes: a `runs/T/<role>/turn.pid` whose process still runs has its whole process group stopped (SIGTERM, then SIGKILL after `TURN_STOP_GRACE`), since the old engine died without stopping it. A recorded pid now held by a process with a different start time is never signalled. The group is stopped even when its leader already exited, because a live group id is never reused as a pid.
3. For each `running` or `integrating` task without a hold, re-run the current step (a held task waits for `resume`, §6); its turn resumes the session (§4.1). The step's turn is idempotent: an existing valid `report.json` is consumed without starting a turn. That holds only for the first turn of the resumed flow, and only when that step's turn dir is still the task's latest and the role's `current_turn`; any other report there was already consumed.
4. Finish interrupted cleanup: a terminal task is entered together with its terminal event (`task_done`, `task_cancelled`) in one transaction and cleaned up afterwards, worktree last. A `done` or `cancelled` task whose task root still exists is cleaned up again (sessions closed, window closed, worktree removed); its event is never re-emitted. A cancelled task whose PR is still open is queued for closing again (§6, cancel), root or not.
5. Every `escalated` task has an open escalation whose packet file exists (§8): a missing packet is rewritten at its recorded path from durable state (timeline, sessions, the escalation row's reason, options and headline, plus the reports, facts and turn tail that still exist); the check runs at start and on every tick, so a packet that could not be written is rewritten once the cause is gone; a task with no open escalation gets a new `engine_error` one asking to retry or cancel. An existing open escalation is never duplicated.
6. Pending `commands` are processed by the normal loop.

Every step and every task of reconciliation is isolated: a fault (a failed worktree removal of a cancelled task's cleanup, say) is logged and recorded as an `engine_fault` with the task id, and the other steps and tasks carry on, so scheduling, the live flow invariant and escalation restoration never wait for a failing cleanup. Orphaned turn processes are ended once per engine start, so no retry can end a turn the engine started itself; a failed cleanup is repeated by the next engine start.

**Live flow invariant.** Every `running` or `integrating` task without a hold has exactly one live `TaskFlow` coroutine. `start_task` never starts a second one: while the task's previous flow is still finishing (an `answer` resolution, which does not cancel the flow, can land during its closing title refresh) it leaves the start to the next tick. Each engine tick (§10) ends by ensuring the invariant: it starts a resumed flow (step 3) for every such task that has none, whether its flow ended by an exception that escaped `TaskFlow.run` or by the race above. A flow that fails inside `TaskFlow.run` escalates the task as `engine_error` (§8) and so leaves the invariant's scope.

**Command replay.** Commands are delivered at least once: a command stays `pending` until its handler returns, so an engine that dies between a durable effect and the acknowledgement replays it on restart. Handlers are idempotent by command id, derived from durable state and never from a replay log:
- `submit`: the queue allocated the task id when appending, so a task with that id already existing is the command's own effect; the replay returns its id. The task insert and `task_queued` commit in one transaction.
- `say`: the message file name carries the command id (`NNN-c<id>.md`, kept when a failed turn requeues its messages). A message already filed under the id, queued or delivered, is not written again, and `operator_message` (data `command_id`) is appended once.
- `hand_back`: the run, hold and `handed_back` (data `command_id`) changes commit in one transaction, after the view pane is restarted and the message is filed, both of which are repeatable. A `handed_back` event for the command id means the hand-back took effect: the replay acknowledges it and only repeats the follow-up (resume the step if it is active and not held, refresh titles).
- `take_over`: the run mark, the `intervention` hold and `taken_over` (data `command_id`) commit in one transaction, after the running turn is stopped; the pane respawn follows and is repeatable. A `taken_over` event for the command id means the take over took effect: the replay acknowledges it and only respawns the pane, while the role is still taken over, then refreshes titles. A second `take_over` by a different command is rejected as before.
- `cancel`: the terminal transition with `task_cancelled` (data `command_id`) and the escalation settlement commit together. A `task_cancelled` event for the command id means it took effect: the replay acknowledges `cancelled` and repeats the terminal cleanup, which is idempotent. A cleanup failure after the commit is logged and recorded as a recoverable `engine_fault`, and the command is still acknowledged; reconcile repeats the cleanup of every terminal task whose root remains (§7 restart).
- `resolve`: `escalation_resolved` carries the `command_id`; the replay acknowledges the resolution with the task's current status, repeats the cleanup of a `cancel` resolution the same tolerant way as `cancel`, and appends nothing.
- `retry`: the requeue, the escalation settlement and `task_retried` (data `command_id`) commit together; a replay that finds the event acknowledges without interrupting or requeueing the task again, so a turn the retry already started keeps running.
- `set_rung` and `pause` set state to a target value: a role already on the rung, or a task already paused, is success with no further change and no event. Each change and its event commit in one transaction.
Commands that only set a value (`prioritize`) are naturally repeatable.

## 8. Escalation

| Reason | Raised when | Options (★ = recommended default) |
|---|---|---|
| `decision_required` | The Leader escalates (product decision, conflicting requirements, missing access), or the consult limit is exceeded | answer ★, cancel |
| `iterations_exhausted` | Final round still `changes_requested` | answer ★ (one more final round with guidance), accept, cancel |
| `integration_conflict` | Conflict rounds exhausted | retry ★, cancel |
| `integration_blocked` | Main checkout blocked beyond `integration_wait`, a merged PR's commit could not be fetched and verified on the remote base, or an open PR conflicts, has a failing check or stays unmerged beyond `pr_wait` (§6) | retry ★, cancel |
| `uncommitted_work` | The task worktree has uncommitted changes when integration starts or is about to merge | retry ★ (after committing or discarding them, e.g. through takeover), cancel |
| `pr_closed` | PR closed unmerged | retry ★ (new PR), cancel |
| `agent_auth` / `agent_error` / `agent_stuck` / `agent_protocol` | §7 | retry [--rung] ★, cancel |
| `ladder_exhausted` / `quota_exhausted` | §7 | retry [--rung] ★, cancel |
| `dependency_cancelled` | A dependency was cancelled | retry ★ (drop the dependency), cancel |
| `engine_error` | An unexpected engine error (e.g. a failing `git` command) in the task's flow; the packet carries the error, and the engine and other tasks keep running | retry ★, cancel |

**Packet** (`runs/T/escalations/E-n.md`, path stored in the DB):
- headline (task, step, reason, one sentence)
- timeline of recent events
- what was tried
- evidence: latest reports and verdict, facts, the last 60 view lines per role, session ids and resume commands
- the Leader's question
- applicable options with consequences and exactly one ★

**Resolution**: one of `answer TEXT`, `retry [rung]`, `accept`, `cancel`, through the palette (06 §5.5) or `resolve_escalation` (06 §4).
- `answer` runs a consult turn (the Leader receives the Operator's text), then continues with implement at the same iteration; for `iterations_exhausted` the next verify is again a final round.
- `retry` re-runs the failed step.
- `accept` integrates.
- `cancel` cancels the task and keeps the branch; a worktree with uncommitted work (an `uncommitted_work` escalation, typically) is kept too and its path is returned in the result (§6).

**Atomicity.** Every path that escalates a task (a step outcome, an integration turn, a dispatch failure, a cancelled dependency) goes through the one `EscalationRaiser`. The packet file is written first, atomically (temporary file, then rename); then the escalated task state, the escalation row and the `escalation` event commit in one transaction, and the packet is removed again if that transaction fails. A task is therefore never `escalated` without an open escalation; a failure before the commit leaves the task in its previous state for the ordinary error handling. A packet that cannot be written (unwritable directory, full disk) is not such a failure: the escalation is the row, and the file is a rendering of it. The state, row and event still commit, so the task is `escalated` and actionable at once (invariant 5); the write failure is logged and recorded once per packet and engine run as an `engine_fault` event of the task (notified, 06 §5.7), and the packet is rewritten by the per-tick restoration (§7 step 5) when the directory is writable again. Only a failure of the transaction itself (state, row or event) rolls back and removes the packet. Title refresh runs after the commit and its failure is logged, never raised.

Resolved tasks are re-queued with `requeued_at`. Raising an escalation frees the slot and notifies.

An escalation acts only on the task state it was raised for. `resolve` is rejected unless the task is still `escalated`, and a resolved escalation cannot be resolved again. A direct `cancel` or `retry` settles the task's open escalations in the same transaction as its transition, so a stale escalation can never resurrect or redirect the task. A resolution's status change, escalation row and event are one transaction; a failed tmux title refresh afterwards is logged and never turns the acknowledgement into a rejection.

## 9. Operator access and intervention

Every action is scoped to **one task**. Other tasks, including parallel ones on the same agent, are unaffected, because each task runs in its own coroutine. The only cross-task effect is an agent quota block (§7), because quota belongs to the account.

| Action | Human | Tool / AI (MCP) | Engine behaviour |
|---|---|---|---|
| Watch | attach, select the window (role panes are live views) | `peek_session` | Last message, recent view lines, usage (from the DB and `view.jsonl`) |
| Message an agent | palette | `send_message` | Queued for the role's next turn (below) |
| Take over / hand back | palette; hand back also by exiting the agent's TUI | `take_over`, `hand_back` | Below |
| Pause / resume | palette | `pause_task` (`now`), `resume_task` | `pause` stops before the next turn or integration effect (§6); `now` stops the running turn and cancels the current step |
| Change model | palette | `set_rung` | Applied at the next turn (§4.1) |
| Redo current step | palette | `retry_task` | Cancels and re-runs the current step |
| Open elsewhere | `status T-n` lists the resume command | `get_task` | — |

**Take over** (`TakeOver(task, role)`, by `Takeover`):
1. If a turn is running on the task, `turn.stop()` and cancel the current step; the session persists.
2. Set the `intervention` hold on **that task**, mark the run `taken_over` and append `taken_over`, in one transaction (§7 replay).
3. Respawn the role's pane in the task root with the plugin's `interactive_command(session_id, run dir)`, with the turn environment of §4.1. The Operator answers any dialog the TUI shows. With no session yet, take over is rejected.
- **While held**: no turns start for the task. The pane border turns operator magenta and the title shows `⌨ operator`.

**Hand back** (`HandBack(task, role)`, or the taken-over pane's process exits, which `LayoutGuard` turns into a `HandBack`):
1. Respawn the pane with the view, mark the run active, and clear `intervention` only when no other role of the task remains taken over.
2. Queue an operator message for that role: "The Operator worked in this session since your last turn; take their changes into account."
3. Re-run the current step unless a hold remains (another taken-over role, or `pause`); its turn resumes the same session, which already holds the Operator's exchange (verified for Claude).

**`resume` while a role is taken over is rejected** with a message to hand the role back first. There is no implicit hand-back: an interactive pane owns the workspace until every taken-over role is handed back, so no headless turn can run beside the Operator's session (invariant 1).

**`say`** (`Say(task, role, text)`) writes `runs/T/messages/<role>/NNN-c<id>.md` (`<id>` is the command id; a replay files it once, §7). A headless turn takes no input mid-turn, so the message is delivered at the start of that role's next turn (§4.3 step 2), never interrupting; a message delivered into a turn that dies without a report is delivered again with the replacement turn (§4.3 step 2); interrupting is what take over is for. `say` never sets a hold.

## 10. Scheduling

The `Scheduler` runs on every state change and every `orchestration.poll_seconds`.

1. **Ready**: `queued`, not held (no `paused` or `intervention` hold, §9; a held task's `wait_reason` is `paused`), all dependencies `done`. A cancelled dependency raises `dependency_cancelled`. Scope changes never leave a cycle, so a dependency wait always ends (§2). Dependencies include the implicit one of a plan task's new tasks on it (§2).
2. **Order**: `requeued_at` set first, then `priority` descending, then `seq`.
3. **Dispatch** in order until a gate fails; the task stops there and its reason is written to `wait_reason`. Gates:
   - **slot**: a free slot exists;
   - **resources**: `ResourceSampler` average over configured `resources.cpu_window_seconds` `< resources.max_cpu_pct` and available RAM `≥ resources.min_free_ram_gb`;
   - **quota**: neither the Leader's nor the Implementer's resolved agent is blocked.
4. **On dispatch**: assign the task's previous slot if free, else the lowest free slot, create the task window (`LayoutGuard`) and start the `TaskFlow` coroutine.
   Every tick then ensures each unheld `running` or `integrating` task has a live flow and starts a resumed one where it is missing (§7, live flow invariant); a dispatch that finds the task's previous flow still finishing is completed by that ensure on the next tick.
5. **Dispatch failure**: if preparing the task (slot, task root, window) raises, the dispatch is rolled back, the task stays `queued` and `dispatch_failed` is logged; after `MAX_DISPATCH_FAILURES` consecutive failures it escalates with `engine_error`.
   A failed base fetch of a `pr`-mode root build (§6, base ref) is not such a failure: it is a transient remote condition. The dispatch is rolled back, the task stays `queued` and is not tried again before a backoff passes (`orchestration.faults`: doubling from `backoff_initial_seconds` to `backoff_max_seconds`, per task), the fault is recorded once per `repeat_notify_seconds` as an `engine_fault` (scope `dispatch fetch`, task id) and an `engine_recovered` follows the first successful dispatch. It never counts toward `MAX_DISPATCH_FAILURES` and never escalates, however long the outage lasts; no `dispatch_failed` is logged for it. The fetch is bounded by `orchestration.network.fetch_timeout_seconds` (30 s), so a hung remote holds the tick for that long at most. Any other failure of the preparation counts as before.
