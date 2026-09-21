# Codex 0.155.0 captures

These samples were reduced from Codex CLI 0.155.0 runs made by the authenticated
live contract suite on 2026-09-20. The original local rollout filenames and
directories are intentionally omitted from this provenance.

The hook records preserve the fields and nesting emitted by those runs. User
paths, session identifiers, turn identifiers, tool identifiers, nonce text and
rollout paths are replaced with non-identifying values. Relative transcript
filenames keep the samples runnable when copied into a role directory. Token
counts, context window, rate-limit fields, event names and the `task_complete`
message shape are unchanged. The token samples contain the zero-valued
`cache_write_input_tokens` field emitted by 0.155.0; no non-zero cache-write
values are invented.

The error records use the JSON error event shape emitted by the same 0.155.0
CLI. The authentication wording and status came from an isolated invalid-key
run; the remaining classification samples retain the same event envelope and
status fields while removing private request details.
