# Popup entry and engine-stop follow-up

Status: complete (Task 249). The working TUI is the priority; HTML parity is not required.

- Add one blank line between Export evidence guidance and its control fields.
- Initially focus Role in Message, Take over and Hand back. Preserve selected-value visibility, dropdown affordance, focus after selection and ordinary Tab navigation.
- Keep the dashboard alive and usable when the engine stops. Show the stopped state, offer Start engine, and allow restart through that same dashboard. Session/client existence alone does not establish dashboard health.

Read the relevant normative design and AGENTS.md. Diagnose and reproduce the shutdown failure with actual engine/dashboard processes on a private tmux server. Preserve the user's running project/session. Keep code compact, use existing boundaries, extend meaningful behavioral coverage, avoid style/wording inventories, and verify spacing through native review. Independent review and both full quality gates precede completion and commit.

## Follow-up evidence

- A fresh CLI Stop popup initially left focus on its close control, so native Enter did not target the action. After routing initial focus through the palette's context restore path, the native input reached the Stop confirmation. Textual also swallowed an immediate second Enter while the action button was active; the palette now consumes Enter while confirmation is armed.
- A private-tmux regression seeded a held task with persisted Leader and Implementer runs, sessions and distinct panes. The first Enter armed confirmation without changing command history. The second recorded one completed Stop and released the engine lock, then the Home pane exited cleanly with status 0 before its task-scoped Actions list could show Start engine. The earlier idle-project pass did not cover this state.
- The held-task reproduction traced the status-0 Home exit to a cancelled Textual `AwaitRemove`: successive Actions-list refreshes cancelled an exclusive worker while its list rebuild awaited deferred child removal. Textual swallowed that `CancelledError` as a normal app close. Actions-list refreshes now coalesce serially instead of cancelling an in-flight rebuild, and list dismissal waits for an active rebuild to finish before Textual unmounts and cancels screen-owned workers. The private native test completes Stop, verifies the stopped Home state and same selected task, reopens Actions, restarts through the same Home process and confirms that Home still responds.
- The Textual render places one terminal row between Export guidance and Destination at 100×40. Destination remains visible and focused in the clipped form at 40×14 and 40×8; at 40×8 it scrolls into view at row five. The integration route reaches every Export control at 40×8.

## Verification

- The shared focus behavior checks pass in mounted and standalone popups, including reopening a Message draft: Role receives initial focus, changing its selection retains focus, and Tab reaches the next field or action. The focused dashboard palette/actions/app suite passes (107 passed); the native stop/restart and focus/Enter cases pass (7 passed).
- The normal full gate, `PYTEST_ADDOPTS='-n 2 --dist loadfile' UV_CACHE_DIR=/private/tmp/shikiru-249-uv-cache uv run poe check`, passes with 1,625 passed, 14 skipped and 91.52% coverage. Log: `/private/tmp/shikiru-249-normal-green.log`.
- The `TERM=dumb NO_COLOR=1` full gate with the same worker configuration passes with 1,625 passed, 14 skipped and 91.51% coverage. Log: `/private/tmp/shikiru-249-dumb-green.log`.
- One earlier normal full-gate attempt exposed the task-detail Hand back helper still assuming the action was focused on popup entry; it now waits for Role focus and advances with Tab, and its affected integration case passes. A subsequent full attempt hit a transient missing fake-agent report while tightening a test state directory; that exact restart case passed when rerun serially, and the fresh full normal gate passed. The first monochrome full attempt hit the mock Docker `ps` 1.5-second deadline under load; that exact stats case passed in isolation, and the fresh full monochrome gate passed. No runtime changes were made for these unrelated transient failures.
- Final resource audit found no task-owned pytest workers, fake agents or private tmux servers left running. The user's default tmux server (PID 50443) and test-project engine/dashboard/client were left untouched; pre-existing `/private/tmp/shk-tmux-*` directories from October 16 were also left untouched. The task-owned UV cache is removed after both gates; green logs are retained.
