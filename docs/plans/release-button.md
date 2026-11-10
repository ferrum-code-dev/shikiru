# Releasing with one button

A release is a run of the Release workflow on `main`: it picks the newest green commit, proposes the next
version from the commits, waits for the owner's approval on `pypi`, publishes, and creates the tag with the
GitHub release. The target behavior is in [Releases and deployment](../design.md#releases-and-deployment).
Replaces "push a tag" (`release-simplification.md`, S2/S3). Each task checks only its own entry when complete.

## Tasks

### B1 — The notes name the commit and install the exact version

- [x] `scripts/release_notes.py` works before the tag exists.

Acceptance criteria:

1. A new option `--commit <sha>` (default: the tag itself, so existing use keeps working). The commits listed
   are `previous..<commit>`; the compare link still reads `previous...tag`.
2. The Install section is `uv tool install shikiru-cli==<version>` (a named constant for the distribution
   name, no download URL, no wheel name).
3. The `chore: release` filter and its constant are removed (no such commits exist any more).
4. Update the existing tests; add one for `--commit` on a temporary git repository and one for the install
   line. Previous-tag selection still ignores anything that is not `v<major>.<minor>.<patch>`.
5. The gate passes.

### B2 — Propose the next version

- [ ] `scripts/next_version.py` (stdlib only, reusing the parsing in `release_notes.py`; the scripts run
  from the `scripts/` folder, so `import release_notes` works).

Acceptance criteria:

1. `python3 scripts/next_version.py --commit <sha> [--version <x.y.z>]` prints the version (no `v`) and exits
   0. The last release is the highest `v<major>.<minor>.<patch>` tag that is an ancestor of `<sha>`; with no
   tag the base is `0.0.0`.
2. Releasable commits are those since the last release, without merges, whose subject is `feat`, `fix` or
   `perf`, or that are breaking (`!` or a `BREAKING CHANGE:` line, as `release_notes.py` already parses).
   With none, print `nothing to release` to stderr and exit 2.
3. Rule table: below 1.0, breaking raises the minor version (patch reset) and anything else raises the patch
   version; from 1.0, breaking raises the major, `feat` the minor, `fix` or `perf` the patch; the highest
   applicable bump wins.
4. `--version` replaces the proposal. It must be `<major>.<minor>.<patch>`, higher than the last release, and
   no tag of that name may exist; otherwise print the reason to stderr and exit 1. With `--version`, the
   releasable-commit check is skipped (the owner decided).
5. A table-driven test of the rule (including no tag, only docs/test commits, breaking below and from 1.0)
   and one test on a temporary git repository for the CLI and its exit codes.
6. `pyproject.toml` lint/type exceptions for standalone scripts cover the new file the way they cover
   `release_notes.py`. The gate passes.

### B3 — The Release workflow starts from a button

- [ ] Replace the tag trigger of `.github/workflows/release.yml`; update docs.

Acceptance criteria:

1. `on: workflow_dispatch` only, with an optional string input `version`; `concurrency: release` with
   `cancel-in-progress: false`; top-level `permissions: {}`. The file name and the `pypi` environment stay
   (PyPI trusted publishing is bound to them).
2. Job `prepare` (`contents: read`, `actions: read`): fails unless the run is on `main`; finds the newest
   successful `ci.yml` run on `main` with event `push` and takes its head SHA (`gh run list --workflow ci.yml
   --branch main --event push --status success --limit 1`); fails if there is none or the SHA is not an
   ancestor of `origin/main`; runs `scripts/next_version.py --commit <sha> [--version <input>]`; writes the
   version and the SHA to the job summary and to the job outputs. Checkouts use `fetch-depth: 0`.
3. Job `build` (needs `prepare`): checks out the SHA, runs `uv build` with `SETUPTOOLS_SCM_PRETEND_VERSION`
   set to the version, checks the wheel is `dist/shikiru_cli-<version>-py3-none-any.whl`, uploads `dist`.
   A test (offline, in a temporary clone like `test_version_from_tag.py`) shows `uv build --wheel` with that
   variable and no tag yields the wheel named with the version.
4. Job `pypi`: unchanged (environment `pypi`, `id-token: write`, pinned publish action).
5. Job `github` (`contents: write`; needs `prepare` and `pypi`): writes the notes with `scripts/release_notes.py
   v<version> --commit <sha>` and runs `gh release create v<version> dist/* --target <sha> --title
   v<version> --notes-file ...`, with `--prerelease` below 1.0. The tag is created by this command, so there
   is no `--verify-tag`.
6. Third-party actions stay pinned to commit SHAs. `tests/unit/test_release_workflow.py` and
   `tests/unit/test_workflow_pins.py` are updated; the workflow test checks the trigger, the permissions
   and that the job order is prepare, build, pypi, github.
7. `CONTRIBUTING.md` "Releasing" describes the button (Actions, Release, Run workflow, optional version,
   approve `pypi`), that a failed run leaves no tag, and keeps the one-time setup paragraph. In `README.md`
   remove the sentence "Before the first PyPI release, install the wheel ..." and, if the Releases page is
   mentioned as the place to download a wheel, say the package manager is the way to install.
8. The gate passes. Nothing is pushed or released by the task.
