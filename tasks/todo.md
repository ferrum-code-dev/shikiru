# Shikiru Progress

Tick a task when all its ACs in [plan.md](plan.md) are ticked.


## Phase 0 — Foundation
- [x] Task 1: Scaffold, quality gate, test support

## Phase 1 — Core model
- [x] Task 2: Configuration
- [x] Task 3: Domain rules
- [x] Task 4: Report models and `shikiru report`
- [x] Task 5: State store

## Phase 2 — Substrate
- [x] Task 6: tmux
- [x] Task 7: Workspace
- [x] Task 8: Agent contract, registry, SDK
- [x] Task 9: Fake agent and contract test kit
- [x] Task 10: Prompts and Helper
- [x] Task 11: UI core and dashboard shell

## Phase 3 — Pipeline on the fake agent
- [x] Task 12: Session manager and monitor
- [x] Task 13: Turn runner
- [x] Task 14: Task flow
- [x] Task 15: Recovery
- [x] Task 16: Integration (local merge)
- [x] Task 17: Engine daemon, scheduler, handlers
- [x] Task 18: Operator services, CLI and MCP

## Phase 4 — Real agents
- [x] Task 19: Claude Code plugin
- [x] Task 20: Codex plugin
- [x] Task 21: Doctor and first real E2E

## Phase 4b — Headless agents
- [x] Task 31: Usage rules and price-table removal
- [x] Task 38: Code health from the 2026-09-25 review
- [x] Task 32: Headless contract, SDK, contract kit and fake agent
- [x] Task 33: Claude Code headless plugin
- [x] Task 34: Codex headless plugin
- [x] Task 35: Engine on process-per-turn; screen machinery removed
- [x] Task 36: Pane views, take over / hand back, queued `say`
- [x] Task 37: Probe and real E2E on headless turns
- [x] Task 39: Proactive context compaction within a turn
- [x] Task 40: Probe every configured rung

## Phase 5 — Resilience and operator completeness
- [x] Task 22: Engine restart recovery
- [x] Task 23: Pull request integration
- [x] Task 24: Parallel, escalation and intervention E2E

## Phase 6 — Operator surfaces and onboarding
- [x] Task 25: Remaining widgets and renderables
- [x] Task 26: Palette
- [x] Task 27: Motion
- [x] Task 28: Sessions list and attach
- [x] Task 29: Init wizard
- [x] Task 30: Release readiness

## Phase 7 — Review remediation (2026-09-29)
- [x] Task 41: Quality gate independent of the terminal (F17)
- [x] Task 42: Integration never discards uncommitted work (F01)
- [x] Task 43: Holds block every execution path (F04)
- [x] Task 44: One owner for a task workspace during takeover (F03)
- [x] Task 45: Stale escalation actions cannot resurrect work (F05)
- [x] Task 46: Turn processes own their descendants (F02)
- [x] Task 47: Dependents start from the merged base in PR mode (F06)
- [x] Task 48: Decomposition delivers tracker changes to children (F07)
- [x] Task 49: Transitions persist triage results and keep concurrent overrides (F09, F10)
- [x] Task 50: Dependency graph validation (F11)
- [x] Task 51: SQLite path is opened exactly (F16)
- [x] Task 52: A slow notification hook cannot block the engine loop (F08)
- [x] Task 53: Idempotent command replay (F12)
- [x] Task 54: Interrupted turns keep operator messages (F13)
- [x] Task 55: Escalated state always has an actionable escalation (F14)
- [x] Task 56: Codex context window belongs to the model (F15)
- [x] Task 57: TaskFlow responsibilities (F18)
- [x] Task 58: Low-severity items (F19, F20, F21, F23, F22)
- [ ] Task 59: Re-verify and regenerate release evidence (reopened by Task 73: no native model-change E2E scenario; clean-install smoke verified on macOS only)

## Phase 7b — Independent-review remediation (2026-09-30)
- [x] Task 60: The engine survives tick faults and tmux loss
- [x] Task 61: Every running task has a live flow
- [x] Task 62: Escalation packet failures never kill or wedge the engine
- [x] Task 63: `supersede` and cancellation never erase unexplained work
- [x] Task 64: Operator rung pins survive every transition
- [x] Task 65: Holds cover every remaining execution path
- [x] Task 66: Command replay and crash windows
- [x] Task 67: PR mode is live and current
- [x] Task 68: Compatibility and leftovers
- [x] Task 69: Process ownership residuals
- [x] Task 70: Trust boundary, permissions and platform support
- [x] Task 71: Comment rule and lint claims (F22 decision, F20)
- [x] Task 72: TaskFlow residuals (F18)
- [x] Task 73: Test gaps and evidence quality
- [x] Task 74a: Bounded E2E scenarios
- [x] Task 74: Final verification and release evidence (Phase 7b)

## Phase 7c — Final-review remediation (2026-10-01)
- [x] Task 74b: Codex sandbox covers the worktree git directory on every turn
- [x] Task 74c: Reconcile and the tick fail per item, not per engine
- [x] Task 74d: Notifications cannot silence an escalation
- [x] Task 74e: PR mode network work leaves the command loop and tolerates blips
- [x] Task 74f: Operator-held tasks and task ids survive tmux loss and reuse
- [x] Task 74g: Lint, docs, smoke and evidence hygiene
- [x] Task 74i: Test-value audit against AGENTS.md
- [ ] Task 74h: Phase 7c verification and release evidence (last AC closes with Task 74p)

## Phase 7d — Release-blocker remediation (2026-10-01)
- [x] Task 74j: Integration requires the task branch
- [x] Task 74k: Graceful shutdown on SIGTERM and SIGINT
- [x] Task 74l: Notifications survive a restart; command handler errors are retried
- [x] Task 74m: Regression power of the Codex sandbox and reconcile fixes
- [x] Task 74n: Documentation and evidence corrections
- [x] Task 74o: Test-value audit of the older suite
- [x] Task 74q: A turn completion never overwrites operator-owned task state (F10)
- [x] Task 74r: The no-literals rule is enforced like the no-comments rule (F22)
- [x] Task 74s: Engine start, tmux and process hygiene (stability)
- [x] Task 74t: Bounded resources and configurable timeouts (stability)
- [x] Task 74u: Soak and chaos run (stability evidence)
- [ ] Task 74p: Phase 7d verification, re-review and release evidence

## Phase 8 — Operator Workspace

Target: [Operator Workspace](../docs/tui-workspace.md); acceptance criteria: [plan.md](plan.md#phase-8--operator-workspace). Implement after Phase 7b.

Delivery order: task navigation and contextual control, attention and evidence views, then responsive behaviour, export, documentation and release verification. Tasks 75–88 are delivered and verified for Phase 8 on macOS. The clean-install/navigation smoke, normal and `TERM=dumb` full gates, and a fresh E1–E6 invocation passed; GNU/Linux was not run and remains unverified. The older Task 59, 74h and 74p review gates remain independently open. See [the release record](../docs/release-readiness-2026-10-03-phase8.md).

- [x] Task 75: Workspace design alignment and shared visual shell
- [x] Task 76: Selectable task browser and search
- [x] Task 77: Task detail and navigation to existing windows
- [x] Task 78: Contextual actions and searchable palette
- [x] Task 79: Durable command feedback and ownership banners
- [x] Task 80: Home overview and attention composition
- [x] Task 81: Escalation resolution with structured evidence
- [x] Task 82: Blocker navigation and task families
- [x] Task 83: Completion history and task evidence timeline
- [x] Task 84: Honest usage and secondary diagnostics
- [x] Task 85: Responsive layouts and accessible refresh behaviour
- [x] Task 86: Markdown and JSON evidence export
- [x] Task 87: Operator documentation and workflow walkthroughs
- [x] Task 88: Workspace release verification and evidence

## Phase 9 — Compact Operator TUI

Target: [Compact Operator TUI](../docs/tui-focus.md); acceptance criteria: [plan.md](plan.md#phase-9-compact-operator-tui). Convert the Phase 8 presentation to the approved Focus layout and Flight deck palette, preserving its useful behavior and evidence. Execute Tasks 89–96 sequentially with architect review between tasks.

- [x] Task 89: Compact TUI design alignment
- [x] Task 90: Explicit billing metadata and historical persistence
- [x] Task 91: Compact metrics and agent-history projections
- [x] Task 92: Shared compact visual language
- [x] Task 93: Focus Home dashboard and compact project readouts
- [x] Task 94: Focus task detail and contextual operator control
- [x] Task 95: Responsive compatibility and obsolete presentation cleanup
- [x] Task 96: Compact TUI walkthroughs and release verification

## Phase 10 — Focus terminal parity correction

Target: [Focus terminal parity correction](../docs/tui-parity.md); acceptance criteria: [plan.md](plan.md#phase-10-focus-terminal-parity-correction). Phase 9 remains accepted complete; Phase 10 corrects the native composition and routes to the approved reference. Tasks 97–102 are accepted complete, with Root sign-off at `580368c`; see the [Phase 10 release record](../docs/release-readiness-2026-10-05-phase10.md). See also the [Task 98 native Home review](../docs/task98-visual-review-2026-10-04.md), [Task 99 native inspector review](../docs/task99-visual-review-2026-10-04.md), [Task 100 native interaction review](../docs/task100-visual-review-2026-10-05.md), and [Task 101 measured native review](../docs/task101-visual-review-2026-10-05.md).

- [x] Task 97: Approved reference parity audit and target specification
- [x] Task 98: Reference-aligned Home frame and task list
- [x] Task 99: Compact shared task inspector and factual stage/signal rows
- [x] Task 100: Contextual actions, direct attention routes and forms
- [x] Task 101: Responsive and compatibility parity review
- [x] Task 102: Connected correction walkthroughs and final evidence

## Real-project TUI feedback — reference review

- [x] Task 103: Revise the Focus Steel HTML reference for operator review
- [x] Task 104: Correct the HTML reference after operator review

## Phase 11 — Real-project TUI corrections

Target: the operator-reviewed [Focus Steel HTML](../docs/design/references/focus-steel-approved.html); scope and acceptance criteria: [plan.md](plan.md#phase-11-real-project-tui-corrections). Implement Tasks 105–112 in order. Template review is complete; native fixes are pending. Preserve the historical Phase 10 evidence.

- [x] Task 105: Prevent dashboard refresh callbacks from crashing during screen teardown
- [x] Task 106: Align dashboard identity, checkout, summaries and engine status with the reference
- [x] Task 107: Show multiple recent tasks and make selection the single task-detail route
- [x] Task 108: Keep action and detail popups over the dashboard and remove redundant controls
- [x] Task 109: Preserve tmux focus and correct task panes, home naming and chrome
- [x] Task 110: Split host, current Shikiru instance and agent resource measurements
- [ ] Task 111: Make unknown billing modes understandable and preserve declared subscription facts
- [ ] Task 112: Verify every reported issue and record native reference alignment
