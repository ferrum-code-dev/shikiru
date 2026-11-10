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

- [x] `scripts/next_version.py` (stdlib only, reusing the parsing in `release_notes.py`; the scripts run
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

- [x] Replace the tag trigger of `.github/workflows/release.yml`; update docs.

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

### B4 — git-cliff writes the notes and the version

- [x] Replace `scripts/release_notes.py` and `scripts/next_version.py` with git-cliff.

Use an existing, dedicated tool instead of our own scripts. Prototyped with git-cliff 2.14.2 on a clone: it
proposes `v0.3.0` for a breaking change and `v0.2.1` for anything else below 1.0, and `v1.1.0` for a `feat`
from 1.0, which is the rule in the design. With no commit since the last tag it prints "There is nothing to
bump" and the last tag. Its notes matched our format. Start from this `cliff.toml` (validated; keep the
behavior, adjust only what the checks below need):

```toml
[changelog]
body = """
{% for group, commits in commits | group_by(attribute="group") %}
### {{ group | striptags | trim | upper_first }}
{% for commit in commits %}
* [{{ commit.id | truncate(length=7, end="") }}](https://github.com/ferrum-code-dev/shikiru/commit/{{ commit.id }}): {{ commit.raw_message | split(pat="\n") | first }}
{%- endfor %}
{% endfor %}
{% if previous.version %}**Full Changelog**: [{{ previous.version }}...{{ version }}](https://github.com/ferrum-code-dev/shikiru/compare/{{ previous.version }}...{{ version }})
{% endif %}
### Install

```sh
uv tool install shikiru-cli=={{ version | trim_start_matches(pat="v") }}
```
"""
trim = true

[git]
conventional_commits = true
filter_unconventional = false
tag_pattern = "^v[0-9]+\\.[0-9]+\\.[0-9]+$"
sort_commits = "newest"
commit_parsers = [
  { message = "^[a-z]+(\\([^)]*\\))?!:", group = "Breaking changes" },
  { body = "BREAKING CHANGE:", group = "Breaking changes" },
  { message = "^feat", group = "Features" },
  { message = "^fix", group = "Fixes" },
  { message = ".*", group = "Other changes" },
]

[bump]
features_always_bump_minor = false
breaking_always_bump_major = false
```

1. Add `cliff.toml` (above) at the repository root.
2. `.github/workflows/release.yml`:
   - `prepare`: after the step that finds the newest green commit, `git checkout --detach <sha>`, then run
     `orhun/git-cliff-action@a9a95522b26fe6403f7bb24031f21fb573d0f5ff # v4.9.1` with `version: v2.14.2` and
     `args: --bumped-version`; its `content` output is the proposed tag (strip the `v`). A non-empty
     `version` input replaces the proposal (no other validation: PyPI and `gh release create` reject a
     version that exists). The step fails with a clear message when the proposal equals the last release tag
     (nothing to release). Delete the step that runs `next_version.py` .
   - `github`: with the same action pinned the same way, `args: --unreleased --tag v<version> --strip all`
     on the checkout of the release commit; write its `content` output to the notes file instead of running
     `scripts/release_notes.py`.
   - Everything else (pre-release flag below 1.0, `--target`, trusted publishing, job order) stays.
3. Delete `scripts/release_notes.py`, `scripts/next_version.py`, their tests, their entries in `pyproject.toml`
   lint and type settings, and every reference to them (CONTRIBUTING.md, docs, workflow comments).
4. Update the workflow and pin tests; add a test that `cliff.toml` parses as TOML and keeps `tag_pattern`
   limited to final tags.

Acceptance criteria:

1. As above. The workflow stays valid: `actionlint` is not required, but the job graph and outputs are
   consistent and the existing workflow tests pass.
2. No custom release script remains. Tests never run git-cliff or the network; the owner verifies the notes
   and the proposal by running git-cliff on the repository, so report the exact commands for that.
3. The gate passes. Nothing is pushed or released by the task.

### B5 — A daily release marked Latest

- [x] Schedule the Release workflow.

Make the release run itself every day and mark it Latest. The target behavior is "Releasing is automatic" in
`docs/design.md`. Change `.github/workflows/release.yml` and the docs only.

1. Triggers: add `schedule` with `cron: "0 0 * * *"` to the existing `workflow_dispatch`.
2. Nothing to release is not a failure. When the proposal equals the last release tag (the case that fails
   today with "nothing to release"), `prepare` writes "Nothing to release since <tag>" to the job summary,
   sets an output `release` to `false` and succeeds; otherwise `release` is `true`. `build`, `pypi` and
   `github` run only when `release == 'true'` (`if:` on each job, so a skipped run is green). A given
   `version` input still means `release == true`.
3. Latest: `gh release create` gets `--latest` and loses `--prerelease` in all cases (GitHub does not let a
   pre-release be Latest). Remove the code that chose the pre-release flag by major version.
4. Docs: `CONTRIBUTING.md` "Releasing" says the release runs every day at 00:00 UTC without approval, can be
   started on demand, ends quietly when nothing changed, and how to recover from a failed run (fix `main`;
   the next run or a manual run starts clean). Remove every sentence about approving `pypi`. Keep the
   one-time setup paragraph, adding that the `pypi` environment has no reviewer and allows only the `main`
   branch (configured by the owner in the repository settings, not in code). Mention in `README.md` only
   what it already says about releases if it contradicts this (pre-release, approval).

Acceptance criteria:
* The workflow test checks the triggers (schedule and manual), the permissions, the job order and that
  `build`, `pypi` and `github` depend on the `release` output; the pin test still passes.
* `gh release create` carries `--latest` and no `--prerelease` (a test on the workflow text).
* No custom script is added. Nothing is pushed or released by the task.
* The gate passes.

### B6 — A resilient daily release

- [x] Filter by commit type, guard, smoke test, retry-safe publish.

Make the daily release resilient and stop it from releasing for documentation alone. See "Releasing is
automatic" and "Release notes come from commits" in `docs/design.md`. Change `cliff.toml`,
`.github/workflows/release.yml` and the docs only.

1. `cliff.toml`: in `commit_parsers`, before the catch-all `Other changes` parser, add
   `{ message = "^(docs|test|chore|ci|build|style|refactor)", skip = true }`. The two breaking-change parsers
   stay first, so a breaking `docs!` is still listed and still releases. Prototyped: docs/chore/test-only
   commits propose the current version ("nothing to bump"), `docs` plus `fix` proposes the next patch with
   only the fix in the notes.
2. `prepare`: the newest green commit must be a descendant of the last release tag
   (`git merge-base --is-ancestor <tag> <sha>`); otherwise treat it as nothing to release.
3. `build`: after the build and the file name check, install the wheel in a clean environment and check its
   version: `uv run --isolated --no-project --with dist/<wheel> shikiru --version`, whose output must contain
   the release version; fail otherwise.
4. `pypi`: set `skip-existing: true` on the publish action. `github`: stop downloading `dist` and attaching it
   (`gh release create` gets no files); it keeps the notes, `--target`, `--latest`.
5. Docs: `CONTRIBUTING.md` "Releasing" says what is left out of releases (the list above), that a failed run is
   finished by the next run (or a manual run), and that the release has no attached files.

Acceptance criteria:
* The workflow test checks the `skip-existing` setting, that `github` does not download `dist`, the
  smoke step before upload, and the ancestor check; the pin test still passes.
* A test parses `cliff.toml` and checks the skip parser comes after both breaking-change parsers and before
  the catch-all.
* No custom script is added. Nothing is pushed or released by the task.
* The gate passes.

### B7 — The package smoke is CI's, so it lives with CI

- [ ] Move `scripts/release-readiness-smoke.sh` to `.github/scripts/release-readiness-smoke.sh`.

Only CI runs the smoke; nobody needs it by hand. Acceptance criteria:

1. `git mv` the script. Fix the way it finds the repository root (it is one level deeper now). Remove the
   empty `scripts/` folder and any lint or type setting that names the old path.
2. `ci.yml` runs the new path.
3. `CONTRIBUTING.md`: remove the command that runs the smoke by hand and keep the paragraph that says what
   the package smoke covers, saying CI runs it. Old plans under `docs/plans/` keep their history; do not
   edit them.
4. The gate passes. Nothing is pushed or released by the task.
