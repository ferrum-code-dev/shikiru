# Changelog

All notable changes to this project are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.1.0] - 2026-11-07

### Added

- Coordinates Claude Code and Codex CLI agents through Operator, Leader, Implementer, Helper and
  Engine roles, with different agents and models per role.
- Turns a request into a task with its own Git branch and worktree, triages it into implement or
  plan routes, and integrates accepted changes locally or through a GitHub pull request.
- Runs independent tasks in parallel up to a configured slot limit, respecting dependencies, pauses,
  CPU and RAM gates and provider quota waits.
- Raises escalations with context and applicable answers when a decision needs you, while other
  eligible tasks continue.
- Keeps per-task reports, Git facts, agent sessions and a timeline in SQLite; refuses databases with
  a newer schema than the code knows.
- Provides a persistent engine in tmux, a terminal dashboard, and a `shikiru` CLI with `init`,
  `start`, `stop`, `restart`, `submit`, `status`, `ls`, `attach`, `doctor` and `mcp` commands.
- Serves the project's operator tools over stdio to MCP clients.
- Deploys a tagged release with `poe deploy` and rolls back to the release that `current` names with
  `poe rollback`.
