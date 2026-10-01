# E2E results

The release summary of the last full passing `SHIKIRU_E2E=1 uv run poe e2e` invocation (07 section 5). It is copied by hand from the git-ignored artifact `build/e2e/<start>.json` and refreshed with each release-readiness document. Agent costs are the agents' own estimates, not billing.

## Last full passing invocation

| | |
|---|---|
| Source commit | `e78c057dc1322ad4f2570b8ef7bff0b55b065fc7`, tree clean (`source_dirty` false) |
| Started / finished (UTC) | 2026-10-01 07:59:03 / 08:20:06 (21 m 03 s; pytest reported 1263.96 s) |
| Exit status | 0; 7 passed (the fake-agent scenario and real E1-E6), 943 deselected |
| Attempts | every real scenario passed at attempt 0; no rerun, no diagnostics file |
| Artifact | `build/e2e/20261001T075903Z.json`, schema 4 (keyed by invocation start, every attempt recorded) |
| Evidence | [release-readiness-2026-10-01-phase7c.md](release-readiness-2026-10-01-phase7c.md) |
| Tools | Claude Code 2.1.284, Codex CLI 0.159.2, tmux 3.7c, git 2.55.0, Python 3.14.7 |
| Sandbox | Codex `sandbox_mode` workspace-write; projects under `/Users/pavel/.cache/shikiru-e2e` (outside `$TMPDIR`, which is `/var/folders/99/dgcz2lpx7ts5bpvl7zvt4fnw0000gn/T/`); host Codex config `approvals_reviewer = auto_review`; Claude in `bypassPermissions` |

## Scenarios

| Scenario | Result | Attempts | Duration | Cost (USD) |
|---|---|---|---|---|
| E1 triage, implement, verify, merge | passed | 1 | 117.9 s | 0.0972 |
| E2 operator message reaches verify | passed | 1 | 189.7 s | 0.1570 |
| E3 plan, parallel dependent tasks, conflict | passed | 1 | 377.5 s | 0.4637 |
| E4 AI operator resolves a decision over MCP | passed | 1 | 175.9 s | 0.1583 |
| E5 engine SIGKILL, restart resumes the turn | passed | 1 | 112.9 s | 0.0999 |
| E6 take over holds only that task | passed | 1 | 284.1 s | 0.2483 |
| Sum | | | 1258.1 s | 1.2244 |

The fake-agent scenario is not an entry of the artifact (no agent, no cost); the six real scenarios account for 1258.1 s of the 1263.96 s the seven tests took.
