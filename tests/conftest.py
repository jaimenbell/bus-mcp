"""Shared fixtures for bus-mcp tests. All HTTP-layer tests mock the bus via
respx (see test_client.py / test_routes.py) -- nothing here talks to a real
bus. BUS_MCP_BASE_URL is pinned to a fixed test URL so respx routes match
deterministically regardless of the developer's real env."""
from __future__ import annotations

import pytest

TEST_BASE_URL = "http://127.0.0.1:8100/api/bus"


@pytest.fixture(autouse=True)
def _pin_base_url(monkeypatch):
    monkeypatch.setenv("BUS_MCP_BASE_URL", TEST_BASE_URL)
    monkeypatch.delenv("BUS_MCP_TIMEOUT_S", raising=False)
    yield
