# Periodic pushes and local squash integration

The Operator requested automatic pushes to origin, a configurable four-hour default interval
in installation step #5, zero meaning push after every integration, and a new
"Local squash and merge" choice in step #6 with accurate task results.
This tracker replaces the implementation scope of planning task T-27 with P1 and P2 below.
Each follow-up task checks only its own entry when complete. This plan implements no behavior.

## Findings and shared decisions

- Configuration currently supports only `merge` and `pr`; `LocalMerge` rebases and fast-forwards
  the base or updates its ref with an expected-old-value check. There is no periodic push service.
- Use `orchestration.push_interval_seconds`, a nonnegative whole number with packaged default
  `14400`. Step #5 displays seconds, identifies the default as four hours, and explains zero.
  Follow the existing field validation, review summary and config serialization paths.
- Automatic pushes apply to locally integrated base branches for both local strategies. Use
  `repo.remote` (normally `origin`) and the base recorded for the integration, including an
  inferred base when `repo.base` is unset. Push the base to its same-named remote branch;
  do not push task branches, tags or unrelated refs. PR mode keeps its existing push path.
- For positive intervals, the first pending integration starts the interval. Subsequent pending
  work is due no earlier than one interval after the last successful push; when that deadline
  has already passed, newly integrated work is due immediately. More integrations do not postpone
  an existing deadline. With no pending local integrations, no push is needed.
- Persist pending targets and scheduling facts independently of expiring event history. Restart
  restores deadlines and retries overdue work. Zero attempts a push following each successful
  integration; a remote failure retains pending work for retry without undoing local completion.
  A push acknowledges only the work it actually sent, preserving any newer pending integration.
- Use `squash` for the new strategy, retaining `merge` as the default. A nonempty task produces
  one commit parented by the current base with the rebased task's complete tree. Use a stable,
  descriptive commit subject including the task ID and title; its body can retain the summary.
  An empty task must not manufacture an empty commit.
- Git is the authority for landed commits. Persist sufficient integration evidence to resume
  safely across a crash after the Git update but before `TASK_DONE`, without duplicating a squash
  commit or losing the commit needed for results. Keep this specific to integration recovery.
- Today `derive_task_results` gets completed commit lines from `TASK_DONE` but produced counts
  from older facts. Squash must make both surfaces agree. Preserve old records and explicitly
  recorded empty results; distinguish absent legacy evidence from a known empty commit list.

## Execution order and verification

P1 runs first; P2 depends on P1 because both touch integration completion, configuration and wizard
wiring. P2 connects squash completion to the push service and verifies the combined behavior.
The report emits both implementation tasks because the Operator requested working features.
No existing trackers were selected; this document is the tracker for these tasks. Leave the
unrelated quota/usage plan unchanged.

Use constructor injection, `Clock`, `SubprocessRunner` and the existing fault reporting boundaries.
Keep SQL in `store`, pure decisions in `domain`, defaults in `defaults.toml` and protocol values
in named constants. Prefer small supporting changes to a generalized integration redesign.

In each implementation worktree, run `uv sync --locked` and confirm imports and tool entrypoints
resolve there. Use real SQLite and local bare Git remotes, the fake agent and `tmux_server` where
needed, `FakeClock` for time and `wait_until` for asynchronous observations. Test behavior at
boundaries; unit tests belong to domain rules, models and parsers. Run relevant checks during
development and `PYTEST_ADDOPTS='--dist=loadfile' uv run poe check` on final changes before the final
commit and completion report, with coverage at least 80%. For this documentation-only planning
commit, review the diff and run `git diff --check`; do not run application tests.

## P1 — Push locally integrated changes periodically

- [x] Implement configurable periodic and after-integration pushes, including installation step #5.

Add the setting through packaged defaults, settings, orchestration choices, wizard field parsing,
review and config writing. Wire a dedicated push service into engine maintenance and local
integration completion. Store minimal durable pending state through the normal migration/store
boundary. Coordinate registration and recovery so an applied integration cannot disappear from
pending work after a crash. Keep remote work bounded and cancellable; isolate retries using the
existing fault/backoff policy so remote failure does not stall independent task dispatch.

Relevant files: `src/shikiru/config/{defaults.toml,settings.py}`,
`src/shikiru/dashboard/{wizard.py,orchestration_fields.py}`,
`src/shikiru/operator/detectors.py`, `src/shikiru/engine/{integration.py,daemon.py,reconcile.py}`,
`src/shikiru/engine/{tick_guard.py,fault_reporter.py}`, `src/shikiru/bootstrap.py`,
`src/shikiru/workspace/git.py`, `src/shikiru/store/`, `README.md`, `docs/design.md`.
Relevant tests: `tests/integration/test_dashboard_wizard.py`,
`tests/integration/test_engine_{integration,daemon,restart,hung_remote,pull_request}.py`,
`tests/support/{git_repo.py,github.py}`. Add focused integration coverage where appropriate.

## Acceptance criteria
- Configuration without an override uses `orchestration.push_interval_seconds = 14400`; a saved nonnegative whole-number override controls engine push timing, and negative or invalid values are rejected.
- Installation step #5 offers the interval with explicit seconds, four-hour default guidance and zero semantics; Finish persists valid values, while invalid input keeps the wizard open on the offending field without writing configuration.
- In local merge mode, multiple integrations within an interval accumulate, no push occurs before the deadline, and the base reaches origin when due even if no further integration happens; subsequent integrations cannot postpone a pending deadline.
- An interval of zero attempts a push after each successful local integration; failed or held integrations do not trigger publication of their unintegrated task branches.
- Automatic pushes target the integration's base branch on `repo.remote`, including inferred bases and the default origin; they never force-overwrite divergent remote history or publish task branches or unrelated refs.
- Restart preserves pending work and deadlines, sends overdue work, and does not drop an integration that arrives during a push or during recovery of local completion.
- Missing remotes, rejected pushes and timeouts produce visible fault evidence, retain pending work, and retry with bounded backoff while local completion and independent engine work remain usable; shutdown supervises any outstanding push operation.
- PR integration retains its current behavior and does not activate the local-base push service.
- Real-remote integration tests with `FakeClock` verify timing, zero mode, restart, concurrent pending work and failure/recovery; README and design describe the setting, scope and reload behavior; the required full gate passes and P1 is checked complete.

## P2 — Squash local integration and show the landed result

- [x] Add local squash integration, installation step #6 and accurate retained task results.

Depends on P1. Extend the strategy choice and wire local base resolution for `squash`. Share the
existing preconditions and serialized integration boundary where practical, preserving conflict
consultation/review. Keep checkout/index protection for overlapping user changes and compare-and-swap
protection when the base is not checked out. Record and recover the actual landed squash commit;
do not infer completion solely from original task-branch ancestry, which squash breaks.

Persist the actual landed commit list and sufficient final change totals before cleanup. Derive
both Result and Results from that evidence for newly completed tasks. Label the integration mode
consistently where it is shown, while preserving existing records with missing new fields. Connect
the successful local squash integration to P1's publication mechanism.

Relevant files: `src/shikiru/config/settings.py`, `src/shikiru/dashboard/wizard.py`,
`src/shikiru/operator/detectors.py`, `src/shikiru/bootstrap.py`,
`src/shikiru/engine/{integration.py,reconcile.py,lifecycle.py}`,
`src/shikiru/workspace/{git.py,roots.py}`, `src/shikiru/store/`,
`src/shikiru/domain/{results.py,briefing.py}`, `src/shikiru/dashboard/{briefing_view.py,fold_bodies.py}`,
`src/shikiru/plugins/widgets/header.py`, `README.md`, `docs/design.md`.
Relevant tests: `tests/integration/test_engine_{integration,restart,pull_request}.py`,
`tests/integration/test_dashboard_{wizard,results,briefing,widgets}.py`,
`tests/integration/test_workspace_roots.py`, `tests/unit/test_domain_results.py`.

## Acceptance criteria
- Installation step #6 includes exactly the user-facing option "Local squash and merge"; selecting it persists `orchestration.integration = "squash"`, reloads successfully, and has a readable integration-mode label in the dashboard.
- Integrating a nonempty task with multiple commits into main advances main by exactly one task commit whose parent is the previous main tip and whose tree contains the complete rebased task changes; configured alternative base branches behave the same way.
- Squash works when the base is checked out and when it is only a ref; overlapping dirty checkout changes, off-task HEAD, uncommitted task work and unresolved conflicts retain the existing blocking/recovery behavior, while unrelated checkout edits remain intact.
- Restart after the squash Git update but before completion recording recognizes the applied integration, records the correct landed commit and completes once without an additional squash commit or loss of work.
- Completed task details Result and extended Results show the actual squash commit count and destination, and Results lists its landed SHA and subject; file/addition/deletion totals describe the integrated change rather than stale pre-integration facts.
- The same result remains accurate after task worktree and branch cleanup and an engine/dashboard restart; an empty integration records zero landed commits without falling back to old report commits or manufacturing an empty commit, and legacy results remain readable.
- Squash completion participates in P1's positive-interval batching and zero-mode push-after-integration behavior; a failed push retains the completed local result and recoverable pending publication.
- Existing merge and PR strategy behavior remains covered; real Git/SQLite integration tests cover squash, guards, recovery and cleanup, domain tests cover result evidence precedence, and dashboard tests verify both result views.
- README and design describe local squash behavior and its results; the required full gate passes and P2 is checked complete.
