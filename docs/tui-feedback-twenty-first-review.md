# Task switching, rung selection and engine-start follow-up

Status: in progress (Tasks 250–252). The working tool is the priority; HTML remains illustrative.

- Switch task initially loads 20 tasks. Each explicit load-more action appends the next 20, retaining selection and the active search. Use a smaller popup with a bounded task viewport rather than filling a tall terminal.
- Diagnose why changing a rung while escalated changes the pane title without changing the session's actual model. Verify effective model/effort at the agent process boundary, preserve session history where supported, and distinguish next-turn configuration from an already running interactive session. The session-route question has not received an answer, so the working reproduction is a rung change while escalated followed by Take over, with the next automated resume checked too. Changes take effect at the next turn or interactive-resume boundary; an active interactive process keeps its effective settings until then, and its title must stay honest.
- Start engine closes its action popup after successful startup, returning to the caller as Stop does. Keep startup failures visible and retryable; preserve duplicate-start protection and the existing dashboard/client.

Execute Tasks 250, 251 and 252 sequentially, each through a GPT-6-Luna agent at xhigh effort. Review each completed candidate and send fixes to the same agent. Follow AGENTS.md, update the relevant normative design, prefer extending existing behavior tests, run the required full quality gates before commits, and clean only task-owned resources. Never use the user's default tmux server for tests.
