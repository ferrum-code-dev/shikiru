# Phase 11 thirteenth review: answering escalations, a readable task panel and lighter detail lists

Status: delivered (Tasks 197–208, 2026-10-15), verified in the [thirteenth-review record](release-readiness-2026-10-15-phase11-thirteenth-review.md). Superseded in part by the [fourteenth review](tui-feedback-fourteenth-review.md) (Enter/Esc answer box, turn lines, stages by kind, fold keys, Decisions log, Events and Agent activity layout, Workspace without buttons). This specification refines the [twelfth](tui-feedback-twelfth-review.md), [eleventh](tui-feedback-eleventh-review.md) and [tenth](tui-feedback-tenth-review.md) reviews where it differs. **The TUI is the source of truth**; the [HTML reference](design/references/focus-steel-approved.html) follows the product (Task 208).

Analysis: four read-only static analyses (Sonnet 5.5 medium for the resolution panel, the task status data and the detail sections; Haiku 5.5 xhigh for selection bars and the Recent pager). Nothing was run; claims marked *unconfirmed* are reproduced by the implementing task first. The operator approved the layouts below on 2026-10-14.

## Requirements

### Selection and small style
- No vertical bar marks a selected or focused row anywhere (fold headers, `RowList` rows, the Tasks list outline in non-focus placements); the background-only highlight of the Tasks list is the one style. Bold name stays on fold and list rows. Views menu and session picker adopt the same highlight (`$selected-surface`).
- The Recent pager reads `↑ later` / `↓ earlier`: lower case, grey, arrows cyan (key helper), inactive state unchanged.

### Escalation resolution panel
- `needs your input` is white (`PRIMARY_TEXT`) so the editable option stands out; the rest of its consequence stays grey.
- The edit area appears directly below the editable alternative (or below `Write your own answer…`), starts empty, and never includes the option title; the sent answer is still `<label> — <details>` for an alternative and the plain text for the own answer; an empty box on an alternative sends the label alone. Look: no side or top border, indented under the option text, a subtle selected background while focused, a single rule marks its bottom edge; no left stripe.
- Keys: **Ctrl+S**, **Ctrl+Enter** and **Ctrl+J** send (Ctrl+Enter reaches the app only on terminals with the kitty keyboard protocol, which tmux does not forward, so `Ctrl+S` is the hint shown); **Enter** inserts a new line; **→** on an editable row enters the box; **←** at the start of the box (or Esc) returns to the list; the old Tab-adds-details route goes. Enter on an unflagged alternative still sends at once; Enter on a `needs_input` row enters the box like →.
- `What was tried` is retitled **Details** and becomes its own Tab stop (Tab from the Choose list, Shift+Tab back; Enter/Space toggles; Up returns to the list); the arrow list no longer has a virtual last row. The selection background covers only its heading and body, not the rule above it.
- Details is label/value rows: grey labels (Reason, Rounds, Turns, Recovery, Produced, Operator), white values; step names cyan, success sage, failure dusty rose, rework and recovery amber, `+N earlier` grey. The digest keeps its capped, chronological content (Task 189) but is produced as structured items (kind and text) and still persisted in the packet in the readable `Label: text` form so old packets and the evidence popup keep working.

### Task status correctness
- A Done task shows every stage as done or skipped, never `? unknown`; a Cancelled task shows its stages as done up to the stop, the stop stage as `✕ stopped`, the rest as skipped. `?`/unknown remains only for live tasks with missing records. The state line of a Cancelled task is `○ Cancelled · stopped during <step>` (and `Done`), with no role tail, no live marker and no `in step` time; a terminal task shows its total duration. List rows and the progress strip show only the terminal status (a cancelled task never shows `waiting on …`, `consult`, an escalated panel); `wait_reason` is cleared when a task finishes.
- A Done task's PR line reads merged, not open.
- Briefing verbs: **Cancel** is added for every non-terminal task; a task waiting for a dependency, slot or quota offers Pause/Resume and Cancel but no Message (no agent will read it yet); escalated offers Answer, Message, Cancel; running offers Take over, Message, Pause, Cancel (maximum four verbs, wrapping in compact). Each verb equals a palette action id.

### Task details
- The briefing is three titled groups, each only when it has data: **Progress** (step and time, rework with escalation proximity, Now with the agent's wrapped last words), **Result so far** (branch → base, commits and diff totals, tests, PR) and **Problems** (verdict with severities, what is needed from the Operator, the last failure), then Cost and the verbs row. A Done task shows a Summary line and what was merged; Cancelled shows where it stopped and that the branch is kept.
- Fold set and order: **Task description** (first; the request, then the acceptance criteria as a list), **Results**, **Decisions**, **Events**, **Agent activity**, **Usage**, **Dependencies**, **Workspace**, **Settings**. Engine facts and Reports and decisions are removed. No project-wide fold (Command history, Header, Escalations, Workflow strips, Queue, Activity dialogue, Project usage, Resources, Diagnostics, Extensions) appears in a task's details; they stay reachable from Home and the palette. Bodies do not repeat their fold title.
- **Results**: Merged/Branch, commits (short hash and subject, first few, `+N more`), files and lines, tests (agent-reported), PR (merged for a Done task), plan changes (scope changes and new tasks), residual risks; a task in progress shows what exists so far, marked so; Cancelled shows the kept branch and `not merged`. Source: recorded reports, `TASK_DONE`, `PR_OPENED`, `SCOPE_CHANGE` and the parsed facts, no git call on render.
- **Decisions**: one line per verification round (verdict, severity counts) in order, then the Operator's answers and messages with time; the open decision, if any, first.
- **Events** are what the engine decided (transitions, verdicts, nudges, escalations, Operator actions); **Agent activity** is what the agents did (messages, tools, edits). Each row is time, who and one human phrase with a status glyph; the category dropdown goes (each section has its fixed meaning), Agent activity keeps role and turn filters behind `f`; the selected row shows its full text wrapped (at most four lines) with no JSON, event ids or ISO timestamps; the paging key hint goes.
- **Usage**: heading with total and tokens, then one aligned line per session (role, model, tokens, cost, context; old sessions marked), then quota windows; session ids, cache figures, billing sources, model-history lines and provenance disclaimers are dropped; a session that reported nothing is one amber note.
- **Dependencies**: aligned relation, id, title and state; blockers and problems in amber or dusty rose; Enter opens the related task; the PgUp/PgDn hint goes (paging keys keep working).
- **Workspace** and **Settings**: grey-label/white-value tables, no repeated title; the workspace buttons look like the briefing verbs and are hidden for Done and Cancelled tasks.

## Superseded accepted behaviour

| Accepted | Superseded by this review |
|---|---|
| Twelfth: Tab adds details to an alternative; Enter in the box sends | Ctrl+S/Ctrl+Enter/Ctrl+J send, Enter newline, →/← enter and leave |
| Twelfth: box prefilled with the label, under the own-answer row | Empty box under the edited option |
| Twelfth: `What was tried` is the last arrow row | `Details`, a Tab stop |
| Eleventh/Twelfth: `▌` marks fold and list selection | Background only |
| Eleventh: nine sections incl. Reports and decisions, Engine facts, project views | New set above |
| Eleventh: briefing rows Now/Produced/Verdict/PR/Cost | Progress / Result so far / Problems |
| Eleventh: verbs max three, Message while waiting | Cancel added, max four |
| Fourth: `↑ Later` / `↓ Earlier` | `↑ later` / `↓ earlier`, grey |

## Execution and evidence

Tasks 197–208 run sequentially, one fresh `shikiru-dev` agent per task (Haiku 5.5 xhigh for 197, Sonnet 5.5 medium for the rest), root review and corrections by the same agent. Follow AGENTS.md test-value rules. Each commit needs a green `uv run poe check`.
