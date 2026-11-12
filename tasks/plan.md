# Plan: Muse Code agent plugin

Status: proposed, 2026-11-12. Evidence comes from Muse Code 1.4.15 (`1.4.15-R8339.1`) probed on macOS
with a logged-in subscription account, and from reading t3code's `packages/provider-muse`.

## Goal

Add `muse` as a third built-in agent next to `claude` and `codex`, with the same capabilities:

- headless turns with session resume and an interactive take-over command,
- usage, quota, compaction and failure signals mapped to the shared contract,
- an authoritative model catalog with per-model efforts,
- `doctor` checks,
- a schema-constrained `headless()` caller, so the helper can run on Muse,
- configuration through `[agents.muse]` options and the setup flow, like the other plugins.

Non-goals: Windows support, Muse subagents/workflows, MCP servers for the agent, approval prompts (turns
run unattended), a Muse-specific dashboard widget.

## Verified facts about Muse

### Turns

- `muse exec --json` prints one JSON record per line (the "MSP" envelope: `schema_version`, `stream`,
  `sequence`, `record_type`, `payload_type`, `payload`). Exit code 0 on success, 1 on a failed run, 2 on a
  usage error, 143 after `SIGTERM`.
- The session id is `stream.id` of every record with `stream.kind == "session"`.
- `muse exec --session-id <uuid>` resumes that session: the stream id stays the same and the model
  remembers the earlier turn. `muse exec resume <id>` does not exist; it becomes prompt text.
- A stopped turn (`SIGTERM`, "flushed session logs") leaves the session resumable.
- The prompt is positional or `--prompt-file <path>`; stdin is not read.
- `--model`, `--reasoning-effort none|minimal|low|medium|high|xhigh|max|ultra`.
- Greeting-like prompts ("hi") are answered locally with `calls: 0`; real work always has `calls >= 1`.

### Records seen in `exec --json`

| `payload_type` | Use |
|---|---|
| `run.model.configured` | model actually used |
| `run.output.delta` | assistant text (`text`) |
| `task.lifecycle.proposed` with `task_kind: "tool.<name>"` | tool started |
| `tool.result` | tool finished: `call_id`, `correlation_facts.{tool_name, outcome}`, `text` is JSON with `command`, `exit_code`, `terminal_status`, `output` |
| `run.usage` | `models[]` (`input_tokens`, `cached_tokens`, `output_tokens`, `reasoning_tokens`, `calls`), `failed_calls`, `allowance` |
| `run.terminal.completed` | final `text` |
| `run.terminal.failed` | `reason` is a plain string; `text` is empty |

`run.usage.allowance` is `null` or `{tier, window{used_percent, resets_at_ms}, weekly{used_percent,
resets_at_ms}, as_of_ms, source}`. Percentages can exceed 100. Cost and context percentage are not reported.

### Failures (exact texts)

| Cause | `reason` / stderr |
|---|---|
| rejected API key | `your API key from META_API_KEY was rejected — update or unset it` |
| not logged in | `missing meta credentials: run \`muse login\` or set META_API_KEY, or save credentials at <path>/auth.json` |
| unknown model | ``model `x` does not exist or you lack access [request_id=…]`` |
| step cap | `model did not reach a terminal state within N step(s)` |

Not captured: a quota-exhausted failure, an overloaded/5xx failure after Muse's own 10 stream retries
(status records read `opening meta model stream attempt N/10`), and a context-length failure.

### Sandbox and permissions

- Default sandbox: writes outside the workspace root are blocked (`$HOME` refused, temp dirs allowed),
  git commits from a linked worktree work, network works. There is no writable-roots option. Shikiru's run
  directory lives outside the task worktree, so the default sandbox cannot be used.
- Tested working: `--yolo`, and `--trust-workspace --disable-sandbox` (writes into `$HOME` succeed;
  `--permission-profile :unrestricted` is accepted). The chosen default is the second form, as in t3code.
- `--disable-write` makes `exec --output-schema` fail with the "does not exist or you lack access" model
  error; do not use it. `--disable-shell`, `--disable-web-tools`, `--disable-reminders`, `--no-session-log`
  are fine.
- `-c KEY=VALUE` refuses unknown keys, so configuration cannot be guessed.

### Headless

`muse exec --json --prompt-file P --output-schema S` returns the schema-valid JSON object as the final
`run.terminal.completed.text` (also as the single `run.output.delta`).

### Authentication and quota

- Authentication is `muse login` (stored in `~/.config/muse/auth.json`, honours `XDG_CONFIG_HOME`) or
  `META_API_KEY`, which wins. The host owner chooses, as for Claude and Codex; the plugin passes the
  environment through unchanged and never reads or strips `META_API_KEY`.
- `muse usage --json` exits 0 and prints `{"schemaVersion":1,"allowance":…}` when logged in
  (`allowance: null` with `reason: "server_sent_no_usage"` when the server has no data), and exits 1 with
  `muse usage: not logged in: run \`muse login\` first` when not.

### Model catalog

`muse serve` speaks JSON-RPC 2.0 as one JSON object per line on stdio. `initialize` (params
`clientInfo{name,title,version}`, `capabilities{requestedCapabilities:[]}`), then the `initialized`
notification, then `model/list` returns
`{providerId:"meta", models:[{modelId, isDefault, variants:[efforts], defaultReasoningEffort,
reasoningEffortVariants, contextLimit, outputLimit}]}`. Right now: `muse-wonder-1.0` (default),
`muse-ember-1.0`, `muse-spark-1.3`, `muse-spark-1.2`, all with a 1,007,997-token window. Before
`initialized`, requests fail with `notInitialized`. `muse serve --disable-shell --disable-write
--no-session-log` is enough for read-only catalog calls.

### Compaction

- Muse compacts by itself, controlled by `--context-compaction-strategy`,
  `--context-compaction-soft-threshold` and `--context-compaction-hard-threshold` (fractions of the
  window). The startup prompt is about 22.5k tokens (2.2%); a hard threshold below that makes the run
  refuse to start (`startup prompt estimate … reaches hard threshold`).
- `exec --json` prints no compaction record. The on-disk session log does:
  `<muse home>/sessions/YYYY/MM/DD/<session id>/session.jsonl`, muse home `~/.local/share/muse`
  (`museHome` in the `initialize` result). Its `runtime.session` events include
  `context_compaction_candidate` (`trigger`, `timing`, `status`, `strategy`, `budget_before`,
  `estimated_budget_after`), `context_compaction_fallback` (`reason`, `can_continue`) and
  `context_compaction_installed`. A rejected candidate with `hard_threshold_failed` was captured; a
  successful `installed` event has not been captured yet.
- The session log also carries `model_completed` records with exact per-call usage.

## What t3code teaches

t3code drives `muse serve` (MSP) because it is an interactive UI. Shikiru's contract is
process-per-turn, so `exec` is the better fit; these practices carry over:

- Set `MUSE_NO_AUTO_UPDATE=1` so Muse cannot update itself in the middle of an unattended run.
- Resolve the workspace with `realpath` before passing it on.
- Parse versions with `\d+\.\d+\.\d+-R\d+(\.\d+)?`.
- Read the model catalog and efforts from `model/list`; treat an empty catalog as a warning.
- Quota heuristics when the structured `error.quota` is not available: a message matching `\b429\b` and
  `quota|usage limit` is a usage limit; its reset is the latest future instant among exhausted allowance
  windows (`used_percent >= 100`), the structured `resetsAtMs`, and an ISO timestamp after `resets at`;
  past, non-finite or absurd instants are ignored; with no evidence the reset stays unknown.
- Muse's own stream retries are informational. A failure after them is a real failure.
- t3code never triggers compaction itself; it forwards `session/compact` on request and shows
  `compaction` items and `session/contextUsage`. Shikiru instead hands the threshold to Muse's own flags.

## Design decisions

1. **Transport:** one `muse exec --json` process per turn, driven by the existing `StreamingTurn`, a
   `TurnParser` and a `TurnProcessSequence`, exactly like Codex. `muse serve` is used only for the model
   catalog.
2. **Prompt delivery:** `--prompt-file` pointing at the prompt file the engine already writes into the
   turn directory (`domain.reports.PROMPT_FILENAME`); if the file is not there at turn start, the sequence
   writes its own with `shikiru.private.write_private_text`. Verified during implementation.
3. **Default arguments:** `["--trust-workspace", "--disable-sandbox"]` in the `args` option, overridable
   by the operator like Codex's `args`. `docs/design.md`'s trust section states that Muse runs with the
   sandbox disabled, like Claude's `bypassPermissions`.
4. **Environment:** the turn environment is `os.environ` plus `spec.env` plus `MUSE_NO_AUTO_UPDATE=1`.
5. **Compaction:** `compact_at_pct` (default 80, like Codex) becomes `--context-compaction-soft-threshold`
   = pct/100 and a hard threshold a fixed margin above it, capped below 1. `Compacted` signals are read from
   the session log's `context_compaction_installed` events after the process ends (same approach as
   Codex's rollout). Where the hard threshold would fall under the startup prompt, `doctor` reports it.
6. **Quota:** `QuotaReported` from `run.usage.allowance` (`window` -> `5h`, `weekly` -> `7d`, as read from
   the account's allowance; confirm the window length from `muse serve`'s `usage/changed`
   `windowDurationMins` during the spike). `read_quota` runs `muse usage --json`.
7. **Failure classification** (text based; `FailureKind`):
   - `AUTH`: `rejected`, `missing meta credentials`, `not logged in`.
   - `QUOTA`: `\b429\b` with `quota|usage limit`, or any allowance window at or above 100 on the failed
     turn; reset per the t3code rule, parsed through `ResetTimeParser` for ISO text.
   - `UNAVAILABLE`: `overloaded`, `temporarily unavailable`, `service unavailable`, 5xx status text.
   - `OTHER`: `does not exist or you lack access`, step cap.
   - `UNKNOWN`: everything else, never swallowed.
   - Exit code 1 with no `run.terminal.*` record is `UNKNOWN` with the captured stderr tail.
8. **Billing:** `billing_mode` option, `BILLING_SOURCE_OPERATOR_CONFIG`, as in Codex. Cost stays unknown;
   `Usage.cost_basis` is `unknown`.
9. **Headless:** `muse exec --json --prompt-file P --output-schema S --disable-shell --no-session-log
   --disable-web-tools --disable-reminders --model M --reasoning-effort E`, run in the given `cwd` with
   the timeout, then parse the JSON object from the final terminal text. Errors raise
   `HeadlessOutputError` like Codex's. No sandbox flags are added: the call does not write.
10. **Interactive take-over:** `muse resume <session id> --model M [--reasoning-effort E] <args>`; the
    command runs in the task root.
11. **Catalog:** `model_catalog()` runs `muse serve` for one request, caches the result, and maps
    `variants` to `ModelChoice.efforts`; the context window feeds the compaction check in `doctor`.

## Layout

```
src/shikiru/plugins/agents/muse/
  __init__.py        # package marker, like the other plugins
  options.py         # constants, MuseOptions(args, compact_at_pct, billing_mode), MUSE_SETTINGS
  turn.py            # MuseTurnParser, MuseTurnSequence, failure classification, quota helpers
  session_log.py     # locate and read <muse home>/sessions/.../session.jsonl: compactions, usage
  catalog.py         # one-shot `muse serve` JSON-RPC client and model/list parser
  headless.py        # MuseHeadless
  agent.py           # MuseAgent: start_turn, interactive_command, doctor, read_quota, model_catalog,
                     #   settings, headless, billing_declaration; create()
```

Wiring and docs: entry point `muse = "shikiru.plugins.agents.muse.agent:create"` in `pyproject.toml`
(and the `PLR0917` ignore for `headless.py`), the keyword list, `docs/design.md` (agents, quota,
compaction, trust boundaries), `README.md` (built-in agents, prerequisites, trust notes),
`CONTRIBUTING.md` (contract test and live-model constants). The layered import-linter contracts need no
change: `plugins` already depends only on `agents.contracts`, `agents.sdk` and `agents.settings`.

## Signal mapping

| Signal | Source |
|---|---|
| `SessionStarted` | first `stream.id` with `stream.kind == "session"`; also on resume |
| `Message` | `run.output.delta.text` |
| `ToolStarted(name, detail)` | `task.lifecycle.proposed` with `task_kind` `tool.<name>`; detail is the command when `tool.result` later supplies it, otherwise the tool name |
| `ToolFinished` | `tool.result`: outcome from `correlation_facts.outcome`, `exit_code` from the JSON in `text` |
| `UsageReported` | `run.usage` summed over `models[]`, added to `spec.usage` when resuming; `cost_usd=None`, `cost_basis="unknown"`, `context_used_pct` from the session log's `estimated_prompt_tokens` over the catalog window when available |
| `QuotaReported` | `run.usage.allowance` windows |
| `Compacted(pre, post)` | session log `context_compaction_installed` events newer than the count at turn start |
| `TurnEnded` | `run.terminal.completed.text` |
| `TurnFailed` | `run.terminal.failed.reason`, classified as above; interrupted turns end without a failure |

## Init, the wizard and doctor

`shikiru init` and the dashboard wizard are generic: core code never names a provider (`docs/design.md`,
"Configuration, observability and extensions"). They reach every plugin through
`operator.plugin_checks.PluginChecks` and `operator.detectors.ProjectDetectors`, so Muse appears there only
if the plugin satisfies these obligations, each of which is a requirement of this plan:

1. **Detection gate.** `ProjectDetectors._agents()` runs `doctor([])` with options `{}` for every
   registered plugin and keeps only those whose checks all pass. A Muse that is installed but fails any
   check is silently absent from init and the wizard (the error is raised only when no plugin passes).
   So the no-models doctor must be exactly the binary, version and login checks, quick (each bounded by
   `DOCTOR_TIMEOUT_SECONDS`), and must pass for a working install. The login check accepts a stored login
   (`muse usage --json` exits 0) **or** a non-empty `META_API_KEY` in the environment, reported as
   "credentials not verified by doctor", like Codex's custom-provider case; otherwise a host that
   authenticates through the API key would not see Muse in the wizard. Model and compaction-threshold
   checks run only when models are passed.
2. **Model catalog is mandatory, not optional.** The wizard's ladder editor lists an agent only when
   `model_catalog()` returned an authoritative catalog with at least one `offered` model; otherwise the
   agent is shown with its error and cannot be added to a ladder, and `assess_model_choice` needs
   `complete=True` to reject unknown models and efforts. The wizard calls it with options `{}` while the
   screen shows "Discovering supported agent model choices…", so the `muse serve` call must be fast, bounded
   by a timeout, closed cleanly, and return `ModelCatalog(error=…)` on any failure. If the operator's `args`
   select a model or profile, the catalog is reported ambiguous with an error, as Codex does.
3. **Headless is required for the Helper role.** The ladder editor offers only agents whose
   `headless()` is non-`None` for the Helper scope, so `MuseHeadless` (Phase 5) is what makes Muse
   selectable there, as well as what lets the helper run.
4. **Settings.** The wizard renders `settings()` generically and the plugin factory validates the result
   (`options_rejection` calls `create()` with the chosen options). Muse offers exactly the three settings
   the other built-in plugins offer: `compact_at_pct`, `billing_mode`, `args`. The default `args`
   (`--trust-workspace --disable-sandbox`) is shown in the wizard and written to the config like Claude's
   `--permission-mode bypassPermissions`; a blank field means the operator removed it, and the docs say what
   that does (writes outside the worktree are refused). `create()` rejects unknown keys through
   `options_error` so the wizard focuses the offending field.
5. **Generated config.** `render_config` writes an `[agents.muse]` section for every passing plugin and
   uses `project.agents[0]` (alphabetical: `claude`, `codex`, `muse`) with the model `default` for the
   non-interactive default ladders. Muse is therefore the default only when it is the sole installed agent,
   and then `muse:default` is rejected by `muse exec --model default`. Codex and Claude have the same
   sentinel; before implementing, check how they treat it and match that behaviour (Phase 0). If neither
   handles it, `doctor` with a complete catalog already says "unsupported model 'default'; use …", which is
   the visible, non-silent outcome; no special case in core.
6. **Doctor at the wizard's last step.** The final Doctor run passes the configured ladder models and the
   catalog, so Muse's `doctor(models)` must accept model names that are valid per the catalog, report
   efforts only through the catalog, and not require a turn.

## Testing strategy

Per `AGENTS.md`: unit tests for the parser and pure rules; integration tests for the rest; no mocks of our
own classes; no sleeping; coverage stays at or above 80%.

1. **Fixtures** (`tests/fixtures/agents/muse/`, with a README stating version, date and redaction, as for
   Codex): redacted genuine captures of a first turn, a resumed turn, a tool turn, an auth failure, a
   missing-credentials failure, a model failure, a step-cap failure, a stopped turn, a headless result, a
   `model/list` reply, `muse usage --json` output, and session-log excerpts with a compaction. Captures
   that cannot be produced (quota exhaustion, overload) are written by hand and labelled synthetic in the
   README.
2. **Unit tests:** `MuseTurnParser` over each fixture; failure classification table (one table per rule);
   reset selection; threshold-to-flag mapping; version parsing; catalog parsing.
3. **Integration tests:** `tests/integration/test_muse_headless_contract.py` subclassing
   `AgentContractSuite`; invocation cases for new, resumed, model-change and environment; a headless test
   against a fake `muse` executable script placed on `PATH` that replays fixtures, so the real
   `SubprocessRunner` is exercised; `doctor` against the fake executable for the missing binary, logged-out
   and logged-in cases.
4. **Contract suite adjustment:** `test_offline_compaction_continues_within_one_turn` requires a recorded
   `Compacted` signal and the three `CompactionCase` scenarios. Muse compacts internally, so the
   `THRESHOLD` case checks the flags passed, `CONTEXT_FAILURE` and `FAILED_RECOVERY` cover the failure
   text and the rejected-candidate fallback. If a real `installed` event cannot be captured, the suite is
   extended so a plugin can declare its compaction as internal, with the scenarios it supports; that
   contract change is its own commit.
   Existing tests that pin the built-in agent set must learn about Muse:
   `tests/integration/test_operator_plugin_checks.py` (`settings` defaults parametrized over `claude` and
   `codex`: add `("muse", ["--trust-workspace", "--disable-sandbox"])`, which also checks the three setting
   keys and the unknown-option rejection), and any test enumerating `AgentRegistry().names()`.
   Wizard coverage uses the fake agent and the real `PluginChecks`: add integration cases that run the
   detection gate, catalog discovery and Helper filtering against a fake `muse` executable on `PATH`
   (installed and logged in, installed and logged out, `META_API_KEY` only, missing binary, slow
   `muse serve`), asserting what the operator can observe: whether Muse is offered, selectable in the
   Leader/Implementer/Helper scopes, and what the generated config contains.
5. **Live checks:** gated like the others (`runs_live_checks_in_default_gate = False`); constants for the
   live model and effort are added to the test module and to `CONTRIBUTING.md`.
6. **E2E rung:** an opt-in `muse` entry in the real-agent e2e configuration (the current constants name
   Codex), costing one small turn per ladder role.

## Risks and open items

- **Compaction record shape:** the successful `context_compaction_installed` event is unobserved. A spike
  forces one with a long conversation (soft and hard thresholds a little above the 2.2% startup size) and
  captures the event; until then the parser reads only fields confirmed from the rejected candidate.
- **Quota and overload texts:** unobserved; classification is text based and conservative, with
  `UNKNOWN` as the default, so an unrecognised failure escalates instead of being retried blindly.
- **Session log location:** `<muse home>` comes from the server's `initialize` result; the plugin
  derives it from `XDG_DATA_HOME` (default `~/.local/share/muse`) and `doctor` verifies it exists. A
  missing log degrades usage and compaction signals but never the turn result; the parser records that as
  a visible `doctor`/fault entry, not a silent fallback.
- **Approval behaviour unattended:** with the sandbox disabled and approvals judged by Muse's LLM judge,
  a denied command would surface as a failed tool, not a hang. Verify in the live E2E; `--approval-mode`
  stays operator-configurable through `args`.
- **Version drift:** tested version constant (`1.4.15`) with a `doctor` warning on other versions, as for
  Codex.
- **Wizard visibility:** a plugin whose no-models doctor fails is dropped from init without a message, so a
  slow or flaky Muse check hides the agent. Keep the checks cheap, deterministic and bounded.
- **Catalog latency:** `muse serve` starts a full runtime; measure it (Phase 0) and set the timeout from the
  measurement, not a guess.
- **Interactive resume:** `muse resume <id>` was read from help only; verify in tmux during the E2E.
- **Cost:** the probes used about 25 tiny turns; the E2E rung should stay at one small turn per role.
