"""The agent plugin contract test kit (04 §3), imported only by tests (02 §2)."""

from __future__ import annotations

from shikiru.agents.testing.contract_suite import (
    AgentContractSuite,
    LaunchCase,
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
    "LaunchCase",
    "RecordingPaneHandle",
    "RespawnCall",
    "SampleCase",
    "TmuxSocket",
]
