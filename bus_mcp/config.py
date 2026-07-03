"""bus_mcp.config -- environment configuration for the bus-mcp server.

The coordination bus is a self-hosted, no-auth, localhost-only v1 service
(backend/coordination_bus.py in the alphahive repo). There is no write-gate
here (unlike github-mcp's read/write split) -- every route the bus exposes is
safe to call; v1 "executes nothing outward-facing" (action_flag is
store+display only). The only knob is *where* the bus lives.
"""
from __future__ import annotations

import os

DEFAULT_BASE_URL = "http://127.0.0.1:8100/api/bus"
DEFAULT_TIMEOUT_S = 10.0
DEFAULT_LEASE_S = 300


def get_base_url() -> str:
    """Base URL for the coordination bus API, e.g. http://127.0.0.1:8100/api/bus.
    Overridable via BUS_MCP_BASE_URL for a non-default host/port."""
    return os.environ.get("BUS_MCP_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def get_timeout_s() -> float:
    """Per-request timeout in seconds. Overridable via BUS_MCP_TIMEOUT_S."""
    raw = os.environ.get("BUS_MCP_TIMEOUT_S")
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_S


def is_live_smoke_enabled() -> bool:
    """True when BUS_MCP_LIVE=1 -- gates the real-network smoke test."""
    return os.environ.get("BUS_MCP_LIVE") == "1"
