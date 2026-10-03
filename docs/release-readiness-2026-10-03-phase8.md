# Phase 8 workspace release readiness — verified

Date: 2026-10-03. **Task 88 is complete for Phase 8 on macOS.** The fresh real-agent E1–E6 run, both full code gates, and the clean-install/navigation smoke passed. GNU/Linux was not run and remains unverified. This evidence closes only Phase 8 verification; the independent historical review gates Tasks 59, 74h and 74p remain open.

## Delivery and design agreement

Tasks 75–87 delivered the Operator Workspace and its documentation. Task 88's application fix is in `e5a7f24`: detail refresh restores the public intended scroll target and declines a stale restore if the target changed while refresh was queued. This preserves focused-control visibility during motion refresh and preserves a stable operator reading position. The integration regression checks the timeline input at 36×20 and 48×14, focus/value, follow state, and settled reading position across resize. It adds no retry or timeout increase.

The real-agent invocation ran on clean `01ab54e8748a56d8ee6310de5da3c18d214236e2`. It includes the E2E harness argument fix in `01ab54e`; the later `6e7a2ed` changes only the integration test's observation boundary to await Textual's scheduled animations before comparing the settled reading position. No application or E2E scenario source changed after the passing invocation. The final documentation/checklist update is also documentation-only.

The verified behavior agrees with the [workspace specification](tui-workspace.md), [design 07 §5](design/07-engineering.md#5-e2e-scenarios-poe-e2e-shikiru_e2e1), and [Task 87 walkthrough record](workspace-walkthroughs-2026-10-03.md). The walkthrough record reports five connected fake-agent Textual/SQLite/Git/tmux flows passing in 22.53 s, 21 seeded UI-boundary tests passing, and five plan/tmux/CLI boundary tests passing in 1.69 s. It distinguishes these fixture runs from the real-agent E1–E6 release check.

## Full code gates

Both gates ran with four pytest workers on the application/test tree at `6e7a2ed02dc35f8e215c8b8c3920db035c4e8383`. The final evidence-document/checklist delta was applied afterward; a final normal gate was run with that documentation-only delta staged.

| Gate | Result |
|---|---|
| `PYTEST_XDIST_AUTO_NUM_WORKERS=4 uv run poe check` | 1,082 passed, 15 skipped; 90.71% coverage; pytest 345.41 s |
| `TERM=dumb PYTEST_XDIST_AUTO_NUM_WORKERS=4 uv run poe check` | 1,082 passed, 15 skipped; 90.73% coverage; pytest 281.81 s |
| Final normal gate with the staged evidence/checklist delta | Passed; 1,082 passed, 15 skipped; coverage above 80%; lint, types, architecture and other static checks passed |

The full gate ran formatting, Ruff, comment/literal rules, import contracts, basedpyright, vulture, and the non-E2E suite. All five architecture contracts remained kept. The displayed coverage exceeds the required 80%.

## Fresh real-agent E1–E6

One fresh `SHIKIRU_E2E=1 uv run poe e2e` invocation passed all six real scenarios together, plus the fake-agent boundary test. The artifact is `build/e2e/20261003T193209Z.json`, schema 5. It records `source_commit=01ab54e8748a56d8ee6310de5da3c18d214236e2`, `source_dirty=false`, exit status 0, seven passed tests and 1,097 deselected.

| Scenario | Outcome | Duration | Attempts | Reported cost |
|---|---|---:|---:|---|
| E1 triage, implement, verify and merge | Passed | 83.93 s | 1 | Unknown |
| E2 operator message reaches verify | Passed | 182.16 s | 1 | Unknown |
| E3 plan, dependent tasks, two concurrent windows, conflict and merge | Passed | 346.33 s | 1 | Unknown |
| E4 MCP operator resolves a product decision | Passed | 189.66 s | 1 | Unknown |
| E5 engine restart resumes the interrupted turn | Passed | 167.27 s | 1 | Unknown |
| E6 takeover holds only the selected task | Passed | 308.64 s | 1 | Unknown |

The run started at 2026-10-03 19:32:09.251 UTC and finished at 19:53:32.765 UTC (21 m 23.51 s wall time; pytest reported 1,284.00 s). Every scenario passed its first attempt. The artifact's agent-reported costs are null for all six scenarios, so the total cost is unknown, not zero.

The E2E-only ladders used Codex `gpt-6-luna` at low effort for Leader, Implementer and Helper. This was the already-authorized agent backend selection for this verification; no provider authentication or production default was changed. Tool versions recorded in the artifact: Python 3.14.7, Claude Code 2.1.287, Codex CLI 0.160.0, tmux 3.7c and git 2.55.0. The projects were under `/Users/pavel/.cache/shikiru-e2e`, outside `$TMPDIR`; the artifact records the Codex workspace-write sandbox and `approvals_reviewer=auto_review`.

The E3 assertions passed for the requested plan, dependency execution, two simultaneous task windows, conflict resolution and integration. Its earlier test-observer race is fixed in the E2E harness; the passing run also records the agent's actual behavior, rather than treating a prior failed attempt as success.

## Preserved earlier attempts

The failed records remain useful context and are not counted as passing verification:

- `build/e2e/20261003T090653Z.json` on `12cf19e`: E1, E2, E4, E5 and E6 passed; E3 failed both attempts. The invocation JSON preserves the attempts, but its date/scenario diagnostic names collided with the next invocation, so the original detailed E3 text was overwritten.
- `build/e2e/20261003T100358Z.json` on `0734ef3`: every scenario hit the Claude weekly quota limit or the resulting watchdog stall. The harness classified the E1/E4 quota assertions as “agent skipped a rule”; that is a misclassification, not evidence of an agent-rule violation. The raw provider message says the limit resets Oct 7 at 08:00 Europe/Warsaw. Separately, the engine recorded `quota_blocked` until 2026-10-03T14:01:00+00:00. Those dates differ; the artifacts do not explain the discrepancy or establish that the provider reset was parsed correctly. Costs were null.
- `build/e2e/20261003T183808Z.json` on `129ce0b`: all six scenarios failed in about 1.27 s before an agent turn because the test harness passed a duplicate `--set` flag before the role overrides. The correction is in `01ab54e`; the later fresh invocation above passed.
- A clean-install smoke attempt on `01ab54e` found that the integration test compared a transient scroll sample (17.1879) with the settled position after resize (17). The focused timeline remained visible; the test now waits for scheduled animations before taking and comparing the reading position. The corrected test passed both full gates and the final clean-install smoke below.
- An additional normal full-gate attempt was interrupted at about 19% when verification was reprioritized to run E1–E6 on the clean E2E revision. It is not reported as a passing or failing full gate; the final normal and `TERM=dumb` results above are the completed gates.

The earlier quota-run diagnostics are preserved under `build/e2e/diagnostics/20261003T100358Z/`. Detailed scenario durations and the earlier passing baseline are retained in [E2E results](e2e-results.md).

## macOS clean-install and workspace smoke

The final smoke passed on clean `6e7a2ed02dc35f8e215c8b8c3920db035c4e8383` using `bash scripts/release-readiness-smoke.sh`. Its artifacts are `build/release-readiness/clean-install-20261003T200558Z-23347.json` and `build/release-readiness/clean-install-20261003T200558Z-23347.log`; the command completed in 25.54 s. The JSON records a fresh disposable HOME, XDG directories, uv tool/cache directories and private tmux socket, and reports `status=passed`.

The smoke installed the package and verified defaults and entry points; interactive init/Doctor at 120×38 and 68×16; `init --yes`, Doctor, start, submit, status, ls, picker attach, named attach, stop and cleanup. Its fake-agent task T-1 merged on `main` and ticked A1. A test-only fake agent reports no billed cost; that cost is unknown.

Installed-package headless Textual navigation observed Home search (`/`), Enter to results, arrow selection, Enter to task detail, Escape returning to the selected Home row, and Ctrl+P carrying the selected task and Implementer role. A contextual message reached Applied. Home was checked at 120×40 down through 20×8; task detail at 36×20 and 48×14; NO_COLOR words/controls and static countdown behavior were checked. The same smoke separately launched the installed Home pane and attach picker in its private live tmux session. The artifact labels the Textual navigation probe as headless.

GNU/Linux was not run. Its smoke and terminal-specific behavior are unverified.

## Remaining limits

- This record verifies the Phase 8 workspace on the tested macOS environment and toolchain. It does not claim Linux verification, older tmux/git floor testing, or overall project release readiness.
- Historical review gates Tasks 59, 74h and 74p remain independently open; Phase 8 success does not close them.
- The E2E run is a measured sample using the recorded Codex model ladder. It does not make claims about untested providers, models or hosts.
- The workspace exports only retained Shikiru evidence; it is not a complete agent transcript or a reconstruction of cleaned worktrees, as described in the specification.
