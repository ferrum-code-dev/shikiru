# Test support

## Slow machines and spawn counts

`SHIKIRU_TEST_SPAWN_DELAY=<seconds>` delays every subprocess the test process spawns through
`SubprocessRunner` (`run`, `spawn`, `spawn_terminal` and `spawn_detached`) by that many real seconds,
so a test that only passes on a fast machine fails here. Run one file with a delay:

    SHIKIRU_TEST_SPAWN_DELAY=0.05 uv run pytest tests/integration/test_engine_faults.py -q

`SHIKIRU_TEST_SPAWN_COUNT_FILE=<path>` appends one line per spawn, the program name, to that file.
Delete the file before a run, then count the spawns of a whole run:

    rm -f /tmp/spawns.txt
    SHIKIRU_TEST_SPAWN_COUNT_FILE=/tmp/spawns.txt uv run pytest tests/integration -q
    wc -l < /tmp/spawns.txt

Both variables are unset by default and then add no delay and write no file. They count and delay only
the spawns the test process makes; the engine and agent processes it starts are separate and are not
affected. Calls through `SubprocessRunner` are always tracked for `processes_in_flight()`.

## Deadlines and the time scale

Every test deadline is written for an unloaded machine and multiplied by one factor from
`tests/support/waiting.py`: `wait_until` and `wait_until_advancing`, the tmux command limit, the CLI
limit, the engine start override and each test's pytest-timeout. `SHIKIRU_TEST_TIME_SCALE=<factor>`
sets the factor. Unset, the test process measures it once from a batch of 20 no-op spawns against
their unloaded time, never below 1 and capped at `MAXIMUM_TIME_SCALE`. Those calibration spawns
bypass `SubprocessRunner`, so they are neither delayed nor counted. Run a file on a slow machine
with a fixed factor:

    SHIKIRU_TEST_TIME_SCALE=3 uv run pytest tests/integration/test_tmux_palette_binding.py -q

## Fake clocks and teardown

An engine or flow driven by a `FakeClock` ends with `stop_engine(task, clock)`: it cancels the task and
advances the clock until the task has ended, bounded by `ENGINE_STOP_SECONDS`. A plain `await` after
`cancel()` hangs when the teardown sleeps on the fake clock. `wait_until_advancing` with `jump_while`
jumps the clock to the next sleeper only while `jump_while()` is true and no subprocess call is in
flight, so fake time does not outrun a tmux or git call that is still running.
