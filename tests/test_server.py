"""Handshake-level tests: every bus route has exactly one registered MCP
tool, with the expected name (via FastMCP's list_tools() introspection) --
plus direct invocation tests for each @mcp.tool async wrapper function
itself. The wrappers are thin passthroughs to bus_mcp.routes, but they were
previously untested in isolation (an /ultrareview P1 finding): an arg-name
mismatch, a dropped kwarg, or a default-value drift between server.py and
bus_mcp.config would not have been caught by list_tools() alone, since that
only inspects the registered schema, never calls the function body."""
from __future__ import annotations

import asyncio

import pytest

from bus_mcp import config, server
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


@pytest.fixture
def fake_routes(monkeypatch):
    """Monkeypatch every bus_mcp.routes function the server wrappers call,
    capturing the exact args each wrapper forwards -- proves the wrapper is a
    faithful passthrough, not just that it exists."""
    calls: dict[str, tuple] = {}

    def _make(name):
        def _fn(*args, **kwargs):
            calls[name] = (args, kwargs)
            return {"ok": True, "from": name}

        return _fn

    for name in (
        "post_message",
        "read_messages",
        "claim_lane",
        "release_lane",
        "heartbeat_lane",
        "get_bus_status",
    ):
        monkeypatch.setattr(server.routes, name, _make(name))
    return calls


def test_post_message_tool_passthrough(fake_routes):
    result = asyncio.run(server.post_message_tool("t", "s", "b", action_flag=True))
    assert result == {"ok": True, "from": "post_message"}
    assert fake_routes["post_message"] == (("t", "s", "b", True), {})


def test_read_messages_tool_passthrough(fake_routes):
    result = asyncio.run(server.read_messages_tool(topic="converge", limit=10))
    assert result == {"ok": True, "from": "read_messages"}
    assert fake_routes["read_messages"] == (("converge", 10), {})


def test_claim_lane_tool_passthrough(fake_routes):
    result = asyncio.run(server.claim_lane_tool("feeds", "session-A"))
    assert result == {"ok": True, "from": "claim_lane"}
    assert fake_routes["claim_lane"] == (("feeds", "session-A", config.DEFAULT_LEASE_S), {})


def test_claim_lane_tool_default_matches_config_default():
    import inspect

    sig = inspect.signature(server.claim_lane_tool)
    assert sig.parameters["lease_s"].default == config.DEFAULT_LEASE_S


def test_release_lane_tool_passthrough(fake_routes):
    result = asyncio.run(server.release_lane_tool("feeds", "session-A"))
    assert result == {"ok": True, "from": "release_lane"}
    assert fake_routes["release_lane"] == (("feeds", "session-A"), {})


def test_heartbeat_lane_tool_passthrough(fake_routes):
    result = asyncio.run(server.heartbeat_lane_tool("feeds", "session-A"))
    assert result == {"ok": True, "from": "heartbeat_lane"}
    assert fake_routes["heartbeat_lane"] == (("feeds", "session-A", config.DEFAULT_LEASE_S), {})


def test_heartbeat_lane_tool_default_matches_config_default():
    import inspect

    sig = inspect.signature(server.heartbeat_lane_tool)
    assert sig.parameters["lease_s"].default == config.DEFAULT_LEASE_S


def test_get_bus_status_tool_passthrough(fake_routes):
    result = asyncio.run(server.get_bus_status_tool())
    assert result == {"ok": True, "from": "get_bus_status"}
    assert fake_routes["get_bus_status"] == ((), {})
