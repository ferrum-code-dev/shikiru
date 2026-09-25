# Claude turn stream scenarios

The `turn_*.jsonl` and matching stderr files in this directory are synthetic
protocol scenarios used for offline parser and process-sequence checks. They
are not captured Claude Code sessions. Claude's login check reported `not
authenticated` while Task 33 was implemented, and the repository contained no
genuine headless stream-json captures to reuse. The recorded-sample and live
verification acceptance criteria remain open until real captures and an
authenticated Claude Code session are available.

On 2026-09-25, an auth-configured live attempt loaded the OpenRouter environment
from the local `.claude/settings.local.json.bak` through a temporary launcher.
Claude Code 2.1.281 emitted seven `system/api_retry` events without an HTTP
status or a terminal result; the live report check timed out after 60 seconds.
No genuine capture was added. Live contract and compaction checks remain
unverified, and context-overflow compaction was not attempted because a basic
turn did not complete.
