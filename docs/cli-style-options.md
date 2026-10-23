# CLI visual directions

This document records the five visual directions and the selected Focus ledger policy. Native application behavior is normative in [Operator interfaces](design/06-operator.md); this document and the [interactive comparison](design/references/cli-style-options.html) provide design rationale and examples. The self-contained HTML page is an offline browser prototype using sample data and mock flows for setup, submission, status, diagnostics and attachment. It runs no shell commands, reads no projects and changes no configuration. Its output, interactions and browser rendering do not override native CLI behavior.

**Selected: Focus ledger (2026-10-18).** The user chose this direction and requested concise, command-specific output while retaining every necessary detail. Native Typer help keeps its overall composition, command order and defaults, with readable section titles, useful visible argument and option descriptions, and one required-argument legend. The HTML defaults to the refined Focus ledger; the earlier alternatives remain available for comparison. These are design alternatives, not five proposed application themes. Native CLI and TUI behavior follows the operator design; the HTML stays illustrative.

| Direction | Primary structure | Main strength | Main cost |
|---|---|---|---|
| Focus ledger | Open sections and aligned rows | Continuity with approved Home | Subtle hierarchy needs careful spacing |
| Project dossiers | One vertical group per object | Self-contained context and long paths | Taller output; slower count comparison |
| Signal rail | Semantic glyphs on a left rail | Distinctive progress/result rhythm | Glyph density and lost horizontal space |
| Yonkoma notebook | Four-cell readouts and workflow strips | Strongest strip identity | Width and chrome for routine commands |
| Command folio | One frame per complete report | Clear boundary across command families | Copying noise and wrapping pressure |

Focus ledger balances frequent `ls` and `status` reading with short lifecycle outcomes, diagnostics and help. Dossiers are the strongest alternative when narrow terminals and long paths dominate. The recommendation concerns composition; all five retain the same palette and semantic facts.

## Selected output policy

The original composition sketches below were selected on 2026-10-18. The 2026-10-20 second review supersedes conflicting first-review details in the sketches and in Tasks 256–263. Keep required evidence within each command's responsibility, with conditional detail when it is present. Add no new verbosity flags for this design work. The selected native behavior is recorded in [Operator interfaces](design/06-operator.md).

| Command | Concise output scope |
|---|---|
| Help | Preserve native Typer layout and command order. Options, Commands and Arguments titles are bold white; visible options and arguments have useful descriptions, metavars use conventional type/enumeration forms, defaults use readable prose with only `Default:` bold, and required arguments keep one `* required` legend. Long help text can ellipsize in the native three-column layout at narrow widths, including 60 and 40 columns. Hidden process entrypoints stay hidden. |
| `ls` | At 65 columns and above, align Session/Engine/Running/Queued/Escalated columns with each full path below its row; below that, stack identity, path and all engine/count facts. Do not add a Sessions heading. Show remote host once. |
| `attach [NAME]`, `--host H` | Attach directly to the nearest initialized current project, including from a descendant directory or explicit `--project`. Explain how to start it if its session is absent. Outside a current project, use a numbered terminal list and validated prompt when several sessions exist. Explicit names and remote hosts remain authoritative. |
| `status` | One compact header with project, engine and known uptime, slots, queued and escalation counts, and run-directory space used. Show one Tasks list of at most ten rows: escalated, other active, queued in dispatch order, and the latest completed task last when available. Keep fault evidence and one per-agent usage table with complete costs or `unknown`, and unlabeled primary/secondary quota gauges in that order. Omit oldest-escalation age, a separate task queue and the human run-directory count. Header counts and JSON/MCP collections remain complete. |
| `status T-n` | That task's ID/title and state, blocker/outcome, retained reports/verdicts, timeline, branches, sessions and evidence errors. Keep task scope and omit project-wide usage and other project noise. |
| `doctor` | One compact row per visible check: semantic glyph, readable check name and complete detail, followed by an honest visible-check count. `--probe` retains probe stream tail/stderr; `--json` keeps every existing check and its schema. |
| `start` / `stop` | Successful lifecycle output is the single outcome `Engine started`, `Engine already running`, `Engine stopped` or `Engine already stopped`. Pending and rejected acknowledgements retain their command identity and truthful reason. |
| `init` | Full-screen Focus dashboard with the subdued `しきる` mark behind a stable centered panel. Keep the seven-step flow and reusable controls. Leader and Implementer use ordered ladder editors; Implementer supports inherited or explicit low/normal/high overrides, and Helper has exactly one rung. Finish uses the same Doctor renderer as `doctor` and shows the written absolute config path and complete visible check details. The human post-wizard result has no placeholder warning; `--yes` keeps its required placeholder/review warning. |
| Errors | Cause, relevant context and a known recovery action; preserve error exits and any necessary startup-output evidence. |
| Submit/version/JSON/MCP | Existing bare task ID, bare version or protocol payload. No extra presentation text. |

The HTML's embedded help captures are historical actual-CLI output from before Task 260. They are retained as proposal evidence, not current help output. Current Typer help is authoritative and follows the row above. Help does not use the prototype's generic framing helpers.

## Shared visual contract

The source of truth is the current [shared palette](../src/shikiru/palette.py), [glyph registry](../src/shikiru/ui/glyphs.py), [UI tokens](../src/shikiru/ui/tokens.py), [operator design](design/06-operator.md#61-tokens-and-glyphs) and [approved Focus Steel reference](design/references/focus-steel-approved.html). These compositions vary layout while preserving shared colors and command behavior. This contract guides the native implementation; the preview only illustrates it.

| Token | Exact color | Treatment |
|---|---|---|
| Background | `#11151b` | Graphite application/frame background |
| Surface | `#171d25` | Ordinary contained surfaces |
| Selected surface | `#233340` | Interactive selected rows; no added selection glyph |
| Detail surface | `#1c2530` | Read-only expanded evidence |
| Neutral border | `#3d4654` | Rules and report edges |
| Primary text | `#dce2e9` | Main text and identifiers |
| Secondary text | `#9aa5b5` | Labels, paths and supporting explanations |
| Steel blue | `#83bddd` | Brand, keys, focus and observed activity |
| Sage | `#90cba6` | Successful outcomes and reported billed amounts |
| Amber | `#dfb16c` | Holds, decisions, subscriptions and estimates |
| Dusty rose | `#e3a0a0` | Errors and diagnosed stalls |

Interactive scrollers retain the existing thumb `#344b5d`, hover `#415e72`, active steel and track `#000008`. Ordinary one-shot output does not repaint the full terminal background. Selection surfaces belong to the full-screen initializer and numbered interactive attach prompt, not static report rows. Existing semantic renderables must be reconciled against the current design rather than introducing a separate CLI-only status palette.

Sentence case applies to headings, actions and hints. The only uppercase title exception is **SHIKIRU**, in bold steel. Identifiers and useful primary figures are bold; prose remains regular; secondary information is muted. Keys are bold steel and their explanatory labels keep their own style. Roles share the same quiet steel accent.

The approved HTML terminal uses `12px/1.65 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace`. Use that stack in the comparison preview. A real CLI inherits its terminal's font, font size, line height and cell dimensions. The implementation can control regular/bold/dim attributes, spacing, indentation and alignment; it cannot impose a browser font, CSS letter spacing or arbitrary text sizes.

| Meaning | Registered glyphs |
|---|---|
| Stages | `⌕` triage, `≡` plan, `⚒` implement, `⚖︎` verify, `⑂` merge, `⇪` pull request |
| Actors | `◆` leader, `▲` implementer, `✦` helper, `⌨` operator, `⚙︎` engine |
| State | `✔` done, `↺` rework, `?` consult, `⏸︎` paused, `⧗` quota, `⚠︎` stalled, `‼` escalated, `⊘` blocked, `·` pending, `✕` stopped |
| Current stage | `›` |
| Quota gauges | `○ ◔ ◑ ◕ ●` |
| Spinner | `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` |

Words carry meaning alongside glyphs. Keep the text-presentation selector on `⚖︎`, `⚙︎`, `⏸︎` and `⚠︎`; do not add emoji or require a patched font. The current one-cell ambiguous-width limitation remains. Kanji may remain decorative inside the retained task strip only. Quota is the only gauge; CPU, RAM, task counts and slot occupancy remain textual figures.

## 1. Focus ledger

One identity line introduces an open report. A factual summary comes first, followed by neutral rules, quiet section labels and borderless aligned rows. Horizontal comparison is primary; long secondary text wraps below its owning row.

```text
SHIKIRU · projects

Session                          Engine   Running Queued Escalated
[shikiru] shikiru                ⚙︎ up          2      3         1
  ~/Projects/shikiru
[shikiru] website                ⚙︎ down        0      1         0
  ~/Projects/website
```

```text
demo · ⚙︎ engine up (5d 4h 53m) · slots 3/4 · queued 10 · escalations 2 · space used 16.1 MiB

Tasks
T-1 · Choose an integration strategy · Escalated
T-2 · Review the migration constraints · Escalated
T-3 · Add ad states and verify the feature · Implementing
T-4 · Preserve literal [review] IDs in report views · Verifying
T-5 · Verify the account settings flow · Verifying
T-15 · Queued follow-up 10 · Queued
T-14 · Queued follow-up 9 · Queued
T-13 · Queued follow-up 8 · Queued
T-12 · Queued follow-up 7 · Queued
T-16 · Publish the verified session report · Done

Escalations
E-1 · T-1 · The Leader needs an Operator decision to continue.
E-2 · T-2 · The Leader needs an Operator decision to continue.

Usage
Agent   Runs  Input  Output    Costs  Quota used
claude     2  16.0k    4.2k    $1.84  five hour ◑ 52% · seven day ● 92%
codex     76  83.6M  673.9k  unknown  ○ 10% / ○ 2%
```

```text
┌────────────────────────────────────────────────────────────┐
│ SHIKIRU · initialize       Step 2 of 7 · Agents and ladders │
│                                                            │
│ Role        │ Leader                                        │
│ Leader base ladder                                          │
│ Ordered rungs               Edit rung 1                    │
│   1 fake-copy · strong · … Agent                           │
│   2 fake · default          │ fake-copy                    │
│ Add Remove Up Down          Model                          │
│                             │ strong                        │
│                             Effort                         │
│                             │ high                          │
│                                                            │
│ Choose only listed models and reported efforts.            │
│ Tab/Enter select · PgUp/PgDn scroll · Esc cancel            │
│   Back     Next     Cancel                                  │
└────────────────────────────────────────────────────────────┘
```

**Strength:** closest continuity with Home, quick comparison across many rows and minimal overhead for small commands. **Tradeoff:** quieter identity; effective hierarchy depends on disciplined spacing. Doctor uses one glyph, readable check name and full detail per line, followed by its result count; help keeps Typer's overall composition. At 65 columns and above, session counts align and each path follows its row; narrower terminals stack the identity, path and complete labeled facts.

## 2. Project dossiers

Each project, task or configuration object gets a self-contained vertical group: bold object name, indented facts and a neutral divider. Reading moves between objects rather than across a shared table.

```text
SHIKIRU · projects

shikiru
  ~/Projects/shikiru
  ⚙︎ Engine up · running 2 · queued 3 · escalated 1
───────────────────────────────────────────────────
website
  ~/Projects/website
  ✕ Engine down · running 0 · queued 1 · escalated 0
```

```text
SHIKIRU · shikiru
  ⚙︎ Engine up · slots 2/4 · queued 3 · escalations 1

T-12 · Add retry handling
  ⚖︎ Verify · ◆ Leader
  ‼ E-3 · T-12 · Review required · open 4m

T-15 · Improve onboarding
  ⏸︎ Paused · ▲ Implementer
```

```text
SHIKIRU · initialize shikiru

  Repository · ✔ ~/Projects/shikiru · main
  Agents · step 2 of 7

  Leader         [claude:                    ]
  Implementer    [codex:                     ]
  Helper         [claude:                    ]

  Choose a model for each role.
  Enter next · Esc cancel
```

**Strength:** strong ownership grouping, natural wrapping for paths and descriptions, comfortable narrow output. **Tradeoff:** taller reports and slower comparison of counts across many projects. Doctor places failure details under each check; lifecycle results stay concise. Help uses Typer's current composition with readable section titles, useful visible descriptions and one required-argument legend.

## 3. Signal rail

A slim left rail carries semantic glyphs while content aligns after it. Neutral vertical segments connect related facts. Snapshot rows are not timestamped and must not imply newly occurring events.

```text
SHIKIRU · projects

⚙︎  shikiru · engine up
│   ~/Projects/shikiru
│   running 2 · queued 3 · escalated 1
│
✕   website · engine down
    ~/Projects/website
    running 0 · queued 1 · escalated 0
```

```text
SHIKIRU · shikiru

⚙︎  Engine up · slots 2/4 · queued 3 · escalations 1
│
⚖︎  T-12 · Add retry handling · Verify · ◆ Leader
│
⏸︎  T-15 · Improve onboarding · Paused · ▲ Implementer
│
‼   E-3 · T-12 · Review required · open 4m
```

```text
SHIKIRU · initialize shikiru

│   Repository · ✔ ~/Projects/shikiru · main
›   Agents · step 2 of 7
│
│   Leader         [claude:                    ]
│   Implementer    [codex:                     ]
│   Helper         [claude:                    ]
│
│   Choose a model for each role.
│   Enter next · Esc cancel
```

**Strength:** distinctive instrument-like rhythm and useful continuity between work in progress and final outcomes. **Tradeoff:** glyph density can become busy, and the rail consumes width. The current-stage marker identifies a wizard stage, never selection in the picker. Errors put the factual failure on the rail; probe progress updates the current check and retains completed outcomes. Help uses Typer's current composition with readable section titles, useful visible descriptions and one required-argument legend.

## 4. Yonkoma notebook

Workflow strips are the defining feature, supported by open notebook sections for non-workflow commands. Task detail retains the meaningful four-panel lifecycle rather than replacing it with generic cards. Project counts can use four information cells, clearly labeled as counts rather than workflow stages.

```text
SHIKIRU · projects

shikiru · ~/Projects/shikiru
┌──────────────┬──────────────┬──────────────┬──────────────┐
│ ⚙︎ Engine up  │ Running 2    │ Queued 3     │ Escalated 1  │
└──────────────┴──────────────┴──────────────┴──────────────┘
website · ~/Projects/website
┌──────────────┬──────────────┬──────────────┬──────────────┐
│ ✕ Engine down│ Running 0    │ Queued 1     │ Escalated 0  │
└──────────────┴──────────────┴──────────────┴──────────────┘
```

```text
SHIKIRU · shikiru

┌──────────────┬──────────────┬──────────────┬──────────────┐
│ ⚙︎ Engine up  │ Slots 2/4    │ Queued 3     │ Escalated 1  │
└──────────────┴──────────────┴──────────────┴──────────────┘
T-12 · Add retry handling · ⚖︎ Verify · ◆ Leader
T-15 · Improve onboarding · ⏸︎ Paused · ▲ Implementer

‼ E-3 · T-12 · Review required · open 4m
```

```text
SHIKIRU · initialize shikiru

Repository · ✔ ~/Projects/shikiru · main
Agents · step 2 of 7

Leader         [claude:                    ]
Implementer    [codex:                     ]
Helper         [claude:                    ]

Choose a model for each role.
Enter next · Esc cancel
```

**Strength:** most distinctive expression of Shikiru's original visual identity and useful lifecycle storytelling. **Tradeoff:** highest width cost, more chrome and a less natural fit for generic commands. Initialization keeps its real seven steps; do not invent a four-stage wizard to match the strip. Doctor stays a check list; help uses Typer's current composition with readable section titles, useful visible descriptions and one required-argument legend. Ordinary acknowledgements stay short, with panels reserved for useful workflow structure. Narrow task strips collapse into the existing one-line form.

## 5. Command folio

One rectangular frame encloses a whole command result. Internal rules divide sections; rows share one report instead of becoming separate object cards. Report borders are neutral; interactive wizard and picker borders use steel.

```text
┌ SHIKIRU · projects ────────────────────────────────────────┐
│ Project    Engine   Running  Queued  Escalated             │
│ shikiru    ⚙︎ up           2       3          1             │
│ ~/Projects/shikiru                                         │
│ website    ✕ down         0       1          0             │
│ ~/Projects/website                                         │
└────────────────────────────────────────────────────────────┘
```

```text
┌ SHIKIRU · shikiru ─────────────────────────────────────────┐
│ ⚙︎ Engine up · slots 2/4 · queued 3 · escalations 1         │
├────────────────────────────────────────────────────────────┤
│ Tasks                                                      │
│ T-12  Add retry handling  ⚖︎ Verify · ◆ Leader              │
│ T-15  Improve onboarding ⏸︎ Paused · ▲ Implementer          │
├────────────────────────────────────────────────────────────┤
│ Escalations                                                │
│ ‼ E-3 · T-12 · Review required · open 4m                   │
└────────────────────────────────────────────────────────────┘
```

```text
┌ SHIKIRU · initialize shikiru ──────────────────────────────┐
│ Repository · ✔ ~/Projects/shikiru · main                   │
│ Agents · step 2 of 7                                       │
│                                                            │
│ Leader         [claude:                    ]               │
│ Implementer    [codex:                     ]               │
│ Helper         [claude:                    ]               │
│                                                            │
│ Choose a model for each role.                              │
│ Enter next · Esc cancel                                    │
└────────────────────────────────────────────────────────────┘
```

**Strength:** consistent containment across reports, help, errors and forms. **Tradeoff:** frames add copying noise and width pressure, and can overwhelm a one-line result. Do not nest boxes. Help uses Typer's current composition with readable section titles, useful visible descriptions and one required-argument legend, without an extra folio frame. One-line lifecycle results and short errors remain unframed. At constrained widths the frame must yield space to the complete text.

## Command and state coverage

This matrix records the selected native presentation boundaries. The browser preview shows representative states but does not implement or override the matrix. Composition changes preserve payloads, side effects, acknowledgements and command eligibility.

| Surface | Required treatment in every direction |
|---|---|
| Global options | Preserve `--project PATH`, `--json`, `--set k=v`, `--version` and existing parsing/placement behavior; style documented flags consistently in help. Do not invent flags for a preview control. |
| Root and command help | Preserve the implemented Typer composition, command order and defaults, with readable Usage, Arguments, Options and Commands titles, useful descriptions for visible arguments/options, one required-argument legend and the shared palette and text attributes. At narrow widths, including 60 and 40 columns, the native three-column layout can ellipsize long help text. Add no masthead, new command groups or enclosing frame. Hidden commands stay hidden. Usage errors retain their cause, relevant usage and exit 2. |
| Version | Preserve the bare version result; no masthead or report chrome. |
| `ls`, `--host H` | Preserve session/project identity, directory, engine state and running/queued/escalated counts. Show remote host context when relevant. Counts retain their existing meanings; do not relabel escalated tasks as Home's grouped attention count. |
| `attach [NAME]`, `--host H` | Direct attachment has no decorative interstitial. From an initialized current project, attach its session directly and show start guidance if absent. Elsewhere, list sessions in the same plain numbered format used for selection, then validate the chosen number. Explicit names and remote hosts remain authoritative. Preserve the exact local/remote attachment arguments. |
| `init` | Seven-step full-screen Textual Focus app: repository, agents, trackers, caches, parallelism, integration mode and finish. Agent choices use the ordered Leader/Implementer ladder editor; Implementer supports inherited or explicit low/normal/high ladders, and Helper has exactly one rung. Preserve catalog-backed model/effort choices, validation, drafts and focus. Finish uses the same Doctor renderer as `doctor` and shows the absolute written path and full details for every visible result, including failures. |
| `init --yes` | Show written config path, editable default-model placeholder warning and the required review/doctor next step. Writing config does not establish that the project is ready to start. |
| `start`, `--attach` | Successful output is only `Engine started` or `Engine already running`. Preserve session collision hints, lock readiness and early-exit diagnostics including engine output. `--attach` remains the final transition and prints no result after replacing the CLI with tmux. |
| `stop`, `--kill-session` | Successful output is only `Engine stopped` or `Engine already stopped`, after the existing shutdown guarantee. Pending and rejected acknowledgements retain their command identity and truthful reason. |
| `submit` and its existing flags | Preserve bare allocated `T-n` on human stdout. If no task ID is available, preserve the command acknowledgement. Do not add a brand banner around this result. |
| `status` | Keep the compact project/engine/uptime/slots/queue/escalation/space header, a single bounded Tasks list, escalation rows, fault scope/task/cause/times/repeats/recoverability, the compact per-agent usage table and the relevant stopped engine-output path. Omit oldest-escalation age, a separate queue list, redundant quota/provenance sections and the human run-directory count. JSON/MCP status collections and fields remain unchanged. |
| `status T-n` | Retain task strip or equivalent factual workflow presentation, timeline, reports/verdicts, branches and sessions. Distinguish pending, live, waiting and unknown facts. |
| `status --watch` | Preserve database-driven refresh. Interactive rendering may redraw in place; non-TTY output must remain linear and free of cursor control. No decorative spinner should imply a live agent turn. |
| `doctor`, `--probe` | Checks, outcome and useful detail; probe failures retain stream tail/stderr. Treat informational engine output as information, not a failed check. Distinguish reported unknowns from passed checks. |
| Command outcomes | Keep pending/applied/rejected distinct. Engine-down queueing and elapsed acknowledgement wait have different explanations; timeout is not rejection. Preserve returned result/error and command identity where available. |
| Errors | Diagnostic errors show a readable cause and actionable recovery when known, on stderr. Rejected command acknowledgements retain their existing stdout path. Preserve exit codes: 0 success, 1 error, 2 usage, 3 not initialized, 4 command rejected. Do not introduce a new structured-error protocol as part of visual work. |
| Empty and unavailable data | State explicit empty cases such as no sessions, no queued tasks or no open escalations. Missing usage is unknown, never zero. Distinguish unavailable/error from an empty successful query. |
| One-shot `NO_COLOR`, `TERM=dumb` and non-TTY | One-shot human reports are monochrome and linear, with readable words and glyphs and no dependence on color. Preserve useful weight cues where terminal capabilities allow; non-TTY reports have no painted surfaces, animations, cursor control or OSC links. The full-screen Textual initializer and numbered attach prompt remain interactive. Do not require ANSI for alignment or meaning. |
| JSON | Existing raw model data only: no masthead, panels, styling, glyph decoration or success prose. Preserve field names and types. Current JSON watch output is successive model documents; changing its framing is a separate protocol task. Existing errors are not silently converted into invented JSON objects. |
| `mcp` | Stdio remains exclusively the MCP transport; no branded output or progress on protocol stdout. |
| Hidden `engine`, `dash`, `palette`, `view`, `report` | Preserve their process-entrypoint and protocol contracts. Do not wrap pane streams or exported reports in new command chrome, or expose hidden entrypoints through redesigned help. Existing shared UI token improvements may flow through intentionally. |

The five alternative direction excerpts preserve the original composition proposal; their mock status, inline wizard, lifecycle wording and clickable session picker are historical samples, not current native output. The selected native contract is the matrix above and the [operator design](design/06-operator.md). The native CLI uses OSC 8 path links only when the terminal is known to support them and always retains the full literal path; browser links do not prove terminal support. Static narrow output wraps complete facts into labeled lines; the preview's compact switch illustrates that requirement and is not a CLI option. For visual comparison, all five use the same sample facts within each command. Layout and grouping distinguish the options.

## Initial snapshot preview verification

The comparison includes 35 command/state examples, all five compositions, requested terminal widths of 80/60/40 columns, truecolor, approximate 256/16-color palettes, `NO_COLOR` and linear non-TTY one-shot reports. The reduced-color views are approximations; actual ANSI colors depend on the terminal palette. Wizard fields and attachment rows are illustrative interactive flows, and watch shows one static frame. Remote host context comes from the requested host argument; the JSON row schema gains no host field.

Historical proposal wording used Applied/Pending/Rejected labels while raw acknowledgement status values remained `done`/`queued`/`rejected`. Current successful lifecycle output is the short contract in the matrix above; pending and rejected results retain their command identity and reason. A stop acknowledgement wait can remain pending while the engine is up. `start --attach` replaces the CLI with tmux and prints no CLI result afterwards. The old session-retained/closed lines are proposal evidence, not current successful output.

Manual browser checks on 2026-10-17 exercised 2,625 combinations per run: 35 examples × five compositions × three requested terminal widths × five output modes. Checks at 320- and 360-pixel content widths found no script errors, horizontal overflow or differences in sampled task/model/usage facts between compositions; the wide comparison was also checked at 736 pixels. Bare task IDs, parseable JSON, empty MCP/handoff output, and completed-versus-pending stop states were checked. The standalone saved comparison was rendered and visually reviewed separately. These checks validate the proposal prototype; they do not constitute application integration tests or native terminal verification. No runtime files or tests changed, and no commit was made.

## Interactive HTML assessment

The [assessment page](design/references/cli-style-options.html) now works directly as a local HTML file without a server, package installation or external resources. It replaces the initial sample-switching comparison with interactive mock flows. The five design buttons preserve the same browser state, inputs and selection, so changing a composition does not restart the scenario. `Reset demo` restores the initial sample state; reloading the page also resets it.

Try these flows:

1. Choose `init`, step through the seven pages, enter explicit role models such as `codex:demo-model`, and use `Run doctor` on Finish. Invalid model ladders, helper rung count, empty comma entries and non-positive parallelism show validation. Back retains inputs, the last step focuses Run doctor, and Escape cancels. All writes and checks are simulated.
2. Choose `submit`, enter a request above the terminal and use `Run submit`. The terminal prints only the allocated task ID. `Inspect task` opens its readout; the external `Advance sample task` control moves the scenario through the stages when the mock engine has a free slot. Paused tasks keep their slot, and a completed task frees it.
3. Run `stop` or `start`, then return to `status` or `ls` to see shared engine/session state. `stop --kill-session` removes that session from discovery. Choose `doctor`, run checks, then simulate/repair a configuration issue and run them again; an unresolved issue blocks the mock start.
4. Choose `attach`: use Up/Down and Enter, or click a session, to enter its simulated destination. Escape cancels; `Detach preview` returns. When only one session remains, Run sample command attaches directly. Row facts and selected-surface cues stay available in monochrome.

Controls above the terminal are assessment controls, not proposed CLI flags or actions. The page labels interactive flows and fixed snapshot examples separately. Reduced-color views are approximations. Non-TTY suppresses interactive controls and offers `Use interactive output`; machine/protocol examples retain their existing output form. The mock is for visual and interaction assessment, not a replacement implementation of the engine.

Interactive follow-up verification: final browser checks passed 175 assertions across all five flows, including validation, focus, state persistence, shared lifecycle state, task progression and keyboard/click attachment. Sweeps of 2,625 composition/command/width/output combinations at 360- and 736-pixel content widths reported no script errors or horizontal overflow; desktop and narrow monochrome screenshots were visually reviewed. These are offline prototype checks, not application integration tests.

## Native implementation status

Tasks 256–263 delivered the first CLI review. The subsequent second-review implementation in Tasks 264–267 replaced ambiguous session picking with current-project and numbered terminal attachment, made dashboard detach select the actual tmux client, and replaced the inline initializer with the full-screen ladder editor. The [operator design](design/06-operator.md) is the normative record of native behavior. The HTML remains an offline assessment prototype; its mock data and browser flows are not evidence of runtime behavior.

Focus ledger refinement verification (2026-10-18): wide and narrow browser runs each passed 320 checks and a 2,625-combination sweep, with no script errors or horizontal overflow. The checks compare all three help examples against actual CLI captures in color, monochrome and non-TTY modes across requested widths, preserve the five mock flows and machine-output contracts, and verify concise unframed lifecycle results. Only proposal files and task tracking changed.
