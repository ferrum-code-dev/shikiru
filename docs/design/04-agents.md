# 04 · Agents

## 1. Contract (`shikiru.agents.contracts`)

Agents are **code plugins**. The engine depends only on this module, and each plugin owns everything agent-specific:
- CLI argv and auth
- hooks and transcripts
- prices and context windows
- quota sources
- error mapping
- compaction policy

The contract doesn't require hooks. A plugin must deliver the signals below by whatever means its agent allows, and prove it with the contract test kit (§3).

```python
@dataclass(frozen=True)
class Rung:            agent: str; model: str; effort: str | None

@dataclass(frozen=True)
class SessionKeys:     interrupt: tuple[str, ...]; exit_input: str; safe: tuple[str, ...]

class FailureKind(StrEnum): QUOTA = auto(); UNAVAILABLE = auto(); CONTEXT = auto(); AUTH = auto(); OTHER = auto()

@dataclass(frozen=True) class SessionStarted:  session_id: str | None     # None → session can't be resumed
@dataclass(frozen=True) class PromptSubmitted: text: str
@dataclass(frozen=True) class Activity:        tool_running: bool
@dataclass(frozen=True) class TurnEnded:       last_message: str
@dataclass(frozen=True) class TurnFailed:      kind: FailureKind; message: str; resets_at: datetime | None
@dataclass(frozen=True) class Compacted:       pass
@dataclass(frozen=True) class AttentionNeeded: message: str
@dataclass(frozen=True) class SessionEnded:    pass
type AgentSignal = (SessionStarted | PromptSubmitted | Activity | TurnEnded | TurnFailed
                    | Compacted | AttentionNeeded | SessionEnded)

@dataclass(frozen=True)
class Usage:           input_tokens: int; output_tokens: int; cache_read_tokens: int; cache_write_tokens: int
                       cost_usd: float | None; cost_basis: Literal["billed", "api_equivalent", "unknown"]
                       context_used_pct: float | None
@dataclass(frozen=True)
class QuotaWindow:     name: str; used_pct: float; resets_at: datetime | None; observed_at: datetime
@dataclass(frozen=True)
class CheckResult:     name: str; ok: bool; detail: str

@dataclass(frozen=True)
class LaunchSpec:      rung: Rung; role: str; cwd: Path; run_dir: Path; add_dirs: tuple[Path, ...]
                       env: Mapping[str, str]; resume_session_id: str | None

class PaneHandle(Protocol):
    """The single tmux pane an agent session lives in."""
    async def respawn(self, argv: Sequence[str], env: Mapping[str, str | None], cwd: Path) -> None: ...
    async def capture_visible(self) -> str: ...
    async def is_alive(self) -> bool: ...
    async def send_keys(self, *keys: str) -> None: ...

class AgentSession(Protocol):
    """A live interactive agent session. `signals()` must be consumed by exactly one reader."""
    def signals(self) -> AsyncIterator[AgentSignal]: ...
    async def before_turn(self) -> list[str]: ...         # lines to inject before a turn, e.g. a compaction command
    async def usage(self) -> Usage: ...                  # cumulative for the session
    def snapshot(self) -> Mapping[str, Any]: ...         # opaque state for attach()

class HeadlessAgent(Protocol):
    """One-shot, schema-constrained, non-interactive calls."""
    async def ask(self, prompt: str, schema: Mapping[str, Any], model: str, effort: str | None,
                  cwd: Path, timeout: timedelta) -> Mapping[str, Any]: ...

class InteractiveAgent(Protocol):
    """An agent CLI plugin."""
    name: str
    keys: SessionKeys
    async def launch(self, spec: LaunchSpec, pane: PaneHandle) -> AgentSession: ...   # returns once the agent accepts input
    async def attach(self, spec: LaunchSpec, pane: PaneHandle, state: Mapping[str, Any]) -> AgentSession: ...
    async def quota(self) -> list[QuotaWindow]: ...      # [] when unknown; plugin owns caching and probing
    async def doctor(self) -> list[CheckResult]: ...     # binary, version, login
    def resume_command(self, session_id: str, cwd: Path) -> str | None: ...
    def headless(self) -> HeadlessAgent | None: ...

@dataclass(frozen=True)
class AgentContext:    options: Mapping[str, Any]; runner: SubprocessRunner; clock: Clock

type AgentFactory = Callable[[AgentContext], InteractiveAgent]   # entry-point target; validates options, raises ConfigError

def shikiru_command(*arguments: str) -> str: ...   # `<python> -P -m shikiru <arguments>` for this installation
```

Prompts and plugin hooks call Shikiru back only through `shikiru_command`. Agent shells may be login shells with their own `PATH`, so nothing agent-facing relies on `shikiru` being on it.

**Obligations**, checked by the contract kit:
1. `SessionStarted` exactly once per launch or attach, no later than the first accepted prompt. `launch` returns once the agent accepts input, having answered the agent's own first-use dialogs.
2. `PromptSubmitted` for every prompt the agent accepts, whether it was injected or typed.
3. `TurnEnded` when a turn finishes, and `TurnFailed` instead when it fails.
4. `Activity` at least every 60 s while the agent works; `tool_running` is true while a tool runs.
5. `usage()` never decreases.
6. `cost_basis` is `api_equivalent` under subscription auth and `billed` with an API key.

## 2. SDK (`shikiru.agents.sdk`)

Small, tested building blocks for plugins:

| Block | Purpose |
|---|---|
| `HookRecorder` + `shikiru hook` | The hook command appends its payload as one compact JSON line to `$SHIKIRU_RUN_DIR/$SHIKIRU_ROLE/hooks.jsonl` under `flock`, taking both from the launch environment (03 §4). It does nothing when they are unset, i.e. outside Shikiru. `HookRecorder.tail(offset)` yields entries |
| `JsonlTail` | Incremental reader for agent-written JSONL, safe with partial lines |
| `ResetTimeParser` | "resets 3:10am (Europe/Warsaw)", ISO, epoch, relative durations → `datetime` |
| `PriceTable` | Packaged `prices.toml` (per model: input, cache read/write, output per 1M tokens, context window) → `cost_usd`, `context_used_pct` |

## 3. Contract test kit (`shikiru.agents.testing`)

`AgentContractSuite` is a pytest base class. A plugin subclasses it and provides a factory and sample files.

**Offline checks** (always run; samples are real agent files committed under `tests/fixtures/agents/<agent>/`):
1. Sample hook and transcript files map to the expected signal sequence.
2. Usage and cost computed from samples match the expected values.
3. Sample error entries map to the expected `FailureKind` and `resets_at`.
4. `launch` argv and env for new, resume and each auth mode (against a recording `PaneHandle`).

**Live checks** (`SHIKIRU_E2E=1`, private tmux socket, scratch repo):
1. Launch → `SessionStarted`.
2. Pointer injection → `PromptSubmitted` → report submitted → `TurnEnded`.
3. `usage()` tokens > 0.
4. Exit and resume recalls a nonce from turn 1 (when `session_id` is set).
5. `quota()` returns windows or an empty list, without error.
6. `headless().ask()` returns schema-valid JSON (when provided).

`shikiru doctor --probe` runs live checks 1–2 for each configured agent through the same `launch` the engine uses.

## 4. Claude Code plugin (`plugins/agents/claude`) — verified with 2.1.273

**Options** (`ClaudeOptions`):

| Key | Default | Meaning |
|---|---|---|
| `auth` | `subscription` | `subscription` unsets `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`; `api_key` sets `ANTHROPIC_API_KEY` from `api_key_env` |
| `api_key_env` | `ANTHROPIC_API_KEY` | Env var holding the key |
| `args` | `["--permission-mode", "auto"]` | Extra CLI args |

**Launch**:

```
claude (--session-id <uuid> | --resume <id>) --model <m> --effort <e>
       --settings '<json: hooks only>' --add-dir <run_dir> <args>
```

- Hooks run `shikiru_command("hook")` for `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `StopFailure`, `Notification`, `PostCompact`, `SessionEnd`.
- The hooks merge with user and project hooks. Nothing else in the user's settings is touched.
- `launch` returns on the first new hook line, answering the workspace-trust and bypass-permissions dialogs if they show.

**Signals**, from hooks (payloads verified):

| Hook | Signal |
|---|---|
| `SessionStart` | `SessionStarted`; the plugin also keeps `transcript_path` |
| `UserPromptSubmit` | `PromptSubmitted` |
| `Pre`/`PostToolUse` | `Activity` |
| `Stop` | `TurnEnded(last_assistant_message)` |
| `StopFailure` | `TurnFailed`, with the kind taken from the transcript's latest error entry |
| `Notification` | `AttentionNeeded` |
| `PostCompact` | `Compacted` |
| `SessionEnd` | `SessionEnded` |

**Usage** comes from the transcript (verified):
- `assistant` entries' `message.usage`, deduplicated by `message.id`.
- Context from the last message's input and cache tokens ÷ the context window.
- Cost via `PriceTable`.

**Failures** come from transcript entries with `isApiErrorMessage` (verified):
- 429 or "limit" → `QUOTA`, with `resets_at` parsed from e.g. `You've hit your session limit · resets 3:10am (Europe/Warsaw)`
- 5xx / 529 → `UNAVAILABLE`
- 401 / 403 → `AUTH`
- "prompt is too long" → `CONTEXT`
- else → `OTHER`

**Quota** (verified):
- Probe: `claude -p "." --output-format stream-json --verbose --model haiku --safe-mode --tools "" --no-session-persistence`, reading `rate_limit_event.rate_limit_info.unifiedWindows.{five_hour,seven_day}`.
- It is cached for 15 min, refreshed after a `QUOTA` failure, and skipped with `api_key` auth.

**Other**:
- `before_turn` → `[]` (Claude auto-compacts).
- `keys`: interrupt `Escape`, exit `/exit`, safe `Enter Escape Down 1 2`.
- `resume_command`: `claude --resume <id>`.
- `headless().ask`: `claude -p --output-format json --json-schema <s> --model <m> --effort <e> --safe-mode --tools "" --no-session-persistence` → `structured_output`.

## 5. Codex plugin (`plugins/agents/codex`) — verified with 0.154.0

**Options** (`CodexOptions`):

| Key | Default | Meaning |
|---|---|---|
| `auth` | `subscription` | `subscription` unsets `OPENAI_API_KEY`/`CODEX_API_KEY`; `api_key` sets `CODEX_API_KEY` |
| `api_key_env` | `OPENAI_API_KEY` | Env var holding the key |
| `args` | `["--approve-for-me"]` | Extra CLI args |
| `compact_below_free_pct` | `60` | `before_turn` returns `["/compact"]` when free context is below this |

**Launch**:

```
codex [resume <id>] -m <m> -c model_reasoning_effort="<e>" --dangerously-bypass-hook-trust
      -c 'hooks.<Event>=[{hooks=[{type="command",command="<shikiru_command hook>"}]}]'   # per event
      -c 'projects."<cwd>".trust_level="trusted"' -C <cwd> --add-dir <run_dir> --add-dir <git common dirs> <args>
```

Codex emits `SessionStart` only after its first prompt, so `launch` waits for its input prompt on screen and accepts the first-use trust dialog (Codex reads trust only from `config.toml`, not from `-c`). Every launch also passes `-c check_for_update_on_startup=false -c notice.hide_rate_limit_model_nudge=true`, so no unattended pane waits on an update or model-switch dialog.

**Signals** (hook payloads verified):

| Hook | Signal |
|---|---|
| `SessionStart` | `SessionStarted`; if the rollout path is null it is found via `~/.codex/sessions/**/rollout-*-<id>.jsonl` |
| `UserPromptSubmit` | `PromptSubmitted` |
| `Pre`/`PostToolUse` | `Activity` |
| `Stop` | `TurnEnded` |
| `PermissionRequest` | `AttentionNeeded` |
| `PostCompact` | `Compacted` |

Rollout `error` events → `TurnFailed`:
- usage limit → `QUOTA`, with `resets_at` from the latest `rate_limits`
- overloaded or 5xx → `UNAVAILABLE`
- 401 → `AUTH`
- context exceeded → `CONTEXT`
- else → `OTHER`

**Usage** comes from rollout `token_count` records (verified):
- `total_token_usage` for token counts.
- `last_token_usage` ÷ the context window for context use.
- Cost via `PriceTable`.

**Quota** (verified): the latest rollout `rate_limits.primary`/`secondary` (`used_percent`, `window_minutes`, `resets_at`).

**Other**:
- `keys`: interrupt `Escape`, exit `/quit`, safe `Enter Escape Down 1 2 y`.
- `resume_command`: `codex resume <id>`.
- `headless().ask`: `codex exec --ephemeral --json --output-schema <file> -m <m> -c model_reasoning_effort="<e>" -s read-only --skip-git-repo-check` → the final `agent_message`.

## 6. Helper (Joshu)

The `Helper` uses the `roles.helper` ladder through `InteractiveAgent.headless()`. `bootstrap` rejects helper rungs whose agent has no headless support.

| Call | Input | Output |
|---|---|---|
| `rebuild_report` | report schema, `last_message` (or visible screen), facts | report JSON, or `{"unrecoverable": reason}` |
| `classify_screen` | visible screen, `keys.safe` | `{state: working/dialog/question/quota/error, keys?, text?, resets_at?}` |

Outputs are validated with pydantic, and invalid output counts as unrecoverable. The Helper never acts itself; the engine applies the result.
