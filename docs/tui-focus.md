# Shikiru Compact Operator TUI

## 1. Decision and delivery status

Phase 9 is planned to implement the Operator-approved direction from 2026-10-04: option B's **Focus** task-list/inspector layout with option A's **Flight deck** palette. The later approved refinement compacts totals, removes separate agent resource readouts, combines task agent identity and usage, explains missing reporting directly and distinguishes subscription usage from API charges. Until Tasks 89–96 ship, the Phase 8 workspace remains the implemented dashboard; this document specifies the approved target, not available behavior.

This is the target specification until each Task 89–96 acceptance criterion ships. As those tasks ship, it supersedes the primary presentation in [Operator Workspace](tui-workspace.md) and the presentation portions of 01 D18/D24 and 06 §5.4/§6. Phase 8's completion and evidence remain historical facts. Its task search/selection, command timing/outcomes, ownership/holds, escalation correctness, reports/evidence/history/export, plugin compatibility and responsive preservation guarantees continue to apply. The [approved standalone mockup](design/references/focus-steel-approved.html) preserves the interactive reference; its [736-pixel approved view](design/references/focus-steel-approved-736.png) records the selected appearance.

Convert the existing Textual/Rich presentation. Preserve operator services, the engine's single writer, bounded store queries, native agent execution, structured role panes and SSH access. No alternate engine, second default TUI or Phase 8 revert is introduced.

Task 89 adds a durable approved mockup reference and reconciles the design documents. Mockup metrics, billing declarations, accounts and quota values are illustrative; they are never implementation defaults or provider guarantees.

## 2. Composition and palette

Wide Home has a compact identity/engine/uptime header, a short operational summary, a compact project-usage line, a compact host/instance resource line, an actionable attention row when required, then task list and selected-task inspector side by side. A quiet project does not require a permanent attention panel. The bottom shows concise keyboard hints and command feedback when relevant.

Graphite background `#11151b`, surface `#171d25`, neutral border `#3d4654`, primary text `#dce2e9`, secondary text `#9aa5b5`, steel blue `#83bddd` for focus/activity, sage `#90cba6` for success/reported charges, amber `#dfb16c` for holds/decisions, and dusty rose `#e3a0a0` for errors. Names, words and width-safe glyphs identify roles/states without requiring a unique bright role color. Use restrained focus treatment, light separators and the established glyph-width policy. CLI/public renderables may retain their existing shapes where compatibility needs them.

There are no default summary-card banks, always-visible search/filter/button bars, expanded comic strips, repeated status panels or task action toolbars. Put infrequent content/actions behind contextual navigation while keeping them discoverable and keyboard/mouse reachable.

## 3. Project readouts

Project tokens cover retained recorded session lineages, including completed tasks and earlier engine runs. State reported/unknown session coverage directly, such as `412k tokens · 12/14 sessions`. A recorded zero remains zero; absent reports remain unknown. Preserve legacy positive-token evidence and identify it in Details. Coverage uses the project-wide set of persisted run/session rows: explicit reports, positive legacy token evidence and unknown rows are distinguishable; totals do not imply a time window or include data whose run row is gone.

Show reported API/billed charge subtotals with explicit missing charge coverage, such as `API $4.82 · 2 costs unreported`. “Costs unreported” counts only declared API-mode sessions without a reported billed amount; subscription sessions with no per-task charge are excluded, and sessions with unknown billing mode are called out separately rather than treated as API charges. Do not infer monetary cost from tokens or mix actual charges and API-equivalent estimates. Estimates remain explicitly labelled in Details. A recorded zero billed amount remains a measured zero. Unknown billing modes and unavailable amounts remain explicit.

A compact subscription summary opens reported quota/window/reset observations and declared billing details. Without recorded account identities, count distinct declared subscription agents and label them as agents, not accounts or independently purchased plans. Quotas belong to the provider/account scope the plugin reports, which can include activity outside this project. Do not sum percentages across accounts or interpret a quota percentage as task completion or task cost.

Current-engine uptime uses lock liveness, recorded start evidence and injected `Clock`. Advance it only when the recorded start can be associated with the current lock holder; a held lock without a matching start reports `Running · uptime unknown`. A stopped engine says `Stopped` and never advances an uptime. Show a previous run duration only when its matching stop is recorded. Missing or inconsistent start/stop observations stay unknown; do not infer them from a PID, lock-file age or task timestamps. No heartbeat or uptime database is needed.

## 4. Task inspector and task view

Use the same compact task presentation in Home and `dash --task`: full reachable ID/title, state/current stage, all holds/ownership, meaningful activity/decision, a short task usage total, then combined agent/usage rows. A row presents role/current-or-previous status and agent/model together with observed tokens and declared billing mode/known charge. Do not create separate bulky Task Usage and Agents sections.

Retain each session lineage's recorded usage and historical agents after ordinary task-window/worktree cleanup. Existing session totals are cumulative and can span several same-agent models. Show recorded earlier model identities in Details, label session-wide amounts as such and do not assign a cumulative total to each model or manufacture model-level usage. A per-turn delta may be attributed only when a retained turn record names that model and contains the corresponding recorded delta; it remains a per-turn observation, not a complete model subtotal. Bounded existing turn events can supply model history; retention, interrupted turns and missing event pairs must be shown as gaps rather than an invented complete history. Never infer an earlier model from the current ladder or the run row's latest model.

Implement/plan/setup/consult/rework/integration states have compact textual stage/activity views appropriate to their actual workflow. A signal spinner is activity evidence, not completion percentage or engine health. Preserve the established signal-driven activity/stall rules, disabled-animation data refresh and motion cap.

Details retain reported input/output/cache, monetary basis/provenance/coverage, declared billing source, reported context, quota windows/reset/observation age and session/model history. Reports/verdicts, request/ACs, engine facts, signals/timeline, blockers/relationships, workspace/role targets and export remain available through purposeful on-demand views.

## 5. Billing declarations and subscriptions

Use normalized **API**, **subscription** or **unknown** mode with an explicit source. Task 89 defines the additive contract in [04 §1](design/04-agents.md#1-contract-shikiruagentscontracts): an optional plugin capability returns `BillingDeclaration(mode, source)`, where mode is `api`, `subscription` or `unknown`, and source is `provider_reported`, `operator_config` or `unknown`. The source is `provider_reported` only for a trustworthy native report and `operator_config` only for an explicit plugin-owned option. Old plugins without that optional capability normalize to `unknown`/`unknown`. A plugin may expose a truthful report or an Operator's explicit optional declaration; absence is a documented unknown capability, not an inference from cost, model, quota, login files, credential environment or provider name.

Claude/Codex declarations remain plugin-owned and validate at their existing factory boundary. An optional `billing_mode` setting belongs under that plugin's `[agents.<name>.options]` table; when supplied, the factory returns it with source `operator_config`. An authoritative native report may instead be returned with source `provider_reported`. If a plugin accepts both, its factory validates their relationship and returns one normalized declaration; core never resolves provider-specific precedence. These fields label billing only; they do not select provider access, alter authentication, inject secrets or introduce a tariff table. Task 89 specifies the contract and persisted fields; Task 90 implements them and their first real consumers.

Persist the declaration/source with the run/session it describes, captured when that run starts. The planned additive `runs.billing_mode` and `runs.billing_source` fields are run-scoped session facts written by the engine's single writer; a resumed or model-changed session keeps its original declaration. Existing rows and sessions without a reliable declaration migrate to `unknown`/`unknown`. Changing current configuration must not retroactively relabel historical runs. Keep billing mode distinct from `billed`, `api_equivalent` and `unknown` monetary evidence, and preserve reported amounts/unknowns without interpreting subscription mode as a measured zero.

A subscription row shows tokens and `Subscription`; reported quota/reset details show how close the account is to a limit. A fixed plan fee belongs to an account/time period, not to each task. This phase adds no account manager, billing integration, guessed plan fee or per-task fee allocation. A reliable separately reported task charge can still be shown with its source. No charge is manufactured from an API-equivalent estimate.

## 6. Navigation and operator control

Use Up/Down to select, Enter to inspect, Escape to return, Ctrl+P for contextual actions, `/` for task search, `?` for help and `u` for task usage Details where appropriate. Contextual filters/history and secondary views preserve task identity, list search, reading position and focus. Native form inputs retain text-entry keys.

Use the existing palette/forms and `OperatorCommands`, not widget-created commands. Keep all operations discoverable, including submit, prioritize, message, rung change, pause/pause-now, resume, take over/hand back, retry, cancel, stop and export. Show timing and consequences, applicable/disabled actions and durable Pending/Applied/Rejected outcomes. Queueing a message is not consumption by an agent.

Decision/ownership/hold information remains visible without multiple competing panels. Missing escalation evidence does not hide its durable question/allowed actions. Another Operator's resolution disables a stale form and preserves guidance; engine revalidation remains authoritative. Navigating or inspecting must not mutate workflow state or recreate completed-task resources.

## 7. Responsive and accessible behavior

At widths where both sides are comfortably readable, retain B's list/inspector split. Compact Home uses list then opened task detail with Escape back; preserve selection/search/scroll. Short panes reduce summary height and scroll focused content into view. Never compress a wide inspector into unreadable columns or silently hide a critical decision, task identity, hold, input or result control.

Verify wide Home/task/form panes, 48 and 36 columns, short and very small panes, `NO_COLOR`, `TERM=dumb` and disabled motion. Respect the existing glyph-width policy and fixed named layout limits; do not add speculative layout/skin settings or automatically rearrange tmux splits. Text/markers retain state, ownership, focus and result meanings without color or movement.

## 8. Architecture and configured plugins

Domain rules stay pure; SQL stays in store; plugin-specific behavior stays in agent plugins; processes/time use injected runners/clocks; wiring stays in bootstrap. Reuse operator queries, usage aggregation, subscriptions, command history, retained evidence and the existing resource sampler. Add bounded consumed projections and compatible widget-contract fields, not another scheduler/reader/stream consumer or preference database.

The compact resource line has **Host** CPU/RAM and **Shikiru including agents** CPU/RAM. Instance measurements use the existing deduplicated engine and project-pane process trees, including agent descendants found in those trees; no separate agent resource item is shown. Host CPU is the existing windowed average, host RAM is the observed host reading, and instance CPU/RSS are the same sample's process-tree measurement. Keep the sample timestamp and age. An empty process tree is not measured zero; preserve failed, stale, unavailable and empty distinctions in Details and show explicit state when a primary value cannot be trusted. Do not sum host and instance measurements or resample on motion ticks.

Existing widget names, entry points and callbacks remain stable. The existing `dashboard.widgets` list and its shipped default are unchanged; no configuration migration rewrites an Operator's chosen order or removes a widget. The planned default composition maps `header` to compact identity/engine status, `queue` to the Home task list, `escalations` to a conditional actionable attention row, `usage` to the compact project/task usage readout, and `resources` to the compact host/instance line. `strips` remains reachable as a secondary workflow view and is not in the primary Home composition. `dialogue` remains in task Activity/history; `docker` and `faults` remain in Diagnostics; command history, detailed usage, reports, relationships and export remain reachable from contextual views. Every configured third-party widget stays in Extensions in configured relative order with its existing entry point and callbacks. A future built-in gets explicit first-party placement; an extension with no placement capability defaults to Extensions. Missing or broken configured plugins continue to fail validation by name and are never silently hidden. Each implementation task documents only the compatible fields it consumes.

Remove superseded private presentation as screens convert. Preserve useful public CLI/plugin contracts and all workflow services. Necessary responsibility-splitting refactors are separate commits, not a speculative framework.

## 9. Sequential execution and review

Execute [Tasks 89–96](../tasks/plan.md#phase-9-compact-operator-tui) one at a time. The orchestrator assigns each task to GPT-6-Luna at xhigh effort, waits for its report, reviews completed implementation/design/test value and sends fixes to that same agent. Only advance after findings are resolved and required gates pass.

Each logical commit is conventional, follows a green `uv run poe check` and ends with `Co-Authored-By: Codex <codex@openai.com>`. Tick task ACs and todo only when implemented and verified. Record design deviations explicitly; the approved layout and honest billing/usage meanings remain required.

## 10. Verification and host hygiene

Reuse behavior tests at real SQLite/git/private-tmux/fake-agent/Textual boundaries. Test actual invariants and observable operator flows rather than style inventories, framework mechanics, prose or duplicate snapshots. Use `wait_until`/FakeClock and preserve coverage at least 80% without treating higher coverage as a target.

Review actual rendered Textual artifacts against the approved composition for Home, task, billing/usage, action, error/stale and compact states. Record sizes, source and findings without introducing a golden screenshot catalogue. Final verification includes normal and `TERM=dumb` gates, connected fake-agent walkthroughs, existing bounded real E1–E6, and clean-install/navigation smoke on macOS. Platform claims require actual runs. Keep earlier phase evidence and independently open release gates intact.

Before tests establish which processes/sockets/directories belong to the task. Use isolated fixture/private tmux servers exclusively. Clean disposable task/test leftovers after verification and before advancing; never kill the user's servers, agents, shells or unrelated processes. Retain approved mockups, useful visual artifacts and necessary failed-attempt evidence. Document final owned-resource cleanup alongside release results.
