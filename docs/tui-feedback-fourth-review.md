# Phase 11 fourth real-project review

Status: planned (Tasks 129–140), not implemented. This specification refines Design 05 §3, 06 §5–6 and the accepted Phase 11 behaviour where the requirements below supersede it. **The TUI is the source of truth**: the [HTML reference](design/references/focus-steel-approved.html) is revised after the TUI changes (Task 139) to match what the product renders, not the other way round.

Analysis: ten read-only static analyses (Haiku 5.5 xhigh for template/styling items, Sonnet 5.5 medium for focus, task-list and tmux items) located the code, conflicts and tests. Nothing was run natively; claims marked *unconfirmed* must be reproduced by the implementing task before it changes behaviour.

Style notation used by the operator: `62<white bold> sessions<grey>` means the text `62` is white bold and ` sessions` is grey; the style follows the text it describes. Palette mapping (no cyan token exists; `cyan` is the steel accent): white = `PRIMARY_TEXT` `#dce2e9`, grey = `SECONDARY_TEXT` `#9aa5b5`, cyan = `STEEL_BLUE` `#83bddd`, green = `SAGE` `#90cba6` (`ui/tokens.py`, `palette.py`).

## Requirements

### Dashboard focus and attention
- Tab / Shift+Tab move through a visible focus state only. Every stop that is focused must highlight **all** of its lines (wrapped Usage and Resources rows included). Widgets that are inert or invisible (quiet escalation bar, non-project Usage, scroll containers, the footer grid, hidden inputs) are never Tab stops; they stay reachable through their explicit routes (Views › Attention, Enter on Usage/Resources).
- At startup the escalation bar shows its selected decision in the armed (highlighted) style so that its `Enter …` hint is meaningful. *Decision:* this is styling, not focus; initial focus stays on the task list so Task 107 (cyclic Up/Down task selection, Esc clear) and Task 118 (list-focused Enter resolves the displayed decision) remain true.
- The bar follows the selected task (click, Up/Down, search result, Recent row), without moving focus or writing a command. A task with an escalation shows it (its first, or the one already chosen with Left/Right) and the `Enter …` hint applies to it. A task without one shows `T-12<white bold> is not escalated<white>`, with the hint falling back to `Ctrl+P actions`, plus a grey ` · N decisions on other tasks` only when N > 0 so other pending decisions are not hidden. With no task selected (startup, Esc clear) the bar shows the first pending escalation in the armed style, or `No decisions needed`. Engine-fault, stall and engine-status rows keep precedence over the task-scoped text.
- Escalation bar: the escalation number (`E-n`) and task number (`T-n`) are bold; everything else keeps its style. The stored reason value (`decision_required`, `integration_blocked`, …) is shown as readable text (`Decision required`) through a total label table over every `EscalationReason`, derived from the persisted reason, never by parsing the stored headline. Stored headlines, packets and Enter routing do not change.

### Dashboard bars
- Token usage bar: `62<white bold> sessions<grey> · 62.5M<cyan bold> tokens<grey>[ · $4.28<green bold> API<grey>]`. The bracketed API segment appears only when API-billed cost data exists; `$0.00` recorded is cost data, an API-equivalent estimate is not. *Decision:* when coverage is partial (some sessions unreported or of unknown billing mode) the honest qualifier is kept as a grey suffix after the template; a known total stays bold cyan with the grey qualifier after it, and only an unknown total renders grey. Subscription and per-mode detail stays in Project Usage.
- Resource bar: `Host<white bold> CPU<white> 5%<cyan> RAM<white> 12.3/24.0 GiB<cyan> · Shikiru<white bold> CPU<white> 1%<cyan> RSS<white> 0.4 GiB<cyan> · Agents<white bold> CPU<white> 2%<cyan> RSS<white> 1.2 GiB<cyan>`, and when the agent tree is empty `Agents<white bold> process tree is empty<grey>`. Host keeps `RAM` used/total (system memory); Shikiru and Agents are labelled `RSS` (resident set size, a process measure) and have no denominator. Unavailable, stale and failed states keep their existing honest meanings. Narrow widths wrap at ` · ` group boundaries, never inside a value. Project Diagnostics keeps the explanation and any totals.
- Actions bar: the latest-command status is right-aligned and follows the right edge as the width grows (CSS specificity: `#focus-footer Button` currently overrides the status rule).

### Dashboard tasks list
- A leading blank column is reserved on every task row and one blank row separates task rows (active and Recent). Selection is shown by background/emphasis only: selecting or deselecting never moves a row's text horizontally or vertically.
- Blank line after the `Recent` heading, as after `TASKS`. The `Recent` heading uses the same colour as `TASKS` (steel).
- The task number (`T-13`) is bold; its title keeps regular weight, in active and Recent rows.
- Recent status words (`Done`, `Cancelled`) take their glyph's colour (Done sage, Cancelled grey).
- Recent keeps at most the **30** most recent terminal tasks by default (configurable; older ones remain in the full History view, which is unlimited) and shows them as a fixed window of **3** tasks, newest first, bottom-aligned in the wide layout (natural flow when compact), with `↑ Later` / `↓ Earlier` controls: Later pages toward newer tasks, Earlier toward older, each disabled at its boundary and hidden when there are three or fewer tasks. *Decisions:* no PageUp/PageDown. Active and Recent tasks remain one common selection list driven by Up/Down: Down from the last visible Recent row selects the next older task and shows the Earlier page, Up from the first Recent row of an older page shows the Later page, Up from the newest page's first row goes to the last active task, and Down from the oldest Recent task wraps to the first active task. `↑ Later` / `↓ Earlier` are indicators and mouse click targets; a click pages without changing the selection. A new completion, search or filter change resets to the newest window; the selected task identity survives paging; blank space (not rows) pads fewer than three tasks above the heading so the heading does not move.

### Popups
- Popup scrollbars use exactly the Tasks-list scrollbar colours (thumb, hover, active, track). *Decision:* name them once in `ui/theme.py` and use them in both places; the HTML adopts the resolved values.
- No popup initially focuses `Esc ×`. Initial focus goes to the first useful target (help/body scroller, views list, history filter, widget content, packet scroller, dialogue scroller, the actions Find input); Escape still closes from there and Tab still reaches `Esc ×`.

### Tmux
- Agent pane titles colour the context percentage: below 40 % green, 40 % up to but not including 60 % yellow, 60 % and above red, using the existing sage/amber/dusty-rose palette tokens. Thresholds are `defaults.toml` tunables with a pure domain classifier and are classified on the displayed rounded value. Dashboard gauges (70/90) are unchanged. Unreported context stays grey.
- Pane titles survive killing and restarting the Shikiru tmux session: titles are re-applied from stored state whenever panes are created or restored, so a restarted session never shows the tmux default (hostname).
- Documentation states explicitly that a taken-over pane is **not** restored as taken over after a respawn: the engine records `takeover_lost`, hands the role back, releases the `intervention` hold (unless another role or pause still holds the task) and resumes the same agent session; the Operator must take over again.

## Superseded accepted behaviour

| Accepted | Superseded by this review |
|---|---|
| Task 106 AC3 / proof: `4/4 sessions`, `API billed $0.42` | Token bar template above |
| Task 110 AC3, Task 117 AC1: host-total denominator per resource group | Denominator only for Host `RAM`; Shikiru/Agents show `RSS` without one |
| Task 116: no blank rows between tasks, scrollable Recent body with scrollbar/cues, grey Recent status | Blank rows, 3-task paged Recent, glyph-coloured status |
| Task 107/126 selection outline border (shifts text) | Non-shifting selection |
| `tui-parity.md:84` normal-weight body rows | Bold task number |
| Task 126 focused `Esc ×` captures | Initial focus off `Esc ×` |
| Task 125 context colour at gauge thresholds 70/90 | 40/60 for pane titles |

## Found while analysing

- `TASKS · Decision first` is still rendered by the queue (`plugins/widgets/queue.py`), contradicting Task 106; confirm natively and remove under Task 133 if present.
- The HTML draws `↑ Earlier / ↓ More` scroll cues (inverted against newest-first order) and a `×` for Cancelled where the TUI uses `○`; Task 139 aligns the HTML to the TUI.
- Task 120's right-aligned feedback was accepted on containment evidence only; Task 135 adds an edge assertion.

## Execution and evidence

Implement Tasks 129–140 sequentially, one fresh agent per task, root review and corrections by the same agent. Follow AGENTS.md test-value rules: real SQLite/Textual/private-tmux/fake-agent boundaries, assertions on rendered styles/coordinates rather than wording or constants. Each commit needs a green `uv run poe check`. Preserve earlier acceptance, the original PNG and the independently open gates 59, 74h and 74p.
