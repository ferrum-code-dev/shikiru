# E2E results

The release summary of the last full passing `SHIKIRU_E2E=1 uv run poe e2e` invocation (07 section 5). It is copied by hand from the git-ignored artifact `build/e2e/<start>.json` and refreshed with each release-readiness document. Agent costs are the agents' own estimates, not billing.

## Last full passing invocation

| | |
|---|---|
| Source commit | `159db49a66ac7aef1fe6d12766027a3adc64365c`, tree clean (`source_dirty` false) |
| Started / finished (UTC) | 2026-10-02 05:38:44 / 06:03:12 (24 m 28 s; pytest reported 1468.61 s) |
| Exit status | 0; 7 passed (the fake-agent scenario and real E1-E6), 957 deselected |
| Attempts | E1, E2, E4, E5, E6 passed at attempt 0; **E3 failed attempt 0** ("an agent skipped a rule": the leader triaged the request as one implement task instead of a plan, so two new task windows were never open at once; no engine fault, clean checkout; diagnostics `build/e2e/diagnostics/2026-10-02-e3-attempt-0.txt`) and passed on its one rerun |
| Artifact | `build/e2e/20261002T053844Z.json`, schema 4 (keyed by invocation start, every attempt recorded) |
| Evidence | [release-readiness-2026-10-02-phase7d.md](release-readiness-2026-10-02-phase7d.md) |
| Tools | Claude Code 2.1.284, Codex CLI 0.159.2, tmux 3.7c, git 2.55.0, Python 3.14.7 |
| Sandbox | Codex `sandbox_mode` workspace-write; projects under `/Users/pavel/.cache/shikiru-e2e` (outside `$TMPDIR`, which is `/var/folders/99/dgcz2lpx7ts5bpvl7zvt4fnw0000gn/T/`); host Codex config `approvals_reviewer = auto_review`; Claude in `bypassPermissions` |

## Scenarios

| Scenario | Result | Attempts | Duration | Cost (USD) |
|---|---|---|---|---|
| E1 triage, implement, verify, merge | passed | 1 | 104.1 s | 0.1009 |
| E2 operator message reaches verify | passed | 1 | 174.5 s | 0.1479 |
| E3 plan, parallel dependent tasks, conflict | passed | 2 (first failed, see above: 156.9 s then 406.2 s) | 563.1 s | 0.6343 |
| E4 AI operator resolves a decision over MCP | passed | 1 | 185.5 s | 0.1816 |
| E5 engine SIGKILL, restart resumes the turn | passed | 1 | 108.9 s | 0.0980 |
| E6 take over holds only that task | passed | 1 | 326.6 s | 0.2818 |
| Sum | | 7 attempts | 1462.8 s | 1.4444 |

The fake-agent scenario is not an entry of the artifact (no agent, no cost). An earlier full invocation at `d5624af` (2026-10-01 21:04:30 to 21:26:59 UTC, 22 m 29 s, `build/e2e/20261001T210430Z.json`) also passed with the same single E3 rerun (E1 97.1 s, E2 180.2 s, E3 512.1 s, E4 192.2 s, E5 112.6 s, E6 249.1 s, $1.4038). The invocation above is the last full one; two later commits (`7ac1139`, `b1ee928`: the detached engine's output file and the engine's lock retry) were covered by the suite, the soak, the smoke and the live Claude and Codex turn checks, not by a new full invocation.

E3's first attempt: 2 of 2 full invocations failed it in the same way in Phase 7d (the 7c invocation passed it at the first attempt). A targeted A/B run of E3 alone (tests/e2e, `-k e3`) showed it is not caused by the Phase 7d prompt sentence (Task 74j): with that sentence the first attempt passed 2 of 2, and with the 7c version of the template (sentence removed) it failed 2 of 2. It is the leader model's triage choice (plan or implement for "Implement all tasks in phase B", Claude haiku at low effort), a variance the one allowed rerun absorbs; the engine behaves per the verdict it is given.
