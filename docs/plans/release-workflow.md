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
