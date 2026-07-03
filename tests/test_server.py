"""Handshake-level tests: every bus route has exactly one registered MCP
tool, with the expected name. No network involved -- pure introspection via
FastMCP's list_tools()."""
from __future__ import annotations

import asyncio

from bus_mcp.server import mcp

EXPECTED_TOOLS = {
    "post_message",
    "read_messages",
    "claim_lane",
    "release_lane",
    "heartbeat_lane",
    "get_bus_status",
}


def _tool_names() -> set[str]:
    tools = asyncio.run(mcp.list_tools())
    return {t.name for t in tools}


def test_all_six_bus_tools_registered():
    assert _tool_names() == EXPECTED_TOOLS


def test_no_unexpected_extra_tools():
    assert len(_tool_names()) == 6
