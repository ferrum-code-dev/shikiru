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
- [ ] Task 41: Quality gate independent of the terminal (F17)
- [ ] Task 42: Integration never discards uncommitted work (F01)
- [ ] Task 43: Holds block every execution path (F04)
- [ ] Task 44: One owner for a task workspace during takeover (F03)
- [ ] Task 45: Stale escalation actions cannot resurrect work (F05)
- [ ] Task 46: Turn processes own their descendants (F02)
- [ ] Task 47: Dependents start from the merged base in PR mode (F06)
- [ ] Task 48: Decomposition delivers tracker changes to children (F07)
- [ ] Task 49: Transitions persist triage results and keep concurrent overrides (F09, F10)
- [ ] Task 50: Dependency graph validation (F11)
- [ ] Task 51: SQLite path is opened exactly (F16)
- [ ] Task 52: A slow notification hook cannot block the engine loop (F08)
- [ ] Task 53: Idempotent command replay (F12)
- [ ] Task 54: Interrupted turns keep operator messages (F13)
- [ ] Task 55: Escalated state always has an actionable escalation (F14)
- [ ] Task 56: Codex context window belongs to the model (F15)
- [ ] Task 57: TaskFlow responsibilities (F18)
- [ ] Task 58: Low-severity items (F19, F20, F21, F23, F22)
- [ ] Task 59: Re-verify and regenerate release evidence
