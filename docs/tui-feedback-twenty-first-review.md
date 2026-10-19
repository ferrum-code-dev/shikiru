# Task switching, rung selection and engine-start follow-up

Status: complete (Tasks 250–252). The working tool is the priority; HTML remains illustrative.

- Switch task initially loads 20 tasks. Each explicit load-more action appends the next 20, retaining selection and the active search. Use a smaller popup with a bounded task viewport rather than filling a tall terminal.
- Confirmed route: while a task is escalated, changing to another rung for the same agent and then taking over showed the old model inside the resumed agent TUI. The interactive resume now receives the selected model and effort on the same session. A later rung change leaves the active TUI and its title on their effective settings, with the configured next rung shown separately; the next automated turn uses the new rung. A different-agent change keeps Take over on the existing agent session, then starts a fresh session with the report hand-off on the next automated turn.
- Start engine closes its action popup after successful startup through the existing completion path, returning to the caller with Stop engine available. Keep startup failures visible and retryable; preserve duplicate-start protection, the same Home and selected task, and the attached client.

Execute Tasks 250, 251 and 252 sequentially, each through a GPT-6-Luna agent at xhigh effort. Review each completed candidate and send fixes to the same agent. Follow AGENTS.md, update the relevant normative design, prefer extending existing behavior tests, run the required full quality gates before commits, and clean only task-owned resources. Never use the user's default tmux server for tests.

Completion evidence: Task 252's focused lifecycle and same-Home regressions passed (2 passed in 8.32s). The combined normal and `TERM=dumb NO_COLOR=1` full gates each passed with 1,627 passed, 14 skipped and 91.52% coverage. Logs: `/private/tmp/shikiru-252-check-normal.log` and `/private/tmp/shikiru-252-check-nocolor.log`.
