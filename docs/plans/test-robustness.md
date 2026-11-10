# Robust integration tests under load

CI integration tests fail intermittently when the runner is slow, a different test each time. An
investigation (measured on this repository, with injected process-spawn latency and CPU load) found
these causes, ranked by the failures they explain:

1. **Non-atomic tmux orchestration.** About 43 `SubprocessRunner` spawns per integration test, half of
   them `tmux set-option`. `SessionView.ensure` (`src/shikiru/tmux/session.py`) applies about twelve options
   one process at a time on every engine tick, and sets `remain-on-exit` last, so a pane command that dies
   early destroys its window or session first ("no such window", "no server running", "respawn pane failed").
   With 30 ms of added latency per spawn three tests fail 8 of 8 runs.
2. **Fixed absolute deadlines.** 779 of 1,284 `wait_until` calls use 30 s; tmux commands have a 5 s limit;
   pytest-timeout is 60 s. Heavy tests take 10 s locally and 47 s on a slow runner. With the test process
   and tmux niced under heavy load, the `tmux_server` fixture fails `new-session` after 5 s.
3. **A snapshot asserted after a weaker wait**, with no diagnostics (`test_quota_visibility`: the wait
   sees the strip on row 0, then a fresh `capture-pane -S -` of the whole history is asserted before the
   view process has replayed it; CI shows only `assert False`).
4. **FakeClock engines coupled to real polls.** `wait_until_advancing` moves fake time on every poll, and each
   poll can spawn tmux; teardown cancels the engine task with a bare `task.cancel(); await task`, which hangs
   on a frozen FakeClock until pytest-timeout (the hung-push test).
5. **Textual teardown races**: a worker runs while the screen is dismissed (`WorkerFailed(NoMatches ...)`).

Rejected as causes (measured): each test starting its own engine (only about 10 do), polling storms,
coverage overhead on subprocesses. CPU load alone reproduced almost nothing.

Each task checks only its own entry when complete and must keep every test that guards a stated
guarantee: do not delete a test unless the task says so and the report names the test that still covers
the guarantee. See [the test rules](../../AGENTS.md).

## Tasks

### R1 — A spawn-latency hook for the tests

- [x] Make "slow machine" reproducible and checkable.

Acceptance criteria:

1. Test support wraps `SubprocessRunner` so that, when `SHIKIRU_TEST_SPAWN_DELAY` (seconds, a float) is
   set, every spawned process in the test run is preceded by that delay (async sleep on the real clock),
   and, when `SHIKIRU_TEST_SPAWN_COUNT_FILE` is set, each spawn appends one line (the program name) to that
   file. Both are unset by default and cost nothing then. The hook lives in `tests/support/` and is
   installed from `tests/conftest.py`; no product code changes.
2. `tests/support/README.md` or the existing test-support docs gets a short paragraph: how to run a file
   with `SHIKIRU_TEST_SPAWN_DELAY=0.05` and how to count spawns.
3. The report lists, for the baseline, the spawn count of the whole integration suite and the result
   of `SHIKIRU_TEST_SPAWN_DELAY=0.03 uv run pytest tests/integration/test_engine_start_failure.py
   tests/integration/test_engine_faults.py tests/integration/test_engine_daemon.py -q` (the failing
   tests are expected to fail here).
4. The gate passes.

### R2 — Batch the tmux setup commands

- [x] Fewer processes per tmux operation, and `remain-on-exit` set before anything can die.

Acceptance criteria:

1. A `Tmux.batch(commands)` method (in the tmux client, `src/shikiru/tmux/`) runs several tmux commands
   in one process, joined with tmux's `;` separator, and returns one result. A failing command in the batch
   fails the call with the same error type as a single command today.
2. `SessionView.ensure` and the decorator and title refresh use it: creating a session or window and
   all its options are one batch, with `remain-on-exit` applied in the same batch as the creation. The
   observable tmux state after `ensure` is unchanged (a test compares the options before and after).
3. The spawn count of the integration suite (R1's counter) falls by at least 40% against the R1 baseline
   (report both numbers).
4. With `SHIKIRU_TEST_SPAWN_DELAY=0.03` and `0.08`, `test_engine_start_failure.py`, the tmux-kill test in
   `test_engine_faults.py` and the takeover-lost test in `test_engine_daemon.py` pass 8 of 8 consecutive
   runs each (a shell loop; report the commands and results).
5. The gate passes.

### R3 — One deadline policy for the tests

- [x] Deadlines scale with the machine instead of being fixed numbers.

Acceptance criteria:

1. `tests/support/waiting.py` gets one scale factor, `SHIKIRU_TEST_TIME_SCALE` (float, default 1.0) and,
   when unset, a factor measured once per test session from a short calibration (the time of a fixed
   small batch of subprocess spawns against its known unloaded time, never below 1.0 and capped at a
   named maximum). `wait_until`, the `TmuxServer` command limit, the CLI limit, the engine-start
   override and the pytest-timeout (a `pytest_collection_modifyitems` hook that scales the marker
   or the configured value) all multiply by it.
2. `WaitTimeoutError` messages include the predicate's description and its last evaluated value
   when available. (Tests keep passing the same arguments.)
3. No test file contains a literal wait/timeout number except as a relative argument to the shared
   helper; a lint-like test or a ruff rule in the gate fails when a new `timeout=<number>` appears in
   `tests/`. Convert the existing ones (mechanical).
4. With the test process and tmux started at `nice -n 19` under 24 busy loops (`yes > /dev/null &`, killed
   afterwards), `tests/integration/test_tmux*.py` and the `tmux_server` fixture users that failed in the
   investigation pass without `new-session` timeouts (report the command and the result).
5. The gate passes.

### R4 — Assert on conditions, not on a fresh snapshot

- [x] `test_quota_visibility` and the dashboard snapshot assertions wait for the condition they assert.

Acceptance criteria:

1. A `wait_for(sample, accept)` helper in `tests/support/waiting.py` polls `sample()` until
   `accept(value)` is true and returns the accepted value; on timeout it raises with the last sampled
   value (and for pane captures, the capture) in the message.
2. The width-resize, height-resize and status-growth tests in `tests/integration/test_quota_visibility.py`
   use it, asserting on the sample that satisfied the condition. Where the guarantee is about the view
   tail replaying history (`ViewTail._reset_for_replay`), test it at unit level with a recording console and
   FakeClock, and keep exactly one real-tmux test of the same guarantee; delete the other real-tmux
   tests only if the report names the test that still covers each guarantee.
3. The other snapshot-after-weak-wait assertions the investigation found
   (`contains_region` right after a refresh, `resource_reads == 1`, `assert clicked`) wait for their
   condition; a count read from a `SystemClock` timer is replaced by a wait on the observable result.
4. Run the changed files 30 times in a loop under `SHIKIRU_TEST_SPAWN_DELAY=0.05`; report the number
   of failures (the target is none).
5. The gate passes.

### R5 — FakeClock engines stop cleanly and wait for processes

- [x] A frozen FakeClock never hangs a teardown, and fake time does not outrun real processes.

Acceptance criteria:

1. A `stop_engine(task, clock)` helper cancels the engine task and keeps advancing the FakeClock until the
   task has ended, with a bounded real-time limit. The about eleven files that cancel the task with
   `task.cancel(); await task` use it.
2. `wait_until_advancing` jumps the FakeClock to the next sleeper only when no subprocess is in flight
   (counted by a wrapper around the runner), so each fake step is not computed while a tmux call is
   still running.
3. The PR-close retry test in `test_engine_daemon.py`, the tmux-kill test in `test_engine_faults.py` and
   the hung-push test in `test_engine_push.py` pass 50 consecutive runs at `SHIKIRU_TEST_SPAWN_DELAY=0.08`
   (report commands and results). The hung-push test is restored to its original form (with its Submit)
   if the earlier workaround removed it.
4. The gate passes.

### R6 — Textual apps end their workers before the test leaves

- [x] No `WorkerFailed` from a screen dismissed under a running worker.

Acceptance criteria:

1. A `leave_app` helper awaits `app.workers.wait_for_complete()` (bounded) before a dashboard test's
   `run_test` block ends; the dashboard tests that dismiss screens use it.
2. Product code that reads widgets from a worker with `query_one` either guards the missing widget or
   runs after the worker is cancelled on dismissal; the report lists each place changed (for example the
   actions and wizard titles).
3. The dashboard test files run 20 times in a loop at `SHIKIRU_TEST_SPAWN_DELAY=0.05` without a
   `WorkerFailed` (report commands and results).
4. The gate passes.

### R7 — The resources test asserts membership, not live memory

- [x] `test_resources_service_excludes_panes_from_other_projects` no longer compares two live RSS readings.

The test (`tests/integration/test_dashboard_remaining_widgets.py`) asserts that the snapshot's RSS is within
`RESOURCE_RSS_MATCH_TOLERANCE_BYTES` (16 MiB) of a second reading of the same processes taken a moment
later. The pytest process and the pane shells change their memory by more than that under parallel load
(measured: 35 MiB), so it fails at random; the guarantee it protects is only which processes are counted.

Acceptance criteria:

1. The test keeps its setup (a pane of this project, a role pane owned by a task, a pane of another
   project) and asserts membership: the pane process of the other project is not counted, and the role
   pane's tree is counted under the agents figure. Make the signal larger than any drift: the processes of
   the other project's pane and the role pane each hold a known extra allocation (for example a Python child
   that allocates and touches 200 MiB and then sleeps on an event the test releases), and the assertions
   compare against half of that allocation, never against a second live reading of unrelated processes.
2. No assertion tolerance in the file is smaller than the signal it separates from the noise;
   `RESOURCE_RSS_MATCH_TOLERANCE_BYTES` is deleted or replaced by the named allocation size.
3. The test passes 30 consecutive runs under `SHIKIRU_TEST_SPAWN_DELAY=0.05` (if R1 has landed; otherwise
   under 8 parallel runs of the file) and 10 consecutive runs while 24 busy loops run
   (`yes > /dev/null &`, killed afterwards). Report the commands and results.
4. The gate passes.

## R8 — The package smoke clicks again when a click is lost

- [x] `click_button` in `tests/support/release_wait.py` retries a click that did not navigate.

CI run 42829345466 (Ubuntu) failed in the package smoke, stage "normal-size interactive wizard": after the
ladders were edited, the click on `Next` did not move the wizard to "SHIKIRU · Trackers" within 20 s, and the
last pane capture still showed the ladders page. The smoke passes locally on the same commit. The click is a
single mouse event sent into the pane; the wizard may redraw between the capture that finds the button and
the event, or may still be handling the last key, and one lost event fails the whole release smoke.

Acceptance criteria:

1. `click_button` sends the click, waits a short interval (a named constant, a few seconds, scaled like the
   other waits) for `expected`, and when it did not appear captures the pane again, finds the button again and
   clicks again. It gives up after a named number of attempts or at the existing overall deadline, with the
   last capture on stderr as today. Before every attempt it checks `expected` first, so a click that landed
   late is never repeated.
2. The helper's unit tests (use the existing fakes for tmux) cover: the first click works; the first click is
   lost and the second works; the button is gone because the page already changed; every attempt fails and
   the error carries the last capture.
3. No other timeout in the smoke or in the waiting helpers is raised.
4. The gate passes. Nothing is pushed by the task.

## R9 — Name what fills the macOS runner's disk

- [x] CI prints disk use before and after the gate, so the next "No space left on device" names its cause.

CI run 42836381544 (macOS) passed the quality gate (2560 passed) and then failed in "Build and exercise
package distributions" right after "installed entry points, defaults, prompt, migration and artwork
resources verified"; the runner's last line is `No space left on device` for its own log folder. The
runner's disk filled during the gate or the smoke. pytest already keeps temporary folders only for failed
tests (`tmp_path_retention_policy = "failed"`), so something else leaves large data behind. Nothing here is
known yet; this task finds out, it does not guess.

Acceptance criteria:

1. In `.github/workflows/ci.yml`, add a step before the quality gate, one after it and one after the
   package smoke (the last two with `if: always()`) that print `df -h` for `/` and the temporary folder and
   the ten largest entries (`du -sk`, depth 1, sorted) of `$TMPDIR`, `/tmp` (or `/private/tmp`),
   `$RUNNER_TEMP` and `$HOME`. The steps never fail the job (`|| true`) and print at most 40 lines each.
2. No other change to the workflow, no cleanup yet, no timeout change.
3. Tests: the workflow test checks the three steps exist, run on both operating systems and cannot fail the
   job.
4. The gate passes. Nothing is pushed by the task.
