# Automated releases and one-command update

Release notes come from commit subjects, not from `CHANGELOG.md`. Releasing is one command, and
updating a host is one command that needs no checkout. Each task checks only its own entry when
complete. The target behavior is in
[Releases and deployment](../design.md#releases-and-deployment). PyPI is a later step and is not part
of this tracker.

## Tasks

### N1 — Release notes from commits

- [x] Generate the notes from the commit log, publish `SHA256SUMS`, and delete `CHANGELOG.md`.

Acceptance criteria:

1. `scripts/release_notes.py <tag> [--repo <owner/name>]` (standard library only) prints the notes
   of the release `<tag>` to standard output. The previous tag is the highest `v<major>.<minor>.<patch>`
   tag below `<tag>` (compare the numbers, not the text); with none, all commits up to `<tag>` count.
   Commits come from `git log <previous>..<tag> --no-merges`; subjects starting with
   `chore: release` are skipped. `--repo` defaults to the `GITHUB_REPOSITORY` environment variable.
2. A subject `type(scope)!: description` is parsed. Sections, in this order, omitting empty ones:
   `### Breaking changes` (a `!` after the type, or a `BREAKING CHANGE:` line in the body),
   `### Features` (`feat`), `### Fixes` (`fix`), `### Other changes` (every other type, and subjects
   that are not conventional, as written). Each line is
   `* [<7-char sha>](<commit url>): <subject>`. A breaking commit appears only under Breaking changes.
3. After the sections: `**Full Changelog**: [<previous>...<tag>](<compare url>)` when there is a previous
   tag, and an `### Install` section with the one-line install command for this tag's wheel
   (`uv tool install https://github.com/<repo>/releases/download/<tag>/shikiru-<version>-py3-none-any.whl`).
4. `.github/workflows/release.yml`: the publish job checks out the full history
   (`fetch-depth: 0`) and passes `$TAG` and `--repo "$GITHUB_REPOSITORY"`. It writes
   `SHA256SUMS` (`cd dist && sha256sum * > SHA256SUMS`) and attaches it with the wheel and the sdist.
   Nothing else in the workflow changes; its pinned actions stay.
5. Delete `CHANGELOG.md` and its link in `pyproject.toml` (`Changelog` URL becomes the releases page).
   Remove every other mention of the changelog (README, CONTRIBUTING). CONTRIBUTING "Releasing"
   describes the new flow in two lines and refers to the design: run `uv run poe release <bump>`; the
   workflow publishes after CI; a failed CI is fixed on `main` and the failed workflow runs are re-run.
6. Table-driven tests of the script on a temporary git repository built in the test: sections and their
   order, breaking by `!` and by the body line, a non-conventional subject, skipped release commits,
   first release (no previous tag), a previous tag chosen by number (`v0.1.10` above `v0.1.9`), and the
   links. The workflow still parses as YAML and every `uses:` is pinned to a full SHA with its version.
7. The workflow cannot run here; say so in the report. The gate passes.

### N2 — One-command release

- [x] Add `scripts/release.py` and `poe release`.

Acceptance criteria:

1. `uv run poe release patch|minor|major|<x.y.z>` (`scripts/release.py`, standard library only, commands
   through an injected runner like `scripts/deploy.py`) refuses with a message naming the reason
   unless: the branch is `main`; the work tree is clean; `git fetch` shows HEAD equal to
   `origin/main`; the new version is greater than the current one and the tag does not exist.
2. It rewrites `version` in `pyproject.toml`, the version of the project's own entry in `uv.lock`
   (only that line; never run `uv lock`), and every whole-token occurrence of the old version in
   `README.md`. It commits `chore: release <version>` (with the Co-Authored-By trailer the repository
   uses), runs `git push origin main`, `git tag v<version>`, `git push origin v<version>`, and prints
   the address of the Release workflow run list. It does not wait for the workflow.
3. `--dry-run` runs the checks and prints every file change and command, and changes nothing.
4. Table-driven tests: bump arithmetic (`0.1.9` patch/minor/major, explicit, lower or equal version
   refused), each refusal, the three file rewrites on sample text (including a README with the version
   in a URL and in prose), and the command sequence with a fake runner.
5. CONTRIBUTING "Releasing" mentions `--dry-run`. `poe release` is in `pyproject.toml`. The gate
   passes.

### N3 — `shikiru update --wheel` replaces `poe deploy`

- [x] Move the deploy logic into the package as `shikiru update`, with `--wheel` and `--rollback`.

Acceptance criteria:

1. New package `src/shikiru/update/` holds what `scripts/deploy.py` does after "build the wheel": the
   release store and `current`, install, version check, engine restarts, rollback and pruning, with the
   same behavior and messages, and the same injected runner for tests. The check that the work tree is
   clean and HEAD is a tag, the gate and the smoke are not part of it.
2. `shikiru update --wheel <path>` stores the wheel, installs it and does steps 3 to 5 of the design.
   `shikiru update --rollback` does the rollback. Without options the command does not exist yet (N4
   adds the download); it prints that `--wheel` is required. Exit codes: 0 success, 1 failure.
3. After the install step the process imports no further `shikiru` modules: every later step runs
   through the new executable as a subprocess. A test checks that the module set after the install
   step equals the one before (import everything the update path needs first).
4. `scripts/deploy.py`, its tests, `poe deploy` and `poe rollback` are deleted. The tests move to the
   package, keeping every case.
5. `scripts/release-readiness-smoke.sh` additionally runs `shikiru update --wheel <the built wheel>` in
   its isolated installation (isolated `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`, `XDG_DATA_HOME`) and checks
   the exit code, the version and `current`. This is the check that a process can replace itself.
6. README "Deploy" part and CONTRIBUTING are rewritten for `shikiru update` (README: how to update and
   roll back; the troubleshooting paragraph that said `poe deploy` says `shikiru update`).
7. The gate passes.

### N4 — `shikiru update` downloads the newest release

- [x] Add the GitHub source, the checksum check and `--check`.

Acceptance criteria:

1. A `ReleaseSource` port with one adapter that uses `urllib` (HTTPS only, a timeout and a size
   limit as named settings, redirects allowed within `https`). Constants name the repository
   (`ferrum-code-dev/shikiru`) and the API address. The adapter lists `GET /repos/<repo>/releases`.
2. A pure function picks the newest release: tags matching `v<major>.<minor>.<patch>` only, drafts
   skipped, pre-releases included, compared by number. A pure function reads the wheel's expected
   SHA-256 from `SHA256SUMS` text. A wheel whose SHA-256 differs, a release without a wheel or without
   `SHA256SUMS`, and malformed lines are errors that name the cause. Nothing is installed in any of
   those cases.
3. `shikiru update` with no options downloads the wheel and `SHA256SUMS`, checks the checksum,
   hands the wheel to the N3 path and reports the old and new version. If the installed version is
   the newest it says so and exits 0. `--check` prints the installed and the newest version and
   whether an update exists; it changes nothing and exits 0.
4. Tests use a fake `ReleaseSource`; none touches the network. Table-driven: newest by number
   (`v0.1.10` over `v0.1.9`), drafts, pre-releases, no usable release, checksum match and mismatch,
   missing assets, already newest, `--check`.
5. README "Install" says that an installed Shikiru updates itself with `shikiru update`. The gate
   passes.

### I1 — A blocked integration says why

- [x] The integration-blocked escalation names the blocker.

Acceptance criteria:

1. When the wait for a dirty checkout or a failing Git step ends in `IntegrationBlocked`
   (`src/shikiru/engine/integration.py`, `merge`), the error message names the last blocker, for
   example `the project checkout has uncommitted changes on <paths>` or the failing Git command's
   message, instead of the default `integration remained blocked`. The message is what the
   escalation question shows.
2. A test with a fake clock covers a dirty checkout that stays dirty (the question names the
   paths) and a Git failure (the question names it).
3. Nothing is deleted or cleaned automatically. The gate passes.
