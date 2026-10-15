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
| [Compact Operator TUI](../tui-focus.md) | Shipped Phase 9 Focus layout, palette, billing and compact readouts; its [2026-10-04 release record](../release-readiness-2026-10-04-phase9.md) preserves the then-open visual/UX gap and the accepted Phase 9 evidence |
| [Focus terminal parity correction](../tui-parity.md) | Historical Phase 10 composition and interaction, implemented in accepted Tasks 97–102 with Root sign-off at `580368c`; Phase 11 Tasks 105–112 are the accepted first delivery against the [current HTML reference](references/focus-steel-approved.html), and Tasks 113, 113a and 114–128 are accepted follow-up work. The [2026-10-09 third-review record](../release-readiness-2026-10-09-phase11-third-review.md) contains current verification; the [second-review follow-up](../release-readiness-2026-10-08-phase11-followup.md), [first-delivery record](../release-readiness-2026-10-07-phase11.md), [Phase 10 record](../release-readiness-2026-10-05-phase10.md) and [736-pixel view](references/focus-steel-approved-736.png) remain historical |

Read the docs in order. Each doc is normative. When a later doc refines an earlier one, it names the section it refines.

The second real-project review is specified in [Phase 11 follow-up](../tui-feedback-followup.md): accepted Tasks 113, 113a and 114–121 refine the first delivery with motion, task-list, tmux, popup, diagnostics, attention, integration and feedback behavior. Root accepted Task 121 verification on 2026-10-08. The [2026-10-08 release record](../release-readiness-2026-10-08-phase11-followup.md) preserves that review's evidence and limits.

The [third real-project review](../tui-feedback-third-review.md) covers accepted Tasks 122–128 for safe narrow inputs, reliable tool-outcome evidence, the revised HTML, full tmux chrome and coherent stable popups. The [2026-10-09 release record](../release-readiness-2026-10-09-phase11-third-review.md) attributes each walkthrough, native capture and gate to its actual source and boundaries.

The [fourth real-project review](../tui-feedback-fourth-review.md) covers Tasks 129–140 (focus traversal, escalation sync, usage/resource templates, task-list geometry and a paged Recent, popup focus, context colours, preserved pane titles and takeover documentation). Tasks 129–139 are delivered, the HTML reference was revised to match the rendered TUI in Task 139, and Task 140's verification and remaining limits are in the [2026-10-11 release record](../release-readiness-2026-10-11-phase11-fourth-review.md). The TUI is the source.

The [fifth real-project review](../tui-feedback-fifth-review.md) (Tasks 142–147) restores the `No decision needed` wording, makes Up/Down inert on the focused escalations bar (superseded: the [twelfth review](../tui-feedback-twelfth-review.md) selects tasks from every bar), narrows the task-detail scrollbar to one column and animates every in-progress status. Tasks 142–145 are delivered and the HTML reference follows them; Task 146 (Implementer pane titles) is closed because it no longer reproduced. See the [2026-10-11 record](../release-readiness-2026-10-11-phase11-fifth-review.md).

The [sixth real-project review](../tui-feedback-sixth-review.md) (Tasks 148–150) colours subscription-limit gauges sage, amber and dusty rose at 50 and 90 percent, titles the popup `Project usage` and restructures it into Totals, By agent and Limits; the HTML reference follows. See the [2026-10-12 record](../release-readiness-2026-10-12-phase11-sixth-review.md).

The [seventh real-project review](../tui-feedback-seventh-review.md) (Tasks 151–155, delivered) restructures Project diagnostics into Resources and Engine plus default-on Docker, Disk and Faults sections that refresh like the Resources bar; the HTML reference follows the TUI. See the [2026-10-12 record](../release-readiness-2026-10-12-phase11-seventh-review.md).

The [eighth real-project review](../tui-feedback-eighth-review.md) (Tasks 156–166, delivered) trims the test suite, drops the age hints and refresh drift from Project diagnostics, measures `.shikiru` on Disk, marks low free space with `‼`, aligns titles to sentence case and reconciles the HTML reference. See the [2026-10-13 record](../release-readiness-2026-10-13-phase11-eighth-review.md).

The [ninth real-project review](../tui-feedback-ninth-review.md) (Tasks 167–174, delivered) makes `SHIKIRU` bold and the heading `Tasks`, hints at reinstalling when a plugin entry point is missing, steadies the load-sensitive tests, pages command history with the arrow keys and colours its rows without the raw result. See the [2026-10-13 record](../release-readiness-2026-10-13-phase11-ninth-review.md).

The [tenth real-project review](../tui-feedback-tenth-review.md) (Tasks 176–179, delivered) resolves every escalation on one choice-list screen (Decision, the Leader's alternatives, Retry, Accept, Cancel task, a collapsed `What was tried`) opened for one concrete escalation, replacing the palette's resolve form. See the [2026-10-13 record](../release-readiness-2026-10-13-phase11-tenth-review.md).

The [eleventh real-project review](../tui-feedback-eleventh-review.md) (Tasks 180–185, delivered) replaces the task detail summary with a briefing (rows only with data, a state-dependent actions row) and the task Views menu with an arrow-driven details list (replaced by inline folds in the twelfth review). See the [2026-10-14 record](../release-readiness-2026-10-14-phase11-eleventh-review.md).

The [twelfth real-project review](../tui-feedback-twelfth-review.md) (Tasks 186–196, delivered) lets Up/Down select tasks from any bar, removes the `u` and `e` keys, gives the resolution panel a background-only highlight, an arrow-reachable `What was tried` digest and `needs_input` alternatives, wraps `Now`, replaces the details list with nine inline folds and paints key names steel blue. See the [2026-10-14 record](../release-readiness-2026-10-14-phase11-twelfth-review.md).

The [thirteenth real-project review](../tui-feedback-thirteenth-review.md) (Tasks 197–208, delivered) lets the Operator answer with an edit box under the option, makes the briefing and nine folds readable and lightens the detail lists; its [record](../release-readiness-2026-10-15-phase11-thirteenth-review.md) lists the verification.

The [fourteenth real-project review](../tui-feedback-fourteenth-review.md) (Tasks 209–220, delivered) simplifies the keys (Enter and Esc on the answer box and the folds), derives stage states from events by kind, quiets the details area (one bottom hint line, shaded text blocks, a Decisions log) and colours the briefing; its [record](../release-readiness-2026-10-15-phase11-fourteenth-review.md) lists the verification.
