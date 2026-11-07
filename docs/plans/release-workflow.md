# GitHub Release on a version tag

Pushing a tag `v<version>` should publish the release: the wheel, the sdist and the changelog text.
PyPI is a later step (the owner must register a trusted publisher first) and is not part of this
tracker. Each task checks only its own entry when complete. See
[Releases and deployment](../design.md#releases-and-deployment).

## Tasks

### W1 — Release workflow

- [x] Add the release-notes script, the workflow and the CONTRIBUTING part.

Acceptance criteria:

1. `scripts/release_notes.py <version> [--changelog CHANGELOG.md]` prints the body of the
   `## [<version>]` section of the changelog without its heading, and exits non-zero with a message
   when the section is missing or empty. Standard library only.
2. `.github/workflows/release.yml` is triggered by `push: tags: ["v*"]`, grants
   `contents: write` to its one job only, and reuses the pinned checkout and uv actions and the uv
   version from `ci.yml`. Steps: fail unless the tag equals `v` plus the `pyproject.toml` version;
   `uv build`; write the notes with the script; `gh release create "$TAG" dist/* --verify-tag
   --title "$TAG" --notes-file <notes>`, with `--prerelease` while the major version is 0. The token
   is `${{ github.token }}`, passed as `GH_TOKEN` to that one step only.
3. `CONTRIBUTING.md` gets a "Releasing" part: bump the version and the changelog, commit, tag
   `v<version>`, `uv run poe deploy` on the host, then push the tag (the push is the owner's
   decision).
4. Table-driven test of the script: a section in the middle, the first and the last section, a
   missing version, an empty section, and a version that is a prefix of another (`0.1` and `0.1.0`).
5. The workflow parses as YAML and every `uses:` is pinned to a full commit SHA with the version in a
   comment. The workflow itself cannot run here; say so in the report.
6. The full gate passes.

### W2 — Install from a release

- [x] Change the README installation to install a release, not a checkout.

Acceptance criteria:

1. README "Requirements and installation": replace "Install from a Shikiru source checkout" with
   "Install a release": from the release wheel
   (`uv tool install https://github.com/ferrum-code-dev/shikiru/releases/download/v0.1.0/shikiru-0.1.0-py3-none-any.whl`)
   or from the tag (`uv tool install git+https://github.com/ferrum-code-dev/shikiru@v0.1.0`); the
   version in the URL is the one to change for a newer release. Keep a short "From a checkout" part
   pointing to `CONTRIBUTING.md`, and say that a long-running installation must be a release (see
   "Applying a release"; never `--editable`).
2. No other text in the README, `docs/` or `AGENTS.md` presents installing from a checkout as the
   normal way. Documentation-only change: review the diff and run `git diff --check`; the full gate
   is not required.

### W3 — The dashboard page-key test fails on GitHub Actions

- [x] Find why the test fails in Actions and make it deterministic.

The first GitHub Actions run (run 42392666568 of the CI workflow, on both `ubuntu-24.04` and
`macos-15`) passed everything except one test:
`tests/integration/test_dashboard_tab_stops.py::test_focused_readout_scrolls_with_page_keys_and_keeps_the_task_selection[size1]`
(the `_TINY` size, 20x8). After `pilot.press("pagedown")` on the focused usage panel, the sum of
`scroll_y` over `#focus-readouts` and `#focus-root` did not rise within the 30 s `wait_until`. The
same test passes locally, alone and in the full gate, including with `CI=true GITHUB_ACTIONS=true
TERM=xterm-256color`. The runs use `SHIKIRU_TEST_CPUS_PER_WORKER=1` (more workers on fewer cores),
`--dist=loadfile` and a pinned Textual. `gh run view 42392666568 --log-failed` has the log.

Acceptance criteria:

1. Find the cause. Try reproducing with the CI settings (`SHIKIRU_TEST_CPUS_PER_WORKER=1`, the pinned
   uv and Python from `ci.yml`, limited CPUs); state in the final report what you found, even if you
   could not reproduce it.
2. Make the test deterministic. If the cause is that the key is sent before the scrollable area has
   its final size, wait for that state (for example `max_scroll_y > 0`) before pressing; do not raise
   the timeout and do not skip or xfail the test. If the cause is in the product (a tiny terminal
   cannot scroll the readout), fix the product and keep the test.
3. The full gate passes locally. You cannot run Actions; say so in the report.

### W4 — The package smoke fails on Ubuntu in GitHub Actions

- [x] Make the smoke print why it fails, then fix the cause.

In run 42401933761 of the CI workflow (after the W3 fix) `macos-15` passed completely, but on
`ubuntu-24.04` the step "Build and exercise package distributions" (`scripts/release-readiness-smoke.sh`)
failed with only `Process completed with exit code 1` about 25 seconds after its last printed line,
`installed entry points, defaults, prompt, migration and artwork resources verified`. The script writes its
details to `build/release-readiness/*.log` and prints nothing of them, so the CI log does not say which check
failed. The gate step of the same job passed.

Acceptance criteria:

1. When any check in the script fails, the script prints the name of the failing stage and the last 60 lines of
   its log to stderr before it exits, and still exits non-zero. A passing run prints what it printed before.
2. Reproduce the failure where you can (the smoke runs the same on Linux and macOS except for the tools; if you
   cannot reproduce it, say so). Find the failing stage from the code that follows line 334 and fix the cause
   in the product or the script. Do not skip, loosen or time-box a check to make it pass.
3. If the cause depends on a Linux detail you cannot test here (for example a different `script` or `tmux`
   behaviour, a missing terminal, or a path length), make the smallest change that is right for both systems
   and say in the report what could still differ on the runner.
4. The full gate passes locally; the script still passes on this machine.

### W5 — The wizard stage of the package smoke fails on the Ubuntu runner

- [x] Find why the Leader and Implementer ladders are empty on the runner and make the smoke wait for the real state.

After W4 the Ubuntu job of run 42406618134 prints the cause location. The smoke fails in the stage
`normal-size interactive wizard`: `click_wizard_button 'Next' 'SHIKIRU · Trackers'` times out after 20 s with
`WaitTimeoutError` in `tests/support/release_wait.py` (`click_button` → `wait_for_pane`). The captured pane shows the
wizard still on `Agents and ladders`, with

    Leader                 (empty)
    Implementer · Base     (empty)
    Implementer · Low/Normal/High  inherits Base · (empty)
    Helper                 fake:smoke
    Edit Leader ... Leader: enter at least one ladder entry

so Next is refused by validation. The three `run_waiter ladder ...` calls before it (`Leader`/`fake:smoke`,
`Implementer · Base`/`fake:strong`, `Helper`/`fake:smoke`) all returned success, yet only Helper kept its value.
`macos-15` passes. The same script passes on this machine and in a clean `ubuntu:24.04` container built to match the
CI job (tmux 3.5a from source, git 2.56, uv 0.12.5, Python 3.14), also limited to one CPU. The CI run uses
x86-64; the container here is arm64.

Acceptance criteria:

1. Read `set_ladder` (and the `ladder` waiter) in `tests/support/release_wait.py` and the wizard's ladder editor.
   Explain in the report why a call can succeed while the ladder ends up empty (for example it accepts text that is
   still only typed in the field and not yet added, or an Enter/BTab sequence that lands on another widget when the
   screen is not settled).
2. Make each ladder step check the state that matters: after setting a ladder, the pane must show that scope with
   its value in the ladder summary, and the next step must not start before that is visible. Do not add sleeps, do
   not raise the timeouts, and do not skip or loosen a check.
3. If a product bug is behind it (for example the editor drops an entry when Add is pressed during a refresh), fix
   the product and add a test that fails without the fix.
4. The full gate passes and the smoke still passes here. You cannot run Actions; say so in the report, and say what
   could still differ on the runner. A Docker image that mirrors the CI job may exist on this host
   (`docker run --rm --cpus=1 -v <repo>:/src:ro shikiru-ubuntu-smoke ...`); use it if it helps and Docker is
   reachable, otherwise skip it.

### W6 — An engine-lock test fails on the macOS runner

- [x] Find why `test_stop_returns_only_after_the_engine_released_its_lock` fails on `macos-15` and make it deterministic.

Run 42412443881 of the CI workflow, commit `0b0ac3db`. `ubuntu-24.04` passed everything (gate and package
smoke, 11 min). On `macos-15` the first attempt passed the gate and then failed in the package smoke with
`No space left on device` (a runner disk problem; the failure text could not even be printed). The re-run
failed the gate with one test:

    FAILED tests/integration/test_engine_signals.py::test_stop_returns_only_after_the_engine_released_its_lock
    AssertionError: ✕ engine did not acquire its lock after start
    1 failed, 2517 passed, 15 skipped in 1324s

The macOS runner has 3 cores and runs 3 workers (`SHIKIRU_TEST_CPUS_PER_WORKER=1`); the gate takes 22 minutes
there against 12 on Ubuntu, so waits that are fine on a fast machine can run out. The test passes here.

Acceptance criteria:

1. Read the test and the helper that waits for the engine lock. Report what is waited for, with what timeout, and
   what a slow start can do to it (the engine process starting later than the wait allows, the lock file created
   after the process is up, or a wait that starts before the start command returns).
2. Make the wait follow the real condition (the engine reports it holds the lock, or the lock is held when tried)
   with the same kind of deadline the other engine tests use. Do not add sleeps, do not only raise a timeout, and
   do not skip or loosen the assertion that `stop` returns only after the lock is released.
3. Look for the same pattern in the other engine and process tests (`git grep` for the helper) and fix any that
   wait the same way, or list them in the report.
4. The full gate passes here. You cannot run Actions; say so in the report.

### W7 — The release waits for a green CI on the tagged commit

- [ ] Add the CI wait to the Release workflow.

Today pushing a tag publishes at once, even when CI has not finished or has failed on that commit. A release
that cannot be unpublished (PyPI later) must only come from a commit CI has passed.

Acceptance criteria:

1. `scripts/wait_for_ci.py` (standard library only): given the commit SHA, it polls
   `gh run list --workflow ci.yml --commit <sha> --json status,conclusion` every `POLL_SECONDS` (30) and decides
   with a pure function. A completed run with conclusion `success` passes. A completed run with another
   conclusion and no running or queued run fails, naming the conclusion. No run at all after
   `START_WAIT_SECONDS` (300) fails with "CI did not start for this commit". Otherwise keep waiting until
   `TOTAL_WAIT_SECONDS` (3600; this repository's CI takes 15 to 25 minutes) and then fail. The runner is
   injected, so tests call neither `gh` nor sleep.
2. `.github/workflows/release.yml`: a first job `ci-green` with `permissions: contents: read, actions: read`,
   a full-history checkout, a step that fails unless the tagged commit is an ancestor of `origin/main`
   (`git merge-base --is-ancestor`), then the script with `GH_TOKEN: ${{ github.token }}` for that step only.
   The existing release job gets `needs: ci-green` and keeps its own narrow permissions. The job's
   `timeout-minutes` covers `TOTAL_WAIT_SECONDS` plus a margin.
3. `CONTRIBUTING.md`, "Releasing": push `main`, tag, push the tag; the release waits for CI, and a failed CI is
   fixed on `main` and re-run (re-run the failed CI, then re-run the Release workflow).
4. Table-driven test of the decision function: success; failure with nothing running; failure while another run
   is queued; no run before and after `START_WAIT_SECONDS`; a run in progress; the total wait exceeded; several
   runs for one commit (a success wins over an earlier failure).
5. The workflow parses as YAML, every `uses:` is pinned to a full SHA with its version in a comment, and only the
   CI-wait step and the release step see a token. The workflow itself cannot run here; say so in the report.
6. The full gate passes.
