# Shikiru Operator Workspace

## 1. Status and relationship to the design

This document specifies the TUI for [Phase 8](../tasks/plan.md#phase-8--operator-workspace). Task 75 shipped the shared visual theme and navigable shell for current widgets. Task 76 shipped the selectable task browser and bounded search through the existing `queue` entry point. Task 77 shipped shared task detail, task-scoped activity and usage, and navigation to existing task windows and role panes. Task 78 shipped contextual actions and the searchable palette. Task 79 shipped bounded durable command feedback and persistent ownership/hold banners. Task 80 shipped the Home summary composition, durable grouped attention, project activity and header context. Task 81 shipped structured escalation resolution with durable allowed kinds, preserved original packets, expandable evidence and stale-form updates. Task 82 shipped blocker evidence, navigation to related tasks and task-family context. Task 83 shipped retained completion detail, filtered bounded evidence timelines, and latest-tail following. Task 84 shipped recorded usage coverage and reachable secondary diagnostics. Task 85 shipped responsive composition and accessible static refresh. Export, operator documentation and release verification remain later work. Each Phase 8 task updates the corresponding normative design sections when its behaviour ships.

The workspace refines [06 · Operator Interfaces](design/06-operator.md), especially §5.4–5.5 and §6: the system pane becomes a navigable workspace with distinct home and task views, contextual actions, and persistent command feedback. The comic strip remains the task's expanded progress view. The architecture and workflow in [02](design/02-architecture.md) and [03](design/03-workflow.md) govern all state changes.

The implementation stays on Python, Textual, Rich, SQLite and tmux. The engine remains the single writer. Human actions use the same operator services as MCP; remote access continues over SSH. Existing role panes remain structured live views and run the agent's native TUI during takeover.

## 2. Operator goals

An Operator should be able to answer these questions without opening logs or remembering task IDs:

1. Does anything need my decision or intervention?
2. Which tasks are progressing, and which are waiting?
3. What is preventing a particular task from progressing?
4. What did the agents decide, and what evidence supports it?
5. What can I do here, and when will that action take effect?
6. Was my action queued, applied or rejected?
7. What happened to a task after its tmux window closed?

The primary interaction is **select a task → understand its situation → act → observe the result**. Browsing and opening evidence are read-only. Navigating the UI must never submit a command, resume a held task, create a worktree or recreate a completed task's window.

## 3. Information model

Keep the following facts distinct in both the view models and the interface:

| Fact | Examples | Presentation rule |
|---|---|---|
| Task status | Queued, running, integrating, escalated, done, cancelled | Use persisted domain state. |
| Current step | Triage, plan, implement, consult, verify, integrate | Show the actual step, including setup before a step exists. |
| Hold or wait | Paused, operator ownership, quota, dependency, slot, resources, PR open | Explain why progress is prevented; several holds may coexist. |
| Activity | Recent signal, running tool, no recent signal, diagnosed stall | Derive from normalized signals and existing activity rules. |
| Ownership | Automation, Operator owns Leader, Operator owns Implementer | Derive from run ownership and intervention state. |
| Command outcome | Pending, applied, rejected | Read the durable command row; acknowledgement is separate from agent delivery. |
| Evidence | Report, verdict, git facts, recorded signal, engine transition | Identify its source and time. |

An engine lock proves that the engine process holds its lock. A recent fault or old task signal is separate evidence. An old event alone does not prove an unhealthy engine. A frozen spinner does not prove a stalled turn while a tool is legitimately running.

Use existing domain rules for holds, dispatch order, activity and recovery. UI projection rules must not become a second scheduler or workflow engine.

## 4. Home view

### 4.1 Header and summaries

Keep a compact header visible while the body scrolls. It contains project identity, engine running/stopped, occupied/maximum slots, integration mode, and checkout branch/dirty state. Show repository sample age when relevant. An engine fault or failed read has an explicit warning with its recorded cause.

The summary area contains:

| Summary | Content | Activation |
|---|---|---|
| Attention | Open decisions, diagnosed task stalls and recorded engine faults requiring inspection | Opens the attention list. |
| Running | Tasks in `running` or `integrating` that are not held or waiting | Opens the active task list. |
| Waiting | Non-terminal tasks that are queued, held, escalated or waiting for an external condition | Opens the waiting task list. |

The Running count describes workflow state, not CPU activity or a guarantee that a process is executing at that instant. Waiting includes tasks whose status is still `running` while paused. Attention and Waiting can overlap; label their categories and do not present the cards as an additive total. Slots continue to follow the domain's slot accounting, including paused tasks and PR waits.

Attention counts grouped task/project rows; reasons for one task share one row. Open decisions lead in creation-time/ID order. A missing or unreadable packet leaves its durable escalation ID, task, headline and allowed actions visible beside the evidence error. Opening evidence never creates a packet or changes the decision. The shell refreshes from durable state, including another client's resolution, without moving focus or opening a modal. Existing diagnosed stall and unrecovered engine-fault evidence is shown alongside decisions. Retries, quota waits, expected rework, and deliberate pauses remain informational workflow states. Engine-lock liveness, fault records and agent signals are distinct observations; attention adds no heartbeat, long-wait classifier or notification policy.

Use compact cards when space permits and one summary line in a narrow or short pane. Large branding, charts and diagnostics must not push attention or task navigation out of reach.

### 4.2 Attention

Put open escalation decisions first, ordered by creation time with ID as the tie-breaker. Each entry names its escalation and task, headline, age and next available action. A task with several signals of trouble appears once with its reasons grouped.

Separate actionable decisions from informational recovery events. A normal quota wait, an expected rework round or an intentionally paused task is not an emergency. Include the existing stall and engine-fault evidence without adding a new alert classifier, heartbeat or long-wait escalation policy.

An escalation remains visible until durable state says it is resolved. When its packet is temporarily unavailable, show the durable headline and allowed actions alongside the evidence error. Opening the view does not manufacture a packet or change escalation state.

### 4.3 Main body

The home body contains a selectable task browser, shared task detail access, and recent project activity. On a wide pane, browser and detail sit side by side. On a compact pane, Enter opens detail and Escape returns to the browser with its previous selection and scroll position. Task 80 composes these with the existing header and bounded summaries; responsive wide/compact layout remains Task 85 work.

Recent activity covers verdicts, scope changes, completions, holds, retry/rework transitions, quota changes, integration waits, engine faults and recovery. Routine tool traffic belongs in task evidence and the role panes. Usage and diagnostics are secondary views reachable from the workspace navigation and palette. At 100 columns or more and more than 18 rows, browser and shared task detail sit side by side; compact panes stack the same mounted browser and detail so selection, search and reading state survive resize.

Illustrative wide home view; the numbers and task names are examples:

```text
 Shikiru · project   Engine running   Slots 1/2   Integration: local merge

 ┌ Attention ─────────┐ ┌ Running ───────────┐ ┌ Waiting ───────────┐
 │ 1 decision         │ │ 1 task            │ │ 3 tasks            │
 │ E-3 · T-12         │ │ T-15 · Implement  │ │ 2 depend on T-12   │
 └────────────────────┘ └───────────────────┘ └────────────────────┘

 ┌ Tasks ──────────────────────┐ ┌ T-12 · Add retry to parser ──────┐
 │ > ! T-12  Needs decision    │ │ Triage ✓ → Implement ✓          │
 │   ● T-15  Implementing     │ │ Verify ! → Merge ·               │
 │   ◌ T-16  Waiting on T-12   │ │                                 │
 │   ◌ T-17  Waiting on T-12   │ │ Leader asks:                    │
 │                            │ │ Which errors should be retried? │
 │ [Active] [Waiting] [History]│ │                                 │
 └────────────────────────────┘ │ [Resolve] [Evidence] [Window]    │
                                └─────────────────────────────────┘
 Recent activity · Verdicts, scope changes and completions
 Tab: section   ↑↓: select   Enter: details   Ctrl+P: actions   ?: help
```

## 5. Task browser

### 5.1 Rows, filters and ordering

Every row keeps the task ID, title and meaningful state visible. Additional columns show current step, iteration, acting role, age and blocker when width allows. A row may expand to two or three lines instead of discarding its title or hold.

| Filter | Membership | Ordering |
|---|---|---|
| Active | `running`, `integrating`, `escalated` | Open decisions and diagnosed stalls first, then other active tasks by start time and sequence. |
| Waiting | Queued tasks and non-terminal tasks with holds or a wait | Queued tasks retain dispatch order; started waiting tasks form a separate labelled group. |
| History | `done`, `cancelled` | Latest finish time first, then sequence. |
| All | Every task | Non-terminal tasks first, then history. |

Search matches task ID and title case-insensitively and combines with the selected filter. The full request is available in detail. Display the active search/filter, matching count and a clear empty result. Clear search and switching filters are explicit operations.

Queued rows use the same dispatch sort rule as the scheduler. A displayed position is an ordering aid, not a promised start time; eligibility can change and agent quota blocks can allow another task to proceed first.

### 5.2 Selection and refresh

Selection is stored by task ID, not by row index. State updates, reordered rows, new tasks, changed titles and clock ticks retain the selected identity. If it leaves the active filter, retain its detail with an explanation and offer to reveal it in the appropriate filter; deliberate movement then selects a visible row. If the record is missing, show that fact and retain the previous navigation location.

Opening detail or a modal preserves the browser's search, filter, selection and scroll. Resizing preserves the selected task and focused section. UI navigation state is local to the dashboard process; it does not need a database table or a preferences system.

Paginate or incrementally load history and long lists through read-only queries. Refresh affected data after durable state changes. Animation ticks update visual frames and displayed ages without repeatedly loading full task history.

## 6. Task detail and task windows

The expanded detail includes these sections, with the most relevant section open first:

1. **Situation:** ID, full title, status, current step, iteration, holds, waits and ownership.
2. **Progress:** the comic strip for the task's actual mode, including consult, rework, setup and integration waits.
3. **Latest decision:** the latest report or verdict, its role and turn, outstanding question, issues, risks and reported tests.
4. **Evidence:** request and acceptance criteria, earlier reports, engine-computed git facts, recorded signals and timeline.
5. **Relationships:** parent, children, replacements where recorded, dependencies and dependents.
6. **Workspace:** recorded branch, worktree, session identities and existing role-pane targets.
7. **Actions:** applicable task/role actions, with their timing and effect.

Label test results from reports as agent-reported; git facts retain their engine-computed provenance. Do not turn a reported successful test into an independent Shikiru verification claim.

`dash --task T-n` opens the same detail component fixed to that task. Its activity, reports and usage are scoped to that task. A compact global attention indicator links back to home; the task window does not repeat the entire project queue, dialogue and diagnostics stack.

Opening an existing task window resolves its current target at activation time. If it closed or disappeared, keep detail readable and explain the unavailable target. Role-pane navigation verifies that the current recorded pane still belongs to the task's current recorded window before selecting it; a moved or inconsistent pane is reported as unavailable. Takeover continues to select the acknowledged role pane. A completed or cancelled task remains inspectable from home without recreating its worktree, sessions or tmux window.

Task 77 ships situation and ownership, the semantic strip for the task's actual mode and step, latest validated agent report and latest validated verdict, request and triage acceptance criteria, bounded recent engine events and recorded role signals, the latest recorded engine git facts, branch/worktree/window/pane metadata and session usage. Report tests are labelled as agent-reported; git facts are labelled engine-computed. A later implementation report does not hide an earlier Leader verdict. An engine override takes precedence only over the Leader verdict from the same numeric turn, while a newer Leader verdict takes precedence over an older engine override; each verdict keeps its source label. Missing or unreadable report/facts paths are reported independently from available role signals, and basic task state remains visible. Task 83 adds history and a separately refreshed timeline while keeping reports and git facts available after ordinary worktree/window cleanup for as long as the retained run files remain. The narrow task layout keeps task ID and the full title in its visible identity block; its horizontal toolbar remains scrollable, and the detail body scrolls independently.

Task 82 adds collapsible blocker evidence and task relationships to this detail. The blockers section shows the stored scheduler reason with current available evidence and recognizes a running quota wait only from the task's current unresolved turn lifecycle, matching role/rung evidence, task quota-block event and still-live account block. Resource sample age is labelled separately from the scheduler reason; a provider reset, including an unknown reset, remains separate from the engine retry/probe deadline. PR details use recorded events and do not claim live CI observation. Relationship pages show their current position and provide Previous and More controls, disabled at the first and last page; navigating pages keeps the detail identity, selected related task and focus on the available page control. Submitted age and recorded requeued age use their respective timestamps; there is no blocker-age or start-time estimate. Related tasks reuse the existing detail callback, and returning preserves Home's browser selection, search and reading position.

In Home, Enter pushes this shared detail screen for the selected task. Escape dismisses it and restores the browser selection, focus and scroll position. In `dash --task`, the detail is fixed at the start of Workspace; Activity and Usage use only that task's rows, and Situation shows global decision and engine-fault counts. The task view omits project-wide header, queue, escalation, strip and diagnostics widgets. Configured third-party widgets remain reachable in Extensions. Detail offers existing-window and role-pane navigation, Home/back and the shared palette entry point; contextual workflow eligibility and action forms remain Task 78 work.

## 7. Navigation and keyboard contract

| Context | Keys | Behaviour |
|---|---|---|
| Workspace | `Tab`, `Shift+Tab` | Move between visible interactive sections. |
| Task or attention list | `Up`, `Down` | Move the selection and keep it visible. |
| Selected task | `Enter` | Open shared detail in a screen and preserve the browser context underneath. |
| Detail, section or evidence | `z` | Expand the focused section to the available body area. |
| Detail, expanded view or modal | `Escape` | Return to the previous view and restore its focus. |
| Workspace or detail | `Ctrl+P` | Open the shared palette with the current task and role context. |
| Workspace | `/` | Focus task search. |
| Workspace | `?` | Show context-aware help and all available bindings. |
| Escalation entry | `r`, `o` | Open resolution or the full packet. |
| Dialogue or timeline entry | `Enter` | Show its complete text and source. |
| Scrollable list or evidence | `PageUp`, `PageDown`, `Home`, `End` | Navigate the current content. |

Printable shortcuts act only outside text inputs. Escape first cancels an input's transient interaction before leaving its form. Mouse activation and keyboard activation have identical effects.

Resize changes use the dashboard pane dimensions and keep the selected task, search, focus and reading scroll. Focused browser rows, detail actions and form fields stay in view; the compact palette scrolls the focused field into view and keeps its Run control available.

Reserve a compact footer for the focused section and its available controls. Keep the complete key bindings in help so the footer stays bounded in narrow panes. Focus uses a border or marker plus a text cue under `NO_COLOR`. New task data never steals focus or opens a modal automatically.

tmux keeps its normal bindings. `prefix + Space` remains the conditional palette popup. The binding resolves the originating pane ID before launching the popup; the palette derives task and role through the existing project/pane mapping. The home context offers a task picker. No global key overrides or automatic pane rearrangement are added.

## 8. Contextual palette and action semantics

The palette offers a searchable action list and a form for the chosen action. The dashboard and tmux popup use the same screen, commands and results. Forms start with the current task, and with the current role when it is known. In task detail, a role-specific action defaults to the selected role; otherwise offer the acting role and an explicit role selector. Every target is visible and editable.

Task selection searches IDs and titles through bounded pages, shows the match count and a More control, and reads current stored state on each search. The selected target stays visible in the editable ID field and picker even when it falls outside the current search. Dependencies use task selection with the existing graph validation. Advanced submission fields, including submission and role rung overrides, remain available in an expandable area.

Applicable actions and their disabled reasons are prepared by the operator layer using current domain state. The engine remains authoritative: refresh before dispatch and display a rejection if state changed. Do not duplicate eligibility rules inside widgets or automatically retry a rejected mutation.

| Action | Explanation next to the form |
|---|---|
| Submit | Creates a task; show its allocated ID and eventual acknowledgement. |
| Prioritise | Changes dispatch priority; it does not pre-empt a running task. |
| Pause | Holds the task before its next operation according to the workflow contract. |
| Pause now | Stops the current turn and holds the task; sessions persist. |
| Resume | Clears the pause hold; remaining ownership, quota and dependency restrictions still apply. |
| Message | Queues guidance for the selected role's next turn; no immediate input injection. |
| Change rung | Applies to the selected role's next invocation; does not change the model of an in-flight process. |
| Take over | Stops automation for this task and opens the selected role's native session. Other tasks continue. |
| Hand back | Returns this role to automation; other taken-over roles and holds may still prevent progress. |
| Resolve | Applies one of this escalation's allowed resolutions with its recorded consequences. |
| Retry | Requests the workflow's retry behaviour; does not bypass holds or prerequisites. |
| Cancel / stop | Shows the affected task or project, work-preservation policy and scope of the action before submission. |

Cancellation and stop use an explicit form submission, with their consequences visible; avoid additional generic confirmation screens. Work-preservation messaging follows the completed Phase 7b rules and reports any preserved worktree path returned by the engine.

## 9. Command feedback and ownership

Show the command lifecycle as **Sending → Pending → Applied or Rejected**. Pending displays the durable command ID and target. A local acknowledgement distinguishes an engine-down queue from an elapsed acknowledgement wait; an elapsed wait is not a rejection. Recovered pending rows show current engine liveness and mark the original wait context unknown. Applied means the engine applied that command's effect; it does not mean an agent consumed a message or completed the task.

Maintain a one-line latest-command summary and a paginated project command history. Each database read is limited to 50 rows; the history screen displays 20 rows per page with status filtering, newer/older continuation through controls or left/right keys, and a scrollable result area. Closing a palette does not stop observation of a pending command, and its mounted state watcher refreshes the form when another client records an outcome. Reopening the form checks its pending command in durable rows, including forms previously submitted before switching actions; for Submit, matching ignores the task ID allocated by the store and still compares the entered request fields. Restarting the dashboard recovers outcomes from every history page, including commands submitted through MCP or another dashboard. Read history through `StateReader` and operator queries; the dashboard never acknowledges or edits command rows.

Disable repeated submission while that exact form's command is outstanding, retaining each pending form identity when another action is used. Leaving and returning to the view does not resubmit it, and a reopened palette reconstructs the guard from the stored pending payload. When the engine completes or rejects it, the mounted palette refreshes without user edits and releases the guard. Display the engine's returned error and target when a command is rejected. The workspace title area shows one bounded latest-command summary; full results are in the scrollable, paginated history opened from its Commands control.

Task ownership has a persistent banner, for example:

```text
 Operator owns Implementer · automation held
 Also paused · handing back alone will not resume this task
 [Hand back Implementer] [View holds]
```

Hand back issues the existing role command; View holds opens the task's hold detail. After hand back, Resume can clear a remaining pause through its own explicit command. Show all taken-over roles and the reason automation is still held after a hand-back acknowledgement.

## 10. Escalations, blockers and task families

### 10.1 Escalation decisions

Resolution presents the question, the recommended agent alternative with its consequences, and the engine's allowed resolution kinds with their consequences. The engine kinds come from the durable escalation row; answer requires guidance and retry may include a valid rung. Keep the full original packet accessible under Evidence, with timeline, reports, git facts, sessions and stream tails separately expandable when their structure is available.

Preserve the packet's full original text and the distinction between an agent's decision alternatives and the engine's allowed resolution kinds. Do not infer a command from option wording. Map selections through the existing validated resolution contract and keep the recommended marker visible.

If another Operator resolves or cancels the task while the form is open, show its new state, disable stale submission and retain entered guidance for copying. A race after submission is handled by the engine's rejection; refresh the form without submitting a replacement command.

### 10.2 Blocker explanations

| Blocker | Example information |
|---|---|
| Dependency | Blocking task ID, title and state, with navigation to it. |
| Slot | Occupied/maximum slots and the tasks occupying them. |
| Resources | Recorded gate reason and the relevant resource sample, with its age. |
| Quota | Required agent, recorded block reason, reported reset when available and quota observation age. |
| Pause | Task is paused; show Resume and remaining holds. |
| Ownership | Taken-over role names; show their Hand back actions. |
| PR | Recorded URL, known integration state and latest recorded observation. |

A quota reset is shown only when reported. Keep an engine retry/probe time separate from a provider-reported reset. A resource sample taken by the dashboard may be newer than the scheduler's decision and must be labelled as such. PR evidence does not imply live CI monitoring unless the engine recorded that state.

Show submitted age and, where a recorded transition supplies it, queued age. Do not label task creation time as time spent in the current blocker. Exact blocker durations and new long-wait alert policies are outside Phase 8.

Task 82 shows the scheduler's stored wait reason with the current available evidence. A dependency or occupying slot can be opened in another task detail screen; returning restores the prior browser selection. Slot evidence uses currently occupied slots and their tasks. Resource evidence labels the sampler's own observation age, separate from the last scheduler reason. For a running task, quota evidence requires matching current-role/rung task events and a live agent block; an account block belonging to another task, an expired block or an earlier rung does not mark this task as waiting. The provider's reported reset, including an unknown reset, remains separate from the engine retry/probe deadline. Home Running excludes the same evidenced current-turn quota waits that the Waiting browser includes. Pause and ownership evidence retains each hold and taken-over role; PR evidence comes from recorded PR events and does not claim live CI state. Detail shows submitted age from task creation and requeued age only when `requeued_at` is stored; it reports no blocker age or start estimate.

### 10.3 Families

Group children under their parent and allow expand/collapse. Show dependency links separately: parentage is not itself a scheduling dependency. Show a superseded task and its replacements when recorded evidence identifies them.

When a filter matches a child, include ancestors as labelled context without counting them as additional matches. A finished plan parent may remain as context for active children. Its successful integration is not completion of the family's implementation. Family summaries show counts by outcome, including cancellations and superseded work; they do not become a task completion percentage.

Task 82 keeps the browser's existing SQL match order and exact counts. Matched ancestors appear in a separate context area, outside the selectable rows. Expandable family detail shows the selected task's direct children, actual parent and family root, then offers bounded continuation pages for children, dependencies, dependents and recorded replacements. Recursive family outcome counts remain rooted at the family root; dependencies and dependents are separate links. A task is a replacement only when the superseded task's recorded scope-change event names it. Cancelled replacements remain visible with their cancelled outcome. Relationship rows, counts, root status and replacement evidence come from one SQLite snapshot.

## 11. History, timeline and evidence export

History retains access to done and cancelled tasks after windows and worktrees close while their run files and events remain within configured retention. Distinguish successful integration, operator cancellation, dependency cancellation and superseded work from their recorded reasons. Reports, decisions, integration events and engine-computed facts retain their recorded source. A rework verdict or recovered turn failure is part of a task's history, not a fabricated terminal failure status. If later retention removes a file or event, preserve the task row and show that evidence as unavailable.

Task evidence can be filtered by role, turn and category: decisions, transitions, all engine events, normalized agent signals, tools, messages, usage, or all evidence. Identify engine events versus normalized agent signals. Preserve engine event IDs and each role file's append order; use recorded UTC timestamps and stable engine/role/source-position tie-breaks when combining sources. Missing, unreadable or changed evidence sources, malformed or timestamp-less signals, and a trailing JSONL row without its newline remain explicit without hiding other role files or SQLite task state. New failure and verdict events record known execution role and iteration so role and turn filters include them; legacy events with no recorded role remain unknown. A recovered `turn_failed` event remains in the timeline as transition evidence; the task's stored terminal status and reason are authoritative.

Long timelines load in bounded pages. The default page contains 20 rows, each SQL query returns at most 50 events, and each role's normalized signal file scan is capped at 256 rows; the page and scan bounds are configurable within their fixed maxima. Filters remain applied across pages, including when a scan must advance through rows rejected by the filter. Live evidence follows the latest page until the Operator opens an older page or scrolls away from the timeline tail, which pauses following and preserves the visible reading position. Provide an explicit Follow latest action. Incoming events do not change a paused page or replace the selected evidence identity; following and paging also retain that identity when it leaves the visible page.

Export a selected task's recorded evidence as Markdown or JSON from the task view or palette. Default contents are request, status/reason, relationships, timeline, validated reports/verdicts, recorded git facts, session/rung metadata and usage with provenance. Normalized signal excerpts are an explicit inclusion option. Export records its format version, export time, selected task and unavailable sections.

Export is a local file operation through an injected service; it does not append a workflow command or mutate task state. Choose a destination, show success or the write error, and require an explicit overwrite choice for an existing file. JSON output is machine-readable; Markdown escapes or fences untrusted text so embedded content cannot alter the report's structure.

The export describes Shikiru's recorded evidence. It is not a complete agent transcript, a reproduction of deleted worktrees, or a way to rerun an execution. Timed playback, new recording infrastructure, HTML reports and automatic external delivery are outside Phase 8.

## 12. Usage and diagnostics

Usage is scoped explicitly: project recorded totals or the selected task. Show input/output/cache tokens, reported cost and basis, context only from a reliable agent report, and reported quota windows with reset countdown and observation age. Token rows state explicit, legacy and unknown run coverage. New runs are unknown until `UsageReported`; positive legacy token fields are retained as legacy recorded totals, while all-zero legacy token defaults remain unknown. Known legacy billed/API-equivalent costs and stored context remain visible with legacy labels even when token provenance is unknown. Quota-only agents have no measured token totals. A selected task shows its own run totals and sessions; project totals cover recorded runs without a time-period label.

Show unknown values as `–` with an explanation. A zero is a measured zero. Separate billed cost from API-equivalent cost. When some runs lack cost, label each known subtotal as partial and state priced and unknown run coverage; never show it as complete project spend. Do not sum different cost bases into an unexplained dollar total. Avoid ETA, guessed percentage complete, savings claims and prices inferred from token counts.

Project totals are not labelled Today or This week without actual time-bounded queries. Daily trend charts and new billing controls are outside Phase 8; the first workspace uses existing recorded totals and quota gauges.

Diagnostics contains host resources, this instance's process-tree measurements, Docker, open recorded engine-fault streaks and observation ages. It is reachable without occupying the primary task area. Explain absent Docker and failed samples in diagnostics while keeping unrelated task data and task navigation readable. Empty process trees, unavailable Docker, stale observations and failed samples have distinct presentations. A stale sample keeps its actual observation time and includes the latest sampling error; a missing sample has no invented timestamp.

## 13. Visual system

Use one shared semantic palette across Rich renderables, the Textual shell, forms, modal screens and tmux decorations. Keep Leader violet, Implementer cyan, Helper sand, Operator magenta and engine silver; success, rework, hold and alert retain their meanings. English carries meaning, and kanji remain decoration.

Add named tokens for background, panel surface, neutral border, primary/secondary text and focus. Use muted neutral chrome around ordinary sections, stronger focus treatment for the active section, and state/role colour on the relevant labels and marks. Keep titles, padding and spacing consistent. Bright warnings should identify attention, not routine queueing.

The comic strip appears expanded for the selected task. Compact rows keep task identity and state. Speech bubbles remain useful for reports and verdicts; escalation splash treatment remains available within the decision view. Use the existing width-safe glyph vocabulary and textual alternatives.

Motion retains the existing signal-driven spinner, diagnosed stall, verdict/merge flash and escalation pulse rules, capped at 4 Hz. No continuous queued-task shimmer or animation that implies activity without evidence. `animations = false` gives static frames; countdowns, task state and ownership still refresh. Respect `NO_COLOR` and `TERM=dumb` with words, markers and focus styling that does not depend on color. Bounded normalized signal tails may update during motion; timeline pages keep their own interval, and report/facts/packet files are reused until their file identity changes.

## 14. Responsive layout and accessibility

Choose layout from the dashboard pane's available width and height, not the outer terminal. The default task system pane may be only 36–48 columns on an ordinary terminal. Layout limits are named implementation constants, not operator settings.

| Available space | Behaviour |
|---|---|
| Browser and useful detail both fit | Show them side by side with independent bounded scrolling. |
| Only one useful content area fits | Show browser or detail, with explicit back navigation. |
| Short pane | Collapse cards to one line, prioritise situation/attention and keep the body scrollable. |
| Narrow task pane | Render ID/title, state/hold/blocker and role on separate compact lines. |
| Extremely small pane | Show a compact identity/state and accessible navigation/help; explain when more space is needed for a form. |

The shared Home browser/detail split starts at `HOME_SPLIT_MIN_COLUMNS` (100 columns) and requires a pane taller than `SHORT_PANE_MAX_ROWS` (18 rows). Otherwise Home stacks the existing browser and shared detail panel in one scroll area. Its split height is clamped to `HOME_SPLIT_MIN_ROWS` (8) through `HOME_SPLIT_MAX_ROWS` (32). At `COMPACT_LAYOUT_MAX_COLUMNS` (59 columns) or a short pane, summaries collapse to one line. At `VERY_SMALL_MAX_COLUMNS` (23 columns) or `VERY_SMALL_MAX_ROWS` (9 rows), navigation uses one row and the browser list shrinks to its tiny form. The compact footer remains four rows, with a two-row control strip; at 40 columns or less its command-history button reads `Cmds`. Compact detail is bounded within Home and its body remains scrollable. The palette reduces request, message and answer text areas to two rows at 18 rows or less and scrolls the focused field into view.

The interaction matrix covers 120×40, 48×20, 36×20, 48×14 and 20×8 panes, including `NO_COLOR`, `TERM=dumb` and static animation. The tested 20×8 layout keeps compact identity and navigation reachable; longer evidence and forms require scrolling, and comfortable form entry needs more rows. The supported glyph policy is unchanged: ambiguous-width CJK terminal settings remain unsupported.

Define named layout limits in the shared UI implementation when they are introduced. Add configuration only for an actual Operator choice; do not create a breakpoint tuning surface. Resizing does not automatically change the user's tmux splits.

Never solve overflow by silently dropping the lower panels, selected item, task title, critical hold or submit/result controls. Focused content scrolls into view. Full titles and text are reachable even where the preview truncates. Colour, animation or a decorative glyph is never the only way to identify a decision, hold or result.

## 15. Implementation boundaries and compatibility

| Layer | Responsibility |
|---|---|
| `domain` | Existing workflow rules; any new pure projection policy that expresses a domain invariant. |
| `store` / `StateReader` | Read-only task, relationship, event and command-history queries with bounded loading. SQL stays here. |
| `operator` | Task/evidence/command queries, action applicability, typed commands and evidence export use cases. |
| `dashboard.views` | Ready-to-render workspace projections, current context and observation metadata. |
| `dashboard.contract` | Additive view models and navigation/action callbacks for widget plugins. |
| `dashboard` | Screens, selection, focus, forms, responsive composition and local navigation state. |
| `ui` | Semantic styling, compact rows, strips, gauges and evidence renderables. |
| `bootstrap.py` | Constructor injection and wiring, including navigation to current tmux targets. |

Widget plugins receive view models and callbacks; they do not read the database, inspect agent formats, execute tmux or construct domain commands. Preserve existing widget names, entry points, fields and callbacks. Add fields with compatible defaults and add capabilities when a task consumes them. Do not replace the stable plugin boundary with a screen framework.

The shell assigns built-in widgets to the home/task sections defined here. Configured order is respected within each section. Existing third-party widgets mount in a reachable Extensions section unless they adopt an additive placement capability. Configured widgets must not disappear silently; unknown or broken plugins continue to fail validation with their names. Defaults and placement behaviour are documented with the implementing task.

State subscriptions belong to mounted screens and are cancelled on unmount. Background reads, file loading and export stay off the UI event loop as appropriate. Use bounded queries and caches for expensive data; display read failures and allow a retry. A partial JSONL row is not presented as a complete signal. No new process reader competes with `TurnMonitor` for an agent's stdout.

The Task 77 detail refresh reads task, run and bounded event rows through the UI-owned SQLite connection, then loads report, verdict, facts and signal files in a worker thread. Motion callbacks update only lightweight animation data; detail file loading is triggered by mount or a state change, never by a motion tick. The detail screen cancels both refresh and state-watch workers when it unmounts. Task 78 adds palette context and task-selection callbacks to the existing widget contract, keeps action eligibility in the operator layer, and uses bounded current-state search in the palette. Task 79 reads bounded command rows through `StateReader` and operator queries; command feedback has no writer, acknowledgement path or navigation-state table. Task 82 adds bounded snapshot-consistent relationship reads and latest resource-sample metadata to `StateReader`-backed views; related-task navigation reuses the existing detail callback and preserves the originating Home selection. The detail banner lists every taken-over role and remaining hold, with separate contextual Hand back and Resume commands.

No workflow commands, network service, generic replay framework, agent pricing table or widget preference database are introduced for this redesign. Any mutation continues through `OperatorCommands`; exporting a file and local navigation are the explicit exceptions that affect no engine state.

## 16. Validation and delivery

Implement Phase 8 after Phase 7b's operator-control and work-preservation fixes. Each task delivers one usable slice and updates the affected design sections. Refactorings, if needed, are separate commits. Keep all Phase 8 ACs open until their implementation and required verification are complete.

Behaviour verification uses real SQLite, Git and the isolated `tmux_server`, Textual `run_test`, and the fake agent. Wait with `wait_until` and inject `FakeClock`; do not mock Shikiru classes. Unit tests cover new domain rules or parsers, not theme token listings, dataclass shapes, Textual features or wording. Keep the existing snapshot limit.

Task 85 visual review used rendered Textual SVGs from the integration harness. At 120×40, Home shows the browser and shared detail in adjacent independently scrolling panes with the footer visible. At 36×20, the task identity and state remain above a vertically scrollable evidence body, and the horizontal toolbar exposes each enabled action when focused. At 36×20 and 48×14, the palette keeps the focused Message text area and Run action within the form frame; the shorter pane uses a two-row text area. The 48×14 detail form retained its text and focus while the viewport scrolled it into view.

Required walkthroughs cover:

1. Find a task by title, inspect its latest verdict, message its role and observe the durable command outcome without typing its ID.
2. Keep task identity, focus and reading position while another task changes state, a list reorders and the pane resizes.
3. Resolve an escalation with evidence and consequences; handle another Operator resolving it while the form is open.
4. Follow dependencies and a decomposed family without treating the plan parent's completion as completion of its children.
5. Take over one role, verify that another task progresses, hand back and explain any remaining pause or ownership.
6. Queue a command with the engine stopped, close the dashboard, restart both and observe one applied effect.
7. Inspect a completed, cancelled and superseded task after its window closes; export available evidence and mark missing evidence.
8. Operate a wide home view, 48- and 36-column task panes, a short pane, `NO_COLOR`, and disabled animation without losing critical state or controls.

These UI walkthroughs use the fake agent and existing engine semantics. Real-agent E1–E6 remain the release gate for workflow regressions; UI work does not require inventing new paid-agent scenarios. Run `uv run poe check` before every commit, preserve the coverage gate, and record final verification against an exact source revision.

Phase 8 finishes with updated design, README navigation/usage examples and a release-readiness record. The record states what shipped, tested sizes and environments, workflow verification, remaining evidence limits and any unverified platform. Documentation describes available behaviour and links to this specification for its rationale.
