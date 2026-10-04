# Phase 9 compact TUI release readiness

Date: 2026-10-04. Tasks 89–96 are verified on macOS for the documented Focus behavior, operator workflows and release gates. The implementation source used for final verification is commit `d49791c269ceef683c8a3155593d3f5ec9b5825d`, plus a dirty documentation and clean-install-selector delta; no application code changed after that commit. The final evidence/checklist commit follows the verification source. GNU/Linux was not run. Historical review gates Tasks 59, 74h and 74p remain independently open.

## Design and evidence contract

The shipped Focus readouts follow the approved broad direction: list and inspector on wide panes, list-first navigation on compact panes, concise engine/project/resource summaries, contextual detail, explicit billing declarations, honest token and charge coverage, retained session/model evidence and accessible actions. API billed amounts, API-equivalent estimates and unknown monetary evidence remain separate. Subscription declarations do not infer an account, a purchased plan, authentication state or a zero task charge. Quota is provider/account scope and can include other projects. Cumulative session tokens can span models; only retained per-turn observations are attributed to a model. Missing event pairs, truncated history and unavailable identities remain explicit gaps.

The Owner reports that the real test-project TUI is improved but still differs significantly from the approved screenshot, design and UX. This record does not claim exact 1:1 visual or interaction parity. A separate alignment follow-up remains after Phase 9; the approved reference was not changed as part of Task 96.

The Codex metadata compatibility fix in `d49791c` uses the installed CLI's bounded, read-only bundled model catalog when the refreshed model cache omits the configured model. It preserves explicit metadata provenance and errors and uses the injected `SubprocessRunner`. The E2E-only `gpt-6-luna` choice is confined to `tests/e2e/real_agent_config.py`; project defaults, the user's Codex cache and authentication were not modified.

## Connected operator flows and seeded projections

The selected Task 96 command ran on the source tree at `d49791c` with only documentation and smoke-selector changes pending. Five existing connected fake-agent integration tests and four existing seeded UI-boundary tests passed together: `9 passed in 10.86s`. This reuses the existing scenario support and does not add a second test catalogue.

Command:

```sh
uv run pytest -q --no-cov \
  tests/integration/test_workspace_walkthroughs.py::test_fake_agent_workspace_finds_verdict_and_applies_contextual_message \
  tests/integration/test_workspace_walkthroughs.py::test_fake_agent_escalation_updates_an_open_resolution_form_from_another_operator \
  tests/integration/test_workspace_walkthroughs.py::test_fake_agent_task_takeover_leaves_other_task_progress_and_pause_visible \
  tests/integration/test_workspace_walkthroughs.py::test_dashboard_queued_submit_survives_engine_and_dashboard_restarts_once \
  tests/integration/test_workspace_walkthroughs.py::test_real_terminal_cleanup_remains_exportable_with_missing_evidence_marked \
  tests/integration/test_dashboard_remaining_widgets.py::test_project_usage_projects_persisted_coverage_without_inventing_api_spend \
  tests/integration/test_dashboard_remaining_widgets.py::test_resource_sampling_failure_is_stale_and_task_navigation_still_works \
  tests/integration/test_dashboard_task_detail.py::test_task_history_keeps_closed_lineages_and_observed_model_deltas \
  tests/integration/test_dashboard_task_detail.py::test_recorded_stop_is_complete_but_unended_turn_has_a_history_gap
```

The five connected fake-agent flows exercised verdict/decision review, contextual message and control application, takeover while other work continues, queued-command recovery after engine/dashboard restart, and export of completed-task evidence after resource cleanup. The four separate seeded projections exercised mixed billing and coverage without invented API spend, stale resource sampling, closed session lineages with observed model deltas, and a completed stop paired with an explicit gap for an unended turn. They use real SQLite, Git, private tmux and Textual boundaries where applicable; the tests do not claim real-agent behavior.

## Full code gates

Both gates used four pytest workers and `UV_CACHE_DIR=/private/tmp/shikiru-phase9-uv-cache`. The normal gate was green before the reviewed `d49791c` commit; the TERM=dumb gate ran after that commit. Both ran the same application/test implementation; the remaining tree delta was documentation and the smoke-test selector.

| Gate | Result |
|---|---|
| `PYTEST_XDIST_AUTO_NUM_WORKERS=4 UV_CACHE_DIR=/private/tmp/shikiru-phase9-uv-cache uv run poe check` | 1,142 passed, 14 skipped; 90.15% coverage; pytest 248.80 s |
| `TERM=dumb PYTEST_XDIST_AUTO_NUM_WORKERS=4 UV_CACHE_DIR=/private/tmp/shikiru-phase9-uv-cache uv run poe check` | 1,142 passed, 14 skipped; 90.16% coverage; pytest 332.84 s |

Formatting, Ruff, comment/literal checks, type checking, import contracts, vulture and architecture checks passed. Five architecture contracts remained kept. The gate is above the required 80% coverage.

## macOS clean-install and navigation smoke

`bash scripts/release-readiness-smoke.sh` passed on the `d49791c` application source. The recorded artifact is [`clean-install-20261004T114746Z-33519.json`](../build/release-readiness/clean-install-20261004T114746Z-33519.json), with log [`clean-install-20261004T114746Z-33519.log`](../build/release-readiness/clean-install-20261004T114746Z-33519.log). The JSON records source diff fingerprint `5395fa851ee7b816897d0ec4a54e2aa0e874bd89b6114d917e2080f2af10f8ed`; this includes the pending docs and Task 96 selector update.

The script used a fresh disposable HOME, XDG directories, uv tool/cache directories and private tmux socket. It verified installed package data and entry points, interactive init/Doctor at 120×38 and 68×16, `init --yes`, Doctor, start, submit, status, `ls`, picker and named attach, stop/cleanup, and installed-package Textual navigation. Seven Focus boundary tests passed in 10.66 s. Headless Home navigation retained search/selection through detail and Escape, contextual palette task/role context, and an Applied message; responsive Home and detail/forms were covered down to 20×8 and at 36×20/48×14. The script also started the installed Home in its private live-tmux fixture and attached the 68×16 picker. The fake agent reports no billed cost; its null cost is unknown, not zero. One pytest cache warning about `/dev/.pytest_cache` did not affect the passing result.

This is a macOS result only. Linux, widths below 20 columns and ambiguous-width CJK terminal behavior were not run or verified.

## Fresh bounded E1–E6

One authorized `SHIKIRU_E2E=1 uv run poe e2e` invocation passed the scripted fake-agent boundary plus E1–E6. It started 2026-10-04 11:49:36.516 UTC and finished at 12:12:48.020 UTC (23 m 11.5 s; pytest reported 1,391.98 s). Artifact: [`20261004T114936Z.json`](../build/e2e/20261004T114936Z.json). At invocation time `HEAD` was `d49791c269ceef683c8a3155593d3f5ec9b5825d` and `source_dirty=true`; the dirty delta contained documentation and the smoke selector, not application code. The schema-5 artifact has no dirty-diff fingerprint.

| Scenario | Outcome | Duration | Attempts | Reported cost |
|---|---|---:|---:|---|
| E1 triage, implement, verify and merge | passed | 101.67 s | 1 | Unknown |
| E2 operator message reaches verify | passed | 166.38 s | 1 | Unknown |
| E3 dependent plan and parallel progress | passed | 313.81 s | 1 | Unknown |
| E4 MCP product-decision path | passed | 182.17 s | 1 | Unknown |
| E5 restart resumes interrupted turn | passed | 98.05 s | 1 | Unknown |
| E6 takeover holds only selected task | passed | 523.92 s total | 2 | Unknown |

E1–E5 passed on attempt 0. E6's first attempt hit an agent-rule assertion; its event trace had no `engine_fault`, both tasks completed, and the agent claimed to preserve tracker changes while A2 remained unchecked after conflict resolution. The harness correctly diagnosed the existing “agent skipped a rule” retry case. The original trace is preserved at [`2026-10-04-e6-attempt-0.txt`](../build/e2e/diagnostics/20261004T114936Z/2026-10-04-e6-attempt-0.txt); its single configured rerun passed. This was the only agent-behavior retry.

The E2E ladder used Codex `gpt-6-luna` at low effort for Leader, Implementer and Helper. Tool versions were Python 3.14.7, Claude Code 2.1.289, Codex CLI 0.160.0, tmux 3.7c and git 2.55.0. All scenario costs were null, so actual total cost is unknown, not zero. E4 uses a scripted MCP client as AIOperator; it is not paid operator inference. The run makes no claims about account authentication, subscription state, other providers/models, Linux or untested hosts.

The earlier same-day run at `d751832` is preserved at [`20261004T110240Z.json`](../build/e2e/20261004T110240Z.json) with six scenario diagnostics. It failed all six at `shikiru start` doctor preflight before any model turn because the refreshed Codex cache omitted `gpt-6-luna`, although the installed CLI's bundled catalog contained it. That zero-turn product setup failure is not counted as an agent retry or as a pass; `d49791c` addresses the compatibility issue. The earlier artifact and diagnostics were left unchanged.

## Visual review and limits

The accepted Task 95 manual review remains in [`task95-visual-review-2026-10-04.md`](task95-visual-review-2026-10-04.md) with native Textual SVGs at Home 120×40, 48×20, 36×14 and 20×8; task decision, usage/subscription, Views and actions at 36×20; Views/actions at 20×8; and stale resources at 120×40. It records readable focus/state/charge colors, responsive clipping and scrolling, visible task identity/holds, reachable on-demand controls, stale-resource age/error and known size limits. The color assessment is supported by the original normal-color captures; several later tiny-pane recaptures are monochrome and are not used as palette evidence. The review added no screenshot dependency or product harness. The Owner reviewed the native artifacts and accepted Task 95's visual review record, while separately reporting the substantial real-project parity gap stated above.

Ordinary cleanup still limits the evidence export to retained Shikiru rows and events; it is not a complete agent transcript or reconstruction of removed worktrees. Session totals are cumulative, model history is bounded by retained events, and missing event pairs remain gaps. Widths below 20 columns and ambiguous-width CJK settings remain unverified. No Linux claim is made.

## Host hygiene and retained evidence

The final read-only process comparison used `/private/tmp/shikiru-phase9-process-baseline.txt`. It found no new pytest, fake-agent or Task96 private-tmux process. The additional Shikiru processes found belonged to the live user `test-project` dashboard/attach session; they were preserved, along with the dashboard/walkthrough processes and sessions called out in the existing baseline. The smoke's disposable tool home and private session cleaned up on exit. The E2E cache under `/Users/pavel/.cache/shikiru-e2e`, shared uv cache, Task 95 captures/previews and prior phase/task logs remain intact.

The socket audit compared `/private/tmp/tmux-501` to `/private/tmp/shikiru-phase9-socket-baseline.json`: no additional socket names remained. `shikiru-soak-b61ed09a`, `shikiru-walkthrough` and `shikiru-soak-e5af753e` retained their recorded inodes. The `default` socket inode differs from the preserved baseline as previously observed during Task 94; Task 96 did not contact or clean it. No broad process kill, default-server command or unrelated temporary-file deletion was performed. The release smoke reported its own disposable Home/XDG/uv/private-tmux isolation and cleaned those fixtures automatically.

## Remaining independent gates

This record does not close historical Tasks 59, 74h or 74p; each remains open independently. GNU/Linux and older tool floors were not verified. The exact Focus visual/UX alignment reported by the Owner remains a separate post-Phase 9 follow-up.
