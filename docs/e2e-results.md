# E2E results

The release summary of full `SHIKIRU_E2E=1 uv run poe e2e` invocations (07 section 5). It is copied by hand from git-ignored artifacts under `build/e2e/` and refreshed with each release-readiness document. Agent costs are the agents' own estimates, not billing.

## Latest fresh invocation — 2026-10-04 (passed)

| | |
|---|---|
| Source commit / tree | `d49791c269ceef683c8a3155593d3f5ec9b5825d`, dirty (`source_dirty` true); pending delta contained documentation and the clean-install smoke selector update, with no application-code changes after the commit. The schema-5 artifact records no diff fingerprint. |
| Started / finished (UTC) | 2026-10-04 11:49:36.516 / 12:12:48.020 (23 m 11.5 s; pytest reported 1,391.98 s) |
| Exit status | 0; 7 passed (the scripted fake-agent boundary and real E1–E6), 1,156 deselected |
| Attempts | E1–E5 passed at attempt 0. E6 failed once with a diagnosed agent-rule skip and passed on the one existing allowed retry. |
| Artifact | `build/e2e/20261004T114936Z.json`, schema 5, keyed by invocation start |
| E6 diagnostic | `build/e2e/diagnostics/20261004T114936Z/2026-10-04-e6-attempt-0.txt` |
| Tools | Python 3.14.7, Claude Code 2.1.289, Codex CLI 0.160.0, tmux 3.7c, git 2.55.0 |
| Agent ladders | E2E-only Codex `gpt-6-luna`, low effort for Leader, Implementer and Helper |
| Sandbox | Codex `workspace-write`; projects under `/Users/pavel/.cache/shikiru-e2e`; `approvals_reviewer=auto_review` |
| Cost | Every artifact cost is null (unknown), not zero |

| Scenario | Result | Attempts | Duration | Cost |
|---|---|---:|---:|---|
| E1 triage, implement, verify and merge | passed | 1 | 101.67 s | unknown |
| E2 operator message reaches verify | passed | 1 | 166.38 s | unknown |
| E3 plan, dependent tasks, parallel progress and merge | passed | 1 | 313.81 s | unknown |
| E4 scripted MCP operator resolves a product decision | passed | 1 | 182.17 s | unknown |
| E5 engine restart resumes the interrupted turn | passed | 1 | 98.05 s | unknown |
| E6 takeover holds only the selected task | passed | 2 | 523.92 s total (242.97 + 280.95) | unknown |

The corrected-source run followed a separate 2026-10-04 zero-turn setup failure described below. Commit `d49791c` adds a bounded read-only lookup of the installed Codex CLI's bundled model catalog when the refreshed user cache omits the configured model. The earlier failure was a product setup failure, not an agent-flaky result and not an automatic retry. No user Codex cache, authentication or production defaults were changed.

The first E6 attempt emitted no `engine_fault`; both tasks completed and the agent reported preserving tracker changes, while A2 remained unchecked after conflict resolution. Its assertion was diagnosed as the permitted “agent skipped a rule” case. The original diagnostic is retained, and the one configured rerun passed. E4's AIOperator is a scripted MCP client; this scenario exercises the MCP decision path and is not evidence of paid operator inference. All costs are null, which means unknown rather than zero.

## Preserved corrected-source setup failure — 2026-10-04

| | |
|---|---|
| Source commit / tree | `d751832d653f3c21f7e35db894aedd760fd8ee04`, dirty (`source_dirty` true); the artifact has no dirty-diff fingerprint |
| Started / finished (UTC) | 2026-10-04 11:02:40.243 / 11:02:56.951 (16.71 s) |
| Exit status | 1; all six E1–E6 tests failed during `shikiru start` preflight, before any model turn |
| Attempts | One attempt per scenario; `ShikiruFailure`, attempt 0, no automatic retry |
| Artifact | `build/e2e/20261004T110240Z.json`, schema 5 |
| Diagnostics | `build/e2e/diagnostics/20261004T110239Z/2026-10-04-e{1,2,3,4,5,6}-attempt-0.txt` |
| Tools | Python 3.14.7, Claude Code 2.1.289, Codex CLI 0.160.0, tmux 3.7c, git 2.55.0 |
| Agent ladders | The configured test-only Codex `gpt-6-luna`, low effort for Leader, Implementer and Helper |
| Sandbox | Codex `workspace-write`; projects under `/Users/pavel/.cache/shikiru-e2e`; `approvals_reviewer=auto_review` |
| Cost | Every artifact cost is null (unknown); no agent turns began |

Codex's `context_window` doctor check refused the configured model because the refreshed `$CODEX_HOME/models_cache.json` did not contain `gpt-6-luna`. A read-only `codex debug models --bundled` check against the same Codex CLI binary reports that exact model with a 272,000-token context window; the refreshed home cache was version 0.150.0 and omitted it. This confirms a local model-catalog mismatch, not an agent behavior failure. This invocation's E1–E6 gate failed and is not counted as evidence for the corrected source. The preserved diagnostics and artifact are the source of the scenario results; null cost is unknown, not zero.

## Previous passing invocation — 2026-10-03

| | |
|---|---|
| Source commit | `01ab54e8748a56d8ee6310de5da3c18d214236e2`, tree clean (`source_dirty` false) |
| Started / finished (UTC) | 2026-10-03 19:32:09.251 / 19:53:32.765 (21 m 23.51 s; pytest reported 1284.00 s) |
| Exit status | 0; 7 passed (fake-agent boundary and real E1–E6), 1,097 deselected |
| Attempts | E1–E6 all passed at attempt 0 in this same invocation; every reported cost was null, so cost is unknown |
| Artifact | `build/e2e/20261003T193209Z.json`, schema 5, keyed by invocation start |
| Evidence | [2026-10-03 Phase 8 release record](release-readiness-2026-10-03-phase8.md) |
| Tools | Python 3.14.7, Claude Code 2.1.287, Codex CLI 0.160.0, tmux 3.7c, git 2.55.0 |
| Agent ladders | E2E-only Codex `gpt-6-luna`, low effort for Leader, Implementer and Helper |
| Sandbox | Codex `workspace-write`; projects under `/Users/pavel/.cache/shikiru-e2e`, outside `$TMPDIR`; `approvals_reviewer = auto_review` |

| Scenario | Result | Attempts | Duration | Cost (USD) |
|---|---|---:|---:|---|
| E1 triage, implement, verify and merge | passed | 1 | 83.93 s | unknown |
| E2 operator message reaches verify | passed | 1 | 182.16 s | unknown |
| E3 plan, dependent tasks, two concurrent windows, conflict and merge | passed | 1 | 346.33 s | unknown |
| E4 MCP operator resolves a product decision | passed | 1 | 189.66 s | unknown |
| E5 engine restart resumes the interrupted turn | passed | 1 | 167.27 s | unknown |
| E6 takeover holds only the selected task | passed | 1 | 308.64 s | unknown |

All scenario costs are null in the artifact; unknown is not zero. The implementation revision after this run only adds a test-only Textual observation boundary; no application code or real-agent E1–E6 scenario changed. See the release record for the exact descendant and smoke evidence.

## Previous full passing invocation (2026-10-02)

The prior full passing invocation is retained below as historical evidence. Its E3 rerun does not describe the newer 2026-10-03 run above.

| | |
|---|---|
| Source commit | `159db49a66ac7aef1fe6d12766027a3adc64365c`, tree clean (`source_dirty` false) |
| Started / finished (UTC) | 2026-10-02 05:38:44 / 06:03:12 (24 m 28 s; pytest reported 1468.61 s) |
| Exit status | 0; 7 passed (the fake-agent scenario and real E1-E6), 957 deselected |
| Attempts | E1, E2, E4, E5, E6 passed at attempt 0; E3 failed attempt 0 (the leader triaged the request as one implement task rather than a plan) and passed on its one rerun; no engine fault |
| Artifact | `build/e2e/20261002T053844Z.json`, schema 4 |
| Evidence | [release-readiness-2026-10-02-phase7d.md](release-readiness-2026-10-02-phase7d.md) |
| Tools | Claude Code 2.1.284, Codex CLI 0.159.2, tmux 3.7c, git 2.55.0, Python 3.14.7 |

## Earlier fresh 2026-10-03 failures

These failed attempts preceded the full passing invocation above and remain preserved as history.

| Run | Source / tree | UTC interval; duration | Result | Tools |
|---|---|---|---|---|
| `build/e2e/20261003T090653Z.json` | `12cf19e47b8bc0e016b1b078a2cfcf94b26eacd0`, clean | 09:06:53.403–09:24:07.050; 17 m 13.6 s | Exit 1; E1, E2, E4, E5, E6 passed; E3 failed both attempts | Claude Code 2.1.287, Codex CLI 0.160.0, tmux 3.7c, git 2.55.0, Python 3.14.7 |
| `build/e2e/20261003T100358Z.json` | `0734ef38810b4ec9ef14d54a030b3ab5b19da06d`, clean | 10:03:58.389–10:20:58.371; 16 m 59.98 s (pytest 1020.51 s) | Exit 1; all E1–E6 ended on the Claude weekly quota limit; costs null, total cost unknown | Claude Code 2.1.287, Codex CLI 0.160.0, tmux 3.7c, git 2.55.0, Python 3.14.7 |
| `build/e2e/20261003T183808Z.json` | `129ce0b9e8a7d888f8152a6b3ec56e398bf017a8`, clean | 18:38:08.813–18:38:22.145; 13.33 s | Exit 1; all six failed before agent turns because the CLI argument tuple duplicated `--set`; costs null | Claude Code 2.1.287, Codex CLI 0.160.0, tmux 3.7c, git 2.55.0, Python 3.14.7 |

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

The 09:06 invocation has non-null reported costs totaling $1.0552 across E1–E6, but the run failed on E3. The 10:03 invocation has no reported cost. The 18:38 invocation failed before model turns. None is a passing E1–E6 result; the fresh 19:32 invocation above is the latest full pass.

## Previous passing invocation — 2026-10-02 scenario breakdown

| Scenario | Result | Attempts | Duration | Cost (USD) |
|---|---|---|---|---|
| E1 triage, implement, verify, merge | passed | 1 | 104.1 s | 0.1009 |
| E2 operator message reaches verify | passed | 1 | 174.5 s | 0.1479 |
| E3 plan, parallel dependent tasks, conflict | passed | 2 (first failed, see above: 156.9 s then 406.2 s) | 563.1 s | 0.6343 |
| E4 AI operator resolves a decision over MCP | passed | 1 | 185.5 s | 0.1816 |
| E5 engine SIGKILL, restart resumes the turn | passed | 1 | 108.9 s | 0.0980 |
| E6 take over holds only that task | passed | 1 | 326.6 s | 0.2818 |
| Sum | | 7 attempts | 1462.8 s | 1.4444 |

The fake-agent scenario is not an entry of the 2026-10-02 artifact (no agent, no cost). An earlier full invocation at `d5624af` (2026-10-01 21:04:30 to 21:26:59 UTC, 22 m 29 s, `build/e2e/20261001T210430Z.json`) also passed with the same single E3 rerun (E1 97.1 s, E2 180.2 s, E3 512.1 s, E4 192.2 s, E5 112.6 s, E6 249.1 s, $1.4038). The 2026-10-02 run above is the previous full pass; two later commits (`7ac1139`, `b1ee928`: the detached engine's output file and the engine's lock retry) were covered by the suite, the soak, the smoke and live Claude/Codex turn checks before the fresh 2026-10-03 full pass.

E3's first attempt: 2 of 2 full invocations failed it in the same way in Phase 7d (the 7c invocation passed it at the first attempt). A targeted A/B run of E3 alone (tests/e2e, `-k e3`) showed it is not caused by the Phase 7d prompt sentence (Task 74j): with that sentence the first attempt passed 2 of 2, and with the 7c version of the template (sentence removed) it failed 2 of 2. It is the leader model's triage choice (plan or implement for "Implement all tasks in phase B", Claude haiku at low effort), a variance the one allowed rerun absorbs; the engine behaves per the verdict it is given.
