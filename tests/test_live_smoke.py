"""Real-network smoke test against a running coordination bus. Skipped
unless BUS_MCP_LIVE=1. As of 2026-07-02 the bus routes are dormant/404 on the
live :8100 AlphaHive backend until the operator restarts it -- so this test
is EXPECTED to skip (or fail-gated) until that restart happens. That is
correct, by design; do not make the bus live to force this test green.

Read-only: calls get_bus_status only. No write smoke exists here -- claim/
release/heartbeat/post_message are exercised only via respx mocks (see
test_client.py / test_routes.py); this project never mutates a real bus lane
or blackboard as part of its own test run.
"""
from __future__ import annotations

import os

import pytest

from bus_mcp import routes

LIVE = os.environ.get("BUS_MCP_LIVE") == "1"
pytestmark = pytest.mark.skipif(
    not LIVE, reason="set BUS_MCP_LIVE=1 to run the real-network bus smoke test"
)


@pytest.mark.live
def test_live_get_bus_status_returns_rollup():
    result = routes.get_bus_status()
    assert result["ok"] is True, result
    assert "_meta" in result
    assert "active_lane_count" in result["_meta"]
