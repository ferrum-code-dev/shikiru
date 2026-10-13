# Phase 11 eleventh review: task details

Status: planned (Tasks 180–185). This specification supersedes the task detail summary, the Views menu for a task and the `p`/`n` paging (Design 06 §5, `tui-focus.md` §4/§6, `tui-workspace.md`, `tui-parity.md`) where it differs. **The TUI is the source of truth**: the [HTML reference](design/references/focus-steel-approved.html) follows the product (Task 185). The operator approved the layouts below (2026-10-13).

Analysis: two read-only static analyses (Sonnet 5.5 medium): an inventory and interaction critique of every detail surface, and a value audit of the progress signals. Facts marked verified were read in code; nothing was run.

## Findings behind the redesign
- The Home inspector, the compact inspector, the task popup and the task window are one class (`TaskDetailPanel`, `detail.py`); the surfaces differ only in sizing and Esc behaviour.
- The shown "last signal" is only the signal class name (`› ToolStarted`), never the tool or command; for Codex the tool name is just `command` or `file_change`. Only its age is informative (is the agent alive). Turn numbers count engine turns, not work. The agent's own sentences (`Message.text`, `TurnEnded.last_message`) are the best "what is it doing now" and are thrown away by the view.
- Stored but never shown: the rework count against `max_iterations`, `rejections_on_rung` against `escalate_after`, total elapsed (`started_at`), the verdict's issues by severity, the agent-reported test counts, the diff summary and commit count in the recorded facts, the PR state.
- The same status is stated in the list row, the state line, the situation sentence and the attention strip; fixed sentences ("Work is active.") and permanent hints restate it.
- The Views menu is a dropdown plus an Open button, `p`/`n` page only two views and "previous" means newer, the timeline is browsed one dropdown row at a time, Esc means four different things, Take over / Hand back / Message have no visible entry.
- Not captured and out of scope: acceptance-criteria status per criterion (needs a verdict schema change), plan checklist, ETA, current file; the activity hint (`editing`/`reading`) was declined.

## Requirements

### Briefing (inspector in the Home wide and compact layouts, the task popup and the task window)
```
 T-12 · Add retry to parser<white bold>
 ▲ Implementing<steel bold> · Leader · rework 2 of 5<grey>
   4m in step · 31m total<grey>
 ✓ triage  ✓ plan  › implement  · verify  · integrate
 ────────────────────────────────────────────────
 Needed from you<amber bold>
   Which failures should the parser retry?
   Enter answer<grey>
 ────────────────────────────────────────────────
 Now<grey>        active 3s ago<grey>
   "Running the parser tests after the retry change."
   escalates after 1 more rejection<grey>
 Produced<grey>   3 commits · 5 files · +120 −34
   tests 8 passed · 1 failed (agent-reported)<grey>
 Verdict<grey>    Rejected · 2 major · 1 minor
 PR<grey>         #41 open · checks pending
 Cost<grey>       Leader $0.42 · Impl $1.10 billed<sage>
 ────────────────────────────────────────────────
 Take over · Message · Pause<steel>   Enter details ▸<grey>
```
Compact (48 columns):
```
 T-12 · Add retry to parser<white bold>
 ▲ Implementing · rework 2/5 · 4m<grey>
 ✓ ✓ › · ·
 Needs you: Which failures
   should the parser retry?<amber>
 "Running the parser tests…" 3s<grey>
 3 commits +120 −34 · 8✓ 1✗
 $1.52 billed<sage>
 Take over · Message   Enter ▸
```
- *Rows appear only with data:* `Needed from you` only when a decision, wait or blocker needs the operator (the decision question first; a blocker or quota wait otherwise); `Now` only while a role works (age of the last activity, with the stalled warning at the configured stall limit; the agent's last words on one line; the escalation proximity line from the stored rejection counter and the configured limit, only when an automatic escalation is close); `Produced` from the recorded facts snapshot (commits, files, lines; taken at verify/hand-off, not live) and the agent-reported test counts (labelled); `Verdict` as the latest decision with issue counts by severity; `PR` only when a pull request exists (CI only when the engine recorded it); `Cost` as one line with the per-role split. A terminal task drops `Now` and shows its report's one-line summary under `Produced`. Holds and operator ownership keep their line under the state line when present.
- *Removed from the summary:* the signal headline (`› ToolStarted`), turn numbers, tool lines, fixed-status situation sentences, the permanent hand-back/resume hint text, per-role session rows (the cost line carries the split; the rest is in Usage and sessions), the `u details` / `e evidence` buttons, the project attention counts.
- *Actions row:* at most three state-dependent verbs from a pure mapping (Take over or Hand back; Message; Pause or Resume; Answer when a decision is pending), each also in `Ctrl+P`; `Enter details ▸` opens the details list.

### Details list (replaces the Views dropdown for a task)
```
 Task details · T-12           Esc ×
 ↑↓ select · Enter open<grey>
 ▌ Reports and decisions   verdict rejected
   Request and criteria
   Events                  42 recorded
   Agent activity          tools, messages
   Usage and sessions      $1.52
   Engine facts
   Workspace               branch task/12
   Blockers and related    1 blocker
   Task settings           priority 3
```
Arrows move, Enter opens a section, Esc goes up one level (section, list, closed). The summary on the right of each row is the section's own headline. Sections keep their content minus engine event ids, timestamp-evidence rows and recorded window/pane ids; Workspace keeps the branch, the worktree path and the pane jump buttons. Events (default: decisions and transitions) and Agent activity (per-role signals, tools, messages with the category, role and turn filters) are arrow lists: ↑↓ move, PgUp/PgDn page, End jumps to the latest and follows; scrolling up pauses following. Relationships open the related task on Enter. `p`/`n`, the entry dropdown, the Related-task Select and the Follow button are removed.

### Interaction
- Enter on a Home list row only selects (unchanged); Enter on the focused inspector opens the details list. Esc in the embedded Home inspector clears the selection (no popup re-open); in the task window Esc goes to Home; in a popup it goes up one level.
- Initial focus on the list; the body is focusable consistently, not only when it overflows.

## Superseded accepted behaviour

| Accepted | Superseded by this review |
|---|---|
| Summary with signal headline, session rows, `u details` / `e evidence` buttons | The briefing above |
| Views dropdown + Open for a task | The details list |
| `p`/`n` paging, entry and related-task dropdowns, Follow button | Arrow lists, PgUp/PgDn, End |
| Embedded Esc re-opens the task popup | Esc clears the selection |

## Execution and evidence
Tasks 180–185, one fresh agent per task (Sonnet 5.5 medium), root review after each. Follow the AGENTS.md test-value rules: keep the behaviour listed in the analysis (report/verdict projection, usage cost bases, signal identity, milestone honesty, timeline paging and filters, relationships and blocker correctness, ownership rules, activation-time navigation), delete shape, wording and widget-id tests; the collected count should fall. Each commit needs a green `uv run poe check`.
