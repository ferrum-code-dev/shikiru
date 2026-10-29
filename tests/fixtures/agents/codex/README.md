# Codex captures

## Codex 0.156.1 headless captures

`turn_first.jsonl`, `rollout_first.jsonl`, `turn_resume_model_change.jsonl`,
`rollout_resume_model_change.jsonl` and their stderr files are genuine redacted
captures from authenticated `codex exec` and model-changing `codex exec resume`
runs in an isolated Git worktree on 2026-09-25. The two turns created separate
commits in the linked worktree. Event types, item types, nesting, token counts,
context windows and quota percentages are retained. Session/tool IDs, prompts,
message text, command details, paths and account/reset details are redacted.

## Mid-turn compaction capture

`turn_midturn_compaction_live_redacted.jsonl` and
`rollout_midturn_compaction_live_redacted.jsonl` are a genuine redacted
`codex exec --json` turn from codex-cli 0.157.1 on `gpt-5.6-luna` with low
reasoning effort and `-c model_auto_compact_token_limit=40000` (subscription
access) on 2026-09-26, in a scratch repository of random dictionary words. One
turn read eight files with one `cat` per command; Codex compacted twice inside
the running turn, each time before the request that would exceed the limit, and
the turn completed with the task's nonce. The stream keeps event and item types,
item IDs, the agent messages and the final usage. The thread ID, command paths
and command output are redacted. The rollout keeps only its `token_count`
entries (token usage and the 258,400-token model context window, with
`rate_limits` set to null) and the `compacted` entries (window number only);
the compaction history, session metadata and account identifiers are removed.
The `token_count` right after each `compacted` entry reports 0 input tokens,
so `post_tokens` comes from the first non-zero one after it.

## Model-change rollout capture

`rollout_model_change_turn_context_live_redacted.jsonl` is the `turn_context` and
`token_count` entries of a genuine rollout from codex-cli 0.159.2 on 2026-09-30:
a first turn on `gpt-5.6-luna`, then `codex exec resume` on `gpt-5.6-terra`
(subscription access, low reasoning effort, scratch repository). It is the only
fixture carrying `turn_context` entries, whose `payload.model` the plugin pairs
with the next `token_count` to decide which model a window was observed for.
Timestamps are zeroed; in each `turn_context` only the model, effort and summary
are kept (turn ID and working directory are redacted; sandbox, permission and
workspace details are removed), and in each `token_count` the quota windows keep
only their percentages and window lengths.

## OpenRouter custom-provider capture

`turn_openrouter_live_redacted.jsonl`, `rollout_openrouter_live_redacted.jsonl`
and its stderr file are a genuine Codex CLI 0.156.1 API-key turn through
OpenRouter's Responses endpoint on 2026-09-25. It used a disposable user-level
Codex configuration, an isolated `CODEX_HOME`, an empty scratch Git repository,
and the content-free prompt `Return only OK. Do not use tools.` No Shikiru or
project files were available to the process. The model was
`openai/gpt-6-luna` with low reasoning effort. Session and item IDs and unrelated
rollout metadata are redacted; token counts, model context window, message, and
null primary and secondary quota windows are retained. This is OpenRouter API
key evidence, not a direct OpenAI API-key sample.

No genuine headless usage-limit failure or direct OpenAI API-key turn was
available. Error-classification cases and null-rate-limit handling remain
synthetic offline coverage for those cases.

`turn_tool_outcomes.jsonl` and `turn_failed_command_incomplete.jsonl` are
synthetic protocol replays based on the observed Codex command item fields. They
cover `status: failed` with exit code 2, a `completed` item with a non-zero exit
code, older completed items without an exit code, a successful command, and a
stream that exits without a terminal turn event. They are not captured T-35
transcripts.

## User-supplied model-capacity output

`turn_model_capacity.jsonl` contains the two exact structured records the user
supplied after reporting three Codex capacity failures. This is user-provided
output, not a project-collected or independently verified live capture.
