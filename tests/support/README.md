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
