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

The remaining genuine sample cases, full live contract suite, and `/compact`
after context overflow remain unverified. Context overflow was not attempted.
