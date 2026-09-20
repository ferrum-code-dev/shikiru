from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from shikiru.bootstrap import build_operator
from shikiru.config.loader import load_settings
from shikiru.mcp.server import build_server
from tests.support.config import write_minimal_project

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
