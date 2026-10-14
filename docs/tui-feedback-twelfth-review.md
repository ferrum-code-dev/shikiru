# Phase 11 twelfth review: keys, escalation answers and inline details

Status: delivered (Tasks 186–196, 2026-10-14), verified in the [twelfth-review record](release-readiness-2026-10-14-phase11-twelfth-review.md). This specification refines the [tenth](tui-feedback-tenth-review.md) (resolution panel), [eleventh](tui-feedback-eleventh-review.md) (task details) and [fifth](tui-feedback-fifth-review.md) (escalation bar keys) reviews where it differs. **The TUI is the source of truth**; the [HTML reference](design/references/focus-steel-approved.html) follows the product (Task 196).

Analysis: three read-only static analyses (Haiku 5.5 xhigh for focus, keys and `u`/`e`; Sonnet 5.5 medium for the resolution panel and for the task details). Nothing was run; the implementing task reproduces first. The operator approved the options below on 2026-10-14.

## Requirements

### Keys and focus
- Up/Down select tasks wherever focus sits on the Home screen: a focused bar (escalation, usage, resources, footer) never swallows them and never scrolls the page instead. *Decision:* one app-level route moves the task selection when focus is not on the task list, a text input or text area, an expanded Select, or a modal other than the task detail; the escalation bar keeps Left/Right (fifth review) but drops its Up/Down stop. This supersedes "Up/Down inert while the bar is focused".
- Key names (`Enter`, `↑↓`, `Esc`, `Tab`, `Ctrl+P`, `←`/`→`, single letters) are cyan (`STEEL_BLUE`) in every hint, footer, help line, popup close control and panel; the label after a key keeps its style. One shared helper in `ui` builds them; bold survives `NO_COLOR`.
- `u` and `e` no longer open task usage or events. Both stay reachable through the details list and `Ctrl+P`.

### Escalation resolution panel
- The selected row has the same background-only highlight as the Tasks list; the `▌` glyph goes (the recommended star stays).
- `What was tried` is the last row of the same arrow list: ↑↓ reach it, Enter expands or collapses it and never sends; `t` and click still work.
- `What was tried` is a digest of the recorded work, at most `escalation.tried_lines` (default 8) lines, chronological: the existing reason sentence first, then rounds (implement ×3, verify ×2, consult ×1, iteration n of m), the last turns with outcome (commits, tests passed/failed, verdict with blocker count), recovery (nudges, helper actions, rung changes), produced numbers and Operator messages; older items collapse to `+N earlier`. It is a pure function over events, reports, task and facts; no new storage. Missing inputs leave their line out.
- A Leader alternative may carry `needs_input` (default false; old packets parse unchanged). Enter on such a row (marked `✎`, consequence line `needs your input`) opens the answer box prefilled with `<label> — `; Enter inside the box sends. On any other alternative Enter still sends the label at once and Tab opens the same prefilled box. The consult and verify prompts tell the Leader when to set the flag (the alternative is an ask for the Operator to provide something).

### Task details
- The agent's current output in `Now` wraps instead of cutting at one line: the whole message (stored up to `dashboard.last_words_chars`, default 4000) wraps to `dashboard.now_max_lines` (default 6; `dashboard.now_compact_lines`, default 2, in the compact layout), with `…` only past the cap.
- The details list popup is retired. Below the briefing and its verbs the nine sections are folds: a header row with the right-hand headline (live even when folded) and a body. ↑↓ move between headers, Enter, Space or → open, ← closes; inside an interactive body (Events, Agent activity, Blockers and related) ↑↓ move its rows and Esc returns to its header. Any number of folds may be open and every open fold stays live (timers run only for open folds). Open folds survive refresh and task selection changes within one panel. Events and Agent activity keep independent timelines (own cursor, category and filters). Compact and tiny layouts use the same folds. Project contextual views follow the nine headers.
- Entry points that opened the chooser or a section (`Enter details ▸`, `v`, the palette Views route, usage and evidence routes, the Home widgets that open a section) open the panel with that fold open and focused. The briefing's `Enter details ▸` row becomes a hint to ↓.

## Superseded accepted behaviour

| Accepted | Superseded by this review |
|---|---|
| Fifth review: Up/Down inert on the focused escalation bar | Up/Down select tasks from any bar |
| Tenth review: `▌` marks the selected choice | Background-only highlight |
| Tenth review: `What was tried` mouse and `t` only; one canned sentence | Arrow-reachable row; digest |
| Tenth review: Enter on any alternative sends | Enter opens the box for `needs_input` alternatives; Tab adds details to the rest |
| Eleventh review: details list popup, one section view at a time | Inline folds |
| Eleventh review: last words on one line | Wrapped to a capped block |
| Eleventh review: `e`/`u` jump to Events/Usage | Removed |

## Execution and evidence

Tasks 186–196 run sequentially, one fresh `shikiru-dev` agent per task (Haiku 5.5 xhigh for 186, 187; Sonnet 5.5 medium for the rest), root review and corrections by the same agent. Follow AGENTS.md test-value rules; each commit needs a green `uv run poe check`.
