# Soak results (Task 74u)

`uv run poe soak [--seed S]` (07 §5). Each run is two arenas side by side, local merge (`merge0`) and pull request mode (`pr1`), 480 s budget by default (60 % intake and faults, the rest draining; it ends once every task is terminal and delivery is confirmed). Host: macOS, tmux 3.7c, git 2.55.0, fake agent and fake `gh`. Timing is real, so a seed replays the schedule (submissions, operations, faults), not the exact interleaving.

## Clean full-length runs after the last fix (`7eff523`)

| Seed | Duration | Arena | Tasks submitted / final states | Operator commands | Faults injected (kind: count) |
|---|---|---|---|---|---|
| s4 | 373.5 s | merge0 | 76 / done 84, cancelled 6 (children of plans and supersedes included) | 113 (say 31, resolve 16, prioritize 17, set_rung 10, retry 7, take_over 7, hand_back 7, resume 9, pause 6, cancel 3) | engine_stop_start 3, engine_kill 2, engine_kill_integrating 2, engine_kill_escalating 2, engine_term 2, engine_freeze 2, tmux_loss 2, turn_kill 2, git_hook_fail 1, db_lock 1 |
| s4 | 373.5 s | pr1 | 68 / done 83, cancelled 14 (84 pull requests landed, 7 closed by the host) | 143 | engine_kill_escalating 2, engine_kill_integrating 2, turn_kill 3, engine_freeze 2, hook_stuck 2, db_lock 3, engine_kill_locked 1, engine_int 1, remote_down 1 |
| s5 | 370.0 s | merge0 | 72 / done 78, cancelled 10 | 149 | engine_kill_locked 3, db_lock 3, git_hook_fail 3, engine_kill_integrating 3, turn_kill 5, engine_kill 1, engine_freeze 1, tmux_loss_takeover 1 |
| s5 | 370.0 s | pr1 | 75 / done 83, cancelled 14 (84 landed, 10 closed) | 145 | engine_kill 4, db_lock 2, engine_stop_start 2, git_hook_fail 2, gh_fail 2, engine_kill_integrating 2, engine_term 1, tmux_loss 1, tmux_loss_takeover 1, engine_freeze 1, remote_down 1, hook_fail 1, hook_stuck 1, engine_kill_locked 1 |

Both runs ended with no violation of the five invariants, no engine exit nobody caused, every task terminal, every escalation and engine fault delivered to the hook, and no process, tmux server or temporary file left (checked from the process table). Earlier clean runs of the same code line: s1 (313 s, before the last three fixes it had found), s2 (306 s), s3 (380 s).

## Defects the soak found, and their fixes (each with a failing-first regression test)

| Seed (arena) | Defect | Fix |
|---|---|---|
| s1 (merge0) | A write lock longer than the 5 s busy timeout made the notification cursor write (outside every fault barrier) raise: "engine stopping on an unrecoverable fault", the engine exited. Critical (engine down until restarted) | `46ff753` the engine loop's own reads and writes go through an `engine loop` guard (02 §1) |
| found while triaging s1/s3 | A database error while recording the stop aborted `_shutdown` before the task supervisor ended the turn process groups: turns outlived the engine. Critical (leaked processes spending money) | `63ecdd6` every shutdown step runs, the lock is released, the first failure is raised afterwards (03 §7) |
| s4 (pr1) | The engine died at start with `database is locked` (`engine_started` append; the restore of the notification cursor): an engine restarted during a lock exited | `212dc18` the start sequence retries on the loop guard |
| s4 (pr1), same analysis | Every guard records an `engine_fault` event on failure; under a real lock that write failed too and ended the engine with the fault it was recording, and a recovery whose record failed was never retried | `9214c3c` `FaultReporter` logs and keeps the record owed; the guard offers every success for the recovery record |
| CLI status probe experiment | `shikiru submit` while the database stayed locked printed a Rich traceback (`OperationalError`) | `6c70790` `OperatorCommands.send` raises `DatabaseError` (readable exit, nothing queued) (06 §1) |

Driver defects found on the way (not product defects, fixed in `tests/soak`): concurrent appends on one connection, a `cancelled with committed work` rule that counted `needs_decision` turns as commits, a hold check racing a resume (now bracketed by two reads), psutil inspection errors on dying processes, hosting acting on a remote that is down.

## Detection power: mutations of the engine, each failing the soak

| Mutation | Invariant | Result |
|---|---|---|
| no replay of undelivered urgent events after a restart | 5 notifications | 5 violations (targeted schedule of kills at new escalations; the plain seed missed it) |
| `Task.held` always false | 2 holds | violations on the first pauses |
| scheduler ignores unfinished dependencies | 4 dependents' base | violations |
| `merge --ff-only` skipped | 3 integrated work | every done task reported |
| take over no longer stops the running turn | 1 single owner | caught by a take-over-heavy schedule (5 violations); the plain seed missed it |

The unmutated engine is clean on the same targeted schedules.

## Limits

No full-disk simulation. Faults whose effect is armed (a failing `gh` call, a failing reference update) take effect at the next such call, which the report counts as "took effect". The invariant-1 overlap check needs the fake agent's module name in the process command line. Linux was not exercised.
