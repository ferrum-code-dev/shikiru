# Release and deployment

Shikiru is built with itself, so what runs the projects must be a tested release and not whatever the
checkout holds, and an upgrade that fails must return to the last good release. This document is the
tracker for the tasks below. Each task checks only its own entry when complete. The design is in
[Releases and deployment](../design.md#releases-and-deployment); read it first.

## Findings and decisions

- `uv tool install .` installs a snapshot of the working tree. The next deploy cannot go back,
  because nothing keeps the previous build. The release store keeps the wheels.
- `scripts/release-readiness-smoke.sh` already builds the wheel and sdist and exercises an isolated
  install. It becomes a step of the deploy, run before anything on the host changes.
- The database `_migrate` in `store/database.py` returns quietly when the stored schema is newer than
  the code. That must become a refusal, or a rollback can corrupt state.
- An engine keeps the code it was started with. `shikiru ls --json` lists every project with an
  engine (`project_dir`, `engine_state`, `running`), which is the list the deploy restarts.
- The Operator (`shikiri-yaku`) restarts its service itself; it does not restart engines. Its
  `stop_grace_min` of two minutes covers a restart that takes seconds.

## Tasks

### R1 — `shikiru restart` and a refusal of newer schemas

- [x] Add the restart command and make an unknown newer schema an error.

Acceptance criteria:

1. `shikiru restart` (global `--project` applies) stops the engine and starts it again, as `stop`
   then `start` do, and prints one line. With no engine running it starts one. It refuses with a
   non-zero exit and a message naming the running tasks while a task is running, unless `--force`.
   `--json` prints the result. It is listed in the README CLI table.
2. A database whose schema is newer than the highest migration is refused: the error names the stored
   and the known version, no write happens, and `doctor` reports it as a failed check. A table-driven
   test covers older, equal and newer.
3. Tests use the existing fake agent and private tmux fixtures; nothing touches the host.
4. The full gate passes.

### R2 — Deploy a tagged release, roll back on failure

- [x] Add `scripts/deploy.py`, the poe tasks, the README part and the human drill.

Acceptance criteria:

1. `scripts/deploy.py` (standard library only; run as `uv run python scripts/deploy.py`, with
   `--rollback`) does the steps of the design section in order. The logic sits in functions that take
   a command runner and the paths, so a test imports them and passes fakes.
2. `pyproject.toml` gets poe tasks `deploy = "python scripts/deploy.py"` and
   `rollback = "python scripts/deploy.py --rollback"`.
3. With fakes: a dirty tree, an untagged HEAD, a tag that differs from the version, a failing gate
   and a failing smoke each stop the script before anything is installed. A successful run installs
   the wheel, verifies the version, restarts only the engines that are up, leaves busy projects
   reported, and writes `current`. A failed verification or an engine that does not come up
   reinstalls the wheel named by `current`, restarts the engines it restarted, and exits non-zero;
   with no `current` it says so and exits non-zero.
4. Wheels beyond the last five are removed; `current` never is.
5. Nothing is installed or run on the host by the tests.
6. README: a section "Applying a release" (tag, `uv run poe deploy`, `uv run poe rollback`, busy
   projects, never `--editable`), and a human drill in the same place for a deploy that fails on
   purpose and rolls back.
7. The full gate passes.

### R3 — Files an open-source project needs

- [ ] Add the license, metadata, CONTRIBUTING, SECURITY, CHANGELOG and templates.

Acceptance criteria:

1. `LICENSE` is the MIT license, `Copyright (c) 2026 Ferrum Code`. `pyproject.toml` gets
   `license = "MIT"`, `license-files = ["LICENSE"]`, `authors`, `keywords`, `classifiers`
   (Python 3.14, alpha) and `[project.urls]` (Homepage, Source, Issues, Changelog) for
   `https://github.com/ferrum-code-dev/shikiru`.
2. `CONTRIBUTING.md` receives the README section "Validate a checkout and a real project" whole,
   with its subsections, and adds: open an issue before anything over a small fix, `AGENTS.md`
   rules apply to people as well as agents, conventional commits, the gate must be green, and CI runs
   the same gate. The README keeps one line pointing to it. No link or text anywhere points to the
   removed README section (check `AGENTS.md`, `docs/` and the CI file).
3. `SECURITY.md`: private vulnerability reporting through GitHub; in scope: agents run with the
   user's privileges (see Trust and implementation boundaries), the Operator MCP surface, state files
   and notification commands; supported versions: the latest release.
4. `CHANGELOG.md` in Keep a Changelog style with `Unreleased` and a `0.1.0` entry summarizing what
   Shikiru does, written from the README and not from the git log.
5. `.github/ISSUE_TEMPLATE/bug_report.yml`, `.github/ISSUE_TEMPLATE/feature_request.yml` and
   `.github/pull_request_template.md`, short; the PR template asks for the gate result.
6. `git grep -n '/Users/'` finds nothing in tracked files.
7. `uv build` into a temporary directory produces a wheel and sdist whose metadata shows the license,
   URLs and classifiers. Every relative link in `README.md`, `CONTRIBUTING.md` and `AGENTS.md`
   resolves. The full gate passes.
