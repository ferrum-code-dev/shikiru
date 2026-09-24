"""`fake`: a scripted `InteractiveAgent` plugin, test-only, for Shikiru's contract test kit.

`fake_agent.agent.create` registers an `InteractiveAgent` named `fake` under the
`shikiru.agents` entry point. `launch`/`attach` respawn a pane's process into
`fake-agent-repl`, a line-reading REPL: one line of injected or typed text is one prompt,
and one prompt is one turn.

The REPL emits its own hook lines through `shikiru.agents.sdk.hooks.HookRecorder`, into
`$SHIKIRU_RUN_DIR/$SHIKIRU_ROLE/hooks.jsonl`, exactly where a real plugin's hooks would land
(04 §2). Each line is a compact JSON object shaped `{"event": <name>, ...fields}`; the event
names and fields are `fake_agent.signals`' contract, read back by `fake_agent.session` to
produce `AgentSignal`s.

**`SHIKIRU_FAKE_SCRIPT`** scripts the REPL's behaviour, one entry per turn, consumed in
order; once exhausted, every further turn behaves as `"accept"`. It is a JSON array whose
entries are each either a bare string, for a no-argument behaviour, or an object
`{"kind": <name>, ...}` for one that takes arguments. The behaviours (07 §3):

- `"accept"`: submits a `shikiru report` that ends the turn successfully, for whichever
  `report.kind` the current turn dir names. A commit is made first, for report kinds that
  carry `commits`. `{"kind": "accept", "uncommitted": "<path>"}` also writes `<path>` in the
  workspace and leaves it uncommitted.
- `"changes_requested"`: only valid when `report.kind` is `verdict`. Submits a
  `changes_requested` verdict with one issue.
- `"needs_decision"`: only valid when `report.kind` is `implementation`. Submits a
  `needs_decision` report. Add `{"kind": "needs_decision", "question": "..."}` to set the
  question; otherwise one is derived from the prompt.
- `"no_report"`: ends the turn without ever calling `shikiru report`.
- `{"kind": "fail", "failure_kind": <name>, "resets_at": "<iso8601>"}`: emits `TurnFailed`
  directly. `failure_kind` names a `FailureKind` member; `resets_at` is optional.
- `"crash"`: exits the REPL process non-zero without emitting anything further, simulating
  a dead pane.
- `"stall"`: reads the prompt but never responds to it: no report, no signal, ever.
- `{"kind": "dialog", "message": "..."}`: emits `AttentionNeeded` and leaves the turn open;
  the next prompt received starts a new turn as usual.

Any entry whose `"kind"` is not one of the above is rejected loudly at REPL start.

**Headless `ask()`** is scripted separately, through the agent's own options (not
`SHIKIRU_FAKE_SCRIPT`, since a headless call has no pane or session):
`agents.fake.options = {"asks": [<json object>, ...]}` in `Settings`, consumed in order,
one per `ask()` call.

**Resuming**: the REPL keeps every prompt it has ever received for its session id under
`$SHIKIRU_RUN_DIR/$SHIKIRU_ROLE/fake_state/<session_id>.json`, which a relaunch under the
same session id (`attach`) can still read.
`shikiru.agents.testing.resume.RESUME_RECALL_PROMPT` sent as a prompt is answered,
regardless of the script, with the first prompt ever recorded for that session id, which is
how `AgentContractSuite`'s resume check works generically for both this fake and a
genuinely conversational real agent.
"""

from __future__ import annotations
