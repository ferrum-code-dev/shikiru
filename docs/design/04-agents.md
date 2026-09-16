# 04 · Agents

## 1. Contracts (`shikiru.agents.contracts`)

Shikiru must drive **any** interactive agent CLI. Hooks, session ids and transcripts are **optional capabilities**, not requirements.
The engine's turn protocol (03-workflow §4) depends only on **signals**. Each adapter chooses which **signal sources** produce them. Agent-agnostic sources, which need nothing from the agent, are always available as a fallback.

```python
class Capability(StrEnum):
    HOOKS = "hooks"             # lifecycle hooks → precise turn/prompt/tool/failure signals
    SESSION_ID = "session_id"   # session id is known → exact resume
    TRANSCRIPT = "transcript"   # agent-written transcript → tokens, errors, context
    QUOTA = "quota"             # subscription quota windows can be read or probed
    HEADLESS = "headless"       # one-shot schema-constrained calls (Helper)

@dataclass(frozen=True)
class Rung:        agent: str; model: str; effort: str | None
@dataclass(frozen=True)
class LaunchSpec:  rung: Rung; cwd: Path; run_dir: Path; add_dirs: list[Path]
                   resume_session_id: str | None; agent_config: AgentConfig; hook_command: str

type AgentSignal = (SessionStarted | PromptSubmitted | ToolStarted | ToolFinished | TurnEnded | TurnFailed
                    | Compacted | AttentionNeeded | SessionEnded | ReportSubmitted | Busy | Quiescent | PaneDied)

class SignalSource(Protocol):
    """Incrementally produces agent signals for one run; polled by the engine."""
    def poll(self) -> list[AgentSignal]: ...

class InteractiveAgent(Protocol):
    """An agent CLI that runs as an interactive TUI inside a tmux pane."""
    name: str
    capabilities: frozenset[Capability]
    compact_command: str | None
    interrupt_keys: list[str]
    exit_command: str
    safe_keys: list[str]
    def launch_argv(self, spec: LaunchSpec) -> list[str]: ...
    def launch_env(self, spec: LaunchSpec) -> dict[str, str | None]: ...
    def signal_sources(self, run: RunInfo) -> list[SignalSource]: ...   # agent-specific sources only
    def telemetry(self, run: RunInfo) -> Telemetry | None: ...          # None without TRANSCRIPT

class QuotaProbe(Protocol):
    """Reads the current subscription quota windows of an agent account."""
    def probe(self, config: AgentConfig) -> list[QuotaWindow]: ...

class HeadlessAgent(Protocol):
    """An agent CLI that answers a single schema-constrained prompt non-interactively."""
    def ask(self, prompt: str, schema: dict, rung: Rung, cwd: Path, timeout_s: int) -> dict: ...

@dataclass(frozen=True)
class Telemetry:   input_tokens: int; cache_read_tokens: int; cache_write_tokens: int; output_tokens: int
                   model: str | None; context_used_tokens: int | None
@dataclass(frozen=True)
class QuotaWindow: name: str; used_pct: float; resets_at: datetime | None
```

Cost and context percentage are **not** read from agents. `UsageCalculator` derives them from `Telemetry` and the config `models` table (05-data §1): prices and context window per model.

### 1.1 Signal sources

| Source | Kind | Produces | Needs |
|---|---|---|---|
| `ReportFileSource` | agnostic, always on | `ReportSubmitted` when a valid `report.json` appears | nothing (the agent runs `shikiru report`) |
| `PaneSource` | agnostic, always on | `PaneDied` (tmux hook) · `Busy` / `Quiescent` | tmux + `psutil` |
| `HookFileSource(mapping)` | agent-specific | `SessionStarted`, `PromptSubmitted`, `Tool*`, `TurnEnded`, `TurnFailed`, `AttentionNeeded`, `Compacted` | `HOOKS` |
| `TranscriptSource(mapping)` | agent-specific | `TurnFailed` from recorded API errors; activity | `TRANSCRIPT` |

**`Quiescent` definition**, over a window of `quiet_seconds` (default 20):
- the visible screen hash is unchanged, with digits masked so clocks and spinners don't count as change, **and**
- the pane process tree's CPU is below `quiet_cpu_pct` (default 3%).

`Busy` fires on the first change after that. These two signals need no regex and no knowledge of the agent.

### 1.2 Degradation when a capability is missing

| Missing | Engine fallback |
|---|---|
| `HOOKS` | **Turn end** = `ReportSubmitted` followed by `Quiescent`, or `Quiescent` for `no_report_quiet_seconds` (90) without a report → nudge path.<br>**Submission confirmed** = `Busy` within 15 s after the paste.<br>**Failures and dialogs** = `classify_screen` by the Helper once quiescent without a report. |
| `SESSION_ID` | Recovery (crash, restart, rung change) starts a new session with `handoff.md.j2` instead of resuming |
| `TRANSCRIPT` | Tokens and cost shown as `n/a`. Stall detection relies on `PaneSource` only |
| `QUOTA` | No quota bars. A quota stop is recognised by `classify_screen` (with `resets_at` when it is shown on screen) |
| `HEADLESS` | This agent can't be a Helper rung |

`shikiru hook <role>` reads the hook JSON from stdin and appends one compact line (the payload minus `tool_input`/`tool_response`, with `ts` added) to `$SHIKIRU_RUN_DIR/hooks.jsonl` under `flock`. It exits 0 in under 100 ms. With no `SHIKIRU_RUN_DIR` set, it does nothing.

### 1.3 Generic `tui` plugin (configuration-only agents)

Any other interactive agent CLI works through config alone. It runs with no capabilities, using the agent-agnostic fallbacks:

```toml
[agents.gemini]
plugin = "tui"
command = "gemini"
args = ["--yolo"]
model_args = ["--model", "{model}"]
effort_args = []
prompt_prefix = ""
compact_command = "/compress"
exit_command = "/quit"
interrupt_keys = ["Escape"]
safe_keys = ["Enter", "Escape", "Down", "1", "2", "y"]
```

When the agent has hooks, a transcript or a quota API, a dedicated plugin adds those sources through the entry points. No engine change is needed (Open-Closed Principle).

## 2. Claude Code (`plugins/agents/claude`) — verified with 2.1.273

Capabilities: `HOOKS`, `SESSION_ID`, `TRANSCRIPT`, `QUOTA` (subscription auth only), `HEADLESS`.

**Launch argv**:

```
claude --session-id <new uuid> | --resume <id>
       --model <rung.model> --effort <rung.effort>
       --settings '<json: hooks only>'
       --add-dir <run_dir> [--add-dir …]
       <agent_config.args…>            # default: --permission-mode auto
```

- `--settings` only adds hooks, and they merge with user and project hooks.
- Shikiru never touches the user's statusline or other settings.
- Hooks registered as `{"type":"command","command":"<hook_command> <role>"}`:
  - `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`
  - `Stop`, `StopFailure`, `Notification`
  - `PostCompact`, `SessionEnd`

**Hook mapping** (payloads verified):

| Hook | Signal |
|---|---|
| `SessionStart` | `SessionStarted(session_id, transcript_path)` |
| `UserPromptSubmit` | `PromptSubmitted(prompt)` |
| `PreToolUse` / `PostToolUse` | `ToolStarted` / `ToolFinished` (by `tool_use_id`) |
| `Stop` | `TurnEnded(last_assistant_message)` |
| `StopFailure` | `TurnFailed` (kind refined from the transcript) |
| `Notification` | `AttentionNeeded` |
| `PostCompact` | `Compacted` |
| `SessionEnd` | `SessionEnded` |

**Transcript** (`transcript_path`, the JSONL Claude writes itself, verified):
- **Tokens**: `type == "assistant"` entries give `message.model` and `message.usage` (`input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`). They are **deduplicated by `message.id`**, because one message spans several lines.
- **Context used** = the last message's `input + cache_read + cache_creation` tokens.
- **Failures**: entries with `isApiErrorMessage: true` carry `apiErrorStatus` and text, e.g. `"You've hit your session limit · resets 3:10am (Europe/Warsaw)"`.
  - 429 or "limit" text → `quota`, with `resets_at` parsed from `resets <time> (<IANA tz>)` via `zoneinfo`. The next occurrence of that time is used.
  - 5xx / 529 → `unavailable`
  - 401 / 403 → `auth`
  - "prompt is too long" → `context`
  - anything else → `other`

**Quota probe** (`ClaudeQuotaProbe`, verified):

```
claude -p "." --output-format stream-json --verbose --model <cheapest rung model> --safe-mode --tools "" --no-session-persistence
```

- It reads the first `rate_limit_event.rate_limit_info.unifiedWindows` (`five_hour`/`seven_day`: `utilization`, `resetsAt`).
- It runs every `quota.probe_interval` (15 min) while the agent has live runs, and immediately after a `quota` failure.
- Each probe costs one tiny request. It is disabled with `quota.probe_interval = 0` or `auth = "api_key"`.

**Auth**:
- `subscription` → `launch_env` unsets `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN`.
- `api_key` → sets `ANTHROPIC_API_KEY` from `api_key_env`.

**Keys**:
- `compact_command = "/compact"`, `exit_command = "/exit"`
- `interrupt_keys = ["Escape"]`
- `safe_keys = ["Enter", "Escape", "Down", "1", "2"]`

**Headless**:

```
claude -p --output-format json --json-schema <schema> --model <m> --effort <e> --safe-mode --tools "" --no-session-persistence
```

The result is read from `structured_output`, falling back to `result`.

**Workspace trust**: a trust dialog for a new worktree dir is one of the dialogs `doctor --probe` checks for. If one shows up at runtime, it raises `AttentionNeeded`, and the Helper's `classify_screen` answers it using `safe_keys`.

## 3. Codex CLI (`plugins/agents/codex`) — verified with 0.154.0

Capabilities: `HOOKS`, `SESSION_ID`, `TRANSCRIPT`, `QUOTA`, `HEADLESS`.

**Launch argv**:

```
codex [resume <id>]
      -m <rung.model> -c model_reasoning_effort="<effort>"
      --dangerously-bypass-hook-trust
      -c 'hooks.<Event>=[{hooks=[{type="command",command="<hook_command> <role>"}]}]'   # per event
      -c 'projects."<task root>".trust_level="trusted"'
      -C <cwd> --add-dir <run_dir> --add-dir <git common dir of each repo>
      <agent_config.args…>      # default: --approve-for-me
```

Hook events: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `PermissionRequest`, `PostCompact`, `SessionEnd`.

**Hook mapping** (payloads verified):

| Hook | Signal |
|---|---|
| `SessionStart` | `SessionStarted(session_id, transcript_path)`. If `transcript_path` is null, the adapter finds the file `~/.codex/sessions/**/rollout-*-<session_id>.jsonl` |
| `UserPromptSubmit` | `PromptSubmitted` |
| `PreToolUse` / `PostToolUse` | `Tool*` |
| `Stop` | `TurnEnded` |
| `PermissionRequest` | `AttentionNeeded` |
| `PostCompact` | `Compacted` |

**Transcript** (the rollout JSONL Codex writes itself, verified):
- **Tokens**: `event_msg` `token_count` records: `info.total_token_usage` (`input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens`).
- **Context used**: `info.last_token_usage`, with the model's context window taken from the record when present, else from config `models`.
- **Quota (passive)**: `rate_limits.primary` / `secondary` (`used_percent`, `window_minutes`, `resets_at`) → `QuotaWindow`s. `CodexQuotaProbe` returns the latest record across recent rollouts.
- **Failures**: `error` events, mapped the same way as for Claude: usage limit → `quota` (with `resets_at` from the latest `rate_limits`), overloaded or 5xx → `unavailable`, 401 → `auth`, context window exceeded → `context`.

**Auth**:
- `subscription` → unset `OPENAI_API_KEY` and `CODEX_API_KEY`.
- `api_key` → set `CODEX_API_KEY` from `api_key_env`.

**Keys**:
- `compact_command = "/compact"`, `exit_command = "/quit"`
- `interrupt_keys = ["Escape"]`
- `safe_keys = ["Enter", "Escape", "Down", "1", "2", "y"]`
- Default `compact_below_free_pct = 60`.

**Headless**:

```
codex exec --ephemeral --json --output-schema <file> -m <m> -c model_reasoning_effort="<e>" -s read-only --skip-git-repo-check
```

The final `agent_message` is parsed.

## 4. Helper (Joshu 助手)

`Helper` uses the `roles.helper` ladder through `HeadlessAgent.ask`. Every call has a template and a pydantic result model, and the output is always validated:

| Call | Input | Output | Used by |
|---|---|---|---|
| `rebuild_report` | report schema, last assistant message (hook) or the last 120 screen lines, transcript tail if available, git facts | report JSON or `{"unrecoverable": reason}` | 03 §4.8 |
| `classify_failure` | error text (transcript or screen) | `{kind, resets_at?, summary}` | 03 §8.1 |
| `classify_screen` | visible pane text, `safe_keys` | `{state: working/dialog/question/quota/error, keys?, text?, resets_at?}` | 03 §8.5, and every agent without hooks (§1.2) |

The Helper never edits files or talks to sessions. The engine acts on what it returns.

## 5. Contract checks (`shikiru doctor --probe`)

For each configured agent, in a scratch git repo inside a real tmux pane on a private socket, using the production launch path. Only the checks for the agent's declared capabilities run:
1. The session starts. `SessionStarted` arrives with an id (`SESSION_ID`), or `Quiescent` arrives (no hooks).
2. The pointer prompt is injected. The agent runs `shikiru report`. Turn end is detected through the agent's signals.
3. No approval or trust dialog is left open.
4. Telemetry parses with tokens > 0 (`TRANSCRIPT`), and the quota probe returns windows (`QUOTA`).
5. Resume: exit, relaunch with the id, and a second turn recalls the first turn's nonce (`SESSION_ID`).

Recorded hook, transcript and probe samples are saved as regression fixtures (07-engineering §4).
