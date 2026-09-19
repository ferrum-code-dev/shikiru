"""The CLI's thin adapter for the injected MCP process entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from shikiru.bootstrap import OperatorServices


def run_mcp(server: FastMCP, services: OperatorServices) -> None:
    """Run an injected FastMCP server and close its operator services."""
    try:
        server.run(transport="stdio")
    finally:
        services.close()
