# Releases the usual way

A release is a tag on a green `main`; the package manager installs and updates; Shikiru has no update
command. The target behavior is in [Releases and deployment](../design.md#releases-and-deployment).
This replaces the bump commit, `poe release`, the CI-wait script and `shikiru update` (all added
earlier in `release-automation.md`, N2 to N4). The release notes script (N1) stays. Each task checks only
its own entry when complete.

## Tasks

### S1 — The version comes from the git tag

- [x] Use `hatch-vcs`; no file holds the version.

Acceptance criteria:

1. `pyproject.toml`: `dynamic = ["version"]`, `hatch-vcs` in `[build-system].requires`,
   `[tool.hatch.version] source = "vcs"` with a `fallback-version` of `0.0.0`, and the generated version
   file (if any) excluded from git. The checkout's own version is whatever `hatch-vcs` yields
   (`0.1.4.dev3+gabcdef` style); the project's entry in `uv.lock` has no pinned version if `uv lock`
   writes none. Run `uv lock` once and commit its result.
2. Builds that need the tag see the full history: `ci.yml` and `release.yml` checkouts use
   `fetch-depth: 0`. The sdist carries the version, so a wheel built from the sdist (no `.git`) has the
   same version; `scripts/release-readiness-smoke.sh` builds the wheel from the sdist and checks
   `shikiru --version` equals the sdist's version.
3. The version reader the code uses (`--version`, the Shikiru version in `doctor` and the compatibility
   checks of shikiri-yaku read `shikiru --version`) accepts a development version: it takes the release
   segment (`0.1.4.dev3+gabcdef` is `0.1.4`). A table test covers `0.1.4`, `0.1.4.dev3+gabc`, `1.0.0rc1`.
4. A test clones this repository into a temporary directory (`git clone --local`), tags the clone
   `v9.9.9` and runs `uv build --wheel` there; the wheel is named `shikiru-9.9.9-...`. It is skipped when
   `git` or `uv` is missing and never uses the network.
5. Remove the version from every place that held it other than the tag: README install lines (made
   version-free in S2), `scripts/release.py` (deleted in S2). Nothing may still read `[project].version`
   (grep and fix; `scripts/release_notes.py` takes the tag).
6. The gate passes.

### S2 — Remove `update`, the release scripts and the pins

- [x] Delete what packaging already does.

Acceptance criteria:

1. Delete `src/shikiru/update/`, its registration in the CLI, its tests, the `update` part of
   `scripts/release-readiness-smoke.sh`, `scripts/release.py` and its tests, `scripts/wait_for_ci.py` and
   its tests, and the `release` poe task. `shikiru restart` stays unchanged.
2. README "Installation": `uv tool install shikiru`; "Updating": `uv tool upgrade shikiru`, then
   `shikiru restart` in each project with a running engine; "Going back": `uv tool install
   shikiru==<version>`. Until the first PyPI release add one sentence: "Before the first PyPI release,
   install the wheel from the latest release on the Releases page." No version number appears in the
   README. The shikiri-yaku section keeps its link.
3. CONTRIBUTING "Releasing": push the tag on a commit of `main` with green CI
   (`git tag v<version> && git push origin v<version>`); the workflow does the rest. A failed run is fixed
   on `main` and a new tag is cut (a tag is not moved).
4. Search the repository for `shikiru update`, `poe deploy`, `poe rollback`, `SHA256SUMS`,
   `release store`, `current` (the release-store file) and remove every remaining mention outside
   `docs/plans/`.
5. The gate passes.

### S3 — The release workflow: check CI once, publish to PyPI

- [x] One CI check, trusted publishing, the GitHub release.

Acceptance criteria:

1. `.github/workflows/release.yml` (trigger: tag `v*`): job `check` (`contents: read`, `actions: read`) fails
   unless the tagged commit is an ancestor of `origin/main` (full-history checkout) and
   `gh run list --workflow ci.yml --commit "$GITHUB_SHA" --json conclusion` contains a `success`
   (inline shell, no script, no waiting; the failure message says "CI has not passed on this commit; wait
   for it and push the tag again").
2. Job `build` (needs `check`): `uv build` and upload `dist/` as an artifact.
3. Job `pypi` (needs `build`): environment `pypi`, `permissions: id-token: write` for this job only,
   downloads the artifact and publishes with the pinned `pypa/gh-action-pypi-publish`
   (trusted publishing, no secret). It is first in line after the build because it cannot be undone.
4. Job `github` (needs `pypi`, `contents: write`): downloads the artifact, writes the notes with
   `scripts/release_notes.py`, creates the release with the wheel and sdist (no `SHA256SUMS`), `--prerelease`
   below 1.0 as today.
5. Pinned actions with the version in a comment; the workflow parses as YAML; tokens only where listed.
   It cannot run here; say so in the report.
6. A paragraph at the end of CONTRIBUTING "Releasing" lists the one-time setup the owner does on
   pypi.org (pending publisher: project `shikiru`, owner `ferrum-code-dev`, repository `shikiru`, workflow
   `release.yml`, environment `pypi`) and on GitHub (environment `pypi`).
7. The gate passes.

### S4 — Back up the state database before a migration

- [x] Same protection shikiri-yaku has for its journal.

Acceptance criteria:

1. In `src/shikiru/store/database.py`, when `_migrate` finds a database older than the newest migration
   (and the database is not new), it first copies the database with SQLite's online backup API
   (`sqlite3.Connection.backup`) to `<state dir>/backups/state-schema<N>.db` (N the schema it is leaving),
   keeps the three newest, and then migrates. A failed backup stops the migration with an error naming
   the cause; nothing is migrated. A new database is not backed up.
2. The "newer than this Shikiru knows" error also names the newest backup file when one exists and says to
   copy it back to restore.
3. Tests with real SQLite in a temporary directory: a migration creates the backup with the old contents; the
   fourth backup removes the oldest; no backup for a fresh database; a failing backup (read-only
   directory) leaves the database unmigrated; the too-new error names the backup.
4. `docs/design.md` already describes this; the README "Recovery" or state part gets two lines on where the
   backups are. The gate passes.

### S5 — The distribution is named `shikiru-cli`

- [ ] PyPI does not allow the name `shikiru`; the distribution is `shikiru-cli`, the command and the import package stay `shikiru`.

Acceptance criteria:

1. `pyproject.toml`: `name = "shikiru-cli"`. `[project.scripts]` still defines `shikiru`, and the package
   under `src/shikiru` is unchanged. Run `uv lock` once and commit its result. Wheels and sdists are named
   `shikiru_cli-<version>...` and `shikiru_cli-<version>.tar.gz`.
2. Everything that names the distribution follows: README install and update lines
   (`uv tool install shikiru-cli`, `uv tool upgrade shikiru-cli`, `uv tool install shikiru-cli==<version>`), the
   project URLs, the install line and the wheel file name in `scripts/release_notes.py` and its tests, the release
   smoke script and CI steps that glob for the wheel, and `docs/design.md` (the "Installing and updating"
   paragraph). Anything that reads the installed distribution's metadata by name uses the new name (search for
   `importlib.metadata` and `distribution(`).
3. README "Updating" gets a short note for existing installations: the tool changed its distribution name, so
   run `uv tool uninstall shikiru` and then `uv tool install shikiru-cli` once (both provide the executable
   `shikiru`, so they cannot be installed together), then `shikiru restart` in each project.
4. The commit subject is `build(package)!: ...` with a `BREAKING CHANGE:` line saying the same, so the
   release notes show it.
5. The gate passes, and `uv build` produces `shikiru_cli-*` files whose `shikiru --version` works when
   installed in an isolated environment (the release smoke script does this).
