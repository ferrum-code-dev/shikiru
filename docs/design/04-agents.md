# 04 · Agents

## 1. Contracts (`shikiru.agents.contracts`)

Interface segregation: an interactive session (Leader, Implementer) and a one-shot call (Helper) are separate protocols. A plugin class may implement either one or both.

```python
@dataclass(frozen=True)
class Rung:            agent: str; model: str; effort: str | None
@dataclass(frozen=True)
class LaunchSpec:      rung: Rung; cwd: Path; run_dir: Path; add_dirs: list[Path]
                       resume_session_id: str | None; agent_config: AgentConfig
                       hook_command: str        # absolute "…/shikiru hook"
                       statusline_command: str  # absolute "…/shikiru statusline"

type AgentSignal = (SessionStarted | PromptSubmitted | ToolStarted | ToolFinished | TurnEnded
                    | TurnFailed | Compacted | AttentionNeeded | SessionEnded)

class InteractiveAgent(Protocol):
    """An agent CLI that runs as an interactive TUI inside a tmux pane."""
    name: str
    compact_command: str | None           # e.g. "/compact"
    interrupt_keys: list[str]             # e.g. ["Escape"]
    exit_command: str                     # e.g. "/exit"
    safe_keys: list[str]                  # keys the Helper may choose for dialogs
    def launch_argv(self, spec: LaunchSpec) -> list[str]: ...
    def launch_env(self, spec: LaunchSpec) -> dict[str, str | None]: ...   # None = unset
    def parse_hook(self, payload: dict) -> AgentSignal | None: ...
    def telemetry(self, run: RunInfo) -> Telemetry: ...                    # cheap, incremental
    def classify_failure(self, run: RunInfo, text: str) -> TurnFailed | None: ...

class HeadlessAgent(Protocol):
    """An agent CLI that can answer a single schema-constrained prompt non-interactively."""
    name: str
    def ask(self, prompt: str, schema: dict, rung: Rung, cwd: Path, timeout_s: int) -> dict: ...

@dataclass(frozen=True)
class Telemetry:       input_tokens: int; cached_tokens: int; output_tokens: int
                       cost_usd: float | None; context_free_pct: float | None
                       quotas: list[QuotaWindow]; model: str | None
@dataclass(frozen=True)
class QuotaWindow:     name: str; used_pct: float; resets_at: datetime | None
```

Shared, agent-independent pieces:
- **`shikiru hook <role>`**: reads the hook JSON from stdin and appends one compact JSON line (the payload minus `tool_input`/`tool_response`, with `ts` added) to `$SHIKIRU_RUN_DIR/hooks.jsonl` under `flock`. It exits 0 in under 100 ms and never blocks the agent. With no `SHIKIRU_RUN_DIR` set, it does nothing.
- **`SignalInbox`** (engine): tails `hooks.jsonl` from the stored byte offset every 250 ms and passes each line through `adapter.parse_hook`.
- **`shikiru statusline`**: stores the stdin JSON in `$SHIKIRU_RUN_DIR/statusline.json`, then runs the user's own statusline command with the same stdin and prints its output. Nothing visible changes for the user.

Adding an agent means writing a class and registering an entry point. No engine code changes (Open-Closed Principle).

## 2. Claude Code (`plugins/agents/claude`) — verified with 2.1.273

**Launch argv**:

```
claude --session-id <new uuid> | --resume <id>
       --model <rung.model> --effort <rung.effort>
       --settings '<json: hooks + statusLine>'
       --add-dir <run_dir> [--add-dir …]
       <agent_config.args…>            # default: --permission-mode auto
```

- `--settings` merges with user and project settings, so project hooks such as graphify still run.
- Hooks registered, each as `{"type":"command","command":"<hook_command> <role>"}`:
  - `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`
  - `Stop`, `StopFailure`, `Notification`
  - `PreCompact`, `PostCompact`, `SessionEnd`
- `statusLine`: `{"type":"command","command":"<statusline_command>"}`. The original command is read from the effective settings and passed in `SHIKIRU_USER_STATUSLINE`.

**Hook mapping** (payloads verified):

| Hook | Signal |
|---|---|
| `SessionStart` | `SessionStarted(session_id, transcript_path)` |
| `UserPromptSubmit` | `PromptSubmitted(prompt)` |
| `PreToolUse` / `PostToolUse` | `ToolStarted(tool_use_id)` / `ToolFinished(tool_use_id)` |
| `Stop` | `TurnEnded(last_assistant_message)` |
| `StopFailure` | `TurnFailed(kind from error)`. Error mapping: rate limit → `quota`, overloaded or server error → `unavailable`, authentication or billing → `auth`, prompt too long → `context`, otherwise `other` |
| `Notification` | `AttentionNeeded(message)` |
| `PostCompact` | `Compacted` |
| `SessionEnd` | `SessionEnded` |

**Telemetry**, all from `statusline.json`:
- `cost.total_cost_usd`
- `context_window.remaining_percentage`
- `rate_limits.five_hour` / `rate_limits.seven_day` (`used_percentage`, `resets_at`) → `QuotaWindow`s
- `model.id`

Token totals come from summing `message.usage` in the transcript JSONL (tailed incrementally).
`resets_at` for `quota` failures is the matching rate-limit window's `resets_at`.

**Auth**:
- `auth = "subscription"` → `launch_env` unsets `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN`, so a stray key can't silently bill the API.
- `auth = "api_key"` → sets `ANTHROPIC_API_KEY` from the `api_key_env` variable.

**Keys and commands**:
- `compact_command = "/compact"`, `exit_command = "/exit"`
- `interrupt_keys = ["Escape"]`
- `safe_keys = ["Enter", "Escape", "Down", "1", "2"]`
- Compaction default: `compact_below_free_pct = 0`, i.e. rely on Claude's own auto-compact.

**Headless (Helper)**:

```
claude -p --output-format json --json-schema <schema> --model <m> --effort <e> --safe-mode --tools "" --no-session-persistence
```

The Helper reads `structured_output` (falling back to `result`).

**Workspace trust**: a trust dialog for a new worktree dir is one of the dialogs `doctor --probe` checks for. If one shows up at runtime, it raises `AttentionNeeded`, and the Helper's `classify_screen` answers it using `safe_keys`.

## 3. Codex CLI (`plugins/agents/codex`) — verified with 0.154.0

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

- Hook events: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `PermissionRequest`, `PostCompact`, `SessionEnd`.
- Session-flag hooks add to project and user hooks.
- `--dangerously-bypass-hook-trust` applies only to hooks declared for this invocation and project hooks. Shikiru's own hook commands are fixed.
- `--add-dir <git common dir>` lets commits work from a worktree.

**Hook mapping** (payloads verified):

| Hook | Signal |
|---|---|
| `SessionStart` | `SessionStarted(session_id, transcript_path)`. `transcript_path` is the rollout file. If it is null, the adapter finds the file `~/.codex/sessions/**/rollout-*-<session_id>.jsonl` |
| `UserPromptSubmit` | `PromptSubmitted(prompt)` |
| `Stop` | `TurnEnded(last_assistant_message)` |
| `PermissionRequest` | `AttentionNeeded` |
| `PostCompact` | `Compacted` |

Codex has no failure hook. When a `Stop` or a stall arrives, `classify_failure` reads the rollout tail:
- an `error` event whose code or message says usage limit → `quota`
- overloaded or server error → `unavailable`
- 401 / unauthorized → `auth`
- context window exceeded → `context`
- anything else → Helper classification

**Telemetry** from the rollout `event_msg` `token_count` records:
- `info.total_token_usage` gives tokens.
- `info.model_context_window` together with `last_token_usage` gives `context_free_pct`.
- `rate_limits.primary` / `secondary` (`used_percent`, `window_minutes`, `resets_at`) → `QuotaWindow`s.
- `cost_usd` = tokens × `pricing[model]` when that is configured, else `None`.

**Auth**:
- `subscription` → unset `OPENAI_API_KEY` and `CODEX_API_KEY`.
- `api_key` → set `CODEX_API_KEY` from `api_key_env`.

**Keys and commands**:
- `compact_command = "/compact"`, `exit_command = "/quit"`
- `interrupt_keys = ["Escape"]`
- `safe_keys = ["Enter", "Escape", "Down", "1", "2", "y"]`
- Default `compact_below_free_pct = 60`, as in the manual practice.

**Headless (Helper)**:

```
codex exec --ephemeral --json --output-schema <file> -m <m> -c model_reasoning_effort="<e>" -s read-only --skip-git-repo-check
```

The Helper parses the final `agent_message`.

## 4. Helper (Joshu 助手)

`Helper` uses the `roles.helper` ladder through `HeadlessAgent.ask`. It is a small fixed set of calls. Each call has a template in `prompts/templates/helper/` and a pydantic result model:

| Call | Input | Output | Used by |
|---|---|---|---|
| `rebuild_report` | report schema, `last_assistant_message`, transcript tail (≤ 30 k chars), git facts | report JSON or `{"unrecoverable": reason}` | turn protocol §4.8 |
| `classify_failure` | error text or transcript tail | `{kind, resets_at?, summary}` | §8.1 unknown failures |
| `classify_screen` | visible pane text, `safe_keys` | `{state: working/dialog/question/error, keys?, text?}` | §8.5 stall |

The Helper output is always validated. An invalid output counts as "unrecoverable". The Helper never edits files or talks to sessions: the engine acts on what it returns.

## 5. Contract checks (`shikiru doctor --probe`)

For each configured agent, the probe runs in a scratch git repo inside a real tmux pane on a private socket, using the production launch path:
1. The session starts and `SessionStarted` arrives with a session id.
2. The pointer prompt is injected. The agent runs `shikiru report` with a trivial schema. `TurnEnded` arrives and `report.json` is valid.
3. No approval or trust dialog appeared: no `AttentionNeeded`, and the screen isn't a dialog.
4. Telemetry parses (tokens > 0).
5. Resume: exit, relaunch with `resume_session_id`, and a second turn sees the first turn's nonce.

The recorded hook and transcript samples are saved to `.shikiru/logs/probe/` and serve as regression fixtures (07-engineering §4).
