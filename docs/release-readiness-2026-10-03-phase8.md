# Phase 8 workspace release readiness — partial

Date: 2026-10-03. **Task 88 is incomplete and remains unchecked.** Tasks 75–87 are delivered, but the required fresh real-agent E1–E6 run did not pass: Claude reported its weekly limit in every scenario. This record preserves the passing evidence and that blocking result; it does not make a release claim or close the older independent review gates.

## Delivery and design agreement

The workspace code delivered by Tasks 75–86 is represented by application source `eed895f`; Task 87 reconciled the operator docs and recorded the walkthroughs. The tested code revision for Task 88's normal/dumb gates and the later E2E attempt was `0734ef38810b4ec9ef14d54a030b3ab5b19da06d`. Its only product-adjacent change was test support: commit `0734ef3` makes the E3 plan-child observer discover windows independently of the parent completion waiter. The installed smoke harness change is commit `d09a6d7`; it adds an installed-package Textual boundary run and does not change Shikiru runtime behavior.

The Task 87 [walkthrough record](workspace-walkthroughs-2026-10-03.md) separates connected fake-agent workflows from seeded UI boundaries. Its five connected Textual/SQLite/Git/tmux fake-agent flows passed in 22.53 s after review corrections; the broader seeded UI set passed 21 tests, and the plan/tmux/CLI boundary set passed 5 tests in 1.69 s. The connected flows demonstrate title search to a verdict, contextual message application, escalation updates from a second operator, one-task takeover while another task progresses, durable pending-command recovery, and retained exports after task-window cleanup. Other walkthrough paths intentionally use seeded boundaries; they are not claimed as extra engine executions.

The observed `/` search → results → task detail → Escape route, contextual action, status words, and compact controls agree with [the workspace specification](tui-workspace.md) and the documented README paths. Task 88 introduced no production UI or workflow behavior. This is an implementation-to-design check from tests and docs, not an independent reviewer verdict.

## Full code gates

The full gate includes formatting, lint, comments/literals rules, imports, type checks, architecture boundaries, and the coverage gate. Four pytest workers were used.

| Gate | Source/tree | Result |
|---|---|---|
| `PYTEST_XDIST_AUTO_NUM_WORKERS=4 uv run poe check` | `0734ef3`, clean | 1,082 passed, 15 skipped; 90.70% coverage; pytest 268.17 s; all static and boundary checks passed |
| `TERM=dumb PYTEST_XDIST_AUTO_NUM_WORKERS=4 uv run poe check` | `0734ef3`, clean | 1,082 passed, 15 skipped; 90.72% coverage; pytest 266.05 s; all static and boundary checks passed |
| Normal `poe check` recheck before `d09a6d7` | Same code plus the smoke-script working-tree delta later committed as `d09a6d7` | 1,082 passed, 15 skipped; 90.71% coverage; pytest 322.38 s; all static and boundary checks passed |
| Final normal `poe check` on the evidence-doc tree | `d09a6d7` plus the uncommitted docs-only delta in this record | 1,082 passed, 15 skipped; 90.70% coverage; pytest 321.41 s; all static and boundary checks passed |

The exact `0734ef3` revision passed both normal and `TERM=dumb` gates. The final normal recheck includes the smoke helper delta; no application source changed after `0734ef3`.

## Real-agent E1–E6

The last full passing invocation remains the 2026-10-02 run at `159db49`, recorded in [E2E results](e2e-results.md). Two fresh invocations were made on 2026-10-03; neither meets Task 88's requirement that E1–E6 pass together on the resulting revision.

The first run, `build/e2e/20261003T090653Z.json`, started at 09:06:53 UTC and ended at 09:24:07 UTC on clean `12cf19e`. E1, E2, E4, E5 and E6 passed; E3 failed both attempts (91.5 s and 121.9 s, $0.1371 and $0.1223 agent-reported). The investigation identified a race in the E3 test's child-window observer and independent variation in how the agent interpreted the phase-B decomposition request. The attempt JSON retains status, duration, cost and rerun reason, but the date/scenario diagnostic names were reused by the next run, so the old detailed text is not preserved. The observer race was fixed and covered at `0734ef3`; this did not turn the failed baseline run into a pass.

The second run used `SHIKIRU_E2E=1 uv run poe e2e` on clean `0734ef38810b4ec9ef14d54a030b3ab5b19da06d`. It began at 10:03:58.389 UTC, ended at 10:20:58.371 UTC, and exited 1 (pytest reported 1,020.51 s). E1 and E4 exhausted the configured attempt plus one retry in 11.1 s and 11.2 s; E2, E3, E5 and E6 hit the watchdog after 246.8 s, 246.3 s, 251.9 s and 247.1 s. Each saved diagnostic shows `turn_failed: You've hit your weekly limit`, followed by `quota_exhausted`; the provider message said the limit resets Oct 7 at 08:00 Europe/Warsaw. Every reported scenario cost is null, so total cost is unknown, not zero. Diagnostics for this invocation are preserved under `build/e2e/diagnostics/20261003T100358Z/`.

Both artifacts record Python 3.14.7, Claude Code 2.1.287, Codex CLI 0.160.0, tmux 3.7c and git 2.55.0. The second run's projects were under `/Users/pavel/.cache/shikiru-e2e`, outside `$TMPDIR`; the artifact records Codex `workspace-write` and `approvals_reviewer = auto_review`. No further paid rerun was made after the account reported its weekly limit. The exact scenario durations, attempts, costs and artifact paths are in [E2E results](e2e-results.md).

## macOS clean-install and workspace smoke

The final clean-install smoke passed on macOS on 2026-10-03. Its ignored artifact is `build/release-readiness/clean-install-20261003T104844Z-88939.json`; its captured terminal/log evidence is `build/release-readiness/clean-install-20261003T104844Z-88939.log`. It records code source `0734ef38810b4ec9ef14d54a030b3ab5b19da06d` and smoke-script diff fingerprint `2d74608d2eb7cf3806bf7f9688a70a6764880e5e62f9788bd0f2fdbb62deb09c`; that exact harness delta was subsequently committed as `d09a6d7`.

The existing script installed Shikiru into a disposable `HOME`, XDG directories and uv tool/cache directories, with a private tmux socket. The installed CLI passed interactive init and Doctor at 120×38 and 68×16, `init --yes`, Doctor, start/submit/status/ls, the attach picker at 120×38 and 68×16, named attach, and stop/cleanup. A fake-agent quickstart task T-1 merged on `main` and ticked A1; the log captures the live Home pane after merge and records no rendered traceback. The test-only fake agent reports no billed cost; cost is unknown.

For installed-package navigation, the smoke invoked five existing integration tests with the disposable tool's package Python, `NO_COLOR=1`, real SQLite/Git fixtures, Textual `run_test`, and private test tmux. The tests covered `/` title search, Enter to results, arrow selection, Enter to detail, Escape restoring Home selection, `Ctrl+P` carrying task and Implementer context, a contextual message reaching Applied, Home at 120×40 down through 20×8, task detail at 36×20 and 48×14, reachable controls, and static countdown updates with color disabled. They passed (`5 passed in 7.09 s`, with one non-blocking pytest cache warning). The artifact labels these navigation checks as headless; the live private-tmux launch and attach-picker checks are separate evidence.

The 10:23 run (`clean-install-20261003T102342Z-81689.json`) passed the original clean-install checks before navigation was added. The next four artifacts (`clean-install-20261003T103055Z-82810.json`, `clean-install-20261003T103407Z-84333.json`, `clean-install-20261003T103535Z-85813.json`, and `clean-install-20261003T103753Z-87331.json`) record failures in the added shell keystroke observer as it tried to time asynchronous focus transitions. Those checks were replaced with the existing installed-package Textual boundary tests above; the final run passed. Linux was not run and is not verified.

## Remaining limits and release status

- Task 88's real-agent AC is open: no fresh invocation on the resulting revision passed E1–E6 together. Provider quota prevented that verification; unknown costs must remain unknown.
- Linux smoke is not verified. The evidence here is from this macOS host, Python 3.14.7, Claude Code 2.1.287, Codex CLI 0.160.0, tmux 3.7c and git 2.55.0.
- The smoke's fake agent verifies packaging, CLI/workspace paths and recorded behaviors without a billed provider call. It does not substitute for E1–E6.
- Historical Tasks 59, 74h and 74p remain independent review gates and are not closed by Phase 8 delivery or this partial verification record.

Task 88 and Phase 8 release verification remain open. Do not tick Task 88 until its four acceptance criteria pass on the resulting revision.
