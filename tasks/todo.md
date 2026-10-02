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

Delivery order: task navigation and contextual control, attention and evidence views, then responsive behaviour, export, documentation and release verification. Task 75's shell is shipped; later tasks remain planned until their acceptance criteria pass.

- [x] Task 75: Workspace design alignment and shared visual shell
- [x] Task 76: Selectable task browser and search
- [x] Task 77: Task detail and navigation to existing windows
- [x] Task 78: Contextual actions and searchable palette
- [x] Task 79: Durable command feedback and ownership banners
- [ ] Task 80: Home overview and attention composition
- [ ] Task 81: Escalation resolution with structured evidence
- [ ] Task 82: Blocker navigation and task families
- [ ] Task 83: Completion history and task evidence timeline
- [ ] Task 84: Honest usage and secondary diagnostics
- [ ] Task 85: Responsive layouts and accessible refresh behaviour
- [ ] Task 86: Markdown and JSON evidence export
- [ ] Task 87: Operator documentation and workflow walkthroughs
- [ ] Task 88: Workspace release verification and evidence
