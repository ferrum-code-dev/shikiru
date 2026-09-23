# 03 · Workflow

## 1. Task model

### 1.1 Fields and state

| Field | Meaning |
|---|---|
| `id` / `seq` | `T-<seq>` |
| `parent_id`, `origin` | `origin` is `operator`, `split` or `leader` |
| `title`, `request` | `request` is the verbatim text |
| `mode` | `implement`, `plan` or `split`. `NULL` until triage, unless `submit --mode` sets it |
| `complexity` | `low`, `normal` or `high` (default `normal`) |
| `priority` | Integer; higher runs first |
| `depends_on` | Task ids |
| `status` | §1.2 |
| `step` | `triage`, `plan`, `implement`, `consult`, `verify` or `integrate` |
| `status_reason` | e.g. `superseded`, `split`, `cancelled by operator` |
| `holds` | Task-scoped: `paused`, `intervention` |
| `iteration` | Implement → verify rounds so far |
| `counters` | Persisted: `rejections_on_rung` (reset on rung change), `consults` (reset each iteration), `conflict_rounds` (reset per integration attempt). Resolving an escalation resets the counter behind its reason |
| `rung_index` | Per role, persisted. Never decreases |
| `overrides` | Operator rungs per role |
| `repos` | `[{path, base, base_sha, branch}]` |
| `slot`, `window_id`, `panes` | `panes` maps `{role: pane_id}` (the only place pane ids are stored) |
| `wait_reason` | Written by the scheduler |
| `requeued_at` | Set when resumed after an escalation; gives precedence |
| `summary` | Final summary |

Counters that only matter within one turn (nudges, relaunches, stall recoveries) live in memory. A restart re-runs the turn with those counters reset, which is bounded and safe.

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
| `escalated` | no. The worktree, sessions and window stay. On re-dispatch the task gets its previous slot if free; otherwise its sessions are relaunched with resume under the new `SHIKIRU_SLOT` |
| `done` | — terminal: window closed, worktrees removed, run files kept |
| `cancelled` | — terminal: window closed, worktrees removed, run files kept |

**Holds**:
- `paused` and `intervention` are task-scoped (§9).
- The **quota hold is derived, not stored on tasks**: a task whose next turn needs an agent with a row in `agent_blocks` waits until `until`.

## 2. Steps

| Step | Role | Template → report | Outcomes |
|---|---|---|---|
| triage | Leader | `triage` → `TriageReport` | `implement` → implement · `plan` → plan · `split` → create child tasks with dependencies; tasks depending on this one are repointed to all children; this task becomes `done` (`status_reason = split`) |
| plan | Leader | `plan` → `PlanReport` | → integrate; `new_tasks` are enqueued (the template tells the Leader to fill them only when the request asks to execute the plan) |
| implement | Implementer | `implement` (i = 1) / `rework` → `ImplementationReport` | `completed` → verify · `needs_decision`/`blocked` → consult |
| consult | Leader | `consult` → `ConsultAnswer` | `answer` → implement (same iteration) · `escalate` → `decision_required`. More than `CONSULT_LIMIT` (3) consults → `decision_required` |
| verify | Leader | `verify` (+ `facts.md`) → `Verdict` | `accept` → integrate · `changes_requested` → implement (i + 1) · `escalate` → `decision_required` · `supersede` → `new_tasks` enqueued, dependents repointed to them, this task `cancelled` (`superseded`) |
| integrate | Implementer on conflicts only | `conflicts` → `ImplementationReport` | §6 |

**Final round.** When `iteration >= max_iterations`, the verify prompt carries `final_round = true`:
- The Leader must choose `accept`, `supersede` (decompose into new tasks) or `escalate`.
- A `changes_requested` in the final round raises `iterations_exhausted`.

**Triage** is read-only (the prompt forbids edits). Tracker edits happen in `plan` tasks and in verify turns.
`submit --mode` skips triage. The title is then the first line of the request, cut to 60 characters.

**Templates.** Every template:
- includes the request and earlier reports (fenced as quoted data), tracker paths, and the sentence "If this work is already done in the workspace, verify it and report";
- ends with the fixed report instruction (§4.3);
- never restates project coding rules.

**Facts** (`facts.md`) are computed by the engine for each repo and outrank the Implementer's claims:
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

### 4.1 SessionManager

`ensure(task, role) → AgentSession`:

| Situation | Action |
|---|---|
| Run live and pane alive | Return the monitored session |
| Engine restarted, pane process alive | `agent.attach(spec, pane, runs.agent_state)` |
| No live process, `session_id` known | `agent.launch(spec with resume_session_id)` |
| Rung change, same agent | Send `keys.exit_input`, then launch with resume and the new model |
| No session yet, or a different agent | `agent.launch(spec)`. If the task already has reports, the first turn is `handoff` (request, latest reports, facts) |

- **Launch environment**: `SHIKIRU_PROJECT`, `SHIKIRU_TASK`, `SHIKIRU_ROLE`, `SHIKIRU_RUN_DIR`, `SHIKIRU_SLOT`.
- **After launch**: wait for `SessionStarted` (`timeouts.launch`; on timeout the turn result is `Stalled`), then store `session_id` and `resume_command`.
- **Task end**: exit sessions with `keys.exit_input`, and kill the pane after 10 s.

### 4.2 SessionMonitor

There is one monitor per live session. It is the **only** consumer of `session.signals()`, and it publishes every signal to subscribers (the current `TurnRunner`) through an asyncio queue.

On every `orchestration.poll_seconds` tick, it:
- stores `usage()`, `snapshot()` and the last `TurnEnded.last_message` on the run;
- checks `pane.is_alive()` → publishes engine event `PaneDied`;
- publishes `Stalled(screen)` when `domain.activity.is_stalled(last_activity, tool_running, now, timeouts)`; `AttentionNeeded` publishes `Stalled` immediately;
- sets the intervention hold when `domain.activity.is_operator_prompt` says so (§9).

`PaneDecorator` subscribes to the same fan-out and updates pane titles and decorations.

### 4.3 TurnRunner

`run(session, TurnSpec) → TurnResult`, where the result is one of:
- `Completed(report, rebuilt)`
- `Failed(kind, resets_at, message)`
- `PaneDied`
- `Stalled(screen)`
- `ProtocolError`

1. **Turn dir** `runs/T/turns/NN-<step>-i<iteration>/`: write `prompt.md`, `report.schema.json` and `report.kind`, remove any stale `report.json`, and point `runs/T/<role>/current_turn` at it.
2. **Before turn**: inject each line from `session.before_turn()` and wait for `Compacted` or `TurnEnded` (≤ `BEFORE_TURN_WAIT`, 10 min; on timeout → `Stalled`).
3. **Inject the pointer**: tmux `load-buffer` → `paste-buffer -p` → `INJECT_DELAY` (150 ms) → `Enter`. The line is `{prompt_prefix}Read {prompt path} and follow it exactly.`
4. **Confirm**: a `PromptSubmitted` containing the path within `SUBMIT_CONFIRM` (15 s). Otherwise press `Enter` once more. Otherwise return `Stalled`.
5. **Await** `TurnEnded`, `TurnFailed`, `PaneDied` or `Stalled`.
6. **On `TurnEnded`**:
   - a valid `report.json` → `Completed`;
   - otherwise nudge, up to `NUDGE_LIMIT` (2) times: inject "Your turn ended without a valid report. Submit it with `shikiru report` as described at the end of {prompt path}.";
   - otherwise `helper.rebuild_report` → `Completed(rebuilt=true)`, saved as `helper.json`;
   - otherwise `ProtocolError`.

**The report instruction** ends every prompt:

> Write the report JSON matching `report.schema.json` and run `shikiru report --file <file>`. If it exits non-zero, fix it and run it again. Then end your turn.

The command is spelled as `shikiru_command` (04 §1) renders it, so the agent's shell `PATH` never decides which Shikiru runs.

`shikiru report` works as follows:
- It resolves `$SHIKIRU_RUN_DIR/$SHIKIRU_ROLE/current_turn` and validates with the pydantic model named in `report.kind`, including cross-field rules.
- It writes `report.json` atomically.
- Exit codes: `0` ok, `2` invalid (errors on stderr), `3` no current turn.

**Cancellation**: `pause --now`, `cancel` and `retry` handlers send `keys.interrupt` and cancel the task coroutine. Step state is persisted at step boundaries, and prompts are idempotent, so the step simply re-runs later.

## 5. Workspaces

- **Task root** `.shikiru/worktrees/T-12/`:
  - Monorepo: the root *is* the worktree.
  - Multi-repo: each repo's worktree sits at its relative path, and the non-repo top-level entries (e.g. the root `CLAUDE.md`, `tasks/`) are symlinked.
  - Agents run in the task root.
- **Worktrees** are created for every configured repo: `git worktree add -b shikiru/T-12-<slug> <path> <base>`. `base` is `repos[].base` or the repo's current branch. Creation is idempotent.
- **Caches** are copied copy-on-write (`repos[].caches` globs that match ignored paths): macOS `cp -c -R`, Linux `cp -a --reflink=always`. If CoW isn't supported, nothing is copied and one warning is logged.
  - **Copy-on-write**: the filesystem (APFS; btrfs/XFS) creates only metadata that points at the same data blocks.
  - A 5 GB `node_modules` or `target` "copies" in about a second and uses no extra disk; only files that one side later modifies get their own blocks.
  - Every task gets a warm, fully independent cache.
- **Shared resources**: `SHIKIRU_SLOT` and `SHIKIRU_TASK` let project scripts separate ports and Docker Compose project names.
- **Git hygiene**: `.shikiru/` is added to each repo's `.git/info/exclude`.
- **Cleanup** on a terminal status: `git worktree remove --force`, and the branch is deleted once merged.

## 6. Integration

Integration runs under the project integration lock and only for repos with commits ahead of `base`.

1. **Rebase** each repo.
   - On conflict: `rebase --abort` → Implementer `conflicts` turn → Leader `verify` turn with post-rebase facts.
   - A conflicts report that isn't `completed` goes to consult (§2). A post-rebase `changes_requested` counts as a conflict round and re-runs the `conflicts` turn with the issues; `escalate` → `decision_required`.
   - At most `CONFLICT_ROUNDS` (2) rounds, then `integration_conflict`.
2. **Strategy**:
   - **`LocalMerge`** (`integration = "merge"`):
     - Pre-check every repo: `merge-base --is-ancestor base branch`, and, if `base` is checked out in the main checkout, no overlap between its dirty paths and the changed paths.
     - If a check fails: retry every `INTEGRATION_RETRY` (2 min) for up to `timeouts.integration_wait`, then `integration_blocked`.
     - Then for each repo: `git merge --ff-only` if `base` is checked out, else `git update-ref refs/heads/<base> <new> <old>`.
   - **`PullRequest`** (`integration = "pr"`):
     - `git push -u <repos[].remote> <branch>` and `gh pr create`.
     - The slot is released, `wait_reason = "PR open"`, and `gh pr view --json state` is polled every `PR_POLL` (5 min).
     - `MERGED` → done. `CLOSED` → `pr_closed`.
3. **Done**: cleanup, close the window, emit `task_done` (summary, commits, residual risks).

Dependents become ready only when their dependencies are `done`.

## 7. Recovery

`TaskFlow` passes every non-`Completed` `TurnResult` to the pure `recovery.decide(result, counters, policy) → Action`, then applies the action:

| Result | Action |
|---|---|
| `Failed(QUOTA)` | Block the agent in `agent_blocks` until `resets_at + QUOTA_MARGIN` (unknown reset: now + `QUOTA_PROBE`). When the block ends, re-inject `continue` into the live session (or resume it); a new failure repeats the cycle. If the wait exceeds `timeouts.max_quota_wait`: next rung with a different agent; none left → `quota_exhausted` |
| `Failed(UNAVAILABLE)` | Retry after 30 s, 2 min, 5 min; then next rung; none left → `ladder_exhausted` |
| `Failed(CONTEXT)` | New session on the same rung with `handoff` (once), then `agent_error` |
| `Failed(AUTH)` | `agent_auth` |
| `Failed(OTHER)` | Retry once, then `agent_error` |
| `PaneDied` | Relaunch with resume (≤ 2 per turn), then next rung, then `agent_crash` |
| `Stalled(screen)` | `helper.classify_screen`: `working` → keep waiting · `dialog` → send the keys if they are all in `keys.safe` · `question` → consult for the Implementer, `decision_required` for the Leader · `quota` → same as `Failed(QUOTA)` · `error` → same as `Failed(OTHER)`. More than 2 per turn → `agent_stuck` |
| `ProtocolError` | `agent_protocol` |

**Rungs**:
- The resolved rung is `overrides[role]` if set, otherwise the ladder at `rung_index[role]`.
- The Leader uses `roles.leader.ladder`. The Implementer uses `roles.implementer.complexity.<c>.ladder`, falling back to `roles.implementer.ladder`.
- Each rung change emits `rung_changed`.

**Engine restart (`Reconciler`)**:
1. Rebuild the tmux session and windows from the DB (`LayoutGuard`).
2. For each `running` or `integrating` task, ensure sessions (§4.1), then re-run the current step. The step's turn is idempotent: an existing valid `report.json` is consumed without re-injecting.
3. Pending `commands` are processed by the normal loop.

## 8. Escalation

| Reason | Raised when | Options (★ = recommended default) |
|---|---|---|
| `decision_required` | The Leader escalates (product decision, conflicting requirements, missing access), or the consult limit is exceeded | answer ★, cancel |
| `iterations_exhausted` | Final round still `changes_requested` | answer ★ (one more final round with guidance), accept, cancel |
| `integration_conflict` | Conflict rounds exhausted | retry ★, cancel |
| `integration_blocked` | Main checkout blocked beyond `integration_wait` | retry ★, cancel |
| `pr_closed` | PR closed unmerged | retry ★ (new PR), cancel |
| `agent_auth` / `agent_error` / `agent_crash` / `agent_stuck` / `agent_protocol` | §7 | retry [--rung] ★, cancel |
| `ladder_exhausted` / `quota_exhausted` | §7 | retry [--rung] ★, cancel |
| `dependency_cancelled` | A dependency was cancelled | retry ★ (drop the dependency), cancel |

**Packet** (`runs/T/escalations/E-n.md`, path stored in the DB):
- headline (task, step, reason, one sentence)
- timeline of recent events
- what was tried
- evidence: latest reports and verdict, facts, last 60 pane lines, session ids and resume commands
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
| Watch | attach, select the window | `peek_session` | Last message, pane screen, usage (from the DB and `capture-pane`) |
| Type into a session | select the pane and type | `send_message` | See below |
| Pause / resume | palette | `pause_task` (`now`), `resume_task` | `pause` stops before the next turn; `now` interrupts and cancels the current step |
| Change model | palette | `set_rung` | Applied at the next turn (§4.1) |
| Redo current step | palette | `retry_task` | Cancels and re-runs the current step |
| Open elsewhere | `status T-n` lists `resume_command` | `get_task` | — |

**Manual intervention.** `domain.activity.is_operator_prompt(text, run_dir, pending_lines)` is true when the prompt contains no path under the task's run dir (every pointer, nudge, `continue`, handoff and `say` injection does) and isn't a `before_turn` line currently being injected. It needs no persisted state. A true result sets the `intervention` hold on **that task**.
- **While set**: no nudges, stall handling or new turns. The pane border turns operator magenta and the title shows `⌨ operator`.
- **Cleared** when the current turn ends with a valid report (the Operator helped finish it), or on resume, which injects `continue` into that pane.

**`say`** writes `runs/T/messages/NNN.md` and injects `Operator message: read <path> and take it into account.` Agent TUIs queue input while busy, so `say` works mid-turn and never sets `intervention`.

## 10. Scheduling

The `Scheduler` runs on every state change and every `orchestration.poll_seconds`.

1. **Ready**: `queued`, not `paused`, all dependencies `done`. A cancelled dependency raises `dependency_cancelled`.
2. **Order**: `requeued_at` set first, then `priority` descending, then `seq`.
3. **Dispatch** in order until a gate fails; the task stops there and its reason is written to `wait_reason`. Gates:
   - **slot**: a free slot exists;
   - **resources**: `ResourceSampler` `CPU_WINDOW` average `< resources.max_cpu_pct` and available RAM `≥ resources.min_free_ram_gb`;
   - **quota**: neither the Leader's nor the Implementer's resolved agent is blocked.
4. **On dispatch**: assign the task's previous slot if free, else the lowest free slot, create the task window (`LayoutGuard`) and start the `TaskFlow` coroutine.
