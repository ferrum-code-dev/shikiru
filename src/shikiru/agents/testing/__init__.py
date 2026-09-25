"""The agent plugin contract test kit (04 §3), imported only by tests (02 §2)."""

from __future__ import annotations

from shikiru.agents.testing.contract_suite import (
    AgentContractSuite,
    InvocationCase,
    RecordedStream,
    assert_agent_obligations,
)
from shikiru.agents.testing.legacy_contract_suite import (
    LaunchCase,
    LegacyAgentContractSuite,
    RecordingPaneHandle,
    RespawnCall,
    SampleCase,
    TmuxSocket,
)
from shikiru.agents.testing.resume import RESUME_NONCE_PROMPT, RESUME_RECALL_PROMPT

__all__ = [
    "RESUME_NONCE_PROMPT",
    "RESUME_RECALL_PROMPT",
    "AgentContractSuite",
    "InvocationCase",
    "LaunchCase",
    "LegacyAgentContractSuite",
    "RecordedStream",
    "RecordingPaneHandle",
    "RespawnCall",
    "SampleCase",
    "TmuxSocket",
    "assert_agent_obligations",
]
