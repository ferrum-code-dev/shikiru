# Claude turn stream scenarios

Most `turn_*.jsonl` files are synthetic protocol scenarios. The genuine
OpenRouter evidence is described below: a successful stream-json capture and a
normalized replay excerpt from the report-contract check. Both redact private
content and credentials. OpenRouter's [Claude Code guide](https://openrouter.ai/docs/cookbook/coding-agents/claude-code-integration)
documents its native Anthropic-compatible API setup and Claude model aliases.
The report check therefore counts as a successful Claude Code API-access live
check; a direct Anthropic API key capture is not a separate requirement.

On 2026-09-25, the default sandbox could not resolve the OpenRouter host
(`gaierror`). A host-only probe with network escalation returned HTTP 200. One
short turn using the configured Haiku model then emitted `SessionStarted`,
`Message`, `UsageReported` with non-zero tokens, and `TurnEnded`. This attributes
the earlier retry-only timeout to the sandbox network restriction; no auth,
model, or stream-protocol failure was reproduced after escalation.

`turn_openrouter_live_redacted.jsonl` is a genuine successful stream-json
capture from Claude Code 2.1.281 through the configured OpenRouter environment.
It keeps protocol and usage fields while redacting session and model
identifiers, response text, timing data, and exact cost. It contains no prompt,
credentials, or raw headers. A separate content-free alias test produced a
generation record identifying Amazon Bedrock as the serving provider.
OpenRouter's guide says Claude Code is only guaranteed to work with the
Anthropic first-party provider, so these captures establish the observed
requests only and do not establish that all routes behave the same way.

`turn_openrouter_report_live_redacted.jsonl` is a normalized replay excerpt from
a successful first-turn report contract test through OpenRouter. The live test
observed `Read` and `Bash`, confirmed that Bash invoked `shikiru report`, and
validated the resulting report before `TurnEnded`. The excerpt preserves event
order, tool names, usage counts, and the successful terminal event. Session and
tool IDs, model IDs, message text, paths, command details, tool output, and cost
are redacted. Tool-result envelopes are normalized to the parser's replay
format; this is not a byte-for-byte provider stream capture. OpenRouter key
usage increased by $0.01826925 for the attempt after its aggregate usage settled;
Claude Code's separate cost estimate had an unknown cost basis.

`session_compact_boundary_manual_live_redacted.jsonl` is a field-whitelisted
excerpt from a genuine local Claude Code 2.1.280 session transcript audited on
2026-09-25. It retains only the event type and subtype, CLI version, manual
trigger, and pre/post token counts. It omits session and parent IDs, timestamps,
paths, user and assistant content, tool data, discovered tools, and all other
metadata. Its `compactMetadata` field is transcript schema, not a Shikiru
subprocess `stream-json` capture. It records a real manual compaction in a
Claude session; it does not verify Shikiru `/compact` after context overflow.

The remaining `turn_*.jsonl` fixtures are synthetic protocol scenarios. They
cover quota windows, list-priced usage, an unknown cost basis, rate limiting,
credit exhaustion, and an unrecognised model. The quota-window fixture is not
evidence that subscription-style windows are emitted during API access; no
genuine `rate_limit_event` capture is present. Their names describe stream
behavior, not Shikiru access modes. The genuine OpenRouter capture reports
`contextWindow=200000` with `costBasis=unknown`, and Shikiru leaves
`context_used_pct` unset. OpenRouter's guide says Claude Code assumes a 200k
window unless `[1m]` is selected, so the captured value does not establish a
provider-measured context window. Other live checks and `/compact` after
context overflow remain unverified. Context overflow was not attempted.
