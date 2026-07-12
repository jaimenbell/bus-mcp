"""bus_mcp.routes -- one function per coordination-bus route (backend/
coordination_bus.py), 1:1. Each function calls bus_mcp.client, which raises
BusUnreachable / BusApiError on failure; every function here catches both and
normalizes to `{"ok": False, "error": {...}}` so a tool caller never sees a
raw exception or stack trace -- only server.py's @mcp.tool wrappers call
these, and they return whatever these functions return, unmodified.
"""
from __future__ import annotations

import re
from typing import Any

from . import client, config

# Security: `lane` is interpolated into the request URL path
# (f"/lanes/{lane}/claim"). Unsanitized, a value containing "/" or "?" or ".."
# lets a caller manipulate the same-host endpoint (path injection). Confine it
# to a strict charset before it is ever placed in a URL. Fail closed with the
# module's standard ok=False error dict rather than raising.
_LANE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _invalid_lane_payload(lane: Any, tool: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "type": "invalid_lane",
            "message": (
                f"lane {lane!r} is invalid; must match {_LANE_RE.pattern} "
                "(letters, digits, underscore, hyphen only)."
            ),
            "tool": tool,
        },
    }


def _valid_lane(lane: Any) -> bool:
    return isinstance(lane, str) and bool(_LANE_RE.match(lane))


def _error_payload(exc: client.BusUnreachable | client.BusApiError) -> dict[str, Any]:
    if isinstance(exc, client.BusUnreachable):
        return {
            "ok": False,
            "error": {
                "type": "bus_unreachable",
                "message": str(exc),
                "tool": exc.tool,
            },
        }
    return {
        "ok": False,
        "error": {
            "type": "bus_api_error",
            "message": str(exc),
            "tool": exc.tool,
            "status_code": exc.status_code,
        },
    }


def post_message(topic: str, sender: str, body: str, action_flag: bool = False) -> dict[str, Any]:
    """Append one message to the bus blackboard (append-only). v1 stores
    action_flag but performs no action -- it is display-only, seen by a human
    watching the command-center panel."""
    try:
        result = client.post(
            "post_message",
            "/message",
            json={"topic": topic, "sender": sender, "body": body, "action_flag": action_flag},
        )
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return {"ok": True, **result}


def read_messages(topic: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Recent bus messages, newest first, optionally filtered by topic."""
    params: dict[str, Any] = {"limit": limit}
    if topic is not None:
        params["topic"] = topic
    try:
        result = client.get("read_messages", "/messages", params=params)
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return {"ok": True, **result}


def claim_lane(lane: str, owner: str, lease_s: int = config.DEFAULT_LEASE_S) -> dict[str, Any]:
    """Claim a coordination lane before starting work in it: claim-if-free,
    steal-if-lease-expired, renew-if-you-already-own-it. A 409 (lane held
    live by another owner) surfaces as a clean ok=False conflict, not a
    crash -- check `error.type == "bus_api_error"` and `status_code == 409`."""
    if not _valid_lane(lane):
        return _invalid_lane_payload(lane, "claim_lane")
    try:
        result = client.post(
            "claim_lane", f"/lanes/{lane}/claim", json={"owner": owner, "lease_s": lease_s}
        )
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return {"ok": True, **result}


def release_lane(lane: str, owner: str) -> dict[str, Any]:
    """Release a lane you hold. A 409 (held live by another owner) surfaces
    as a clean ok=False conflict, not a crash."""
    if not _valid_lane(lane):
        return _invalid_lane_payload(lane, "release_lane")
    try:
        result = client.post("release_lane", f"/lanes/{lane}/release", json={"owner": owner})
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return {"ok": True, **result}


def heartbeat_lane(lane: str, owner: str, lease_s: int = config.DEFAULT_LEASE_S) -> dict[str, Any]:
    """Renew the lease on a lane you hold live. A 409 (not held live by you)
    surfaces as a clean ok=False conflict telling you to (re)claim instead."""
    if not _valid_lane(lane):
        return _invalid_lane_payload(lane, "heartbeat_lane")
    try:
        result = client.post(
            "heartbeat_lane", f"/lanes/{lane}/heartbeat", json={"owner": owner, "lease_s": lease_s}
        )
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return {"ok": True, **result}


def get_bus_status() -> dict[str, Any]:
    """Roll-up for the command-center panel: active lanes, orphaned/stale
    claims, recent messages, pending display-only action flags."""
    try:
        result = client.get("get_bus_status", "/status")
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return {"ok": True, **result}
