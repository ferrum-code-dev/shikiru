# Command output and initializer examples

The [offline interactive review page](design/references/cli-output-examples.html) records approved human command outcomes and the initializer composition. The Operator selected Operator briefing as the default task-status proposal and approved the human `submit` result that shows an accepted task identity instead of a bare task ID. Current, Task ledger, and Evidence view remain comparison alternatives; Task 275 implements the selected status layout. Help remains unchanged.

Every terminal value is a fixture: paths, task/escalation/command IDs, session names and tmux IDs, repository facts, Codex and Claude catalogs, Doctor reports, and simulated writes. The page reads no project data and executes no commands. User-controlled values are inserted as text.

## Public command coverage

| Command | Examples |
|---|---|
| `init` | Seven steps; Run doctor writes the fixture config and displays the full passing or failing per-check Doctor report in Finish; the unavailable-provider case includes Repair. Pre-write Finish shows the config destination and selected summary without redundant Roles or Doctor fact rows. |
| `init --yes` | Initialized path; separate not-a-repository, nested-project, and no-commit causes with scoped recovery actions. |
| `start` | Started, already running, missing project state. |
| `start --attach` | Terminal handoff without an invented post-handoff CLI line; refusal when another client owns the session. |
| `stop` | Stopped, already stopped, pending acknowledgement, rejected request. |
| `stop --kill-session` | Stopped and already stopped; the result does not claim the tmux session closed. |
| `ls` | Aligned wide rows, narrow stacked rows, partial discovery with healthy project details retained, empty results, and all-unavailable diagnostics. |
| `ls --host H` | Remote success, partial failure, empty, and all-unavailable outcomes; repair rows explicitly name the host. |
| `attach` | Current project, numbered selection, busy session, retained session with missing project state. |
| `attach NAME` | Direct named attach and missing named session. |
| `attach NAME --host H` | Remote named attach and unavailable remote session. |
| `submit "REQUEST" [flags]` | Accepted, pending, and rejected human results retain command and task identity; the applied example includes mode, complexity, priority, dependency, and pinned rungs. JSON keeps its current object. |
| `submit` | Click/Typer missing-required-request error in the shared error form. |
| `status` | Project summary and task list, usage, empty project, and append-only `--watch` updates. The snapshot orders escalated, all active, queued, then latest completed tasks. Task, escalation and usage groups are subsections inside Details; status and watch share one snapshot renderer. Engine is green-bold `⚙︎ up` or normal-grey `⚙︎ down`; positive escalation counts are amber-bold. Usage columns keep intrinsic width with two spaces between them. |
| `status T-n` | Operator briefing is the selected default. It shows task identity/title/colored state, decision or blocker context first, only an informative recorded stage, latest report/event rows labelled `1 of N`, branches, and complete recorded session facts. Unknown IDs fail with a cause and a scoped project-status repair in the existing CLI error form; JSON status models and MCP contracts remain unchanged. |
| `doctor` | The native per-check report format with passing checks, wrong exact model ID (`GPT-6-Luna`, with supported `gpt-6-luna`), and invalid configuration effort (`extreme`). Doctor uses its own report format. |
| `doctor --probe` | The native per-check report with completed probes and a provider-unavailable cause and suggestion. |
| `mcp` | Command only; the protocol boundary is described outside the simulated terminal. |

Help, version, shell completion, JSON, MCP stdio, hidden process entrypoints, pane streams, and evidence exports are exceptions to human outcome formatting. Help's existing layout and wording are not redesigned. Version stays bare; protocol and machine streams stay unchanged.

## Approved human outcome and compatibility selection

Human success starts with a bold green `✔` and a short white outcome. Human errors start with a bold red `✕` and a short white cause summary. Optional `Details`, `Repair`, and `Next step` sections use bold cyan headings, grey action labels, white values or instructions, and cyan shell commands. Empty sections are omitted. Pending acknowledgements are amber and do not claim an unapplied effect. Start and stop confirmations stay on one short line. Ordinary command outcomes have no frame.

Human `submit` uses the approved shared outcome. An applied acknowledgement shows `Task created` with the allocated task ID and command identity. Pending and rejected acknowledgements keep their own state, command identity, and allocated task ID where available; neither claims that a pending task was applied or a rejected task was created. This is an approved human CLI compatibility change. JSON retains its existing object. Applied means the engine applied the command; pending does not mean a task is running.

Fixture output uses clean session names such as `[shikiru] demo`. When repair uses a shell-quoted native tmux ID, Details maps it to the clean session name, for example `[shikiru] archive` and `$7`; `tmux kill-session -t '$7'` then targets that fixture session. `$7` is synthetic and must be looked up in the intended server before use. Tmux's `=` prefix is exact-name target syntax, not part of the session name. Runtime exact targeting stays unchanged. Remote repair rows name the host, and a missing named remote session shows an inspection command scoped to that host.

## Task status comparison

Operator briefing is selected by default. Task ledger pairs the full project-style task identity row with iteration/slot, the latest report and event, branch, and complete session facts. Operator briefing puts decision or blocker context first, then recorded stage or hold facts only when informative, the latest report and event, branch-to-base facts and complete session facts. Evidence view shows every recorded report and the newest-first event history, plus literal branch/base facts and each session's role, agent, model, status, ID, and effort. Current remains available to compare the existing known `T-42` output. The shared synthetic fixtures are active `T-42`, escalated `T-41`, and completed `T-39`; completed sessions remain closed. Quota values use unlabeled gauges only for reported `5h` and `7d` windows; `primary`, `secondary` and other provider windows keep their recorded names. Task 275 implements the selected layout.

## Initializer composition

The wizard sits inside a neutral dashboard viewport. Its preferred 96×36-cell panel is clamped to the simulated terminal. A normal top row shows cyan-bold `SHIKIRU`, a white-bold step title, and the N / 7 counter, followed by the dashboard divider; only the body scrolls. The static hint follows the body, then a divider and right-aligned action buttons at the panel bottom. Global terminal header/footer rows remain absent. Ctrl+F hides the whole panel for five seconds, then restores the unchanged state and focus so the supplied art behind it can be read. It does not intercept typing in editable controls or open combos. The embedded ANSI artwork starts top-left, preserves color and blank/leading cells, stays unscaled and unwrapped, and is clipped to logical terminal bounds. Ordinary command examples hide it; the outer review page scrolls without shrinking terminal cells.

Repository shows the base branch, clean or changed worktree cues, and config target. Agents displays all six role/scope rows concurrently. Each selected scope has an editable comma-separated ladder field; the dependent Agent/Model/Effort controls below it only append a supported entry. Blank Implementer Low/Normal/High fields inherit Base; typing or Add creates an override. Runtime and schema support ordered Helper fallback; the current wizard editor still exposes one rung pending Task 279. Catalog values are synthetic fixtures, not live discovery. Unsupported choices are unavailable and changing a dependency clears its dependents.

Tracker and cache fields start blank. Detected candidates add once to the comma-separated fields; removing an entry enables adding it again. Tracker paths are project-relative Markdown files used as task trackers. Cache paths are registered for task worktrees. Empty tracker input sends no tracker files to tasks; empty cache input copies no caches. CoW evidence describes the project filesystem, and unsupported CoW means cache copies are skipped. The extra Doctor effort case uses the explicitly invalid value `extreme`; it does not claim a live catalog rejects a valid effort. Parallelism shows the default of 2 without Current/Accepted rows. Integration retains both reference details: local merge merges a finished task branch locally; pull request opens a pull request for review. Before Run doctor, Finish shows the config destination and selected summary without redundant Roles or Doctor fact rows, while keeping the original explanation about writing `.shikiru/config.toml`, excluding `.shikiru/` in Git, and running Doctor.

Before writing, invalid ladder entries or parallelism keep the wizard at the field needing correction. Run doctor writes the fixture configuration and shows the full post-write Doctor report in Finish, using the native per-check format: bold white check names, grey separators, white detail, and the aggregate passed/failed line at the bottom after a blank row. The provider-unavailable fixture marks failed Claude checks with a cause and recovery suggestion. The result is read-only with Exit and no duplicate report. Escape never cancels or exits; explicit Cancel and Exit actions remain. Hints are static, non-focusable text. The left hint appears only when applicable, including a scroll hint only when the body overflows; Ctrl+F and `富士山` stay at the right.

## Validation limits

Missing project state fixtures explicitly lack both `.shikiru/state.db` and `config.toml`; recovery offers session removal and fresh initialization, which cannot recover a database that is already missing. Missing `config.toml` alone is not treated as a task-database failure. The Operator verified the latest prototype, followed by static review of its selected outcomes and initializer composition. An earlier temporary Node harness exhausted host resources and was abandoned; it is not current validation proof. Browser interaction and geometry remain unverified. Task 274 adds behavior-focused CLI integration coverage and records its bounded focused-check result separately.
