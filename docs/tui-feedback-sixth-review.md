# Phase 11 sixth real-project review

Status: Task 148 (gauge colours) delivered; the operator approved the three-section layout below (2026-10-12) and Task 149 implements it. This specification refines the [fourth](tui-feedback-fourth-review.md) and [fifth](tui-feedback-fifth-review.md) reviews. **The TUI is the source of truth**: the [HTML reference](design/references/focus-steel-approved.html) follows the product (Task 150).

Analysis: two read-only static analyses (Sonnet 5.5 medium for the panel layout, Haiku 5.5 xhigh for the gauge rule). The panel layout was rendered natively with fixture data; nothing else was run.

## Requirements

### Subscription-limit gauges
- Only the subscription-limit (quota window) gauges change colour: below 50 % sage (green), 50 % up to but not including 90 % amber (yellow), 90 % and above dusty rose (red), using the existing palette tokens. They are the only gauge kind in the product (shown in the Project usage popup); context colour in pane titles (40/60) and every other text stay unchanged.
- *Decisions:* thresholds are `defaults.toml` tunables (`[dashboard.gauges]` `quota_warn_pct = 50`, `quota_alert_pct = 90`, validated `0 < warn < alert <= 100`); one pure shared classifier replaces `context_level` and `gauge_color` (a `refactor:` commit first, context output unchanged); the displayed rounded whole percentage is classified (49.6 shows 50 % and is amber; 89.6 shows 90 % and is red); 100 % and above is red with the reported value; a window exists only when the provider reported a number, so there is no grey gauge state; a stale observation keeps the value's colour (its age is already printed).
- This supersedes the statements that the dashboard gauges use 70/90 (06 §5.3, §6.1, fourth review) for quota gauges.

### Project usage panel
- The popup title `Usage · Usage` (category and label of the context widget are both `Usage`) becomes a single, non-duplicated `Project usage`, matching the menu entry.
- The panel is restructured so the details are well structured and not duplicated (today coverage, billed cost, unreported charges and the subscription count each appear two or three times, and the Home bar totals repeat), in the operator's styling (bold white / cyan / green values, grey labels and qualifiers). The operator approved this layout (notation: W bold white, C bold cyan, G bold green, A amber, g grey; values illustrative):

```
Project usage

Totals<g>   62<W> sessions<g> · 62.5M<C> tokens<g> · $4.28<G> API<g>
Tokens<g>   in 60.0M<C> · out 2.5M<C> · cache r 900M<C> · w 30M<C>
Coverage<g> 55<W> reported<g> · 3<W> legacy<g> · 4<W> without tokens<g>
Billing<g>  40<W> subscription<g> · 6<W> API billed<g> · 5<W> unknown<g>
            est. $31.50<A> API-equivalent (20 sessions)<g>
────────────────────────────────────────
By agent<g>
claude<W>  30<W> runs<g> · 40.0M<C> tokens<g> · $4.28<G> billed<g>
codex<W>   32<W> runs<g> · 20.0M<C> tokens<g> · est. $6.50<A>
────────────────────────────────────────
Limits<g>
claude<W> 5h<g> ◑ 38%<sage> resets 1h 40m<g>
       weekly<g> ● 91%<rose> resets 3d 2h<g>
codex<W>  5h<g> ◑ 62%<amber> resets 2h 15m<g>
observed 2m ago<g>
```

Rules: each fact appears once (Home-bar totals are the Totals row; coverage, billed cost, unreported charges and the subscription count are not repeated per section); an unknown token total reads `tokens unknown<g>`; an estimate reads `est. $…<A>` and never counts in the `$ API` total; partial coverage is a grey qualifier after the bold value; unreported API charges and unknown billing modes sit in the Billing row, not the headline; the per-agent word is `runs` (project totals say `sessions`); raw source identifiers are replaced by readable words; the two config hints leave the panel (they belong in the docs). Compact widths wrap at ` · ` boundaries with the same rows; at 20×8 the section labels stay and the body scrolls. Gauge colours follow Task 148 (sage / amber / dusty rose).

## Superseded accepted behaviour

| Accepted | Superseded by this review |
|---|---|
| 06 §5.3/§6.1, fourth review, Task 136: quota gauges 70/90 | 50/90 |
| Popup title `Usage · Usage` | `Project usage` (Task 149) |

## Execution

Task 148 runs now (Haiku 5.5 xhigh); Task 149 (Sonnet 5.5 medium) after approval; Task 150 verifies, updates the HTML reference and records the outcome. Each commit needs a green `uv run poe check`.
