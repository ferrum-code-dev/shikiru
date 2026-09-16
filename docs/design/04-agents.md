# 04 · Agents

## 1. Contracts (`shikiru.agents.contracts`)

**Agents are code plugins**: Python packages registered under the `shikiru.agents` entry point group. Each one is tested against the shared contract test kit (07-engineering §3).
Supporting a new agent means writing and testing a plugin, **never** describing an agent in configuration. That approach (v4's `profiles/*.toml` with argv and regexes) is rejected.

**Dependency rule (DIP)**: the orchestrator depends only on `shikiru.agents.contracts`, and it knows nothing about any specific agent:
- not transcripts, hooks or CLIs,
- not model names, prices or context windows,
- not how quota is read.

Plugins own all of that and hand the engine **normalized** data: signals, usage, quota, failure kinds.
Model identifiers in ladders are opaque strings that the engine passes through to the plugin.

```python
class Capability(StrEnum):
    RESUME = "resume"                 # can reopen a session by id (else engine recovers via handoff)
    PROMPT_EVENTS = "prompt_events"   # emits PromptSubmitted for every submitted prompt (enables precise intervention detection)
    QUOTA = "quota"                   # can report subscription quota windows
    HEADLESS = "headless"             # can serve as a Helper rung

@dataclass(frozen=True)
class Rung:        agent: str; model: str; effort: str | None
@dataclass(frozen=True)
class LaunchSpec:  rung: Rung; role: str; cwd: Path; run_dir: Path; add_dirs: list[Path]
                   resume_session_id: str | None; restore: Mapping[str, Any] | None; env: Mapping[str, str]

type AgentSignal = (SessionStarted | PromptSubmitted | TurnEnded | TurnFailed | Activity
                    | Compacted | AttentionNeeded | SessionEnded)

@dataclass(frozen=True)
class TurnFailed:  kind: FailureKind; message: str; resets_at: datetime | None
class FailureKind(StrEnum): QUOTA, UNAVAILABLE, CONTEXT, AUTH, OTHER

@dataclass(frozen=True)
class Usage:       input_tokens: int; output_tokens: int; cache_read_tokens: int; cache_write_tokens: int
                   cost_usd: float | None; cost_basis: Literal["billed", "api_equivalent", "unknown"]
                   context_used_pct: float | None
@dataclass(frozen=True)
class QuotaWindow: name: str; used_pct: float; resets_at: datetime | None; observed_at: datetime

class AgentSession(Protocol):
    """One live agent session in a tmux pane, created by an InteractiveAgent for a task role."""
    session_id: str | None
    async def signals(self) -> AsyncIterator[AgentSignal]: ...        # normalized, incl. TurnEnded for hook-less agents
    def before_turn(self) -> list[str]: ...                            # lines to inject first (e.g. "/compact"), may be empty
    def usage(self) -> Usage: ...                                      # cumulative for this session
    def interrupt_keys(self) -> list[str]: ...
    def exit_input(self) -> str: ...
    def safe_keys(self) -> list[str]: ...
    def snapshot(self) -> dict[str, Any]: ...                          # opaque plugin state (offsets, transcript path); engine persists it in runs.agent_state and passes it back as LaunchSpec.restore

class InteractiveAgent(Protocol):
    """An agent CLI plugin that runs interactive sessions inside tmux panes."""
    name: str
    capabilities: frozenset[Capability]
    options_model: type[BaseModel]                                     # validates [agents.<name>.options]
    def launch(self, spec: LaunchSpec, pane: PaneHandle) -> AgentSession: ...
    def quota(self) -> list[QuotaWindow]: ...                          # [] without QUOTA; plugin decides caching/probing
    def doctor(self) -> list[CheckResult]: ...                         # binary, version, login
    def resume_hint(self, session_id: str, cwd: Path) -> str | None: ...  # command an Operator can run to reopen a session

class HeadlessAgent(Protocol):
    """An agent plugin that answers one schema-constrained prompt non-interactively."""
    def ask(self, prompt: str, schema: dict, rung: Rung, cwd: Path, timeout_s: int) -> dict: ...
```

What each side owns:

| Concern | Orchestrator | Plugin |
|---|---|---|
| When to run a turn, what the prompt says, waiting, retries, ladders, escalation | ✔ | |
| Prompt injection mechanics (paste a pointer line), report file validation | ✔ (generic, the same for every agent) | |
| Launch argv and env, auth handling, hooks, transcripts, turn-end detection | | ✔ |
| Mapping native errors to `FailureKind` with `resets_at` | | ✔ |
| Tokens, cost (its own price table), context %, whether to compact | | ✔ |
| Quota windows (passive reading or active probing, cadence) | | ✔ |
| Showing usage and quota, `budget_usd` checks | ✔ (on normalized values) | |

`PaneHandle` is the narrow tmux facade a plugin gets for its own pane: `respawn(argv, env)`, `capture_visible()`, `process_tree()`, `is_alive()`. Plugins never see the session, windows or other tasks.

### 1.1 Agent SDK (`shikiru.agents.sdk`)

These are reusable, tested building blocks that plugins **compose in code**. They are not configuration:

| Building block | Purpose |
|---|---|
| `HookRecorder` + `shikiru hook` CLI | Hook commands append trimmed JSON lines to `$SHIKIRU_RUN_DIR/hooks.jsonl` (flock, < 100 ms, a no-op outside Shikiru). `HookRecorder.tail()` yields them incrementally from a stored offset |
| `JsonlTail` | Incremental reader for agent-written JSONL transcripts, safe with partial lines |
| `PaneActivity` | Agent-agnostic `Activity` and quiescence detection: screen hash with digits masked, plus pane process-tree CPU below a threshold for N seconds |
| `QuiescentTurnDetector` | For agents without a turn-end hook: emits `TurnEnded` after the report file plus quiescence, or after a longer quiescence without a report |
| `ResetTimeParser` | Parses "resets 3:10am (Europe/Warsaw)", ISO timestamps, epoch values and relative durations |
| `PriceTable` | Loads a plugin's packaged `prices.toml`, merges `options.prices` overrides, and computes `cost_usd` from `Usage` token counts |
| `CachedProbe` | Rate-limited, cached execution of an active quota probe |

A plugin for an agent without hooks is therefore a small class: argv plus `PaneActivity` plus `QuiescentTurnDetector`. Anything that doesn't fit is handled by the plugin's own code and its own tests.

### 1.2 Contract test kit (`shikiru.agents.testing`)

`AgentContractSuite` is a pytest class that every plugin subclasses, providing a way to launch its agent. It checks against a real pane on a private tmux socket:
1. launch → a signal stream starts; `session_id` is set if the plugin declares `RESUME`;
2. pointer injection → the agent submits a report → `TurnEnded`;
3. `PromptSubmitted` is emitted if the plugin declares `PROMPT_EVENTS`;
4. `usage()` returns increasing token counts, and `cost_basis` matches the auth mode;
5. resume recalls a nonce from the first turn (`RESUME`);
6. `quota()` returns windows (`QUOTA`);
7. `ask()` returns schema-valid JSON (`HEADLESS`);
8. recorded failure fixtures map to the expected `FailureKind` and `resets_at`.

`shikiru doctor --probe` runs the live parts of the same suite for the configured agents.

### 1.3 Engine behaviour by capability

| Capability absent | Engine behaviour |
|---|---|
| `RESUME` | Crash, restart and rung-change recovery start a new session with `handoff.md.j2` |
| `PROMPT_EVENTS` | Intervention is detected as `Activity` while no turn is in flight; Operators pause before typing (03-workflow §10) |
| `QUOTA` | No quota bars for that agent. A `TurnFailed(QUOTA)` without `resets_at` is probed by re-injecting `continue.md` every 15 min |
| `HEADLESS` | The agent can't be a Helper rung (validated at config load) |

## 2. Claude Code plugin (`plugins/agents/claude`) — verified with 2.1.273

Capabilities: `RESUME`, `PROMPT_EVENTS`, `QUOTA` (subscription auth), `HEADLESS`.

**Options** (`ClaudeOptions`):

| Option | Default | Meaning |
|---|---|---|
| `quota_probe_interval` | `900` | Seconds between probes while this agent has live sessions (`0` = off) |
| `compact_below_free_pct` | `0` | `0` = rely on Claude's own auto-compact |
| `prices` | `{}` | Overrides of the packaged `prices.toml` |

**Launch**:

```
claude --session-id <uuid> | --resume <id>
       --model <m> --effort <e>
       --settings '<json: hooks only>'
       --add-dir <run_dir> …
       <args>                          # default: --permission-mode auto
```

- Env: subscription auth unsets `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`; `api_key` auth sets `ANTHROPIC_API_KEY` from `api_key_env`.
- `--settings` only adds hooks, which merge with user and project hooks. Nothing else of the user's configuration is touched.

**Signals** (hook payloads verified):

| Hook | Signal |
|---|---|
| `SessionStart` | `SessionStarted(session_id)`, and the plugin keeps `transcript_path` |
| `UserPromptSubmit` | `PromptSubmitted(prompt)` |
| `PreToolUse` / `PostToolUse` | `Activity` |
| `Stop` | `TurnEnded(last_message)` |
| `StopFailure` | `TurnFailed`, with the kind refined from the transcript |
| `Notification` | `AttentionNeeded` |
| `PostCompact` | `Compacted` |
| `SessionEnd` | `SessionEnded` |

**Usage**, from the transcript Claude writes itself (verified):
- Token counts from `assistant` entries' `message.usage`, deduplicated by `message.id`.
- `context_used_pct` from the last message's `input + cache_read + cache_creation` tokens ÷ the model's context window in `prices.toml`.
- `cost_usd` from `PriceTable`. `cost_basis` is `api_equivalent` under subscription auth and `billed` with an API key.

**Failures**: transcript entries with `isApiErrorMessage: true`, `apiErrorStatus` and text:
- 429 or "limit" → `QUOTA`, with `resets_at` parsed from e.g. `"You've hit your session limit · resets 3:10am (Europe/Warsaw)"`
- 5xx / 529 → `UNAVAILABLE`
- 401 / 403 → `AUTH`
- "prompt is too long" → `CONTEXT`
- otherwise → `OTHER`

**Quota** (verified): the probe `claude -p "." --output-format stream-json --verbose --model haiku --safe-mode --tools "" --no-session-persistence` → `rate_limit_event.rate_limit_info.unifiedWindows.{five_hour,seven_day}` (`utilization`, `resetsAt`).
- It runs through `CachedProbe` at `quota_probe_interval`, and immediately after a `QUOTA` failure.

**Session keys and commands**:
- `before_turn` returns `["/compact"]` when `context_used_pct > 100 - compact_below_free_pct`.
- `exit_input = "/exit"`, `interrupt_keys = ["Escape"]`.
- `safe_keys = ["Enter", "Escape", "Down", "1", "2"]`.

**Headless**: `claude -p --output-format json --json-schema <schema> --model <m> --effort <e> --safe-mode --tools "" --no-session-persistence` → `structured_output`.

**Workspace trust**: a trust dialog for a new worktree dir raises `AttentionNeeded`. The Helper's `classify_screen` answers it using `safe_keys`, and the contract suite checks that no dialog stays open.

## 3. Codex plugin (`plugins/agents/codex`) — verified with 0.154.0

Capabilities: `RESUME`, `PROMPT_EVENTS`, `QUOTA`, `HEADLESS`.

**Options** (`CodexOptions`):

| Option | Default | Meaning |
|---|---|---|
| `compact_below_free_pct` | `60` | Compact before a turn when free context is below this |
| `prices` | `{}` | Overrides of the packaged `prices.toml` |

**Launch**:

```
codex [resume <id>]
      -m <m> -c model_reasoning_effort="<e>"
      --dangerously-bypass-hook-trust
      -c 'hooks.<Event>=[{hooks=[{type="command",command="shikiru hook"}]}]'     # per event
      -c 'projects."<cwd>".trust_level="trusted"'
      -C <cwd> --add-dir <run_dir> --add-dir <git common dirs>
      <args>                                 # default: --approve-for-me
```

- Env: subscription auth unsets `OPENAI_API_KEY`/`CODEX_API_KEY`; `api_key` auth sets `CODEX_API_KEY`.

**Signals** (hook payloads verified):

| Hook | Signal |
|---|---|
| `SessionStart` | `SessionStarted`. If `transcript_path` is null, the rollout file is found via `~/.codex/sessions/**/rollout-*-<id>.jsonl` |
| `UserPromptSubmit` | `PromptSubmitted` |
| `Pre`/`PostToolUse` | `Activity` |
| `Stop` | `TurnEnded` |
| `PermissionRequest` | `AttentionNeeded` |
| `PostCompact` | `Compacted` |

**Usage**, from the rollout Codex writes itself (verified): `token_count` records.
- Tokens from `info.total_token_usage`.
- Context from `info.last_token_usage` ÷ the context window (from the record, or from `prices.toml`).
- Cost via `PriceTable`.

**Failures**: `error` events. Usage limit → `QUOTA`, with `resets_at` from the latest `rate_limits`. Overloaded or 5xx → `UNAVAILABLE`. 401 → `AUTH`. Context window exceeded → `CONTEXT`.

**Quota** (passive, verified): the latest `rate_limits.primary`/`secondary` (`used_percent`, `window_minutes`, `resets_at`) across recent rollouts.

**Session keys and commands**:
- `before_turn` returns `["/compact"]` below the threshold.
- `exit_input = "/quit"`, `interrupt_keys = ["Escape"]`.
- `safe_keys = ["Enter", "Escape", "Down", "1", "2", "y"]`.

**Headless**: `codex exec --ephemeral --json --output-schema <file> -m <m> -c model_reasoning_effort="<e>" -s read-only --skip-git-repo-check` → the final `agent_message`.

## 4. Helper (Joshu 助手)

`Helper` uses the `roles.helper` ladder through `HeadlessAgent.ask`. Every call has a template and a pydantic result model, and the output is always validated:

| Call | Input | Output | Used by |
|---|---|---|---|
| `rebuild_report` | report schema, `TurnEnded.last_message` or the last 120 screen lines, git facts | report JSON or `{"unrecoverable": reason}` | 03 §4.8 |
| `classify_failure` | `TurnFailed(OTHER).message` | `{kind, resets_at?, summary}` | 03 §8.1 |
| `classify_screen` | visible pane text, `safe_keys` | `{state: working/dialog/question/quota/error, keys?, text?, resets_at?}` | 03 §8.5 |

The Helper never edits files or talks to sessions. The engine acts on what it returns.

## 5. Contract checks

`shikiru doctor --probe` runs the live checks of `AgentContractSuite` (§1.2) for each configured agent, in a scratch git repo on a private tmux socket. It saves the recorded samples as regression fixtures (07-engineering §4).
