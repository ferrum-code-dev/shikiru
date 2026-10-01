# 04 · Agents

## 1. Contract (`shikiru.agents.contracts`)

Agents are **code plugins**. The engine depends only on this module, and each plugin owns everything agent-specific:
- CLI argv and flags
- stream and session-log formats
- usage, context window and quota extraction
- error mapping
- context compaction

Rungs select only `{agent, model, effort}`. Agent options are a plugin-owned extension point, including access options when a plugin has a concrete agent-specific need for them. Core Shikiru has no provider-access configuration or credential handling. For the current Claude and Codex plugins, the Operator configures access through each agent's native project or user files and environment, and the plugins pass the Operator's environment to their child processes.

**Turn model (D1).** Every turn is one agent process running headless on a session: the first turn creates the session, every later turn resumes it, also after a model change, a stall, a quota wait, an operator take-over or an engine restart. The process reads nothing from stdin (it is closed), shows no dialogs, and exits when the turn ends. Its structured output stream is the only signal source: no hooks, no screen.

```python
@dataclass(frozen=True)
class Rung:            agent: str; model: str; effort: str | None

class FailureKind(StrEnum): QUOTA = auto(); UNAVAILABLE = auto(); AUTH = auto(); OTHER = auto(); UNKNOWN = auto()

@dataclass(frozen=True)
class Usage:           input_tokens: int; output_tokens: int; cache_read_tokens: int; cache_write_tokens: int
                       cost_usd: float | None; cost_basis: Literal["billed", "api_equivalent", "unknown"]
                       context_used_pct: float | None
@dataclass(frozen=True)
class QuotaWindow:     name: str; used_pct: float; resets_at: datetime | None; observed_at: datetime
@dataclass(frozen=True)
class CheckResult:     name: str; ok: bool; detail: str

@dataclass(frozen=True) class SessionStarted: session_id: str
@dataclass(frozen=True) class Message:        text: str
@dataclass(frozen=True) class ToolStarted:    name: str; detail: str
@dataclass(frozen=True) class ToolFinished:   name: str
@dataclass(frozen=True) class UsageReported:  usage: Usage                       # session totals
@dataclass(frozen=True) class QuotaReported:  windows: tuple[QuotaWindow, ...]
@dataclass(frozen=True) class Compacted:      pre_tokens: int | None; post_tokens: int | None
@dataclass(frozen=True) class TurnEnded:      last_message: str
@dataclass(frozen=True) class TurnFailed:     kind: FailureKind; message: str; resets_at: datetime | None
type AgentSignal = (SessionStarted | Message | ToolStarted | ToolFinished | UsageReported
                    | QuotaReported | Compacted | TurnEnded | TurnFailed)

@dataclass(frozen=True)
class TurnSpec:        rung: Rung; role: str; prompt: str; cwd: Path; run_dir: Path; turn_dir: Path
                       add_dirs: tuple[Path, ...]; env: Mapping[str, str]
                       session_id: str | None          # None → new session; else resume it
                       usage: Usage | None             # the session's totals so far
                       on_process: Callable[[int], None]  # called with each spawned pid before it runs (03 §4.2)

class Turn(Protocol):
    """One agent turn: one process, or a short sequence when the plugin compacts (obligation 8).
    `signals()` must be consumed by exactly one reader."""
    pid: int                                               # the process running now
    def signals(self) -> AsyncIterator[AgentSignal]: ...   # ends when the process has exited
    async def stop(self) -> None: ...                      # terminate; the session stays resumable

class HeadlessAgent(Protocol):
    """One-shot, schema-constrained calls without a session."""
    async def ask(self, prompt: str, schema: Mapping[str, Any], model: str, effort: str | None,
                  cwd: Path, timeout: timedelta) -> Mapping[str, Any]: ...

class Agent(Protocol):
    """An agent CLI plugin."""
    name: str
    async def start_turn(self, spec: TurnSpec) -> Turn: ...
    def interactive_command(self, session_id: str, run_dir: Path) -> Sequence[str]: ...   # take over (03 §9); also the resume command
    async def doctor(self, models: Sequence[str]) -> list[CheckResult]: ...   # binary, version, native access; models: the ladders' models for this agent
    def headless(self) -> HeadlessAgent | None: ...

@dataclass(frozen=True)
class AgentContext:    options: Mapping[str, Any]; runner: SubprocessRunner; clock: Clock

type AgentFactory = Callable[[AgentContext], Agent]   # entry-point target; validates options, raises ConfigError

def shikiru_command(*arguments: str) -> str: ...   # `<python> -P -m shikiru <arguments>` for this installation
def module_command(module: str, *arguments: str) -> str: ...   # the same for a plugin's own module, e.g. a hook
```

Prompts call Shikiru back only through `shikiru_command`. Agent shells may be login shells with their own `PATH`, so nothing agent-facing relies on `shikiru` being on it.

**Obligations**, checked by the contract kit:
1. `SessionStarted` exactly once per turn, before any other signal. For a resumed turn its id equals `spec.session_id`.
2. `Message`, `ToolStarted` and `ToolFinished` as the agent works; a tool runs between its `ToolStarted` and `ToolFinished`.
3. Every stream ends with exactly one `TurnEnded` or `TurnFailed`, and then the process has exited. A process that exits without a structured result yields `TurnFailed(UNKNOWN)` with the exit status and the stderr tail.
4. `UsageReported` carries session totals; its token counts never decrease (`context_used_pct` drops after `Compacted`).
5. **Usage is only what the agent reports** (D21): tokens always; `cost_usd` only when the agent marks its cost as priced, else `None` with `cost_basis = "unknown"`; `context_used_pct` only from a context window the agent reports reliably, else `None`; `QuotaReported` only when the agent reports windows. A provider's list-price estimate has `cost_basis = "api_equivalent"`; `billed` requires a provider-reported charge; otherwise use `unknown`. Plugins must not derive the basis from how access was configured.
6. The process never waits for input: stdin is closed and no flag it is given can open a dialog.
7. `stop()` ends the process and every descendant it started within `TURN_STOP_GRACE` (SIGTERM to the turn's process group, then SIGKILL) and returns within a bounded time even when a descendant holds the output pipes (03 §4.2); the next turn on the same `session_id` resumes the session.
8. **Compaction.** The plugin prevents context overflow by compacting proactively at its `compact_at_pct` option (default 80), including while a single turn is running. Core Shikiru knows nothing of this beyond displaying `Compacted`:
   - The threshold applies to the agent's own view of its context window for the configured model, before a turn and before each model request within it. A turn that starts below the threshold and reaches it compacts and continues within the same logical `Turn`, preserving the session and task progress.
   - The headroom above the threshold must hold one request's pending input and the model's output allowance; a lower `compact_at_pct` buys more.
   - The mechanism belongs to the plugin: the agent's native auto-compaction when verified in headless turns, otherwise a plugin-driven sequence. It must work in every access mode the agent supports; Shikiru passes no provider or window information. Display rules (obligation 5) never limit protection.
   - Recovery is the plugin's too: after a context failure it compacts and re-runs the same prompt once inside the same `Turn` (prompts are idempotent). If that fails the turn ends with `TurnFailed(OTHER)`. Recovery is exceptional and does not replace prevention.
   - Every compaction emits `Compacted`. `SessionStarted` and the final `TurnEnded`/`TurnFailed` still occur once per `Turn`.

**Failure mapping.** A plugin maps the structured errors it knows to a `FailureKind` and returns `UNKNOWN` with the raw text for anything else. The engine asks the Helper to classify `UNKNOWN` (§6); the Helper never replaces the structured path (D22).

## 2. SDK (`shikiru.agents.sdk`)

Small, tested building blocks for plugins:

| Block | Purpose |
|---|---|
| `TurnParser` | Protocol a plugin implements: `feed(line) → list[AgentSignal]` for one stdout line, `finish(exit_code, stderr) → list[AgentSignal]` at exit. Pure, so recorded streams replay offline |
| `StreamingTurn` | `Turn` implementation: spawns the argv through `SubprocessRunner.spawn` with stdin closed, copies stdout to `turn_dir/<role>.stream.jsonl` and stderr to `turn_dir/<role>.stderr.log` (the engine keeps only the last 1 MiB of a process's stderr in memory, so the parser and the log see that tail of a flood), feeds lines to the parser, enforces obligations 1 and 3, and implements `stop()`. Runs the plugin's process sequence for one turn (for example prompt, compact, continue), appending every process's output to the same files |
| `ResetTimeParser` | "resets 3:10am (Europe/Warsaw)", ISO, epoch, relative durations → `datetime` |

## 3. Contract test kit (`shikiru.agents.testing`)

`AgentContractSuite` is a pytest base class. A plugin subclasses it and provides its factory, its `TurnParser`, sample files and compaction scenarios: recorded processes (plus files the agent wrote, such as a hook marker or a session log) that the kit replays through the plugin's own process sequence and `StreamingTurn`.

**Offline checks** (always run; samples are recorded streams committed under `tests/fixtures/agents/<agent>/`, 07 §4):
1. Each recorded stream (stdout lines, stderr, exit code, plus session-log excerpts when the plugin reads them) replays through the parser to the expected signal sequence and satisfies obligations 1–5.
2. Recorded error streams map to the expected `FailureKind` and `resets_at`; unmapped text yields `UNKNOWN`.
3. Recorded compaction output yields `Compacted`. Scenarios exercise threshold crossing within one turn and repeated compaction within one turn, each continuing to one `TurnEnded`. A context failure followed by compaction and a rerun yields one `Turn` ending in `TurnEnded`; a failed compaction or a second context failure yields `TurnFailed(OTHER)`.
4. Argv for a new turn, a resumed turn, a model change and the compaction setting; Operator-provided environment pass-through; `interactive_command`.

**Live checks** (`SHIKIRU_E2E=1`, scratch repository):
1. A first turn whose prompt points at a prompt file → `SessionStarted`, a valid report through `shikiru report`, `TurnEnded`.
2. `UsageReported` tokens > 0.
3. A resumed turn on another model of the same agent recalls a nonce from turn 1.
4. `stop()` during a turn, then a resumed turn completes.
5. `headless().ask()` returns schema-valid JSON (when provided).
6. With a low `compact_at_pct`, a turn that starts below the threshold grows past it during its own work, compacts (`Compacted`) before overflow, continues working and completes with a valid report while recalling an earlier nonce. No second Shikiru turn triggers the compaction. It runs in the Operator's own agent configuration; a skipped check establishes nothing.

`shikiru doctor --probe` validates the configured ladders through the same `start_turn` the engine uses, in a scratch repository reused at `.shikiru/probe/`: the first rung of each agent runs live check 1, and every other distinct rung (agent, model, effort) runs a smoke turn ("Hi") that must end in `TurnEnded`. No dialog needs answering.

## 4. Claude Code plugin (`plugins/agents/claude`) — verified with 2.1.281

**Options** (`ClaudeOptions`):

Claude reads access from the Operator's native Claude settings and environment. The plugin does not configure access modes, choose key variables, or remove variables. Turn and headless child processes inherit the Operator's environment; turn-specific Shikiru variables are added from `TurnSpec.env`.

| Key | Default | Meaning |
|---|---|---|
| `args` | `["--permission-mode", "bypassPermissions"]` | Extra CLI args; must keep the turn unattended |
| `compact_at_pct` | `80` | Context use that triggers compaction (obligation 8) |

Trust: the default skips every permission prompt, so the agent can run any command and edit any file the Operator's user can. `--add-dir` only widens the working directories (the run directory); it confines nothing. See the trust boundary in 01 §6.

**Turn**:

```
claude -p "<prompt>" (--session-id <uuid> | --resume <id>) --output-format stream-json --verbose
       --model <m> --effort <e> --add-dir <run_dir> <args>
```

The plugin chooses the uuid of a new session. A model change is the next `--resume` with the new `--model`; the context is kept (verified).

**Signals**, from stream-json:

| Stream entry | Signal |
|---|---|
| `system/init` | `SessionStarted(session_id)` |
| `assistant` text block | `Message` |
| `assistant` `tool_use` block | `ToolStarted(name, input summary)` |
| `user` `tool_result` block | `ToolFinished` |
| `system/compact_boundary` | `Compacted(compact_metadata.pre_tokens, post_tokens)` |
| `rate_limit_event` | `QuotaReported` (`five_hour`, `seven_day`: utilization, `resetsAt`) |
| `result`, not `is_error` | `UsageReported`, then `TurnEnded(result)` |
| `result` with `is_error`, `api_error_status` or an error `terminal_reason`; stderr tags such as `[claude-code:unrecognized_model]` | `TurnFailed` |

**Failures**:
- 429 or a provider-reported usage-limit window → `QUOTA`, `resets_at` from the latest `rate_limit_event` or the message (`ResetTimeParser`); 402 payment/credit exhaustion → `AUTH` for immediate operator escalation
- 5xx / 529, `unrecognized_model` → `UNAVAILABLE`
- 401 / 403 → `AUTH`
- prompt too long / context terminal reason → the plugin's recovery (Compaction); unrecovered → `OTHER`
- else → `UNKNOWN`

**Usage**:
- Tokens: `spec.usage` plus the turn's `result.usage`.
- Cost: `result.total_cost_usd` (cumulative per session) only when `modelUsage.<m>.costBasis` is `"list"`; report it as `api_equivalent`, independent of how access is configured. For any other cost basis, where the list-price estimate does not represent a reliable provider price, report `None`, `unknown`.
- Context: the last assistant message's input and cache tokens ÷ `modelUsage.<m>.contextWindow` only when the cost basis is `"list"`. A CLI-assumed 200000 default is not a reliable provider-reported context window.

**Compaction** (obligation 8), against the window Claude itself assumes for the model: 200,000 tokens, or 1,000,000 with a `[1m]` model suffix (protocol constants):
- `claude -p` never compacts within a turn (verified with 2.1.282: none with `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=20`, none by default up to 199,279 of 200,000 tokens), so the plugin drives it.
- Every turn passes `--settings` with a `PostToolUse` hook, a plugin module run by Shikiru's interpreter. It estimates the next request as the last assistant usage in the session transcript (input, cache and output tokens) plus the tool results returned since that message (characters ÷ 3, accumulated in the turn dir for parallel calls). At ≥ `compact_at_pct` of the window it marks the turn dir and returns `{"continue": false}`: the process ends with `terminal_reason: "hook_stopped"` before the request is sent.
- On that marker the plugin runs `claude -p "/compact" --resume <id>` and resumes with a continue prompt, as often as needed, within one `Turn`. A `/compact` whose `compact_boundary` reports no `post_tokens`, or `post_tokens` at or above the limit, ends the turn with `TurnFailed(OTHER)` instead of looping. Verified: six compactions in one logical turn at a 20% threshold; every compaction ran below the limit and the task completed, recalling its nonce. Stopping on stream usage instead acts only after the request is sent: one parallel batch grew a request from 12k to 106k tokens.
- Before a resumed turn, the same estimate over the transcript decides a leading `/compact`.
- Recovery: on a context failure, `/compact` and the same prompt once more. *Verify* that `/compact` succeeds on an overflowed session; if it does not, the turn fails with `OTHER`.

**Other**:
- `interactive_command`: `claude --resume <id> --add-dir <run_dir> <args>` (verified: its turns land in the same session, and the next headless `--resume` sees them).
- `headless().ask`: `claude -p --output-format json --json-schema <s> --model <m> --effort <e> --safe-mode --tools "" --no-session-persistence` → `structured_output`.

## 5. Codex plugin (`plugins/agents/codex`) — verified with 0.159.2

**Options** (`CodexOptions`):

| Key | Default | Meaning |
|---|---|---|
| `args` | `[]` | Extra CLI args |
| `compact_at_pct` | `80` | Context use that triggers compaction (obligation 8) |

Codex reads access from the Operator's native Codex settings and environment. The plugin must not configure access modes, choose key variables, or remove variables.

**Turn** (cwd = task root, stdin closed — `codex exec` waits on an open stdin):

```
codex exec --json -m <m> -c model_reasoning_effort="<e>" -c sandbox_mode="workspace-write"
           -c sandbox_workspace_write.writable_roots=[<run_dir>, <worktree git dir>, <git common dir>]
           <args> "<prompt>"                                                                  # new session
codex exec resume <thread_id> --json -m <m> -c model_reasoning_effort="<e>" <the same two -c>
           <args> "<prompt>"                                                                  # later turns
```

- `exec resume` has neither `--sandbox` nor `--add-dir`, and a resumed session does not carry the first turn's extra roots (14 of 14 rollouts of multi-turn sessions on 0.156 to 0.159 show `writable_roots: []` on later turns). Both turn kinds therefore pass the sandbox mode and the roots as the same two `-c` overrides, computed per turn from the turn's cwd (`git rev-parse --absolute-git-dir --git-common-dir`) and `TurnSpec.run_dir`.
- Since Codex 0.158 the sandbox denies writes under a linked worktree's own git directory (`<common>/worktrees/<id>/`, which holds `index`, `HEAD` and locks) even when the common directory is a writable root. The worktree git directory must be listed itself; commits then work. Verified on 0.159.2 with the free `codex sandbox -c sandbox_mode="workspace-write" -c sandbox_workspace_write.writable_roots=[...]` outside `$TMPDIR`: only the common directory fails with `index.lock: Operation not permitted`, with the worktree directory added `git commit` passes. That the `-c` roots also hold in `codex exec` and `codex exec resume` is verified by the live check recorded in `docs/release-readiness-2026-10-01-phase7b.md` (Phase 7c); the sandbox command itself cannot take `--add-dir`. A host `default_permissions` profile or a guardian (`approvals_reviewer = auto_review`) can mask or override this; hosts without them are unverified.
- Trust: `workspace-write` confines writes to the task root and the writable roots, but the shared git common dir is one of them. A task can change refs, hooks and config of the main repository and so of every worktree. Network and other access follow the Operator's native Codex settings (01 §6).
- A worktree's git metadata lives in the main repository's `.git`, so the git common dir must be a writable root or commits fail. `--add-dir` does not reach `exec resume`, so `sandbox_workspace_write.writable_roots` carries it.
- `doctor` runs a free sandbox probe (`codex sandbox` with the same `-c` roots, in a scratch linked worktree under `~/.cache/shikiru/`, never `$TMPDIR` which Codex treats as writable) and fails when the worktree git directory or the run directory is read-only under it. It reports the Codex version and warns when that is not `TESTED_CODEX_VERSION`.
- A model change is the next `exec resume` with the new `-m`; the context is kept (verified).

**Signals**, from `--json` events:

| Event | Signal |
|---|---|
| `thread.started` | `SessionStarted(thread_id)` |
| `item.completed` `agent_message` | `Message` |
| `item.started` / `item.completed` for commands, file changes and MCP tool calls | `ToolStarted` / `ToolFinished` |
| `turn.completed` | `UsageReported`, then `TurnEnded(last agent_message)` |
| `turn.failed`, `error` | `TurnFailed` |

After exit the plugin reads the session log `~/.codex/sessions/**/rollout-*-<thread_id>.jsonl` for the latest `token_count`: `info.model_context_window` and `info.last_token_usage` give context, and non-null `rate_limits.primary`/`secondary` (`used_percent`, `window_minutes`, `resets_at`; ChatGPT plans only) give `QuotaReported`.

**Failures**: `turn.failed.error.message` is JSON in a string carrying `status`/`code`:
- 429, `usage_limit_reached` → `QUOTA`, `resets_at` from the latest `rate_limits` or the message; 402 payment/credit exhaustion → `AUTH` for immediate operator escalation
- 5xx, overloaded → `UNAVAILABLE`
- 401 → `AUTH`
- `context_length_exceeded` → the plugin's recovery (Compaction); unrecovered → `OTHER`
- else → `UNKNOWN`

**Compaction** (obligation 8), through Codex's native limit:
- Every turn whose model has a known window adds `-c model_auto_compact_token_limit=<compact_at_pct × context window>` (the flag is omitted when no window is known; the doctor gate at `start` prevents that). The window belongs to the selected model: the latest rollout `model_context_window` counts only when the `turn_context` entry preceding it names the selected model. After a rung change to another model that window is dropped, and so is one with no recorded model. The window then comes from Codex's model list (`$CODEX_HOME/models_cache.json`), as before the first turn. When the model list does not know a configured model, doctor's `context_window` check fails, so `start` refuses to run it unprotected.
- Verified: without the limit `codex exec` never compacts. With it (0.157.1), Codex compacts within a running turn at the next request boundary once its reported usage reaches the limit, and at the start of a resumed turn. One request can therefore exceed the limit by the tool output pending at that point: at a 40,800-token limit, requests of 45,264 and 45,831 input tokens were sent before the two compactions of one live turn, which then completed, recalling its nonce. The headroom (100 − `compact_at_pct`) must absorb that overshoot.
- The compaction shows only in the rollout as a `compacted` entry, so the plugin emits `Compacted` from the rollout (`pre_tokens` from the last `token_count` before it, `post_tokens` from the first non-zero one after; the `token_count` right after a `compacted` entry reports 0).
- Recovery: on a context failure, the same prompt once more via `exec resume` with the limit set, which compacts first; if that fails, `OTHER`.

**Usage**: tokens from `turn.completed.usage` (cumulative for the thread on resume, so already session totals); context from the rollout; no cost is reported anywhere → `cost_usd = None`, `cost_basis = "unknown"`.

**Other**:
- `interactive_command`: `codex resume --no-daemon <id> <args>`. The TUI otherwise resumes the thread in the shared app-server daemon, which keeps the thread's writer after the TUI exits, so the next headless `exec resume` fails with "already has an active writer" (verified 0.157.1). An untrusted worktree shows Codex's trust dialog (trust or quit); quitting hands back.
- `headless().ask`: `codex exec --ephemeral --json --output-schema <file> -m <m> -c model_reasoning_effort="<e>" -s read-only --skip-git-repo-check` → the final `agent_message`.
- Turns inherit the Operator's process environment, with `TurnSpec.env` overriding it for that turn. Headless calls and doctor inherit the process environment unchanged. Codex uses `CODEX_HOME` from that environment; Shikiru uses the same home to read model caches and rollout files.
- Doctor checks `codex login status` for the built-in provider. When `$CODEX_HOME/config.toml` selects a configured custom provider, the login check reports that provider access is Operator-managed and unverified; the CLI login status does not establish custom-provider access.

## 6. Helper (Joshu)

The `Helper` uses the single `roles.helper.ladder` rung through `Agent.headless()`. Settings reject a helper ladder with more than one rung because helper fallback is not implemented. `bootstrap` rejects a helper whose agent has no headless support.

| Call | Input | Output |
|---|---|---|
| `rebuild_report` | report schema, `last_message`, facts | report JSON, or `{"unrecoverable": reason}` |
| `classify_failure` | the `UNKNOWN` failure text | `{kind: FailureKind, confidence: 0..1, resets_at?}` |

Outputs are validated with pydantic, and invalid output counts as unrecoverable. The Helper never acts itself; the engine applies the result: a classification below `CLASSIFY_CONFIDENCE` escalates (03 §7).
