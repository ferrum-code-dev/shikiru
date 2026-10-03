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

## Fresh 2026-10-03 invocations (both failed)

These are later, fresh runs; neither replaces the last full passing invocation above.

| Run | Source / tree | UTC interval; duration | Result | Tools |
|---|---|---|---|---|
| `build/e2e/20261003T090653Z.json` | `12cf19e47b8bc0e016b1b078a2cfcf94b26eacd0`, clean | 09:06:53.403–09:24:07.050; 17 m 13.6 s | Exit 1; E1, E2, E4, E5, E6 passed; E3 failed both attempts | Claude Code 2.1.287, Codex CLI 0.160.0, tmux 3.7c, git 2.55.0, Python 3.14.7 |
| `build/e2e/20261003T100358Z.json` | `0734ef38810b4ec9ef14d54a030b3ab5b19da06d`, clean | 10:03:58.389–10:20:58.371; 16 m 59.98 s (pytest 1020.51 s) | Exit 1; all E1–E6 ended on the Claude weekly quota limit; costs null, total cost unknown | Claude Code 2.1.287, Codex CLI 0.160.0, tmux 3.7c, git 2.55.0, Python 3.14.7 |

| Scenario | 09:06 run: result, duration, reported cost | 10:03 run: result, duration, reported cost |
|---|---|---|
| E1 | passed, 84.8 s, $0.1011 | quota exhaustion after two configured attempts, 11.1 s total, unknown |
| E2 | passed, 174.8 s, $0.1457 | quota exhaustion then watchdog stall, 246.8 s, unknown |
| E3 | failed both attempts, 213.5 s total ($0.1371 + $0.1223) | quota exhaustion then watchdog stall, 246.3 s, unknown |
| E4 | passed, 209.3 s, $0.2019 | quota exhaustion after two configured attempts, 11.2 s total, unknown |
| E5 | passed, 115.6 s, $0.0994 | quota exhaustion then watchdog stall, 251.9 s, unknown |
| E6 | passed, 230.3 s, $0.2477 | quota exhaustion then watchdog stall, 247.1 s, unknown |

The 09:06 E3 artifact records two `AssertionError` attempts, both marked `an agent skipped a rule`, with durations 91.5 s and 121.9 s. During that run, investigation identified a race in the test's plan-child window observer and independent agent variation in how the phase-B decomposition request was interpreted. The observer was corrected in `0734ef3`: it now discovers child windows independently while tracking the parent task tree, instead of depending on the unrelated parent `_wait_done` timing. The original date/scenario diagnostic names collided with the later same-date invocation, so its detailed attempt text was overwritten; the invocation JSON retains each attempt's status, duration and agent-reported cost. The failure is not a passing E3 result.

The 10:03 diagnostics were copied to `build/e2e/diagnostics/20261003T100358Z/` before any later invocation. E1 and E4 each used the configured one internal retry; both attempts immediately showed the provider's weekly-limit message. The artifact labels their `AssertionError` rerun reason as `an agent skipped a rule`, which is a harness misclassification: both attempt diagnostics show quota exhaustion, not an agent-rule violation. E2, E3, E5 and E6 reached the watchdog after quota exhaustion, with no turn process and no new event. The raw provider `turn_failed` text says the weekly limit resets Oct 7 at 08:00 Europe/Warsaw. Separately, the engine recorded `quota_blocked` until 2026-10-03T14:01:00+00:00. Those timestamps differ; the artifacts do not establish why, and do not show that the provider reset time was parsed correctly. Each scenario's `cost` is null, which means unknown, not zero. No later E1–E6 run was made on the quota-limited account.

The 09:06 invocation has non-null reported costs totaling $1.0552 across E1–E6, but the run failed on E3. The 10:03 invocation has no reported cost. Neither invocation satisfies Task 88's requirement for all real-agent E1–E6 to pass together on the resulting revision; the last full passing invocation remains the 2026-10-02 run above.

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
