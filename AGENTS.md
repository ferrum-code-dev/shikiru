# Shikiru — Agent Conventions

- **Read first**: `docs/design/` (normative) and your task in `tasks/plan.md`. Do one task at a time; tick its ACs in `plan.md` and the task in `todo.md`.
- **Stack**: Python 3.14, uv. Run `uv run poe check` before every commit; it must be green.
- **Design**: SOLID, KISS, YAGNI. One responsibility per class; pure functions in `domain`; constructor injection; wiring only in `bootstrap.py`. Build nothing beyond the task.
- **Boundaries** (lint-enforced): SQL only in `store`; subprocess only via `process.SubprocessRunner`; time only via `clock.Clock`; agent specifics only in `plugins/agents/*`; the engine imports only `agents.contracts`.
- **Code**: no comments, clear names; docstrings only on public contracts and their implementations. No literals: tunables go in `defaults.toml`, protocol values in named constants. No silent fallbacks, no bare `except`.
- **Tests**: behaviour over implementation. Unit tests only for `domain` rules, models and parsers; integration tests for the rest (real SQLite, git, tmux via `tmux_server`, fake agent). Wait with `wait_until`, time with `FakeClock`, never sleep. No mocks of our own classes, no snapshot spam. Coverage ≥ 80%.
- **Commits**: conventional commits, one logical change each; refactorings in separate `refactor:` commits.
