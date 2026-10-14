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

Target: the operator-reviewed [Focus Steel HTML](../docs/design/references/focus-steel-approved.html); scope and acceptance criteria: [plan.md](plan.md#phase-11-real-project-tui-corrections). Tasks 105–112 are the accepted first delivery in the [Phase 11 release record](../docs/release-readiness-2026-10-07-phase11.md). The subsequent review adds Tasks 113–121, including Task 113a, under the [follow-up specification](../docs/tui-feedback-followup.md); implement sequentially with GPT-6-Luna xhigh and root review, preserving prior historical evidence.

- [x] Task 105: Prevent dashboard refresh callbacks from crashing during screen teardown
- [x] Task 106: Align dashboard identity, checkout, summaries and engine status with the reference
- [x] Task 107: Show multiple recent tasks and make selection the single task-detail route
- [x] Task 108: Keep action and detail popups over the dashboard and remove redundant controls
- [x] Task 109: Preserve tmux focus and correct task panes, home naming and chrome
- [x] Task 110: Split host, current Shikiru instance and agent resource measurements
- [x] Task 111: Make unknown billing modes understandable and preserve declared subscription facts
- [x] Task 112: Verify every reported issue and record native reference alignment
- [x] Task 113: Keep live agent motion responsive and animate active task status
- [x] Task 113a: Update the HTML reference for the second review
- [x] Task 114: Correct tmux title contrast and agent labels while preserving the clock
- [x] Task 115: Keep popup underlays stationary and align frames and close controls
- [x] Task 116: Make Recent fixed and scrollable with contiguous task rows
- [x] Task 117: Align the resource strip and composed Project Diagnostics popup
- [x] Task 118: Remove escalation identity duplication and make Enter resolve truthful
- [x] Task 119: Resolve rebase conflicts through bounded turns and classify continuation failures
- [x] Task 120: Move clear durable command feedback into the footer
- [x] Task 121: Verify the second review end to end and reconcile Phase 11 evidence (accepted by root 2026-10-08)

Third-review scope and evidence: [third real-project review](../docs/tui-feedback-third-review.md). Continue sequential GPT-6-Luna xhigh implementation and root review; preserve earlier acceptance and independently open gates.

- [x] Task 122: Prevent narrow popup inputs from crashing the dashboard
- [x] Task 123: Retain reliable tool outcomes without changing turn recovery (accepted by root 2026-10-09)
- [x] Task 124: Revise the HTML reference for the third review (accepted by root 2026-10-09)
- [x] Task 125: Style the complete project tmux chrome (accepted by root 2026-10-09)
- [x] Task 126: Unify popup styling and actual keyboard controls (accepted by root 2026-10-09)
- [x] Task 127: Keep popup frames stable through content changes
- [x] Task 128: Verify the third review and reconcile Phase 11 evidence (accepted by root 2026-10-09)

Fourth-review scope and evidence: [fourth real-project review](../docs/tui-feedback-fourth-review.md). Execute sequentially; the TUI is the source and the HTML reference follows it (Task 139). Preserve earlier acceptance and independently open gates 59/74h/74p.

- [x] Task 129: Popups open with useful focus and share the Tasks-list scrollbar colours
- [x] Task 130: Make Tab traverse only visible, fully highlighted dashboard stops
- [x] Task 131: Escalation bar follows the selected task and reads clearly
- [x] Task 132: Align the token usage and resource bars with the operator templates
- [x] Task 133: Stabilise task-list geometry and typography
- [x] Task 134: Show Recent as a bottom-aligned three-task window with Later/Earlier
- [x] Task 135: Right-align the latest command in the actions bar
- [x] Task 136: Colour agent context by usage level
- [x] Task 137: Preserve agent pane titles across session restart
- [x] Task 138: Document that takeover is not restored after respawn
- [x] Task 139: Revise the HTML reference to match the TUI
- [x] Task 140: Verify the fourth review and reconcile Phase 11 evidence ([record](../docs/release-readiness-2026-10-11-phase11-fourth-review.md))
- [x] Task 141: Count short-lived agent children and show light CPU honestly

Fifth-review scope and evidence: [fifth real-project review](../docs/tui-feedback-fifth-review.md). Execute sequentially; the TUI is the source and the HTML reference follows it (Task 147). Preserve earlier acceptance and independently open gates 59/74h/74p.

- [x] Task 142: Restore the `No decision needed` wording for a task without an escalation
- [x] Task 143: Make Up and Down inert while the escalations bar is focused
- [x] Task 144: One-column task-detail scrollbar
- [x] Task 145: Animate every in-progress status like Implementing
- [x] Task 146: Restore Implementer pane titles after restart (closed: no longer reproduced, no change made)
- [x] Task 147: Verify the fifth review and reconcile Phase 11 evidence ([record](../docs/release-readiness-2026-10-11-phase11-fifth-review.md))

Sixth-review scope and evidence: [sixth real-project review](../docs/tui-feedback-sixth-review.md). The operator approved the panel layout (2026-10-12).

- [x] Task 148: Colour subscription-limit gauges at 50 and 90 percent
- [x] Task 149: Project usage panel title and layout
- [x] Task 150: Verify the sixth review and reconcile Phase 11 evidence

Seventh-review scope and evidence: [seventh real-project review](../docs/tui-feedback-seventh-review.md). The operator approved the panel layout (2026-10-12).

- [x] Task 151: Docker sampler with numeric model, project scope, top five and problems
- [x] Task 152: Diagnostics section primitives and the Docker section
- [x] Task 153: Disk section
- [x] Task 154: Project diagnostics panel restructure and timer refresh
- [x] Task 155: Verify the seventh review and reconcile Phase 11 evidence

Eighth-review scope and evidence: [eighth real-project review](../docs/tui-feedback-eighth-review.md).

- [x] Task 156: Remove screenshot-capture side effects and task-numbered test names
- [x] Task 157: Fold and simplify `test_dashboard_app.py`
- [x] Task 158: Fold the rest of the dashboard-family integration tests
- [x] Task 159: Trim unit tables and fold tmux/workspace/config tests
- [x] Task 160: Stale only after a failure; age hint only when stale
- [x] Task 161: One refresh moment for the bar and the popup; header keeps ticking
- [x] Task 162: No leading rule or blank above Resources; `‼` marker for low disk space
- [x] Task 163: Disk `.shikiru` size
- [x] Task 164: Sentence-case titles everywhere
- [x] Task 165: Verify the eighth review
- [x] Task 166: Reconcile the HTML reference, design docs and release record

Ninth-review scope and evidence: [ninth real-project review](../docs/tui-feedback-ninth-review.md).

- [x] Task 167: `Tasks` heading and bold `SHIKIRU`
  - Proof: native before/after SVG exports (plain and `NO_COLOR`) show bold steel-blue `SHIKIRU` and the `Tasks` count with a lower-case filter label, and the assertion-backed anchors and pinned strings match them. The gate is green (1586 passed, 14 skipped, 91.17 % coverage); collected cases 1605 → 1607.
- [x] Task 168: Plugin reinstall hint
  - Proof: a `python -I` call with a missing name printed the not-found message before the change; after it both agent and widget messages end with the reinstall hint, and `--reinstall` and `--editable` are confirmed in `uv tool install --help`. The registry test asserts the hint constant, and collected cases stay at 1607. Gate green on the default workers (1586 passed, 14 skipped, 91.16 % coverage).
- [x] Task 169: One UI wait budget
- [x] Task 170: `pause(); assert` becomes `wait_until`
- [x] Task 171: Harden the CPU and take-over tests
- [x] Task 172: Command history pages with the arrow keys
  - Proof: before the change, Right from the initial focus left the page at 1 (the probe's wait for page 2 timed out after 30 s) and the buttons read `p previous`/`n next`. After it, native Textual dumps at 100×34, 48×20 and 20×8, with and without `NO_COLOR`, show `← newer`/`→ older` with the arrow span `#83bddd`, bare arrows and `1 · 20/52` when compact, and the Help line `History: ← newer · → older`; Left/Right page from the initial and collapsed focus and stay inert while the Select is expanded. The p/n assertions were converted in place (collected cases 1607 before and after), the 8 history and keyboard tests pass, and the gate is green (1586 passed, 14 skipped, 91.16 % coverage).
- [x] Task 173: Coloured rows, no raw result
- [x] Task 174: Verify the ninth review, reconcile reference, docs and AGENTS.md
- [x] Task 175: Reveal the whole evidence-export result in the palette

Tenth-review scope and evidence: [tenth review, escalation resolution panel](../docs/tui-feedback-tenth-review.md).

- [x] Task 176: Resolution view model
- [x] Task 177: Escalation resolution screen
- [x] Task 178: Remove the old resolve form
- [x] Task 179: Verify the tenth review, reconcile the reference and docs

Eleventh-review scope and evidence: [eleventh review, task details](../docs/tui-feedback-eleventh-review.md).

- [x] Task 180: Briefing data
- [x] Task 181: Briefing render and actions row
- [x] Task 182: Details list and section views
- [x] Task 183: Arrow lists for events, agent activity and relationships
- [x] Task 184: Remove the old detail code and tests
- [x] Task 185: Verify the eleventh review, reconcile the reference and docs

Twelfth-review scope and evidence: [twelfth review, keys, escalation answers and inline details](../docs/tui-feedback-twelfth-review.md).

- [x] Task 186: Up/Down select tasks from any bar
  - Proof: Reproduced on the unchanged code: a native DashboardApp pilot (Tab to each stop, Down, Up) left the selection on T-2 with focus on the escalation bar, usage, resources or the footer while the task list moved to T-3, and the new table failed for those four stops. After the change every stop moves T-2 to T-3 and back, the list moves one step, and the Left/Right, search-input (arrows then Escape), expanded-Select-in-task-detail and related-task RowList arrow tests stay green (a mutation check shows the RowList exclusion is guarded by existing tests). Text areas exist only in modals the route never handles, so no table row covers them. Gate `uv run poe check` is green: 1574 passed, 14 skipped, 91.21% coverage; collected cases 1589 to 1595.
- [x] Task 187: Remove the `u` and `e` task keys
  - Proof: Reproduced on the unchanged code: a task-window pilot pressing `u` then `e` pushed a TaskDetailScreen, failing the new `test_task_usage_and_evidence_keys_open_nothing_and_send_nothing`; after removing both key branches, `TASK_USAGE_KEY`, `_EVIDENCE_KEY` and the help line, that test passes and a native text dump of the window and of Home shows an unchanged screen stack and identity, while `v` still opens the details list. Usage and Events are now opened through `choose_details_entry` and `choose_inspector_details_entry` in `tests/support/dashboard.py` (the only place that presses `v`), and the Ctrl+P Evidence and Usage routes are unchanged. The operator docs (README, 06-operator, tui-focus, tui-workspace, tui-parity) no longer name the keys; the HTML reference's footer hint (`e evidence · u usage`) is left to Task 196 by the rules. Gate `uv run poe check` is green: 1575 passed, 14 skipped, 91.17% coverage; collected cases 1595 to 1596.
- [x] Task 188: Resolution panel highlight and keyboard-reachable evidence
  - Proof: Reproduced on the unchanged code: Down past the last choice never selected or expanded `What was tried` (the new table failed), and a compositor dump showed `▌` on the selected label and consequence rows. Now the glyph and bold are gone (text columns identical selected vs not, background `$selected-surface` as in the Tasks list, which has no extra NO_COLOR cue beyond that grey background, also seen in a NO_COLOR dump) and the tried row is the last arrow row (Enter toggles, never sends; hint shows Enter expand/collapse), checked at 100x40, 48 and 40 columns in colour and NO_COLOR. Gate `uv run poe check` is green: 1576 passed, 14 skipped, 91.18% coverage; collected cases 1596 to 1597.
- [x] Task 189: What was tried digest
- [x] Task 190: Alternatives that need Operator input
- [x] Task 191: Wrap the agent's current output in Now
- [x] Task 192: Independent timelines for Events and Agent activity
- [x] Task 193: Inline folds replace the details list popup
- [x] Task 194: Route entry points to folds and retire the chooser
- [x] Task 195: Cyan key names everywhere
- [x] Task 196: Verify the twelfth review, reconcile the reference and docs

Thirteenth-review scope and evidence: [thirteenth review](../docs/tui-feedback-thirteenth-review.md).

- [x] Task 197: Selection without a vertical bar; Recent pager style
  - Proof: Reproduced on the unchanged code: the focused fold header and the selected RowList row painted `▌` in steel blue, the Tasks list in a non-focus placement drew a `┌─┐` outline in `#99c8ff`, and the Views menu and session picker showed Textual's solid block cursor (`#83bddd` and `#0178d4`, bold). Now fold headers and rows keep only the `$selected-surface` background, the bold name and the two-cell lead; the outline and the `selection-outline` token are gone; Views and the session picker use `$selected-surface` (the picker installs the shikiru theme); and the Recent pager reads `↑ later` / `↓ earlier`, grey with cyan arrows. A lean test checks the background and no `▌` on a focused fold header and a selected row; native dumps before and after, in colour and `NO_COLOR`, show these changes, and 06 §6.1 and tui-parity are updated. Gate `uv run poe check` is green: 1612 passed, 14 skipped, 91.45% coverage; collected cases 1632 to 1633.
- [x] Task 198: Edit area under the option, new keys
- [x] Task 199: Details section of the resolution panel
  - Proof: Details is its own Tab stop (Enter/Space/`t` toggle, Up returns) with the virtual arrow row removed, and its rule is a separate widget so the focus background no longer covers it. The digest is produced as typed rows, persisted as `Label: text` lines and rebuilt by `parse_attempt_lines` (old packets parse to one Reason row). Checked natively at 100x40 and 48 columns with colour cells asserted in the integration test; `poe check` green.
- [x] Task 200: Terminal-state correctness
  - Proof: Reproduced on the unchanged code: a Cancelled task with its step kept read `○ Consulting · Leader`, `? Triage unknown ? Implement unknown`, a blocker `Waiting on dependency` and a Home row `Consulting · waiting on dependency`. `project_workflow` now gives Done stages done or skipped and Cancelled stages done, `✕ stopped` and skipped; state line, PR (`merged`), Home row and strip show only the terminal status with `stopped during <step>`, and the store clears `wait_reason` on Done/Cancelled. Tables cover truncated and complete Done, Cancelled with kept and cleared step and the live task keeping `?`; native dumps checked. Gate `uv run poe check` green.
- [x] Task 201: Briefing verbs
- [ ] Task 202: Results data
- [ ] Task 203: Briefing as Progress, Result so far and Problems
- [ ] Task 204: Results and Decisions folds
- [ ] Task 205: Task description, Dependencies, Workspace, Settings; no project-wide folds
- [ ] Task 206: Events and Agent activity
- [ ] Task 207: Usage
- [ ] Task 208: Verify the thirteenth review, reconcile the reference and docs
