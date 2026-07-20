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

# --- Write-tool gate (defense-in-depth, added 2026-07-19) -----------------
# The 4 mutating tools (post_message / claim_lane / release_lane /
# heartbeat_lane) are OFF by default and refuse locally unless
# BUS_MCP_ENABLE_WRITE is truthy in the server's environment -- mirroring
# github-mcp's GITHUB_MCP_ENABLE_WRITE / desktop-mcp's DESKTOP_MCP_ENABLE_*
# pattern. This is SEPARATE from BUS_WRITE_SECRET: the secret authenticates a
# write against the *backend bus* over the wire; this gate governs whether
# THIS MCP server will attempt a write at all. Read tools (read_messages /
# get_bus_status) are never gated, by design.
GROUP_READ = "read"
GROUP_WRITE = "write"

_ENV_GATES = {
    GROUP_WRITE: "BUS_MCP_ENABLE_WRITE",
}


def _env_truthy(name: str) -> bool:
    val = os.environ.get(name, "")
    return val.strip().lower() in ("1", "true", "yes", "on")


def group_enabled(group: str) -> bool:
    """read is always on; write requires its env gate (default OFF)."""
    if group == GROUP_READ:
        return True
    env_name = _ENV_GATES.get(group)
    if env_name is None:
        return False
    return _env_truthy(env_name)


def policy_refusal(group: str, tool: str) -> dict:
    """Structured refusal payload for a disabled tool group -- same shape as
    routes.py's other error dicts ({"ok": False, "error": {...}})."""
    env_name = _ENV_GATES.get(group, f"BUS_MCP_ENABLE_{group.upper()}")
    return {
        "ok": False,
        "error": {
            "type": "policy_refusal",
            "message": (
                f"Tool group '{group}' is disabled. Set {env_name}=1 in the "
                f"server's environment to enable it."
            ),
            "group": group,
            "tool": tool,
            "required_env": env_name,
        },
    }


def check_group(group: str, tool: str) -> dict | None:
    """Gate check for a tool call. Returns a structured refusal dict if the
    group is disabled, else None (caller proceeds)."""
    if not group_enabled(group):
        return policy_refusal(group, tool)
    return None


def gated_write(fn):
    """Decorator applied directly to the write-group route functions so the
    policy gate is enforced at the source -- not just in the MCP tool wrapper
    -- and is unit-testable without spinning up fastmcp or hitting the
    network. Reads the env on every call (not import-time) so an operator can
    arm/disarm and tests can monkeypatch without a code change."""

    def wrapper(*args, **kwargs):
        refusal = check_group(GROUP_WRITE, fn.__name__)
        if refusal is not None:
            return refusal
        return fn(*args, **kwargs)

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    wrapper.__wrapped__ = fn
    return wrapper


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
