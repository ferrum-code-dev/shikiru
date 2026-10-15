# Phase 11 fourteenth review: simpler keys, truthful stages and a quieter details area

Status: planned (Tasks 209–219). This specification refines the [thirteenth](tui-feedback-thirteenth-review.md) and [twelfth](tui-feedback-twelfth-review.md) reviews where it differs. **The TUI is the source of truth**; the [HTML reference](design/references/focus-steel-approved.html) follows the product (Task 219).

Analysis: four read-only static analyses (Sonnet 5.5 medium). Nothing was run; the implementing task reproduces first. The operator approved the layouts below on 2026-10-15.

## Requirements

### Escalation resolution panel
- On an editable row only **Enter** opens the edit area and **Esc** leaves it; the Left/Right bindings, their actions, the `Exited` arrow path and their hints go. Enter inside the area inserts a new line; Ctrl+S, Ctrl+Enter and Ctrl+J still send. Hints: list `↑↓ select · Enter edit`, area `Ctrl+S send · Enter new line · Esc back`.
- The edit area is hidden when edit mode ends (Esc, or moving to another row). Typed text of an alternative is discarded; the own-answer text stays as its draft, and while that draft is non-empty its area stays visible.
- `Details` turn lines read `step · Role · what happened` in plain words (step cyan, role white): `triage · Leader · triaged as implement`, `plan · Leader · plan ready`, `implement · Implementer · 3 commits, 12 tests passed`, `… · no commits`, `… · needs a decision`, `… · blocked`, `consult · Leader · answered|escalated`, `verify · Leader · accepted|changes requested (1 blocker)|escalated|superseded`, `… · failed (message)`. Old packets keep parsing.

### Stages and the task panel
- Stage states no longer depend on the last 40 events: the workflow projection also reads the task's `TASK_QUEUED`, `STEP_STARTED`, `TURN_STARTED` and `VERDICT` events by kind (bounded, cheap), so a stage with a record is done/current/rework as proven. A task that has not started (`step` unset) shows every stage as later (`·`), never `?`. `?` unknown remains only for a live task whose entered stage has no evidence.
- Tab into the task details lands on the first verb button (no highlight on the area itself); without verbs it lands on the current fold header. The body is no longer a Tab stop. The `↓ details` button, its label constants, the Enter "Details" and `v` panel bindings are removed; Up/Down on a verb button do nothing.
- Group titles `Progress`, `Result so far`/`Result`, `Problems` and the `Summary` label are cyan. `+lines` are sage and `−lines` dusty rose in the Result commits row and in the Header widget (`main (+100 −20)`). The `Needed` label becomes **Blocker** for an escalated question and **Pending** for a wait (dependency, slot, quota, PR, resources). The time line (`N in step · N total`) is flush left. A planning task's Result lists the tasks it added (`added T-15 Admin page`, sage).

### Details list
- Fold titles are cyan (bold when focused). Click toggles (a second click folds). An open fold is followed by one blank row, gone when folded. Left/Right are removed. **Enter** on a header opens the fold and moves into its list (if any); **Esc** returns to the header; **Enter** on an open header folds it. Esc on a header leaves as today.
- One hint line is docked at the bottom of the details area, always visible, and follows focus: header `↑↓ move · Enter open/close · Esc back`; Events list `↑↓ select · End follow · Esc back`; Agent activity adds `f filter`, and with the filter bar focused `r role · t turn · x clear`; Dependencies `↑↓ select · Enter open · Esc back`; Workspace `t task window` (`t` cyan bold, text grey). All per-section hint rows (`↑↓ select · End follow`, `Enter open`, `f filter`) are removed from the bodies. Tiny panes show keys only.
- **Task description**: `Acceptance criteria` is cyan bold. **Results**: commit hashes cyan; `Plan` rows are not truncated, `added T-15 Admin page` is sage and each scope change is shown in full, wrapped; the Tests row says `reported by the Implementer` (grey) in place of `agent-reported`, also in the briefing; a planning task shows its Summary, added tasks and plan/spec commits.
- **Decisions** is a chronological decision log: time, decider (Leader, You, Engine) and what/why in a line, wrapped: Leader verdicts with their top issues, escalations paired with how they were answered, scope changes (added tasks), model changes, Operator messages, take-over/hand-back/holds, PR opened/merged; an open escalation question first (amber). Source: bounded reads by kind; no new storage.
- **Events**: its status (`following`/`paused`) is right-aligned on the fold's title line (all fold headlines are right-aligned); the table holds short phrases; below it only the selected row's full text appears, in a shaded block with its own text style (no time/role/turn meta line). **Agent activity** rows are time, role, turn, command; the block below shows only the full command; the filter is a one-line text bar `role: any · turn: any` opened with `f` and edited with `r` (cycle role), `t` (turn) and `x` (clear), with no dropdowns or bordered inputs; pressing Down on the last row loads older rows, so older turns are reachable.
- **Usage** first row: `123.4k tokens` with the number cyan bold and the word grey (cost first, white). **Workspace**: no buttons; only `t` opens the task window (hint in the bottom line; the Leader/Implementer pane buttons and their dead plumbing go). **Settings** headline: `<mode> · <priority word> priority`, priority word `standard` (0), `high` (>0), `low` (<0), the exact number stays in the Priority row.

## Superseded accepted behaviour

| Accepted | Superseded by this review |
|---|---|
| Thirteenth: → enters and ← leaves the edit area; box stays open after leaving | Enter/Esc; hidden on exit |
| Thirteenth: `Role: step outcome` turn lines | `step · Role · what happened` |
| Thirteenth: stages from the last 40 events; queued tasks show `?` | Events by kind; queued stages `·` |
| Twelfth: `↓ details` hint button, Enter/`v` details bindings | Removed; Tab lands on the first verb |
| Twelfth/Thirteenth: Enter/Space/→ open, ← close; per-body hint rows | Enter/Esc; one bottom hint line |
| Thirteenth: Decisions as verification rounds | Decision log |
| Thirteenth: Events/Agent activity `Select`+`Input` filter row, meta line | Text filter bar, shaded full-text block |
| Thirteenth: Workspace buttons | `t` key and hint |

## Execution and evidence

Tasks 209–219 run sequentially, one fresh `shikiru-dev` agent per task (Sonnet 5.5 medium; Haiku 5.5 xhigh for 209 and 212), root review and corrections by the same agent. Follow AGENTS.md test-value rules; collected cases must not grow overall (1,651 at the start; Task 219 trims). Each commit needs a green `uv run poe check`.
