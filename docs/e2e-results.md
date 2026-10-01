# E2E results

The release summary of the last full passing `SHIKIRU_E2E=1 uv run poe e2e` invocation (07 section 5). It is copied by hand from the git-ignored artifact `build/e2e/<start>.json` and refreshed with each release-readiness document; Task 74h replaces it with an invocation that records every attempt and per-scenario durations. Agent costs are the agents' own estimates, not billing.

## Last full passing invocation

| | |
|---|---|
| Source commit | `6116446fe768caf044e4ffe3d806517a51f8d869`, tree clean |
| Started / finished (UTC) | 2026-09-30 22:33:26 / 23:04:37 (31 m 11 s) |
| Exit status | 0; 7 passed (the fake-agent scenario and real E1-E6) |
| Artifact | `build/e2e/2026-09-30.json`, schema 2 (keyed by date, final attempt only; schema 4 is keyed by start time) |
| Evidence | [release-readiness-2026-10-01-phase7b.md](release-readiness-2026-10-01-phase7b.md) |
| Tools | Claude Code 2.1.284, Codex CLI 0.159.2, tmux 3.7c, git 2.55.0, Python 3.14.7 |

## Scenarios

| Scenario | Result | Attempts | Duration | Cost (USD) |
|---|---|---|---|---|
| E1 triage, implement, verify, merge | passed | 1 | not recorded | 0.1209 |
| E2 operator message reaches verify | passed | 1 | not recorded | 0.1577 |
| E3 plan, parallel dependent tasks, conflict | passed | 2 (attempt 0 failed, rerun passed) | not recorded; about 7 min from plan to last merge in the passing attempt | 0.9313 (final attempt; may omit attempt 0) |
| E4 AI operator resolves a decision over MCP | passed | 1 | not recorded | 0.1840 |
| E5 engine SIGKILL, restart resumes the turn | passed | 1 | not recorded | 0.1091 |
| E6 take over holds only that task | passed | 1 | not recorded | 0.3119 |

The attempt count of E3 comes from the evidence document, not from the artifact. E3 attempt 0 failed with a Codex implementer unable to create the worktree index lock; its diagnostics were `build/e2e/diagnostics/2026-09-30-e3-attempt-0.txt` and the cause was not established.
