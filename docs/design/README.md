# Shikiru Design

| Doc | Contents |
|---|---|
| [01-overview.md](01-overview.md) | Goals, roles, principles, decision log, lessons from prior art |
| [02-architecture.md](02-architecture.md) | Processes, components, project structure, plugin contracts |
| [03-workflow.md](03-workflow.md) | Task lifecycle, turn protocol, review loop, scheduling, integration, resilience, escalation, take over and hand back |
| [04-agents.md](04-agents.md) | Agent plugin contract (headless turns on resumed sessions), SDK and contract test kit, Claude Code and Codex plugins, Helper |
| [05-data.md](05-data.md) | Configuration reference, state DB, run files, report schemas |
| [06-operator.md](06-operator.md) | CLI, MCP, dashboard, tmux session, attach, notifications, style |
| [07-engineering.md](07-engineering.md) | Stack, conventions, quality gates, testing and E2E scenarios |
| [Operator Workspace](../tui-workspace.md) | Phase 8 TUI specification and implementation status; Tasks 75–88 verified on macOS, including fresh E1–E6 and clean-install/navigation evidence. GNU/Linux remains unverified; historical review gates 59, 74h and 74p remain open ([2026-10-03 record](../release-readiness-2026-10-03-phase8.md)) |
| [Compact Operator TUI](../tui-focus.md) | Shipped Phase 9 Focus layout, palette, billing and compact readouts; exact real-project visual/UX alignment remains an explicit follow-up in the [2026-10-04 release evidence](../release-readiness-2026-10-04-phase9.md). See also the [approved standalone mockup](references/focus-steel-approved.html) and [736-pixel view](references/focus-steel-approved-736.png) |

Read the docs in order. Each doc is normative. When a later doc refines an earlier one, it names the section it refines.
