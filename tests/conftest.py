"""Shared fixtures for bus-mcp tests. All HTTP-layer tests mock the bus via
respx (see test_client.py / test_routes.py) -- nothing here talks to a real
bus. BUS_MCP_BASE_URL is pinned to a fixed test URL so respx routes match
deterministically regardless of the developer's real env."""
from __future__ import annotations

import pytest
import respx

TEST_BASE_URL = "http://127.0.0.1:8100/api/bus"


@pytest.fixture(autouse=True)
def _pin_base_url(monkeypatch):
    monkeypatch.setenv("BUS_MCP_BASE_URL", TEST_BASE_URL)
    monkeypatch.delenv("BUS_MCP_TIMEOUT_S", raising=False)
    yield


@pytest.fixture(autouse=True)
def _block_unmocked_network(request):
    """NO TEST MAY REACH A REAL BUS. Discovered the hard way while running a
    positive control: a gate test that forgot its own `@respx.mock` posted a
    task claim to the LIVE backend on 127.0.0.1:8100 and passed on its 401.
    A per-test decorator is opt-in, and the tests that most need the guard --
    the refusal tests, which assert a call does NOT happen -- are exactly the
    ones where forgetting it is invisible.

    So the guard is autouse. An unmocked request raises AllMockedAssertionError
    instead of leaving the process; test-level `respx.mock` routers nest
    inside this one and take precedence.

    Two exemptions, both MARKED rather than name-matched: `live` (a
    real-network smoke test reaching the network is the entire point) and
    `no_respx` (a test whose subject is httpx's OWN url handling -- respx
    intercepts before httpx can raise InvalidURL, so mocking it would hide
    the very behaviour under test)."""
    if "live" in request.keywords or "no_respx" in request.keywords:
        yield
        return
    with respx.mock:
        yield


@pytest.fixture(autouse=True)
def _enable_write_gate_by_default(monkeypatch):
    """The write gate (BUS_MCP_ENABLE_WRITE) is OFF by default in production,
    but the existing route/server passthrough tests exercise the *network*
    behavior of the 4 write tools -- so enable the gate for them by default.
    Tests that specifically prove the gate's OFF/refusal behavior override
    this by deleting the env var (see test_write_gate.py)."""
    monkeypatch.setenv("BUS_MCP_ENABLE_WRITE", "1")
    yield
