"""Identity tests: BUS_MCP_AGENT_ID, its import-time default, and the
`agent_id` echo every tool result carries.

The echo is the point. The bus authenticates NOBODY on `sender`/`owner`/
`opened_by` -- it shape-checks a string -- so what this client owes a caller
is not proof of identity but a straight answer to "what did you put on the
wire as me". These tests pin that answer to the same value the write actually
carried, including on the error and refusal paths, where a caller most needs
to know which identity was rejected.
"""
from __future__ import annotations

import os
import re

import httpx
import pytest
import respx

from bus_mcp import config, routes

BASE = "http://127.0.0.1:8100/api/bus"


class TestAgentIdDefault:
    def test_default_is_session_host_pid_shaped(self, monkeypatch):
        monkeypatch.delenv("BUS_MCP_AGENT_ID", raising=False)
        agent_id = config.get_agent_id()
        assert agent_id.startswith("session:")
        assert agent_id.endswith(f":{os.getpid()}")

    def test_default_computed_once_at_import(self, monkeypatch):
        """The default is a module constant, not recomputed per call -- a
        stable identity for the life of the server process."""
        monkeypatch.delenv("BUS_MCP_AGENT_ID", raising=False)
        assert config.get_agent_id() == config.DEFAULT_AGENT_ID
        assert config.get_agent_id() == config.get_agent_id()

    def test_default_matches_the_bus_role_shape(self, monkeypatch):
        """The bus's own `_ROLE_RE`: printable ASCII, no whitespace, 1-64
        chars. `opened_by` IS role-validated server-side, so a hostname with a
        space would 422 the thread routes with no hint why."""
        monkeypatch.delenv("BUS_MCP_AGENT_ID", raising=False)
        assert re.fullmatch(r"[!-~]{1,64}", config.get_agent_id())

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("BUS_MCP_AGENT_ID", "lane:bus-mcp-tools")
        assert config.get_agent_id() == "lane:bus-mcp-tools"

    def test_blank_override_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("BUS_MCP_AGENT_ID", "   ")
        assert config.get_agent_id() == config.DEFAULT_AGENT_ID

    def test_override_is_sanitized_to_role_shape(self, monkeypatch):
        monkeypatch.setenv("BUS_MCP_AGENT_ID", "lane with spaces")
        assert config.get_agent_id() == "lane-with-spaces"

    def test_override_is_truncated_to_64_chars(self, monkeypatch):
        monkeypatch.setenv("BUS_MCP_AGENT_ID", "x" * 200)
        assert config.get_agent_id() == "x" * 64


class TestAgentIdEcho:
    @respx.mock
    def test_success_payload_carries_agent_id(self, monkeypatch):
        monkeypatch.setenv("BUS_MCP_AGENT_ID", "lane:echo")
        respx.get(f"{BASE}/status").mock(return_value=httpx.Response(200, json={"a": 1}))
        assert routes.get_bus_status()["agent_id"] == "lane:echo"

    @respx.mock
    def test_bus_field_named_agent_id_cannot_overwrite_the_echo(self, monkeypatch):
        """`agent_id` is merged LAST on purpose: the field answers 'who did
        THIS process claim to be', and a bus payload must not be able to
        answer it for us."""
        monkeypatch.setenv("BUS_MCP_AGENT_ID", "lane:echo")
        respx.get(f"{BASE}/status").mock(
            return_value=httpx.Response(200, json={"agent_id": "somebody-else"})
        )
        assert routes.get_bus_status()["agent_id"] == "lane:echo"

    @respx.mock
    def test_api_error_payload_carries_agent_id(self, monkeypatch):
        monkeypatch.setenv("BUS_MCP_AGENT_ID", "lane:echo")
        respx.post(f"{BASE}/message").mock(
            return_value=httpx.Response(401, json={"detail": "bad secret"})
        )
        result = routes.post_message("t", "s", "b")
        assert result["ok"] is False
        assert result["agent_id"] == "lane:echo"

    @respx.mock
    def test_unreachable_payload_carries_agent_id(self, monkeypatch):
        monkeypatch.setenv("BUS_MCP_AGENT_ID", "lane:echo")
        respx.get(f"{BASE}/status").mock(side_effect=httpx.ConnectError("refused"))
        result = routes.get_bus_status()
        assert result["error"]["type"] == "bus_unreachable"
        assert result["agent_id"] == "lane:echo"

    @respx.mock
    def test_policy_refusal_carries_agent_id(self, monkeypatch):
        monkeypatch.delenv("BUS_MCP_ENABLE_WRITE", raising=False)
        monkeypatch.setenv("BUS_MCP_AGENT_ID", "lane:echo")
        result = routes.post_message("t", "s", "b")
        assert result["error"]["type"] == "policy_refusal"
        assert result["agent_id"] == "lane:echo"

    @respx.mock
    def test_invalid_lane_refusal_carries_agent_id(self, monkeypatch):
        monkeypatch.setenv("BUS_MCP_AGENT_ID", "lane:echo")
        result = routes.claim_lane("../etc", "owner")
        assert result["error"]["type"] == "invalid_lane"
        assert result["agent_id"] == "lane:echo"


class TestAgentIdIsTheWriteDefault:
    """The identity is not decoration: it is what lands in the row when the
    caller omits the field."""

    @respx.mock
    @pytest.mark.parametrize(
        "call,field",
        [
            (lambda: routes.open_thread("t", "ti", "b"), "opened_by"),
        ],
    )
    def test_omitted_identity_field_defaults_to_agent_id(self, monkeypatch, call, field):
        monkeypatch.setenv("BUS_MCP_AGENT_ID", "lane:default-me")
        route = respx.post(f"{BASE}/threads").mock(
            return_value=httpx.Response(200, json={"ok": True, "thread_id": 1})
        )
        call()
        import json as _json

        sent = _json.loads(route.calls.last.request.content)
        assert sent[field] == "lane:default-me"

    @respx.mock
    def test_explicit_identity_is_not_overridden(self, monkeypatch):
        monkeypatch.setenv("BUS_MCP_AGENT_ID", "lane:default-me")
        route = respx.post(f"{BASE}/threads").mock(
            return_value=httpx.Response(200, json={"ok": True, "thread_id": 1})
        )
        routes.open_thread("t", "ti", "b", opened_by="orchestrator")
        import json as _json

        sent = _json.loads(route.calls.last.request.content)
        assert sent["opened_by"] == "orchestrator"
