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
import re
import socket

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
# --- Task-claim gate (added 2026-09-10, v0.2.0) ---------------------------
# A SECOND, NARROWER gate stacked UNDER the write gate for the three task
# mutating tools (claim_task / heartbeat_task / finish_task). It is not a
# duplicate of BUS_MCP_ENABLE_WRITE: the write gate asks "may this MCP write
# to the bus at all", this one asks "is an MCP-driven Claude session a
# registered CLAIMANT of board work". Today it is not -- the work-queue class
# table names exactly one claimant class (the standing overnight task worker),
# and adding a second is an operator re-sign, not a code change. So the tools
# ship DARK: wired, tested, and refusing by default. Nothing to roll back if
# the answer stays no.
GROUP_TASK_CLAIM = "task_claim"

_ENV_GATES = {
    GROUP_WRITE: "BUS_MCP_ENABLE_WRITE",
    GROUP_TASK_CLAIM: "BUS_MCP_ENABLE_TASK_CLAIM",
}

# Why a group is off, when the reason is more than "you did not set the var".
_GATE_REASONS = {
    GROUP_TASK_CLAIM: (
        "claimant class not yet signed -- the work-queue class table names one "
        "claimant (the standing task worker); adding MCP sessions as a second "
        "claimant class is an operator decision, not a config default"
    ),
}


# --- Self-asserted agent identity (added 2026-09-10, v0.2.0) --------------
# BUS_MCP_AGENT_ID is the default `sender` / `owner` / `opened_by` /
# `requested_by` / `voter` / `minted_by` / `resolved_by` when a caller omits
# one, and every tool result echoes the value actually used under `agent_id`.
#
# THIS IS NOT AUTHENTICATION, and the echo is the honest part: the bus
# validates the SHAPE of a role string and nothing else, so any holder of the
# write secret can assert any identity. What this buys is CONSISTENCY -- two
# MCP sessions on one box stop being indistinguishable in the log -- and a
# result field that says which string this process actually put on the wire,
# so a caller never has to guess what it will be attributed as.
#
# The default is computed ONCE at import (a stable id for the life of the
# server process); the env override is read per call so tests and an operator
# can set it without a restart.
_ROLE_SAFE_RE = re.compile(r"[^!-~]")
_MAX_ROLE_LEN = 64


def _sanitize_agent_id(raw: str) -> str:
    """Coerce to the bus's own role shape (printable ASCII, no whitespace,
    1-64 chars -- `_ROLE_RE` in coordination_bus.py). A hostname with a space
    or a non-ASCII byte would otherwise 422 on the thread routes, where
    `opened_by` IS role-validated, and the caller would have no idea why."""
    cleaned = _ROLE_SAFE_RE.sub("-", raw)[:_MAX_ROLE_LEN]
    return cleaned or "session:unknown"


def _default_agent_id() -> str:
    try:
        host = socket.gethostname() or "unknown"
    except OSError:  # pragma: no cover - gethostname failing is a platform edge
        host = "unknown"
    return _sanitize_agent_id(f"session:{host}:{os.getpid()}")


DEFAULT_AGENT_ID = _default_agent_id()


def get_agent_id() -> str:
    """The identity this process asserts on bus writes. `BUS_MCP_AGENT_ID`
    wins when set and non-blank; otherwise the import-time
    `session:<hostname>:<pid>` default. Always role-shaped."""
    override = os.environ.get("BUS_MCP_AGENT_ID", "").strip()
    return _sanitize_agent_id(override) if override else DEFAULT_AGENT_ID


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
    reason = _GATE_REASONS.get(group)
    message = (
        f"Tool group '{group}' is disabled. Set {env_name}=1 in the "
        f"server's environment to enable it."
    )
    if reason:
        message = f"{message} Reason: {reason}."
    error = {
        "type": "policy_refusal",
        "message": message,
        "group": group,
        "tool": tool,
        "required_env": env_name,
    }
    if reason:
        error["reason"] = reason
    return {"ok": False, "error": error, "agent_id": get_agent_id()}


def check_group(group: str, tool: str) -> dict | None:
    """Gate check for a tool call. Returns a structured refusal dict if the
    group is disabled, else None (caller proceeds)."""
    if not group_enabled(group):
        return policy_refusal(group, tool)
    return None


def gated(group: str):
    """Decorator FACTORY: gate a route function on one tool group. Applied
    directly to the route functions so the policy gate is enforced at the
    source -- not just in the MCP tool wrapper -- and is unit-testable without
    spinning up fastmcp or hitting the network. Reads the env on every call
    (not import-time) so an operator can arm/disarm and tests can monkeypatch
    without a code change."""

    def decorate(fn):
        def wrapper(*args, **kwargs):
            refusal = check_group(group, fn.__name__)
            if refusal is not None:
                return refusal
            return fn(*args, **kwargs)

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        wrapper.__wrapped__ = fn
        # Machine-readable marker so a test can ENUMERATE the gated routes
        # instead of re-listing them by hand. A hand-maintained list is the
        # thing that silently lags the module it describes.
        wrapper._gate_group = group
        return wrapper

    return decorate


def gated_write(fn):
    """The write gate. Kept as its own name (rather than `gated(GROUP_WRITE)`
    at every call site) because it is the gate every existing write route
    already carries and the one the README documents."""
    return gated(GROUP_WRITE)(fn)


def gated_task_claim(fn):
    """The task-claim gate. STACKED UNDER `gated_write` on the three task
    mutating routes, never instead of it -- write-gate-off must still read as
    a write refusal, so `gated_write` is listed FIRST (outermost) at the call
    site and answers first."""
    return gated(GROUP_TASK_CLAIM)(fn)


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
