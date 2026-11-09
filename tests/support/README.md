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

Both variables are unset by default and then cost nothing. They count and delay only the spawns the
test process makes; the engine and agent processes it starts are separate and are not affected.

## Deadlines and the time scale

Every test deadline is written for an unloaded machine and multiplied by one factor from
`tests/support/waiting.py`: `wait_until` and `wait_until_advancing`, the tmux command limit, the CLI
limit, the engine start override and each test's pytest-timeout. `SHIKIRU_TEST_TIME_SCALE=<factor>`
sets the factor. Unset, the test process measures it once from a batch of 20 no-op spawns against
their unloaded time, never below 1 and capped at `MAXIMUM_TIME_SCALE`. Those calibration spawns
bypass `SubprocessRunner`, so they are neither delayed nor counted. Run a file on a slow machine
with a fixed factor:

    SHIKIRU_TEST_TIME_SCALE=3 uv run pytest tests/integration/test_tmux_palette_binding.py -q
