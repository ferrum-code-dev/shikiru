"""The resume-recall protocol exercised by `AgentContractSuite`'s live check 4.

A plugin whose agent has no memory of its own (only the fake agent needs this) must answer
`RESUME_RECALL_PROMPT` with a message that contains whatever nonce it was given via
`RESUME_NONCE_PROMPT` earlier in the *same* session, recovered from state tied to that
session's id rather than to the pane or run directory alone. A real, conversational agent
needs no special handling: it answers from genuine memory.
"""

from __future__ import annotations

RESUME_NONCE_PROMPT = "Remember this nonce for later in this session: {nonce}"
RESUME_RECALL_PROMPT = (
    "What nonce did I ask you to remember earlier in this session? Reply with only the nonce."
)
