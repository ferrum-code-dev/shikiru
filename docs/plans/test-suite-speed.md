# Test suite speed

The quality gate takes about 6 minutes on a 10-core Mac and 12 to 25 minutes on GitHub Actions
(Ubuntu 4 cores, macOS 3 cores). The suite has 293 unit tests and 1,165 integration tests; the slowest
15 take 24 to 52 seconds each on the macOS runner, and most of those start real engines, tmux
sessions and agent processes. The owner believes many integration tests are badly designed or not
necessary. This tracker starts with measurement and judgment; nothing is deleted or changed in A1.

Each task checks only its own entry when complete.

## Tasks

### A1 — Measure and audit (report only)

- [ ] Produce `docs/plans/test-suite-audit.md` and change no test and no source.

Acceptance criteria:

1. Measure on this machine with nothing else running: `PYTEST_ADDOPTS='--dist=loadfile' uv run pytest
   --durations=0 -p no:cacheprovider`, once. Record the total, the setup/call/teardown split, and the time per
   file and per test directory. State the worker count used.
2. In the report, list the 40 slowest tests and the files that take the most total time, each with: what
   behaviour it protects (one sentence, from the test's name and body), why it is slow (real engine start,
   real tmux, waits, sleeps, repeated expensive fixture, parametrization multiplying a slow case), and a
   verdict: `keep as is`, `make faster` (say how: shared fixture, narrower setup, fake clock instead of a wait,
   fewer parametrized cases), `merge with <test>` (same scenario with another assertion), `cover at a lower
   level` (a unit or in-process test could protect the same rule), `duplicate of <test>`, or `not needed` (it
   protects no stated guarantee of `docs/design.md`).
3. For every verdict other than `keep as is`, name the design guarantee or rule that still protects the
   behaviour afterwards, or say that none does. Do not recommend removing a test of crash recovery, locking,
   integration or git safety without naming the remaining coverage.
4. Add a section "Quick wins" with the changes that would save the most time for the least risk, estimated in
   seconds of total CPU time, and a section "Shared costs" for fixtures or helpers whose setup is repeated (for
   example engine start, project creation, tmux server) with the measured cost of each.
5. Count the `sleep` calls under `tests/` and classify them (real waiting on a process, fake process payload,
   avoidable). `AGENTS.md` says never to sleep in tests; list the violations.
6. Documentation only: review the diff and run `git diff --check`; the full gate is not required. State in the
   report that no test changed.
