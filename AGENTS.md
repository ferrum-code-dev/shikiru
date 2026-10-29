# Shikiru — Agent Conventions

- **Read first**: `docs/design.md`.
- **Conventions**: SOLID, KISS, YAGNI. One responsibility per class; pure functions in `domain`; constructor injection; no comments, clear names; docstrings only on public contracts and their implementations. No literals: tunables go in `defaults.toml`, protocol values in named constants. No silent fallbacks, no bare `except`. Build nothing beyond the task.
- **Tests**: behaviour over implementation. Unit tests only for `domain` rules, models and parsers; integration tests for the rest (real SQLite, git, tmux via `tmux_server`, fake agent). Wait with `wait_until`, time with `FakeClock`, never sleep. No mocks of our own classes, no snapshot spam. Coverage ≥ 80%.
- **Test value**: test domain rules and invariants, not the framework or data shape — no tests for pydantic features (frozen, extra, defaults), enum or constant listings, trivial wiring, or message/description wording. One table per rule. Integration tests assert observable behaviour at boundaries; coverage beyond the gate is not a goal.
- **Verification**: in a fresh worktree run `uv sync --locked`, then `PYTEST_ADDOPTS='-n 2 --dist=loadfile' uv run poe check` before every commit; it must be green.
- **Commits**: conventional commits, one logical change each; refactorings in separate commits.

