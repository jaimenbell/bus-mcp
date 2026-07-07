"""bus_mcp.server -- FastMCP instance + tool wiring. Six tools, one per
coordination-bus route (backend/coordination_bus.py in the alphahive repo),
each a thin async passthrough to bus_mcp.routes. No auth, no write-gate --
the bus is a localhost-only v1 coordination substrate; every route is safe.
"""
from __future__ import annotations

from fastmcp import FastMCP

from . import config, routes

SERVER_NAME = "bus-mcp"
mcp = FastMCP(SERVER_NAME)


@mcp.tool(
    name="post_message",
    description=(
        "Append one message to the coordination-bus blackboard (append-only). "
        "v1 stores action_flag but performs no action -- it is display-only, "
        "seen by a human watching the command-center panel."
    ),
)
async def post_message_tool(
    topic: str, sender: str, body: str, action_flag: bool = False
) -> dict:
    return routes.post_message(topic, sender, body, action_flag)


@mcp.tool(
    name="read_messages",
    description="Recent bus messages, newest first, optionally filtered by topic.",
)
async def read_messages_tool(topic: str | None = None, limit: int = 50) -> dict:
    return routes.read_messages(topic, limit)


@mcp.tool(
    name="claim_lane",
    description=(
        "Claim a coordination lane before starting work in it: claim-if-free, "
        "steal-if-lease-expired, renew-if-you-already-own-it. A 409 (lane held "
        "live by another owner) comes back as a clean ok=False conflict, not a crash. "
        "The bus may grant a shorter lease than requested (server-side ceiling, "
        "coordination-bus v1.3+): on ok=True the response's top-level `lease_s` is "
        "the EFFECTIVE (post-clamp) duration actually granted -- always check it "
        "rather than assuming the requested value was honored. See get_bus_status's "
        "`_meta.max_lease_seconds` for the currently configured ceiling. Older bus "
        "servers (pre-v1.3) omit `lease_s` from the response entirely; its absence "
        "just means the bus predates the ceiling feature, not an error."
    ),
)
async def claim_lane_tool(lane: str, owner: str, lease_s: int = config.DEFAULT_LEASE_S) -> dict:
    return routes.claim_lane(lane, owner, lease_s)


@mcp.tool(
    name="release_lane",
    description=(
        "Release a coordination lane you hold. A 409 (held live by another "
        "owner) comes back as a clean ok=False conflict, not a crash."
    ),
)
async def release_lane_tool(lane: str, owner: str) -> dict:
    return routes.release_lane(lane, owner)


@mcp.tool(
    name="heartbeat_lane",
    description=(
        "Renew the lease on a coordination lane you hold live. A 409 (not "
        "held live by you) tells you to (re)claim instead of crashing. Like "
        "claim_lane, renewal is subject to the same server-side lease ceiling "
        "(coordination-bus v1.3+): on ok=True the response's top-level `lease_s` "
        "is the EFFECTIVE (post-clamp) duration actually granted, which may be "
        "shorter than requested -- check it rather than assuming the request was "
        "honored in full. See get_bus_status's `_meta.max_lease_seconds` for the "
        "currently configured ceiling. Older bus servers (pre-v1.3) omit `lease_s` "
        "from the response entirely; its absence just means the bus predates the "
        "ceiling feature, not an error."
    ),
)
async def heartbeat_lane_tool(lane: str, owner: str, lease_s: int = config.DEFAULT_LEASE_S) -> dict:
    return routes.heartbeat_lane(lane, owner, lease_s)


@mcp.tool(
    name="get_bus_status",
    description=(
        "Roll-up for the command-center panel: active lanes, orphaned/stale "
        "claims, recent messages, pending display-only action flags. Also "
        "exposes `_meta.max_lease_seconds` (coordination-bus v1.3+): the "
        "currently configured lease ceiling that claim_lane/heartbeat_lane "
        "requests get silently clamped to. Check this before claiming a lane "
        "for longer than the default if you need to know whether the request "
        "will actually be honored in full. Older bus servers (pre-v1.3) omit "
        "`max_lease_seconds` from `_meta` entirely; its absence just means the "
        "bus predates the ceiling feature, not an error."
    ),
)
async def get_bus_status_tool() -> dict:
    return routes.get_bus_status()


if __name__ == "__main__":
    mcp.run()
