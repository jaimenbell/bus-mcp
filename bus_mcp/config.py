"""bus_mcp.config -- environment configuration for the bus-mcp server.

The coordination bus is a self-hosted, localhost-only v1 service
(backend/coordination_bus.py in the alphahive repo). v1 "executes nothing
outward-facing" (action_flag is store+display only) -- every route is safe to
call. As of coordination-bus v1.1, the 4 write routes MAY be gated behind an
optional shared secret (BUS_WRITE_SECRET, header X-Bus-Secret); this client
mirrors that same env var so arming the bus and arming this MCP is one env
var set in both processes, not a bus-mcp-specific config surface. Unset (the
default) means the bus is unarmed -- this client sends no header, matching
the bus's own default-open behavior byte-for-byte.
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


def get_write_secret() -> str | None:
    """Reads BUS_WRITE_SECRET from the environment on every call (not cached),
    mirroring the bus server's own per-request read -- so tests can
    monkeypatch it and an operator can arm/disarm without a code change.
    Empty string counts as unset. None means: send no X-Bus-Secret header,
    matching the bus's own default-open behavior."""
    return os.environ.get("BUS_WRITE_SECRET") or None
