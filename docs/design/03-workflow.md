# 03 · Workflow

## 1. Task model

A **task** has these fields:
- `id` (`T-<n>`), `parent_id`, `origin` (`operator | split | leader | replan`)
- `title`, `request` (the verbatim Operator or Leader text)
- `mode` (`triage | implement | plan | split`), `complexity` (`low | normal | high`), `priority` (int, higher runs first)
- `depends_on[]`, `repos[]`
- `status`, `step`, `holds[]`, `iteration`, `slot`, `rung overrides`, `window_id`, `budget_usd`, and timestamps

### 1.1 Status and holds

```
queued ──► running ──► integrating ──► done
  ▲           │  │          │
  │           │  └─► waiting (split parent: until all children are terminal)
  │           ▼
  └──── escalated ◄─── (any running step or integration)
any non-terminal ──► cancelled
```

| Status | Holds a slot | Meaning |
|---|---|---|
| `queued` | no | Waiting for dependencies, a free slot or the resource gate. Tasks resumed after an escalation are queued with precedence. |
| `running` | yes | `step` ∈ `triage, plan, implement, consult, verify, replan` |
| `integrating` | yes (plus the project integration lock) | Rebase, conflict hand-off, merge or PR |
| `waiting` | no | A split parent, or PR mode with the PR open (a `pr_open` hold, polled) |
| `escalated` | no | Waiting for a resolution. The worktree, sessions and window stay. |
| `done`, `cancelled` | no | Terminal. The window closes, the worktree is removed, the run files are kept. |

**Holds** pause a task without changing its status, and they clear on their own:

| Hold | Clears when |
|---|---|
| `paused` | `resume` |
| `intervention` | §10 |
| `quota` | the reset time passes |
| `backoff` | a timer fires |
| `resources` | the resource gate passes (only at dispatch) |
| `integration` | the main checkout becomes clean, or a retry timer fires |
| `pr_open` | the PR is merged |

`domain.transitions` holds the full transition table as data. `TaskFlow` looks transitions up there and never branches on status ad hoc.

## 2. Steps

| Step | Agent | Template | Report schema | Next |
|---|---|---|---|---|
| prepare | — | — | — | Create worktrees (§6), then triage. If `--mode` is given at submit, go straight to that step. |
| triage | Leader | `triage.md.j2` | `TriageReport` | `implement` → implement · `plan` → plan · `split` → create children, then `waiting` |
| plan | Leader | `plan.md.j2` | `PlanReport` | integrate. `new_tasks` are enqueued when `enqueue=true`. |
| implement | Implementer | `implement.md.j2` (first) / `rework.md.j2` (iteration > 1) | `ImplementationReport` | `completed` → verify · `needs_decision`/`blocked` → consult |
| consult | Leader | `consult.md.j2` | `Verdict` (with `answer`) | `answer` → implement (same iteration) · `escalate` → escalation |
| verify | Leader | `verify.md.j2` + engine `facts.md` | `Verdict` | `accept` → integrate · `changes_requested` → implement (iteration+1) · `escalate` → escalation |
| replan | Leader | `replan.md.j2` | `ReplanDecision` | `decompose` → children + this task `cancelled(superseded)` · `restate` → implement with the new request (once per task) · `escalate` |
| integrate | Implementer (only on conflicts) | `conflicts.md.j2` | `ImplementationReport` | §7 |

**Triage is read-only**: the prompt forbids file changes, because a `split` parent never integrates. Any tracker edit needed goes into a `plan` task.
After triage:
- worktrees of repos not listed in `TriageReport.repos` are removed;
- a `split` parent releases its worktrees, window and slot, while its sessions end.

Every template starts with the task request, the relevant earlier reports (fenced as quoted data), the tracker file paths from config and the report instructions.
It never restates project coding rules: the agent's own CLAUDE.md, AGENTS.md and skills apply.

**Verify facts (`facts.md`)** are computed by the engine and are authoritative over the Implementer's claims. For each repo they contain:
- `git log --oneline base..HEAD`
- `git diff --stat base...HEAD`
- `git status --porcelain` (should be empty)
- whether each configured `tracker` file changed

**Scope changes (D4).** A `Verdict` or `PlanReport` may carry `new_tasks[]` (each with `title`, `request`, `priority`, `depends_on`) and `scope_changes[]` (descriptions).
- The Leader edits the tracker files in the task worktree during its turn, following project conventions, and commits.
- The engine enqueues `new_tasks` with `origin=leader` and logs a `scope_change` event, which triggers a notification.
- Undo is `shikiru cancel T-n`.

## 3. Review loop

```
iteration = 1
loop:
  report = implement(iteration)                      # rework prompt carries previous verdict issues
  while report.status in {needs_decision, blocked}:  # at most consult_limit (3) per task
      verdict = consult(report) ; if escalate → ESCALATE
      report = implement(iteration, answer=verdict.answer)
  verdict = verify(report, facts)
  accept             → INTEGRATE
  escalate           → ESCALATE
  changes_requested  → rejected_on_rung += 1
                       if rejected_on_rung ≥ ladder.escalate_after (2): move implementer one rung up (§8.2)
                       iteration += 1 ; if iteration > max_iterations (5): REPLAN
```

The Leader accepts only when no issues remain (`review.accept_minor_issues = false`, which matches the "address even minor issues" practice).
Residual risks that should not block go to `new_tasks`. The Leader states each accepted residual risk in `residual_risks[]`, and these are shown in the task summary.

## 4. Turn protocol

`SessionDriver.run_turn(task, role, template, schema) → TurnResult` is the only way the engine talks to an agent.

1. **Resolve rung**: the task override, else the role ladder for the task complexity at `rung_index`.
2. **Write the turn dir** `runs/T/turns/NN-<step>-i<iteration>/`: `prompt.md`, `report.schema.json`, `report.kind`. Remove any stale `report.json`. Point `runs/T/<role>/current_turn` at this dir.
3. **Ensure the session** (§5).
4. **Compact if needed**: the adapter has `compact_command` and the computed context free % (04-agents §1) is `< rung.compact_below_free_pct` → send the command → await `Compacted` or `TurnEnded` (≤ 10 min).
5. **Inject the pointer**: `load-buffer -b shk-<pane> -` → `paste-buffer -d -p -b shk-<pane> -t <pane>` → sleep `inject_delay_ms` (150) → `send-keys -t <pane> Enter`.
   The pasted line is `{prompt_prefix}Read {abs prompt path} and follow it exactly.`
6. **Confirm submission**: a `PromptSubmitted` signal with this turn's path (or `Busy` for agents without hooks, 04-agents §1.2) must arrive within 15 s. If not, press `Enter` once more. If it still doesn't arrive, run stall analysis (§8.5).
7. **Await** the first of:
   - `TurnEnded`
   - `TurnFailed`
   - `PaneDied`
   - `Stall`
   - a matching Operator command (`pause --now`, `cancel`, `retry`)
8. **On `TurnEnded`** (for agents without hooks: synthesised from `ReportSubmitted` plus `Quiescent`, 04-agents §1.2):
   1. If `report.json` exists and validates → `Completed(report)`.
   2. Otherwise nudge, at most `nudge_limit` (2) times: inject "Your turn ended without a valid report. Submit it with `shikiru report` exactly as described at the end of {prompt path}.", then go back to 7.
   3. Otherwise the Helper rebuilds the report from `last_assistant_message` and the transcript tail → `Completed(report, rebuilt=true)`, logged.
   4. Otherwise escalate (`agent_protocol`).
9. `TurnFailed`, `PaneDied` and `Stall` are handled in §8.

**Report submission.** The prompt ends with the same fixed instruction every time:

> Write the report JSON matching `report.schema.json` and run `shikiru report --file <file>`. If it exits non-zero, fix the JSON and run it again. Then end your turn.

`shikiru report` works as follows:
- It resolves the current turn dir (§5) and reads `report.kind` there (e.g. `Verdict`).
- It validates the JSON with that pydantic model, including cross-field rules the JSON Schema can't express, and writes `report.json` atomically.
- Exit codes: `0` ok, `2` invalid (errors on stderr), `3` no active turn.
- It never touches the DB, so it works inside agent sandboxes. The turn dir is passed to Codex with `--add-dir`.

Prompts are idempotent: "If this work is already done in the workspace, verify it and report." This makes re-injection after recovery safe.

## 5. Sessions and panes

- Each running task has one tmux **window** with the panes `system`, `leader`, `implementer` and `shell` (06-operator §5). Pane ids (`%N`) are stored in the DB.
- **Launch**:

  ```
  respawn-pane -k -t <pane> -c <task root> -e SHIKIRU_PROJECT=… -e SHIKIRU_TASK=T-12 -e SHIKIRU_ROLE=implementer -e SHIKIRU_RUN_DIR=… -e SHIKIRU_TURN_DIR=<current> -e SHIKIRU_SLOT=2 -- <adapter argv>
  ```

  Then wait for `SessionStarted(session_id, transcript_path)` (≤ `launch_timeout` 90 s) and store both on the run.
- `SHIKIRU_TURN_DIR` can't change inside a running process. So `shikiru report` resolves the current turn from `$SHIKIRU_RUN_DIR/current_turn`, a symlink the engine updates. The env var is only a fallback.
- **Resume**: the same argv with `resume_session_id` (`claude --resume <id>`, `codex resume <id>`). Used after a pane death, an engine restart, a quota wait with an exited process, or a same-agent rung change (with the new `--model` and effort).
- **Agent change** (a rung with a different agent): start a new session. The first prompt is `handoff.md.j2`, which contains the task request, the latest reports, facts and the previous transcript path.
- Sessions live until the task is terminal. At that point the engine sends the adapter's `exit_command`, or kills the pane after 10 s.
- The session id, transcript path and resume command are always shown by `shikiru sessions T-12`, so an Operator can reopen the session anywhere.

## 6. Workspaces

- **Task root** `.shikiru/worktrees/T-12/`:
  - Monorepo, where the project root is the repo: the root is the worktree.
  - Multi-repo: each repo's worktree sits at its relative path. Top-level entries that are not repos and not `.shikiru` are symlinked, e.g. the root `CLAUDE.md` and `tasks/`.
  - Agents start in the task root.
- **Create** per repo:

  ```
  git -C <repo> worktree add -b shikiru/T-12-<slug> <path> <base>
  ```

  - `base` = `repos[].base` or the repo's current branch, recorded on the task.
  - Then `git submodule update --init --recursive` if `.gitmodules` exists.
  - Worktree creation for existing branches is idempotent.
- **Caches**:
  - For each `repos[].caches` glob that matches an existing ignored path in the main checkout, copy it with copy-on-write: macOS `cp -c -R`, Linux `cp -a --reflink=always`.
  - **Copy-on-write (CoW)**: a filesystem feature (APFS on macOS; btrfs and XFS on Linux) where a "copy" is only new metadata pointing at the same data blocks. A 5 GB `node_modules` or `target` dir is copied in about a second and uses no extra disk. Only files one side later modifies get their own blocks. Each task gets a warm, fully independent cache, and a build in one worktree can't corrupt another's.
  - If copy-on-write isn't supported, `cache_mode = "clone"` falls back to no copy and logs a warning once. `doctor` reports copy-on-write support.
  - `cache_mode = "symlink"` is available for caches that are safe to share.
- **Isolation of shared resources**: each task exports `SHIKIRU_SLOT` and `SHIKIRU_TASK`. Projects use them in their own instructions or scripts for ports and Docker Compose project names. Shikiru adds nothing project-specific.
- **Git hygiene**: `init` adds `.shikiru/` to `.git/info/exclude` of every repo. Worktrees share it through the common dir.
- **Cleanup** on terminal status: `git worktree remove --force` for each repo. Delete the branch if it was merged and `keep_branches=false`.

## 7. Integration

Integration happens under the project **integration lock**, one task at a time.

1. For each repo: `git -C <wt> rebase <base>`.
   On conflict: `rebase --abort` → Implementer `conflicts` turn (rebase, resolve, rerun the relevant tests, commit, report) → Leader `verify` turn with the post-rebase facts.
   At most `integration.conflict_rounds` (2) rounds, then escalate `integration_conflict`.
2. **Pre-check for all repos**, before merging anything:
   - `merge-base --is-ancestor <base> <branch>` holds.
   - If the main checkout has `base` checked out, none of its dirty paths overlap the changed paths.
   If the pre-check fails because of Operator edits in the main checkout: `integration` hold, retry every 2 min for up to `integration.dirty_wait` (30 min), then escalate `integration_blocked`.
3. **Merge** (`integration = "merge"`):
   - If the main checkout has `base` checked out: `git -C <repo> merge --ff-only <branch>`.
   - Otherwise: `git update-ref refs/heads/<base> <branch sha> <old base sha>`.
   - Then `git push <remote> <base>` if `push = true`.
4. **PR** (`integration = "pr"`):
   - `git push -u <remote> <branch>`.
   - `gh pr create --base <base> --head <branch> --title "<T-12> <title>" --body <summary + verdict + risks>`.
   - Set the `waiting` status with a `pr_open` hold. Poll `gh pr view --json state` every 5 min: `MERGED` → done, `CLOSED` → escalate.
5. `done`: cleanup (§6), close the window, emit `task_done` with the summary, commits and residual risks.

Dependents become ready only after their dependency is `done`, i.e. its changes are on `base`.

## 8. Resilience

### 8.1 Failure signals

Adapters map hooks and transcripts into `TurnFailed(kind, resets_at?, message)`. Unknown text goes to the Helper, which classifies it into one of these kinds.

| Kind | Handling |
|---|---|
| `quota` | `on_quota = "wait"` (default): `quota` hold until `resets_at + 60 s`. If `resets_at` is unknown, probe every 15 min by injecting `continue.md`. If the wait would exceed `max_quota_wait` (8 h), or `on_quota = "next"`: move to the next rung whose agent differs. After the wait: inject `continue.md` into the live session, or resume the session if it exited. Other tasks on the same agent stay blocked by the same hold, which is set per agent. |
| `unavailable` (overloaded, model missing) | Back off 30 s, 2 min, 5 min, then move to the next rung |
| `context` (overflow) | Compact and retry once, then start a new session on the same rung with a handoff |
| `auth` | Escalate `agent_auth` immediately |
| `other` | Retry once, then escalate `agent_error` |

### 8.2 Model ladder

Rungs are `{agent, model, effort}` (05-data §1). Moving up happens on repeated rejections (§3), `unavailable` and the quota policy. Every rung change is an event and appears in the pane title.
The rung index never moves down within a task. `shikiru rung T-12 implementer codex:gpt-5.6-sol:high` sets an explicit override.
If no rung is left, escalate `ladder_exhausted`.

### 8.3 Pane death

Relaunch with resume, at most 2 times per turn. After that, move to the next rung. If that fails too, escalate `agent_crash` with the dead pane's output kept, since `remain-on-exit` is on.

### 8.4 Engine restart (`Reconciler`)

On start:
1. Clear stale locks.
2. Rebuild the tmux session and windows from the DB.
3. For each `running` or `integrating` task:
   - If the stored pane is alive and still runs the agent, re-subscribe to `hooks.jsonl` at the stored offset.
   - Otherwise resume the session.
   - Then either consume an existing valid `report.json` or re-inject the current turn's pointer.
   - Integration restarts from §7.1, which is idempotent.
4. Process pending `commands`.

### 8.5 Stall

A turn is stalled when no hook event has arrived and the transcript is unchanged for `stall_timeout` (20 min).
While a tool is running (a `ToolStarted` without its `ToolFinished`), the limit is `tool_timeout` (120 min) instead, so long test tiers aren't flagged.

On a stall:
1. Capture the visible screen (`capture-pane -p -J`).
2. The Helper classifies it into one of:
   - `working` → extend the timer.
   - `dialog{keys}` → send the keys, but only from the adapter's `safe_keys`.
   - `question{text}` → treat as `TurnEnded` with `needs_decision`, which leads to a consult.
   - `error{text}` → `TurnFailed(other)`.
3. After two stall recoveries in one turn → escalate `agent_stuck`.

An `AttentionNeeded` hook signal (Claude `Notification`, Codex `PermissionRequest`) starts stall analysis immediately.

## 9. Escalation

**Escalations are raised only in these cases**:

| Reason | Trigger |
|---|---|
| `decision_required` | Leader `Verdict`/`ReplanDecision` `escalate` (product decision, conflicting requirements, missing access or credentials) |
| `iterations_exhausted` | `replan` decides `escalate` |
| `integration_conflict`, `integration_blocked`, `pr_closed` | §7 |
| `agent_auth`, `agent_error`, `agent_crash`, `agent_stuck`, `agent_protocol`, `ladder_exhausted` | §4, §8 |
| `quota_exhausted` | The quota wait exceeds `max_quota_wait` and no other agent rung exists |
| `budget_exceeded` | The task cost exceeds `budget_usd` (optional) |
| `dependency_cancelled` | A dependency was cancelled |

**Packet** `runs/T/escalations/E-3.md`, which also goes in DB `escalations.packet`, contains:
- **Headline**: task, step, reason and a one-sentence summary.
- **What happened**: a timeline of the last events.
- **What was tried**: retries, rungs, nudges, consults.
- **Evidence**: latest reports and verdict (full), facts, the last 60 lines of the pane, transcript paths, resume commands.
- **Question**: from the Leader when present.
- **Options**: numbered, with consequences and one marked ★ recommended. Leader options come first, then the engine's standard options that apply:

| Option | Effect |
|---|---|
| `answer <text>` | Consult turn for the Leader with the text. The flow continues from the recorded step. |
| `retry [--rung a:m:e]` | Re-run the failed step (optionally on another rung) |
| `accept` | Override the verdict and integrate |
| `leave` | Mark `done` without integrating. The branch is kept. |
| `cancel` | Cancel the task. The branch is kept. |

**Resolution**: `shikiru resolve E-3 <option> [text]` (CLI, MCP or palette). The engine re-queues the task with precedence, or finishes it.
Raising an escalation frees the slot, sends a notification (06-operator §6) and turns the pane borders of the task window red.

## 10. Operator access and intervention

Every agent session is a real interactive session that the Operator can reach at any time:

| Action | Human | Tool / AI (CLI `--json`, MCP) | Engine behaviour |
|---|---|---|---|
| Watch | attach, select the task window | `peek T-12 --role leader [--lines N]` → last assistant message, pane screen, telemetry | — |
| Type into a session | click or select the pane and type | `say T-12 --to implementer "…"` | See below |
| Pause / resume | palette, `pause T-12 [--now]` | same | `pause` stops before the next turn. `--now` also sends the adapter's `interrupt_keys` (`Escape`). |
| Change model | `rung T-12 <role> a:m:e` | same | Applied at the next turn by resuming the session |
| Redo a step | `retry T-12 [--step verify]` | same | Aborts the current turn and runs the step again |
| Open elsewhere | `sessions T-12` → `cd <root> && claude --resume <id>` | same | — |

**Scope: always one task.** Every intervention, hold, pause and resume applies to the one task that owns the pane or is named in the command. Other tasks, including those running in parallel on the same agent, are unaffected. Each task has its own `TaskFlow` coroutine, and a hold only blocks that coroutine.
`quota` is the one hold that spans tasks: it is set per agent account, because the account's limit is shared (§8.1).

**Detecting manual intervention.** The engine records every line it injects into a run: pointers, nudges, `continue.md`, `say` pointers and compact commands. The `intervention` hold is set on **that task only** when:
- **agents with hooks**: a `PromptSubmitted` signal arrives whose prompt matches none of the recorded lines;
- **agents without hooks**: `Busy` appears in a pane while the task has no turn in flight (between turns or after the report). Input typed during an in-flight turn can't be told apart from the agent's own output, so the Operator should run `pause T-12` first. The palette's "Intervene" action does this automatically.

While the hold is set:
- that task's nudges, stall detection and new turns are suspended;
- its pane titles show `✋ operator`;
- its comic strip shows an `OPERATOR` stamp.

The hold clears:
- when a valid `report.json` for the current turn appears and the turn ends (the Operator helped finish it), or
- on `resume T-12`, which resumes only T-12 and injects `continue.md` into the pane that was intervened.

`say` writes `runs/T/messages/NNN.md` and injects the pointer `Operator message: read <path> and take it into account.` Both CLIs queue input typed during a busy turn, so `say` works mid-turn and never sets the `intervention` hold.

## 11. Scheduling

The `Scheduler` runs on every state change and at least every 5 s.

1. **Ready set**: `queued` tasks with no `paused` hold whose dependencies are all `done`. A task with a `cancelled` dependency is escalated `dependency_cancelled` instead.
2. **Order**: tasks resumed from an escalation first, then `priority` descending, then `seq` ascending.
3. **Dispatch**: fill free slots (`max_parallel` minus the number of `running`/`integrating` tasks) in that order. Stop at the first task that fails a gate, so priority order is kept.
   - **Resource gate**: host CPU averaged over 30 s `< max_cpu_pct` and available RAM `≥ min_free_ram_gb`. On failure, set a `resources` hold, visible in the queue widget, and retry on the next tick.
   - **Quota gate**: the task's first Leader rung agent is not under a `quota` hold. Starting it would only park it in a slot.
4. `Scheduler.explain(task)` returns the blocking reason (dependency, slot, resources, quota, paused) for `queue`, `show` and the dashboard.

Slots are numbered. A task takes the lowest free number and keeps it until it leaves `running`/`integrating`. The slot number is exported as `SHIKIRU_SLOT`.

Rebase-and-merge keeps each task's own commits instead of cloning repositories, and worktrees share one object store. That covers the requirement for a separate branch per task with build caches kept (see D6).
