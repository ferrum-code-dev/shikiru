# 03 · Workflow

## 1. Task model

### 1.1 Fields and state

| Field | Meaning |
|---|---|
| `id` / `seq` | `T-<seq>` |
| `parent_id`, `origin` | `origin` is `operator` or `leader` |
| `title`, `request` | `request` is the verbatim text |
| `mode` | `implement` or `plan`. `NULL` until triage, unless `submit --mode` sets it |
| `complexity` | `low`, `normal` or `high` (default `normal`) |
| `priority` | Integer; higher runs first |
| `depends_on` | Task ids |
| `status` | §1.2 |
| `step` | `triage`, `plan`, `implement`, `consult`, `verify` or `integrate` |
| `status_reason` | e.g. `superseded`, `cancelled by operator` |
| `holds` | Task-scoped: `paused`, `intervention` |
| `iteration` | Implement → verify rounds so far |
| `counters` | Persisted: `rejections_on_rung` (reset on rung change), `consults` (reset each iteration), `conflict_rounds` (reset per integration attempt). Resolving an escalation resets the counter behind its reason |
| `rung_index` | Per role, persisted. Never decreases |
| `overrides` | Operator rungs per role |
| `repo` | `{base, base_sha, branch}`, set when the task root is built |
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
| `cancelled` | — terminal: window closed, worktrees removed, run files kept |

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
- The engine enqueues the new tasks (`origin = leader`) and emits `scope_change`, which is notified.
- The Operator undoes a change with `cancel T-n`.

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

The engine writes `runs/T/<role>/turn.pid` while the process runs, so a restarted engine can end an orphan (§7). The file holds the process id and its start time; a process is treated as the orphan only while both still match, so a reused pid is never signalled.

### 4.3 TurnRunner

`run(task, role, TurnSpec) → TurnResult`, where the result is one of:
- `Completed(report, rebuilt)`
- `Failed(kind, resets_at, message)`
- `Stalled`
- `ProtocolError`

1. **Turn dir** `runs/T/turns/NN-<step>-i<iteration>/`: write `prompt.md`, `report.schema.json` and `report.kind`, remove any stale `report.json`, and point `runs/T/<role>/current_turn` at it.
2. **Operator messages**: move the role's queued `runs/T/messages/<role>/*.md` into the turn dir's `messages/`; `prompt.md` lists them under "Operator messages — read these first" (§9).
3. **Start** the turn with the pointer line `Read {prompt path} and follow it exactly.` and the session from §4.1.
4. **Await** the monitor's terminal outcome: `TurnEnded`, `TurnFailed` or `Stalled`.
5. **On `TurnFailed(UNKNOWN)`**: `helper.classify_failure` (04 §6); a result with confidence ≥ `CLASSIFY_CONFIDENCE` becomes `Failed(kind)`, anything else `Failed(OTHER)` marked unclassified, which escalates `agent_error` without a retry (§7).
6. **On `TurnEnded`**:
   - a valid `report.json` → `Completed`;
   - otherwise nudge, up to `NUDGE_LIMIT` (2) times, each a short resumed turn with the line "Your turn ended without a valid report. Submit it with `shikiru report` as described at the end of {prompt path}.";
   - otherwise `helper.rebuild_report` → `Completed(rebuilt=true)`, saved as `helper.json`;
   - otherwise `ProtocolError`.

**The report instruction** ends every prompt:

> Write the report JSON matching `report.schema.json` and run `shikiru report --file <file>`. If it exits non-zero, fix it and run it again. Then end your turn.

The command is spelled as `shikiru_command` (04 §1) renders it, so the agent's shell `PATH` never decides which Shikiru runs.

`shikiru report` works as follows:
- It resolves `$SHIKIRU_RUN_DIR/$SHIKIRU_ROLE/current_turn` and validates with the pydantic model named in `report.kind`, including cross-field rules.
- It writes `report.json` atomically.
- Exit codes: `0` ok, `2` invalid (errors on stderr), `3` no current turn.

**Cancellation**: `pause --now`, `cancel`, `retry` and take over (§9) call `turn.stop()` and cancel the task coroutine. Step state is persisted at step boundaries, and prompts are idempotent, so the step simply re-runs later on the same session.

## 5. Workspaces

- **Task root** `.shikiru/worktrees/T-12/` *is* the task's worktree of the project repository (D20). Agents run in it and never in the project checkout.
- **Worktree**: `git worktree add -b shikiru/T-12-<slug> <task root> <base>`. `base` is `repo.base` or the branch checked out in the project. Creation is idempotent.
- **Caches** are copied copy-on-write (`repo.caches` globs that match ignored paths): macOS `cp -c -R`, Linux `cp -a --reflink=always`. If CoW isn't supported, nothing is copied and one warning is logged.
  - **Copy-on-write**: the filesystem (APFS; btrfs/XFS) creates only metadata that points at the same data blocks.
  - A 5 GB `node_modules` or `target` "copies" in about a second and uses no extra disk; only files that one side later modifies get their own blocks.
  - Every task gets a warm, fully independent cache.
- **Shared resources**: `SHIKIRU_SLOT` and `SHIKIRU_TASK` let project scripts separate ports and Docker Compose project names.
- **Git hygiene**: `.shikiru/` is added to the repository's `.git/info/exclude`.
- **Cleanup** on a terminal status: `git worktree remove --force`, and the branch is deleted once merged. The task root goes last, with or without a repo; symlinks in it are removed, never followed.

## 6. Integration

Integration runs under the project integration lock. A branch with no commits ahead of `base` merges nothing and finishes cleanly.

**Uncommitted changes.** A verify `accept` on a worktree with uncommitted changes (`git status --porcelain` not empty) never reaches the rebase: the engine replaces it with its own `changes_requested` verdict whose issue lists the files ("Commit what belongs to this task and discard the rest."), stored as `engine-verdict.json` beside the verify turn's report. It is a normal `changes_requested` round (§3), final-round rules included.

1. **Rebase** the task branch onto `base`.
   - On conflict, leave the rebase in progress so its conflict markers remain in the worktree. The Implementer `conflicts` turn resolves and stages them, then the engine continues the rebase before the Leader `verify` turn with post-rebase facts.
   - A conflicts report that isn't `completed` goes to consult (§2). A post-rebase `changes_requested` counts as a conflict round and re-runs the `conflicts` turn with the issues; `escalate` → `decision_required`.
   - At most `CONFLICT_ROUNDS` (2) rounds, then `integration_conflict`.
2. **Strategy**:
   - **`LocalMerge`** (`integration = "merge"`):
     - Pre-check: `merge-base --is-ancestor base branch`, and, if `base` is checked out in the main checkout, no overlap between its dirty paths and the changed paths.
     - If a check fails: retry every `INTEGRATION_RETRY` (2 min) for up to `timeouts.integration_wait`, then `integration_blocked`.
     - Then: `git merge --ff-only` if `base` is checked out, else `git update-ref refs/heads/<base> <new> <old>`.
   - **`PullRequest`** (`integration = "pr"`):
     - `git push -u <repo.remote> <branch>` and `gh pr create --base <base> --head <branch>`, titled with the task title and with the accepted summary as its body. `pr_opened` records the PR URL and commits.
     - The task branch is Shikiru's; a re-push after a rebase uses `--force-with-lease` against the last pushed sha. An open PR already on that head and base (the engine died before `pr_opened`) is adopted instead of opening another.
     - Polling runs outside the integration lock: the slot is released, `wait_reason = "PR open"`, and `gh pr view <url> --json state` is polled every `PR_POLL` (5 min).
     - `MERGED` → done. `CLOSED` → `pr_closed`, whose `retry` pushes again and opens a new PR.
     - The open PR is the task's latest `pr_opened` not followed by a `pr_closed` escalation. A resumed integration (§7) polls it instead of rebasing, pushing or opening another.
3. **Done**: the `done` status and `task_done` (summary, commits, residual risks) land in one transaction; then the window closes and the worktree is removed, last (§7).

Dependents become ready only when their dependencies are `done`.

## 7. Recovery

`TaskFlow` passes every non-`Completed` `TurnResult` to the pure `recovery.decide(result, counters, policy) → Action`, then applies the action:

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
- The Leader uses `roles.leader.ladder`. The Implementer uses `roles.implementer.complexity.<c>.ladder`, falling back to `roles.implementer.ladder`.
- Each rung change emits `rung_changed`.

**Engine restart (`Reconciler`)**:
1. Rebuild the tmux session and windows from the DB (`LayoutGuard`).
2. End orphaned turn processes: a `runs/T/<role>/turn.pid` whose process still runs is stopped (SIGTERM, then SIGKILL after `TURN_STOP_GRACE`), since the old engine died without stopping it.
3. For each `running` or `integrating` task, re-run the current step; its turn resumes the session (§4.1). The step's turn is idempotent: an existing valid `report.json` is consumed without starting a turn. That holds only for the first turn of the resumed flow, and only when that step's turn dir is still the task's latest and the role's `current_turn`; any other report there was already consumed.
4. Finish interrupted cleanup: a terminal task is entered together with its terminal event (`task_done`, `task_cancelled`) in one transaction and cleaned up afterwards, worktree last. A `done` or `cancelled` task whose task root still exists is cleaned up again (sessions closed, window closed, worktree removed); its event is never re-emitted.
5. Pending `commands` are processed by the normal loop.

## 8. Escalation

| Reason | Raised when | Options (★ = recommended default) |
|---|---|---|
| `decision_required` | The Leader escalates (product decision, conflicting requirements, missing access), or the consult limit is exceeded | answer ★, cancel |
| `iterations_exhausted` | Final round still `changes_requested` | answer ★ (one more final round with guidance), accept, cancel |
| `integration_conflict` | Conflict rounds exhausted | retry ★, cancel |
| `integration_blocked` | Main checkout blocked beyond `integration_wait` | retry ★, cancel |
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
- `cancel` cancels the task and keeps the branch.

Resolved tasks are re-queued with `requeued_at`. Raising an escalation frees the slot and notifies.

## 9. Operator access and intervention

Every action is scoped to **one task**. Other tasks, including parallel ones on the same agent, are unaffected, because each task runs in its own coroutine. The only cross-task effect is an agent quota block (§7), because quota belongs to the account.

| Action | Human | Tool / AI (MCP) | Engine behaviour |
|---|---|---|---|
| Watch | attach, select the window (role panes are live views) | `peek_session` | Last message, recent view lines, usage (from the DB and `view.jsonl`) |
| Message an agent | palette | `send_message` | Queued for the role's next turn (below) |
| Take over / hand back | palette; hand back also by exiting the agent's TUI | `take_over`, `hand_back` | Below |
| Pause / resume | palette | `pause_task` (`now`), `resume_task` | `pause` stops before the next turn; `now` stops the running turn and cancels the current step |
| Change model | palette | `set_rung` | Applied at the next turn (§4.1) |
| Redo current step | palette | `retry_task` | Cancels and re-runs the current step |
| Open elsewhere | `status T-n` lists the resume command | `get_task` | — |

**Take over** (`TakeOver(task, role)`, by `Takeover`):
1. If a turn is running on the task, `turn.stop()` and cancel the current step; the session persists.
2. Set the `intervention` hold on **that task** and mark the run `taken_over`.
3. Respawn the role's pane in the task root with the plugin's `interactive_command(session_id, run dir)`, with the turn environment of §4.1. The Operator answers any dialog the TUI shows. With no session yet, take over is rejected.
- **While held**: no turns start for the task. The pane border turns operator magenta and the title shows `⌨ operator`.

**Hand back** (`HandBack(task, role)`, or the taken-over pane's process exits, which `LayoutGuard` turns into a `HandBack`):
1. Respawn the pane with the view, mark the run active, clear `intervention`.
2. Queue an operator message for that role: "The Operator worked in this session since your last turn; take their changes into account."
3. Re-run the current step; its turn resumes the same session, which already holds the Operator's exchange (verified for Claude).

**`say`** (`Say(task, role, text)`) writes `runs/T/messages/<role>/NNN.md`. A headless turn takes no input mid-turn, so the message is delivered at the start of that role's next turn (§4.3 step 2), never interrupting; interrupting is what take over is for. `say` never sets a hold.

## 10. Scheduling

The `Scheduler` runs on every state change and every `orchestration.poll_seconds`.

1. **Ready**: `queued`, not `paused`, all dependencies `done`. A cancelled dependency raises `dependency_cancelled`.
2. **Order**: `requeued_at` set first, then `priority` descending, then `seq`.
3. **Dispatch** in order until a gate fails; the task stops there and its reason is written to `wait_reason`. Gates:
   - **slot**: a free slot exists;
   - **resources**: `ResourceSampler` `CPU_WINDOW` average `< resources.max_cpu_pct` and available RAM `≥ resources.min_free_ram_gb`;
   - **quota**: neither the Leader's nor the Implementer's resolved agent is blocked.
4. **On dispatch**: assign the task's previous slot if free, else the lowest free slot, create the task window (`LayoutGuard`) and start the `TaskFlow` coroutine.
5. **Dispatch failure**: if preparing the task (slot, task root, window) raises, the dispatch is rolled back, the task stays `queued` and `dispatch_failed` is logged; after `MAX_DISPATCH_FAILURES` consecutive failures it escalates with `engine_error`.
