#!/usr/bin/env python3
"""Handshake check: import the server and print every registered MCP tool
name, one per line. Does not start a transport -- pure introspection."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bus_mcp.server import mcp  # noqa: E402


async def _main() -> None:
    tools = await mcp.list_tools()
    for name in sorted(t.name for t in tools):
        print(name)


if __name__ == "__main__":
    asyncio.run(_main())
