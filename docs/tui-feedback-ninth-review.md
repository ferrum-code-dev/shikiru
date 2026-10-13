# Phase 11 ninth real-project review

Status: delivered (Tasks 167–174, 2026-10-13; see the [record](release-readiness-2026-10-13-phase11-ninth-review.md)). This specification refines the [eighth review](tui-feedback-eighth-review.md). **The TUI is the source of truth**: the [HTML reference](design/references/focus-steel-approved.html) follows the product (Task 174).

Analysis: three read-only static analyses (Sonnet 5.5 medium for the flaky tests, Haiku 5.5 xhigh for the Command history popup and for titles, plugin error and AGENTS.md). Nothing was run; claims marked *unconfirmed* are reproduced by the implementing task first.

## Requirements

### Titles (decisions on the eighth-review open questions)
- `TASKS` becomes `Tasks`; `SHIKIRU` stays upper case and is bold (steel blue). `SHIKIRU` is the only upper-case exception. The count line reads `Tasks · waiting · 1 of 1`: the filter label is lower case after ` · `, like `Task filter · waiting`.

### Plugin error hint
- The error `no <kind> named 'x' is registered or installed` (dashboard widget and agent plugins, one producer) ends with a hint to reinstall from the checkout when the name is right (`uv tool install . --reinstall`, editable `uv tool install --editable . --reinstall`): entry points are written at install time, so a new or renamed plugin needs a reinstall. The README install procedure and design 07 say the same. Ambiguity and load-failure messages are unchanged.

### Test stability (the gate must not flake under host load)
- *Causes:* `wait_until` budgets of 2 s under ten xdist workers (about 330 call sites and several module constants), `await pilot.pause()` followed straight by an assertion (about 75 sites), a wall-clock CPU duty bound with 20 % headroom, and a tmux `/exit` sent before the fake agent is ready. The worker count is not lowered, and no retry, skip or sleep is added.
- One named UI wait budget replaces the 2 s constants and literals; `pause(); assert` becomes `wait_until`; the CPU test measures a longer burn and finds its burner by command line; the take-over test waits for the pane. Verification is a repeated run of the touched files under ten parallel copies, not one green gate.
- AGENTS.md "Test value" gains the audit lessons (no screenshot or SVG writes, behaviour-named tests, one table per rule, outcomes rather than wiring, `wait_until` rather than `pilot.pause()` before an assert) once the sweep makes the last sentence true.

### Command history popup
- *Rows are coloured* with existing palette tokens, as the Home footer summary already does: `#43` grey, ` · ` grey, status word bold (Pending amber, Applied sage, Rejected dusty rose), action white, target bold white, detail and queue time grey. Status words survive `NO_COLOR` as text and bold.
  `#44<grey> · Pending<amber bold> · Message<white> · T-12<white bold> · queued 10/13 12:34Z; engine currently stopped<grey>`
- *The `returned {json}` part is removed* from rows and from the palette's command details. A rejected command keeps its reason: the engine's `error` text, else `engine rejected the command`. Pending keeps its recorded queue time (no age; the popup does not re-render on the clock). 06 §5 and tui-focus no longer say the complete result stays in history.
- *Previous/next page use the arrow keys* (Left newer, Right older); `p` and `n` and their bindings are removed. The hint reads `←<cyan> newer` and `→<cyan> older` (the page buttons keep mouse parity; compact labels are the bare arrows). *Decision:* the arrows are active whenever the status filter is collapsed, so they work from the initial focus; they stay inert while the Select is expanded. Task-detail `p`/`n` paging is unchanged.

## Superseded accepted behaviour

| Accepted | Superseded by this review |
|---|---|
| Eighth review / 06 §6.1: `SHIKIRU` and `TASKS` are the upper-case exceptions | Only `SHIKIRU`, bold; `Tasks` |
| Command history `p previous` / `n next` | `←` / `→` in cyan |
| History rows plain text with `; returned {json}` | Coloured rows, no raw result |

## Execution and evidence

Implement Tasks 167–174 sequentially, one fresh agent per task (Haiku 5.5 xhigh for 167, 168 and 172, Sonnet 5.5 medium for the rest), root review and corrections by the same agent. Follow the AGENTS.md test-value rules; no task grows the collected test count except where it adds a behaviour that had none. Each commit needs a green `uv run poe check`.
