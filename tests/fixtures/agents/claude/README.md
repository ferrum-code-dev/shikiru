# Claude turn stream scenarios

The `turn_*.jsonl` files are synthetic protocol scenarios except
`turn_openrouter_live_redacted.jsonl`, a genuine successful stream-json capture
from Claude Code 2.1.281 through the configured OpenRouter environment. That
capture keeps protocol and usage fields, while redacting session and model
identifiers, response text, timing data, and exact cost. It contains no prompt,
credentials, or raw headers.

On 2026-09-25, the default sandbox could not resolve the OpenRouter host
(`gaierror`). A host-only probe with network escalation returned HTTP 200. One
short turn using the configured Haiku model then emitted `SessionStarted`,
`Message`, `UsageReported` with non-zero tokens, and `TurnEnded`. This attributes
the earlier retry-only timeout to the sandbox network restriction; no auth,
model, or stream-protocol failure was reproduced after escalation.

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

The other fixtures are synthetic protocol scenarios. They cover provider-reported
quota, list-priced usage, OpenRouter's unpriced basis, rate limiting, credit
exhaustion, and an unrecognised model. Their names describe stream behavior, not
Shikiru access modes. Other live checks and `/compact` after context overflow
remain unverified. Genuine subscription and direct Anthropic API captures also
remain unavailable. Context overflow was not attempted.
