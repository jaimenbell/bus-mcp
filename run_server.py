#!/usr/bin/env python3
"""Entrypoint: `python run_server.py`. Adds this file's own directory to
sys.path so `bus_mcp` imports cleanly regardless of the caller's cwd (the
same convention as github-mcp's run_server.py, so ~/.claude.json can invoke
this by absolute path with no `cwd` key)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bus_mcp.server import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run()
