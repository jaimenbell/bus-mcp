"""Write-gate tests: EVERY mutating route must REFUSE locally when
BUS_MCP_ENABLE_WRITE is unset (the production default), and only reach the
network when it is set.

The table below is the enforcement point, and `test_every_gated_route_is_in_
the_table` is what stops it from silently lagging the module: a new write
route that nobody added here would otherwise ship with a gate nothing ever
proved fires.

Mirrors github-mcp's tests/test_write.py structure. The `no_route` guard is
the key safety assertion: no respx route is registered, so if the gate ever
let a call fall through to the network the mock would raise -- the refusal
can't pass by coincidentally returning an ok=False-shaped dict.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from bus_mcp import config, routes

BASE = "http://127.0.0.1:8100/api/bus"

# (fn, args) for each gated write route.
WRITE_FN_ARGS = [
    (routes.post_message, ("converge", "sender", "body")),
    (routes.claim_lane, ("feeds", "session-A")),
    (routes.release_lane, ("feeds", "session-A")),
    (routes.heartbeat_lane, ("feeds", "session-A")),
    # v0.2.0 -- EVERY new write, no exceptions. A write tool absent from this
    # table is a write tool whose gate has never been shown to fire.
    (routes.open_thread, ("wave", "title", "body")),
    (routes.reply_in_thread, (2, "body")),
    (routes.resolve_thread, (2,)),
]


@pytest.fixture
def write_disabled(monkeypatch):
    """Override conftest's autouse enable: gate OFF (production default)."""
    monkeypatch.delenv("BUS_MCP_ENABLE_WRITE", raising=False)


class TestConfigGate:
    def test_write_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("BUS_MCP_ENABLE_WRITE", raising=False)
        assert config.group_enabled(config.GROUP_WRITE) is False

    def test_read_group_always_enabled(self, monkeypatch):
        monkeypatch.delenv("BUS_MCP_ENABLE_WRITE", raising=False)
        assert config.group_enabled(config.GROUP_READ) is True

    @pytest.mark.parametrize("val,expected", [
        ("1", True), ("true", True), ("yes", True), ("on", True), ("TRUE", True),
        ("0", False), ("false", False), ("", False), ("no", False),
    ])
    def test_env_truthiness(self, monkeypatch, val, expected):
        monkeypatch.setenv("BUS_MCP_ENABLE_WRITE", val)
        assert config.group_enabled(config.GROUP_WRITE) is expected


def gate_groups(fn) -> set[str]:
    """Every gate wrapped around a route function, walking the decorator
    chain outward-in."""
    groups = set()
    while fn is not None:
        group = getattr(fn, "_gate_group", None)
        if group is not None:
            groups.add(group)
        fn = getattr(fn, "__wrapped__", None)
    return groups


def test_every_gated_route_is_in_the_table():
    """PARITY. Enumerates the write-gated functions in bus_mcp.routes and
    asserts the table above covers all of them -- so adding a write route
    without adding a gate test FAILS here rather than shipping a gate that
    was never shown to fire."""
    gated = {
        name
        for name, obj in vars(routes).items()
        if callable(obj) and config.GROUP_WRITE in gate_groups(obj)
    }
    covered = {fn.__name__ for fn, _ in WRITE_FN_ARGS}
    assert gated - covered == set(), f"write routes with no gate test: {gated - covered}"


class TestGateRefusesWhenDisabled:
    @respx.mock
    @pytest.mark.parametrize("fn,args", WRITE_FN_ARGS)
    def test_refused_when_write_disabled(self, fn, args, write_disabled):
        """No respx route registered -- if the gate leaked to the network,
        respx would raise instead of the test passing on a refusal dict."""
        result = fn(*args)
        assert result["ok"] is False
        assert result["error"]["type"] == "policy_refusal"
        assert result["error"]["group"] == "write"
        assert result["error"]["required_env"] == "BUS_MCP_ENABLE_WRITE"
        assert result["error"]["tool"] == fn.__name__


class TestGatePermitsWhenEnabled:
    """conftest's autouse fixture sets BUS_MCP_ENABLE_WRITE=1, so these prove
    the gate lets a real call through to the (mocked) network when armed."""

    @respx.mock
    def test_post_message_reaches_network(self):
        route = respx.post(f"{BASE}/message").mock(
            return_value=httpx.Response(200, json={"id": 1})
        )
        result = routes.post_message("converge", "a", "hi")
        assert result["ok"] is True
        assert route.called

    @respx.mock
    def test_claim_lane_reaches_network(self):
        route = respx.post(f"{BASE}/lanes/feeds/claim").mock(
            return_value=httpx.Response(200, json={"ok": True, "decision": "claim"})
        )
        result = routes.claim_lane("feeds", "session-A")
        assert result["ok"] is True
        assert route.called

    @respx.mock
    def test_release_lane_reaches_network(self):
        route = respx.post(f"{BASE}/lanes/feeds/release").mock(
            return_value=httpx.Response(200, json={"ok": True, "decision": "released"})
        )
        result = routes.release_lane("feeds", "session-A")
        assert result["ok"] is True
        assert route.called

    @respx.mock
    def test_heartbeat_lane_reaches_network(self):
        route = respx.post(f"{BASE}/lanes/feeds/heartbeat").mock(
            return_value=httpx.Response(200, json={"ok": True, "decision": "heartbeat"})
        )
        result = routes.heartbeat_lane("feeds", "session-A")
        assert result["ok"] is True
        assert route.called


class TestReadRoutesNeverGated:
    """read_messages / get_bus_status must work even with the write gate OFF."""

    @respx.mock
    def test_read_messages_works_with_write_disabled(self, write_disabled):
        respx.get(f"{BASE}/messages").mock(
            return_value=httpx.Response(200, json={"messages": []})
        )
        result = routes.read_messages()
        assert result["ok"] is True

    @respx.mock
    def test_get_bus_status_works_with_write_disabled(self, write_disabled):
        respx.get(f"{BASE}/status").mock(
            return_value=httpx.Response(200, json={"active_lanes": []})
        )
        result = routes.get_bus_status()
        assert result["ok"] is True


class TestGateEnforcedThroughServerWrapper:
    """The @mcp.tool async wrappers call routes.* -- prove the gate holds
    end-to-end through the tool wrapper, not just the bare route function."""

    def test_post_message_tool_refuses_when_disabled(self, monkeypatch):
        import asyncio
        from bus_mcp import server
        monkeypatch.delenv("BUS_MCP_ENABLE_WRITE", raising=False)
        result = asyncio.run(server.post_message_tool("t", "s", "b"))
        assert result["ok"] is False
        assert result["error"]["type"] == "policy_refusal"
