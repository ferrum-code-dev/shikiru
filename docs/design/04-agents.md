# 04 · Agents

## 1. Contract (`shikiru.agents.contracts`)

Agents are **code plugins**. The engine depends only on this module, and each plugin owns everything agent-specific:
- CLI argv and flags
- stream and session-log formats
- usage, context window and quota extraction
- error mapping
- context compaction

Rungs select only `{agent, model, effort}`. The Operator configures access in the agent's native project or user files and environment. Shikiru does not select an access mode, read credentials, or rewrite access-related environment variables; plugins pass the Operator's environment to their child processes.

**Turn model (D1).** Every turn is one agent process running headless on a session: the first turn creates the session, every later turn resumes it, also after a model change, a stall, a quota wait, an operator take-over or an engine restart. The process reads nothing from stdin (it is closed), shows no dialogs, and exits when the turn ends. Its structured output stream is the only signal source: no hooks, no screen.

```python
@dataclass(frozen=True)
class Rung:            agent: str; model: str; effort: str | None

class FailureKind(StrEnum): QUOTA = auto(); UNAVAILABLE = auto(); CONTEXT = auto(); AUTH = auto(); OTHER = auto(); UNKNOWN = auto()

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
    def interactive_command(self, session_id: str, cwd: Path) -> Sequence[str]: ...   # take over (03 §9); also the resume command
    async def doctor(self) -> list[CheckResult]: ...     # binary, version, login
    def headless(self) -> HeadlessAgent | None: ...

@dataclass(frozen=True)
class AgentContext:    options: Mapping[str, Any]; runner: SubprocessRunner; clock: Clock

type AgentFactory = Callable[[AgentContext], Agent]   # entry-point target; validates options, raises ConfigError

def shikiru_command(*arguments: str) -> str: ...   # `<python> -P -m shikiru <arguments>` for this installation
```

Prompts call Shikiru back only through `shikiru_command`. Agent shells may be login shells with their own `PATH`, so nothing agent-facing relies on `shikiru` being on it.

**Obligations**, checked by the contract kit:
1. `SessionStarted` exactly once per turn, before any other signal. For a resumed turn its id equals `spec.session_id`.
2. `Message`, `ToolStarted` and `ToolFinished` as the agent works; a tool runs between its `ToolStarted` and `ToolFinished`.
3. Every stream ends with exactly one `TurnEnded` or `TurnFailed`, and then the process has exited. A process that exits without a structured result yields `TurnFailed(UNKNOWN)` with the exit status and the stderr tail.
4. `UsageReported` carries session totals; its token counts never decrease (`context_used_pct` drops after `Compacted`).
5. **Usage is only what the agent reports** (D21): tokens always; `cost_usd` only when the agent marks its cost as priced, else `None` with `cost_basis = "unknown"`; `context_used_pct` only from a context window the agent reports reliably, else `None`; `QuotaReported` only when the agent reports windows. `cost_basis` is `api_equivalent` for priced subscription usage and `billed` for priced API-key usage.
6. The process never waits for input: stdin is closed and no flag it is given can open a dialog.
7. `stop()` ends the process within `TURN_STOP_GRACE` (SIGTERM, then SIGKILL); the next turn on the same `session_id` resumes the session.
8. **Compaction.** Headless turns never compact reliably on their own, so the plugin keeps the session's context below its `compact_at_pct` option (default 80):
   - *Before* the turn, the plugin compacts when the session is at or above the threshold (Claude), or starts the turn with the agent's own compaction limit set (Codex).
   - *After* a context failure, the plugin compacts and re-runs the same prompt once inside the same `Turn` (prompts are idempotent). Only a second context failure ends the turn with `TurnFailed(CONTEXT)`.
   - Every compaction emits `Compacted`. `SessionStarted` and the final `TurnEnded`/`TurnFailed` still occur once per `Turn`.

**Failure mapping.** A plugin maps the structured errors it knows to a `FailureKind` and returns `UNKNOWN` with the raw text for anything else. The engine asks the Helper to classify `UNKNOWN` (§6); the Helper never replaces the structured path (D22).

## 2. SDK (`shikiru.agents.sdk`)

Small, tested building blocks for plugins:

| Block | Purpose |
|---|---|
| `TurnParser` | Protocol a plugin implements: `feed(line) → list[AgentSignal]` for one stdout line, `finish(exit_code, stderr) → list[AgentSignal]` at exit. Pure, so recorded streams replay offline |
| `StreamingTurn` | `Turn` implementation: spawns the argv through `SubprocessRunner.spawn` with stdin closed, copies stdout to `turn_dir/<role>.stream.jsonl` and stderr to `turn_dir/<role>.stderr.log`, feeds lines to the parser, enforces obligations 1 and 3, and implements `stop()`. Runs the plugin's process sequence for one turn (compact, prompt, compact-and-rerun), appending every process's output to the same files |
| `JsonlTail` | Incremental reader for agent-written JSONL (e.g. a session log), safe with partial lines |
| `ResetTimeParser` | "resets 3:10am (Europe/Warsaw)", ISO, epoch, relative durations → `datetime` |

## 3. Contract test kit (`shikiru.agents.testing`)

`AgentContractSuite` is a pytest base class. A plugin subclasses it and provides its factory, its `TurnParser` and sample files.

**Offline checks** (always run; samples are recorded streams committed under `tests/fixtures/agents/<agent>/`, 07 §4):
1. Each recorded stream (stdout lines, stderr, exit code, plus session-log excerpts when the plugin reads them) replays through the parser to the expected signal sequence and satisfies obligations 1–5.
2. Recorded error streams map to the expected `FailureKind` and `resets_at`; unmapped text yields `UNKNOWN`.
3. Recorded compaction output yields `Compacted`; a context failure followed by a compaction and a rerun yields one `Turn` ending in `TurnEnded`, and a second context failure yields `TurnFailed(CONTEXT)`.
4. Argv for a new turn, a resumed turn, a model change and compaction; Operator-provided environment pass-through; `interactive_command`.

**Live checks** (`SHIKIRU_E2E=1`, scratch repository):
1. A first turn whose prompt points at a prompt file → `SessionStarted`, a valid report through `shikiru report`, `TurnEnded`.
2. `UsageReported` tokens > 0.
3. A resumed turn on another model of the same agent recalls a nonce from turn 1.
4. `stop()` during a turn, then a resumed turn completes.
5. `headless().ask()` returns schema-valid JSON (when provided).
6. With a low `compact_at_pct`, a resumed turn compacts (`Compacted`) and still recalls a nonce from turn 1.

`shikiru doctor --probe` runs live check 1 for each agent on a ladder through the same `start_turn` the engine uses, in a scratch repository reused at `.shikiru/probe/`. No dialog needs answering.

## 4. Claude Code plugin (`plugins/agents/claude`) — verified with 2.1.281

**Options** (`ClaudeOptions`):

Claude reads access from the Operator's native Claude settings and environment. The plugin does not configure access modes, choose key variables, or remove variables. Turn and headless child processes inherit the Operator's environment; turn-specific Shikiru variables are added from `TurnSpec.env`.

| Key | Default | Meaning |
|---|---|---|
| `args` | `["--permission-mode", "bypassPermissions"]` | Extra CLI args; must keep the turn unattended |
| `compact_at_pct` | `80` | Context use that triggers compaction (obligation 8) |

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
- prompt too long / context terminal reason → `CONTEXT`
- else → `UNKNOWN`

**Usage**:
- Tokens: `spec.usage` plus the turn's `result.usage`.
- Cost: `result.total_cost_usd` (cumulative per session) only when `modelUsage.<m>.costBasis` is `"list"`; report it as `api_equivalent`, independent of how access is configured. Other bases (e.g. OpenRouter or other non-Anthropic-priced models, where the list-price estimate is wrong) → `None`, `unknown`.
- Context: the last assistant message's input and cache tokens ÷ `modelUsage.<m>.contextWindow`, only when the cost basis is `"list"` (other providers report a fixed 200000 default).

**Compaction** (obligation 8):
- Before a resumed turn whose `spec.usage.context_used_pct` ≥ `compact_at_pct`, the plugin first runs `claude -p "/compact" --resume <id>` with the same model and flags. Verified: a headless `/compact` emits `compact_boundary` (47,571 → 1,949 tokens), and the next turn still recalls earlier context.
- `context_used_pct` is only known for list-priced models (see Usage). On other providers only the after-failure path applies.
- On a `CONTEXT` failure: `/compact`, then the same prompt once more. *Verify in implementation* that `/compact` succeeds on a session that already overflowed. If it does not, a Claude context failure goes straight to `TurnFailed(CONTEXT)` (handoff, 03 §7).
- Not relied on: Claude's own auto-compaction. In `-p` mode it did not trigger, even with `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`.

**Other**:
- `interactive_command`: `claude --resume <id> --add-dir <run_dir> <args>` (verified: its turns land in the same session, and the next headless `--resume` sees them).
- `headless().ask`: `claude -p --output-format json --json-schema <s> --model <m> --effort <e> --safe-mode --tools "" --no-session-persistence` → `structured_output`.

## 5. Codex plugin (`plugins/agents/codex`) — verified with 0.156.1

**Options** (`CodexOptions`):

| Key | Default | Meaning |
|---|---|---|
| `args` | `[]` | Extra CLI args |
| `compact_at_pct` | `80` | Context use that triggers compaction (obligation 8) |

Codex reads access from the Operator's native Codex settings and environment. The plugin must not configure access modes, choose key variables, or remove variables.

**Turn** (cwd = task root, stdin closed — `codex exec` waits on an open stdin):

```
codex exec --json -m <m> -c model_reasoning_effort="<e>" --sandbox workspace-write
           --add-dir <run_dir> --add-dir <git common dir> <args> "<prompt>"                  # new session
codex exec resume <thread_id> --json -m <m> -c model_reasoning_effort="<e>" <args> "<prompt>"  # later turns
```

- `exec resume` has no `--sandbox` flag and keeps the session's sandbox.
- A worktree's git metadata lives in the main repository's `.git`, so the git common dir must be a writable root or commits fail. *Verify in implementation* that `--add-dir` grants it and persists across `exec resume`; otherwise pass it as `sandbox_workspace_write.writable_roots`.
- A model change is the next `exec resume` with the new `-m`; the context is kept (verified).

**Signals**, from `--json` events:

| Event | Signal |
|---|---|
| `thread.started` | `SessionStarted(thread_id)` |
| `item.completed` `agent_message` | `Message` |
| `item.started` / `item.completed` for commands, file changes and MCP tool calls | `ToolStarted` / `ToolFinished` |
| `turn.completed` | `UsageReported`, then `TurnEnded(last agent_message)` |
| `turn.failed`, `error` | `TurnFailed` |

After exit the plugin reads the session log `~/.codex/sessions/**/rollout-*-<thread_id>.jsonl` (`JsonlTail`) for the latest `token_count`: `info.model_context_window` and `info.last_token_usage` give context, and non-null `rate_limits.primary`/`secondary` (`used_percent`, `window_minutes`, `resets_at`; ChatGPT plans only) give `QuotaReported`.

**Failures**: `turn.failed.error.message` is JSON in a string carrying `status`/`code`:
- 429, `usage_limit_reached` → `QUOTA`, `resets_at` from the latest `rate_limits` or the message; 402 payment/credit exhaustion → `AUTH` for immediate operator escalation
- 5xx, overloaded → `UNAVAILABLE`
- 401 → `AUTH`
- `context_length_exceeded` → `CONTEXT` (after the compact-and-rerun of obligation 8)
- else → `UNKNOWN`

**Compaction** (obligation 8):
- Every turn adds `-c model_auto_compact_token_limit=<compact_at_pct × context window>`. The window is the model's `model_context_window` from the latest rollout, or, before the first turn, from Codex's model list (`$CODEX_HOME/models_cache.json`). When neither knows the model, the limit is omitted.
- Without the limit, `codex exec` never compacts. With it (verified), Codex compacts at the start of the next turn whose context exceeds it, keeping earlier context. The compaction shows only in the rollout as a `compacted` entry, so the plugin emits `Compacted` from the rollout (`pre_tokens` from the last `token_count` before it, `post_tokens` from the first after).
- On a `CONTEXT` failure: the same prompt once more via `exec resume` with the limit set, which compacts first.

**Usage**: tokens from `turn.completed.usage` (cumulative for the thread on resume, so already session totals); context from the rollout; no cost is reported anywhere → `cost_usd = None`, `cost_basis = "unknown"`.

**Other**:
- `interactive_command`: `codex resume <id> <args>`.
- `headless().ask`: `codex exec --ephemeral --json --output-schema <file> -m <m> -c model_reasoning_effort="<e>" -s read-only --skip-git-repo-check` → the final `agent_message`.

## 6. Helper (Joshu)

The `Helper` uses the single `roles.helper.ladder` rung through `Agent.headless()`. Settings reject a helper ladder with more than one rung because helper fallback is not implemented. `bootstrap` rejects a helper whose agent has no headless support.

| Call | Input | Output |
|---|---|---|
| `rebuild_report` | report schema, `last_message`, facts | report JSON, or `{"unrecoverable": reason}` |
| `classify_failure` | the `UNKNOWN` failure text | `{kind: FailureKind, confidence: 0..1, resets_at?}` |

Outputs are validated with pydantic, and invalid output counts as unrecoverable. The Helper never acts itself; the engine applies the result: a classification below `CLASSIFY_CONFIDENCE` escalates (03 §7).
