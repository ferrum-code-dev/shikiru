# Codex captures

## Codex 0.156.1 headless captures

`turn_first.jsonl`, `rollout_first.jsonl`, `turn_resume_model_change.jsonl`,
`rollout_resume_model_change.jsonl` and their stderr files are genuine redacted
captures from authenticated `codex exec` and model-changing `codex exec resume`
runs in an isolated Git worktree on 2026-09-25. The two turns created separate
commits in the linked worktree. Event types, item types, nesting, token counts,
context windows and quota percentages are retained. Session/tool IDs, prompts,
message text, command details, paths and account/reset details are redacted.

`rollout_compaction.jsonl` is a genuine protocol excerpt from a low-threshold
resumed turn in the same live suite. It retains the `event_msg` token-count
shape and the top-level `compacted` event with its nested payload, plus the
token counts needed to exercise compaction parsing. Other rollout fields and
identifiers are omitted.

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
