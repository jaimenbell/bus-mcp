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

# The v0.1.1 six -- the lane/message/status surface.
V1_TOOLS = {
    "post_message",
    "read_messages",
    "claim_lane",
    "release_lane",
    "heartbeat_lane",
    "get_bus_status",
}

# v0.2.0 -- threads, validations, dispatches, board, worker, events.
V2_TOOLS = {
    "list_threads",
    "get_thread",
    "open_thread",
    "reply_in_thread",
    "resolve_thread",
    "list_validations",
    "get_validation",
    "request_validation",
    "vote",
    "list_dispatches",
    "mint_dispatch",
    "report_dispatch",
}

EXPECTED_TOOLS = V1_TOOLS | V2_TOOLS


def _tool_names() -> set[str]:
    tools = asyncio.run(mcp.list_tools())
    return {t.name for t in tools}


def test_all_expected_bus_tools_registered():
    assert _tool_names() == EXPECTED_TOOLS


def test_no_unexpected_extra_tools():
    """An exact-set assertion, not a floor: a tool that appears here without
    a row in the README table and a line in this set is an undocumented
    surface on a public server."""
    assert _tool_names() - EXPECTED_TOOLS == set()
    assert EXPECTED_TOOLS - _tool_names() == set()


def test_the_original_six_are_still_registered_unrenamed():
    """v0.2.0 is ADDITIVE. Every pre-existing caller keeps its tool."""
    assert V1_TOOLS <= _tool_names()


def _tool_descriptions() -> dict[str, str]:
    tools = asyncio.run(mcp.list_tools())
    return {t.name: (t.description or "") for t in tools}


def test_claim_and_heartbeat_descriptions_mention_lease_s_clamping():
    # coordination-bus v1.3+ may grant a shorter lease than requested; an
    # agent reading the tool description alone (never the source) needs to
    # know its actual grant can be clamped and that lease_s in the response
    # is the effective value to check.
    descriptions = _tool_descriptions()
    for name in ("claim_lane", "heartbeat_lane"):
        assert "lease_s" in descriptions[name]
        assert "clamp" in descriptions[name].lower()


def test_get_bus_status_description_mentions_max_lease_seconds():
    descriptions = _tool_descriptions()
    assert "max_lease_seconds" in descriptions["get_bus_status"]


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

    for name in sorted(EXPECTED_TOOLS):
        monkeypatch.setattr(server.routes, name, _make(name))
    return calls


def test_post_message_tool_passthrough(fake_routes):
    result = asyncio.run(server.post_message_tool("t", "s", "b", action_flag=True))
    assert result == {"ok": True, "from": "post_message"}
    assert fake_routes["post_message"] == (("t", "s", "b", True, None, None, None), {})


def test_post_message_tool_forwards_the_addressing_fields(fake_routes):
    asyncio.run(
        server.post_message_tool(
            "t", "s", "b", action_flag=False, thread_id=2, reply_to=7, recipient="operator"
        )
    )
    assert fake_routes["post_message"] == (("t", "s", "b", False, 2, 7, "operator"), {})


def test_read_messages_tool_passthrough(fake_routes):
    result = asyncio.run(server.read_messages_tool(topic="converge", limit=10))
    assert result == {"ok": True, "from": "read_messages"}
    assert fake_routes["read_messages"] == (("converge", 10, None, None, None), {})


def test_read_messages_tool_forwards_the_new_filters(fake_routes):
    asyncio.run(
        server.read_messages_tool(thread_id=2, recipient="orchestrator", since_id=11)
    )
    assert fake_routes["read_messages"] == ((None, 50, 2, "orchestrator", 11), {})


def test_list_threads_tool_passthrough(fake_routes):
    assert asyncio.run(server.list_threads_tool("open", 5)) == {
        "ok": True, "from": "list_threads"}
    assert fake_routes["list_threads"] == (("open", 5), {})


def test_get_thread_tool_passthrough(fake_routes):
    assert asyncio.run(server.get_thread_tool(2)) == {"ok": True, "from": "get_thread"}
    assert fake_routes["get_thread"] == ((2, 500), {})


def test_open_thread_tool_passthrough(fake_routes):
    asyncio.run(server.open_thread_tool("t", "ti", "b", kind="DECIDE"))
    assert fake_routes["open_thread"] == (("t", "ti", "b", "DECIDE", None, None, False), {})


def test_reply_in_thread_tool_passthrough(fake_routes):
    asyncio.run(server.reply_in_thread_tool(2, "body", reply_to=9))
    assert fake_routes["reply_in_thread"] == ((2, "body", 9, None, False, None, None), {})


def test_resolve_thread_tool_passthrough(fake_routes):
    asyncio.run(server.resolve_thread_tool(2, resolved_by="lane:a", note="n"))
    assert fake_routes["resolve_thread"] == ((2, "lane:a", "n"), {})


def test_list_validations_tool_passthrough(fake_routes):
    asyncio.run(server.list_validations_tool(10, "message:1", "pending", 2))
    assert fake_routes["list_validations"] == ((10, "message:1", "pending", 2), {})


def test_get_validation_tool_passthrough(fake_routes):
    asyncio.run(server.get_validation_tool(5))
    assert fake_routes["get_validation"] == ((5,), {})


def test_request_validation_tool_passthrough(fake_routes):
    asyncio.run(server.request_validation_tool("message:1", "ref"))
    assert fake_routes["request_validation"] == (("message:1", "ref", None, None), {})


def test_vote_tool_passthrough(fake_routes):
    asyncio.run(server.vote_tool(5, "a1b2c3d4e5f6", "confirmed", "ref"))
    assert fake_routes["vote"] == ((5, "a1b2c3d4e5f6", "confirmed", "ref", None), {})


def test_list_dispatches_tool_passthrough(fake_routes):
    asyncio.run(server.list_dispatches_tool("open", 10))
    assert fake_routes["list_dispatches"] == (("open", 10), {})


def test_mint_dispatch_tool_passthrough(fake_routes):
    asyncio.run(server.mint_dispatch_tool("lane", "repo", "purpose"))
    assert fake_routes["mint_dispatch"] == (("lane", "repo", "purpose", None), {})


def test_report_dispatch_tool_passthrough(fake_routes):
    asyncio.run(server.report_dispatch_tool("a1b2c3d4e5f6", "ref"))
    assert fake_routes["report_dispatch"] == (("a1b2c3d4e5f6", "ref"), {})


def test_read_messages_description_states_the_ignored_filters():
    """The limitation must be visible to a caller that never opens the
    source: today's backend ignores thread_id/recipient/since_id."""
    description = _tool_descriptions()["read_messages"]
    assert "IGNORES" in description
    for name in ("thread_id", "recipient", "since_id"):
        assert name in description


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
