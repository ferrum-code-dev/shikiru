from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from shikiru.bootstrap import build_operator
from shikiru.clock import SystemClock
from shikiru.config.loader import load_settings
from shikiru.domain.commands import ResolutionKind
from shikiru.domain.tasks import EscalationReason
from shikiru.mcp.server import build_server
from shikiru.paths import state_db_path
from shikiru.store.database import Database
from tests.support.config import write_minimal_project
from tests.support.escalations import seed_escalation
from tests.support.store import seed_task

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio


async def test_mcp_in_memory_client_reads_and_mutates_operator_state(tmp_path: Path) -> None:
    project_root = write_minimal_project(tmp_path)
    services = build_operator(project_root, load_settings(project_root))
    server = build_server(services)
    try:
        async with create_connected_server_and_client_session(server) as client:
            status = await client.call_tool("status", {})
            assert status.isError is False
            assert status.structuredContent is not None
            assert status.structuredContent["engine_up"] is False

            submitted = await client.call_tool("submit_task", {"request": "MCP request"})
            assert submitted.isError is False
            assert submitted.structuredContent is not None
            assert submitted.structuredContent["status"] == "queued"
            assert submitted.structuredContent["result"]["task_id"] == "T-1"

            events = await client.call_tool(
                "wait_for_events", {"since_id": 0, "task_id": "T-1", "timeout_s": 0}
            )
            assert events.isError is False
            assert events.structuredContent is not None
            assert events.structuredContent == {"result": []}
    finally:
        services.close()


async def test_mcp_escalation_tools_return_agent_options_and_resolutions_as_json(
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    services = build_operator(project_root, load_settings(project_root))
    connection = Database(state_db_path(project_root)).connect()
    try:
        clock = SystemClock()
        task = seed_task(connection, clock, title="decide")
        escalation, _ = seed_escalation(
            connection,
            clock,
            task.id,
            project_root,
            EscalationReason.DECISION_REQUIRED,
        )
        async with create_connected_server_and_client_session(build_server(services)) as client:
            single = await client.call_tool("get_escalation", {"escalation_id": escalation.id})
            listed = await client.call_tool("list_escalations", {})
    finally:
        connection.close()
        services.close()

    assert single.isError is False
    assert single.structuredContent is not None
    view = single.structuredContent["result"]
    assert view["question"] == "Which behavior should remain?"
    assert [option["label"] for option in view["agent_options"]] == [
        "Keep existing behavior",
        "Change behavior",
    ]
    assert all(
        set(option) == {"label", "consequences", "recommended", "needs_input"}
        for option in view["agent_options"]
    )
    assert [option["recommended"] for option in view["agent_options"]] == [True, False]
    assert all(set(item) == {"kind", "consequences", "recommended"} for item in view["resolutions"])
    assert [item["kind"] for item in view["resolutions"]] == [
        ResolutionKind.ANSWER.value,
        ResolutionKind.CANCEL.value,
    ]
    assert [item["recommended"] for item in view["resolutions"]] == [True, False]
    assert listed.isError is False
    assert listed.structuredContent is not None
    assert listed.structuredContent["result"] == [view]
