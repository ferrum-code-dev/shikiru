# Phase 11 sixth real-project review

Status: planned. Task 148 (gauge colours) is approved to run; Task 149 (title and layout of the Project usage panel) **waits for the operator to approve the layout** and must not start before that. This specification refines the [fourth](tui-feedback-fourth-review.md) and [fifth](tui-feedback-fifth-review.md) reviews. **The TUI is the source of truth**: the [HTML reference](design/references/focus-steel-approved.html) follows the product (Task 150).

Analysis: two read-only static analyses (Sonnet 5.5 medium for the panel layout, Haiku 5.5 xhigh for the gauge rule). The panel layout was rendered natively with fixture data; nothing else was run.

## Requirements

### Subscription-limit gauges
- Only the subscription-limit (quota window) gauges change colour: below 50 % sage (green), 50 % up to but not including 90 % amber (yellow), 90 % and above dusty rose (red), using the existing palette tokens. They are the only gauge kind in the product (shown in the Project usage popup); context colour in pane titles (40/60) and every other text stay unchanged.
- *Decisions:* thresholds are `defaults.toml` tunables (`[dashboard.gauges]` `quota_warn_pct = 50`, `quota_alert_pct = 90`, validated `0 < warn < alert <= 100`); one pure shared classifier replaces `context_level` and `gauge_color` (a `refactor:` commit first, context output unchanged); the displayed rounded whole percentage is classified (49.6 shows 50 % and is amber; 89.6 shows 90 % and is red); 100 % and above is red with the reported value; a window exists only when the provider reported a number, so there is no grey gauge state; a stale observation keeps the value's colour (its age is already printed).
- This supersedes the statements that the dashboard gauges use 70/90 (06 §5.3, §6.1, fourth review) for quota gauges.

### Project usage panel
- The popup title `Usage · Usage` (category and label of the context widget are both `Usage`) becomes a single, non-duplicated `Project usage`, matching the menu entry.
- The panel is restructured so the details are well structured and not duplicated (today coverage, billed cost, unreported charges and the subscription count each appear two or three times, and the Home bar totals repeat), in the operator's styling (bold white / cyan / green values, grey labels and qualifiers). **The layout is pending the operator's approval**; the approved layout is recorded here before Task 149 starts.

## Superseded accepted behaviour

| Accepted | Superseded by this review |
|---|---|
| 06 §5.3/§6.1, fourth review, Task 136: quota gauges 70/90 | 50/90 |
| Popup title `Usage · Usage` | `Project usage` (Task 149) |

## Execution

Task 148 runs now (Haiku 5.5 xhigh); Task 149 (Sonnet 5.5 medium) after approval; Task 150 verifies, updates the HTML reference and records the outcome. Each commit needs a green `uv run poe check`.
