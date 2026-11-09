# Contributing to Shikiru

Thank you for helping. Before you start:

- Open an issue before anything larger than a small fix, so the approach can be agreed first.
- Read [AGENTS.md](AGENTS.md) and [the design](docs/design.md). Its conventions apply to people as
  well as agents.
- Commit with [Conventional Commits](https://www.conventionalcommits.org/), one coherent change per
  commit.
- Before you open a pull request, make sure the quality gate is green. Its result goes in the pull
  request template.
- CI runs the same gate on Ubuntu and macOS.

## Validate a checkout and a real project

```sh
uv sync --locked
PYTEST_ADDOPTS='--dist=loadfile' uv run poe check
bash scripts/release-readiness-smoke.sh
```

The quality gate covers formatting, lint, boundaries, types, unused-code checks and behavior tests.
Real Claude/Codex adapter code is tested offline using recorded protocol output and controlled
subprocesses: invocation, parsing, resumption, compaction, usage and failures. Engine integration
uses the fake-agent plugin with real Git, SQLite and private tmux. Live provider contracts and
real-agent end-to-end scenarios are opt-in and excluded from normal CI.
The package smoke builds wheel/sdist and exercises an isolated installed package with a fake agent,
a disposable HOME/repository and private tmux server; it makes no paid provider calls. GitHub CI
syncs the locked development environment and runs both checks with one test worker per available CPU
and job timeouts. The isolated installation resolves the built package's declared runtime
dependencies.
Automated checks do not establish reliability for every provider or real project.

### Verify real agents

These checks use your authenticated agent CLIs and consume real provider usage. Run them locally
from the Shikiru checkout after `uv sync --locked`, with Git/tmux available and the agents you are
testing installed, authenticated and permitted to execute the scratch project's commands. Run
sequentially (`-n 0`); timeouts are not spending or RAM limits.

For a configured project's actual role ladders, start with `shikiru doctor --probe` in that project.
For automated adapter verification, choose model IDs supported by your account first:

- Claude contract tests accept `SHIKIRU_CLAUDE_LIVE_MODEL` and
  `SHIKIRU_CLAUDE_LIVE_CHANGE_MODEL` for the initial and model-change checks. Set both to supported
  models; the model-change check skips if no second model is configured.
- Codex contract tests currently use `_LIVE_MODEL`, `_LIVE_OTHER_MODEL` and `_EFFORT` in
  [their test module](tests/integration/test_codex_headless_contract.py). Review/update those test
  constants for your account; they are separate from project configuration and have no equivalent
  model environment overrides.

Enable live checks only for the invocation below. Include just the test file for each agent you
have configured (both are shown):

```sh
SHIKIRU_E2E=1 uv run pytest -n 0 --no-cov -k live \
  tests/integration/test_claude_headless_contract.py \
  tests/integration/test_codex_headless_contract.py
```

These exercise report submission, measured usage, session/model resumption, stop/resume, structured
Helper answers and context compaction. The compaction check deliberately grows context and can
consume substantially more usage than a basic report check. Review skips as well as failures.

For complete workflow verification, review `REAL_AGENT`, `REAL_MODEL`, `REAL_EFFORT` and
`REAL_ROLE_LADDERS` in [real_agent_config.py](tests/e2e/real_agent_config.py); these configure all
roles in disposable fixture repositories, independently of your `.shikiru/config.toml`. Start
with one completion/integration scenario:

```sh
SHIKIRU_E2E=1 uv run pytest -n 0 --no-cov \
  tests/e2e/test_real_agents.py::test_real_agents_e1_completes_and_integrates
```

Then run all six real-agent scenarios:

```sh
SHIKIRU_E2E=1 uv run pytest -n 0 --no-cov tests/e2e/test_real_agents.py
```

They cover completion/integration, next-turn Operator messages, planning with dependent parallel
tasks, MCP decision resolution, restart recovery and takeover/handback. Tests use private tmux
servers and disposable projects under `~/.cache/shikiru-e2e/`, with cleanup afterwards. Results go
to `build/e2e/`; failure diagnostics go to `build/e2e/diagnostics/`. The harness has per-attempt and
one-hour invocation time budgets and can retry an eligible failed scenario once, consuming
additional usage. Its parallel-work scenarios disable CPU/free-RAM dispatch gates; monitor your
host and account usage. See [budgets.py](tests/e2e/budgets.py) for the current limits. These paid
checks are excluded from normal CI and have not been run as part of this cleanup.

### Check your own project

For a provider-backed check, clone a committed project into a fresh disposable directory, initialize
Shikiru, review its ladders and submit one small, reviewable change. While active, detach/reattach,
exercise a task pause and, where useful, Take over/Hand back; stop/start the engine to inspect
recovery. Confirm the resulting diff, tests and Git history. Use a model and account you are willing
to spend usage on; `doctor --probe` is also a live check.

## Releasing

A release is the commit tagged `v<version>`; see [the design](docs/design.md#releases-and-deployment).
Push the tag on a commit of `main` whose CI has passed:

```sh
git tag v<version> && git push origin v<version>
```

The Release workflow does the rest. If a run fails, fix the cause on `main` and cut a new tag; a tag
is not moved.

The owner sets up publishing once. On pypi.org, add a pending publisher for the project `shikiru-cli`
with owner `ferrum-code-dev`, repository `shikiru`, workflow `release.yml` and environment `pypi`. On
GitHub, create the environment `pypi` in the repository settings. No PyPI token is stored in the
repository.
